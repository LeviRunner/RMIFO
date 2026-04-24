#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
import random
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import psycopg2


def db_connect(base_dir: Path):
    return psycopg2.connect(
        host=os.getenv("PG_SOCKET", str(base_dir / ".local" / "run")),
        port=int(os.getenv("PG_PORT", "55432")),
        dbname=os.getenv("DB_NAME", "global_network_resilience"),
        user=os.getenv("PGUSER", "postgres"),
    )


def probe_http_latency(url: str | None, timeout: int = 8) -> tuple[float | None, str, str]:
    if not url:
        return None, "synthetic", "heuristic-no-url"

    cmd = [
        "curl",
        "-o",
        "/dev/null",
        "-sS",
        "-L",
        "--connect-timeout",
        str(timeout),
        "--max-time",
        str(timeout),
        "-w",
        "%{time_total}",
        url,
    ]

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        latency_ms = float(result.stdout.strip() or "0") * 1000.0
        if latency_ms <= 0:
            raise ValueError("latency zero")
        return latency_ms, "active_http_probe", "curl-time-total"
    except Exception:
        return None, "synthetic", "heuristic-fallback"


def synthetic_from_cable(length_km: int | None, capacity_gbps: int | None, endpoint_count: int | None) -> dict:
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
        "latency_ms": round(base_latency, 3),
        "jitter_ms": round(jitter_ms, 3),
        "packet_loss_pct": round(packet_loss_pct, 3),
        "throughput_mbps": round(throughput_mbps, 3),
        "packets_per_second": max(1000, packets_per_second),
        "availability_pct": round(availability_pct, 3),
    }


def ensure_telemetry_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cable_telemetry_samples (
                sample_id BIGSERIAL PRIMARY KEY,
                cable_id VARCHAR(255) NOT NULL REFERENCES cables(cable_id) ON DELETE CASCADE,
                measured_at TIMESTAMPTZ NOT NULL,
                latency_ms DOUBLE PRECISION,
                jitter_ms DOUBLE PRECISION,
                packet_loss_pct DOUBLE PRECISION,
                throughput_mbps DOUBLE PRECISION,
                packets_per_second BIGINT,
                availability_pct DOUBLE PRECISION,
                route_error_count INTEGER,
                source VARCHAR(80) NOT NULL DEFAULT 'synthetic',
                method VARCHAR(120) NOT NULL DEFAULT 'heuristic',
                confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.55,
                estimated BOOLEAN NOT NULL DEFAULT TRUE,
                telemetry_model_version VARCHAR(40) NOT NULL DEFAULT '1.0.0',
                metadata JSONB,
                UNIQUE (cable_id, measured_at)
            );
            CREATE TABLE IF NOT EXISTS cable_telemetry_reference (
                cable_id VARCHAR(255) PRIMARY KEY REFERENCES cables(cable_id) ON DELETE CASCADE,
                baseline_latency_ms DOUBLE PRECISION,
                baseline_jitter_ms DOUBLE PRECISION,
                baseline_packet_loss_pct DOUBLE PRECISION,
                baseline_throughput_mbps DOUBLE PRECISION,
                baseline_availability_pct DOUBLE PRECISION,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                notes TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_telemetry_cable_time ON cable_telemetry_samples(cable_id, measured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_telemetry_time ON cable_telemetry_samples(measured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_telemetry_source_method ON cable_telemetry_samples(source, method);
            """
        )


def upsert_reference(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cable_telemetry_reference (
                cable_id,
                baseline_latency_ms,
                baseline_jitter_ms,
                baseline_packet_loss_pct,
                baseline_throughput_mbps,
                baseline_availability_pct,
                updated_at,
                notes
            )
            SELECT
                cable_id,
                AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL),
                AVG(jitter_ms) FILTER (WHERE jitter_ms IS NOT NULL),
                AVG(packet_loss_pct) FILTER (WHERE packet_loss_pct IS NOT NULL),
                AVG(throughput_mbps) FILTER (WHERE throughput_mbps IS NOT NULL),
                AVG(availability_pct) FILTER (WHERE availability_pct IS NOT NULL),
                NOW(),
                'baseline recalculado automaticamente (janela 7d)'
            FROM cable_telemetry_samples
            WHERE measured_at >= NOW() - INTERVAL '7 days'
            GROUP BY cable_id
            ON CONFLICT (cable_id) DO UPDATE SET
                baseline_latency_ms = EXCLUDED.baseline_latency_ms,
                baseline_jitter_ms = EXCLUDED.baseline_jitter_ms,
                baseline_packet_loss_pct = EXCLUDED.baseline_packet_loss_pct,
                baseline_throughput_mbps = EXCLUDED.baseline_throughput_mbps,
                baseline_availability_pct = EXCLUDED.baseline_availability_pct,
                updated_at = NOW(),
                notes = EXCLUDED.notes
            """
        )


def ingest(base_dir: Path, limit: int, seed: int, timeout: int) -> None:
    random.seed(seed)
    measured_at = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    conn = db_connect(base_dir)
    conn.autocommit = False

    with conn:
        ensure_telemetry_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.cable_id, c.length_km, c.capacity_gbps, c.landing_points_count, cm.public_url
                FROM cables c
                LEFT JOIN cable_metadata cm ON cm.cable_id = c.cable_id
                ORDER BY c.cable_id
                LIMIT %s
                """,
                (limit,),
            )
            cables = cur.fetchall()

            inserted = 0
            for cable_id, length_km, capacity_gbps, landing_points_count, public_url in cables:
                baseline = synthetic_from_cable(length_km, capacity_gbps, landing_points_count)
                latency_probe_ms, source, method = probe_http_latency(public_url, timeout=timeout)

                if latency_probe_ms is not None:
                    latency_ms = round((baseline["latency_ms"] * 0.6) + (latency_probe_ms * 0.4), 3)
                    confidence = 0.82
                    estimated = False
                else:
                    latency_ms = baseline["latency_ms"] * (1 + random.uniform(-0.05, 0.05))
                    confidence = 0.56
                    estimated = True

                throughput_mbps = baseline["throughput_mbps"] * (1 + random.uniform(-0.08, 0.08))
                jitter_ms = max(0.5, baseline["jitter_ms"] * (1 + random.uniform(-0.15, 0.20)))
                packet_loss_pct = max(0.01, baseline["packet_loss_pct"] * (1 + random.uniform(-0.2, 0.35)))
                availability_pct = max(97.5, min(100.0, baseline["availability_pct"] - (packet_loss_pct * 0.03)))
                packets_per_second = int((throughput_mbps * 1_000_000.0) / (8.0 * 900.0))
                route_error_count = int(packet_loss_pct > 0.25)

                cur.execute(
                    """
                    INSERT INTO cable_telemetry_samples (
                        cable_id, measured_at, latency_ms, jitter_ms, packet_loss_pct,
                        throughput_mbps, packets_per_second, availability_pct,
                        route_error_count, source, method, confidence_score,
                        estimated, telemetry_model_version, metadata
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s::jsonb
                    )
                    ON CONFLICT (cable_id, measured_at) DO UPDATE SET
                        latency_ms = EXCLUDED.latency_ms,
                        jitter_ms = EXCLUDED.jitter_ms,
                        packet_loss_pct = EXCLUDED.packet_loss_pct,
                        throughput_mbps = EXCLUDED.throughput_mbps,
                        packets_per_second = EXCLUDED.packets_per_second,
                        availability_pct = EXCLUDED.availability_pct,
                        route_error_count = EXCLUDED.route_error_count,
                        source = EXCLUDED.source,
                        method = EXCLUDED.method,
                        confidence_score = EXCLUDED.confidence_score,
                        estimated = EXCLUDED.estimated,
                        telemetry_model_version = EXCLUDED.telemetry_model_version,
                        metadata = EXCLUDED.metadata
                    """,
                    (
                        cable_id,
                        measured_at,
                        round(latency_ms, 3),
                        round(jitter_ms, 3),
                        round(packet_loss_pct, 3),
                        round(throughput_mbps, 3),
                        max(1000, packets_per_second),
                        round(availability_pct, 3),
                        route_error_count,
                        source,
                        method,
                        round(confidence, 3),
                        estimated,
                        "1.1.0",
                        '{"collector":"ingest_telemetry_samples.py"}',
                    ),
                )
                inserted += 1

        upsert_reference(conn)

    print(f"Telemetria ingerida para {inserted} cabos em {measured_at.isoformat()}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingestão inicial de telemetria por cabo")
    parser.add_argument("--limit", type=int, default=120, help="Número de cabos a coletar por execução")
    parser.add_argument("--seed", type=int, default=42, help="Seed para ruído sintético controlado")
    parser.add_argument("--timeout", type=int, default=8, help="Timeout (segundos) para coleta HTTP ativa")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ingest(Path(__file__).resolve().parent.parent, args.limit, args.seed, args.timeout)
