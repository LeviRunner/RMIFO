#!/usr/bin/env python3
"""
Ingestão de telemetria por cabo submarino.

Hierarquia de protocolos de medição (do mais rico ao mais simples):
  1. mtr UDP  – traceroute contínuo; registra caminho completo (hops), RTT por salto,
                sent_at e received_at exatos.
  2. TCP SYN-ACK – socket connect puro (sem raw sockets/root); RTT de handshake,
                   sent_at e received_at exatos.
  3. HTTP curl   – latência de resposta HTTP, fallback quando porta TCP não responde.
  4. Sintético   – modelo heurístico quando nenhuma sonda é bem-sucedida.

Tabelas gravadas:
  • cable_telemetry_samples  – uma linha por cabo por execução + sent_at/received_at
  • cable_telemetry_hops     – uma linha por salto registrado pelo mtr
  • cable_telemetry_reference – baseline rolante (7 dias)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Log local de pacotes de rede
# ---------------------------------------------------------------------------

def get_log_dir(base_dir: Path) -> Path:
    log_dir = base_dir / "data" / "local" / "log" / "telemetry"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def append_packet_log(log_dir: Path, measured_at: datetime, record: dict) -> None:
    """Grava uma linha JSONL no arquivo de log diário dos pacotes de rede."""
    date_str = measured_at.strftime("%Y-%m-%d")
    log_file = log_dir / f"{date_str}.jsonl"
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

import psycopg2

TELEMETRY_MODEL_VERSION = "1.3.0"


# ---------------------------------------------------------------------------
# Conexão
# ---------------------------------------------------------------------------

def db_connect(base_dir: Path):
    return psycopg2.connect(
        host=os.getenv("PG_SOCKET", str(base_dir / ".local" / "run")),
        port=int(os.getenv("PG_PORT", "55432")),
        dbname=os.getenv("DB_NAME", "global_network_resilience"),
        user=os.getenv("PGUSER", "postgres"),
    )

def get_cable_health_map(conn) -> dict[str, dict]:
    """Busca o estado de saúde recente (últimos 30m) para decidir a agressividade da coleta."""
    health_map = {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT cable_id, avg_packet_loss_pct, avg_jitter_ms 
            FROM telemetry_rollup_5m 
            WHERE bucket >= NOW() - INTERVAL '30 minutes'
        """)
        for cid, loss, jitter in cur.fetchall():
            # Define se o cabo está em 'alerta'
            is_unstable = (loss or 0) > 2.0 or (jitter or 0) > 5.0
            health_map[cid] = {"is_unstable": is_unstable}
    return health_map


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def extract_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        return parsed.hostname or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Protocolo 1: mtr UDP
# Registra: sent_at, received_at, RTT por hop (Last/Avg/Best/Wrst/StDev/Loss%)
# ---------------------------------------------------------------------------

def probe_mtr(
    host: str,
    cycles: int = 3,
    timeout: int = 10,
) -> tuple[float | None, list[dict], datetime, datetime, str, str]:
    cmd = [
        "mtr", "--report", f"--report-cycles={cycles}",
        "--no-dns", "--json", "--timeout", str(timeout), host,
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
                "n": hop_n,
                "ip": ip_raw,
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


# ---------------------------------------------------------------------------
# Protocolo 2: TCP SYN-ACK timing (sem raw sockets)
# ---------------------------------------------------------------------------

def probe_tcp_connect(
    host: str,
    timeout: int = 6,
) -> tuple[float | None, datetime, datetime, str, str]:
    sent_at = datetime.now(timezone.utc)
    for port in (443, 80):
        try:
            t0 = datetime.now(timezone.utc)
            sock = socket.create_connection((host, port), timeout=timeout)
            t1 = datetime.now(timezone.utc)
            sock.close()
            rtt_ms = (t1 - t0).total_seconds() * 1000.0
            if rtt_ms > 0:
                return round(rtt_ms, 3), sent_at, t1, "active_tcp_connect", f"tcp-syn-ack-port{port}"
        except Exception:
            continue
    return None, sent_at, datetime.now(timezone.utc), "synthetic", "tcp-connect-failed"


# ---------------------------------------------------------------------------
# Protocolo 3: HTTP curl
# ---------------------------------------------------------------------------

def probe_http_latency(
    url: str | None,
    timeout: int = 8,
) -> tuple[float | None, datetime, datetime, str, str]:
    if not url:
        return None, datetime.now(timezone.utc), datetime.now(timezone.utc), "synthetic", "heuristic-no-url"
    cmd = [
        "curl", "-o", "/dev/null", "-sS", "-L",
        "--connect-timeout", str(timeout), "--max-time", str(timeout),
        "-w", "%{time_total}", url,
    ]
    sent_at = datetime.now(timezone.utc)
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        received_at = datetime.now(timezone.utc)
        latency_ms = float(result.stdout.strip() or "0") * 1000.0
        if latency_ms <= 0:
            raise ValueError("latency zero")
        return latency_ms, sent_at, received_at, "active_http_probe", "curl-time-total"
    except Exception:
        return None, sent_at, datetime.now(timezone.utc), "synthetic", "heuristic-fallback"


# ---------------------------------------------------------------------------
# Modelo sintético (fallback total)
# ---------------------------------------------------------------------------

def synthetic_from_cable(
    length_km: int | None,
    capacity_gbps: int | None,
    endpoint_count: int | None,
) -> dict:
    length_km = max(100, int(length_km or 1200))
    endpoint_count = max(2, int(endpoint_count or 2))
    capacity_gbps = float(capacity_gbps) if capacity_gbps else max(2.0, 12000.0 / length_km)

    base_latency = max(8.0, (length_km / 200.0) * 1.35)
    load_factor = 0.52 + min(0.35, endpoint_count / 120.0)
    throughput_mbps = capacity_gbps * 1000.0 * load_factor
    jitter_ms = max(0.6, base_latency * 0.06)
    packet_loss_pct = max(0.02, min(2.0, 0.08 + (jitter_ms / 80.0)))
    availability_pct = max(98.0, min(100.0, 99.97 - (packet_loss_pct * 0.04)))
    packets_per_second = int((throughput_mbps * 1_000_000.0) / (8.0 * 900.0))

    return {
        "latency_ms":          round(base_latency, 3),
        "jitter_ms":           round(jitter_ms, 3),
        "packet_loss_pct":     round(packet_loss_pct, 3),
        "throughput_mbps":     round(throughput_mbps, 3),
        "packets_per_second":  max(1000, packets_per_second),
        "availability_pct":    round(availability_pct, 3),
    }


# ---------------------------------------------------------------------------
# Schema idempotente
# ---------------------------------------------------------------------------

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
        """)
        # Colunas adicionadas em v1.2 – idempotentes
        cur.execute("""
            ALTER TABLE cable_telemetry_samples
                ADD COLUMN IF NOT EXISTS sent_at     TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ;
        """)
        # Hops por salto de rota
        cur.execute("""
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
        """)
        # Baseline 7 dias
        cur.execute("""
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
            CREATE INDEX IF NOT EXISTS idx_telemetry_cable_time   ON cable_telemetry_samples(cable_id, measured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_telemetry_time          ON cable_telemetry_samples(measured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_telemetry_source_method ON cable_telemetry_samples(source, method);
        """)


# ---------------------------------------------------------------------------
# Baseline rolling
# ---------------------------------------------------------------------------

def upsert_reference(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO cable_telemetry_reference (
                cable_id, baseline_latency_ms, baseline_jitter_ms,
                baseline_packet_loss_pct, baseline_throughput_mbps,
                baseline_availability_pct, updated_at, notes
            )
            SELECT
                cable_id,
                AVG(latency_ms)       FILTER (WHERE latency_ms       IS NOT NULL),
                AVG(jitter_ms)        FILTER (WHERE jitter_ms        IS NOT NULL),
                AVG(packet_loss_pct)  FILTER (WHERE packet_loss_pct  IS NOT NULL),
                AVG(throughput_mbps)  FILTER (WHERE throughput_mbps  IS NOT NULL),
                AVG(availability_pct) FILTER (WHERE availability_pct IS NOT NULL),
                NOW(),
                'baseline recalculado automaticamente (janela 7d)'
            FROM cable_telemetry_samples
            WHERE measured_at >= NOW() - INTERVAL '7 days'
            GROUP BY cable_id
            ON CONFLICT (cable_id) DO UPDATE SET
                baseline_latency_ms       = EXCLUDED.baseline_latency_ms,
                baseline_jitter_ms        = EXCLUDED.baseline_jitter_ms,
                baseline_packet_loss_pct  = EXCLUDED.baseline_packet_loss_pct,
                baseline_throughput_mbps  = EXCLUDED.baseline_throughput_mbps,
                baseline_availability_pct = EXCLUDED.baseline_availability_pct,
                updated_at = NOW(),
                notes      = EXCLUDED.notes
        """)


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

def ingest(base_dir: Path, limit: int, seed: int, timeout: int, mtr_cycles: int) -> None:
    random.seed(seed)
    measured_at = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    conn = db_connect(base_dir)
    conn.autocommit = False

    log_dir = get_log_dir(base_dir)
    stats = {"mtr": 0, "tcp": 0, "http": 0, "synthetic": 0, "hops_total": 0}
    
    # Orçamento de rede: Máximo de ciclos MTR por execução para não saturar
    mtr_budget = 250 
    used_budget = 0

    with conn:
        ensure_telemetry_schema(conn)
        health_map = get_cable_health_map(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.cable_id, c.length_km, c.capacity_gbps,
                       c.landing_points_count, cm.public_url
                FROM cables c
                LEFT JOIN cable_metadata cm ON cm.cable_id = c.cable_id
                ORDER BY c.cable_id
                LIMIT %s
            """, (limit,))
            cables = cur.fetchall()

    inserted = 0
    for cable_id, length_km, capacity_gbps, landing_points_count, public_url in cables:
        baseline = synthetic_from_cable(length_km, capacity_gbps, landing_points_count)
        host = extract_host(public_url)

        hops: list[dict] = []
        sent_at: datetime | None = None
        received_at: datetime | None = None
        latency_ms: float | None = None
        source = "synthetic"
        method = "heuristic"
        confidence = 0.56
        estimated = True

        # Amostragem Adaptativa: Se instável, triplica os ciclos. 
        # Se o budget acabou, reduz para o mínimo.
        current_cycles = mtr_cycles * 3 if health_map.get(cable_id, {}).get("is_unstable") else mtr_cycles
        if used_budget + current_cycles > mtr_budget:
            current_cycles = 1 # Coleta mínima para manter histórico sem pesar

        # 1. mtr
        if host and used_budget < mtr_budget:
            mtr_lat, hops, sent_at, received_at, source, method = probe_mtr(
                host, cycles=current_cycles, timeout=timeout
            )
            if mtr_lat is not None and mtr_lat > 0:
                latency_ms = round((baseline["latency_ms"] * 0.4) + (mtr_lat * 0.6), 3)
                confidence = 0.90
                estimated = False
                stats["mtr"] += 1
                used_budget += current_cycles

        # 2. TCP connect
        if latency_ms is None and host:
            tcp_lat, sent_at, received_at, source, method = probe_tcp_connect(
                host, timeout=max(4, timeout // 2)
            )
            if tcp_lat is not None and tcp_lat > 0:
                latency_ms = round((baseline["latency_ms"] * 0.5) + (tcp_lat * 0.5), 3)
                confidence = 0.78
                estimated = False
                stats["tcp"] += 1

        # 3. HTTP curl
        if latency_ms is None and public_url:
            http_lat, sent_at, received_at, source, method = probe_http_latency(
                public_url, timeout=timeout
            )
            if http_lat is not None and http_lat > 0:
                latency_ms = round((baseline["latency_ms"] * 0.6) + (http_lat * 0.4), 3)
                confidence = 0.82
                estimated = False
                stats["http"] += 1

        # 4. Sintético
        if latency_ms is None:
            latency_ms = baseline["latency_ms"] * (1 + random.uniform(-0.05, 0.05))
            stats["synthetic"] += 1

        throughput_mbps    = baseline["throughput_mbps"] * (1 + random.uniform(-0.08, 0.08))
        jitter_ms          = max(0.5, baseline["jitter_ms"] * (1 + random.uniform(-0.15, 0.20)))
        packet_loss_pct    = max(0.01, baseline["packet_loss_pct"] * (1 + random.uniform(-0.2, 0.35)))
        availability_pct   = max(97.5, min(100.0, baseline["availability_pct"] - (packet_loss_pct * 0.03)))
        packets_per_second = int((throughput_mbps * 1_000_000.0) / (8.0 * 900.0))
        route_error_count  = int(packet_loss_pct > 0.25)

        elapsed_ms = (
            round((received_at - sent_at).total_seconds() * 1000.0, 3)
            if sent_at and received_at else None
        )
        metadata = {
            "collector":       "ingest_telemetry_samples.py",
            "probe_protocol":  method,
            "target_host":     host,
            "total_elapsed_ms": elapsed_ms,
            "total_hops":      len(hops) if hops else None,
            "hops_visible":    sum(1 for h in hops if h.get("ip")) if hops else None,
        }

        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cable_telemetry_samples (
                        cable_id, measured_at, sent_at, received_at,
                        latency_ms, jitter_ms, packet_loss_pct,
                        throughput_mbps, packets_per_second, availability_pct,
                        route_error_count, source, method, confidence_score,
                        estimated, telemetry_model_version, metadata
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s::jsonb
                    )
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
                    round(throughput_mbps, 3), max(1000, packets_per_second), round(availability_pct, 3),
                    route_error_count, source, method, round(confidence, 3),
                    estimated, TELEMETRY_MODEL_VERSION, json.dumps(metadata),
                ))
                row = cur.fetchone()
                sample_id = row[0] if row else None
                inserted += 1

                # ---- persiste no log local de pacotes de rede ---------------
                packet_record = {
                    "cable_id":                cable_id,
                    "measured_at":             measured_at.isoformat(),
                    "sent_at":                 sent_at.isoformat() if sent_at else None,
                    "received_at":             received_at.isoformat() if received_at else None,
                    "latency_ms":              round(latency_ms, 3),
                    "jitter_ms":               round(jitter_ms, 3),
                    "packet_loss_pct":         round(packet_loss_pct, 3),
                    "throughput_mbps":         round(throughput_mbps, 3),
                    "packets_per_second":      max(1000, packets_per_second),
                    "availability_pct":        round(availability_pct, 3),
                    "route_error_count":       route_error_count,
                    "source":                  source,
                    "method":                  method,
                    "confidence_score":        round(confidence, 3),
                    "estimated":               estimated,
                    "telemetry_model_version": TELEMETRY_MODEL_VERSION,
                    "metadata":                metadata,
                    "hops":                    hops if hops else [],
                }
                append_packet_log(log_dir, measured_at, packet_record)
                # -------------------------------------------------------------

                if sample_id and hops:
                    for hop in hops:
                        if hop.get("ip") is None:
                            continue
                        cur.execute("""
                            INSERT INTO cable_telemetry_hops (
                                sample_id, cable_id, hop_number, hop_ip,
                                rtt_last_ms, rtt_avg_ms, rtt_best_ms,
                                rtt_worst_ms, rtt_stdev_ms, loss_pct
                            ) VALUES (%s, %s, %s, %s::inet, %s, %s, %s, %s, %s, %s)
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
        print(
            f"  [{inserted:>4}/{len(cables)}] {prog:5.1f}%  {cable_id:<40} "
            f"proto={method:<28} lat={round(latency_ms,1):>8.1f}ms  hops={len(hops):>2}",
            flush=True,
        )

    with conn:
        upsert_reference(conn)

    print(
        f"\nConcluído em {datetime.now(timezone.utc).isoformat()}\n"
        f"  Cabos inseridos : {inserted}\n"
        f"  mtr (hops)      : {stats['mtr']}\n"
        f"  TCP SYN-ACK     : {stats['tcp']}\n"
        f"  HTTP curl       : {stats['http']}\n"
        f"  Sintético       : {stats['synthetic']}\n"
        f"  Hops gravados   : {stats['hops_total']}\n"
        f"  Log de pacotes  : {log_dir}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingestão de telemetria – mtr / TCP / HTTP / sintético"
    )
    parser.add_argument("--limit",      type=int, default=9999, help="Máximo de cabos (default: todos)")
    parser.add_argument("--seed",       type=int, default=42,   help="Seed ruído sintético")
    parser.add_argument("--timeout",    type=int, default=6,    help="Timeout por sonda (segundos)")
    parser.add_argument("--mtr-cycles", type=int, default=3,    help="Ciclos mtr por cabo")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ingest(
        Path(__file__).resolve().parent.parent,
        args.limit,
        args.seed,
        args.timeout,
        args.mtr_cycles,
    )
