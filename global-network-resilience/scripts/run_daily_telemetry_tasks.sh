#!/usr/bin/env bash
# run_daily_telemetry_tasks.sh — Orquestra a saúde da rede (v2)
# Melhorias: verificação real de pacotes, validação de deps, retry, relatório.

# ─── Segurança de pipe ────────────────────────────────────────────────────────
# set -e omitido intencionalmente: tratamos cada exit code manualmente.
# set -u garante que variáveis não declaradas causem erro imediato.
set -uo pipefail

# ─── Resolução de caminhos ────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="${BASE_DIR:-$(dirname "$SCRIPT_DIR")}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(dirname "$BASE_DIR")}"
PYTHON_BIN="${WORKSPACE_ROOT}/.venv/bin/python"
[[ ! -x "${PYTHON_BIN}" ]] && PYTHON_BIN="$(command -v python3 || true)"

# ─── Parâmetros ───────────────────────────────────────────────────────────────
FULL_SCAN="${FULL_SCAN:-1}"
MTR_CYCLES="${MTR_CYCLES:-100}"
TIMEOUT="${TIMEOUT:-8}"
PACKET_COUNT="${PACKET_COUNT:-5}"
PACKET_LOSS_THRESHOLD="${PACKET_LOSS_THRESHOLD:-50}"
MAX_RTT_MS="${MAX_RTT_MS:-300}"
RETRY_ATTEMPTS="${RETRY_ATTEMPTS:-2}"

# ─── Endpoints públicos para verificação real de pacotes ─────────────────────
# Diversificados: DNS públicos, anycast global, CDN — evita falso positivo
PROBE_TARGETS=(
    "8.8.8.8"           # Google DNS
    "1.1.1.1"           # Cloudflare DNS
    "9.9.9.9"           # Quad9
    "208.67.222.222"    # OpenDNS
    "185.228.168.9"     # CleanBrowsing
)

# ─── Log ──────────────────────────────────────────────────────────────────────
TODAY="$(date +%Y-%m-%d)"
HIST_DIR="${BASE_DIR}/data/local/historico/${TODAY}"
mkdir -p "${HIST_DIR}"
LOG_FILE="${LOG_FILE:-${HIST_DIR}/run_daily_$(date +%H%M%S).log}"
PROBE_REPORT="${HIST_DIR}/probe_report_$(date +%H%M%S).json"

exec > >(tee -a "${LOG_FILE}") 2>&1

# ─── Utilitários ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo -e "[$(date '+%H:%M:%S')] ${GREEN}✔${NC} $*"; }
warn() { echo -e "[$(date '+%H:%M:%S')] ${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "[$(date '+%H:%M:%S')] ${RED}✖${NC}  $*"; }

ERRORS=()
WARNINGS=()

log "=== Iniciando Ciclo de Telemetria RMIFO v2 ==="
log "    Modo: $([ "${FULL_SCAN}" = "1" ] && echo "sonda completa (${MTR_CYCLES} pacotes/rota)" || echo "padrão")"
log "    Log:  ${LOG_FILE}"
log ""

# ─── 0. Verificação de dependências ──────────────────────────────────────────
log "=== [PRÉ-FLIGHT] Verificando dependências ==="

check_dep() {
    local cmd="$1" pkg="${2:-$1}"
    if ! command -v "$cmd" &>/dev/null; then
        fail "Dependência ausente: '$cmd' (instale: $pkg)"
        ERRORS+=("dep_missing:$cmd")
        return 1
    fi
    ok "  $cmd → $(command -v "$cmd")"
}

check_dep ping  "iputils-ping"
check_dep mtr   "mtr"
check_dep curl  "curl"

if [[ -z "${PYTHON_BIN}" ]] || [[ ! -x "${PYTHON_BIN}" ]]; then
    fail "Python3 não encontrado. Defina PYTHON_BIN ou instale python3."
    ERRORS+=("dep_missing:python3")
fi

if [[ ${#ERRORS[@]} -gt 0 ]]; then
    fail "Abortando: ${#ERRORS[@]} dependência(s) crítica(s) ausente(s)."
    exit 2
fi

# ─── 1. Verificação real de pacotes (ICMP ping) ───────────────────────────────
log ""
log "=== [1/5] Conectividade — ${PACKET_COUNT} pacotes por endpoint ==="

declare -A PROBE_RESULTS
REACHABLE_COUNT=0

probe_icmp() {
    local target="$1"
    local raw loss rtt_avg rtt_int status

    raw=$(ping -c "${PACKET_COUNT}" -W "${TIMEOUT}" -q "${target}" 2>&1) || true

    # Extrai perda: "X% packet loss"
    loss=$(echo "$raw" | grep -oP '\d+(?=% packet loss)' || echo "100")
    loss="${loss:-100}"

    # Extrai RTT avg: campo 2 de "min/avg/max/mdev"
    rtt_avg=$(echo "$raw" | grep -oP 'rtt min/avg/max/mdev = [\d.]+/([\d.]+)' \
              | grep -oP '(?<=/)\d+\.\d+' | head -1 || echo "999")
    rtt_avg="${rtt_avg:-999}"
    rtt_int=$(printf '%.0f' "${rtt_avg}" 2>/dev/null || echo "999")

    status="ok"
    if   [[ "${loss}" -ge 100 ]]; then
        status="fail"
    elif [[ "${loss}" -ge "${PACKET_LOSS_THRESHOLD}" ]]; then
        status="warn"
    elif [[ "${rtt_int}" -gt "${MAX_RTT_MS}" ]]; then
        status="warn"
    fi

    PROBE_RESULTS["${target}"]="${status}|loss=${loss}%|rtt=${rtt_avg}ms"

    case "${status}" in
        ok)   ok   "  ${target}: perda=${loss}% RTT=${rtt_avg}ms"
              (( REACHABLE_COUNT++ )) || true ;;
        warn) warn "  ${target}: perda=${loss}% RTT=${rtt_avg}ms — degradado"
              WARNINGS+=("probe_degraded:${target}:loss=${loss}%") ;;
        fail) fail "  ${target}: inacessível (perda=${loss}%)"
              ERRORS+=("probe_unreachable:${target}") ;;
    esac
}

for target in "${PROBE_TARGETS[@]}"; do
    probe_icmp "${target}"
done

if [[ "${REACHABLE_COUNT}" -lt 2 ]]; then
    fail "Conectividade insuficiente: ${REACHABLE_COUNT}/${#PROBE_TARGETS[@]} endpoints alcançáveis."
    fail "Abortando — rodar telemetria agora produziria dados inválidos."
    exit 3
fi
ok "Conectividade: ${REACHABLE_COUNT}/${#PROBE_TARGETS[@]} endpoints OK — prosseguindo."

# ─── 2. Verificação DNS ───────────────────────────────────────────────────────
log ""
log "=== [2/5] Resolução DNS ==="

DNS_HOSTS=("google.com" "cloudflare.com" "github.com")
DNS_FAIL=0

for host in "${DNS_HOSTS[@]}"; do
    if resolved=$(getent hosts "${host}" 2>/dev/null | awk '{print $1}' | head -1) && [[ -n "${resolved}" ]]; then
        ok "  ${host} → ${resolved}"
    else
        warn "  ${host} → falha na resolução"
        (( DNS_FAIL++ )) || true
        WARNINGS+=("dns_fail:${host}")
    fi
done

if [[ "${DNS_FAIL}" -ge ${#DNS_HOSTS[@]} ]]; then
    ERRORS+=("dns_total_failure")
    fail "Resolução DNS completamente falha!"
fi

# ─── 3. Verificação HTTP/HTTPS ────────────────────────────────────────────────
log ""
log "=== [3/5] Verificação HTTP ==="

HTTP_TARGETS=(
    "https://1.1.1.1/cdn-cgi/trace"
    "https://www.google.com/generate_204"
)

for url in "${HTTP_TARGETS[@]}"; do
    http_code=$(curl -o /dev/null -s -w "%{http_code}" \
        --max-time "${TIMEOUT}" \
        --connect-timeout 5 \
        "${url}" 2>/dev/null || echo "000")
    if [[ "${http_code}" =~ ^(200|204|301|302)$ ]]; then
        ok "  ${url} → HTTP ${http_code}"
    else
        warn "  ${url} → HTTP ${http_code}"
        WARNINGS+=("http_fail:${url}:${http_code}")
    fi
done

# ─── 4. Relatório JSON de probe ───────────────────────────────────────────────
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

# ─── 5. Pipeline Python com retry e validação de exit code ────────────────────
log ""
log "=== [4/5] Pipeline Python ==="

export TELEMETRY_PROBE_REPORT="${PROBE_REPORT}"
export TELEMETRY_REACHABLE_COUNT="${REACHABLE_COUNT}"

run_python() {
    local label="$1"; shift
    local attempt=0 exit_code=0

    while [[ "${attempt}" -le "${RETRY_ATTEMPTS}" ]]; do
        log "  ${label} (tentativa $((attempt + 1))/$((RETRY_ATTEMPTS + 1)))..."
        "${PYTHON_BIN}" "$@"
        exit_code=$?
        if [[ "${exit_code}" -eq 0 ]]; then
            ok "  ${label} → sucesso"
            return 0
        fi
        warn "  ${label} → falhou com exit ${exit_code}"
        (( attempt++ )) || true
        [[ "${attempt}" -le "${RETRY_ATTEMPTS}" ]] && sleep 5
    done

    fail "  ${label} → falhou após $((RETRY_ATTEMPTS + 1)) tentativa(s)"
    ERRORS+=("python_fail:${label}:exit=${exit_code}")
    return 1
}

run_python "rollups pré-ingestão" \
    "${BASE_DIR}/scripts/compute_telemetry_rollups.py" || true

if [[ "${FULL_SCAN}" = "1" ]]; then
    run_python "ingestão sonda completa" \
        "${BASE_DIR}/scripts/ingest_telemetry_samples.py" \
        --full-scan \
        --mtr-cycles "${MTR_CYCLES}" \
        --timeout "${TIMEOUT}" \
        --probe-report "${PROBE_REPORT}" || true
else
    run_python "ingestão padrão" \
        "${BASE_DIR}/scripts/ingest_telemetry_samples.py" \
        --mtr-cycles 3 \
        --timeout "${TIMEOUT}" \
        --probe-report "${PROBE_REPORT}" || true
fi

run_python "rollups pós-ingestão" \
    "${BASE_DIR}/scripts/compute_telemetry_rollups.py" || true

if [[ -f "${BASE_DIR}/scripts/validate_telemetry_baseline.py" ]]; then
    run_python "validação de baseline" \
        "${BASE_DIR}/scripts/validate_telemetry_baseline.py" || true
fi

# ─── 6. Sumário final ────────────────────────────────────────────────────────
log ""
log "=== [5/5] Sumário do Ciclo ==="
log "    Endpoints alcançáveis : ${REACHABLE_COUNT}/${#PROBE_TARGETS[@]}"
log "    Avisos                : ${#WARNINGS[@]}"
log "    Erros                 : ${#ERRORS[@]}"

if [[ ${#WARNINGS[@]} -gt 0 ]]; then
    warn "Avisos:"
    for w in "${WARNINGS[@]}"; do warn "  • ${w}"; done
fi

if [[ ${#ERRORS[@]} -gt 0 ]]; then
    fail "Erros:"
    for e in "${ERRORS[@]}"; do fail "  • ${e}"; done
    echo ""
    fail "Ciclo concluído COM ERROS — verifique ${LOG_FILE}"
    exit 1
fi

ok "=== Ciclo concluído com sucesso — RMIFO Global Network Resilience ==="
log "    Histórico:  ${HIST_DIR}"
log "    Probe JSON: ${PROBE_REPORT}"
exit 0
