#!/usr/bin/env bash
# RMIFO — Utilitários de Ambiente de Desenvolvimento
#
# Subcomandos:
#   postgres    Inicia instância local do PostgreSQL
#   bootstrap   Inicializa banco: cria DB + schema + importa catálogo
#   web         Inicia servidor PHP embutido (porta 8080)
#   all         postgres → bootstrap → web
#
# Uso:
#   bash rmifo_dev.sh postgres
#   bash rmifo_dev.sh bootstrap
#   bash rmifo_dev.sh web
#   bash rmifo_dev.sh all

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BASE_DIR="${WORKSPACE_ROOT}/global-network-resilience"

PG_ROOT="${PG_ROOT:-${WORKSPACE_ROOT}/.local/postgresql}"
if [[ -z "${PG_VERSION:-}" ]]; then
    PG_VERSION="$(find "${PG_ROOT}/usr/lib/postgresql" -mindepth 1 -maxdepth 1 -type d \
                  -printf '%f\n' 2>/dev/null | sort -n | tail -n 1)"
fi
PG_VERSION="${PG_VERSION:-17}"
PG_BIN="${PG_ROOT}/usr/lib/postgresql/${PG_VERSION}/bin"
PG_DATA="${PG_DATA:-${WORKSPACE_ROOT}/.local/pgdata}"
PG_SOCKET_DIR="${PG_SOCKET_DIR:-${WORKSPACE_ROOT}/.local/run}"
PG_PORT="${PG_PORT:-55432}"
PG_LOG="${PG_LOG:-${WORKSPACE_ROOT}/.local/postgres.log}"
PG_LIB="${PG_ROOT}/usr/lib/x86_64-linux-gnu"

DB_NAME="${DB_NAME:-global_network_resilience}"
SQL_FILE="${SQL_FILE:-${BASE_DIR}/db/schema.sql}"

PHP_ROOT="${PHP_ROOT:-${WORKSPACE_ROOT}/.local/php_root}"
PHP_INI="${WORKSPACE_ROOT}/.local/php_cli.ini"
DOC_ROOT="${BASE_DIR}/web"
HOST="${HOST:-localhost}"
PORT="${PORT:-8080}"

if [[ -d "${PG_LIB}" ]]; then
    export LD_LIBRARY_PATH="${PG_LIB}:${LD_LIBRARY_PATH:-}"
fi

# ── Python ────────────────────────────────────────────────────────────────────
PYTHON_BIN="${PYTHON_BIN:-${WORKSPACE_ROOT}/.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="$(command -v python3 || true)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# SUBCOMANDO: postgres — inicia PostgreSQL local
# ─────────────────────────────────────────────────────────────────────────────

run_postgres() {
    if [[ ! -x "${PG_BIN}/pg_ctl" ]]; then
        echo "Erro: binários do PostgreSQL não encontrados em ${PG_BIN}."
        echo "Execute antes: ./scripts/install_postgres_ubuntu.sh"
        exit 1
    fi

    mkdir -p "${PG_DATA}" "${PG_SOCKET_DIR}"

    if [[ ! -f "${PG_DATA}/PG_VERSION" ]]; then
        echo "Inicializando cluster PostgreSQL em ${PG_DATA}…"
        "${PG_BIN}/initdb" -D "${PG_DATA}" -A trust -U postgres >/dev/null
    fi

    if ! "${PG_BIN}/pg_ctl" -D "${PG_DATA}" status >/dev/null 2>&1; then
        echo "Iniciando PostgreSQL…"
        "${PG_BIN}/pg_ctl" -D "${PG_DATA}" -l "${PG_LOG}" \
            -o "-k ${PG_SOCKET_DIR} -p ${PG_PORT}" start >/dev/null
    else
        echo "PostgreSQL já está rodando."
    fi

    echo "PG_BIN=${PG_BIN}"
    echo "PG_DATA=${PG_DATA}"
    echo "PG_SOCKET_DIR=${PG_SOCKET_DIR}"
    echo "PG_PORT=${PG_PORT}"
}

# ─────────────────────────────────────────────────────────────────────────────
# SUBCOMANDO: bootstrap — inicializa banco completo
# ─────────────────────────────────────────────────────────────────────────────

run_bootstrap() {
    local PSQL="${PG_BIN}/psql"

    if [[ ! -x "${PSQL}" ]]; then
        echo "Erro: psql não encontrado em ${PSQL}."
        echo "Execute antes: ./scripts/install_postgres_ubuntu.sh"
        exit 1
    fi
    if [[ ! -f "${SQL_FILE}" ]]; then
        echo "Erro: arquivo SQL não encontrado em ${SQL_FILE}"
        exit 1
    fi
    if [[ -z "${PYTHON_BIN}" ]]; then
        echo "Erro: interpretador Python não encontrado."
        exit 1
    fi

    run_postgres >/dev/null

    PSQL_CMD=("${PSQL}" -h "${PG_SOCKET_DIR}" -p "${PG_PORT}" -U postgres)

    echo "Criando banco ${DB_NAME} (se necessário)…"
    DB_EXISTS="$("${PSQL_CMD[@]}" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" || true)"
    if [[ "${DB_EXISTS}" != "1" ]]; then
        "${PSQL_CMD[@]}" -d postgres -c "CREATE DATABASE ${DB_NAME};"
    fi

    echo "Habilitando extensões…"
    "${PSQL_CMD[@]}" -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS btree_gist;"

    echo "Aplicando schema de ${SQL_FILE}…"
    "${PSQL_CMD[@]}" -v ON_ERROR_STOP=1 -d "${DB_NAME}" -f "${SQL_FILE}"

    echo "Importando catálogo de cabos e endpoints…"
    PG_SOCKET="${PG_SOCKET_DIR}" PG_PORT="${PG_PORT}" DB_NAME="${DB_NAME}" \
        "${PYTHON_BIN}" "${SCRIPT_DIR}/rmifo_catalog.py" import

    echo "Validando tabelas criadas…"
    "${PSQL_CMD[@]}" -d "${DB_NAME}" -c "\dt"

    echo "Ambiente pronto. Banco ${DB_NAME} inicializado com sucesso."
    echo "Conexão: host=${PG_SOCKET_DIR} port=${PG_PORT} dbname=${DB_NAME} user=postgres"
}

# ─────────────────────────────────────────────────────────────────────────────
# SUBCOMANDO: web — inicia servidor PHP
# ─────────────────────────────────────────────────────────────────────────────

run_web() {
    local PHP_BIN="" PHP_EXT_DIR="" PHP_ARGS=()

    if [[ -d "${PHP_ROOT}" ]]; then
        PHP_BIN="$(find "${PHP_ROOT}/usr/bin" -maxdepth 1 -type f -name 'php8.*' 2>/dev/null \
                   | sort -V | tail -n 1)"
        PHP_EXT_DIR="$(find "${PHP_ROOT}/usr/lib/php" -mindepth 1 -maxdepth 1 -type d 2>/dev/null \
                       | sort -V | tail -n 1)"
    fi

    if [[ -n "${PHP_BIN}" && -x "${PHP_BIN}" && -n "${PHP_EXT_DIR}" && -d "${PHP_EXT_DIR}" ]]; then
        mkdir -p "$(dirname "${PHP_INI}")"
        cat > "${PHP_INI}" <<INI
extension_dir   = "${PHP_EXT_DIR}"
extension       = pdo.so
extension       = pdo_pgsql.so
extension       = pgsql.so
error_reporting = E_ALL
display_errors  = On
INI
        PHP_ARGS=(-c "${PHP_INI}")
    elif command -v php >/dev/null 2>&1; then
        PHP_BIN="$(command -v php)"
        echo "Aviso: usando PHP do sistema em ${PHP_BIN}."
    else
        echo "Erro: nenhum binário PHP disponível."
        echo "Tente: ./scripts/install_postgres_ubuntu.sh"
        exit 1
    fi

    if bash "${SCRIPT_DIR}/rmifo_dev.sh" postgres >/dev/null 2>&1; then
        echo "PostgreSQL local: OK"
    else
        echo "Aviso: PostgreSQL local indisponível — site em modo de visualização."
    fi

    export LD_LIBRARY_PATH="${PG_LIB}:${LD_LIBRARY_PATH:-}"
    export WORKSPACE_ROOT

    echo ""
    echo "======================================================"
    echo "  RMIFO — Global Network Resilience"
    echo "  Acesse: http://${HOST}:${PORT}/"
    echo "  Banco:  ${DB_NAME} (porta ${PG_PORT})"
    echo "  Ctrl+C para parar"
    echo "======================================================"
    echo ""

    exec "${PHP_BIN}" "${PHP_ARGS[@]}" -S "${HOST}:${PORT}" -t "${DOC_ROOT}"
}

# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

SUBCMD="${1:-help}"
shift || true

case "$SUBCMD" in
    postgres)   run_postgres ;;
    bootstrap)  run_bootstrap ;;
    web)        run_web ;;
    all)
        run_postgres
        run_bootstrap
        run_web
        ;;
    -h|--help|help)
        cat <<'EOF'
Uso: bash rmifo_dev.sh <subcomando>

Subcomandos:
  postgres    Inicia PostgreSQL local
  bootstrap   Cria DB + schema + importa catálogo de cabos
  web         Inicia servidor PHP embutido em http://localhost:8080/
  all         postgres → bootstrap → web

Variáveis de ambiente:
  PG_PORT, PG_DATA, PG_SOCKET_DIR, DB_NAME, SQL_FILE, HOST, PORT
EOF
        ;;
    *)
        echo "Subcomando desconhecido: $SUBCMD  (use: postgres | bootstrap | web | all)"
        exit 1
        ;;
esac
