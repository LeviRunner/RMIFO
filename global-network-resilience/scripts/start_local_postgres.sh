#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_ROOT="${PG_ROOT:-${BASE_DIR}/.local/postgresql}"
PG_VERSION="${PG_VERSION:-17}"
PG_BIN="${PG_ROOT}/usr/lib/postgresql/${PG_VERSION}/bin"
PG_DATA="${PG_DATA:-${BASE_DIR}/.local/pgdata}"
PG_SOCKET_DIR="${PG_SOCKET_DIR:-${BASE_DIR}/.local/run}"
PG_PORT="${PG_PORT:-55432}"
PG_LOG="${PG_LOG:-${BASE_DIR}/.local/postgres.log}"
PG_LIB="${PG_ROOT}/usr/lib/x86_64-linux-gnu"

if [[ -d "${PG_LIB}" ]]; then
  export LD_LIBRARY_PATH="${PG_LIB}:${LD_LIBRARY_PATH:-}"
fi

if [[ ! -x "${PG_BIN}/pg_ctl" ]]; then
  echo "Erro: binarios locais do PostgreSQL nao encontrados em ${PG_BIN}."
  echo "Execute antes: ./scripts/install_postgres_ubuntu.sh"
  exit 1
fi

mkdir -p "${PG_DATA}" "${PG_SOCKET_DIR}"

if [[ ! -f "${PG_DATA}/PG_VERSION" ]]; then
  "${PG_BIN}/initdb" -D "${PG_DATA}" -A trust -U postgres >/dev/null
fi

if ! "${PG_BIN}/pg_ctl" -D "${PG_DATA}" status >/dev/null 2>&1; then
  "${PG_BIN}/pg_ctl" -D "${PG_DATA}" -l "${PG_LOG}" -o "-k ${PG_SOCKET_DIR} -p ${PG_PORT}" start >/dev/null
fi

echo "PG_BIN=${PG_BIN}"
echo "PG_DATA=${PG_DATA}"
echo "PG_SOCKET_DIR=${PG_SOCKET_DIR}"
echo "PG_PORT=${PG_PORT}"
