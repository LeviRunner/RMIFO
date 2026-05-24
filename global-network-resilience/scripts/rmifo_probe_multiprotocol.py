#!/usr/bin/env python3
"""
RMIFO — Multi-Protocol Network Probe
Sondagens BGP (RIPE STAT), SNMP (pysnmp-lextudio) e captura passiva
LLDP/NetFlow (Scapy opcional) sobre os IPs mapeados nos cabos submarinos.

Uso:
    python rmifo_probe_multiprotocol.py --mode bgp
    python rmifo_probe_multiprotocol.py --mode snmp --target 192.0.2.1
    python rmifo_probe_multiprotocol.py --mode netflow --interface eth0
    python rmifo_probe_multiprotocol.py --mode full --bgp-delay 1.0
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import socket
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import requests

# ── Dependências opcionais ────────────────────────────────────────────────────

try:
    from pysnmp.hlapi import (
        CommunityData, ContextData, ObjectIdentity, ObjectType,
        SnmpEngine, UdpTransportTarget, getCmd,
    )
    SNMP_AVAILABLE = True
except ImportError:
    SNMP_AVAILABLE = False

try:
    import scapy.all as scapy
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False

# ── Configuração ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("rmifo.probe")

RIPE_STAT_BASE   = "https://stat.ripe.net/data"
REQUEST_TIMEOUT  = 10
MAX_WORKERS      = int(os.getenv("PROBE_WORKERS", "8"))
SNMP_COMMUNITY   = os.getenv("SNMP_COMMUNITY", "public")

SNMP_OIDS = {
    "sysDescr":     "1.3.6.1.2.1.1.1.0",
    "sysUpTime":    "1.3.6.1.2.1.1.3.0",
    "ifOperStatus": "1.3.6.1.2.1.2.2.1.8.1",
    "ifInOctets":   "1.3.6.1.2.1.2.2.1.10.1",
    "ifOutOctets":  "1.3.6.1.2.1.2.2.1.16.1",
}

# ── Conexão ao banco ──────────────────────────────────────────────────────────

def _make_connection():
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url)
    from pathlib import Path
    base_dir = Path(__file__).resolve().parent.parent
    return psycopg2.connect(
        host=os.getenv("PG_SOCKET", str(base_dir / ".local" / "run")),
        port=int(os.getenv("PG_PORT", "55432")),
        dbname=os.getenv("DB_NAME", "global_network_resilience"),
        user=os.getenv("PGUSER", "postgres"),
    )


@contextmanager
def get_connection():
    conn = _make_connection()
    try:
        yield conn
    finally:
        conn.close()


# ── DDL — novas tabelas ───────────────────────────────────────────────────────

def ensure_tables(conn):
    """Cria as tabelas necessárias se ainda não existirem (idempotente)."""
    ddl = """
    CREATE TABLE IF NOT EXISTS bgp_probe_results (
        id              SERIAL PRIMARY KEY,
        probed_at       TIMESTAMPTZ DEFAULT NOW(),
        prefix          TEXT NOT NULL,
        cable_id        INTEGER,
        cable_name      TEXT,
        landing_country TEXT,
        peers_seeing    INTEGER,
        origin_asn      TEXT,
        as_path         JSONB,
        visibility_pct  NUMERIC(5,2),
        raw_json        JSONB
    );
    CREATE INDEX IF NOT EXISTS idx_bgp_prefix ON bgp_probe_results(prefix);

    CREATE TABLE IF NOT EXISTS snmp_probe_results (
        id               SERIAL PRIMARY KEY,
        probed_at        TIMESTAMPTZ DEFAULT NOW(),
        target_ip        TEXT NOT NULL,
        cable_id         INTEGER,
        sys_descr        TEXT,
        sys_uptime_ticks BIGINT,
        if_oper_status   TEXT,
        in_octets        BIGINT,
        out_octets       BIGINT,
        error_message    TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_snmp_ip ON snmp_probe_results(target_ip);

    CREATE TABLE IF NOT EXISTS lldp_topology_map (
        id              SERIAL PRIMARY KEY,
        discovered_at   TIMESTAMPTZ DEFAULT NOW(),
        local_device    TEXT,
        local_port      TEXT,
        neighbor_device TEXT,
        management_ip   TEXT,
        chassis_id      TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_lldp_local ON lldp_topology_map(local_device);

    CREATE TABLE IF NOT EXISTS netflow_traffic_samples (
        id          SERIAL PRIMARY KEY,
        captured_at TIMESTAMPTZ DEFAULT NOW(),
        src_ip      TEXT NOT NULL,
        dst_ip      TEXT NOT NULL,
        src_port    INTEGER,
        dst_port    INTEGER,
        protocol    INTEGER,
        packets     INTEGER,
        bytes       BIGINT,
        tos         INTEGER,
        input_if    INTEGER,
        output_if   INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_netflow_ips ON netflow_traffic_samples(src_ip, dst_ip);
    """
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()
    log.info("Estrutura de tabelas verificada/criada com sucesso.")


# ── Coleta de alvos ───────────────────────────────────────────────────────────

def fetch_target_ips() -> list[dict]:
    """Busca IPs ativos mapeados em cabos submarinos e pontos de ancoragem."""
    query = """
        SELECT DISTINCT e.ip_address, e.cable_id, c.name, lp.country_code
        FROM endpoint_ip_samples e
        LEFT JOIN cables c ON e.cable_id = c.id
        LEFT JOIN landing_points lp ON c.id = lp.cable_id
        WHERE e.is_active = TRUE AND e.ip_address IS NOT NULL;
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(query)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        log.error(f"Erro ao buscar IPs alvos do banco: {e}")
        return []


# ── BGP Probe (RIPE STAT API) ─────────────────────────────────────────────────

def probe_bgp_prefix(target: dict, bgp_delay: float = 0.5) -> None:
    """
    Consulta visibilidade BGP e ROA para o IP via RIPE STAT API.
    bgp_delay: segundos de espera após cada requisição (rate limiting).
    """
    ip = target["ip_address"]
    try:
        net = ipaddress.ip_network(ip, strict=False)
        prefix = str(net)
    except Exception:
        prefix = ip

    url = f"{RIPE_STAT_BASE}/routing-status/data.json?resource={ip}"
    try:
        res = requests.get(url, timeout=REQUEST_TIMEOUT,
                           headers={"User-Agent": "RMIFO-GlobalNetworkResilience/1.0"})
        if res.status_code == 200:
            data = res.json().get("data", {})
            visibility_pct = data.get("visibility_percentage", 100.0)
            bgp_state      = data.get("bgp_state", [])
            peers_seeing   = len(bgp_state)
            origin_asn     = bgp_state[0].get("origin_as", "Unknown") if bgp_state else "Unknown"
            as_path        = json.dumps([p.get("as_path", []) for p in bgp_state])

            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO bgp_probe_results
                            (prefix, cable_id, cable_name, landing_country,
                             peers_seeing, origin_asn, as_path, visibility_pct, raw_json)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (prefix, target.get("cable_id"), target.get("name"),
                          target.get("country_code"), peers_seeing, origin_asn,
                          as_path, visibility_pct, json.dumps(data)))
                conn.commit()
            log.info(f"BGP OK — {prefix}  peers={peers_seeing}  vis={visibility_pct}%")
        else:
            log.warning(f"BGP HTTP {res.status_code} para {ip}")
    except Exception as e:
        log.error(f"Falha na sonda BGP para {ip}: {e}")
    finally:
        # Rate limiting: evita sobrecarregar a API pública da RIPE
        if bgp_delay > 0:
            time.sleep(bgp_delay)


# ── SNMP Probe (pysnmp-lextudio) ──────────────────────────────────────────────

def _snmp_get(target_ip: str, oid: str) -> tuple[str | None, str | None]:
    """SNMP GetCmd síncrono para um OID. Retorna (valor, erro)."""
    if not SNMP_AVAILABLE:
        return None, "pysnmp-lextudio não instalado"
    try:
        err_ind, err_status, _, var_binds = next(
            getCmd(
                SnmpEngine(),
                CommunityData(SNMP_COMMUNITY, mpModel=1),
                UdpTransportTarget((target_ip, 161), timeout=2, retries=1),
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            )
        )
        if err_ind:
            return None, str(err_ind)
        if err_status:
            return None, str(err_status)
        return str(var_binds[0][1]), None
    except Exception as e:
        return None, str(e)


def probe_snmp_target(target: dict) -> None:
    """Sonda todas as MIBs configuradas para o IP alvo e persiste no banco."""
    if not SNMP_AVAILABLE:
        log.warning("pysnmp-lextudio não está disponível. Instale com: pip install pysnmp-lextudio")
        return

    ip = target["ip_address"]
    results: dict[str, str | None] = {}
    last_error = None

    for key, oid in SNMP_OIDS.items():
        val, err = _snmp_get(ip, oid)
        results[key] = val
        if err:
            last_error = err

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO snmp_probe_results
                        (target_ip, cable_id, sys_descr, sys_uptime_ticks,
                         if_oper_status, in_octets, out_octets, error_message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    ip,
                    target.get("cable_id"),
                    results.get("sysDescr"),
                    int(results["sysUpTime"]) if results.get("sysUpTime") else None,
                    results.get("ifOperStatus"),
                    int(results["ifInOctets"])  if results.get("ifInOctets")  else None,
                    int(results["ifOutOctets"]) if results.get("ifOutOctets") else None,
                    last_error,
                ))
            conn.commit()
        log.info(f"SNMP OK — {ip}  descr={results.get('sysDescr', '—')[:40]}")
    except Exception as e:
        log.error(f"Erro ao salvar telemetria SNMP para {ip}: {e}")


# ── Monitoração Passiva — Scapy (opcional, requer root) ───────────────────────

def _packet_callback(packet) -> None:
    """Callback Scapy: processa LLDP e NetFlow v5 em tempo real."""
    try:
        if packet.haslayer("Link Layer Discovery Protocol"):
            lldp      = packet["Link Layer Discovery Protocol"]
            local_port = packet.summary()
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO lldp_topology_map (local_device, local_port, neighbor_device)
                        VALUES (%s, %s, %s)
                    """, ("Probe_Local_Host", local_port, "Discovered_Neighbor"))
                conn.commit()
            log.info("LLDP frame capturado — topologia atualizada.")

        elif packet.haslayer("UDP") and packet["UDP"].dport == 2055:
            raw = bytes(packet["UDP"].payload)
            if len(raw) < 4:
                return
            version, count = struct.unpack("!HH", raw[:4])
            if version != 5:
                return
            offset = 24
            rows = []
            for _ in range(min(count, 30)):
                if len(raw) < offset + 28:
                    break
                fd = struct.unpack("!IIIIIIJ", raw[offset:offset + 28])
                src_ip = socket.inet_ntoa(struct.pack("!I", fd[0]))
                dst_ip = socket.inet_ntoa(struct.pack("!I", fd[1]))
                rows.append((src_ip, dst_ip, fd[4], fd[5]))
                offset += 48
            if rows:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.executemany("""
                            INSERT INTO netflow_traffic_samples (src_ip, dst_ip, packets, bytes)
                            VALUES (%s, %s, %s, %s)
                        """, rows)
                    conn.commit()
    except Exception:
        pass  # Silencia erros de parsing para não interromper a captura


def start_passive_sniffing(interface: str) -> None:
    """Inicia captura passiva na interface de rede. Requer root e Scapy."""
    if not SCAPY_AVAILABLE:
        log.error("Scapy não está instalado. Instale com: pip install scapy")
        return
    if os.geteuid() != 0:
        log.error("Captura passiva exige privilégios root (execute com sudo).")
        return
    log.info(f"Captura passiva iniciada na interface '{interface}' (LLDP + NetFlow v5)…")
    try:
        scapy.sniff(iface=interface, prn=_packet_callback, store=0)
    except KeyboardInterrupt:
        log.info("Captura passiva interrompida pelo usuário.")


# ── CLI principal ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="RMIFO — Multi-Protocol Network Probe"
    )
    parser.add_argument("--mode", required=True,
                        choices=["bgp", "snmp", "netflow", "full"],
                        help="Modo operacional da sonda")
    parser.add_argument("--target",
                        help="IP específico para testar individualmente (opcional)")
    parser.add_argument("--interface", default="eth0",
                        help="Interface para captura de tráfego passivo (padrão: eth0)")
    parser.add_argument("--bgp-delay", type=float, default=0.5, metavar="SEG",
                        help="Pausa entre requisições RIPE STAT em segundos (padrão: 0.5)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help=f"Número de workers paralelos (padrão: {MAX_WORKERS})")
    args = parser.parse_args()

    # Garante integridade do banco antes de rodar
    try:
        with get_connection() as conn:
            ensure_tables(conn)
    except Exception as e:
        log.critical(f"Incapaz de conectar ou inicializar tabelas do PostgreSQL: {e}")
        sys.exit(1)

    # Coleta alvos do banco RMIFO
    if args.target:
        targets = [{"ip_address": args.target, "cable_id": None, "name": None, "country_code": None}]
    else:
        targets = fetch_target_ips()

    if not targets and args.mode in ("bgp", "snmp", "full"):
        log.warning("Nenhum IP alvo ativo retornado da infraestrutura RMIFO.")

    # ── BGP
    if args.mode in ("bgp", "full") and targets:
        log.info(f"Sonda BGP iniciada — {len(targets)} alvos, delay={args.bgp_delay}s, workers={args.workers}")
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            ex.map(lambda t: probe_bgp_prefix(t, args.bgp_delay), targets)

    # ── SNMP
    if args.mode in ("snmp", "full"):
        if not SNMP_AVAILABLE:
            log.error("pysnmp-lextudio não instalado. Execute: pip install pysnmp-lextudio")
        elif targets:
            log.info(f"Sonda SNMP iniciada — {len(targets)} alvos, workers={args.workers}")
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                ex.map(probe_snmp_target, targets)

    # ── NetFlow/LLDP passivo
    if args.mode in ("netflow", "full"):
        if not SCAPY_AVAILABLE:
            log.error("Scapy não instalado. Execute: pip install scapy")
        else:
            start_passive_sniffing(args.interface)


if __name__ == "__main__":
    main()
