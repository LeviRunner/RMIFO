#!/usr/bin/env python3
"""
rmifo_realtime_probe.py — Medição real de latência para cabos submarinos

PROBLEMA: sonda.js usa o public_url do cabo (site institucional do operador)
que geralmente está atrás de CDN (Cloudflare/Akamai). O TCP mede a edge da
CDN, não o caminho físico do cabo — por isso retorna "Sintético" quando nenhum
host real responde.

SOLUÇÃO: usar IPs dos pontos de aterrissagem (endpoint_ip_samples) ou IPs
descobertos por traceroute/BGP que estão topologicamente no caminho do cabo.

Protocolos suportados (sem root / sem CAP_NET_RAW):
  1. TCP SYN timing     — handshake TCP completo → RTT real e jitter
  2. HTTP/HTTPS timing  — GET / com requests → inclui DNS + TCP + TLS + TTFB
  3. DNS timing         — query DNS contra resolver específico (latência de rede)
  4. STUN timing        — UDP STUN Binding Request (porta 3478) → RTT sem TCP overhead
  5. ICMP via mtr/ping  — se executando como root ou com cap_net_raw (opcional)
  6. RIPE Atlas         — lê medições públicas de probes perto dos landing points

Uso:
  python3 rmifo_realtime_probe.py aec-1
  python3 rmifo_realtime_probe.py aec-1 --ips 41.203.64.1 196.200.16.1
  python3 rmifo_realtime_probe.py aec-1 --mode tcp,http,stun
  python3 rmifo_realtime_probe.py aec-1 --ports 22,80,443,8080
  python3 rmifo_realtime_probe.py --demo
"""

import argparse
import socket
import struct
import time
import os
import sys
import json
import threading
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import psycopg2
    HAS_PG = True
except ImportError:
    HAS_PG = False

# ── Configuração ──────────────────────────────────────────────────────────────

TIMEOUT      = float(os.getenv("PROBE_TIMEOUT", "4"))
WORKERS      = int(os.getenv("PROBE_WORKERS",   "10"))
JITTER_PKTS  = int(os.getenv("TCP_JITTER_PKTS", "30"))
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Portas padrão para TCP scan
DEFAULT_PORTS = [443, 80, 22, 8080, 8443, 179, 23]

# STUN servers públicos (UDP 3478) — retornam RTT puro sem overhead HTTP
STUN_SERVERS = [
    ("stun.l.google.com",      3478),
    ("stun1.l.google.com",     3478),
    ("stun.cloudflare.com",    3478),
    ("stun.ekiga.net",         3478),
]

# ── 1. TCP SYN timing ─────────────────────────────────────────────────────────
# Mede o RTT do handshake TCP completo (SYN → SYN-ACK → ACK).
# Esta é a medição mais confiável sem privilégios de root.
# RTT = tempo de ida e volta físico na fibra + latência de processamento do stack TCP.

def tcp_rtt(host: str, port: int, timeout: float = TIMEOUT) -> Optional[float]:
    """Retorna RTT em ms do handshake TCP ou None se falhou."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        t0 = time.perf_counter()
        sock.connect((host, port))
        rtt = (time.perf_counter() - t0) * 1000
        sock.close()
        return round(rtt, 2)
    except Exception:
        return None


def tcp_jitter(host: str, ports: list[int], n: int = JITTER_PKTS,
               timeout: float = TIMEOUT) -> Optional[dict]:
    """
    Mede jitter TCP: n conexões distribuídas entre as portas abertas.
    Calcula mean, stdev (jitter real), p95, perda.

    Jitter = desvio padrão do RTT entre conexões consecutivas.
    Valor < 5ms → excelente; 5-20ms → normal; > 50ms → congestionamento.
    """
    if not ports:
        return None

    tasks = []
    for i in range(n):
        port = ports[i % len(ports)]
        tasks.append((host, port))

    results = []
    with ThreadPoolExecutor(max_workers=min(WORKERS, n)) as ex:
        futures = {ex.submit(tcp_rtt, h, p, timeout): i for i, (h, p) in enumerate(tasks)}
        for fut in as_completed(futures):
            r = fut.result()
            if r is not None:
                results.append(r)

    if len(results) < 2:
        return None

    results.sort()
    mean  = statistics.mean(results)
    jit   = statistics.stdev(results)
    p95   = results[int(len(results) * 0.95)]
    loss  = round((1 - len(results) / n) * 100, 2)

    return {
        "mean_ms":  round(mean, 2),
        "jitter_ms": round(jit, 2),
        "p95_ms":   round(p95, 2),
        "loss_pct": loss,
        "samples":  len(results),
    }


def scan_open_ports(host: str, ports: list[int] = DEFAULT_PORTS,
                    timeout: float = 1.5) -> list[int]:
    """Descobre portas TCP abertas. Rápido com timeout curto."""
    with ThreadPoolExecutor(max_workers=len(ports)) as ex:
        futs = {ex.submit(tcp_rtt, host, p, timeout): p for p in ports}
        return sorted([p for fut, p in futs.items() if fut.result() is not None])


# ── 2. HTTP/HTTPS timing ──────────────────────────────────────────────────────
# Inclui resolução DNS + TCP handshake + TLS negotiation + TTFB.
# Mais alto que TCP puro, mas útil para verificar disponibilidade real.

def http_rtt(url: str, timeout: float = TIMEOUT) -> Optional[dict]:
    """
    Mede latência HTTP completa.
    Retorna: connect_ms (TCP), ttfb_ms (Time To First Byte), total_ms.
    """
    if not HAS_REQUESTS:
        return None
    if not url.startswith("http"):
        url = f"https://{url}"

    try:
        t0 = time.perf_counter()
        resp = requests.get(url, timeout=timeout, allow_redirects=True,
                            headers={"User-Agent": "RMIFO-probe/1.0"})
        total = (time.perf_counter() - t0) * 1000
        return {
            "total_ms":   round(total, 2),
            "status":     resp.status_code,
            "server":     resp.headers.get("Server", ""),
            "via_cdn":    "cf-ray" in resp.headers or "x-amz-" in str(resp.headers).lower(),
        }
    except Exception:
        return None


# ── 3. DNS timing ─────────────────────────────────────────────────────────────
# Mede o RTT de uma query DNS contra um resolver específico.
# Útil para verificar se o resolver está no caminho do cabo.
# Não requer TCP — usa UDP puro (datagrama DNS).

def dns_rtt(hostname: str, resolver_ip: str = "8.8.8.8",
            timeout: float = TIMEOUT) -> Optional[float]:
    """
    Envia query DNS Type A via UDP e mede o RTT.
    Construção manual do pacote DNS (sem dependência de dnspython).
    """
    try:
        # Construção manual do header DNS
        txid   = os.urandom(2)
        flags  = b"\x01\x00"                # QR=0 (query), RD=1 (recursion desired)
        qdcnt  = b"\x00\x01"                # 1 pergunta
        zeros  = b"\x00\x00\x00\x00\x00\x00"

        # Encode hostname em wire format
        qname = b""
        for part in hostname.split("."):
            qname += bytes([len(part)]) + part.encode()
        qname += b"\x00"

        qtype  = b"\x00\x01"               # Type A
        qclass = b"\x00\x01"               # Class IN
        query  = txid + flags + qdcnt + zeros + qname + qtype + qclass

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        t0 = time.perf_counter()
        sock.sendto(query, (resolver_ip, 53))
        sock.recv(512)
        rtt = (time.perf_counter() - t0) * 1000
        sock.close()
        return round(rtt, 2)
    except Exception:
        return None


# ── 4. STUN timing ────────────────────────────────────────────────────────────
# STUN (Session Traversal Utilities for NAT) usa UDP porta 3478.
# Um Binding Request → Binding Response = 1 RTT UDP puro.
# Vantagem: sem overhead TCP/TLS, mede apenas a latência de rede.
# Limitação: não mede o caminho do cabo específico, mas servidores bem
# distribuídos geograficamente podem aproximar a latência regional.

def stun_rtt(server: str, port: int = 3478,
             timeout: float = TIMEOUT) -> Optional[float]:
    """
    Envia STUN Binding Request (RFC 5389) e mede RTT UDP.

    Pacote: 20 bytes
      - Type: 0x0001 (Binding Request)
      - Length: 0x0000
      - Magic Cookie: 0x2112A442
      - Transaction ID: 12 bytes aleatórios
    """
    try:
        magic   = b"\x21\x12\xa4\x42"
        txid    = os.urandom(12)
        request = b"\x00\x01\x00\x00" + magic + txid   # 20 bytes

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        t0 = time.perf_counter()
        sock.sendto(request, (server, port))
        data, _ = sock.recvfrom(512)
        rtt = (time.perf_counter() - t0) * 1000
        sock.close()

        # Verificar que é uma Binding Response (0x0101)
        if len(data) >= 2 and data[0:2] == b"\x01\x01":
            return round(rtt, 2)
        return None
    except Exception:
        return None


# ── 5. ICMP via ping/mtr (requer root ou cap_net_raw) ────────────────────────
# ICMP Echo dá o RTT mais limpo possível — apenas ida e volta na fibra.
# Bloqueado pela maioria dos firewalls em servidores de produção.
# Disponível se: executando como root OU com `sudo setcap cap_net_raw+ep $(which python3)`

def icmp_ping(host: str, count: int = 5) -> Optional[dict]:
    """Usa o binário ping do sistema. Funciona sem root via setuid."""
    import subprocess
    try:
        out = subprocess.run(
            ["ping", "-c", str(count), "-W", str(int(TIMEOUT)), host],
            capture_output=True, text=True, timeout=TIMEOUT * count + 5
        )
        # Extrai: rtt min/avg/max/mdev
        for line in out.stdout.splitlines():
            if "rtt min" in line or "round-trip" in line:
                parts = line.split("=")[-1].strip().split("/")
                if len(parts) >= 4:
                    return {
                        "min_ms":  float(parts[0]),
                        "avg_ms":  float(parts[1]),
                        "max_ms":  float(parts[2]),
                        "jit_ms":  float(parts[3]),
                    }
        return None
    except Exception:
        return None


# ── 6. Buscar IPs reais via banco de dados ────────────────────────────────────
# endpoint_ip_samples tem IPs por ponto de aterrissagem.
# São os melhores alvos — estão geograficamente nos terminais do cabo.

def get_landing_ips_from_db(cable_id: str) -> list[dict]:
    """
    Busca IPs dos pontos de aterrissagem do cabo no banco.
    Estes IPs estão topologicamente próximos ao terminal do cabo.
    """
    if not HAS_PG or not DATABASE_URL:
        return []
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur  = conn.cursor()
        cur.execute("""
            SELECT
                lp.endpoint_id,
                lp.city, lp.country,
                eis.ip_start::text AS ip,
                eis.network::text
            FROM cable_landing_points clp
            JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id
            JOIN endpoint_ip_samples eis ON eis.endpoint_id = lp.endpoint_id
            WHERE clp.cable_id = %s
              AND eis.ip_start IS NOT NULL
            ORDER BY lp.country, eis.sample_order
            LIMIT 20
        """, (cable_id,))
        rows = cur.fetchall()
        conn.close()
        return [{"endpoint": r[0], "city": r[1], "country": r[2],
                 "ip": r[3], "network": r[4]} for r in rows]
    except Exception as e:
        print(f"[DB] Erro: {e}", file=sys.stderr)
        return []


def get_hop_ips_from_db(cable_id: str) -> list[dict]:
    """
    Busca IPs de hops MTR já registrados para o cabo.
    São roteadores no caminho — excelentes alvos para medição real.
    """
    if not HAS_PG or not DATABASE_URL:
        return []
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur  = conn.cursor()
        cur.execute("""
            SELECT DISTINCT
                cth.hop_ip,
                hsg.city, hsg.country_code
            FROM cable_telemetry_samples cts
            JOIN cable_telemetry_hops cth ON cth.sample_id = cts.id
            LEFT JOIN hop_switch_geolocations hsg ON hsg.ip = cth.hop_ip
            WHERE cts.cable_id = %s
              AND cth.hop_ip IS NOT NULL
              AND cth.hop_ip NOT IN ('*', '???')
            ORDER BY cth.hop_ip
            LIMIT 15
        """, (cable_id,))
        rows = cur.fetchall()
        conn.close()
        return [{"ip": r[0], "city": r[1], "country": r[2]} for r in rows]
    except Exception as e:
        print(f"[DB] Erro hops: {e}", file=sys.stderr)
        return []


# ── Probe completo de um host ─────────────────────────────────────────────────

def probe_host(host: str, modes: list[str], ports: list[int] = None) -> dict:
    """
    Executa todos os protocolos solicitados contra um host.
    Retorna dict com todos os resultados.
    """
    ports = ports or DEFAULT_PORTS
    result = {"host": host, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # Descoberta de portas abertas
    open_ports = scan_open_ports(host, ports)
    result["open_ports"] = open_ports

    if "tcp" in modes and open_ports:
        # RTT do primeiro handshake
        rtt = tcp_rtt(host, open_ports[0])
        result["tcp_rtt_ms"] = rtt

        # Jitter TCP com múltiplos pacotes
        jit = tcp_jitter(host, open_ports)
        if jit:
            result["tcp_jitter"] = jit
            result["latency_ms"] = jit["mean_ms"]
            result["jitter_ms"]  = jit["jitter_ms"]
            result["loss_pct"]   = jit["loss_pct"]
            result["source"]     = "tcp_real"

    if "http" in modes:
        scheme = "https" if 443 in open_ports else "http" if 80 in open_ports else None
        if scheme:
            http = http_rtt(f"{scheme}://{host}")
            if http:
                result["http"] = http
                if "latency_ms" not in result:
                    result["latency_ms"] = http["total_ms"]
                    result["source"]     = "http_real"
                    if http.get("via_cdn"):
                        result["warning"] = "Host atrás de CDN — RTT mede edge da CDN, não o cabo"

    if "dns" in modes:
        # Usa o host como resolver (se for um roteador/server com DNS)
        dns = dns_rtt("probe.rmifo.local", host)
        result["dns_rtt_ms"] = dns

    if "stun" in modes:
        for srv, port in STUN_SERVERS[:2]:
            s = stun_rtt(srv, port)
            if s:
                result["stun_rtt_ms"] = s
                result["stun_server"] = srv
                break

    if "icmp" in modes:
        ping = icmp_ping(host)
        if ping:
            result["icmp"] = ping
            if "latency_ms" not in result:
                result["latency_ms"] = ping["avg_ms"]
                result["source"]     = "icmp_real"

    # Determina se resultado é real ou sem dados
    if "latency_ms" not in result:
        result["latency_ms"] = None
        result["source"]     = "no_response"

    return result


# ── Demo sem banco de dados ───────────────────────────────────────────────────

DEMO_TARGETS = [
    # IXPs e roteadores conhecidos próximos a cabos submarinos africanos/europeus
    {"host": "decix-gw.he.net",       "desc": "DE-CIX Frankfurt (HE)"},
    {"host": "linx-gw.he.net",        "desc": "LINX Londres (HE)"},
    {"host": "amsix-gw.he.net",       "desc": "AMS-IX Amsterdã (HE)"},
    {"host": "nap.afrinic.net",       "desc": "AFRINIC (João Pessoa → África)"},
    {"host": "www.angola-cables.com", "desc": "Angola Cables (operador SACS)"},
    {"host": "www.sat3.com",          "desc": "SAT3/WASC (costa Oeste África)"},
]


def run_demo():
    print("\n=== DEMO — Medição real TCP/HTTP/STUN (sem banco de dados) ===\n")
    print(f"{'Host':<35} {'Portas':<15} {'TCP RTT':>9} {'HTTP RTT':>10} {'STUN RTT':>10}  Fonte")
    print("-" * 90)

    for t in DEMO_TARGETS:
        r = probe_host(t["host"], modes=["tcp", "http", "stun"])
        tcp   = f"{r.get('tcp_rtt_ms', '-')} ms"  if r.get("tcp_rtt_ms") else "—"
        http  = f"{r['http']['total_ms']} ms"      if r.get("http") else "—"
        stun  = f"{r.get('stun_rtt_ms', '-')} ms"  if r.get("stun_rtt_ms") else "—"
        ports = ",".join(map(str, r["open_ports"])) or "nenhuma"
        src   = r.get("source", "—")
        cdn   = " ⚠CDN" if r.get("warning") else ""
        print(f"{t['desc']:<35} {ports:<15} {tcp:>9} {http:>10} {stun:>10}  {src}{cdn}")

    print("\n[!] Hosts CDN: RTT mede edge da CDN, não o cabo físico.")
    print("[!] Hosts com portas abertas: TCP RTT = latência real de rede.\n")


# ── CLI principal ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RMIFO — Probe de latência real")
    parser.add_argument("cable_id", nargs="?",  help="ID do cabo (ex: aec-1)")
    parser.add_argument("--ips",    nargs="+",  help="IPs a sondar diretamente")
    parser.add_argument("--ports",  default="443,80,22,8080", help="Portas TCP a escanear")
    parser.add_argument("--mode",   default="tcp,http,stun",  help="Modos: tcp,http,dns,stun,icmp")
    parser.add_argument("--pkts",   type=int, default=JITTER_PKTS, help="Pacotes jitter TCP")
    parser.add_argument("--demo",   action="store_true", help="Rodar demo sem banco")
    parser.add_argument("--json",   action="store_true", help="Saída JSON")
    args = parser.parse_args()

    if args.demo:
        run_demo()
        return

    if not args.cable_id and not args.ips:
        parser.print_help()
        return

    ports  = [int(p.strip()) for p in args.ports.split(",")]
    modes  = [m.strip() for m in args.mode.split(",")]

    # Coleta de IPs alvo
    targets = []

    if args.ips:
        targets = [{"ip": ip, "source": "cli"} for ip in args.ips]
    elif args.cable_id:
        # 1. IPs dos pontos de aterrissagem
        landing = get_landing_ips_from_db(args.cable_id)
        if landing:
            print(f"[DB] {len(landing)} IPs de landing points encontrados")
            targets += [{"ip": r["ip"], "source": f"landing:{r['country']}:{r['city']}"} for r in landing]

        # 2. IPs de hops MTR conhecidos
        hops = get_hop_ips_from_db(args.cable_id)
        if hops:
            print(f"[DB] {len(hops)} IPs de hops MTR encontrados")
            targets += [{"ip": r["ip"], "source": f"hop:{r.get('country','?')}:{r.get('city','?')}"} for r in hops]

        if not targets:
            print(f"[!] Nenhum IP encontrado no banco para '{args.cable_id}'.")
            print("    Rode primeiro: python3 rmifo_advanced_reach.py --mode banner --cable-id aec-1")
            print("    Ou forneça IPs com: --ips 1.2.3.4 5.6.7.8")
            return

    # Probe de cada IP
    all_results = []
    print(f"\nSondando {len(targets)} IPs com modos: {modes}\n")
    print(f"{'IP':<20} {'Portas':<18} {'Latência':>10} {'Jitter':>8} {'Perda':>7}  Fonte")
    print("-" * 80)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(probe_host, t["ip"], modes, ports): t for t in targets}
        for fut in as_completed(futs):
            t = futs[fut]
            r = fut.result()
            r["target_source"] = t.get("source", "")
            all_results.append(r)

            lat   = f"{r['latency_ms']} ms" if r.get("latency_ms") else "sem resp"
            jit   = f"{r['tcp_jitter']['jitter_ms']} ms" if r.get("tcp_jitter") else "—"
            loss  = f"{r['tcp_jitter']['loss_pct']}%" if r.get("tcp_jitter") else "—"
            ports_str = ",".join(map(str, r["open_ports"])) or "nenhuma"
            cdn   = " ⚠CDN" if r.get("warning") else ""
            print(f"{r['host']:<20} {ports_str:<18} {lat:>10} {jit:>8} {loss:>7}  {r.get('source','?')}{cdn}")

    # Sumário
    real = [r for r in all_results if r.get("source") and "real" in r.get("source", "")]
    print(f"\n[✓] {len(real)}/{len(all_results)} hosts com dados reais (não-sintéticos)")

    if real:
        lats = [r["latency_ms"] for r in real if r.get("latency_ms")]
        if lats:
            print(f"    Latência média: {statistics.mean(lats):.1f} ms")
            print(f"    Min/Max: {min(lats):.1f} / {max(lats):.1f} ms")

    if args.json:
        print(json.dumps(all_results, indent=2, default=str))


if __name__ == "__main__":
    main()
