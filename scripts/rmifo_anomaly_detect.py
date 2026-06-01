#!/usr/bin/env python3
"""
rmifo_anomaly_detect.py — Deteccao automatica de anomalias de telemetria.

Compara as amostras mais recentes de cada cabo (cable_telemetry_samples) com a
baseline de referencia (cable_telemetry_reference) e gera registros em
telemetry_alerts quando uma metrica desvia alem do limiar. Idempotente: nao
duplica um alerta que ja esteja aberto (resolved_at IS NULL) para o mesmo
cabo+metrica. Resolve automaticamente alertas cujo valor voltou ao normal.

Uso:
    DATABASE_URL=postgres://... python3 rmifo_anomaly_detect.py
Agendamento (cron / Railway cron), ex. a cada 15 min:
    */15 * * * * DATABASE_URL=... python3 scripts/rmifo_anomaly_detect.py

Limiares (multiplicador sobre a baseline; ajustaveis por env):
    LAT_MULT (2.0)   latencia atual > baseline * mult
    LOSS_ABS (5.0)   perda atual - baseline > abs (pontos percentuais)
    AVAIL_DROP (1.0) disponibilidade baseline - atual > drop (pontos)
"""
import os
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("psycopg2 nao instalado: pip install psycopg2-binary")

DB = os.environ.get("DATABASE_URL")
if not DB:
    sys.exit("Defina DATABASE_URL")

LAT_MULT   = float(os.environ.get("LAT_MULT", "2.0"))
LOSS_ABS   = float(os.environ.get("LOSS_ABS", "5.0"))
AVAIL_DROP = float(os.environ.get("AVAIL_DROP", "1.0"))


def severity(metric, cur, base):
    """Classifica severidade conforme magnitude do desvio."""
    if metric == "latency_ms" and base:
        r = cur / base
        return "critical" if r >= 4 else "high" if r >= 3 else "medium"
    if metric == "packet_loss_pct":
        d = cur - (base or 0)
        return "critical" if d >= 20 else "high" if d >= 10 else "medium"
    if metric == "availability_pct":
        d = (base or 100) - cur
        return "critical" if d >= 5 else "high" if d >= 2 else "medium"
    return "medium"


def main():
    conn = psycopg2.connect(DB)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Ultima amostra por cabo + baseline
    cur.execute("""
        SELECT DISTINCT ON (s.cable_id)
            s.cable_id, s.latency_ms, s.packet_loss_pct, s.availability_pct,
            r.baseline_latency_ms, r.baseline_packet_loss_pct, r.baseline_availability_pct
        FROM cable_telemetry_samples s
        JOIN cable_telemetry_reference r ON r.cable_id = s.cable_id
        WHERE s.measured_at > NOW() - INTERVAL '1 hour'
        ORDER BY s.cable_id, s.measured_at DESC
    """)
    rows = cur.fetchall()

    opened = resolved = 0
    for row in rows:
        checks = []
        lat, base_lat = row["latency_ms"], row["baseline_latency_ms"]
        if lat is not None and base_lat and lat > base_lat * LAT_MULT:
            checks.append(("latency_ms", lat, base_lat))
        loss, base_loss = row["packet_loss_pct"], row["baseline_packet_loss_pct"]
        if loss is not None and (loss - (base_loss or 0)) > LOSS_ABS:
            checks.append(("packet_loss_pct", loss, base_loss))
        avail, base_avail = row["availability_pct"], row["baseline_availability_pct"]
        if avail is not None and ((base_avail or 100) - avail) > AVAIL_DROP:
            checks.append(("availability_pct", avail, base_avail))

        breached = {m for m, _, _ in checks}

        # Abrir novos alertas (sem duplicar abertos)
        for metric, cur_v, base_v in checks:
            cur.execute("""
                SELECT 1 FROM telemetry_alerts
                WHERE cable_id = %s AND metric = %s AND resolved_at IS NULL LIMIT 1
            """, (row["cable_id"], metric))
            if cur.fetchone():
                continue
            cur.execute("""
                INSERT INTO telemetry_alerts
                    (cable_id, metric, severity, current_value, baseline_value, detected_at, description)
                VALUES (%s, %s, %s, %s, %s, NOW(), %s)
            """, (row["cable_id"], metric, severity(metric, cur_v, base_v),
                  cur_v, base_v, f"auto: {metric} desviou da baseline"))
            opened += 1

        # Resolver alertas abertos cujo metric voltou ao normal
        cur.execute("""
            SELECT alert_id, metric FROM telemetry_alerts
            WHERE cable_id = %s AND resolved_at IS NULL
        """, (row["cable_id"],))
        for alert in cur.fetchall():
            if alert["metric"] not in breached:
                cur.execute("UPDATE telemetry_alerts SET resolved_at = NOW() WHERE alert_id = %s",
                            (alert["alert_id"],))
                resolved += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"anomaly-detect: {len(rows)} cabos avaliados, {opened} alertas abertos, {resolved} resolvidos")


if __name__ == "__main__":
    main()
