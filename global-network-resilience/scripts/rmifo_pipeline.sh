#!/usr/bin/env bash
# RMIFO — Orquestrador Unificado de Pipeline
#
# Subcomandos:
#   daily      Ciclo diário completo: pre-flight + conectividade + DNS + sonda Node.js
#   pipeline   Ingestão Python: telemetria → rollup → histórico JSON → BD
#   all        daily + pipeline em sequência
#
# Uso:
#   bash rmifo_pipeline.sh daily                     # produção (Railway)
#   bash rmifo_pipeline.sh pipeline [--limit 50]     # pipeline Python
#   bash rmifo_pipeline.sh pipeline --full           # full scan
#   bash rmifo_pipeline.sh pipeline import --hours 24
#   bash rmifo_pipeline.sh all
#
# Variáveis de ambiente:
#   FULL_SCAN, MTR_CYCLES, TIMEOUT, RETRY_ATTEMPTS, WORKSPACE_ROOT,
#   PG_SOCKET, PG_PORT, PGDATABASE, PGUSER

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="${BASE_DIR:-$(dirname "$SCRIPT_DIR")}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(dirname "$BASE_DIR")}"
NODE_BIN="$(command -v node || true)"

export WORKSPACE_ROOT
export PG_SOCKET="${PG_SOCKET:-$WORKSPACE_ROOT/.local/run}"
export PG_PORT="${PG_PORT:-55432}"
export PGDATABASE="${PGDATABASE:-global_network_resilience}"
export PGUSER="${PGUSER:-postgres}"

PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
VENV="$WORKSPACE_ROOT/.venv"
[[ -d "$VENV" ]] && PY="$VENV/bin/python"

# ── Parâmetros do ciclo diário ────────────────────────────────────────────────
FULL_SCAN="${FULL_SCAN:-1}"
MTR_CYCLES="${MTR_CYCLES:-15}"
TIMEOUT="${TIMEOUT:-8}"
PACKET_COUNT="${PACKET_COUNT:-5}"
PACKET_LOSS_THRESHOLD="${PACKET_LOSS_THRESHOLD:-50}"
MAX_RTT_MS="${MAX_RTT_MS:-300}"
RETRY_ATTEMPTS="${RETRY_ATTEMPTS:-2}"

PROBE_TARGETS=(
    "8.8.8.8"         # Google DNS
    "1.1.1.1"         # Cloudflare DNS
    "9.9.9.9"         # Quad9
    "208.67.222.222"  # OpenDNS
    "185.228.168.9"   # CleanBrowsing
)

# ── Utilitários ───────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo -e "[$(date '+%H:%M:%S')] ${GREEN}✔${NC} $*"; }
warn() { echo -e "[$(date '+%H:%M:%S')] ${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "[$(date '+%H:%M:%S')] ${RED}✖${NC}  $*"; }
sep()  { echo ""; echo "──────────────────────────────────────────"; }

# ─────────────────────────────────────────────────────────────────────────────
# SUBCOMANDO: daily — ciclo diário de telemetria RMIFO
# ─────────────────────────────────────────────────────────────────────────────

run_daily() {
    TODAY="$(date +%Y-%m-%d)"
    HIST_DIR="${BASE_DIR}/data/local/historico/${TODAY}"
    mkdir -p "${HIST_DIR}"
    LOG_FILE="${LOG_FILE:-${HIST_DIR}/run_daily_$(date +%H%M%S).log}"
    PROBE_REPORT="${HIST_DIR}/probe_report_$(date +%H%M%S).json"

    exec > >(tee -a "${LOG_FILE}") 2>&1

    ERRORS=()
    WARNINGS=()

    log "=== Iniciando Ciclo de Telemetria RMIFO v2 ==="
    log "    Modo: $([ "${FULL_SCAN}" = "1" ] && echo "sonda completa (${MTR_CYCLES} pacotes/rota)" || echo "padrão")"
    log "    Log:  ${LOG_FILE}"

    # ── 0. Dependências ───────────────────────────────────────────────────────
    log ""
    log "=== [PRÉ-FLIGHT] Verificando dependências ==="

    check_dep_opt() {
        local cmd="$1" pkg="${2:-$1}"
        if ! command -v "$cmd" &>/dev/null; then
            warn "  $cmd não encontrado — fallback ativado (instale: $pkg)"
            return 1
        fi
        ok "  $cmd → $(command -v "$cmd")"
    }

    check_dep_opt ping "iputils-ping"
    check_dep_opt mtr  "mtr"
    check_dep_opt curl "curl"

    if [[ -z "${NODE_BIN}" ]]; then
        fail "Node.js não encontrado. Adicione 'nodejs_20' ao nixpkgs."
        ERRORS+=("dep_missing:node")
    fi

    if [[ ${#ERRORS[@]} -gt 0 ]]; then
        fail "Abortando: Node.js é obrigatório."
        exit 2
    fi
    ok "Dependências críticas OK."

    # ── 1. Conectividade ──────────────────────────────────────────────────────
    log ""
    log "=== [1/5] Conectividade ==="
    declare -A PROBE_RESULTS
    REACHABLE_COUNT=0
    PING_AVAILABLE=false
    command -v ping &>/dev/null && PING_AVAILABLE=true

    probe_icmp() {
        local target="$1"
        local raw loss rtt_avg rtt_int status
        raw=$(ping -c "${PACKET_COUNT}" -W "${TIMEOUT}" -q "${target}" 2>&1) || true
        loss=$(echo "$raw" | grep -oP '\d+(?=% packet loss)' || echo "100"); loss="${loss:-100}"
        rtt_avg=$(echo "$raw" | grep -oP 'rtt min/avg/max/mdev = [\d.]+/([\d.]+)' \
                  | grep -oP '(?<=/)\d+\.\d+' | head -1 || echo "999"); rtt_avg="${rtt_avg:-999}"
        rtt_int=$(printf '%.0f' "${rtt_avg}" 2>/dev/null || echo "999")
        status="ok"
        if   [[ "${loss}" -ge 100 ]];                     then status="fail"
        elif [[ "${loss}" -ge "${PACKET_LOSS_THRESHOLD}" ]]; then status="warn"
        elif [[ "${rtt_int}" -gt "${MAX_RTT_MS}" ]];      then status="warn"
        fi
        PROBE_RESULTS["${target}"]="${status}|loss=${loss}%|rtt=${rtt_avg}ms"
        case "${status}" in
            ok)   ok   "  ${target}: perda=${loss}% RTT=${rtt_avg}ms"; (( REACHABLE_COUNT++ )) || true ;;
            warn) warn "  ${target}: perda=${loss}% RTT=${rtt_avg}ms — degradado"
                  WARNINGS+=("probe_degraded:${target}:loss=${loss}%") ;;
            fail) warn "  ${target}: ICMP inacessível (perda=${loss}%)" ;;
        esac
    }

    probe_http_fallback() {
        local ok_count=0
        for url in "https://1.1.1.1/cdn-cgi/trace" "https://www.google.com/generate_204"; do
            local code
            code=$(curl -o /dev/null -s -w "%{http_code}" --max-time 8 --connect-timeout 5 "$url" 2>/dev/null || echo "000")
            if [[ "${code}" =~ ^(200|204|301|302)$ ]]; then
                ok "  curl ${url} → HTTP ${code}"; (( ok_count++ )) || true
            else
                warn "  curl ${url} → HTTP ${code}"
            fi
        done
        REACHABLE_COUNT="${ok_count}"
    }

    if [[ "${PING_AVAILABLE}" == "true" ]]; then
        for target in "${PROBE_TARGETS[@]}"; do probe_icmp "${target}"; done
    fi
    if [[ "${REACHABLE_COUNT}" -lt 2 ]]; then
        warn "ICMP insuficiente — verificando via HTTP/curl..."
        probe_http_fallback
    fi
    if [[ "${REACHABLE_COUNT}" -lt 1 ]]; then
        warn "Sem conectividade verificável — prosseguindo via TCP/sintético."
        REACHABLE_COUNT=1
    fi
    ok "Conectividade: ${REACHABLE_COUNT} endpoint(s)."

    # ── 2. DNS ────────────────────────────────────────────────────────────────
    log ""
    log "=== [2/5] Resolução DNS ==="
    DNS_FAIL=0
    for host in "google.com" "cloudflare.com" "github.com"; do
        if resolved=$(getent hosts "${host}" 2>/dev/null | awk '{print $1}' | head -1) && [[ -n "${resolved}" ]]; then
            ok "  ${host} → ${resolved}"
        else
            warn "  ${host} → falha na resolução"
            (( DNS_FAIL++ )) || true
            WARNINGS+=("dns_fail:${host}")
        fi
    done

    # ── 3. HTTP ───────────────────────────────────────────────────────────────
    log ""
    log "=== [3/5] Verificação HTTP ==="
    for url in "https://1.1.1.1/cdn-cgi/trace" "https://www.google.com/generate_204"; do
        http_code=$(curl -o /dev/null -s -w "%{http_code}" \
            --max-time "${TIMEOUT}" --connect-timeout 5 "${url}" 2>/dev/null || echo "000")
        if [[ "${http_code}" =~ ^(200|204|301|302)$ ]]; then
            ok "  ${url} → HTTP ${http_code}"
        else
            warn "  ${url} → HTTP ${http_code}"; WARNINGS+=("http_fail:${url}:${http_code}")
        fi
    done

    # ── 4. Relatório JSON ─────────────────────────────────────────────────────
    log ""
    log "=== Gerando relatório de probe ==="
    {
        printf '{\n'
        printf '  "timestamp": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '  "reachable": %d,\n' "${REACHABLE_COUNT}"
        printf '  "total_targets": %d,\n' "${#PROBE_TARGETS[@]}"
        printf '  "probes": {\n'
        first=true
        for target in "${!PROBE_RESULTS[@]}"; do
            [[ "${first}" == "true" ]] && first=false || printf ',\n'
            IFS='|' read -r pstatus loss_s rtt_s <<< "${PROBE_RESULTS[$target]}"
            printf '    "%s": {"status": "%s", "loss": "%s", "rtt": "%s"}' \
                "${target}" "${pstatus}" "${loss_s#loss=}" "${rtt_s#rtt=}"
        done
        printf '\n  }\n}\n'
    } > "${PROBE_REPORT}"
    ok "Relatório salvo: ${PROBE_REPORT}"

    # ── 5. Node.js sonda.js com retry ─────────────────────────────────────────
    log ""
    log "=== [4/5] Pipeline Node.js ==="
    export TELEMETRY_PROBE_REPORT="${PROBE_REPORT}"
    export TELEMETRY_REACHABLE_COUNT="${REACHABLE_COUNT}"

    run_node() {
        local label="$1"; shift
        local attempt=0 exit_code=0
        while [[ "${attempt}" -le "${RETRY_ATTEMPTS}" ]]; do
            log "  ${label} (tentativa $((attempt + 1))/$((RETRY_ATTEMPTS + 1)))..."
            "${NODE_BIN}" "$@"
            exit_code=$?
            if [[ "${exit_code}" -eq 0 ]]; then ok "  ${label} → sucesso"; return 0; fi
            warn "  ${label} → falhou com exit ${exit_code}"
            (( attempt++ )) || true
            [[ "${attempt}" -le "${RETRY_ATTEMPTS}" ]] && sleep 5
        done
        fail "  ${label} → falhou após $((RETRY_ATTEMPTS + 1)) tentativa(s)"
        ERRORS+=("node_fail:${label}:exit=${exit_code}")
        return 1
    }

    if [[ "${FULL_SCAN}" = "1" ]]; then
        run_node "sonda completa" \
            "${BASE_DIR}/scripts/sonda.js" \
            --full-scan --mtr-cycles "${MTR_CYCLES}" \
            --timeout "${TIMEOUT}" --probe-report "${PROBE_REPORT}" || true
    else
        run_node "sonda padrão" \
            "${BASE_DIR}/scripts/sonda.js" \
            --mtr-cycles 3 --timeout "${TIMEOUT}" \
            --probe-report "${PROBE_REPORT}" || true
    fi

    # ── 6. Sumário ────────────────────────────────────────────────────────────
    log ""
    log "=== [5/5] Sumário do Ciclo ==="
    log "    Endpoints alcançáveis : ${REACHABLE_COUNT}/${#PROBE_TARGETS[@]}"
    log "    Avisos                : ${#WARNINGS[@]}"
    log "    Erros                 : ${#ERRORS[@]}"

    if [[ ${#WARNINGS[@]} -gt 0 ]]; then
        warn "Avisos:"; for w in "${WARNINGS[@]}"; do warn "  • ${w}"; done
    fi
    if [[ ${#ERRORS[@]} -gt 0 ]]; then
        fail "Erros:"; for e in "${ERRORS[@]}"; do fail "  • ${e}"; done
        fail "Ciclo concluído COM ERROS — verifique ${LOG_FILE}"
        exit 1
    fi

    ok "=== Ciclo concluído com sucesso — RMIFO Global Network Resilience ==="
    log "    Histórico:  ${HIST_DIR}"
    log "    Probe JSON: ${PROBE_REPORT}"
}

# ─────────────────────────────────────────────────────────────────────────────
# SUBCOMANDO: pipeline — ingestão Python
# ─────────────────────────────────────────────────────────────────────────────

run_pipeline() {
    local args=("$@")

    _ingest() {
        local extra=()
        [[ " ${args[*]} " == *" --full "* ]]  && extra+=("--full-scan")
        if [[ " ${args[*]} " == *" --limit "* ]]; then
            local lim; lim=$(echo " ${args[*]} " | grep -oP '(?<=--limit )\d+')
            extra+=("--limit" "$lim")
        fi
        sep
        log "Ingestão de telemetria (${extra[*]:-padrão})…"
        "$PY" "$SCRIPT_DIR/rmifo_probe.py" telemetry "${extra[@]}"
    }

    _rollup() {
        sep
        log "Computando rollups 5m e 1h…"
        "$PY" "$SCRIPT_DIR/rmifo_analytics.py" rollup
    }

    _import() {
        local extra=()
        if [[ " ${args[*]} " == *" --hours "* ]]; then
            local hrs; hrs=$(echo " ${args[*]} " | grep -oP '(?<=--hours )\d+')
            extra+=("--hours" "$hrs")
        fi
        sep
        log "Importando histórico JSON → cable_telemetry_samples…"
        "$PY" "$SCRIPT_DIR/rmifo_catalog.py" history "${extra[@]}"
    }

    # Determina alvo (padrão: all)
    local target="${args[0]:-all}"
    case "$target" in
        rollup)  _rollup ;;
        import)  _import ;;
        ingest)  _ingest; _rollup ;;
        all|*)   _ingest; _rollup; _import; sep; log "Pipeline completo." ;;
    esac
}

# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher principal
# ─────────────────────────────────────────────────────────────────────────────

SUBCMD="${1:-daily}"
shift || true

case "$SUBCMD" in
    daily)
        run_daily
        ;;
    pipeline)
        run_pipeline "$@"
        ;;
    all)
        run_daily
        sep
        run_pipeline "$@"
        ;;
    -h|--help|help)
        cat <<'EOF'
Uso: bash rmifo_pipeline.sh <subcomando> [opções]

Subcomandos:
  daily                   Ciclo diário: pre-flight + conectividade + sonda Node.js
  pipeline [alvo] [opts]  Pipeline Python (alvos: all|ingest|rollup|import)
  all      [opts]         daily + pipeline em sequência

Opções do pipeline:
  --limit N    Sonda apenas N cabos
  --full       Full scan (gera probe_full)
  --hours N    Import apenas últimas N horas

Exemplos:
  bash rmifo_pipeline.sh daily
  bash rmifo_pipeline.sh pipeline --limit 50 --full
  bash rmifo_pipeline.sh pipeline import --hours 24
  bash rmifo_pipeline.sh all
EOF
        ;;
    *)
        echo "Subcomando desconhecido: $SUBCMD  (use: daily | pipeline | all)"
        exit 1
        ;;
esac
