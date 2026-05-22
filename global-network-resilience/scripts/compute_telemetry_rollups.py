#!/usr/bin/env python3
"""
Script para processar rollups de telemetria (5m e 1h).
Consolida dados brutos em buckets temporais para acelerar consultas históricas.
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
        # Top 20 cabos por latência nas últimas 1h
        cur.execute("""
            SELECT r.cable_id, c.cable_name,
                   r.avg_latency_ms, r.avg_jitter_ms, r.avg_packet_loss_pct,
                   r.avg_throughput_mbps, r.sample_count, r.bucket
            FROM telemetry_rollup_1h r
            JOIN cables c ON c.cable_id = r.cable_id
            WHERE r.bucket >= NOW() - INTERVAL '2 hours'
            ORDER BY r.avg_latency_ms DESC
            LIMIT 20
        """)
        top_lat = [
            {"cable_id": row[0], "cable_name": row[1],
             "avg_latency_ms": row[2], "avg_jitter_ms": row[3],
             "avg_packet_loss_pct": row[4], "avg_throughput_mbps": row[5],
             "sample_count": row[6], "bucket": str(row[7])}
            for row in cur.fetchall()
        ]

        # Estatísticas globais
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

        # Contagem de rollups gerados
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
        with conn.cursor() as cur:
            # Rollup 5 minutos ( buckets de 300 segundos )
            cur.execute("""
                INSERT INTO telemetry_rollup_5m (cable_id, bucket, avg_latency_ms, avg_jitter_ms, max_latency_ms, avg_packet_loss_pct, sample_count)
                SELECT 
                    cable_id,
                    to_timestamp(floor(extract(epoch from measured_at) / 300) * 300) AT TIME ZONE 'UTC' as bucket,
                    AVG(latency_ms),
                    AVG(jitter_ms),
                    MAX(latency_ms),
                    AVG(packet_loss_pct),
                    COUNT(*)
                FROM cable_telemetry_samples
                WHERE measured_at >= NOW() - INTERVAL '3 days'
                GROUP BY 1, 2
                ON CONFLICT (cable_id, bucket) DO UPDATE SET
                    avg_latency_ms = EXCLUDED.avg_latency_ms,
                    avg_jitter_ms = EXCLUDED.avg_jitter_ms,
                    sample_count = EXCLUDED.sample_count;
            """)
            
            # Rollup 1 hora (buckets de 3600 segundos)
            cur.execute("""
                INSERT INTO telemetry_rollup_1h (
                    cable_id, bucket, avg_latency_ms, avg_jitter_ms, max_latency_ms, 
                    p95_latency_ms, avg_packet_loss_pct, avg_throughput_mbps, 
                    sample_count, confidence_avg
                )
                SELECT 
                    cable_id,
                    date_trunc('hour', measured_at) as bucket,
                    AVG(latency_ms),
                    AVG(jitter_ms),
                    MAX(latency_ms),
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms),
                    AVG(packet_loss_pct),
                    AVG(throughput_mbps),
                    COUNT(*),
                    AVG(confidence_score)
                FROM cable_telemetry_samples
                WHERE measured_at >= NOW() - INTERVAL '14 days'
                GROUP BY 1, 2
                ON CONFLICT (cable_id, bucket) DO UPDATE SET
                    avg_latency_ms = EXCLUDED.avg_latency_ms,
                    avg_jitter_ms = EXCLUDED.avg_jitter_ms,
                    p95_latency_ms = EXCLUDED.p95_latency_ms,
                    avg_throughput_mbps = EXCLUDED.avg_throughput_mbps,
                    sample_count = EXCLUDED.sample_count;
            """)
            conn.commit()
            run_at = datetime.now()
            print(f"[{run_at}] Rollups processados com sucesso.")

        hist_path = save_rollup_history(base_dir, conn, run_at)
        print(f"  Historico rollup: {hist_path}")

    except Exception as e:
        print(f"Erro no Rollup: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    base_dir = Path(
        os.getenv("WORKSPACE_ROOT", str(Path(__file__).resolve().parent.parent.parent))
    )
    run_rollups(base_dir)