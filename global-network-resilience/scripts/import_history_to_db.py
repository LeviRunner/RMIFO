#!/usr/bin/env python3
"""
Importa arquivos data/local/historico/YYYY-MM-DD/*_probe_full_*.json e *_checkpoint_*.json
para a tabela cable_telemetry_samples no PostgreSQL.

Uso:
    python3 import_history_to_db.py                # importa tudo
    python3 import_history_to_db.py --hours 48     # apenas últimas 48 h
    python3 import_history_to_db.py --dry-run      # simula sem gravar

Os registros já existentes (cable_id + measured_at) são ignorados via ON CONFLICT DO NOTHING.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("[import_history] psycopg2 não instalado — execute: pip install psycopg2-binary")
    sys.exit(1)

BASE_DIR  = Path(__file__).resolve().parent.parent           # global-network-resilience/
HIST_DIR  = BASE_DIR / "data" / "local" / "historico"

UPSERT_SQL = """
INSERT INTO cable_telemetry_samples (
    cable_id, measured_at,
    latency_ms, jitter_ms, packet_loss_pct, throughput_mbps,
    packets_per_second, availability_pct, route_error_count,
    source, method, confidence_score, estimated,
    telemetry_model_version, metadata
) VALUES (
    %(cable_id)s, %(measured_at)s,
    %(latency_ms)s, %(jitter_ms)s, %(packet_loss_pct)s, %(throughput_mbps)s,
    %(packets_per_second)s, %(availability_pct)s, %(route_error_count)s,
    %(source)s, %(method)s, %(confidence_score)s, %(estimated)s,
    %(telemetry_model_version)s, %(metadata)s
)
ON CONFLICT (cable_id, measured_at) DO NOTHING
"""


def db_connect() -> psycopg2.extensions.connection:
    ws   = os.getenv("WORKSPACE_ROOT", str(BASE_DIR))
    host = os.getenv("PG_SOCKET", os.getenv("PGHOST", f"{ws}/.local/run"))
    port = int(os.getenv("PG_PORT", os.getenv("PGPORT", "55432")))
    db   = os.getenv("PGDATABASE", "global_network_resilience")
    user = os.getenv("PGUSER", "postgres")
    return psycopg2.connect(host=host, port=port, dbname=db, user=user, connect_timeout=10)


def iter_history_files(cutoff: datetime | None) -> list[Path]:
    # Busca em MM/DD/ subdiretórios e também na raiz (compatibilidade legada)
    patterns = ["*_probe_full_*.json", "*_checkpoint_*.json"]
    files: list[Path] = []
    for pat in patterns:
        files.extend(HIST_DIR.glob(pat))           # raiz (legado)
        files.extend(HIST_DIR.glob(f"*/{pat}"))    # YYYY-MM-DD/
    files = list(set(files))
    files.sort()

    if cutoff is None:
        return files

    filtered = []
    for f in files:
        date_str = f.name[:10]
        try:
            file_day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if file_day >= cutoff - timedelta(days=1):
                filtered.append(f)
        except ValueError:
            filtered.append(f)
    return filtered


def parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception:
        return None


def record_to_row(reg: dict) -> dict | None:
    ts = parse_ts(reg.get("measured_at", ""))
    if ts is None or not reg.get("cable_id"):
        return None

    meta = reg.get("metadata") or {}
    meta["hops"]         = reg.get("hops", [])
    meta["open_ports"]   = reg.get("open_ports", [])
    meta["probe_agent"]  = reg.get("probe_agent", "")

    return {
        "cable_id":                reg["cable_id"],
        "measured_at":             ts,
        "latency_ms":              reg.get("latency_ms"),
        "jitter_ms":               reg.get("jitter_ms"),
        "packet_loss_pct":         reg.get("packet_loss_pct"),
        "throughput_mbps":         reg.get("throughput_mbps"),
        "packets_per_second":      reg.get("packets_per_second"),
        "availability_pct":        reg.get("availability_pct"),
        "route_error_count":       reg.get("route_error_count", 0),
        "source":                  reg.get("source", "synthetic"),
        "method":                  reg.get("method", "heuristic"),
        "confidence_score":        reg.get("confidence_score", 0.55),
        "estimated":               bool(reg.get("estimated", True)),
        "telemetry_model_version": reg.get("telemetry_model_version", "1.0.0"),
        "metadata":                json.dumps(meta, default=str),
    }


def run(hours: int | None, dry_run: bool) -> None:
    if not HIST_DIR.is_dir():
        print(f"[import_history] Diretório não encontrado: {HIST_DIR}")
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours) if hours else None
    files  = iter_history_files(cutoff)

    if not files:
        print("[import_history] Nenhum arquivo encontrado.")
        return

    print(f"[import_history] {len(files)} arquivo(s) a processar"
          + (f"  (janela: últimas {hours}h)" if hours else ""))

    conn   = db_connect() if not dry_run else None
    total  = 0
    errors = 0

    seen: set[tuple] = set()

    for fpath in files:
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  [ERRO] {fpath.name}: {exc}")
            errors += 1
            continue

        rows = []
        for reg in data.get("registros", []):
            row = record_to_row(reg)
            if row is None:
                continue
            if cutoff and row["measured_at"] < cutoff:
                continue
            key = (row["cable_id"], row["measured_at"].isoformat())
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

        if not rows:
            continue

        if dry_run:
            print(f"  [dry-run] {fpath.name}: {len(rows)} registros")
            total += len(rows)
            continue

        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, UPSERT_SQL, rows, page_size=500)
            conn.commit()
            total += len(rows)
            print(f"  {fpath.name}: {len(rows)} registros inseridos/ignorados")
        except Exception as exc:
            conn.rollback()
            print(f"  [ERRO] {fpath.name}: {exc}")
            errors += 1

    if conn:
        conn.close()

    print(f"\n[import_history] Total: {total} registros  |  Erros: {errors}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa histórico JSON → cable_telemetry_samples")
    parser.add_argument("--hours", type=int, default=None,
                        help="Importar apenas registros das últimas N horas (padrão: tudo)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simula sem gravar no banco")
    args = parser.parse_args()
    run(args.hours, args.dry_run)


if __name__ == "__main__":
    main()
