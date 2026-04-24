#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${BASE_DIR}/../.venv/bin/python}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "Erro: Python nao encontrado."
  exit 1
fi

# Garante que o banco local esteja em execucao.
bash "${BASE_DIR}/scripts/start_local_postgres.sh" >/dev/null

# 1) Ingestao de telemetria (coleta + fallback controlado)
"${PYTHON_BIN}" "${BASE_DIR}/scripts/ingest_telemetry_samples.py" --limit "${TELEMETRY_LIMIT:-120}" --timeout "${TELEMETRY_TIMEOUT:-8}"

# 2) Validacao contra baseline e emissao de relatorio
"${PYTHON_BIN}" "${BASE_DIR}/scripts/validate_telemetry_baseline.py" --hours "${VALIDATION_WINDOW_HOURS:-24}" --output-dir "results/telemetry_validation"

echo "Pipeline diario de telemetria concluido."
