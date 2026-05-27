#!/usr/bin/env python3
"""
RMIFO WireGuard Provisioner Daemon
Syncs wg_tunnels table → wg0 interface, bills traffic credits, suspends low-credit users.
"""

import os
import re
import sys
import time
import logging
import subprocess
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("wg_provisioner")

DATABASE_URL   = os.environ["DATABASE_URL"]
WG_INTERFACE   = os.environ.get("WG_INTERFACE", "wg0")
WG_SERVER_KEY  = os.environ.get("WG_SERVER_PRIVKEY_PATH", "/etc/wireguard/server_privkey")
WG_SUBNET      = os.environ.get("WG_SUBNET", "10.88.0.0/16")
WG_PORT        = int(os.environ.get("WG_SERVER_PORT", "51820"))
CREDITS_PER_GB = int(os.environ.get("CREDITS_PER_GB", "10"))
LOW_CREDIT_THR = int(os.environ.get("LOW_CREDIT_THRESHOLD", "50"))


def db_connect():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def wg_run(*args, check=True) -> str:
    cmd = ["wg"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, check=check)
    return result.stdout.strip()


def wg_quick_run(*args, check=True) -> str:
    cmd = ["wg-quick"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, check=check)
    return result.stdout.strip()


def ip_run(*args, check=True) -> str:
    cmd = ["ip"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, check=check)
    return result.stdout.strip()


def gen_keypair():
    privkey = subprocess.run(["wg", "genkey"], capture_output=True, text=True, check=True).stdout.strip()
    pubkey  = subprocess.run(["wg", "pubkey"], input=privkey, capture_output=True, text=True, check=True).stdout.strip()
    return privkey, pubkey


def gen_psk():
    return subprocess.run(["wg", "genpsk"], capture_output=True, text=True, check=True).stdout.strip()


def next_client_ip(conn) -> str:
    """Allocate the next free IP from WG_SUBNET (10.88.x.x, skip .0 and .1)."""
    parts = WG_SUBNET.split("/")[0].split(".")
    base_a, base_b = int(parts[0]), int(parts[1])

    with conn.cursor() as cur:
        cur.execute("SELECT client_ip FROM wg_tunnels WHERE status != 'deleted'")
        used = {r["client_ip"] for r in cur.fetchall()}

    for c in range(0, 256):
        for d in range(2, 255):
            candidate = f"{base_a}.{base_b}.{c}.{d}"
            if candidate not in used:
                return candidate
    raise RuntimeError("WireGuard IP pool exhausted")


def sync_peers(conn):
    """Add/remove wg peers to match the DB state."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT tunnel_id, client_pubkey, client_ip, preshared_key, status
            FROM wg_tunnels
            WHERE status IN ('active', 'suspended', 'deleted')
        """)
        tunnels = cur.fetchall()

    # Get current peers from wg show
    try:
        show_out = wg_run("show", WG_INTERFACE, "peers", check=False)
        active_pubkeys = set(show_out.splitlines()) if show_out else set()
    except Exception:
        active_pubkeys = set()

    for t in tunnels:
        pubkey = t["client_pubkey"]
        if t["status"] == "deleted":
            if pubkey in active_pubkeys:
                try:
                    wg_run("set", WG_INTERFACE, "peer", pubkey, "remove")
                    ip_run("route", "del", f"{t['client_ip']}/32", "dev", WG_INTERFACE, check=False)
                    log.info("Removed deleted peer %s (%s)", t["tunnel_id"], t["client_ip"])
                except Exception as e:
                    log.warning("Error removing peer %s: %s", t["tunnel_id"], e)
            continue

        if t["status"] == "suspended":
            if pubkey in active_pubkeys:
                try:
                    wg_run("set", WG_INTERFACE, "peer", pubkey, "remove")
                    log.info("Suspended peer %s (%s)", t["tunnel_id"], t["client_ip"])
                except Exception as e:
                    log.warning("Error suspending peer %s: %s", t["tunnel_id"], e)
            continue

        # active
        if pubkey not in active_pubkeys:
            try:
                psk_args = ["preshared-key", "/dev/stdin"] if t["preshared_key"] else []
                cmd = ["wg", "set", WG_INTERFACE, "peer", pubkey,
                       "allowed-ips", f"{t['client_ip']}/32"]
                if t["preshared_key"]:
                    proc = subprocess.run(
                        cmd + ["preshared-key", "/dev/stdin"],
                        input=t["preshared_key"], capture_output=True, text=True
                    )
                else:
                    subprocess.run(cmd, check=True)
                ip_run("route", "add", f"{t['client_ip']}/32", "dev", WG_INTERFACE, check=False)
                log.info("Added peer %s (%s)", t["tunnel_id"], t["client_ip"])
            except Exception as e:
                log.warning("Error adding peer %s: %s", t["tunnel_id"], e)


def poll_traffic(conn):
    """Read wg transfer stats, update DB, bill credits."""
    try:
        dump = wg_run("show", WG_INTERFACE, "transfer", check=False)
    except Exception as e:
        log.warning("wg show transfer failed: %s", e)
        return

    # Format: <pubkey>\t<rx_bytes>\t<tx_bytes>
    traffic_map = {}
    for line in dump.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            traffic_map[parts[0]] = (int(parts[1]), int(parts[2]))

    if not traffic_map:
        return

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, user_id, client_pubkey, bytes_rx, bytes_tx, credits_per_gb, status
            FROM wg_tunnels
            WHERE status = 'active' AND client_pubkey = ANY(%s)
        """, (list(traffic_map.keys()),))
        tunnels = cur.fetchall()

    for t in tunnels:
        pubkey = t["client_pubkey"]
        if pubkey not in traffic_map:
            continue
        new_rx, new_tx = traffic_map[pubkey]
        delta_bytes = max(0, (new_rx - t["bytes_rx"]) + (new_tx - t["bytes_tx"]))
        delta_gb = delta_bytes / (1024 ** 3)
        credits_to_bill = int(delta_gb * t["credits_per_gb"])

        with conn.cursor() as cur:
            cur.execute("""
                UPDATE wg_tunnels SET bytes_rx = %s, bytes_tx = %s, updated_at = NOW()
                WHERE id = %s
            """, (new_rx, new_tx, t["id"]))

            if credits_to_bill > 0:
                cur.execute("""
                    UPDATE users SET credits = GREATEST(0, credits - %s), updated_at = NOW()
                    WHERE id = %s
                """, (credits_to_bill, t["user_id"]))
                cur.execute("""
                    INSERT INTO usage_log (user_id, service, credits, status)
                    VALUES (%s, 'tunnel_traffic', %s, 'ok')
                """, (t["user_id"], credits_to_bill))
                log.debug("Billed %d credits to user %d (%.3f GB)", credits_to_bill, t["user_id"], delta_gb)

        conn.commit()


def suspend_low_credit_users(conn):
    """Suspend WireGuard tunnels for users below credit threshold."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT t.id, t.tunnel_id, t.client_pubkey, t.user_id
            FROM wg_tunnels t
            JOIN users u ON u.id = t.user_id
            WHERE t.status = 'active' AND (u.credits < %s OR u.suspended = TRUE)
        """, (LOW_CREDIT_THR,))
        to_suspend = cur.fetchall()

    for t in to_suspend:
        try:
            wg_run("set", WG_INTERFACE, "peer", t["client_pubkey"], "remove", check=False)
        except Exception as e:
            log.warning("Could not remove peer %s: %s", t["tunnel_id"], e)
        with conn.cursor() as cur:
            cur.execute("UPDATE wg_tunnels SET status = 'suspended', updated_at = NOW() WHERE id = %s", (t["id"],))
        conn.commit()
        log.info("Suspended tunnel %s (user %d low credits)", t["tunnel_id"], t["user_id"])


def restore_funded_users(conn):
    """Re-activate tunnels for users that have topped up above threshold."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT t.id, t.tunnel_id, t.client_pubkey, t.client_ip, t.preshared_key, t.user_id
            FROM wg_tunnels t
            JOIN users u ON u.id = t.user_id
            WHERE t.status = 'suspended' AND u.credits >= %s AND u.suspended = FALSE
        """, (LOW_CREDIT_THR,))
        to_restore = cur.fetchall()

    for t in to_restore:
        try:
            cmd = ["wg", "set", WG_INTERFACE, "peer", t["client_pubkey"],
                   "allowed-ips", f"{t['client_ip']}/32"]
            if t["preshared_key"]:
                subprocess.run(cmd + ["preshared-key", "/dev/stdin"],
                               input=t["preshared_key"], capture_output=True, text=True)
            else:
                subprocess.run(cmd, check=True)
            ip_run("route", "add", f"{t['client_ip']}/32", "dev", WG_INTERFACE, check=False)
        except Exception as e:
            log.warning("Could not restore peer %s: %s", t["tunnel_id"], e)
            continue
        with conn.cursor() as cur:
            cur.execute("UPDATE wg_tunnels SET status = 'active', updated_at = NOW() WHERE id = %s", (t["id"],))
        conn.commit()
        log.info("Restored tunnel %s (user %d)", t["tunnel_id"], t["user_id"])


def update_handshakes(conn):
    """Read last handshake times from wg and update DB."""
    try:
        dump = wg_run("show", WG_INTERFACE, "latest-handshakes", check=False)
    except Exception:
        return

    handshakes = {}
    for line in dump.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1] != "0":
            ts = datetime.fromtimestamp(int(parts[1]), tz=timezone.utc)
            handshakes[parts[0]] = ts

    if not handshakes:
        return

    with conn.cursor() as cur:
        for pubkey, ts in handshakes.items():
            cur.execute("""
                UPDATE wg_tunnels SET last_handshake = %s, updated_at = NOW()
                WHERE client_pubkey = %s AND (last_handshake IS NULL OR last_handshake < %s)
            """, (ts, pubkey, ts))
    conn.commit()


# ── Exposed helper for api_gateway.php (called via CLI) ──────────────────────

def provision_tunnel(user_id: int, label: str) -> dict:
    conn = db_connect()
    try:
        privkey, pubkey = gen_keypair()
        psk = gen_psk()
        client_ip = next_client_ip(conn)

        # Read server public key
        try:
            server_privkey = open(WG_SERVER_KEY).read().strip()
            server_pubkey = subprocess.run(
                ["wg", "pubkey"], input=server_privkey, capture_output=True, text=True
            ).stdout.strip()
        except Exception:
            server_pubkey = ""

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO wg_tunnels (user_id, client_pubkey, client_ip, server_pubkey, preshared_key, label)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING tunnel_id, client_ip
            """, (user_id, pubkey, client_ip, server_pubkey, psk, label))
            row = cur.fetchone()
        conn.commit()

        # Add peer immediately
        cmd = ["wg", "set", WG_INTERFACE, "peer", pubkey, "allowed-ips", f"{client_ip}/32"]
        subprocess.run(cmd + ["preshared-key", "/dev/stdin"],
                       input=psk, capture_output=True, text=True)
        ip_run("route", "add", f"{client_ip}/32", "dev", WG_INTERFACE, check=False)

        return {
            "tunnel_id":    row["tunnel_id"],
            "client_ip":    client_ip,
            "client_pubkey": pubkey,
            "client_privkey": privkey,
            "preshared_key": psk,
            "server_pubkey": server_pubkey,
        }
    finally:
        conn.close()


# ── Main loop ────────────────────────────────────────────────────────────────

def run_all():
    conn = db_connect()
    try:
        sync_peers(conn)
        poll_traffic(conn)
        update_handshakes(conn)
        suspend_low_credit_users(conn)
        restore_funded_users(conn)
    except Exception as e:
        log.error("Cycle error: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return
    conn.close()


def main():
    log.info("WireGuard Provisioner starting (interface=%s)", WG_INTERFACE)

    schedule.every(30).seconds.do(run_all)
    schedule.every(1).hour.do(lambda: log.info("Hourly heartbeat — provisioner running"))

    # Run immediately on startup
    run_all()

    while True:
        schedule.run_pending()
        time.sleep(5)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "provision":
        # CLI mode: provision_tunnel <user_id> [label]
        import json
        uid   = int(sys.argv[2])
        label = sys.argv[3] if len(sys.argv) > 3 else ""
        result = provision_tunnel(uid, label)
        print(json.dumps(result))
    else:
        main()
