#!/usr/bin/env python3
"""
RMIFO — Analytics e Exportação de Telemetria

Subcomandos:
  rollup   Gera snapshot de rollup (telemetry_rollup_1h/5m) e salva em historico/ como JSON
  export   Exporta cable_telemetry_samples para CSV em data/local/log/telemetry/
  all      Executa rollup → export em sequência

Uso:
  python rmifo_analytics.py rollup
  python rmifo_analytics.py export
  python rmifo_analytics.py all
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2


BASE_DIR = Path(__file__).resolve().parent.parent

EXPORT_COLUMNS = [
    "cable_id", "measured_at", "sent_at", "received_at",
    "latency_ms", "jitter_ms", "packet_loss_pct", "throughput_mbps",
    "packets_per_second", "availability_pct", "route_error_count",
    "source", "method", "confidence_score", "estimated", "telemetry_model_version",
]


# ─────────────────────────────────────────────────────────────────────────────
# Conexão
# ─────────────────────────────────────────────────────────────────────────────

def db_connect() -> psycopg2.extensions.connection:
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url)
    ws = os.getenv("WORKSPACE_ROOT", str(BASE_DIR.parent))
    return psycopg2.connect(
        host=os.getenv("PG_SOCKET", f"{ws}/.local/run"),
        port=int(os.getenv("PG_PORT", os.getenv("PGPORT", "55432"))),
        dbname=os.getenv("DB_NAME", os.getenv("PGDATABASE", "global_network_resilience")),
        user=os.getenv("PGUSER", "postgres"),
        connect_timeout=10,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ── SUBCOMANDO: rollup ────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def cmd_rollup(_args: argparse.Namespace) -> None:
    run_at  = datetime.now(timezone.utc)
    hist_dir = BASE_DIR / "data" / "local" / "historico" / run_at.strftime("%Y-%m-%d")
    hist_dir.mkdir(parents=True, exist_ok=True)

    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.cable_id, c.cable_name,
                       r.avg_latency_ms, r.avg_jitter_ms, r.avg_packet_loss_pct,
                       r.sample_count, r.bucket
                FROM telemetry_rollup_1h r
                JOIN cables c ON c.cable_id = r.cable_id
                WHERE r.bucket >= NOW() - INTERVAL '2 hours'
                ORDER BY r.avg_latency_ms DESC
                LIMIT 20
            """)
            top_lat = [
                {"cable_id": row[0], "cable_name": row[1],
                 "avg_latency_ms": row[2], "avg_jitter_ms": row[3],
                 "avg_packet_loss_pct": row[4],
                 "sample_count": row[5], "bucket": str(row[6])}
                for row in cur.fetchall()
            ]

            cur.execute("""
                SELECT COUNT(DISTINCT cable_id), AVG(avg_latency_ms),
                       AVG(avg_jitter_ms), AVG(avg_packet_loss_pct), SUM(sample_count)
                FROM telemetry_rollup_1h
                WHERE bucket >= NOW() - INTERVAL '2 hours'
            """)
            row = cur.fetchone()
            global_stats = {
                "cabos_com_dados":  row[0],
                "latencia_media_ms": round(float(row[1] or 0), 3),
                "jitter_medio_ms":   round(float(row[2] or 0), 3),
                "perda_media_pct":   round(float(row[3] or 0), 4),
                "amostras_totais":   row[4],
            }

            cur.execute("SELECT COUNT(*) FROM telemetry_rollup_5m WHERE bucket >= NOW() - INTERVAL '1 hour'")
            n5m = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM telemetry_rollup_1h WHERE bucket >= NOW() - INTERVAL '2 hours'")
            n1h = cur.fetchone()[0]

        summary = {
            "gerado_em":          run_at.isoformat(),
            "tipo":               "rollup_summary",
            "rollups_5m":         n5m,
            "rollups_1h":         n1h,
            "estatisticas":       global_stats,
            "top20_por_latencia": top_lat,
        }
        path = hist_dir / f"{run_at.strftime('%Y-%m-%d')}_rollup_{run_at.strftime('%H%M%S')}.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)
        print(f"[{run_at}] Snapshot de rollup gerado: {path}")

    except Exception as e:
        print(f"Erro no rollup: {e}")
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# ── SUBCOMANDO: export ────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def cmd_export(_args: argparse.Namespace) -> None:
    log_dir = BASE_DIR / "data" / "local" / "log" / "telemetry"
    log_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    csv_path = log_dir / f"backup_{date_str}.csv"

    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT " + ", ".join(EXPORT_COLUMNS)
                + " FROM cable_telemetry_samples ORDER BY cable_id, measured_at"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(EXPORT_COLUMNS)
        writer.writerows(rows)

    print(f"Exportado: {csv_path}  ({len(rows)} registros)")


# ─────────────────────────────────────────────────────────────────────────────
# ── CLI ───────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="RMIFO — Analytics e Exportação de Telemetria",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="subcmd", required=True)
    sub.add_parser("rollup", help="Snapshot rollup 1h/5m → JSON em historico/")
    sub.add_parser("export", help="Exporta telemetria → CSV em log/telemetry/")
    sub.add_parser("all",    help="rollup + export em sequência")

    args = parser.parse_args()

    if   args.subcmd == "rollup": cmd_rollup(args)
    elif args.subcmd == "export": cmd_export(args)
    elif args.subcmd == "all":
        cmd_rollup(args)
        cmd_export(args)


if __name__ == "__main__":
    main()
