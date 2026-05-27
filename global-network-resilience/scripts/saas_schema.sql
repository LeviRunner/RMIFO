-- RMIFO SaaS Schema
-- PostgreSQL schema for credit-based API monetization

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Users
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name          TEXT,
    credits       INTEGER NOT NULL DEFAULT 0,
    suspended     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- API keys
CREATE TABLE IF NOT EXISTS api_keys (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash   TEXT UNIQUE NOT NULL,
    key_prefix TEXT NOT NULL,
    label      TEXT,
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

-- Credit packs (product catalog)
CREATE TABLE IF NOT EXISTS credit_packs (
    id               SERIAL PRIMARY KEY,
    name             TEXT NOT NULL,
    credits          INTEGER NOT NULL,
    price_cents      INTEGER NOT NULL,
    stripe_price_id  TEXT,
    active           BOOLEAN NOT NULL DEFAULT TRUE
);

-- Credit purchases (Stripe checkout sessions)
CREATE TABLE IF NOT EXISTS credit_purchases (
    id                 SERIAL PRIMARY KEY,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pack_id            INTEGER REFERENCES credit_packs(id),
    credits            INTEGER NOT NULL,
    amount_cents       INTEGER NOT NULL,
    stripe_session_id  TEXT UNIQUE,
    stripe_payment_id  TEXT,
    status             TEXT NOT NULL DEFAULT 'pending',  -- pending, completed, refunded
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_purchases_user ON credit_purchases(user_id);
CREATE INDEX IF NOT EXISTS idx_purchases_session ON credit_purchases(stripe_session_id);

-- Service pricing
CREATE TABLE IF NOT EXISTS service_pricing (
    id           SERIAL PRIMARY KEY,
    service      TEXT UNIQUE NOT NULL,
    credits_cost INTEGER NOT NULL DEFAULT 1,
    description  TEXT,
    active       BOOLEAN NOT NULL DEFAULT TRUE
);

INSERT INTO service_pricing (service, credits_cost, description) VALUES
    ('geo_lookup',      1,  'Single IP geolocation lookup'),
    ('geo_bulk',        1,  'Bulk IP geolocation (per IP)'),
    ('security',        2,  'IP security/threat intelligence'),
    ('asn',             1,  'ASN lookup'),
    ('bgp',             3,  'BGP routing lookup'),
    ('tcp_probe',       5,  'TCP connectivity probe'),
    ('mtr_probe',      20,  'MTR traceroute probe'),
    ('tunnel_create',  50,  'WireGuard tunnel creation'),
    ('tunnel_delete',   0,  'WireGuard tunnel deletion'),
    ('tunnel_list',     1,  'List WireGuard tunnels'),
    ('db_download',   500,  'IP database download'),
    ('db_list',         1,  'List available IP databases')
ON CONFLICT (service) DO NOTHING;

-- Usage log
CREATE TABLE IF NOT EXISTS usage_log (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    api_key_id  INTEGER REFERENCES api_keys(id) ON DELETE SET NULL,
    service     TEXT NOT NULL,
    credits     INTEGER NOT NULL DEFAULT 0,
    ip          TEXT,
    status      TEXT NOT NULL DEFAULT 'ok',   -- ok, error, insufficient_credits
    duration_ms INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_service ON usage_log(service, created_at DESC);

-- WireGuard tunnels
CREATE TABLE IF NOT EXISTS wg_tunnels (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tunnel_id       TEXT UNIQUE NOT NULL DEFAULT gen_random_uuid()::TEXT,
    client_pubkey   TEXT UNIQUE NOT NULL,
    client_ip       TEXT UNIQUE NOT NULL,
    server_pubkey   TEXT,
    preshared_key   TEXT,
    label           TEXT,
    status          TEXT NOT NULL DEFAULT 'active',  -- active, suspended, deleted
    bytes_rx        BIGINT NOT NULL DEFAULT 0,
    bytes_tx        BIGINT NOT NULL DEFAULT 0,
    last_handshake  TIMESTAMPTZ,
    credits_per_gb  INTEGER NOT NULL DEFAULT 10,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tunnels_user ON wg_tunnels(user_id);

-- IP database downloads
CREATE TABLE IF NOT EXISTS db_downloads (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    db_name    TEXT NOT NULL,
    version    TEXT NOT NULL,
    url        TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    downloaded BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_downloads_user ON db_downloads(user_id);

-- Stripe webhook events (idempotency)
CREATE TABLE IF NOT EXISTS webhook_events (
    stripe_event_id TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Function: atomically debit credits and log usage
CREATE OR REPLACE FUNCTION debit_credits(
    p_user_id    INTEGER,
    p_api_key_id INTEGER,
    p_service    TEXT,
    p_credits    INTEGER,
    p_ip         TEXT,
    p_duration   INTEGER
) RETURNS BOOLEAN AS $$
DECLARE
    v_ok BOOLEAN := FALSE;
BEGIN
    -- Deduct credits atomically
    UPDATE users
    SET    credits    = credits - p_credits,
           updated_at = NOW()
    WHERE  id = p_user_id
      AND  credits >= p_credits
      AND  suspended = FALSE;

    IF FOUND THEN
        v_ok := TRUE;
        INSERT INTO usage_log (user_id, api_key_id, service, credits, ip, status, duration_ms)
        VALUES (p_user_id, p_api_key_id, p_service, p_credits, p_ip, 'ok', p_duration);
    ELSE
        INSERT INTO usage_log (user_id, api_key_id, service, credits, ip, status, duration_ms)
        VALUES (p_user_id, p_api_key_id, p_service, p_credits, p_ip, 'insufficient_credits', p_duration);
    END IF;

    RETURN v_ok;
END;
$$ LANGUAGE plpgsql;

-- Function: credit refund on error
CREATE OR REPLACE FUNCTION refund_credits(
    p_user_id INTEGER,
    p_credits INTEGER,
    p_service TEXT
) RETURNS VOID AS $$
BEGIN
    UPDATE users SET credits = credits + p_credits, updated_at = NOW() WHERE id = p_user_id;
    INSERT INTO usage_log (user_id, service, credits, status) VALUES (p_user_id, p_service, -p_credits, 'refund');
END;
$$ LANGUAGE plpgsql;

-- Default credit packs
INSERT INTO credit_packs (name, credits, price_cents) VALUES
    ('Starter',      1000,   499),
    ('Pro',          5000,  1999),
    ('Business',    25000,  7999),
    ('Enterprise', 100000, 24999)
ON CONFLICT DO NOTHING;
