#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="$SCRIPT_DIR/db/schema.sql"
SEED="$SCRIPT_DIR/db/seed.sql"

echo "[RMIFO] Iniciando deploy..."

# Aplica schema e seed se DATABASE_URL estiver disponível
if [[ -n "${DATABASE_URL:-}" ]]; then
    echo "[RMIFO] Aplicando schema..."
    psql "$DATABASE_URL" -f "$SCHEMA" && echo "[RMIFO] Schema OK."

    echo "[RMIFO] Aplicando seed..."
    psql "$DATABASE_URL" -f "$SEED" && echo "[RMIFO] Seed OK."
else
    echo "[RMIFO] DATABASE_URL não definido — pulando schema/seed."
fi

echo "[RMIFO] Iniciando PHP server na porta ${PORT:-8080}..."
exec php -S "0.0.0.0:${PORT:-8080}" -t "$SCRIPT_DIR/web/"