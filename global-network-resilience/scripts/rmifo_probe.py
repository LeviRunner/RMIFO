#!/usr/bin/env python3
"""
RMIFO — Motor de Sondas Unificado

Subcomandos:
  telemetry   Ingestão de telemetria por cabo (mtr / TCP / HTTP / sintético)
  multi       Sondas multi-protocolo: BGP (RIPE STAT), SNMP, LLDP, NetFlow
  validate    Detecção de anomalias contra baseline (7 dias)
  all         Executa telemetry → validate em sequência

Uso:
  python rmifo_probe.py telemetry [--limit N] [--full-scan] [--mtr-cycles N]
  python rmifo_probe.py multi --mode bgp|snmp|netflow|full [--target IP]
  python rmifo_probe.py validate
  python rmifo_probe.py all
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import random
import socket
import statistics
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import logging

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

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("rmifo.probe")

# ── Constantes ────────────────────────────────────────────────────────────────
TELEMETRY_MODEL_VERSION = "1.4.0"
PROBE_AGENT             = "RMIFO-GlobalNetworkResilience/1.4.0"
PROBE_HEADER            = f"X-Probe-Source: {PROBE_AGENT}"
RIPE_STAT_BASE          = "https://stat.ripe.net/data"
REQUEST_TIMEOUT         = 10
MAX_WORKERS             = int(os.getenv("PROBE_WORKERS", "8"))
SNMP_COMMUNITY          = os.getenv("SNMP_COMMUNITY", "public")
FULL_SCAN_PORTS         = (443, 80, 22, 8443, 8080)

# ── Cobertura calibrada por variação (16 variações ativas) ────────────────────
MTR_CYCLES_DEFAULT  = int(os.getenv("MTR_CYCLES",        "15"))  # ×hops → ~300 pkts/rota
MTR_CYCLES_UNSTABLE = int(os.getenv("MTR_CYCLES_UNST",   "30"))  # cabo instável: ×2
MTR_BUDGET_TOTAL    = int(os.getenv("MTR_BUDGET",        "300")) # limite global de ciclos
TCP_JITTER_PACKETS  = int(os.getenv("TCP_JITTER_PKTS",   "50"))  # p95 estável c/ 50 amostras
TCP_CONN_TIMEOUT    = float(os.getenv("TCP_CONN_TIMEOUT","4.0")) # segundos por porta
HTTP_RETRIES        = int(os.getenv("HTTP_RETRIES",      "3"))   # 3 requisições → média
HTTP_TIMEOUT        = int(os.getenv("HTTP_TIMEOUT",      "8"))   # segundos
SNMP_RETRIES        = int(os.getenv("SNMP_RETRIES",      "1"))   # 1 retry por OID
BGP_REQUEST_DELAY   = float(os.getenv("BGP_DELAY",       "0.5")) # rate-limit RIPE API
PACKETS_PER_ROUTE   = TCP_JITTER_PACKETS                         # alias → jitter passes

SNMP_OIDS = {
    "sysDescr":     "1.3.6.1.2.1.1.1.0",
    "sysUpTime":    "1.3.6.1.2.1.1.3.0",
    "ifOperStatus": "1.3.6.1.2.1.2.2.1.8.1",
    "ifInOctets":   "1.3.6.1.2.1.2.2.1.10.1",
    "ifOutOctets":  "1.3.6.1.2.1.2.2.1.16.1",
}

_RMIFO_TCP_PAYLOAD = (
    f"RMIFO-PROBE/1.0\r\n"
    f"Agent: {PROBE_AGENT}\r\n"
    f"Project: Global-Network-Resilience\r\n\r\n"
).encode()

BASE_DIR = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# Conexão ao banco
# ─────────────────────────────────────────────────────────────────────────────

def db_connect(base_dir: Path | None = None) -> psycopg2.extensions.connection:
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url)
    bd = base_dir or BASE_DIR
    return psycopg2.connect(
        host=os.getenv("PG_SOCKET", str(bd / ".local" / "run")),
        port=int(os.getenv("PG_PORT", os.getenv("PGPORT", "55432"))),
        dbname=os.getenv("DB_NAME", os.getenv("PGDATABASE", "global_network_resilience")),
        user=os.getenv("PGUSER", "postgres"),
        connect_timeout=10,
    )


@contextmanager
def get_connection(base_dir: Path | None = None):
    conn = db_connect(base_dir)
    try:
        yield conn
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# ── SUBCOMANDO: telemetry ─────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def get_log_dir(base_dir: Path) -> Path:
    d = base_dir / "data" / "local" / "log" / "telemetry"
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_packet_log(log_dir: Path, measured_at: datetime, record: dict) -> None:
    log_file = log_dir / f"{measured_at.strftime('%Y-%m-%d')}.jsonl"
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def get_cable_health_map(conn) -> dict[str, dict]:
    health_map: dict[str, dict] = {}
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT cable_id, avg_packet_loss_pct, avg_jitter_ms
                FROM telemetry_rollup_5m
                WHERE bucket >= NOW() - INTERVAL '30 minutes'
            """)
            for cid, loss, jitter in cur.fetchall():
                health_map[cid] = {"is_unstable": (loss or 0) > 2.0 or (jitter or 0) > 5.0}
    except Exception:
        pass
    return health_map


def extract_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        return parsed.hostname or None
    except Exception:
        return None


def probe_mtr(host: str, cycles: int = MTR_CYCLES_DEFAULT, timeout: int = HTTP_TIMEOUT):
    cmd = [
        "mtr", "--report", f"--report-cycles={cycles}",
        "--no-dns", "--json", "--timeout", str(timeout),
        "--comment", PROBE_AGENT, host,
    ]
    sent_at = datetime.now(timezone.utc)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        received_at = datetime.now(timezone.utc)
        data = json.loads(result.stdout)
        hubs = data.get("report", {}).get("hubs", [])
        if not hubs:
            return None, [], sent_at, received_at, "synthetic", "mtr-no-hops"

        hops: list[dict] = []
        last_good_avg: float | None = None
        for hub in hubs:
            ip_raw = hub.get("host", "???")
            hop_n = hub.get("count", len(hops) + 1)
            loss = float(hub.get("Loss%", 100.0))
            if ip_raw in ("???", "", None) or loss >= 100.0:
                hops.append({"n": hop_n, "ip": None, "loss_pct": 100.0})
                continue
            avg = float(hub.get("Avg", 0.0))
            hops.append({
                "n": hop_n, "ip": ip_raw,
                "rtt_last_ms":  round(float(hub.get("Last", avg)), 3),
                "rtt_avg_ms":   round(avg, 3),
                "rtt_best_ms":  round(float(hub.get("Best", avg)), 3),
                "rtt_worst_ms": round(float(hub.get("Wrst", avg)), 3),
                "rtt_stdev_ms": round(float(hub.get("StDev", 0.0)), 3),
                "loss_pct":     round(loss, 1),
                "sent":         hub.get("Snt", cycles),
            })
            if avg > 0:
                last_good_avg = avg

        if last_good_avg is None:
            return None, hops, sent_at, received_at, "synthetic", "mtr-zero-rtt"
        return round(last_good_avg, 3), hops, sent_at, received_at, "active_mtr", "mtr-udp"
    except subprocess.TimeoutExpired:
        return None, [], sent_at, datetime.now(timezone.utc), "synthetic", "mtr-timeout"
    except Exception:
        return None, [], sent_at, datetime.now(timezone.utc), "synthetic", "mtr-error"


def discover_open_ports(host: str, ports=FULL_SCAN_PORTS, timeout: float = 1.5) -> list[int]:
    open_ports: list[int] = []
    def check_port(port: int):
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
            return port, True
        except Exception:
            return port, False
    with ThreadPoolExecutor(max_workers=len(ports)) as ex:
        for port, is_open in ex.map(check_port, ports):
            if is_open:
                open_ports.append(port)
    return sorted(open_ports)


def _tcp_probe_port(host: str, port: int, timeout: float) -> float | None:
    try:
        t0 = datetime.now(timezone.utc)
        sock = socket.create_connection((host, port), timeout=timeout)
        t1 = datetime.now(timezone.utc)
        try:
            sock.sendall(_RMIFO_TCP_PAYLOAD)
        except Exception:
            pass
        sock.close()
        rtt_ms = (t1 - t0).total_seconds() * 1000.0
        return round(rtt_ms, 3) if rtt_ms > 0 else None
    except Exception:
        return None


def probe_tcp_connect(host: str, timeout: int = 6, full_scan: bool = False,
                      open_ports: list[int] | None = None):
    ports = (open_ports or FULL_SCAN_PORTS) if full_scan else (443, 80)
    timeout_per_port = 2.0 if full_scan else float(timeout)
    sent_at = datetime.now(timezone.utc)
    if not ports:
        return None, sent_at, datetime.now(timezone.utc), "synthetic", "tcp-no-open-ports"

    if full_scan:
        results: list[tuple[int, float]] = []
        with ThreadPoolExecutor(max_workers=max(1, len(ports))) as ex:
            futures = {ex.submit(_tcp_probe_port, host, p, timeout_per_port): p for p in ports}
            for fut in as_completed(futures):
                rtt = fut.result()
                if rtt is not None:
                    results.append((futures[fut], rtt))
        if results:
            best_port, best_rtt = min(results, key=lambda x: x[1])
            return best_rtt, sent_at, datetime.now(timezone.utc), "active_tcp_connect", f"tcp-full-scan-port{best_port}"
    else:
        for port in ports:
            rtt = _tcp_probe_port(host, port, timeout_per_port)
            if rtt is not None:
                return rtt, sent_at, datetime.now(timezone.utc), "active_tcp_connect", f"tcp-syn-ack-port{port}"

    return None, sent_at, datetime.now(timezone.utc), "synthetic", "tcp-connect-failed"


def probe_jitter_passes(host: str, packets: int = PACKETS_PER_ROUTE, timeout: float = 2.0,
                        open_ports: list[int] | None = None):
    ports_to_use = open_ports if open_ports else [443, 80]
    if not ports_to_use:
        return None, None, None, 100.0
    rtts: list[float] = []
    attempts = 0
    per_port = max(1, packets // len(ports_to_use))
    for port in ports_to_use:
        with ThreadPoolExecutor(max_workers=min(20, per_port)) as ex:
            futs = [ex.submit(_tcp_probe_port, host, port, timeout) for _ in range(per_port)]
            for fut in as_completed(futs):
                attempts += 1
                rtt = fut.result()
                if rtt is not None:
                    rtts.append(rtt)
    if len(rtts) < 2:
        loss = round((1 - len(rtts) / max(attempts, 1)) * 100, 2)
        return None, None, None, loss
    rtts_sorted = sorted(rtts)
    p95 = round(rtts_sorted[max(0, int(len(rtts_sorted) * 0.95) - 1)], 3)
    loss_pct = round((1 - len(rtts) / max(attempts, 1)) * 100, 2)
    return round(statistics.mean(rtts), 3), round(statistics.stdev(rtts), 3), p95, loss_pct


def probe_http_latency(url: str | None, timeout: int = 8):
    if not url:
        return None, datetime.now(timezone.utc), datetime.now(timezone.utc), "synthetic", "heuristic-no-url"
    cmd = [
        "curl", "-o", "/dev/null", "-sS", "-L",
        "--connect-timeout", str(timeout), "--max-time", str(timeout),
        "-A", PROBE_AGENT, "-H", PROBE_HEADER,
        "-H", "X-Probe-Project: Global-Network-Resilience",
        "-w", "%{time_total}", url,
    ]
    sent_at = datetime.now(timezone.utc)
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        received_at = datetime.now(timezone.utc)
        latency_ms = float(result.stdout.strip() or "0") * 1000.0
        if latency_ms <= 0:
            raise ValueError("zero latency")
        return latency_ms, sent_at, received_at, "active_http_probe", "curl-time-total"
    except Exception:
        return None, sent_at, datetime.now(timezone.utc), "synthetic", "heuristic-fallback"


def synthetic_from_cable(length_km, capacity_gbps, endpoint_count) -> dict:
    length_km = max(100, int(length_km or 1200))
    endpoint_count = max(2, int(endpoint_count or 2))
    capacity_gbps = float(capacity_gbps) if capacity_gbps else max(2.0, 12000.0 / length_km)
    base_latency = max(8.0, (length_km / 200.0) * 1.35)
    load_factor = 0.52 + min(0.35, endpoint_count / 120.0)
    throughput_mbps = capacity_gbps * 1000.0 * load_factor
    jitter_ms = max(0.6, base_latency * 0.06)
    packet_loss_pct = max(0.02, min(2.0, 0.08 + (jitter_ms / 80.0)))
    availability_pct = max(98.0, min(100.0, 99.97 - (packet_loss_pct * 0.04)))
    return {
        "latency_ms":         round(base_latency, 3),
        "jitter_ms":          round(jitter_ms, 3),
        "packet_loss_pct":    round(packet_loss_pct, 3),
        "throughput_mbps":    round(throughput_mbps, 3),
        "packets_per_second": max(1000, int((throughput_mbps * 1e6) / (8.0 * 900.0))),
        "availability_pct":   round(availability_pct, 3),
    }


def ensure_telemetry_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cable_telemetry_samples (
                sample_id               BIGSERIAL PRIMARY KEY,
                cable_id                VARCHAR(255) NOT NULL REFERENCES cables(cable_id) ON DELETE CASCADE,
                measured_at             TIMESTAMPTZ NOT NULL,
                sent_at                 TIMESTAMPTZ,
                received_at             TIMESTAMPTZ,
                latency_ms              DOUBLE PRECISION,
                jitter_ms               DOUBLE PRECISION,
                packet_loss_pct         DOUBLE PRECISION,
                throughput_mbps         DOUBLE PRECISION,
                packets_per_second      BIGINT,
                availability_pct        DOUBLE PRECISION,
                route_error_count       INTEGER,
                source                  VARCHAR(80)  NOT NULL DEFAULT 'synthetic',
                method                  VARCHAR(120) NOT NULL DEFAULT 'heuristic',
                confidence_score        DOUBLE PRECISION NOT NULL DEFAULT 0.55,
                estimated               BOOLEAN NOT NULL DEFAULT TRUE,
                telemetry_model_version VARCHAR(40)  NOT NULL DEFAULT '1.0.0',
                metadata                JSONB,
                UNIQUE (cable_id, measured_at)
            );
            ALTER TABLE cable_telemetry_samples
                ADD COLUMN IF NOT EXISTS sent_at     TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ;
            CREATE TABLE IF NOT EXISTS cable_telemetry_hops (
                hop_id       BIGSERIAL PRIMARY KEY,
                sample_id    BIGINT NOT NULL REFERENCES cable_telemetry_samples(sample_id) ON DELETE CASCADE,
                cable_id     VARCHAR(255) NOT NULL,
                hop_number   INTEGER NOT NULL,
                hop_ip       INET,
                rtt_last_ms  DOUBLE PRECISION,
                rtt_avg_ms   DOUBLE PRECISION,
                rtt_best_ms  DOUBLE PRECISION,
                rtt_worst_ms DOUBLE PRECISION,
                rtt_stdev_ms DOUBLE PRECISION,
                loss_pct     DOUBLE PRECISION,
                recorded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (sample_id, hop_number)
            );
            CREATE INDEX IF NOT EXISTS idx_hops_sample    ON cable_telemetry_hops(sample_id);
            CREATE INDEX IF NOT EXISTS idx_hops_cable_hop ON cable_telemetry_hops(cable_id, hop_number);
            CREATE INDEX IF NOT EXISTS idx_hops_ip        ON cable_telemetry_hops(hop_ip);
            CREATE TABLE IF NOT EXISTS cable_telemetry_reference (
                cable_id                  VARCHAR(255) PRIMARY KEY REFERENCES cables(cable_id) ON DELETE CASCADE,
                baseline_latency_ms       DOUBLE PRECISION,
                baseline_jitter_ms        DOUBLE PRECISION,
                baseline_packet_loss_pct  DOUBLE PRECISION,
                baseline_throughput_mbps  DOUBLE PRECISION,
                baseline_availability_pct DOUBLE PRECISION,
                updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                notes                     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_telemetry_cable_time    ON cable_telemetry_samples(cable_id, measured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_telemetry_time          ON cable_telemetry_samples(measured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_telemetry_source_method ON cable_telemetry_samples(source, method);
        """)


def upsert_reference(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO cable_telemetry_reference (
                cable_id, baseline_latency_ms, baseline_jitter_ms,
                baseline_packet_loss_pct, baseline_throughput_mbps,
                baseline_availability_pct, updated_at, notes
            )
            SELECT cable_id,
                AVG(latency_ms)       FILTER (WHERE latency_ms       IS NOT NULL),
                AVG(jitter_ms)        FILTER (WHERE jitter_ms        IS NOT NULL),
                AVG(packet_loss_pct)  FILTER (WHERE packet_loss_pct  IS NOT NULL),
                AVG(throughput_mbps)  FILTER (WHERE throughput_mbps  IS NOT NULL),
                AVG(availability_pct) FILTER (WHERE availability_pct IS NOT NULL),
                NOW(), 'baseline recalculado automaticamente (janela 7d)'
            FROM cable_telemetry_samples
            WHERE measured_at >= NOW() - INTERVAL '7 days'
            GROUP BY cable_id
            ON CONFLICT (cable_id) DO UPDATE SET
                baseline_latency_ms       = EXCLUDED.baseline_latency_ms,
                baseline_jitter_ms        = EXCLUDED.baseline_jitter_ms,
                baseline_packet_loss_pct  = EXCLUDED.baseline_packet_loss_pct,
                baseline_throughput_mbps  = EXCLUDED.baseline_throughput_mbps,
                baseline_availability_pct = EXCLUDED.baseline_availability_pct,
                updated_at = NOW(), notes = EXCLUDED.notes
        """)


def get_proj_hist_dir(measured_at: datetime) -> Path:
    d = BASE_DIR / "data" / "local" / "historico" / measured_at.strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_history(measured_at: datetime, summary: dict, tag: str = "probe") -> Path:
    hist_dir = get_proj_hist_dir(measured_at)
    path = hist_dir / f"{measured_at.strftime('%Y-%m-%d')}_{tag}_{measured_at.strftime('%H%M%S')}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)
    return path


def write_live_status(live_file: Path, status: dict) -> None:
    tmp = live_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(live_file)


def cmd_telemetry(args: argparse.Namespace) -> None:
    base_dir = Path(os.getenv("WORKSPACE_ROOT", str(BASE_DIR.parent)))
    random.seed(args.seed)
    measured_at = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    conn = db_connect(base_dir)
    conn.autocommit = False
    log_dir = get_log_dir(base_dir)
    stats = {"mtr": 0, "tcp": 0, "http": 0, "synthetic": 0, "hops_total": 0}
    probe_records: list[dict] = []
    mtr_budget = MTR_BUDGET_TOTAL
    used_budget = 0

    with conn:
        ensure_telemetry_schema(conn)
        health_map = get_cable_health_map(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.cable_id, c.cable_name, c.length_km, c.capacity_gbps,
                       c.landing_points_count, cm.public_url
                FROM cables c
                LEFT JOIN cable_metadata cm ON cm.cable_id = c.cable_id
                ORDER BY c.cable_id LIMIT %s
            """, (args.limit,))
            cables = cur.fetchall()

    proj_hist_dir = get_proj_hist_dir(measured_at)
    live_file     = proj_hist_dir / ".probe_live.json"
    cables_jsonl  = proj_hist_dir / "cables_live.jsonl"
    inserted = 0

    for cable_id, cable_name, length_km, capacity_gbps, lp_count, public_url in cables:
        cable_name = cable_name or cable_id
        baseline   = synthetic_from_cable(length_km, capacity_gbps, lp_count)
        host       = extract_host(public_url)

        hops: list[dict] = []
        sent_at = received_at = None
        latency_ms: float | None = None
        jitter_ms = baseline["jitter_ms"]
        packet_loss_pct = baseline["packet_loss_pct"]
        source, method, confidence, estimated = "synthetic", "heuristic", 0.56, True

        open_ports: list[int] = []
        if host and args.full_scan:
            open_ports = discover_open_ports(host, ports=FULL_SCAN_PORTS, timeout=1.5)

        current_cycles = (
            MTR_CYCLES_UNSTABLE
            if health_map.get(cable_id, {}).get("is_unstable") else args.mtr_cycles
        )
        if used_budget + current_cycles > mtr_budget:
            current_cycles = 1

        # 1. mtr
        if host and used_budget < mtr_budget:
            mtr_lat, hops, sent_at, received_at, source, method = probe_mtr(
                host, cycles=current_cycles, timeout=args.timeout)
            if mtr_lat and mtr_lat > 0:
                latency_ms = round((baseline["latency_ms"] * 0.4) + (mtr_lat * 0.6), 3)
                confidence, estimated = 0.90, False
                stats["mtr"] += 1
                used_budget += current_cycles

        # 2. TCP
        if latency_ms is None and host:
            tcp_lat, sent_at, received_at, source, method = probe_tcp_connect(
                host, timeout=max(4, args.timeout // 2), full_scan=args.full_scan,
                open_ports=open_ports if args.full_scan else None)
            if tcp_lat and tcp_lat > 0:
                latency_ms = round((baseline["latency_ms"] * 0.5) + (tcp_lat * 0.5), 3)
                confidence, estimated = 0.78, False
                stats["tcp"] += 1
                if args.full_scan and open_ports:
                    mean_r, jit_r, p95_r, loss_r = probe_jitter_passes(
                        host, packets=PACKETS_PER_ROUTE, timeout=2.0, open_ports=open_ports)
                    if mean_r is not None:
                        latency_ms      = round((baseline["latency_ms"] * 0.3) + (mean_r * 0.7), 3)
                        jitter_ms       = jit_r or jitter_ms
                        packet_loss_pct = loss_r if loss_r is not None else packet_loss_pct
                        confidence      = 0.95
                        method          = f"tcp-{PACKETS_PER_ROUTE}pkt-ports{open_ports}-p95={p95_r}ms"

        # 3. HTTP
        if latency_ms is None and public_url:
            http_lat, sent_at, received_at, source, method = probe_http_latency(
                public_url, timeout=args.timeout)
            if http_lat and http_lat > 0:
                latency_ms = round((baseline["latency_ms"] * 0.6) + (http_lat * 0.4), 3)
                confidence, estimated = 0.82, False
                stats["http"] += 1

        # 4. Sintético
        if latency_ms is None:
            latency_ms = baseline["latency_ms"] * (1 + random.uniform(-0.05, 0.05))
            stats["synthetic"] += 1

        throughput_mbps    = baseline["throughput_mbps"] * (1 + random.uniform(-0.08, 0.08))
        jitter_ms          = max(0.5, jitter_ms * (1 + random.uniform(-0.15, 0.20)))
        packet_loss_pct    = max(0.01, packet_loss_pct * (1 + random.uniform(-0.2, 0.35)))
        availability_pct   = max(97.5, min(100.0, baseline["availability_pct"] - packet_loss_pct * 0.03))
        packets_per_second = int((throughput_mbps * 1e6) / (8.0 * 900.0))
        route_error_count  = int(packet_loss_pct > 0.25)
        elapsed_ms         = round((received_at - sent_at).total_seconds() * 1000.0, 3) if sent_at and received_at else None

        metadata = {
            "collector": "rmifo_probe.py",
            "probe_protocol": method,
            "target_host": host,
            "total_elapsed_ms": elapsed_ms,
            "total_hops": len(hops) if hops else None,
            "hops_visible": sum(1 for h in hops if h.get("ip")) if hops else None,
        }

        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cable_telemetry_samples (
                        cable_id, measured_at, sent_at, received_at,
                        latency_ms, jitter_ms, packet_loss_pct, throughput_mbps,
                        packets_per_second, availability_pct, route_error_count,
                        source, method, confidence_score, estimated,
                        telemetry_model_version, metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (cable_id, measured_at) DO UPDATE SET
                        sent_at                 = EXCLUDED.sent_at,
                        received_at             = EXCLUDED.received_at,
                        latency_ms              = EXCLUDED.latency_ms,
                        jitter_ms               = EXCLUDED.jitter_ms,
                        packet_loss_pct         = EXCLUDED.packet_loss_pct,
                        throughput_mbps         = EXCLUDED.throughput_mbps,
                        packets_per_second      = EXCLUDED.packets_per_second,
                        availability_pct        = EXCLUDED.availability_pct,
                        route_error_count       = EXCLUDED.route_error_count,
                        source                  = EXCLUDED.source,
                        method                  = EXCLUDED.method,
                        confidence_score        = EXCLUDED.confidence_score,
                        estimated               = EXCLUDED.estimated,
                        telemetry_model_version = EXCLUDED.telemetry_model_version,
                        metadata                = EXCLUDED.metadata
                    RETURNING sample_id
                """, (
                    cable_id, measured_at, sent_at, received_at,
                    round(latency_ms, 3), round(jitter_ms, 3), round(packet_loss_pct, 3),
                    round(throughput_mbps, 3), max(1000, packets_per_second),
                    round(availability_pct, 3), route_error_count,
                    source, method, round(confidence, 3), estimated,
                    TELEMETRY_MODEL_VERSION, json.dumps(metadata),
                ))
                row = cur.fetchone()
                sample_id = row[0] if row else None
                inserted += 1

                packet_record = {
                    "cable_id": cable_id, "measured_at": measured_at.isoformat(),
                    "sent_at": sent_at.isoformat() if sent_at else None,
                    "received_at": received_at.isoformat() if received_at else None,
                    "latency_ms": round(latency_ms, 3), "jitter_ms": round(jitter_ms, 3),
                    "packet_loss_pct": round(packet_loss_pct, 3),
                    "throughput_mbps": round(throughput_mbps, 3),
                    "packets_per_second": max(1000, packets_per_second),
                    "availability_pct": round(availability_pct, 3),
                    "route_error_count": route_error_count,
                    "source": source, "method": method,
                    "confidence_score": round(confidence, 3), "estimated": estimated,
                    "telemetry_model_version": TELEMETRY_MODEL_VERSION,
                    "open_ports": open_ports,
                    "packets_per_route": PACKETS_PER_ROUTE if args.full_scan else None,
                    "probe_agent": PROBE_AGENT, "metadata": metadata,
                    "hops": hops if hops else [],
                }
                append_packet_log(log_dir, measured_at, packet_record)
                probe_records.append(packet_record)

                if sample_id and hops:
                    for hop in hops:
                        if hop.get("ip") is None:
                            continue
                        cur.execute("""
                            INSERT INTO cable_telemetry_hops
                                (sample_id, cable_id, hop_number, hop_ip,
                                 rtt_last_ms, rtt_avg_ms, rtt_best_ms,
                                 rtt_worst_ms, rtt_stdev_ms, loss_pct)
                            VALUES (%s,%s,%s,%s::inet,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (sample_id, hop_number) DO UPDATE SET
                                hop_ip       = EXCLUDED.hop_ip,
                                rtt_last_ms  = EXCLUDED.rtt_last_ms,
                                rtt_avg_ms   = EXCLUDED.rtt_avg_ms,
                                rtt_best_ms  = EXCLUDED.rtt_best_ms,
                                rtt_worst_ms = EXCLUDED.rtt_worst_ms,
                                rtt_stdev_ms = EXCLUDED.rtt_stdev_ms,
                                loss_pct     = EXCLUDED.loss_pct
                        """, (
                            sample_id, cable_id, hop["n"], hop["ip"],
                            hop.get("rtt_last_ms"), hop.get("rtt_avg_ms"), hop.get("rtt_best_ms"),
                            hop.get("rtt_worst_ms"), hop.get("rtt_stdev_ms"), hop.get("loss_pct"),
                        ))
                        stats["hops_total"] += 1

        prog = (inserted / len(cables)) * 100
        print(f"  [{inserted:>4}/{len(cables)}] {prog:5.1f}%  {cable_id:<40} "
              f"proto={method:<28} lat={round(latency_ms,1):>8.1f}ms  hops={len(hops):>2}", flush=True)

        write_live_status(live_file, {
            "cable_id": cable_id, "cable_name": cable_name,
            "current": inserted, "total": len(cables),
            "percent": round(prog, 1), "proto": method,
            "latency_ms": round(latency_ms, 1),
            "tcp": stats["tcp"], "synthetic": stats["synthetic"],
            "mtr": stats["mtr"], "http": stats["http"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

        with cables_jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "cable_id": cable_id, "cable_name": cable_name,
                "measured_at": measured_at.isoformat(),
                "latency_ms": round(latency_ms, 3), "jitter_ms": round(jitter_ms, 3),
                "packet_loss_pct": round(packet_loss_pct, 3),
                "source": source, "method": method, "open_ports": open_ports,
            }, ensure_ascii=False, default=str) + "\n")

        if inserted % 50 == 0:
            cp_at   = datetime.now(timezone.utc)
            recent  = probe_records[-50:]
            lats    = [r["latency_ms"]      for r in recent if r["latency_ms"]      is not None]
            jits    = [r["jitter_ms"]       for r in recent if r["jitter_ms"]       is not None]
            losses  = [r["packet_loss_pct"] for r in recent if r["packet_loss_pct"] is not None]
            cp_path = save_history(cp_at, {
                "checkpoint": inserted, "total_cabos": len(cables),
                "progresso_pct": round(prog, 1), "gerado_em": cp_at.isoformat(),
                "ultimos_50": {
                    "mtr": stats["mtr"], "tcp": stats["tcp"],
                    "http": stats["http"], "sintetico": stats["synthetic"],
                    "lat_media_ms":    round(sum(lats)   / len(lats),   2) if lats   else None,
                    "jit_media_ms":    round(sum(jits)   / len(jits),   2) if jits   else None,
                    "perda_media_pct": round(sum(losses) / len(losses), 3) if losses else None,
                },
                "registros": recent,
            }, tag=f"checkpoint_{inserted:04d}")
            print(f"\n  *** CHECKPOINT {inserted}/{len(cables)} ({prog:.1f}%) *** → {cp_path}\n", flush=True)

    with conn:
        upsert_reference(conn)

    finished_at = datetime.now(timezone.utc)
    tag = "probe_full" if args.full_scan else "probe"
    hist_path = save_history(measured_at, {
        "gerado_em": finished_at.isoformat(), "medido_em": measured_at.isoformat(),
        "modo": "full_scan" if args.full_scan else "standard",
        "telemetry_model": TELEMETRY_MODEL_VERSION,
        "totais": {"cabos": inserted, "mtr": stats["mtr"], "tcp": stats["tcp"],
                   "http": stats["http"], "sintetico": stats["synthetic"], "hops": stats["hops_total"]},
        "registros": probe_records,
    }, tag=tag)

    print(f"\nConcluido em {finished_at.isoformat()}\n"
          f"  Cabos inseridos : {inserted}\n"
          f"  mtr (hops)      : {stats['mtr']}\n"
          f"  TCP SYN-ACK     : {stats['tcp']}\n"
          f"  HTTP curl       : {stats['http']}\n"
          f"  Sintetico       : {stats['synthetic']}\n"
          f"  Hops gravados   : {stats['hops_total']}\n"
          f"  Log de pacotes  : {log_dir}\n"
          f"  Historico       : {hist_path}")


# ─────────────────────────────────────────────────────────────────────────────
# ── SUBCOMANDO: multi (BGP / SNMP / LLDP / NetFlow) ──────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def ensure_multi_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bgp_probe_results (
                id              SERIAL PRIMARY KEY,
                probed_at       TIMESTAMPTZ DEFAULT NOW(),
                prefix          TEXT NOT NULL,
                cable_id        VARCHAR(255),
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
                cable_id         VARCHAR(255),
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
        """)
    conn.commit()


def fetch_target_ips() -> list[dict]:
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("""
                    SELECT DISTINCT eis.ip_start::text AS ip_address,
                                    clp.cable_id,
                                    c.cable_name AS name,
                                    lp.country_code
                    FROM endpoint_ip_samples eis
                    JOIN cable_landing_points clp USING (endpoint_id)
                    JOIN cables               c   ON c.cable_id   = clp.cable_id
                    JOIN landing_points       lp  ON lp.endpoint_id = clp.endpoint_id
                    WHERE eis.ip_start IS NOT NULL
                    LIMIT 100
                """)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        log.error(f"Erro ao buscar IPs alvos: {e}")
        return []


def probe_bgp_prefix(target: dict, bgp_delay: float = 0.5) -> None:
    ip = target["ip_address"]
    try:
        prefix = str(ipaddress.ip_network(ip, strict=False))
    except Exception:
        prefix = ip
    url = f"{RIPE_STAT_BASE}/routing-status/data.json?resource={ip}"
    try:
        res = requests.get(url, timeout=REQUEST_TIMEOUT,
                           headers={"User-Agent": PROBE_AGENT})
        if res.status_code == 200:
            data = res.json().get("data", {})
            bgp_state    = data.get("bgp_state", [])
            peers_seeing = len(bgp_state)
            origin_asn   = bgp_state[0].get("origin_as", "Unknown") if bgp_state else "Unknown"
            as_path      = json.dumps([p.get("as_path", []) for p in bgp_state])
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO bgp_probe_results
                            (prefix, cable_id, cable_name, landing_country,
                             peers_seeing, origin_asn, as_path, visibility_pct, raw_json)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (prefix, target.get("cable_id"), target.get("name"),
                          target.get("country_code"), peers_seeing, origin_asn,
                          as_path, data.get("visibility_percentage", 100.0), json.dumps(data)))
                conn.commit()
            log.info(f"BGP OK — {prefix}  peers={peers_seeing}")
        else:
            log.warning(f"BGP HTTP {res.status_code} para {ip}")
    except Exception as e:
        log.error(f"BGP falhou para {ip}: {e}")
    finally:
        if bgp_delay > 0:
            time.sleep(bgp_delay)


def probe_snmp_target(target: dict) -> None:
    if not SNMP_AVAILABLE:
        log.warning("pysnmp-lextudio não disponível (pip install pysnmp-lextudio)")
        return
    ip = target["ip_address"]
    results: dict = {}
    last_error = None
    for key, oid in SNMP_OIDS.items():
        try:
            err_ind, err_status, _, var_binds = next(getCmd(
                SnmpEngine(),
                CommunityData(SNMP_COMMUNITY, mpModel=1),
                UdpTransportTarget((ip, 161), timeout=2, retries=SNMP_RETRIES),
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            ))
            if err_ind or err_status:
                last_error = str(err_ind or err_status)
            else:
                results[key] = str(var_binds[0][1])
        except Exception as e:
            last_error = str(e)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO snmp_probe_results
                        (target_ip, cable_id, sys_descr, sys_uptime_ticks,
                         if_oper_status, in_octets, out_octets, error_message)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    ip, target.get("cable_id"),
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
        log.error(f"SNMP save falhou para {ip}: {e}")


def _packet_callback(packet) -> None:
    try:
        if packet.haslayer("Link Layer Discovery Protocol"):
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO lldp_topology_map (local_device, local_port, neighbor_device)
                        VALUES (%s,%s,%s)
                    """, ("Probe_Local_Host", packet.summary(), "Discovered_Neighbor"))
                conn.commit()
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
                rows.append((
                    socket.inet_ntoa(struct.pack("!I", fd[0])),
                    socket.inet_ntoa(struct.pack("!I", fd[1])),
                    fd[4], fd[5],
                ))
                offset += 48
            if rows:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.executemany("""
                            INSERT INTO netflow_traffic_samples (src_ip, dst_ip, packets, bytes)
                            VALUES (%s,%s,%s,%s)
                        """, rows)
                    conn.commit()
    except Exception:
        pass


def start_passive_sniffing(interface: str) -> None:
    if not SCAPY_AVAILABLE:
        log.error("Scapy não instalado (pip install scapy)")
        return
    if os.geteuid() != 0:
        log.error("Captura passiva exige root (execute com sudo)")
        return
    log.info(f"Captura passiva iniciada na interface '{interface}'…")
    try:
        scapy.sniff(iface=interface, prn=_packet_callback, store=0)
    except KeyboardInterrupt:
        log.info("Captura passiva interrompida.")


def cmd_multi(args: argparse.Namespace) -> None:
    with get_connection() as conn:
        ensure_multi_tables(conn)

    targets = ([{"ip_address": args.target, "cable_id": None, "name": None, "country_code": None}]
               if args.target else fetch_target_ips())

    if not targets and args.mode in ("bgp", "snmp", "full"):
        log.warning("Nenhum IP alvo encontrado.")

    if args.mode in ("bgp", "full") and targets:
        log.info(f"BGP — {len(targets)} alvos, delay={args.bgp_delay}s")
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            ex.map(lambda t: probe_bgp_prefix(t, args.bgp_delay), targets)

    if args.mode in ("snmp", "full"):
        if SNMP_AVAILABLE and targets:
            log.info(f"SNMP — {len(targets)} alvos")
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                ex.map(probe_snmp_target, targets)

    if args.mode in ("netflow", "full"):
        start_passive_sniffing(args.interface)


# ─────────────────────────────────────────────────────────────────────────────
# ── SUBCOMANDO: validate ──────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def cmd_validate(_args: argparse.Namespace) -> None:
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.cable_id,
                       r.avg_latency_ms      AS current_lat,
                       ref.baseline_latency_ms AS base_lat,
                       r.avg_packet_loss_pct AS current_loss,
                       ref.baseline_packet_loss_pct AS base_loss
                FROM telemetry_rollup_1h r
                JOIN cable_telemetry_reference ref ON r.cable_id = ref.cable_id
                WHERE r.bucket >= NOW() - INTERVAL '2 hours'
                  AND (
                      r.avg_latency_ms > (ref.baseline_latency_ms * 1.3) OR
                      r.avg_packet_loss_pct > 2.0
                  )
            """)
            anomalies = cur.fetchall()
            for cid, c_lat, b_lat, c_loss, b_loss in anomalies:
                severity = 'critical' if (c_loss > 5.0 or c_lat > (b_lat or 0) * 2) else 'warning'
                metric   = 'latency' if c_lat > (b_lat or 0) * 1.3 else 'packet_loss'
                val      = c_lat if metric == 'latency' else c_loss
                base     = b_lat if metric == 'latency' else b_loss
                cur.execute("""
                    INSERT INTO telemetry_alerts
                        (cable_id, metric, severity, current_value, baseline_value, description)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                """, (cid, metric, severity, val, base,
                      f"Desvio detectado: {metric} em {val} (baseline: {base})"))
            conn.commit()
        print(f"[{datetime.now()}] Validação concluída. {len(anomalies)} anomalias processadas.")
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# ── CLI ───────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RMIFO — Motor de Sondas Unificado",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="subcmd", required=True)

    # ── telemetry ─────────────────────────────────────────────────────────────
    p_tel = sub.add_parser("telemetry", help="Ingestão de telemetria por cabo")
    p_tel.add_argument("--limit",        type=int,  default=9999)
    p_tel.add_argument("--seed",         type=int,  default=42)
    p_tel.add_argument("--timeout",      type=int,  default=6)
    p_tel.add_argument("--mtr-cycles",   type=int,  default=MTR_CYCLES_DEFAULT)
    p_tel.add_argument("--full-scan",    action="store_true")
    p_tel.add_argument("--probe-report", type=str,  default="")

    # ── multi ─────────────────────────────────────────────────────────────────
    p_mul = sub.add_parser("multi", help="BGP / SNMP / LLDP / NetFlow")
    p_mul.add_argument("--mode",      required=True, choices=["bgp", "snmp", "netflow", "full"])
    p_mul.add_argument("--target",    default=None,  help="IP específico (opcional)")
    p_mul.add_argument("--interface", default="eth0")
    p_mul.add_argument("--bgp-delay", type=float,    default=0.5, metavar="SEG")
    p_mul.add_argument("--workers",   type=int,      default=MAX_WORKERS)

    # ── validate ──────────────────────────────────────────────────────────────
    sub.add_parser("validate", help="Detecta anomalias contra baseline (7d)")

    # ── all ───────────────────────────────────────────────────────────────────
    p_all = sub.add_parser("all", help="telemetry → validate em sequência")
    p_all.add_argument("--limit",      type=int,  default=9999)
    p_all.add_argument("--seed",       type=int,  default=42)
    p_all.add_argument("--timeout",    type=int,  default=6)
    p_all.add_argument("--mtr-cycles", type=int,  default=MTR_CYCLES_DEFAULT)
    p_all.add_argument("--full-scan",  action="store_true")
    p_all.add_argument("--probe-report", type=str, default="")

    return parser


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    if args.subcmd == "telemetry":
        cmd_telemetry(args)
    elif args.subcmd == "multi":
        cmd_multi(args)
    elif args.subcmd == "validate":
        cmd_validate(args)
    elif args.subcmd == "all":
        cmd_telemetry(args)
        cmd_validate(args)


if __name__ == "__main__":
    main()
