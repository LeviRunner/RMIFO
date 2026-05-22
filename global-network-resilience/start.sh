#!/usr/bin/env bash
# start.sh — entrypoint Railway
# Usa PHP (não psql) para aplicar schema/seed — mais confiável no Railway.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[RMIFO] Iniciando deploy..."

# Aplica schema e seed via PHP (sem depender do psql no PATH)
php "$SCRIPT_DIR/db/setup.php"

echo "[RMIFO] Iniciando PHP server na porta ${PORT:-8080}..."
exec php -S "0.0.0.0:${PORT:-8080}" -t "$SCRIPT_DIR/web/"
