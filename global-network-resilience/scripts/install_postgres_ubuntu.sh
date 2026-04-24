#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_ROOT="${PG_ROOT:-${BASE_DIR}/.local/postgresql}"
PG_VERSION="${PG_VERSION:-17}"
TMP_DIR="${BASE_DIR}/.local/tmp_pg_download"

mkdir -p "${PG_ROOT}" "${TMP_DIR}"
cd "${TMP_DIR}"

PHP_ROOT="${PHP_ROOT:-${BASE_DIR}/.local/php_root}"
TMP_PHP_DIR="${BASE_DIR}/.local/tmp_php_download"

mkdir -p "${PG_ROOT}" "${TMP_DIR}" "${PHP_ROOT}" "${TMP_PHP_DIR}"

echo "[1/4] Baixando PostgreSQL ${PG_VERSION}"
cd "${TMP_DIR}"
apt download "postgresql-${PG_VERSION}" "postgresql-client-${PG_VERSION}" libpq5

echo "[2/4] Extraindo PostgreSQL em ${PG_ROOT}"
rm -rf "${PG_ROOT}"
mkdir -p "${PG_ROOT}"
for deb_file in ./*.deb; do
  dpkg-deb -x "${deb_file}" "${PG_ROOT}"
done

echo "[3/4] Baixando PHP 8.4 CLI + extensão pgsql"
cd "${TMP_PHP_DIR}"
apt download php8.4-cli php8.4-pgsql php8.4-common php8.4-readline

echo "[4/4] Extraindo PHP em ${PHP_ROOT}"
rm -rf "${PHP_ROOT}"
mkdir -p "${PHP_ROOT}"
for deb_file in ./*.deb; do
  dpkg-deb -x "${deb_file}" "${PHP_ROOT}"
done

echo "Validando binarios"
test -x "${PG_ROOT}/usr/lib/postgresql/${PG_VERSION}/bin/postgres"
test -x "${PG_ROOT}/usr/lib/postgresql/${PG_VERSION}/bin/pg_ctl"
test -x "${PG_ROOT}/usr/lib/postgresql/${PG_VERSION}/bin/initdb"
test -x "${PG_ROOT}/usr/lib/postgresql/${PG_VERSION}/bin/psql"
test -x "${PHP_ROOT}/usr/bin/php8.4"

echo "PostgreSQL e PHP extraidos com sucesso."
echo "Próximo passo:"
echo "  ./scripts/bootstrap_test_db.sh   # cria o banco"
echo "  ./scripts/start_web.sh            # inicia servidor web"