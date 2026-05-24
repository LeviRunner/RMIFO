#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
BASE_DIR="${WORKSPACE_ROOT}/global-network-resilience"
PG_ROOT="${PG_ROOT:-${WORKSPACE_ROOT}/.local/postgresql}"
if [[ -z "${PG_VERSION:-}" ]]; then
  PG_VERSION="$(find "${PG_ROOT}/usr/lib/postgresql" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -n | tail -n 1)"
fi
PG_VERSION="${PG_VERSION:-17}"
PG_BIN="${PG_ROOT}/usr/lib/postgresql/${PG_VERSION}/bin"
PG_DATA="${PG_DATA:-${WORKSPACE_ROOT}/.local/pgdata}"
PG_SOCKET_DIR="${PG_SOCKET_DIR:-${WORKSPACE_ROOT}/.local/run}"
PG_PORT="${PG_PORT:-55432}"
PG_LOG="${PG_LOG:-${WORKSPACE_ROOT}/.local/postgres.log}"
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
