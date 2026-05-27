#!/usr/bin/env python3
"""
RMIFO — Country IP Range Probe
================================
Consome country_ip_ranges.json e executa sondas TCP/ICMP/BGP
em IPs representativos de cada país, integrado ao banco PostgreSQL.

Uso:
  python rmifo_country_probe.py --countries BR JP DE
  python rmifo_country_probe.py --rir LACNIC
  python rmifo_country_probe.py --all --workers 20
  python rmifo_country_probe.py --country US --mode icmp
"""

import os, sys, json, socket, time, ipaddress, argparse, logging
import subprocess, struct
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("rmifo-country")

# ─── Config ──────────────────────────────────────────────────────────────────
JSON_PATH   = Path(__file__).parent / "country_ip_ranges.json"
TIMEOUT     = int(os.getenv("PROBE_TIMEOUT", "5"))
MAX_WORKERS = int(os.getenv("PROBE_WORKERS", "30"))
RIPE_BASE   = "https://stat.ripe.net/data"

# ─── Carrega base de dados ────────────────────────────────────────────────────
def load_ranges(json_path: Path = JSON_PATH) -> dict:
    with open(json_path) as f:
        data = json.load(f)
    return data.get("countries", data)


# ─── Seleção de IPs representativos de um range ──────────────────────────────
def sample_ips_from_cidr(cidr: str, n: int = 3) -> list[str]:
    """
    Extrai N IPs representativos de um CIDR para sonda:
    - .1   (geralmente gateway)
    - meio do bloco
    - penúltimo host
    """
    try:
        net   = ipaddress.ip_network(cidr, strict=False)
        hosts = list(net.hosts())
        if not hosts:
            return [str(net.network_address)]
        if len(hosts) <= n:
            return [str(h) for h in hosts]
        indices = [0, len(hosts) // 2, -1]
        return [str(hosts[i]) for i in indices[:n]]
    except Exception:
        return []


def get_gateway_ip(cidr: str) -> str | None:
    """Retorna o primeiro IP utilizável do CIDR (geralmente o gateway)."""
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        hosts = list(net.hosts())
        return str(hosts[0]) if hosts else None
    except Exception:
        return None


# ─── Sondas ───────────────────────────────────────────────────────────────────
def probe_icmp(ip: str, count: int = 3) -> dict:
    """Ping ICMP com subprocess."""
    result = {"ip": ip, "reachable": False, "loss_pct": 100.0, "avg_ms": None}
    try:
        cmd = ["ping", "-c", str(count), "-W", "2", "-q", ip]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        for line in out.stdout.splitlines():
            if "packet loss" in line:
                result["loss_pct"] = float(line.split("%")[0].split()[-1])
                result["reachable"] = result["loss_pct"] < 100
            if "rtt" in line or "round-trip" in line:
                parts = line.split("=")[-1].strip().split("/")
                if len(parts) >= 2:
                    result["avg_ms"] = float(parts[1])
    except Exception:
        pass
    return result


def probe_tcp(ip: str, ports: list[int] = [80, 443, 22, 8080]) -> dict:
    """TCP connect probe nas portas especificadas."""
    result = {"ip": ip, "open_ports": [], "latency_ms": None}
    for port in ports:
        try:
            start = time.time()
            with socket.create_connection((ip, port), timeout=TIMEOUT):
                ms = round((time.time() - start) * 1000, 2)
                result["open_ports"].append(port)
                if result["latency_ms"] is None:
                    result["latency_ms"] = ms
        except Exception:
            pass
    result["reachable"] = len(result["open_ports"]) > 0
    return result


def probe_bgp_ripe(prefix: str) -> dict:
    """Consulta visibilidade BGP do prefixo via RIPE STAT."""
    result = {"prefix": prefix, "peers_seeing": 0, "origin_asn": None,
              "visibility_pct": 0.0, "roa_status": "unknown"}
    try:
        r = requests.get(
            f"{RIPE_BASE}/visibility/data.json",
            params={"resource": prefix}, timeout=TIMEOUT
        )
        data = r.json().get("data", {}).get("visibilities", [])
        if data:
            v4 = data[0].get("ipv4", {}) or {}
            peers = v4.get("ris_peers_seeing", 0)
            total = v4.get("total_ris_peers", 1)
            result["peers_seeing"]   = peers
            result["visibility_pct"] = round(peers / total * 100, 2) if total else 0
    except Exception:
        pass
    return result


def traceroute_hops(ip: str, max_hops: int = 15) -> list[dict]:
    """Traceroute rápido para mapear hops até o IP do país."""
    hops = []
    try:
        cmd = ["traceroute", "-n", "-m", str(max_hops), "-w", "2", ip]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        for i, line in enumerate(out.stdout.splitlines()[1:], 1):
            parts = line.split()
            if len(parts) >= 2 and parts[1] != "*":
                try:
                    ipaddress.ip_address(parts[1])
                    hops.append({
                        "hop": i,
                        "ip":  parts[1],
                        "rtt": float(parts[2]) if len(parts) > 2 else None,
                    })
                except Exception:
                    pass
    except Exception:
        pass
    return hops


# ─── Pipeline por país ────────────────────────────────────────────────────────
def probe_country(cc: str, info: dict, mode: str = "tcp",
                  max_ranges: int = 3, ips_per_range: int = 2) -> dict:
    """
    Executa sonda em IPs representativos dos ranges de um país.
    Retorna sumário com taxa de alcançabilidade e latência média.
    """
    ranges  = info["ipv4_ranges"][:max_ranges]
    results = []

    for cidr in ranges:
        sample_ips = sample_ips_from_cidr(cidr, n=ips_per_range)
        for ip in sample_ips:
            if mode == "icmp":
                r = probe_icmp(ip)
            elif mode == "bgp":
                r = probe_bgp_ripe(cidr)
                r["ip"] = get_gateway_ip(cidr)
                break
            else:
                r = probe_tcp(ip)
            r["cidr"] = cidr
            results.append(r)

    reachable = [r for r in results if r.get("reachable")]
    latencies  = [r["latency_ms"] or r.get("avg_ms", 0)
                  for r in reachable if r.get("latency_ms") or r.get("avg_ms")]

    return {
        "country":      info["country"],
        "iso2":         cc,
        "rir":          info["rir"],
        "ranges_tested": len(ranges),
        "ips_tested":   len(results),
        "reachable":    len(reachable),
        "reach_pct":    round(len(reachable) / max(len(results), 1) * 100, 1),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "min_latency_ms": round(min(latencies), 2) if latencies else None,
        "results":      results,
        "probed_at":    datetime.now(timezone.utc).isoformat(),
    }


# ─── Relatório ────────────────────────────────────────────────────────────────
def print_report(summaries: list[dict]):
    """Imprime tabela de resultados ordenada por latência."""
    sortable = [s for s in summaries if s["avg_latency_ms"] is not None]
    others   = [s for s in summaries if s["avg_latency_ms"] is None]

    sorted_results = sorted(sortable, key=lambda x: x["avg_latency_ms"]) + others

    print("\n" + "─" * 72)
    print(f"{'CC':<5} {'País':<22} {'RIR':<8} {'Alcance':<10} {'Lat. Média':<12} {'Lat. Mín'}")
    print("─" * 72)

    for s in sorted_results:
        lat    = f"{s['avg_latency_ms']:.1f} ms" if s["avg_latency_ms"] else "—"
        latmin = f"{s['min_latency_ms']:.1f} ms" if s["min_latency_ms"] else "—"
        reach  = f"{s['reachable']}/{s['ips_tested']} ({s['reach_pct']}%)"
        print(f"{s['iso2']:<5} {s['country']:<22} {s['rir']:<8} {reach:<10} {lat:<12} {latmin}")

    print("─" * 72)
    total_tested = sum(s["ips_tested"] for s in summaries)
    total_reach  = sum(s["reachable"]  for s in summaries)
    print(f"Total: {len(summaries)} países · {total_tested} IPs testados · {total_reach} alcançáveis")


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="RMIFO Country IP Probe")
    ap.add_argument("--countries", nargs="*", default=[],
                    help="Códigos ISO2 (ex: BR JP DE US)")
    ap.add_argument("--rir", choices=["ARIN","RIPE","APNIC","LACNIC","AFRINIC"],
                    help="Filtrar por RIR")
    ap.add_argument("--all",     action="store_true", help="Sondar todos os países")
    ap.add_argument("--mode",    choices=["tcp","icmp","bgp"], default="tcp")
    ap.add_argument("--ranges",  type=int, default=2,  help="Ranges por país (padrão: 2)")
    ap.add_argument("--ips",     type=int, default=2,  help="IPs por range (padrão: 2)")
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--output",  type=str, help="Arquivo JSON de saída")
    ap.add_argument("--json-db", type=str, default=str(JSON_PATH),
                    help="Caminho para country_ip_ranges.json")
    args = ap.parse_args()

    # Carrega dados
    db = load_ranges(Path(args.json_db))
    log.info(f"Base carregada: {len(db)} países")

    # Seleciona países alvo
    if args.all:
        targets = dict(db)
    elif args.rir:
        targets = {cc: info for cc, info in db.items() if info["rir"] == args.rir}
    elif args.countries:
        targets = {cc: db[cc] for cc in args.countries if cc in db}
    else:
        log.error("Especifique --countries, --rir ou --all")
        ap.print_help()
        sys.exit(1)

    if not targets:
        log.error("Nenhum país encontrado com os filtros especificados.")
        sys.exit(1)

    log.info(f"Sondando {len(targets)} países em modo '{args.mode}' "
             f"({args.workers} workers)...")

    # Executa sondas em paralelo
    summaries = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(probe_country, cc, info, args.mode, args.ranges, args.ips): cc
            for cc, info in targets.items()
        }
        for i, f in enumerate(as_completed(futures), 1):
            cc  = futures[f]
            res = f.result()
            summaries.append(res)
            log.info(f"[{i}/{len(targets)}] {cc} {res['country']}: "
                     f"{res['reachable']}/{res['ips_tested']} alcançáveis, "
                     f"lat={res['avg_latency_ms']}ms")

    # Relatório
    print_report(summaries)

    # Salva JSON de saída
    if args.output:
        with open(args.output, "w") as f:
            json.dump({
                "probed_at":  datetime.now(timezone.utc).isoformat(),
                "mode":       args.mode,
                "countries":  summaries,
            }, f, indent=2, ensure_ascii=False)
        log.info(f"Resultado salvo em {args.output}")

    return summaries


if __name__ == "__main__":
    main()
