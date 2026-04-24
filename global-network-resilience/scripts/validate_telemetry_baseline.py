#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import psycopg2


@dataclass
class CableValidation:
    cable_id: str
    sample_size: int
    avg_latency_ms: float | None
    baseline_latency_ms: float | None
    latency_delta_pct: float | None
    avg_packet_loss_pct: float | None
    baseline_packet_loss_pct: float | None
    packet_loss_delta_pct: float | None
    avg_throughput_mbps: float | None
    baseline_throughput_mbps: float | None
    throughput_delta_pct: float | None
    status: str


def db_connect(base_dir: Path):
    return psycopg2.connect(
        host=os.getenv("PG_SOCKET", str(base_dir / ".local" / "run")),
        port=int(os.getenv("PG_PORT", "55432")),
        dbname=os.getenv("DB_NAME", "global_network_resilience"),
        user=os.getenv("PGUSER", "postgres"),
    )


def pct_delta(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None or baseline == 0:
        return None
    return ((current - baseline) / baseline) * 100.0


def classify(lat_delta: float | None, loss_delta: float | None, th_delta: float | None) -> str:
    risk = 0
    if lat_delta is not None and lat_delta > 25:
        risk += 1
    if loss_delta is not None and loss_delta > 40:
        risk += 1
    if th_delta is not None and th_delta < -20:
        risk += 1

    if risk >= 2:
        return "critical"
    if risk == 1:
        return "warning"
    return "stable"


def validate(base_dir: Path, hours: int, out_dir: Path) -> tuple[Path, Path, dict]:
    conn = db_connect(base_dir)
    conn.autocommit = False

    with conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH recent AS (
                SELECT
                    cable_id,
                    COUNT(*) AS sample_size,
                    AVG(latency_ms) AS avg_latency_ms,
                    AVG(packet_loss_pct) AS avg_packet_loss_pct,
                    AVG(throughput_mbps) AS avg_throughput_mbps
                FROM cable_telemetry_samples
                WHERE measured_at >= NOW() - (%s::text || ' hours')::interval
                GROUP BY cable_id
            )
            SELECT
                r.cable_id,
                r.sample_size,
                r.avg_latency_ms,
                ref.baseline_latency_ms,
                r.avg_packet_loss_pct,
                ref.baseline_packet_loss_pct,
                r.avg_throughput_mbps,
                ref.baseline_throughput_mbps
            FROM recent r
            LEFT JOIN cable_telemetry_reference ref ON ref.cable_id = r.cable_id
            ORDER BY r.sample_size DESC, r.cable_id
            """,
            (hours,),
        )
        rows = cur.fetchall()

    validations: list[CableValidation] = []
    for row in rows:
        cable_id, sample_size, avg_lat, base_lat, avg_loss, base_loss, avg_thr, base_thr = row
        lat_delta = pct_delta(avg_lat, base_lat)
        loss_delta = pct_delta(avg_loss, base_loss)
        thr_delta = pct_delta(avg_thr, base_thr)
        status = classify(lat_delta, loss_delta, thr_delta)
        validations.append(
            CableValidation(
                cable_id=cable_id,
                sample_size=int(sample_size),
                avg_latency_ms=float(avg_lat) if avg_lat is not None else None,
                baseline_latency_ms=float(base_lat) if base_lat is not None else None,
                latency_delta_pct=round(lat_delta, 3) if lat_delta is not None else None,
                avg_packet_loss_pct=float(avg_loss) if avg_loss is not None else None,
                baseline_packet_loss_pct=float(base_loss) if base_loss is not None else None,
                packet_loss_delta_pct=round(loss_delta, 3) if loss_delta is not None else None,
                avg_throughput_mbps=float(avg_thr) if avg_thr is not None else None,
                baseline_throughput_mbps=float(base_thr) if base_thr is not None else None,
                throughput_delta_pct=round(thr_delta, 3) if thr_delta is not None else None,
                status=status,
            )
        )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": hours,
        "cables_evaluated": len(validations),
        "stable": sum(1 for v in validations if v.status == "stable"),
        "warning": sum(1 for v in validations if v.status == "warning"),
        "critical": sum(1 for v in validations if v.status == "critical"),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"telemetry_validation_{ts}.json"
    md_path = out_dir / f"telemetry_validation_{ts}.md"

    json_path.write_text(
        json.dumps({"summary": summary, "items": [asdict(v) for v in validations]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    top_critical = [v for v in validations if v.status == "critical"][:15]
    top_warning = [v for v in validations if v.status == "warning"][:15]

    md_lines = [
        "# Relatório de Validação de Telemetria",
        "",
        f"- Gerado em: {summary['generated_at']}",
        f"- Janela avaliada: {hours}h",
        f"- Cabos avaliados: {summary['cables_evaluated']}",
        f"- Estáveis: {summary['stable']}",
        f"- Alerta: {summary['warning']}",
        f"- Críticos: {summary['critical']}",
        "",
        "## Críticos",
        "| cable_id | amostras | delta latência % | delta perda % | delta throughput % |",
        "|---|---:|---:|---:|---:|",
    ]

    for item in top_critical:
        md_lines.append(
            f"| {item.cable_id} | {item.sample_size} | {item.latency_delta_pct} | {item.packet_loss_delta_pct} | {item.throughput_delta_pct} |"
        )

    md_lines.extend([
        "",
        "## Alertas",
        "| cable_id | amostras | delta latência % | delta perda % | delta throughput % |",
        "|---|---:|---:|---:|---:|",
    ])

    for item in top_warning:
        md_lines.append(
            f"| {item.cable_id} | {item.sample_size} | {item.latency_delta_pct} | {item.packet_loss_delta_pct} | {item.throughput_delta_pct} |"
        )

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return json_path, md_path, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validação diária de telemetria vs baseline")
    parser.add_argument("--hours", type=int, default=24, help="Janela de análise em horas")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/telemetry_validation"),
        help="Diretório de saída para relatórios",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    base_dir = Path(__file__).resolve().parent.parent
    json_path, md_path, summary = validate(base_dir, args.hours, base_dir / args.output_dir)
    print(f"Validação concluída: {summary['cables_evaluated']} cabos")
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
