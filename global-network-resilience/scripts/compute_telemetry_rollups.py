#!/usr/bin/env python3
"""
Gera snapshot de rollup de telemetria e salva em historico/.

Após a migration 001, telemetry_rollup_1h e telemetry_rollup_5m são
VIEWs computadas de cable_telemetry_samples — não há INSERTs.
Este script apenas lê as views e persiste o snapshot em JSON.
"""
import json
import os
import psycopg2
from pathlib import Path
from datetime import datetime

def db_connect(base_dir: Path):
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url)
    return psycopg2.connect(
        host=os.getenv("PG_SOCKET", str(base_dir / ".local" / "run")),
        port=int(os.getenv("PG_PORT", "55432")),
        dbname=os.getenv("DB_NAME", "global_network_resilience"),
        user=os.getenv("PGUSER", "postgres"),
    )

def save_rollup_history(base_dir: Path, conn, run_at: datetime) -> Path:
    proj_dir = Path(__file__).resolve().parent.parent
    hist_dir = proj_dir / "data" / "local" / "historico" / run_at.strftime("%Y-%m-%d")
    hist_dir.mkdir(parents=True, exist_ok=True)
    date_str = run_at.strftime("%Y-%m-%d")
    ts_str   = run_at.strftime("%H%M%S")

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
                   AVG(avg_jitter_ms), AVG(avg_packet_loss_pct),
                   SUM(sample_count)
            FROM telemetry_rollup_1h
            WHERE bucket >= NOW() - INTERVAL '2 hours'
        """)
        row = cur.fetchone()
        global_stats = {
            "cabos_com_dados": row[0],
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
        "gerado_em":    run_at.isoformat(),
        "tipo":         "rollup_summary",
        "rollups_5m":   n5m,
        "rollups_1h":   n1h,
        "estatisticas": global_stats,
        "top20_por_latencia": top_lat,
    }
    path = hist_dir / f"{date_str}_rollup_{ts_str}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)
    return path


def run_rollups(base_dir: Path):
    conn = db_connect(base_dir)
    try:
        run_at   = datetime.now()
        hist_path = save_rollup_history(base_dir, conn, run_at)
        print(f"[{run_at}] Snapshot de rollup gerado: {hist_path}")
    except Exception as e:
        print(f"Erro no rollup: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    base_dir = Path(
        os.getenv("WORKSPACE_ROOT", str(Path(__file__).resolve().parent.parent.parent))
    )
    run_rollups(base_dir)
