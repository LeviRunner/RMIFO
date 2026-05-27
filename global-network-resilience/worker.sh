#!/usr/bin/env bash
# WireGuard Provisioner — run as a separate Railway service
# Start command: bash worker.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure wg0 interface exists (requires NET_ADMIN capability on Railway)
if ! ip link show wg0 &>/dev/null; then
  ip link add wg0 type wireguard 2>/dev/null || true
  ip address add "${WG_SUBNET:-10.88.0.1/16}" dev wg0 2>/dev/null || true
  ip link set wg0 up 2>/dev/null || true
fi

# Load server private key if path provided
if [ -n "${WG_SERVER_PRIVKEY_PATH:-}" ] && [ -f "$WG_SERVER_PRIVKEY_PATH" ]; then
  wg set wg0 private-key "$WG_SERVER_PRIVKEY_PATH" listen-port "${WG_SERVER_PORT:-51820}" 2>/dev/null || true
fi

echo "[worker] Starting WireGuard provisioner..."
exec python3 "$SCRIPT_DIR/scripts/wg_provisioner.py"
