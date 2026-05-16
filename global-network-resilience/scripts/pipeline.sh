#!/usr/bin/env bash
# RMIFO — Pipeline de ingestão + rollup + importação histórica
#
# Uso:
#   bash pipeline.sh                   # ciclo completo (padrão: 699 cabos)
#   bash pipeline.sh --limit 50        # sonda apenas 50 cabos
#   bash pipeline.sh --full            # full scan (probe_full)
#   bash pipeline.sh rollup            # apenas rollup 5m/1h
#   bash pipeline.sh import            # apenas importar JSON → PG
#   bash pipeline.sh import --hours 24 # importar apenas últimas 24h
#
# Variáveis de ambiente (opcional):
#   WORKSPACE_ROOT   raiz do repositório  (detectada automaticamente)
#   PG_SOCKET        diretório do socket  (padrão: $WORKSPACE_ROOT/.local/run)
#   PG_PORT          porta PostgreSQL      (padrão: 55432)
#   PGDATABASE       nome do banco         (padrão: global_network_resilience)
#   PGUSER           usuário               (padrão: postgres)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

export WORKSPACE_ROOT="${WORKSPACE_ROOT:-$ROOT}"
export PG_SOCKET="${PG_SOCKET:-$WORKSPACE_ROOT/.local/run}"
export PG_PORT="${PG_PORT:-55432}"
export PGDATABASE="${PGDATABASE:-global_network_resilience}"
export PGUSER="${PGUSER:-postgres}"

PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
VENV="$ROOT/.venv"
[[ -d "$VENV" ]] && PY="$VENV/bin/python"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
sep()  { echo ""; echo "──────────────────────────────────────────"; }

# ── subcomandos ───────────────────────────────────────────────────────────────
run_ingest() {
    local extra=()
    [[ "$*" == *"--full"* ]]   && extra+=("--full-scan")
    [[ "$*" == *"--limit"* ]]  && {
        local lim
        lim=$(echo "$*" | grep -oP '(?<=--limit )\d+')
        extra+=("--limit" "$lim")
    }
    sep
    log "Ingestão de telemetria TCP  (${extra[*]:-padrão})…"
    "$PY" "$SCRIPT_DIR/ingest_telemetry_samples.py" "${extra[@]}"
}

run_rollup() {
    sep
    log "Computando rollups 5m e 1h…"
    "$PY" "$SCRIPT_DIR/compute_telemetry_rollups.py"
}

run_import() {
    local extra=("$@")
    sep
    log "Importando histórico JSON → cable_telemetry_samples…"
    "$PY" "$SCRIPT_DIR/import_history_to_db.py" "${extra[@]}"
}

# ── dispatcher ────────────────────────────────────────────────────────────────
TARGET="${1:-all}"
shift || true

case "$TARGET" in
    rollup)
        run_rollup
        ;;
    import)
        run_import "$@"
        ;;
    ingest)
        run_ingest "$@"
        run_rollup
        ;;
    all)
        run_ingest "$@"
        run_rollup
        run_import
        sep
        log "Pipeline completo."
        ;;
    *)
        echo "Uso: bash pipeline.sh [all|ingest|rollup|import] [opções]"
        echo ""
        echo "  all     ingest + rollup + import (padrão)"
        echo "  ingest  sonda + rollup"
        echo "  rollup  apenas rollup 5m/1h"
        echo "  import  JSON → cable_telemetry_samples"
        echo ""
        echo "Opções:"
        echo "  --limit N    sonda N cabos"
        echo "  --full       full scan (gera probe_full)"
        echo "  --hours N    import apenas últimas N horas"
        exit 1
        ;;
esac
