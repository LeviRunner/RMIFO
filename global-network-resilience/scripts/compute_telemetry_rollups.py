#!/usr/bin/env python3
"""
Script para processar rollups de telemetria (5m e 1h).
Consolida dados brutos em buckets temporais para acelerar consultas históricas.
"""
import os
import psycopg2
from pathlib import Path
from datetime import datetime

def db_connect(base_dir: Path):
    return psycopg2.connect(
        host=os.getenv("PG_SOCKET", str(base_dir / ".local" / "run")),
        port=int(os.getenv("PG_PORT", "55432")),
        dbname=os.getenv("DB_NAME", "global_network_resilience"),
        user=os.getenv("PGUSER", "postgres"),
    )

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
            print(f"[{datetime.now()}] Rollups processados com sucesso.")

    except Exception as e:
        print(f"Erro no Rollup: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent.parent
    run_rollups(base_dir)