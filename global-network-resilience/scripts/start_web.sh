#!/usr/bin/env bash
# start_web.sh — inicia servidor PHP embutido para a aplicação de busca binária
set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
BASE_DIR="${WORKSPACE_ROOT}/global-network-resilience"
PG_ROOT="${PG_ROOT:-${WORKSPACE_ROOT}/.local/postgresql}"
PHP_ROOT="${PHP_ROOT:-${WORKSPACE_ROOT}/.local/php_root}"
PHP_BIN=""
PHP_EXT_DIR=""
PG_LIB="${PG_ROOT}/usr/lib/x86_64-linux-gnu"
PHP_INI="${WORKSPACE_ROOT}/.local/php_cli.ini"
DOC_ROOT="${BASE_DIR}/web"
HOST="${HOST:-localhost}"
PORT="${PORT:-8080}"
PHP_ARGS=()

# ── Resolve PHP disponível ────────────────────────────────────────────────────
if [[ -d "${PHP_ROOT}" ]]; then
    PHP_BIN="$(find "${PHP_ROOT}/usr/bin" -maxdepth 1 -type f -name 'php8.*' 2>/dev/null | sort -V | tail -n 1)"
    PHP_EXT_DIR="$(find "${PHP_ROOT}/usr/lib/php" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -V | tail -n 1)"
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
    echo "Aviso: se pdo_pgsql/PostgreSQL local nao estiverem disponiveis, a interface abre em modo de visualizacao."
else
    echo "Erro: nenhum binario PHP disponivel."
    echo "Tente instalar dependencias com ./scripts/install_postgres_ubuntu.sh"
    exit 1
fi

# ── Sobe PostgreSQL local se necessário ──────────────────────────────────────
if bash "${BASE_DIR}/scripts/start_local_postgres.sh" >/dev/null 2>&1; then
    echo "PostgreSQL local: OK"
else
    echo "Aviso: PostgreSQL local indisponivel; o site sera iniciado para validacao visual."
fi

# ── Exporta libs necessárias ─────────────────────────────────────────────────
export LD_LIBRARY_PATH="${PG_LIB}:${LD_LIBRARY_PATH:-}"
export WORKSPACE_ROOT

# ── Inicia servidor ───────────────────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  Busca Binária — Cabos Submarinos"
echo "  Acesse: http://${HOST}:${PORT}/"
echo "  Banco:  global_network_resilience (porta 55432)"
echo "  Ctrl+C para parar"
echo "======================================================"
echo ""

exec "${PHP_BIN}" "${PHP_ARGS[@]}" -S "${HOST}:${PORT}" -t "${DOC_ROOT}"
