#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Grant cap_net_raw to mtr so it can run without sudo (needed for ICMP raw sockets)
MTR_BIN="$(which mtr 2>/dev/null || true)"
if [ -n "$MTR_BIN" ]; then
  setcap cap_net_raw+ep "$MTR_BIN" 2>/dev/null || true
fi

# Run SaaS schema migration (idempotent — skips if already applied)
php "$SCRIPT_DIR/scripts/migrate_saas.php" || true

echo "[RMIFO] Iniciando servidor..."
exec php -S "0.0.0.0:${PORT:-8080}" -t "$SCRIPT_DIR/web/"
