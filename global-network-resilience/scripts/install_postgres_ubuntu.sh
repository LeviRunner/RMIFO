#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_ROOT="${PG_ROOT:-${BASE_DIR}/.local/postgresql}"
PG_VERSION="${PG_VERSION:-$(apt-cache search '^postgresql-[0-9]+$' | sed -E 's/^postgresql-([0-9]+).*/\1/' | sort -n | tail -n 1)}"
TMP_DIR="${BASE_DIR}/.local/tmp_pg_download"

mkdir -p "${PG_ROOT}" "${TMP_DIR}"
cd "${TMP_DIR}"

PHP_ROOT="${PHP_ROOT:-${BASE_DIR}/.local/php_root}"
TMP_PHP_DIR="${BASE_DIR}/.local/tmp_php_download"
PHP_PACKAGE_PREFIX="${PHP_PACKAGE_PREFIX:-$(if command -v php >/dev/null 2>&1; then php -r 'echo "php", PHP_MAJOR_VERSION, ".", PHP_MINOR_VERSION;'; else apt-cache search '^php8\.[0-9]+-pgsql$' | sed -E 's/^(php8\.[0-9]+)-pgsql.*/\1/' | sort -V | tail -n 1; fi)}"

mkdir -p "${PG_ROOT}" "${TMP_DIR}" "${PHP_ROOT}" "${TMP_PHP_DIR}"

if [[ -z "${PG_VERSION}" ]]; then
  echo "Erro: nenhuma versao de PostgreSQL encontrada no apt-cache."
  exit 1
fi

if [[ -z "${PHP_PACKAGE_PREFIX}" ]]; then
  echo "Erro: nenhuma versao de PHP compatível encontrada."
  exit 1
fi

echo "[1/4] Baixando PostgreSQL ${PG_VERSION}"
cd "${TMP_DIR}"
apt download "postgresql-${PG_VERSION}" "postgresql-client-${PG_VERSION}" libpq5

echo "[2/4] Extraindo PostgreSQL em ${PG_ROOT}"
rm -rf "${PG_ROOT}"
mkdir -p "${PG_ROOT}"
for deb_file in ./*.deb; do
  dpkg-deb -x "${deb_file}" "${PG_ROOT}"
done

echo "[3/4] Baixando ${PHP_PACKAGE_PREFIX} CLI + extensao pgsql"
cd "${TMP_PHP_DIR}"
apt download "${PHP_PACKAGE_PREFIX}-cli" "${PHP_PACKAGE_PREFIX}-pgsql" "${PHP_PACKAGE_PREFIX}-common" "${PHP_PACKAGE_PREFIX}-readline"

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
test -x "${PHP_ROOT}/usr/bin/${PHP_PACKAGE_PREFIX}"

echo "PostgreSQL e PHP extraidos com sucesso."
echo "Próximo passo:"
echo "  ./scripts/bootstrap_test_db.sh   # cria o banco"
echo "  ./scripts/start_web.sh            # inicia servidor web"