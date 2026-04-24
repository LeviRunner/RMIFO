#!/usr/bin/env bash
# start_web.sh — inicia servidor PHP embutido para a aplicação de busca binária
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_ROOT="${PG_ROOT:-${BASE_DIR}/.local/postgresql}"
PHP_ROOT="${PHP_ROOT:-${BASE_DIR}/.local/php_root}"
PHP_BIN="${PHP_ROOT}/usr/bin/php8.4"
PHP_EXT_DIR="${PHP_ROOT}/usr/lib/php/20240924"
PG_LIB="${PG_ROOT}/usr/lib/x86_64-linux-gnu"
PHP_INI="${BASE_DIR}/.local/php_cli.ini"
DOC_ROOT="${BASE_DIR}/implementacoes/busca_binaria"
HOST="${HOST:-localhost}"
PORT="${PORT:-8080}"

# ── Verifica PHP local ────────────────────────────────────────────────────────
if [[ ! -x "${PHP_BIN}" ]]; then
    echo "Erro: PHP não encontrado em ${PHP_BIN}."
    echo "Execute antes: ./scripts/install_postgres_ubuntu.sh  (extrai também PHP)"
    exit 1
fi

# ── Sobe PostgreSQL local se necessário ──────────────────────────────────────
"${BASE_DIR}/scripts/start_local_postgres.sh" >/dev/null
echo "PostgreSQL local: OK"

# ── Gera php.ini mínimo ───────────────────────────────────────────────────────
mkdir -p "$(dirname "${PHP_INI}")"
cat > "${PHP_INI}" <<INI
extension_dir   = "${PHP_EXT_DIR}"
extension       = pdo.so
extension       = pdo_pgsql.so
extension       = pgsql.so
error_reporting = E_ALL
display_errors  = On
INI

# ── Exporta libs necessárias ─────────────────────────────────────────────────
export LD_LIBRARY_PATH="${PG_LIB}:${LD_LIBRARY_PATH:-}"

# ── Inicia servidor ───────────────────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  Busca Binária — Cabos Submarinos"
echo "  Acesse: http://${HOST}:${PORT}/"
echo "  Banco:  global_network_resilience (porta 55432)"
echo "  Ctrl+C para parar"
echo "======================================================"
echo ""

exec "${PHP_BIN}" -c "${PHP_INI}" -S "${HOST}:${PORT}" -t "${DOC_ROOT}"
