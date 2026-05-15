-- Script voltado para PostgreSQL.
-- Observação: em PostgreSQL, a criação de banco e a troca de conexão
-- normalmente são executadas no cliente (ex.: psql), fora de transações.
-- Exemplo:
--   CREATE DATABASE global_network_resilience;
--   \c global_network_resilience

--EXEMPLO DE DADOS PARA AS TABELAS
-- Modelo de Dados RMIFO
-- Gerado a partir das definições de ingestão de dados e metadados de cabos submarinos.

-- Tabela de Nós (Topologia CAIDA / PoPs)
CREATE TABLE IF NOT EXISTS nodes (
    node_id SERIAL PRIMARY KEY,
    node_name VARCHAR(255),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    country_code VARCHAR(5),
    asn INTEGER
);

-- Tabela de Cabos Submarinos
CREATE TABLE IF NOT EXISTS cables (
    cable_id VARCHAR(255) PRIMARY KEY, -- Slug identificador (ex: americas-i-north)
    cable_name VARCHAR(255) NOT NULL,
    owners TEXT,                       -- Lista de proprietários (ex: AT&T, América Móvil)
    capacity_gbps DOUBLE PRECISION,     -- Capacidade de design em Gbps
    length_km INTEGER,                 -- Comprimento convertido para inteiro
    rfs_year INTEGER,                  -- Ano de entrada em operação (Ready For Service)
    is_operational BOOLEAN DEFAULT TRUE,
    landing_points_count INTEGER,      -- Quantidade de pontos de ancoragem
    suppliers TEXT,                    -- Fornecedores (ex: SubCom, ASN)
    source_node_id INTEGER REFERENCES nodes(node_id),      -- FK para topologia lógica
    destination_node_id INTEGER REFERENCES nodes(node_id)  -- FK para topologia lógica
);

-- Tabela de Geolocalização IP (GeoIP)
CREATE TABLE IF NOT EXISTS geoip_mapping (
    id SERIAL PRIMARY KEY,
    network CIDR,                      -- Suporte nativo a blocos CIDR (ex: 1.1.1.0/24)
    ip_start INET,                     -- Suporte nativo a IPv4/IPv6 para ranges
    ip_end INET,                       -- Suporte nativo a IPv4/IPv6 para ranges
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    country_code VARCHAR(5)
);

-- Índices para otimização de busca geográfica e de conectividade
CREATE INDEX IF NOT EXISTS idx_nodes_geo ON nodes(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_cables_topology ON cables(source_node_id, destination_node_id);
-- Índice GIST é ideal para buscas de inclusão de rede/IP em PostgreSQL
CREATE INDEX IF NOT EXISTS idx_geoip_network ON geoip_mapping USING GIST (network inet_ops);

-- Índices auxiliares para filtros comuns
CREATE INDEX IF NOT EXISTS idx_nodes_country_code ON nodes(country_code);
CREATE INDEX IF NOT EXISTS idx_geoip_country_code ON geoip_mapping(country_code);
CREATE INDEX IF NOT EXISTS idx_geoip_ip_range ON geoip_mapping(ip_start, ip_end);

-- Metadados extras de cabos para consultas detalhadas na aplicação web.
CREATE TABLE IF NOT EXISTS cable_metadata (
    cable_id VARCHAR(255) PRIMARY KEY REFERENCES cables(cable_id) ON DELETE CASCADE,
    public_url TEXT,
    source_url TEXT,
    notes TEXT,
    is_planned BOOLEAN,
    raw_length TEXT,
    raw_rfs TEXT
);

-- Cadastro de pontos de aterrissagem (endpoints) dos cabos.
CREATE TABLE IF NOT EXISTS landing_points (
    endpoint_id VARCHAR(255) PRIMARY KEY,
    endpoint_name VARCHAR(255) NOT NULL,
    city_name VARCHAR(255),
    country_name VARCHAR(120),
    country_code VARCHAR(5),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    geo_source VARCHAR(40)
);

-- Relacao N:N entre cabos e endpoints, preservando a ordem original.
CREATE TABLE IF NOT EXISTS cable_landing_points (
    cable_id VARCHAR(255) NOT NULL REFERENCES cables(cable_id) ON DELETE CASCADE,
    endpoint_id VARCHAR(255) NOT NULL REFERENCES landing_points(endpoint_id) ON DELETE CASCADE,
    endpoint_order INTEGER,
    is_tbd BOOLEAN,
    PRIMARY KEY (cable_id, endpoint_id)
);

-- Amostras de IPs publicos por endpoint, derivadas do dataset GeoIP.
CREATE TABLE IF NOT EXISTS endpoint_ip_samples (
    endpoint_id VARCHAR(255) NOT NULL REFERENCES landing_points(endpoint_id) ON DELETE CASCADE,
    sample_order INTEGER NOT NULL,
    network CIDR,
    ip_start INET,
    ip_end INET,
    country_name VARCHAR(120),
    country_code VARCHAR(5),
    region VARCHAR(120),
    city VARCHAR(120),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    PRIMARY KEY (endpoint_id, sample_order)
);

CREATE INDEX IF NOT EXISTS idx_cable_landing_points_cable ON cable_landing_points(cable_id, endpoint_order);
CREATE INDEX IF NOT EXISTS idx_cable_landing_points_endpoint ON cable_landing_points(endpoint_id);
CREATE INDEX IF NOT EXISTS idx_landing_points_country ON landing_points(country_code, country_name);
CREATE INDEX IF NOT EXISTS idx_endpoint_ip_samples_endpoint ON endpoint_ip_samples(endpoint_id, sample_order);

-- Telemetria histórica por cabo para análise temporal e pesquisa.
CREATE TABLE IF NOT EXISTS cable_telemetry_samples (
    sample_id BIGSERIAL PRIMARY KEY,
    cable_id VARCHAR(255) NOT NULL REFERENCES cables(cable_id) ON DELETE CASCADE,
    measured_at TIMESTAMPTZ NOT NULL,
    latency_ms DOUBLE PRECISION,
    jitter_ms DOUBLE PRECISION,
    packet_loss_pct DOUBLE PRECISION,
    throughput_mbps DOUBLE PRECISION,
    packets_per_second BIGINT,
    availability_pct DOUBLE PRECISION,
    route_error_count INTEGER,
    source VARCHAR(80) NOT NULL DEFAULT 'synthetic',
    method VARCHAR(120) NOT NULL DEFAULT 'heuristic',
    confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.55,
    estimated BOOLEAN NOT NULL DEFAULT TRUE,
    telemetry_model_version VARCHAR(40) NOT NULL DEFAULT '1.0.0',
    metadata JSONB,
    UNIQUE (cable_id, measured_at)
);

-- Tabelas de Rollup para Telemetria (Agregação para performance)
CREATE TABLE IF NOT EXISTS telemetry_rollup_1h (
    cable_id VARCHAR(255) NOT NULL REFERENCES cables(cable_id) ON DELETE CASCADE,
    bucket TIMESTAMPTZ NOT NULL,
    avg_latency_ms DOUBLE PRECISION,
    avg_jitter_ms DOUBLE PRECISION,
    max_latency_ms DOUBLE PRECISION,
    p95_latency_ms DOUBLE PRECISION,
    avg_packet_loss_pct DOUBLE PRECISION,
    avg_throughput_mbps DOUBLE PRECISION,
    sample_count INTEGER,
    confidence_avg DOUBLE PRECISION,
    PRIMARY KEY (cable_id, bucket)
);

CREATE TABLE IF NOT EXISTS telemetry_rollup_5m (
    cable_id VARCHAR(255) NOT NULL REFERENCES cables(cable_id) ON DELETE CASCADE,
    bucket TIMESTAMPTZ NOT NULL,
    avg_latency_ms DOUBLE PRECISION,
    avg_jitter_ms DOUBLE PRECISION,
    max_latency_ms DOUBLE PRECISION,
    avg_packet_loss_pct DOUBLE PRECISION,
    avg_throughput_mbps DOUBLE PRECISION,
    sample_count INTEGER,
    PRIMARY KEY (cable_id, bucket)
);

-- Índices para buscas temporais rápidas
CREATE INDEX IF NOT EXISTS idx_rollup_1h_time ON telemetry_rollup_1h(bucket DESC);
CREATE INDEX IF NOT EXISTS idx_rollup_5m_time ON telemetry_rollup_5m(bucket DESC);

-- Tabela de Alertas Operacionais
CREATE TABLE IF NOT EXISTS telemetry_alerts (
    alert_id      SERIAL PRIMARY KEY,
    cable_id      VARCHAR(255) NOT NULL REFERENCES cables(cable_id) ON DELETE CASCADE,
    metric        VARCHAR(50) NOT NULL, -- latency, packet_loss, jitter
    severity      VARCHAR(20) NOT NULL, -- warning, critical
    current_value DOUBLE PRECISION,
    baseline_value DOUBLE PRECISION,
    detected_at   TIMESTAMPTZ DEFAULT NOW(),
    resolved_at   TIMESTAMPTZ,
    description   TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON telemetry_alerts(detected_at) WHERE resolved_at IS NULL;

-- Faixas de referência por cabo para calibração e avaliação automática.
CREATE TABLE IF NOT EXISTS cable_telemetry_reference (
    cable_id VARCHAR(255) PRIMARY KEY REFERENCES cables(cable_id) ON DELETE CASCADE,
    baseline_latency_ms DOUBLE PRECISION,
    baseline_jitter_ms DOUBLE PRECISION,
    baseline_packet_loss_pct DOUBLE PRECISION,
    baseline_throughput_mbps DOUBLE PRECISION,
    baseline_availability_pct DOUBLE PRECISION,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_telemetry_cable_time ON cable_telemetry_samples(cable_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_time ON cable_telemetry_samples(measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_source_method ON cable_telemetry_samples(source, method);

-- Índice parcial para alertas ativos (resolved_at IS NULL é o caso dominante de consulta)
CREATE INDEX IF NOT EXISTS idx_alerts_cable_active ON telemetry_alerts(cable_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON telemetry_alerts(severity, detected_at DESC) WHERE resolved_at IS NULL;

-- Rollup 5m: índice com cable_id para leitura por janela de tempo
CREATE INDEX IF NOT EXISTS idx_rollup_5m_cable_time ON telemetry_rollup_5m(cable_id, bucket DESC);
-- Rollup 1h: idem
CREATE INDEX IF NOT EXISTS idx_rollup_1h_cable_time ON telemetry_rollup_1h(cable_id, bucket DESC);

-- Índice para busca de IPs por endpoint (utilizado pela sonda de rede)
CREATE INDEX IF NOT EXISTS idx_endpoint_ip_start ON endpoint_ip_samples(endpoint_id, ip_start);

-- Consultas de validação rápida (somente leitura)
-- 1) Quantidade de registros por tabela
-- SELECT 'nodes' AS tabela, COUNT(*) AS total FROM nodes
-- UNION ALL
-- SELECT 'cables', COUNT(*) FROM cables
-- UNION ALL
-- SELECT 'geoip_mapping', COUNT(*) FROM geoip_mapping;

-- 2) Cabos sem mapeamento completo de nós
-- SELECT cable_id, cable_name
-- FROM cables
-- WHERE source_node_id IS NULL OR destination_node_id IS NULL;

-- 3) Distribuição de nós por país
-- SELECT country_code, COUNT(*) AS total
-- FROM nodes
-- GROUP BY country_code
-- ORDER BY total DESC;