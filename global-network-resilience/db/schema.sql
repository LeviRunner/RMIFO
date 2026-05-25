-- Schema RMIFO — Global Network Resilience
-- Tabela única de telemetria: cable_telemetry_samples
-- Rollups são VIEWS calculadas automaticamente (sem tabelas físicas redundantes)
--
-- Uso (Railway ou psql):
--   psql $DATABASE_URL -f schema.sql

-- ── Topologia ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nodes (
    node_id      SERIAL PRIMARY KEY,
    node_name    VARCHAR(255),
    latitude     DOUBLE PRECISION,
    longitude    DOUBLE PRECISION,
    country_code VARCHAR(5),
    asn          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_nodes_geo          ON nodes(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_nodes_country_code ON nodes(country_code);

-- ── Cabos submarinos ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cables (
    cable_id              VARCHAR(255) PRIMARY KEY,
    cable_name            VARCHAR(255) NOT NULL,
    owners                TEXT,
    capacity_gbps         DOUBLE PRECISION,
    length_km             INTEGER,
    rfs_year              INTEGER,
    is_operational        BOOLEAN DEFAULT TRUE,
    landing_points_count  INTEGER,
    suppliers             TEXT,
    source_node_id        INTEGER REFERENCES nodes(node_id),
    destination_node_id   INTEGER REFERENCES nodes(node_id)
);
CREATE INDEX IF NOT EXISTS idx_cables_topology ON cables(source_node_id, destination_node_id);

CREATE TABLE IF NOT EXISTS cable_metadata (
    cable_id   VARCHAR(255) PRIMARY KEY REFERENCES cables(cable_id) ON DELETE CASCADE,
    public_url TEXT,
    source_url TEXT,
    notes      TEXT,
    is_planned BOOLEAN,
    raw_length TEXT,
    raw_rfs    TEXT
);

-- ── Pontos de aterrissagem ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS landing_points (
    endpoint_id   VARCHAR(255) PRIMARY KEY,
    endpoint_name VARCHAR(255) NOT NULL,
    city_name     VARCHAR(255),
    country_name  VARCHAR(120),
    country_code  VARCHAR(5),
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION,
    geo_source    VARCHAR(40)
);
CREATE INDEX IF NOT EXISTS idx_landing_points_country ON landing_points(country_code, country_name);

CREATE TABLE IF NOT EXISTS cable_landing_points (
    cable_id       VARCHAR(255) NOT NULL REFERENCES cables(cable_id) ON DELETE CASCADE,
    endpoint_id    VARCHAR(255) NOT NULL REFERENCES landing_points(endpoint_id) ON DELETE CASCADE,
    endpoint_order INTEGER,
    is_tbd         BOOLEAN,
    PRIMARY KEY (cable_id, endpoint_id)
);
CREATE INDEX IF NOT EXISTS idx_cable_landing_points_cable    ON cable_landing_points(cable_id, endpoint_order);
CREATE INDEX IF NOT EXISTS idx_cable_landing_points_endpoint ON cable_landing_points(endpoint_id);

-- ── GeoIP ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS geoip_mapping (
    id           SERIAL PRIMARY KEY,
    network      CIDR,
    ip_start     INET,
    ip_end       INET,
    latitude     DOUBLE PRECISION,
    longitude    DOUBLE PRECISION,
    country_code VARCHAR(5)
);
CREATE INDEX IF NOT EXISTS idx_geoip_network      ON geoip_mapping(network);
CREATE INDEX IF NOT EXISTS idx_geoip_country_code ON geoip_mapping(country_code);
CREATE INDEX IF NOT EXISTS idx_geoip_ip_range     ON geoip_mapping(ip_start, ip_end);

CREATE TABLE IF NOT EXISTS endpoint_ip_samples (
    endpoint_id  VARCHAR(255) NOT NULL REFERENCES landing_points(endpoint_id) ON DELETE CASCADE,
    sample_order INTEGER NOT NULL,
    network      CIDR,
    ip_start     INET,
    ip_end       INET,
    country_name VARCHAR(120),
    country_code VARCHAR(5),
    region       VARCHAR(120),
    city         VARCHAR(120),
    latitude     DOUBLE PRECISION,
    longitude    DOUBLE PRECISION,
    PRIMARY KEY (endpoint_id, sample_order)
);
CREATE INDEX IF NOT EXISTS idx_endpoint_ip_samples_endpoint ON endpoint_ip_samples(endpoint_id, sample_order);
CREATE INDEX IF NOT EXISTS idx_endpoint_ip_start            ON endpoint_ip_samples(endpoint_id, ip_start);

-- ── Telemetria (tabela única de medições) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS cable_telemetry_samples (
    sample_id               BIGSERIAL PRIMARY KEY,
    cable_id                VARCHAR(255) NOT NULL REFERENCES cables(cable_id) ON DELETE CASCADE,
    measured_at             TIMESTAMPTZ NOT NULL,
    sent_at                 TIMESTAMPTZ,
    received_at             TIMESTAMPTZ,
    latency_ms              DOUBLE PRECISION,
    jitter_ms               DOUBLE PRECISION,
    packet_loss_pct         DOUBLE PRECISION,
    throughput_mbps         DOUBLE PRECISION,
    packets_per_second      BIGINT,
    availability_pct        DOUBLE PRECISION,
    route_error_count       INTEGER,
    source                  VARCHAR(80)  NOT NULL DEFAULT 'synthetic',
    method                  VARCHAR(120) NOT NULL DEFAULT 'heuristic',
    confidence_score        DOUBLE PRECISION NOT NULL DEFAULT 0.55,
    estimated               BOOLEAN NOT NULL DEFAULT TRUE,
    telemetry_model_version VARCHAR(40)  NOT NULL DEFAULT '1.0.0',
    metadata                JSONB,
    UNIQUE (cable_id, measured_at)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_cable_time    ON cable_telemetry_samples(cable_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_time          ON cable_telemetry_samples(measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_source_method ON cable_telemetry_samples(source, method);

-- Hops de rota (MTR) por amostra
CREATE TABLE IF NOT EXISTS cable_telemetry_hops (
    hop_id       BIGSERIAL PRIMARY KEY,
    sample_id    BIGINT NOT NULL REFERENCES cable_telemetry_samples(sample_id) ON DELETE CASCADE,
    cable_id     VARCHAR(255) NOT NULL,
    hop_number   INTEGER NOT NULL,
    hop_ip       INET,
    rtt_last_ms  DOUBLE PRECISION,
    rtt_avg_ms   DOUBLE PRECISION,
    rtt_best_ms  DOUBLE PRECISION,
    rtt_worst_ms DOUBLE PRECISION,
    rtt_stdev_ms DOUBLE PRECISION,
    loss_pct     DOUBLE PRECISION,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (sample_id, hop_number)
);
CREATE INDEX IF NOT EXISTS idx_hops_sample    ON cable_telemetry_hops(sample_id);
CREATE INDEX IF NOT EXISTS idx_hops_cable_hop ON cable_telemetry_hops(cable_id, hop_number);
CREATE INDEX IF NOT EXISTS idx_hops_ip        ON cable_telemetry_hops(hop_ip);

-- Geolocalização de comutadores/roteadores descobertos via MTR
CREATE TABLE IF NOT EXISTS hop_switch_geolocations (
    id              BIGSERIAL PRIMARY KEY,
    hop_ip          INET        NOT NULL UNIQUE,
    lat             DOUBLE PRECISION,
    lng             DOUBLE PRECISION,
    city            VARCHAR(120),
    region          VARCHAR(120),
    country         VARCHAR(80),
    country_code    CHAR(2),
    isp             VARCHAR(200),
    org             VARCHAR(200),
    as_number       VARCHAR(30),
    as_name         VARCHAR(200),
    -- Enriquecimento nativo (rmifo_ip_enrich.py)
    hostname        VARCHAR(255),                   -- PTR DNS reverso
    timezone        VARCHAR(60),                    -- derivado do código IATA no PTR
    asn_route       CIDR,                           -- prefixo BGP do WHOIS (route:)
    asn_domain      VARCHAR(120),                   -- domínio do operador do ASN
    asn_type        VARCHAR(20),                    -- isp | hosting | business | edu | gov
    company_domain  VARCHAR(120),                   -- domínio da empresa (OrgURL / email)
    company_type    VARCHAR(20),                    -- isp | hosting | business | edu | gov
    abuse_email     VARCHAR(200),                   -- abuse-mailbox do WHOIS
    abuse_network   VARCHAR(50),                    -- inetnum / NetRange do WHOIS
    privacy_vpn     BOOLEAN NOT NULL DEFAULT false, -- porta VPN aberta (1194/51820/500)
    privacy_proxy   BOOLEAN NOT NULL DEFAULT false, -- porta proxy aberta (3128/1080) + CONNECT
    privacy_tor     BOOLEAN NOT NULL DEFAULT false, -- porta Tor aberta (9001/9030)
    privacy_relay   BOOLEAN NOT NULL DEFAULT false, -- padrão PTR de relay (iCloud/Cloudflare)
    privacy_hosting BOOLEAN NOT NULL DEFAULT false, -- ASN ou PTR de data center / CDN
    tls_san_count   INTEGER,                        -- número de SANs no certificado TLS
    tls_san_sample  TEXT[],                         -- primeiros 20 domínios do SAN
    enriched_at     TIMESTAMPTZ,                    -- última execução do enriquecimento
    queried_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_hop_geo_ip       ON hop_switch_geolocations(hop_ip);
CREATE INDEX IF NOT EXISTS idx_hop_geo_coords   ON hop_switch_geolocations(lat, lng) WHERE lat IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_hop_geo_asn      ON hop_switch_geolocations(as_number);
CREATE INDEX IF NOT EXISTS idx_hop_geo_type     ON hop_switch_geolocations(asn_type);
CREATE INDEX IF NOT EXISTS idx_hop_geo_hosting  ON hop_switch_geolocations(privacy_hosting) WHERE privacy_hosting = true;

-- Migration para instalações existentes (idempotente)
DO $$ BEGIN
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS hostname        VARCHAR(255);
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS timezone        VARCHAR(60);
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS asn_route       CIDR;
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS asn_domain      VARCHAR(120);
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS asn_type        VARCHAR(20);
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS company_domain  VARCHAR(120);
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS company_type    VARCHAR(20);
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS abuse_email     VARCHAR(200);
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS abuse_network   VARCHAR(50);
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS privacy_vpn     BOOLEAN NOT NULL DEFAULT false;
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS privacy_proxy   BOOLEAN NOT NULL DEFAULT false;
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS privacy_tor     BOOLEAN NOT NULL DEFAULT false;
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS privacy_relay   BOOLEAN NOT NULL DEFAULT false;
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS privacy_hosting BOOLEAN NOT NULL DEFAULT false;
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS tls_san_count   INTEGER;
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS tls_san_sample  TEXT[];
    ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS enriched_at     TIMESTAMPTZ;
END $$;

-- Baseline de referência por cabo (calibração 7 dias)
CREATE TABLE IF NOT EXISTS cable_telemetry_reference (
    cable_id                  VARCHAR(255) PRIMARY KEY REFERENCES cables(cable_id) ON DELETE CASCADE,
    baseline_latency_ms       DOUBLE PRECISION,
    baseline_jitter_ms        DOUBLE PRECISION,
    baseline_packet_loss_pct  DOUBLE PRECISION,
    baseline_throughput_mbps  DOUBLE PRECISION,
    baseline_availability_pct DOUBLE PRECISION,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes                     TEXT
);

-- ── Views de agregação (substituem as tabelas físicas de rollup) ───────────────
CREATE OR REPLACE VIEW telemetry_rollup_1h AS
SELECT
    cable_id,
    date_trunc('hour', measured_at)                               AS bucket,
    ROUND(AVG(latency_ms)::numeric,      3)                      AS avg_latency_ms,
    ROUND(AVG(jitter_ms)::numeric,       3)                      AS avg_jitter_ms,
    ROUND(MAX(latency_ms)::numeric,      3)                      AS max_latency_ms,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP
          (ORDER BY latency_ms)::numeric, 3)                      AS p95_latency_ms,
    ROUND(AVG(packet_loss_pct)::numeric, 3)                      AS avg_packet_loss_pct,
    ROUND(AVG(throughput_mbps)::numeric, 3)                      AS avg_throughput_mbps,
    ROUND(AVG(confidence_score)::numeric, 4)                     AS confidence_avg,
    COUNT(*)                                                      AS sample_count
FROM cable_telemetry_samples
GROUP BY cable_id, date_trunc('hour', measured_at);

CREATE OR REPLACE VIEW telemetry_rollup_5m AS
SELECT
    cable_id,
    date_trunc('minute', measured_at)
        - (EXTRACT(MINUTE FROM measured_at)::int % 5)
          * INTERVAL '1 minute'                                    AS bucket,
    ROUND(AVG(latency_ms)::numeric,      3)                      AS avg_latency_ms,
    ROUND(AVG(jitter_ms)::numeric,       3)                      AS avg_jitter_ms,
    ROUND(MAX(latency_ms)::numeric,      3)                      AS max_latency_ms,
    ROUND(AVG(packet_loss_pct)::numeric, 3)                      AS avg_packet_loss_pct,
    ROUND(AVG(throughput_mbps)::numeric, 3)                      AS avg_throughput_mbps,
    COUNT(*)                                                      AS sample_count
FROM cable_telemetry_samples
GROUP BY cable_id,
         date_trunc('minute', measured_at)
             - (EXTRACT(MINUTE FROM measured_at)::int % 5) * INTERVAL '1 minute';

-- ── Alertas operacionais ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS telemetry_alerts (
    alert_id       SERIAL PRIMARY KEY,
    cable_id       VARCHAR(255) NOT NULL REFERENCES cables(cable_id) ON DELETE CASCADE,
    metric         VARCHAR(50) NOT NULL,
    severity       VARCHAR(20) NOT NULL,
    current_value  DOUBLE PRECISION,
    baseline_value DOUBLE PRECISION,
    detected_at    TIMESTAMPTZ DEFAULT NOW(),
    resolved_at    TIMESTAMPTZ,
    description    TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_active       ON telemetry_alerts(detected_at) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_alerts_cable_active ON telemetry_alerts(cable_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_alerts_severity     ON telemetry_alerts(severity, detected_at DESC) WHERE resolved_at IS NULL;

-- ── Rate limiting ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_rate_limit (
    ip_hash      CHAR(64)    NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    req_count    INTEGER     NOT NULL DEFAULT 1,
    PRIMARY KEY (ip_hash, window_start)
);
CREATE INDEX IF NOT EXISTS idx_rate_limit_window ON api_rate_limit(window_start);
