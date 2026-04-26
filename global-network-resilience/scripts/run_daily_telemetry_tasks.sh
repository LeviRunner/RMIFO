#!/usr/bin/env bash
set -euo pipefail

# run_daily_telemetry_tasks.sh — Orquestra a saúde da rede

WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
BASE_DIR="${WORKSPACE_ROOT}/global-network-resilience"
PYTHON_BIN="${WORKSPACE_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

echo "--- [$(date)] Iniciando Ciclo de Telemetria ---"

# 1. Processa Rollups (Garante que a lógica adaptativa tenha dados frescos)
echo "[1/3] Atualizando Rollups de performance..."
"${PYTHON_BIN}" "${BASE_DIR}/scripts/compute_telemetry_rollups.py"

# 2. Ingestão Adaptativa (Usa os rollups para decidir o esforço)
echo "[2/3] Executando ingestão adaptativa (mtr/tcp/http)..."
"${PYTHON_BIN}" "${BASE_DIR}/scripts/ingest_telemetry_samples.py" --mtr-cycles 3 --timeout 8

# 3. Validação de Baseline (Opcional, gera alertas se houver desvio)
if [[ -f "${BASE_DIR}/scripts/validate_telemetry_baseline.py" ]]; then
    echo "[3/3] Validando métricas contra o baseline..."
    "${PYTHON_BIN}" "${BASE_DIR}/scripts/validate_telemetry_baseline.py"
fi

echo "--- [$(date)] Ciclo concluído com sucesso ---"