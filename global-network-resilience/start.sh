#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[RMIFO] Iniciando servidor..."
exec php -S "0.0.0.0:${PORT:-8080}" -t "$SCRIPT_DIR/web/"
