#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# RMIFO — Setup completo do banco de dados para testes reais
#
# Uso:
#   bash db/setup.sh          # setup normal
#   bash db/setup.sh --reset  # apaga o banco e recria do zero
#
# O que faz:
#   1. Inicia o PostgreSQL local (binários em .local/postgresql/)
#   2. Cria o banco global_network_resilience
#   3. Aplica o schema (Mods/teste.SQL)
#   4. Aplica dados de seed (db/seed.sql)
#   5. Imprime string de conexão pronta para uso
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── caminhos ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(cd "${BASE_DIR}/.." && pwd)}"

PG_ROOT="${PG_ROOT:-${WORKSPACE_ROOT}/.local/postgresql}"
PG_VERSION="${PG_VERSION:-$(find "${PG_ROOT}/usr/lib/postgresql" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -n | tail -n1)}"
PG_VERSION="${PG_VERSION:-16}"
PG_BIN="${PG_ROOT}/usr/lib/postgresql/${PG_VERSION}/bin"
PG_LIB="${PG_ROOT}/usr/lib/x86_64-linux-gnu"
PG_DATA="${PG_DATA:-${WORKSPACE_ROOT}/.local/pgdata}"
PG_SOCKET_DIR="${PG_SOCKET_DIR:-${WORKSPACE_ROOT}/.local/run}"
PG_PORT="${PG_PORT:-55432}"
PG_LOG="${WORKSPACE_ROOT}/.local/postgres.log"
PG_USER="postgres"
DB_NAME="${DB_NAME:-global_network_resilience}"

SCHEMA_SQL="${BASE_DIR}/Mods/teste.SQL"
SEED_SQL="${SCRIPT_DIR}/seed.sql"
PSQL="${PG_BIN}/psql"

# ── cores ────────────────────────────────────────────────────────────────────
BOLD='\033[1m'; CYAN='\033[0;36m'; GREEN='\033[0;32m'
YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

log()  { echo -e "${CYAN}[rmifo]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC}    $*"; }
warn() { echo -e "${YELLOW}[warn]${NC}  $*"; }
die()  { echo -e "${RED}[erro]${NC}  $*" >&2; exit 1; }

# ── verificações ──────────────────────────────────────────────────────────────
[[ -x "${PSQL}" ]]       || die "psql não encontrado em ${PSQL}. Execute scripts/install_postgres_ubuntu.sh"
[[ -f "${SCHEMA_SQL}" ]] || die "Schema não encontrado: ${SCHEMA_SQL}"
[[ -f "${SEED_SQL}" ]]   || die "Seed não encontrado: ${SEED_SQL}"

[[ -d "${PG_LIB}" ]] && export LD_LIBRARY_PATH="${PG_LIB}:${LD_LIBRARY_PATH:-}"

# ── flag --reset ──────────────────────────────────────────────────────────────
RESET=0
for arg in "$@"; do [[ "$arg" == "--reset" ]] && RESET=1; done

# ── 1. Iniciar PostgreSQL ─────────────────────────────────────────────────────
log "Verificando PostgreSQL..."
mkdir -p "${PG_DATA}" "${PG_SOCKET_DIR}"

if [[ ! -f "${PG_DATA}/PG_VERSION" ]]; then
    log "Inicializando cluster de dados em ${PG_DATA}..."
    "${PG_BIN}/initdb" -D "${PG_DATA}" -A trust -U "${PG_USER}" >/dev/null
fi

if ! "${PG_BIN}/pg_ctl" -D "${PG_DATA}" status >/dev/null 2>&1; then
    log "Iniciando servidor PostgreSQL ${PG_VERSION}..."
    "${PG_BIN}/pg_ctl" -D "${PG_DATA}" -l "${PG_LOG}" \
        -o "-k ${PG_SOCKET_DIR} -p ${PG_PORT}" start >/dev/null
    sleep 1
fi
ok "PostgreSQL rodando (porta ${PG_PORT}, socket ${PG_SOCKET_DIR})"

PSQL_CMD=("${PSQL}" -h "${PG_SOCKET_DIR}" -p "${PG_PORT}" -U "${PG_USER}")

# ── 2. Criar banco (ou apagar se --reset) ────────────────────────────────────
DB_EXISTS="$("${PSQL_CMD[@]}" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" 2>/dev/null || echo "")"

if [[ "${RESET}" == "1" && "${DB_EXISTS}" == "1" ]]; then
    warn "--reset: apagando banco ${DB_NAME}..."
    "${PSQL_CMD[@]}" -d postgres -c "DROP DATABASE ${DB_NAME};" >/dev/null
    DB_EXISTS=""
fi

if [[ "${DB_EXISTS}" != "1" ]]; then
    log "Criando banco ${DB_NAME}..."
    "${PSQL_CMD[@]}" -d postgres -c "CREATE DATABASE ${DB_NAME};" >/dev/null
    ok "Banco ${DB_NAME} criado."
else
    ok "Banco ${DB_NAME} já existe."
fi

# ── 3. Extensões ──────────────────────────────────────────────────────────────
log "Habilitando extensões (btree_gist, pg_trgm)..."
"${PSQL_CMD[@]}" -d "${DB_NAME}" -q -c "
    CREATE EXTENSION IF NOT EXISTS btree_gist;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
" >/dev/null
ok "Extensões OK."

# ── 4. Schema ─────────────────────────────────────────────────────────────────
log "Aplicando schema em ${SCHEMA_SQL}..."
"${PSQL_CMD[@]}" -v ON_ERROR_STOP=1 -d "${DB_NAME}" -q -f "${SCHEMA_SQL}"
ok "Schema aplicado."

# ── 5. Seed ───────────────────────────────────────────────────────────────────
log "Inserindo dados de seed (cabos, endpoints, telemetria)..."
"${PSQL_CMD[@]}" -v ON_ERROR_STOP=1 -d "${DB_NAME}" -f "${SEED_SQL}"
ok "Seed concluído."

# ── 6. Resumo ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}──────────────────────────────────────────────────────────────${NC}"
echo -e "${BOLD}  Banco pronto para testes reais!${NC}"
echo -e "${BOLD}──────────────────────────────────────────────────────────────${NC}"
"${PSQL_CMD[@]}" -d "${DB_NAME}" -P format=aligned -c "
SELECT
    'cables'                   AS tabela, COUNT(*) AS registros FROM cables
UNION ALL SELECT 'landing_points',         COUNT(*) FROM landing_points
UNION ALL SELECT 'cable_landing_points',   COUNT(*) FROM cable_landing_points
UNION ALL SELECT 'endpoint_ip_samples',    COUNT(*) FROM endpoint_ip_samples
UNION ALL SELECT 'telemetry_samples',      COUNT(*) FROM cable_telemetry_samples
UNION ALL SELECT 'telemetry_rollup_1h',    COUNT(*) FROM telemetry_rollup_1h
UNION ALL SELECT 'alertas_ativos',         COUNT(*) FROM telemetry_alerts WHERE resolved_at IS NULL;
"
echo ""
echo -e "  ${BOLD}String de conexão psql:${NC}"
echo -e "  ${CYAN}${PSQL_CMD[*]} -d ${DB_NAME}${NC}"
echo ""
echo -e "  ${BOLD}Variáveis de ambiente para as apps:${NC}"
echo -e "  ${YELLOW}export WORKSPACE_ROOT=${WORKSPACE_ROOT}${NC}"
echo -e "  ${YELLOW}export PG_SOCKET=${PG_SOCKET_DIR}${NC}"
echo -e "  ${YELLOW}export PG_PORT=${PG_PORT}${NC}"
echo -e "  ${YELLOW}export PGDATABASE=${DB_NAME}${NC}"
echo -e "  ${YELLOW}export PGUSER=${PG_USER}${NC}"
echo ""
echo -e "  ${BOLD}Terminal C:${NC}   ${CYAN}cd Mods/TERMINAL && ./rmifo${NC}"
echo -e "  ${BOLD}Dashboard Python:${NC} ${CYAN}python Mods/TERMINAL/dashboard.py${NC}"
echo ""
