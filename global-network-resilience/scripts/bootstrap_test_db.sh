#!/usr/bin/env bash
set -euo pipefail

DB_NAME="${DB_NAME:-global_network_resilience}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
BASE_DIR="${WORKSPACE_ROOT}/global-network-resilience"
SQL_FILE="${SQL_FILE:-${BASE_DIR}/db/schema.sql}"
PYTHON_BIN="${PYTHON_BIN:-${WORKSPACE_ROOT}/.venv/bin/python}"
PG_ROOT="${PG_ROOT:-${WORKSPACE_ROOT}/.local/postgresql}"
if [[ -z "${PG_VERSION:-}" ]]; then
  PG_VERSION="$(find "${PG_ROOT}/usr/lib/postgresql" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -n | tail -n 1)"
fi
PG_VERSION="${PG_VERSION:-17}"
PG_BIN="${PG_ROOT}/usr/lib/postgresql/${PG_VERSION}/bin"
PG_SOCKET_DIR="${PG_SOCKET_DIR:-${WORKSPACE_ROOT}/.local/run}"
PG_PORT="${PG_PORT:-55432}"
PG_LIB="${PG_ROOT}/usr/lib/x86_64-linux-gnu"
PSQL="${PG_BIN}/psql"

if [[ -d "${PG_LIB}" ]]; then
  export LD_LIBRARY_PATH="${PG_LIB}:${LD_LIBRARY_PATH:-}"
fi

if [[ ! -x "${PSQL}" ]]; then
  echo "Erro: binario local do psql nao encontrado em ${PSQL}."
  echo "Execute antes: ./scripts/install_postgres_ubuntu.sh"
  exit 1
fi

if [[ ! -f "${SQL_FILE}" ]]; then
  echo "Erro: arquivo SQL nao encontrado em ${SQL_FILE}"
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "Erro: interpretador Python nao encontrado para importar dados auxiliares."
  exit 1
fi

bash "${BASE_DIR}/scripts/start_local_postgres.sh" >/dev/null

PSQL_CMD=("${PSQL}" -h "${PG_SOCKET_DIR}" -p "${PG_PORT}" -U postgres)

echo "Criando banco ${DB_NAME} (se necessario)..."
DB_EXISTS="$("${PSQL_CMD[@]}" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" || true)"
if [[ "${DB_EXISTS}" != "1" ]]; then
  "${PSQL_CMD[@]}" -d postgres -c "CREATE DATABASE ${DB_NAME};"
fi

echo "Habilitando extensoes necessarias..."
"${PSQL_CMD[@]}" -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS btree_gist;"

echo "Aplicando schema de ${SQL_FILE}..."
"${PSQL_CMD[@]}" -v ON_ERROR_STOP=1 -d "${DB_NAME}" -f "${SQL_FILE}"

echo "Importando catalogo detalhado de cabos e endpoints..."
PG_SOCKET="${PG_SOCKET_DIR}" PG_PORT="${PG_PORT}" DB_NAME="${DB_NAME}" \
  "${PYTHON_BIN}" "${BASE_DIR}/scripts/import_catalog_data.py"

echo "Validando tabelas criadas..."
"${PSQL_CMD[@]}" -d "${DB_NAME}" -c "\dt"

echo "Ambiente pronto. Banco ${DB_NAME} preparado com sucesso."
echo "Conexao: host=${PG_SOCKET_DIR} port=${PG_PORT} dbname=${DB_NAME} user=postgres"