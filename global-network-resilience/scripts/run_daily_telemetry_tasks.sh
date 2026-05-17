#!/usr/bin/env bash
set -euo pipefail

# run_daily_telemetry_tasks.sh — Orquestra a saúde da rede

# Detecta BASE_DIR a partir da localização do próprio script (Railway + local)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="${BASE_DIR:-$(dirname "$SCRIPT_DIR")}"
# WORKSPACE_ROOT para os scripts Python que usam parent.parent.parent de __file__
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(dirname "$BASE_DIR")}"
PYTHON_BIN="${WORKSPACE_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

FULL_SCAN="${FULL_SCAN:-1}"          # 1 = sonda completa (padrão), 0 = padrão
MTR_CYCLES="${MTR_CYCLES:-100}"      # 100 pacotes por rota via mtr
TIMEOUT="${TIMEOUT:-8}"

# Pasta de histórico: data/local/historico/YYYY-MM-DD/
TODAY="$(date +%Y-%m-%d)"
HIST_DIR="${BASE_DIR}/data/local/historico/${TODAY}"
mkdir -p "${HIST_DIR}"
LOG_FILE="${LOG_FILE:-${HIST_DIR}/run_daily_$(date +%H%M%S).log}"

# Tee: exibe no terminal e salva em arquivo
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "--- [$(date)] Iniciando Ciclo de Telemetria RMIFO ---"
echo "    Modo: $([ "${FULL_SCAN}" = "1" ] && echo 'sonda completa (100 pacotes/rota)' || echo 'padrão')"
echo "    Log: ${LOG_FILE}"

# 1. Rollups (garante dados frescos para lógica adaptativa)
echo "[1/3] Atualizando rollups de performance..."
"${PYTHON_BIN}" "${BASE_DIR}/scripts/compute_telemetry_rollups.py"

# 2. Ingestão com sonda completa — 100 pacotes por rota, identificação RMIFO
echo "[2/3] Sonda completa — ${MTR_CYCLES} pacotes por rota, agente: RMIFO-GlobalNetworkResilience..."
if [[ "${FULL_SCAN}" = "1" ]]; then
    "${PYTHON_BIN}" "${BASE_DIR}/scripts/ingest_telemetry_samples.py" \
        --full-scan \
        --mtr-cycles "${MTR_CYCLES}" \
        --timeout "${TIMEOUT}"
else
    "${PYTHON_BIN}" "${BASE_DIR}/scripts/ingest_telemetry_samples.py" \
        --mtr-cycles 3 \
        --timeout "${TIMEOUT}"
fi

# 3. Rollups pós-ingestão (consolida os novos dados)
echo "[3/3] Rollups pós-ingestão..."
"${PYTHON_BIN}" "${BASE_DIR}/scripts/compute_telemetry_rollups.py"

# 4. Validação de baseline e alertas
if [[ -f "${BASE_DIR}/scripts/validate_telemetry_baseline.py" ]]; then
    echo "[4/4] Validando métricas contra o baseline..."
    "${PYTHON_BIN}" "${BASE_DIR}/scripts/validate_telemetry_baseline.py"
fi

echo "--- [$(date)] Ciclo concluído — RMIFO Global Network Resilience ---"
echo "    Histórico salvo em: ${HIST_DIR}"
