#!/usr/bin/env python3
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

def validate_performance(base_dir: Path):
    conn = db_connect(base_dir)
    try:
        with conn.cursor() as cur:
            # Busca desvios: Latência > 30% do baseline ou Perda > 2%
            cur.execute("""
                SELECT 
                    r.cable_id, 
                    r.avg_latency_ms as current_lat, 
                    ref.baseline_latency_ms as base_lat,
                    r.avg_packet_loss_pct as current_loss,
                    ref.baseline_packet_loss_pct as base_loss
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
                severity = 'critical' if (c_loss > 5.0 or c_lat > b_lat * 2) else 'warning'
                metric = 'latency' if c_lat > b_lat * 1.3 else 'packet_loss'
                val = c_lat if metric == 'latency' else c_loss
                base = b_lat if metric == 'latency' else b_loss
                
                cur.execute("""
                    INSERT INTO telemetry_alerts (cable_id, metric, severity, current_value, baseline_value, description)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING;
                """, (cid, metric, severity, val, base, f"Desvio detectado: {metric} em {val} (baseline: {base})"))
            
            conn.commit()
            print(f"[{datetime.now()}] Validação concluída. {len(anomalies)} anomalias processadas.")
    finally:
        conn.close()

if __name__ == "__main__":
    validate_performance(
        Path(os.getenv("WORKSPACE_ROOT", str(Path(__file__).resolve().parent.parent.parent)))
    )