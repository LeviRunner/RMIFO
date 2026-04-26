#!/usr/bin/env python3
"""
Exporta os dados de telemetria do banco para um CSV de backup/histórico,
ordenado por cable_id, gravado em data/local/log/telemetry/.

Arquivo gerado: data/local/log/telemetry/backup_YYYY-MM-DD.csv
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2


COLUMNS = [
    "cable_id",
    "measured_at",
    "sent_at",
    "received_at",
    "latency_ms",
    "jitter_ms",
    "packet_loss_pct",
    "throughput_mbps",
    "packets_per_second",
    "availability_pct",
    "route_error_count",
    "source",
    "method",
    "confidence_score",
    "estimated",
    "telemetry_model_version",
]


def db_connect(base_dir: Path):
    return psycopg2.connect(
        host=os.getenv("PG_SOCKET", str(base_dir / ".local" / "run")),
        port=int(os.getenv("PG_PORT", "55432")),
        dbname=os.getenv("DB_NAME", "global_network_resilience"),
        user=os.getenv("PGUSER", "postgres"),
    )


def export(base_dir: Path) -> Path:
    log_dir = base_dir / "data" / "local" / "log" / "telemetry"
    log_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    csv_path = log_dir / f"backup_{date_str}.csv"

    conn = db_connect(base_dir)
    try:
        with conn.cursor() as cur:
            query = (
                "SELECT " + ", ".join(COLUMNS)
                + " FROM cable_telemetry_samples"
                + " ORDER BY cable_id, measured_at"
            )
            cur.execute(query)
            rows = cur.fetchall()
    finally:
        conn.close()

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        writer.writerows(rows)

    print(f"Exportado: {csv_path}  ({len(rows)} registros)")
    return csv_path


if __name__ == "__main__":
    base_dir = Path(
        os.getenv("WORKSPACE_ROOT", str(Path(__file__).resolve().parent.parent.parent))
    )
    export(base_dir)
