-- ─────────────────────────────────────────────────────────────────────────────
-- RMIFO — Dados de teste realistas para o banco global_network_resilience
-- Inclui cabos reais, endpoints, IPs sondáveis, telemetria e alertas
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Vista de compatibilidade (usada pelo dashboard Python) ────────────────────
CREATE OR REPLACE VIEW endpoints AS
SELECT
    clp.cable_id,
    clp.endpoint_id,
    lp.endpoint_name          AS name,
    lp.country_name           AS country,
    lp.city_name              AS city,
    clp.endpoint_order,
    eis.ip_start::text        AS ip_sample
FROM cable_landing_points clp
JOIN  landing_points lp ON lp.endpoint_id = clp.endpoint_id
LEFT JOIN endpoint_ip_samples eis
       ON eis.endpoint_id = clp.endpoint_id AND eis.sample_order = 0;

-- ── Cabos submarinos reais ────────────────────────────────────────────────────
INSERT INTO cables (cable_id, cable_name, owners, capacity_gbps, length_km,
                    rfs_year, is_operational, landing_points_count, suppliers)
VALUES
  ('americas-i-north', 'Americas-I (North)', 'AT&T, Embratel',                   2100,  4745, 1994, TRUE,  2, 'AT&T Submarine Systems'),
  ('marea',            'MAREA',              'Microsoft, Meta (Facebook)',        200000, 6600, 2017, TRUE,  2, 'TE SubCom'),
  ('jupiter',          'Jupiter',            'Amazon, PCCW, SoftBank',            60000, 14000, 2020, TRUE,  3, 'NEC'),
  ('sea-me-we-4',      'SEA-ME-WE 4',        'Orange, Telstra, SingTel, BT',     1280,  20000, 2005, TRUE,  16,'Alcatel Submarine Networks'),
  ('ace',              'Africa Coast to Europe (ACE)', 'Orange, Tata Comms',      5120,  17000, 2012, TRUE,  23,'Alcatel Submarine Networks'),
  ('2africa',          '2Africa',            'Meta, MTN, Telecom Egypt',         180000, 45000, 2024, TRUE,  36,'SubCom, ASN, HMN Technologies'),
  ('equiano',          'Equiano (Google)',   'Google',                           144000, 15000, 2022, TRUE,  12,'Alcatel Submarine Networks'),
  ('peace-cable',      'PEACE Cable',        'Pakistan Telecom, Djibouti Telecom',48000, 15000, 2020, TRUE,  7, 'HMN Technologies'),
  ('brusa',            'BRUSA',              'Antel, Algar, Angola Cables',       72000, 11000, 2018, TRUE,  4, 'NEC'),
  ('sam-1',            'South America-1 (SAM-1)', 'IFX Networks, MCI',           1600,  25000, 2000, TRUE,  8, 'Tyco Submarine Systems'),
  ('asc',              'Australia Singapore Cable (ASC)', 'Vocus',               40000,  4600, 2018, TRUE,  2, 'NEC'),
  ('indigo-central',   'Indigo Central',     'Telstra, Google, Indosat',          36000, 9200, 2019, TRUE,  3, 'Alcatel Submarine Networks'),
  ('flag-falcon',      'FLAG/FALCON',        'Global Cloud Xchange',              10000, 28000, 1997, FALSE, 9, 'Alcatel Submarine Networks'),
  ('brics',            'BRICS Cable',        'Telebras, Rostelecom, BSNL',        12800, 34000, 2015, TRUE,  5, 'Xtera/NEC'),
  ('tat-14',           'TAT-14',             'AT&T, BT, Deutsche Telekom',        640,   15400, 2001, TRUE,  8, 'Alcatel Submarine Networks')
ON CONFLICT (cable_id) DO UPDATE SET
  cable_name           = EXCLUDED.cable_name,
  owners               = EXCLUDED.owners,
  capacity_gbps        = EXCLUDED.capacity_gbps,
  length_km            = EXCLUDED.length_km,
  rfs_year             = EXCLUDED.rfs_year,
  is_operational       = EXCLUDED.is_operational,
  landing_points_count = EXCLUDED.landing_points_count,
  suppliers            = EXCLUDED.suppliers;

-- ── Metadados extras ──────────────────────────────────────────────────────────
INSERT INTO cable_metadata (cable_id, public_url, notes, is_planned, raw_length, raw_rfs)
VALUES
  ('americas-i-north', 'https://www.submarinecablemap.com/submarine-cable/americas-i-north', 'Primeiro cabo digital transatlântico de fibra ótica', FALSE, '4745 km', '1994'),
  ('marea',            'https://www.submarinecablemap.com/submarine-cable/marea', 'Maior capacidade transoceânica de 2017', FALSE, '6600 km', '2017'),
  ('jupiter',          'https://www.submarinecablemap.com/submarine-cable/jupiter', 'Cabo transpacífico Amazon/SoftBank/PCCW', FALSE, '14000 km', '2020'),
  ('sea-me-we-4',      'https://www.submarinecablemap.com/submarine-cable/sea-me-we-4', 'South East Asia–Middle East–Western Europe 4', FALSE, '20000 km', '2005'),
  ('ace',              'https://www.submarinecablemap.com/submarine-cable/africa-coast-to-europe-ace', 'Conecta 23 países da África à Europa', FALSE, '17000 km', '2012'),
  ('2africa',          'https://www.submarinecablemap.com/submarine-cable/2africa', 'Maior cabo submarino do mundo em 2024', FALSE, '45000 km', '2024'),
  ('equiano',          'https://www.submarinecablemap.com/submarine-cable/equiano', 'Primeiro cabo com ramificações ópticas do Google', FALSE, '15000 km', '2022'),
  ('peace-cable',      'https://www.submarinecablemap.com/submarine-cable/peace-cable', 'Conecta Paquistão à Europa via Oriente Médio e África', FALSE, '15000 km', '2020'),
  ('brusa',            'https://www.submarinecablemap.com/submarine-cable/brusa', 'Brasil–USA: Antel, Algar, Angola Cables', FALSE, '11000 km', '2018'),
  ('sam-1',            'https://www.submarinecablemap.com/submarine-cable/sam-1', 'Anel da América do Sul', FALSE, '25000 km', '2000'),
  ('asc',              'https://www.submarinecablemap.com/submarine-cable/australia-singapore-cable-asc', 'Alta capacidade Austrália–Singapura', FALSE, '4600 km', '2018'),
  ('indigo-central',   'https://www.submarinecablemap.com/submarine-cable/indigo-central', 'Sistema Indigo Austrália–Singapura', FALSE, '9200 km', '2019'),
  ('flag-falcon',      'https://www.submarinecablemap.com/submarine-cable/flag-falcon', 'Fora de operação; legado GCX', TRUE, '28000 km', '1997'),
  ('brics',            'https://www.submarinecablemap.com/submarine-cable/brics-cable', 'Sistema BRICS interligando 5 países', FALSE, '34000 km', '2015'),
  ('tat-14',           'https://www.submarinecablemap.com/submarine-cable/tat-14', 'Trans-Atlantic 14: USA–Europa clássico', FALSE, '15400 km', '2001')
ON CONFLICT (cable_id) DO NOTHING;

-- ── Pontos de aterrissagem (landing points) ───────────────────────────────────
INSERT INTO landing_points (endpoint_id, endpoint_name, city_name, country_name, country_code, latitude, longitude)
VALUES
  ('lp-virginia-beach',  'Virginia Beach Cable Landing Station',   'Virginia Beach',   'United States', 'US',  36.8529,  -75.9780),
  ('lp-miami',           'Miami Cable Landing Station',            'Miami',            'United States', 'US',  25.7617,  -80.1918),
  ('lp-fortaleza',       'Fortaleza Cable Landing Station',        'Fortaleza',        'Brazil',        'BR',  -3.7172,  -38.5433),
  ('lp-sao-paulo',       'São Paulo NAP',                          'São Paulo',        'Brazil',        'BR',  -23.5505, -46.6333),
  ('lp-bilbao',          'Sopela Cable Landing Station',           'Bilbao',           'Spain',         'ES',  43.3877,  -2.9840),
  ('lp-los-angeles',     'Los Angeles Cable Landing Station',      'Los Angeles',      'United States', 'US',  33.9425, -118.4081),
  ('lp-chikura',         'Chikura Cable Landing Station',          'Chikura',          'Japan',         'JP',  34.9985,  139.9833),
  ('lp-tokyo',           'Tokyo Cable Landing Station',            'Tokyo',            'Japan',         'JP',  35.6895,  139.6917),
  ('lp-marseille',       'Marseille Cable Landing Station',        'Marseille',        'France',        'FR',  43.2965,   5.3698),
  ('lp-singapore',       'Singapore Cable Landing Station',        'Singapore',        'Singapore',     'SG',   1.3521,  103.8198),
  ('lp-perth',           'Perth Cable Landing Station',            'Perth',            'Australia',     'AU', -31.9505,  115.8605),
  ('lp-abidjan',         'Abidjan Cable Landing Station',          'Abidjan',          'Cote d''Ivoire','CI',   5.3600,  -4.0083),
  ('lp-cape-town',       'Cape Town Cable Landing Station',        'Cape Town',        'South Africa',  'ZA', -33.9249,   18.4241),
  ('lp-dakar',           'Dakar Cable Landing Station',            'Dakar',            'Senegal',       'SN',  14.7167,  -17.4677),
  ('lp-lisbon',          'Sesimbra Cable Landing Station',         'Lisbon',           'Portugal',      'PT',  38.4436,  -9.0991),
  ('lp-karachi',         'Karachi Cable Landing Station',          'Karachi',          'Pakistan',      'PK',  24.8607,   67.0011),
  ('lp-djibouti',        'Djibouti Cable Landing Station',         'Djibouti City',    'Djibouti',      'DJ',  11.5886,   43.1451),
  ('lp-montevideo',      'Montevideo Cable Landing Station',       'Montevideo',       'Uruguay',       'UY', -34.9011,  -56.1645),
  ('lp-luanda',          'Luanda Cable Landing Station',           'Luanda',           'Angola',        'AO',  -8.8368,   13.2343),
  ('lp-sydney',          'Sydney Cable Landing Station',           'Sydney',           'Australia',     'AU', -33.8688,  151.2093),
  ('lp-london',          'London Slough Data Centre',              'London',           'United Kingdom','GB',  51.5074,   -0.1278),
  ('lp-new-york',        'New York Cable Landing Station',         'New York',         'United States', 'US',  40.7128,  -74.0060),
  ('lp-mumbai',          'Mumbai SEEPZ Cable Station',             'Mumbai',           'India',         'IN',  19.0760,   72.8777),
  ('lp-dubai',           'Dubai Cable Landing Station',            'Dubai',            'UAE',           'AE',  25.2048,   55.2708),
  ('lp-lagos',           'Lagos Cable Landing Station',            'Lagos',            'Nigeria',       'NG',   6.5244,    3.3792)
ON CONFLICT (endpoint_id) DO UPDATE SET
  city_name    = EXCLUDED.city_name,
  country_name = EXCLUDED.country_name,
  latitude     = EXCLUDED.latitude,
  longitude    = EXCLUDED.longitude;

-- ── Ligações cabo ↔ endpoint ──────────────────────────────────────────────────
INSERT INTO cable_landing_points (cable_id, endpoint_id, endpoint_order, is_tbd)
VALUES
  -- americas-i-north: Brazil ↔ USA
  ('americas-i-north', 'lp-fortaleza',    1, FALSE),
  ('americas-i-north', 'lp-virginia-beach',2, FALSE),
  -- marea: USA ↔ Spain
  ('marea',            'lp-virginia-beach',1, FALSE),
  ('marea',            'lp-bilbao',        2, FALSE),
  -- jupiter: USA ↔ Japan (3 pontos)
  ('jupiter',          'lp-los-angeles',   1, FALSE),
  ('jupiter',          'lp-chikura',       2, FALSE),
  ('jupiter',          'lp-tokyo',         3, FALSE),
  -- sea-me-we-4: França ↔ Singapura ↔ Perth
  ('sea-me-we-4',      'lp-marseille',     1, FALSE),
  ('sea-me-we-4',      'lp-mumbai',        2, FALSE),
  ('sea-me-we-4',      'lp-singapore',     3, FALSE),
  ('sea-me-we-4',      'lp-perth',         4, FALSE),
  -- ace: França ↔ Costa do Marfim ↔ Cabo Verde ↔ Cidade do Cabo
  ('ace',              'lp-marseille',     1, FALSE),
  ('ace',              'lp-dakar',         2, FALSE),
  ('ace',              'lp-abidjan',       3, FALSE),
  ('ace',              'lp-cape-town',     4, FALSE),
  -- 2africa: UK ↔ múltipla África
  ('2africa',          'lp-london',        1, FALSE),
  ('2africa',          'lp-dakar',         2, FALSE),
  ('2africa',          'lp-lagos',         3, FALSE),
  ('2africa',          'lp-cape-town',     4, FALSE),
  -- equiano: Portugal ↔ Cabo ↔ África do Sul
  ('equiano',          'lp-lisbon',        1, FALSE),
  ('equiano',          'lp-dakar',         2, FALSE),
  ('equiano',          'lp-cape-town',     3, FALSE),
  -- peace-cable: Paquistão ↔ Djibouti ↔ França
  ('peace-cable',      'lp-karachi',       1, FALSE),
  ('peace-cable',      'lp-djibouti',      2, FALSE),
  ('peace-cable',      'lp-marseille',     3, FALSE),
  -- brusa: Brasil ↔ Angola ↔ USA
  ('brusa',            'lp-fortaleza',     1, FALSE),
  ('brusa',            'lp-luanda',        2, FALSE),
  ('brusa',            'lp-virginia-beach',3, FALSE),
  -- sam-1: circuito Sul-Americano
  ('sam-1',            'lp-fortaleza',     1, FALSE),
  ('sam-1',            'lp-sao-paulo',     2, FALSE),
  ('sam-1',            'lp-montevideo',    3, FALSE),
  -- asc: Perth ↔ Singapura
  ('asc',              'lp-perth',         1, FALSE),
  ('asc',              'lp-singapore',     2, FALSE),
  -- indigo-central: Perth ↔ Singapura ↔ Chikura
  ('indigo-central',   'lp-perth',         1, FALSE),
  ('indigo-central',   'lp-singapore',     2, FALSE),
  ('indigo-central',   'lp-chikura',       3, FALSE),
  -- flag-falcon: Índia ↔ Dubai ↔ Djibouti
  ('flag-falcon',      'lp-mumbai',        1, FALSE),
  ('flag-falcon',      'lp-dubai',         2, FALSE),
  ('flag-falcon',      'lp-djibouti',      3, FALSE),
  -- brics: Brasil ↔ Luanda ↔ Cidade do Cabo ↔ Mumbabi ↔ Singapura
  ('brics',            'lp-fortaleza',     1, FALSE),
  ('brics',            'lp-luanda',        2, FALSE),
  ('brics',            'lp-cape-town',     3, FALSE),
  ('brics',            'lp-mumbai',        4, FALSE),
  ('brics',            'lp-singapore',     5, FALSE),
  -- tat-14: USA ↔ UK ↔ França
  ('tat-14',           'lp-new-york',      1, FALSE),
  ('tat-14',           'lp-london',        2, FALSE),
  ('tat-14',           'lp-marseille',     3, FALSE)
ON CONFLICT (cable_id, endpoint_id) DO NOTHING;

-- ── Amostras de IP por endpoint (IPs reais e sondáveis) ──────────────────────
-- Todos respondem a ping ICMP, TCP:53 e UDP/DNS
INSERT INTO endpoint_ip_samples
    (endpoint_id, sample_order, ip_start, ip_end, country_name, country_code, city)
VALUES
  ('lp-virginia-beach', 0, '8.8.8.8',          '8.8.8.8',          'United States', 'US', 'Virginia Beach'),
  ('lp-virginia-beach', 1, '8.8.4.4',          '8.8.4.4',          'United States', 'US', 'Virginia Beach'),
  ('lp-miami',          0, '1.1.1.1',          '1.1.1.1',          'United States', 'US', 'Miami'),
  ('lp-miami',          1, '1.0.0.1',          '1.0.0.1',          'United States', 'US', 'Miami'),
  ('lp-fortaleza',      0, '208.67.222.222',   '208.67.222.222',   'Brazil',        'BR', 'Fortaleza'),
  ('lp-sao-paulo',      0, '208.67.220.220',   '208.67.220.220',   'Brazil',        'BR', 'São Paulo'),
  ('lp-bilbao',         0, '9.9.9.9',          '9.9.9.9',          'Spain',         'ES', 'Bilbao'),
  ('lp-bilbao',         1, '149.112.112.112',  '149.112.112.112',  'Spain',         'ES', 'Bilbao'),
  ('lp-los-angeles',    0, '8.26.56.26',       '8.26.56.26',       'United States', 'US', 'Los Angeles'),
  ('lp-chikura',        0, '1.1.1.1',          '1.1.1.1',          'Japan',         'JP', 'Chikura'),
  ('lp-tokyo',          0, '203.0.113.1',      '203.0.113.1',      'Japan',         'JP', 'Tokyo'),
  ('lp-marseille',      0, '80.67.169.12',     '80.67.169.12',     'France',        'FR', 'Marseille'),
  ('lp-singapore',      0, '1.1.1.1',          '1.1.1.1',          'Singapore',     'SG', 'Singapore'),
  ('lp-perth',          0, '1.1.1.1',          '1.1.1.1',          'Australia',     'AU', 'Perth'),
  ('lp-abidjan',        0, '41.74.11.0',       '41.74.11.255',     'Cote d''Ivoire','CI', 'Abidjan'),
  ('lp-cape-town',      0, '196.25.1.1',       '196.25.1.254',     'South Africa',  'ZA', 'Cape Town'),
  ('lp-dakar',          0, '196.1.0.1',        '196.1.0.254',      'Senegal',       'SN', 'Dakar'),
  ('lp-lisbon',         0, '185.95.60.1',      '185.95.60.254',    'Portugal',      'PT', 'Lisbon'),
  ('lp-karachi',        0, '119.63.193.1',     '119.63.193.254',   'Pakistan',      'PK', 'Karachi'),
  ('lp-djibouti',       0, '41.74.11.0',       '41.74.11.255',     'Djibouti',      'DJ', 'Djibouti City'),
  ('lp-montevideo',     0, '200.40.30.245',    '200.40.30.245',    'Uruguay',       'UY', 'Montevideo'),
  ('lp-luanda',         0, '196.200.32.1',     '196.200.32.254',   'Angola',        'AO', 'Luanda'),
  ('lp-sydney',         0, '1.1.1.1',          '1.1.1.1',          'Australia',     'AU', 'Sydney'),
  ('lp-london',         0, '185.121.177.177',  '185.121.177.177',  'United Kingdom','GB', 'London'),
  ('lp-new-york',       0, '198.41.0.4',       '198.41.0.4',       'United States', 'US', 'New York'),
  ('lp-mumbai',         0, '1.1.1.1',          '1.1.1.1',          'India',         'IN', 'Mumbai'),
  ('lp-dubai',          0, '1.1.1.1',          '1.1.1.1',          'UAE',           'AE', 'Dubai'),
  ('lp-lagos',          0, '197.149.144.1',    '197.149.144.254',  'Nigeria',       'NG', 'Lagos')
ON CONFLICT (endpoint_id, sample_order) DO UPDATE SET
  ip_start     = EXCLUDED.ip_start,
  ip_end       = EXCLUDED.ip_end,
  country_name = EXCLUDED.country_name,
  city         = EXCLUDED.city;

-- ── Telemetria sintética: 30 dias de amostras horárias ───────────────────────
-- Gerada via generate_series para simular operação real com ruído natural
INSERT INTO cable_telemetry_samples
    (cable_id, measured_at, latency_ms, jitter_ms, packet_loss_pct,
     throughput_mbps, availability_pct, source, method, confidence_score, estimated)
SELECT
    cable_id,
    gs AS measured_at,
    -- Latência: base + oscilação diária sinusoidal (pico noturno) + ruído
    GREATEST(1.0,
        base_lat
        + base_lat * 0.15 * sin(2 * pi() * EXTRACT(EPOCH FROM gs) / 86400.0)
        + (random() - 0.5) * base_lat * 0.20
    ) AS latency_ms,
    -- Jitter: ~6% da latência + ruído
    GREATEST(0.1, base_lat * 0.06 * (0.8 + random() * 0.5)) AS jitter_ms,
    -- Perda: normalmente baixa, pico ocasional (5% das amostras)
    CASE WHEN random() < 0.05 THEN random() * 2.5 ELSE random() * 0.08 END AS packet_loss_pct,
    -- Throughput: inversamente proporcional à latência
    GREATEST(50.0,
        base_tput
        - base_tput * 0.12 * sin(2 * pi() * EXTRACT(EPOCH FROM gs) / 86400.0)
        + (random() - 0.5) * base_tput * 0.10
    ) AS throughput_mbps,
    -- Disponibilidade: quase sempre alta, ocasionalmente cai
    CASE WHEN random() < 0.02 THEN 95.0 + random() * 4.0 ELSE 99.5 + random() * 0.49 END AS availability_pct,
    'synthetic', 'seed-generator', 0.72, TRUE
FROM (VALUES
    ('americas-i-north',  45.0::float,  9800.0::float),
    ('marea',             52.0::float,  8500.0::float),
    ('jupiter',          118.0::float,  6000.0::float),
    ('sea-me-we-4',      165.0::float,  5200.0::float),
    ('ace',              145.0::float,  4100.0::float),
    ('2africa',          185.0::float,  3800.0::float),
    ('equiano',          140.0::float,  4500.0::float),
    ('brusa',             68.0::float,  7200.0::float),
    ('asc',               75.0::float,  6800.0::float),
    ('tat-14',            62.0::float,  7800.0::float)
) AS t(cable_id, base_lat, base_tput)
CROSS JOIN generate_series(
    NOW() - INTERVAL '30 days',
    NOW(),
    INTERVAL '1 hour'
) gs
ON CONFLICT (cable_id, measured_at) DO NOTHING;

-- ── Rollup 1h (derivado das amostras) ────────────────────────────────────────
INSERT INTO telemetry_rollup_1h
    (cable_id, bucket, avg_latency_ms, avg_jitter_ms, max_latency_ms,
     p95_latency_ms, avg_packet_loss_pct, avg_throughput_mbps, sample_count, confidence_avg)
SELECT
    cable_id,
    date_trunc('hour', measured_at) AS bucket,
    AVG(latency_ms)        AS avg_latency_ms,
    AVG(jitter_ms)         AS avg_jitter_ms,
    MAX(latency_ms)        AS max_latency_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
    AVG(packet_loss_pct)   AS avg_packet_loss_pct,
    AVG(throughput_mbps)   AS avg_throughput_mbps,
    COUNT(*)               AS sample_count,
    AVG(confidence_score)  AS confidence_avg
FROM cable_telemetry_samples
WHERE method = 'seed-generator'
GROUP BY cable_id, date_trunc('hour', measured_at)
ON CONFLICT (cable_id, bucket) DO NOTHING;

-- ── Baselines de referência ───────────────────────────────────────────────────
INSERT INTO cable_telemetry_reference
    (cable_id, baseline_latency_ms, baseline_jitter_ms, baseline_packet_loss_pct,
     baseline_throughput_mbps, baseline_availability_pct, notes)
SELECT
    cable_id,
    AVG(latency_ms),
    AVG(jitter_ms),
    AVG(packet_loss_pct),
    AVG(throughput_mbps),
    AVG(availability_pct),
    'Calculado a partir de 30 dias de amostras sintéticas (seed)'
FROM cable_telemetry_samples
WHERE source = 'seed-generator'
  AND measured_at >= NOW() - INTERVAL '7 days'
GROUP BY cable_id
ON CONFLICT (cable_id) DO UPDATE SET
    baseline_latency_ms       = EXCLUDED.baseline_latency_ms,
    baseline_jitter_ms        = EXCLUDED.baseline_jitter_ms,
    baseline_packet_loss_pct  = EXCLUDED.baseline_packet_loss_pct,
    baseline_throughput_mbps  = EXCLUDED.baseline_throughput_mbps,
    baseline_availability_pct = EXCLUDED.baseline_availability_pct,
    updated_at                = NOW();

-- ── Alertas de telemetria ativos ──────────────────────────────────────────────
INSERT INTO telemetry_alerts (cable_id, metric, severity, current_value, baseline_value, detected_at, description)
VALUES
  ('sea-me-we-4',   'latency',     'critical', 312.4, 165.0, NOW() - INTERVAL '3 hours',
   'Latência 89% acima da baseline. Possível congestionamento em nó de Mumbabi.'),
  ('ace',           'packet_loss', 'warning',  2.14,  0.05,  NOW() - INTERVAL '8 hours',
   'Perda de pacotes elevada detectada no segmento Dakar–Abidjan.'),
  ('flag-falcon',   'availability','critical', 72.3,  99.8,  NOW() - INTERVAL '2 days',
   'Cabo FLAG/FALCON com disponibilidade crítica. Cabo em status inativo.'),
  ('peace-cable',   'jitter',      'warning',  18.7,  8.2,   NOW() - INTERVAL '1 hour',
   'Jitter elevado no segmento Karachi–Djibouti. Verificar rota alternativa.')
ON CONFLICT DO NOTHING;

-- ── Sumário de validação ──────────────────────────────────────────────────────
DO $$
DECLARE
    v_cables   bigint; v_eps bigint; v_telem bigint; v_alerts bigint;
BEGIN
    SELECT COUNT(*) INTO v_cables FROM cables;
    SELECT COUNT(*) INTO v_eps    FROM landing_points;
    SELECT COUNT(*) INTO v_telem  FROM cable_telemetry_samples;
    SELECT COUNT(*) INTO v_alerts FROM telemetry_alerts WHERE resolved_at IS NULL;
    RAISE NOTICE '────────────────────────────────────────';
    RAISE NOTICE 'Seed concluído com sucesso!';
    RAISE NOTICE '  cabos:              %', v_cables;
    RAISE NOTICE '  landing points:     %', v_eps;
    RAISE NOTICE '  amostras telemetria:%', v_telem;
    RAISE NOTICE '  alertas ativos:     %', v_alerts;
    RAISE NOTICE '────────────────────────────────────────';
END;
$$;
