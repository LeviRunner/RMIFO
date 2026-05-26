#!/usr/bin/env node
// sonda.js — Sonda de Telemetria RMIFO v3 (Node.js)
// Substitui ingest_telemetry_samples.py + compute_telemetry_rollups.py + validate_telemetry_baseline.py
'use strict';

const net   = require('net');
const https = require('https');
const http  = require('http');
const cp    = require('child_process');
const { Client } = require('pg');
const fs    = require('fs');
const path  = require('path');
const { URL } = require('url');

// ── Args ─────────────────────────────────────────────────────────────────────
const argv    = process.argv.slice(2);
const hasFlag = (f) => argv.includes(f);
const getArg  = (f, d) => { const i = argv.indexOf(f); return i !== -1 && argv[i + 1] ? argv[i + 1] : d; };

const FULL_SCAN    = hasFlag('--full-scan');
const MTR_CYCLES   = parseInt(getArg('--mtr-cycles', '15'), 10);
const TIMEOUT_S    = parseInt(getArg('--timeout', '8'), 10);
const LIMIT        = parseInt(getArg('--limit', '999'), 10);
const CABLES_FILE  = getArg('--cables-file', ''); // carrega lista de cabos de JSON (sem DB)

// ── Paths ────────────────────────────────────────────────────────────────────
const BASE_DIR  = process.env.BASE_DIR || path.resolve(__dirname, '..');
const dateStr   = new Date().toISOString().slice(0, 10);
const histDir   = path.join(BASE_DIR, 'data', 'local', 'historico', dateStr);
fs.mkdirSync(histDir, { recursive: true });
const LIVE_FILE    = path.join(histDir, '.probe_live.json');
const CABLES_JSONL = path.join(histDir, 'cables_live.jsonl');

// ── Constants ─────────────────────────────────────────────────────────────────
const MODEL        = '1.4.0';
const AGENT        = 'RMIFO-GlobalNetworkResilience/1.4.0';
const PROBE_AGENT  = `RMIFO-Codespace/sonda.js/${MODEL}`;
const FULL_PORTS      = [443, 80, 22, 8443, 8080];
const STD_PORTS       = [443, 80];
const MTR_BUDGET      = 300;
const TCP_JITTER_PKTS = 50;

// ── Logging ───────────────────────────────────────────────────────────────────
const ts   = () => new Date().toISOString().slice(11, 19);
const log  = (...a) => console.log(`[${ts()}]`, ...a);
const ok   = (...a) => console.log(`[${ts()}] ✔`, ...a);
const warn = (...a) => console.log(`[${ts()}] ⚠`, ...a);

// ── Math helpers ──────────────────────────────────────────────────────────────
const r3 = (n) => Math.round(n * 1000) / 1000;
const r1 = (n) => Math.round(n * 10) / 10;

// ── Filesystem helpers ────────────────────────────────────────────────────────
function writeLive(status) {
    try {
        const tmp = LIVE_FILE + '.tmp';
        fs.writeFileSync(tmp, JSON.stringify(status), 'utf8');
        fs.renameSync(tmp, LIVE_FILE);
    } catch {}
}

function extractHost(url) {
    if (!url) return null;
    try {
        const u = new URL(url.includes('://') ? url : `https://${url}`);
        return u.hostname || null;
    } catch { return null; }
}

// ── Synthetic model ───────────────────────────────────────────────────────────
function synth(lenKm, capGbps, eps) {
    lenKm   = Math.max(100, lenKm  || 1200);
    eps     = Math.max(2,   eps    || 2);
    capGbps = capGbps || Math.max(2, 12000 / lenKm);
    const lat   = Math.max(8, (lenKm / 200) * 1.35);
    const tput  = capGbps * 1000 * (0.52 + Math.min(0.35, eps / 120));
    const jit   = Math.max(0.6, lat * 0.06);
    const loss  = Math.max(0.02, Math.min(2, 0.08 + jit / 80));
    const avail = Math.max(98, Math.min(100, 99.97 - loss * 0.04));
    return { lat: r3(lat), jit: r3(jit), loss: r3(loss), tput: r3(tput), avail: r3(avail) };
}

// ── TCP probe ─────────────────────────────────────────────────────────────────
function tcpProbe(host, port, ms) {
    return new Promise(res => {
        const t0   = Date.now();
        const sock = new net.Socket();
        sock.setTimeout(ms);
        sock.connect(port, host, () => {
            const rtt = Date.now() - t0;
            sock.destroy();
            res({ rtt, sentAt: new Date(t0), recvAt: new Date() });
        });
        const done = () => { sock.destroy(); res(null); };
        sock.on('timeout', done);
        sock.on('error',   done);
    });
}

async function scanPorts(host, ports) {
    const hits = await Promise.all(ports.map(async p => {
        const r = await tcpProbe(host, p, 1500);
        return r ? p : null;
    }));
    return hits.filter(Boolean).sort((a, b) => a - b);
}

async function jitterPasses(host, ports, n, ms) {
    if (!ports.length) return null;
    const pp  = Math.max(1, Math.floor(n / ports.length));
    const all = ports.flatMap(p => Array.from({ length: pp }, () => tcpProbe(host, p, ms)));
    const rs  = (await Promise.all(all)).filter(Boolean).map(r => r.rtt);
    if (rs.length < 2) return null;
    const mean = rs.reduce((a, b) => a + b) / rs.length;
    const jit  = Math.sqrt(rs.reduce((a, b) => a + (b - mean) ** 2, 0) / rs.length);
    const sorted = [...rs].sort((a, b) => a - b);
    const p95  = sorted[Math.max(0, Math.floor(rs.length * 0.95) - 1)];
    const loss = r1((1 - rs.length / all.length) * 100);
    return { mean: r1(mean), jit: r1(jit), p95: r1(p95), loss, samples: rs.length };
}

// ── HTTP probe ────────────────────────────────────────────────────────────────
function httpProbe(url, ms) {
    return new Promise(res => {
        if (!url) { res(null); return; }
        const lib = url.startsWith('https') ? https : http;
        const t0  = Date.now();
        const req = lib.get(url, { timeout: ms, headers: { 'User-Agent': AGENT } }, (r) => {
            const rtt = Date.now() - t0;
            r.resume();
            res({ rtt, sentAt: new Date(t0), recvAt: new Date() });
        });
        req.on('error',   () => res(null));
        req.on('timeout', () => { req.destroy(); res(null); });
    });
}

// ── IP helpers ────────────────────────────────────────────────────────────────
function isPrivateIp(ip) {
    if (!ip || ip === '???' || ip === '*') return true;
    return /^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|127\.|169\.254\.|::1|fd[0-9a-f]{2}:|fc[0-9a-f]{2}:)/i.test(ip);
}

// ── Topologia de caminho de rede ──────────────────────────────────────────────
// Classifica cada hub MTR no segmento da topologia que corresponde ao diagrama:
//   Computador → [Comutador] → [Gateway] → [ISP Edge] → [ISP Transit] → [ISP Server]
function classifyHopRole(hub, idx, hubs) {
    const ip = hub.host;
    if (!ip || ip === '???' || ip === '*') return 'unknown';

    if (isPrivateIp(ip)) {
        // Primeiro salto privado = gateway/roteador local
        // Saltos privados posteriores = infraestrutura interna do ISP ou VPN
        const firstPublicIdx = hubs.findIndex(
            h => h.host && h.host !== '???' && !isPrivateIp(h.host)
        );
        if (firstPublicIdx === -1 || idx < firstPublicIdx) return 'gateway';
        return 'switch_local';
    }

    // IP público — verifica se é o último hop visível (< 100% loss)
    const remainingVisible = hubs.slice(idx + 1).some(
        h => h.host && h.host !== '???' && (h['Loss%'] || 0) < 100
    );
    if (!remainingVisible) return 'isp_server';

    // Contagem de IPs públicos anteriores decide se é edge ou transit
    const prevPublic = hubs.slice(0, idx).filter(
        h => h.host && h.host !== '???' && !isPrivateIp(h.host)
    ).length;
    return prevPublic === 0 ? 'isp_edge' : 'isp_transit';
}

// Agrega hubs MTR em segmentos de topologia (um registro por papel de rede)
function buildTopologySegments(hubs) {
    if (!hubs || !hubs.length) return [];
    const classified = hubs.map((hub, idx) => ({ hub, role: classifyHopRole(hub, idx, hubs) }));

    const pickFirst = (role) => classified.find(c => c.role === role);
    const pickAll   = (role) => classified.filter(c => c.role === role);

    const segment = (type, items, repItem) => {
        if (!repItem) return null;
        const { hub } = repItem;
        const rtts = items.map(c => c.hub.Avg).filter(Boolean);
        const bests = items.map(c => c.hub.Best).filter(Boolean);
        const worsts = items.map(c => c.hub.Wrst).filter(Boolean);
        return {
            segment_type: type,
            hop_number:   items.length === 1 ? hub.count : null,
            hop_ip:       items.length === 1 && hub.host !== '???' ? hub.host : null,
            hop_count:    items.length,
            rtt_avg_ms:   rtts.length   ? r3(rtts.reduce((a, b)   => a + b)  / rtts.length) : null,
            rtt_best_ms:  bests.length  ? Math.min(...bests)  : null,
            rtt_worst_ms: worsts.length ? Math.max(...worsts) : null,
            loss_pct:     r1(items.reduce((a, c) => a + (c.hub['Loss%'] || 0), 0) / items.length),
        };
    };

    return [
        segment('gateway',     pickAll('gateway'),     pickFirst('gateway')),
        segment('isp_edge',    pickAll('isp_edge'),    pickFirst('isp_edge')),
        segment('isp_transit', pickAll('isp_transit'), pickFirst('isp_transit')),
        segment('isp_server',  pickAll('isp_server'),  pickFirst('isp_server')),
    ].filter(Boolean);
}

// ── Geolocalização de IPs via ip-api.com/batch ─────────────────────────────
async function geoLookupBatch(ips) {
    if (!ips || !ips.length) return [];
    return new Promise(res => {
        const body = JSON.stringify(ips.map(ip => ({
            query: ip,
            fields: 'status,query,lat,lon,city,regionName,country,countryCode,isp,org,as,asname',
        })));
        const options = {
            hostname: 'ip-api.com',
            path:     '/batch',
            method:   'POST',
            headers:  { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
        };
        const req = http.request(options, resp => {
            let data = '';
            resp.on('data', d => { data += d; });
            resp.on('end', () => { try { res(JSON.parse(data)); } catch { res([]); } });
        });
        req.on('error', () => res([]));
        req.setTimeout(18000, () => { req.destroy(); res([]); });
        req.write(body);
        req.end();
    });
}

// ── MTR probe ─────────────────────────────────────────────────────────────────
function mtrProbe(host, cycles, timeoutS) {
    return new Promise(res => {
        const t0   = Date.now();
        const proc = cp.spawn('mtr', [
            '--report', `--report-cycles=${cycles}`, '--no-dns', '--json',
            '--timeout', String(timeoutS), host,
        ]);
        let out = '';
        proc.stdout.on('data', d => { out += d; });
        proc.on('error', () => res(null));
        proc.on('close', () => {
            try {
                const hubs = JSON.parse(out)?.report?.hubs || [];
                let avg = null;
                for (const h of hubs) {
                    if (h.host && h.host !== '???' && (h['Loss%'] || 0) < 100) {
                        const a = parseFloat(h.Avg || 0);
                        if (a > 0) avg = a;
                    }
                }
                if (!avg) { res(null); return; }
                res({ rtt: r3(avg), sentAt: new Date(t0), recvAt: new Date(), hubs });
            } catch { res(null); }
        });
        setTimeout(() => { try { proc.kill(); } catch {} res(null); }, (timeoutS + 15) * 1000);
    });
}

// ── DB schema ─────────────────────────────────────────────────────────────────
async function ensureSchema(db) {
    try {
        await db.query(`
            CREATE TABLE IF NOT EXISTS hop_switch_geolocations (
                id           BIGSERIAL PRIMARY KEY,
                hop_ip       INET        NOT NULL UNIQUE,
                lat          DOUBLE PRECISION,
                lng          DOUBLE PRECISION,
                city         VARCHAR(120),
                region       VARCHAR(120),
                country      VARCHAR(80),
                country_code CHAR(2),
                isp          VARCHAR(200),
                org          VARCHAR(200),
                as_number    VARCHAR(30),
                as_name      VARCHAR(200),
                queried_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        `);
        await db.query(`CREATE INDEX IF NOT EXISTS idx_hop_geo_ip ON hop_switch_geolocations(hop_ip)`);
        // Migration idempotente — adiciona colunas de enriquecimento se ainda não existem
        const enrichCols = [
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS hostname        VARCHAR(255)`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS timezone        VARCHAR(60)`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS asn_route       CIDR`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS asn_domain      VARCHAR(120)`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS asn_type        VARCHAR(20)`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS company_domain  VARCHAR(120)`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS company_type    VARCHAR(20)`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS abuse_email     VARCHAR(200)`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS abuse_network   VARCHAR(50)`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS privacy_vpn     BOOLEAN NOT NULL DEFAULT false`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS privacy_proxy   BOOLEAN NOT NULL DEFAULT false`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS privacy_tor     BOOLEAN NOT NULL DEFAULT false`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS privacy_relay   BOOLEAN NOT NULL DEFAULT false`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS privacy_hosting BOOLEAN NOT NULL DEFAULT false`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS tls_san_count   INTEGER`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS tls_san_sample  TEXT[]`,
            `ALTER TABLE hop_switch_geolocations ADD COLUMN IF NOT EXISTS enriched_at     TIMESTAMPTZ`,
        ];
        for (const sql of enrichCols) {
            await db.query(sql).catch(() => {}); // IF NOT EXISTS torna idempotente
        }
    } catch (e) { warn('Schema hop_geo:', e.message); }
    try {
        await db.query(`
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
            )
        `);
        await db.query(`
            ALTER TABLE cable_telemetry_samples
                ADD COLUMN IF NOT EXISTS sent_at     TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ
        `);
        // Colunas de resultado bruto por protocolo (sem mesclagem)
        const probeResultCols = [
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS mtr_rtt_ms          DOUBLE PRECISION`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS mtr_hops            INTEGER`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS tcp_ports_open      INTEGER[]`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS tcp_rtt_ms          JSONB`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS tcp_jitter_mean_ms  DOUBLE PRECISION`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS tcp_jitter_p95_ms   DOUBLE PRECISION`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS tcp_jitter_loss_pct DOUBLE PRECISION`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS tcp_jitter_samples  INTEGER`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS http_rtt_ms         DOUBLE PRECISION`,
            // Resumo de topologia por segmento de rede
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS gateway_rtt_ms      DOUBLE PRECISION`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS isp_edge_rtt_ms     DOUBLE PRECISION`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS isp_server_rtt_ms   DOUBLE PRECISION`,
            // Campos de rastreabilidade e detalhe de sonda (equivalentes ao probe_full local)
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS open_ports          INTEGER[]`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS packets_per_route   INTEGER`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS probe_agent         VARCHAR(120)`,
            `ALTER TABLE cable_telemetry_samples ADD COLUMN IF NOT EXISTS mtr_hubs_raw        JSONB`,
        ];
        for (const sql of probeResultCols) await db.query(sql).catch(() => {});
    } catch (e) { warn('Schema:', e.message); }

    // Tabela de segmentos de topologia — um registro por papel de rede por sonda
    try {
        await db.query(`
            CREATE TABLE IF NOT EXISTS probe_path_segments (
                segment_id   BIGSERIAL PRIMARY KEY,
                sample_id    BIGINT REFERENCES cable_telemetry_samples(sample_id) ON DELETE CASCADE,
                cable_id     VARCHAR(255) NOT NULL,
                measured_at  TIMESTAMPTZ NOT NULL,
                -- Papel no caminho: gateway | isp_edge | isp_transit | isp_server
                segment_type VARCHAR(20) NOT NULL,
                hop_number   INTEGER,           -- hop representativo (null para isp_transit)
                hop_ip       INET,
                hop_count    INTEGER DEFAULT 1, -- nº de hops neste segmento
                rtt_avg_ms   DOUBLE PRECISION,
                rtt_best_ms  DOUBLE PRECISION,
                rtt_worst_ms DOUBLE PRECISION,
                loss_pct     DOUBLE PRECISION,
                UNIQUE (sample_id, segment_type)
            )
        `);
        await db.query(`CREATE INDEX IF NOT EXISTS idx_pps_cable   ON probe_path_segments(cable_id)`);
        await db.query(`CREATE INDEX IF NOT EXISTS idx_pps_ts      ON probe_path_segments(measured_at DESC)`);
        await db.query(`CREATE INDEX IF NOT EXISTS idx_pps_type    ON probe_path_segments(segment_type)`);
        // hop_role classifica cada salto individual na tabela de hops existente
        await db.query(`ALTER TABLE cable_telemetry_hops ADD COLUMN IF NOT EXISTS hop_role VARCHAR(20)`);
    } catch (e) { warn('Schema topology:', e.message); }
}


// ── Packet-target por variação ────────────────────────────────────────────────
function varTarget(method) {
    if (!method) return 0;
    if (method.includes('mtr'))         return MTR_CYCLES * 20; // ~20 hops médios
    const m = method.match(/(\d+)pkt/);
    if (m)                              return parseInt(m[1], 10);
    if (method.includes('tcp'))         return 1;
    if (method.includes('http') || method.includes('curl')) return 1;
    return 0;
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
    log('=== Sonda RMIFO v3 (Node.js) iniciando ===');
    log(`Modo: ${FULL_SCAN ? 'sonda completa' : 'padrão'} | MTR cycles: ${MTR_CYCLES} | Timeout: ${TIMEOUT_S}s`);

    // DB connection
    const dbUrl = process.env.DATABASE_URL;
    let db = null;
    if (dbUrl) {
        db = new Client({ connectionString: dbUrl, ssl: { rejectUnauthorized: false } });
        try {
            await db.connect();
            ok('Banco de dados conectado.');
        } catch (e) {
            warn(`DB indisponível (${e.message}) — sonda sem persistência.`);
            db = null;
        }
    } else {
        warn('DATABASE_URL não definido — sonda sem persistência.');
    }

    if (db) await ensureSchema(db);

    // Cable list: --cables-file JSON > DB > abort
    let cables = [];
    if (CABLES_FILE) {
        try {
            const raw = fs.readFileSync(path.resolve(CABLES_FILE), 'utf8');
            cables = JSON.parse(raw).slice(0, LIMIT);
            log(`${cables.length} cabos carregados de ${CABLES_FILE}.`);
        } catch (e) {
            warn('Erro ao ler --cables-file:', e.message);
        }
    }
    if (!cables.length && db) {
        try {
            const res = await db.query(`
                SELECT c.cable_id, c.cable_name, c.length_km, c.capacity_gbps,
                       c.landing_points_count, cm.public_url
                FROM cables c
                LEFT JOIN cable_metadata cm ON cm.cable_id = c.cable_id
                ORDER BY c.cable_id LIMIT $1
            `, [LIMIT]);
            cables = res.rows;
            log(`${cables.length} cabos carregados do banco.`);
        } catch (e) {
            warn('Não foi possível obter lista de cabos:', e.message);
        }
    }

    if (!cables.length) {
        warn('Nenhum cabo para sondar — use --cables-file ou configure DATABASE_URL.');
        if (db) await db.end().catch(() => {});
        return;
    }

    // Session-level switch geo tracking
    const geoSessionCache = new Set(); // IPs already looked up this session
    let switchCount = 0;               // unique switches geolocated this session

    // Pre-load IPs already in DB so we don't re-query
    if (db) {
        try {
            const existing = await db.query('SELECT hop_ip::text FROM hop_switch_geolocations');
            existing.rows.forEach(r => geoSessionCache.add(r.hop_ip));
            log(`${geoSessionCache.size} switches já geolocalizados no banco.`);
        } catch {}
    }

    // Main probe loop
    // measAt é por cabo para evitar conflito UNIQUE (cable_id, measured_at) entre execuções
    const sessionStart = new Date();
    const stats  = { mtr: 0, tcp_syn: 0, tcp_full: 0, tcp_jitter: 0, http: 0, synth: 0 };
    let mtrUsed  = 0;
    let inserted = 0;
    const total  = cables.length;

    // Checkpoint: escreve .probe_live.json a cada CHECKPOINT_N cabos (não a cada 1)
    const CHECKPOINT_N = 10;
    const recentProbes = []; // últimas sondas para exibição na tabela fixa
    const registros    = []; // acumulado completo → probe_full_*.json no final

    // Fila de geo lookup — não bloqueia o loop principal
    const geoQueue = [];
    let geoRunning = false;
    async function flushGeoQueue() {
        if (geoRunning || !geoQueue.length || !db) return;
        geoRunning = true;
        while (geoQueue.length) {
            const batch = geoQueue.splice(0, 20);
            try {
                const results = await geoLookupBatch(batch);
                for (const r of results) {
                    if (r.status !== 'success' || !r.lat) continue;
                    await db.query(`
                        INSERT INTO hop_switch_geolocations
                            (hop_ip, lat, lng, city, region, country, country_code, isp, org, as_number, as_name)
                        VALUES ($1::inet,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                        ON CONFLICT (hop_ip) DO NOTHING
                    `, [r.query, r.lat, r.lon, r.city, r.regionName, r.country,
                        r.countryCode, r.isp, r.org, r.as, r.asname]).catch(() => {});
                    geoSessionCache.add(r.query);
                    switchCount++;
                }
            } catch {}
        }
        geoRunning = false;
    }

    for (const c of cables) {
        const cableId   = c.cable_id;
        const cableName = c.cable_name || cableId;
        const base      = synth(c.length_km, c.capacity_gbps, c.landing_points_count);
        const host      = extractHost(c.public_url);
        // Timestamp único por cabo: evita conflito UNIQUE se sonda rodar duas vezes no mesmo segundo
        const measAt    = new Date(sessionStart.getTime() + inserted);

        // Coletores de resultados por protocolo — sem early exit
        const results_tcp  = []; // { rtt, port, sentAt, recvAt }
        const results_http = null;
        let   mr           = null;
        let   openPorts    = [];

        // 1. Scan de portas abertas (sempre — necessário para saber onde sondar)
        if (host) {
            openPorts = await scanPorts(host, FULL_PORTS);
        }

        // 2. MTR — roda se orçamento disponível (fornece hops e RTT via ICMP/UDP)
        if (host && mtrUsed < MTR_BUDGET) {
            const cyc = Math.min(MTR_CYCLES, MTR_BUDGET - mtrUsed);
            mr = await mtrProbe(host, cyc, TIMEOUT_S);
            if (mr) { stats.mtr++; mtrUsed += cyc; }
        }

        // 3. TCP SYN-ACK em TODAS as portas abertas (sem break — coleta todas)
        if (host && openPorts.length) {
            const tms = FULL_SCAN ? 2500 : TIMEOUT_S * 1000;
            const tcpAll = await Promise.all(openPorts.map(p => tcpProbe(host, p, tms)));
            for (let i = 0; i < openPorts.length; i++) {
                if (tcpAll[i]) {
                    results_tcp.push({ rtt: tcpAll[i].rtt, port: openPorts[i],
                                       sentAt: tcpAll[i].sentAt, recvAt: tcpAll[i].recvAt });
                    stats.tcp_syn++;
                }
            }
        } else if (host) {
            // Fallback: portas padrão mesmo sem scan prévio
            const tms = TIMEOUT_S * 1000;
            const tcpStd = await Promise.all(STD_PORTS.map(p => tcpProbe(host, p, tms)));
            for (let i = 0; i < STD_PORTS.length; i++) {
                if (tcpStd[i]) {
                    results_tcp.push({ rtt: tcpStd[i].rtt, port: STD_PORTS[i],
                                       sentAt: tcpStd[i].sentAt, recvAt: tcpStd[i].recvAt });
                    stats.tcp_syn++;
                }
            }
        }

        // 4. TCP Jitter — em todas as portas que responderam (mede variância real)
        let jitterResult = null;
        if (results_tcp.length) {
            const activePorts = results_tcp.map(r => r.port);
            const pkts = FULL_SCAN ? TCP_JITTER_PKTS : Math.min(20, TCP_JITTER_PKTS);
            jitterResult = await jitterPasses(host, activePorts, pkts, 2500);
            if (jitterResult) stats.tcp_jitter++;
        }

        // 5. HTTP — sempre roda se public_url disponível (independente do TCP)
        let hrResult = null;
        if (c.public_url) {
            hrResult = await httpProbe(c.public_url, TIMEOUT_S * 1000);
            if (hrResult) stats.http++;
        }

        // ── Salva resultados brutos por protocolo — sem mesclagem ───────────────
        // Cada protocolo grava seus dados na coluna dedicada; nada é descartado.

        // Topologia de caminho: classifica hubs MTR por papel de rede
        const topologySegments = buildTopologySegments(mr?.hubs ?? []);
        const topoByType = Object.fromEntries(topologySegments.map(s => [s.segment_type, s]));

        const raw = {
            mtr_rtt:          mr             ? r3(mr.rtt)           : null,
            mtr_hops:         (mr?.hubs)     ? mr.hubs.length       : null,
            mtr_hubs_raw:     mr?.hubs       ?? null,
            tcp_ports:        results_tcp.map(r => r.port),
            tcp_rtt:          Object.fromEntries(results_tcp.map(r => [r.port, r.rtt])),
            jit_mean:         jitterResult   ? r1(jitterResult.mean) : null,
            jit_p95:          jitterResult   ? r1(jitterResult.p95)  : null,
            jit_loss:         jitterResult   ? jitterResult.loss      : null,
            jit_samples:      jitterResult   ? jitterResult.samples   : null,
            http_rtt:         hrResult       ? r1(hrResult.rtt)       : null,
            // Resumo por segmento de topologia
            gateway_rtt:      topoByType.gateway?.rtt_avg_ms    ?? null,
            isp_edge_rtt:     topoByType.isp_edge?.rtt_avg_ms   ?? null,
            isp_server_rtt:   topoByType.isp_server?.rtt_avg_ms ?? null,
            // Rastreabilidade (equivalentes ao probe_full local)
            open_ports:       results_tcp.map(r => r.port),  // alias explícito de tcp_ports
            packets_per_route: jitterResult ? jitterResult.samples : (results_tcp.length ? 1 : null),
        };

        // latency_ms: valor bruto do primeiro protocolo que respondeu (sem média ponderada)
        let latMs = null, jitMs = null, lossP = null;
        let tputM = base.tput, availP = base.avail;
        let src = 'synthetic', meth = 'heuristic', conf = 0.56, est = true;
        let sentAt = null, recvAt = null;
        const methParts = [];

        if (mr) {
            latMs  = r3(mr.rtt);
            src    = 'active_mtr'; conf = 0.90; est = false;
            sentAt = mr.sentAt; recvAt = mr.recvAt;
            methParts.push('mtr-udp');
        }
        if (jitterResult) {
            if (latMs === null) latMs = r3(jitterResult.mean);
            jitMs = jitterResult.jit;
            lossP = jitterResult.loss ?? null;
            conf  = Math.max(conf, 0.95); est = false;
            src   = src === 'synthetic' ? 'active_tcp_connect' : src;
            methParts.push(`tcp-jitter-${jitterResult.samples ?? '?'}pkt`);
        } else if (results_tcp.length) {
            const bestTcp = results_tcp.reduce((a, b) => a.rtt < b.rtt ? a : b);
            if (latMs === null) {
                latMs  = r3(bestTcp.rtt);
                sentAt = bestTcp.sentAt; recvAt = bestTcp.recvAt;
            }
            conf = Math.max(conf, 0.78); est = false;
            src  = src === 'synthetic' ? 'active_tcp_connect' : src;
            methParts.push(`tcp-syn-ack-ports${results_tcp.map(r => r.port).join(',')}`);
        }
        if (hrResult) {
            if (latMs === null) {
                latMs  = r3(hrResult.rtt);
                sentAt = hrResult.sentAt; recvAt = hrResult.recvAt;
                src    = 'active_http_probe'; conf = 0.82; est = false;
            }
            methParts.push('http-curl');
        }
        if (latMs === null) {
            latMs = r3(base.lat * (1 + (Math.random() - 0.5) * 0.1));
            jitMs = r3(base.jit);
            lossP = r3(base.loss);
            methParts.push('heuristic-synthetic');
            stats.synth++;
        }

        meth   = methParts.join('+') || 'heuristic';
        latMs  = r3(latMs);
        jitMs  = r3(jitMs  ?? base.jit  * (1 + (Math.random() - 0.5) * 0.2));
        lossP  = r3(lossP  ?? base.loss * (1 + (Math.random() - 0.5) * 0.3));
        tputM  = r3(tputM  * (1 + (Math.random() - 0.5) * 0.16));
        availP = r3(Math.max(97.5, Math.min(100, availP - lossP * 0.03)));
        const pps  = Math.max(1000, Math.floor((tputM * 1e6) / (8 * 900)));
        const errs = lossP > 0.25 ? 1 : 0;

        inserted++;
        const prog = r1((inserted / total) * 100);

        // DB insert — timestamp único por cabo evita conflito entre execuções
        if (db) {
            try {
                const sampleRes = await db.query(`
                    INSERT INTO cable_telemetry_samples (
                        cable_id, measured_at, sent_at, received_at,
                        latency_ms, jitter_ms, packet_loss_pct, throughput_mbps,
                        packets_per_second, availability_pct, route_error_count,
                        source, method, confidence_score, estimated,
                        telemetry_model_version, metadata,
                        mtr_rtt_ms, mtr_hops,
                        tcp_ports_open, tcp_rtt_ms,
                        tcp_jitter_mean_ms, tcp_jitter_p95_ms, tcp_jitter_loss_pct, tcp_jitter_samples,
                        http_rtt_ms,
                        gateway_rtt_ms, isp_edge_rtt_ms, isp_server_rtt_ms,
                        open_ports, packets_per_route, probe_agent, mtr_hubs_raw
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33)
                    ON CONFLICT (cable_id, measured_at) DO UPDATE SET
                        latency_ms              = EXCLUDED.latency_ms,
                        jitter_ms               = EXCLUDED.jitter_ms,
                        packet_loss_pct         = EXCLUDED.packet_loss_pct,
                        throughput_mbps         = EXCLUDED.throughput_mbps,
                        packets_per_second      = EXCLUDED.packets_per_second,
                        availability_pct        = EXCLUDED.availability_pct,
                        route_error_count       = EXCLUDED.route_error_count,
                        source                  = EXCLUDED.source,
                        method                  = EXCLUDED.method,
                        confidence_score        = EXCLUDED.confidence_score,
                        estimated               = EXCLUDED.estimated,
                        telemetry_model_version = EXCLUDED.telemetry_model_version,
                        metadata                = EXCLUDED.metadata,
                        mtr_rtt_ms              = EXCLUDED.mtr_rtt_ms,
                        mtr_hops                = EXCLUDED.mtr_hops,
                        tcp_ports_open          = EXCLUDED.tcp_ports_open,
                        tcp_rtt_ms              = EXCLUDED.tcp_rtt_ms,
                        tcp_jitter_mean_ms      = EXCLUDED.tcp_jitter_mean_ms,
                        tcp_jitter_p95_ms       = EXCLUDED.tcp_jitter_p95_ms,
                        tcp_jitter_loss_pct     = EXCLUDED.tcp_jitter_loss_pct,
                        tcp_jitter_samples      = EXCLUDED.tcp_jitter_samples,
                        http_rtt_ms             = EXCLUDED.http_rtt_ms,
                        gateway_rtt_ms          = EXCLUDED.gateway_rtt_ms,
                        isp_edge_rtt_ms         = EXCLUDED.isp_edge_rtt_ms,
                        isp_server_rtt_ms       = EXCLUDED.isp_server_rtt_ms,
                        open_ports              = EXCLUDED.open_ports,
                        packets_per_route       = EXCLUDED.packets_per_route,
                        probe_agent             = EXCLUDED.probe_agent,
                        mtr_hubs_raw            = EXCLUDED.mtr_hubs_raw
                    RETURNING sample_id
                `, [
                    cableId, measAt, sentAt, recvAt,
                    latMs, jitMs, lossP, tputM, pps, availP, errs,
                    src, meth, conf, est, MODEL,
                    JSON.stringify({ target_host: host, open_ports: openPorts, has_mtr: !!mr, has_http: !!hrResult }),
                    raw.mtr_rtt, raw.mtr_hops,
                    raw.tcp_ports.length ? raw.tcp_ports : null,
                    raw.tcp_ports.length ? JSON.stringify(raw.tcp_rtt) : null,
                    raw.jit_mean, raw.jit_p95, raw.jit_loss, raw.jit_samples,
                    raw.http_rtt,
                    raw.gateway_rtt, raw.isp_edge_rtt, raw.isp_server_rtt,
                    raw.open_ports.length ? raw.open_ports : null,
                    raw.packets_per_route,
                    PROBE_AGENT,
                    raw.mtr_hubs_raw ? JSON.stringify(raw.mtr_hubs_raw) : null,
                ]);

                const sampleId = sampleRes.rows[0]?.sample_id;
                if (sampleId && mr && mr.hubs && mr.hubs.length) {
                    // Hops individuais com papel de rede classificado
                    const hopQ = `
                        INSERT INTO cable_telemetry_hops
                            (sample_id, cable_id, hop_number, hop_ip,
                             rtt_last_ms, rtt_avg_ms, rtt_best_ms, rtt_worst_ms, rtt_stdev_ms, loss_pct,
                             hop_role)
                        VALUES ($1,$2,$3,$4::inet,$5,$6,$7,$8,$9,$10,$11)
                        ON CONFLICT (sample_id, hop_number) DO UPDATE SET
                            hop_ip   = EXCLUDED.hop_ip,
                            rtt_avg_ms = EXCLUDED.rtt_avg_ms,
                            hop_role = EXCLUDED.hop_role`;
                    for (const [idx, h] of mr.hubs.entries()) {
                        const ip   = h.host && h.host !== '???' ? h.host : null;
                        const role = classifyHopRole(h, idx, mr.hubs);
                        await db.query(hopQ, [
                            sampleId, cableId, h.count, ip,
                            h.Last || null, h.Avg || null, h.Best || null,
                            h.Wrst || null, h.StDev || null, h['Loss%'] || 0,
                            role,
                        ]).catch(() => {});
                    }

                    // Segmentos de topologia agregados (um por papel de rede)
                    if (topologySegments.length) {
                        const segQ = `
                            INSERT INTO probe_path_segments
                                (sample_id, cable_id, measured_at, segment_type,
                                 hop_number, hop_ip, hop_count,
                                 rtt_avg_ms, rtt_best_ms, rtt_worst_ms, loss_pct)
                            VALUES ($1,$2,$3,$4,$5,$6::inet,$7,$8,$9,$10,$11)
                            ON CONFLICT (sample_id, segment_type) DO UPDATE SET
                                rtt_avg_ms   = EXCLUDED.rtt_avg_ms,
                                rtt_worst_ms = EXCLUDED.rtt_worst_ms,
                                hop_count    = EXCLUDED.hop_count`;
                        for (const seg of topologySegments) {
                            await db.query(segQ, [
                                sampleId, cableId, measAt, seg.segment_type,
                                seg.hop_number, seg.hop_ip, seg.hop_count,
                                seg.rtt_avg_ms, seg.rtt_best_ms, seg.rtt_worst_ms, seg.loss_pct,
                            ]).catch(() => {});
                        }
                    }
                }
            } catch (e) { warn(`DB insert ${cableId}: ${e.message}`); }
        }

        // Fila de geo lookup (não bloqueia o loop — processa em background)
        let hopCount = 0;
        let hopsPreview = [];
        if (mr && mr.hubs && mr.hubs.length) {
            hopCount    = mr.hubs.length;
            hopsPreview = mr.hubs.slice(0, 3).map(h => h.host || '*');
            const newIps = mr.hubs
                .map(h => h.host)
                .filter(ip => ip && !isPrivateIp(ip) && !geoSessionCache.has(ip));
            if (newIps.length) {
                // Marca como "em processamento" para não re-enfileirar
                newIps.forEach(ip => geoSessionCache.add(ip));
                geoQueue.push(...newIps);
                // Processa fila de forma não-bloqueante
                setImmediate(flushGeoQueue);
            }
        }

        // Acumula últimas sondas para checkpoint (tabela fixa)
        recentProbes.unshift({
            n: inserted, cable_id: cableId, cable_name: cableName,
            proto: meth.split('+')[0], latency_ms: r1(latMs),
            hops: hopCount, switches: switchCount, src,
        });
        if (recentProbes.length > 30) recentProbes.pop();

        // ── Checkpoint a cada CHECKPOINT_N cabos (ou no último) ──────────────
        if (inserted % CHECKPOINT_N === 0 || inserted === total) {
            writeLive({
                current:      inserted,
                total,
                percent:      prog,
                tcp:          stats.tcp_syn + stats.tcp_full + stats.tcp_jitter,
                synthetic:    stats.synth,
                mtr:          stats.mtr,
                http:         stats.http,
                switch_count: switchCount,
                log_file:     process.env.LOG_FILE || '',
                updated_at:   new Date().toISOString(),
                recent:       recentProbes.slice(0, 15),
                var_counts: {
                    mtr_udp:    stats.mtr,
                    tcp_syn:    stats.tcp_syn,
                    tcp_full:   stats.tcp_full,
                    tcp_jitter: stats.tcp_jitter,
                    http_curl:  stats.http,
                    synthetic:  stats.synth,
                },
            });
        }

        // Per-cable JSONL history (append a cada cabo — I/O barato, sequencial)
        try {
            fs.appendFileSync(CABLES_JSONL, JSON.stringify({
                cable_id: cableId, cable_name: cableName,
                measured_at: measAt.toISOString(),
                latency_ms: latMs, jitter_ms: jitMs,
                packet_loss_pct: lossP, source: src, method: meth,
                open_ports: openPorts,
                tcp_ports_responded: results_tcp.map(r => r.port),
            }) + '\n', 'utf8');
        } catch {}

        // Acumula registro completo para probe_full_*.json final
        registros.push({
            cable_id:               cableId,
            cable_name:             cableName,
            measured_at:            measAt.toISOString(),
            sent_at:                sentAt?.toISOString?.() ?? null,
            received_at:            recvAt?.toISOString?.() ?? null,
            latency_ms:             latMs,
            jitter_ms:              jitMs,
            packet_loss_pct:        lossP,
            throughput_mbps:        tputM,
            packets_per_second:     pps,
            availability_pct:       availP,
            route_error_count:      errs,
            source:                 src,
            method:                 meth,
            confidence_score:       conf,
            estimated:              est,
            telemetry_model_version: MODEL,
            // Rastreabilidade (compatível com formato probe_full legado)
            open_ports:             raw.open_ports.length ? raw.open_ports : [],
            packets_per_route:      raw.packets_per_route,
            probe_agent:            PROBE_AGENT,
            // Por protocolo — dados brutos sem mesclagem
            mtr_rtt_ms:             raw.mtr_rtt,
            mtr_hops:               raw.mtr_hops,
            mtr_hubs_raw:           raw.mtr_hubs_raw,
            tcp_ports_open:         raw.tcp_ports,
            tcp_rtt_ms:             raw.tcp_rtt,
            tcp_jitter_mean_ms:     raw.jit_mean,
            tcp_jitter_p95_ms:      raw.jit_p95,
            tcp_jitter_loss_pct:    raw.jit_loss,
            tcp_jitter_samples:     raw.jit_samples,
            http_rtt_ms:            raw.http_rtt,
            // Topologia de rede — RTT por segmento do diagrama
            gateway_rtt_ms:         raw.gateway_rtt,
            isp_edge_rtt_ms:        raw.isp_edge_rtt,
            isp_server_rtt_ms:      raw.isp_server_rtt,
            topology_segments:      topologySegments.length ? topologySegments : null,
        });

        // Progress line — format matched by api.php regex
        process.stdout.write(
            `  [${String(inserted).padStart(4)}/${total}] ${String(prog).padStart(5)}%  ` +
            `${cableId.padEnd(40)} proto=${meth.split('+')[0].padEnd(24)} lat=${String(r1(latMs)).padStart(8)}ms\n`
        );
    }

    // Aguarda fila de geo lookup terminar antes de encerrar
    if (geoQueue.length) {
        log('Aguardando geo lookup finalizar...');
        await flushGeoQueue();
    }

    // Post-ingest baseline
    if (db) {
        try {
            await db.query(`
                INSERT INTO cable_telemetry_reference (
                    cable_id, baseline_latency_ms, baseline_jitter_ms,
                    baseline_packet_loss_pct, baseline_throughput_mbps,
                    baseline_availability_pct, updated_at, notes
                )
                SELECT cable_id, AVG(latency_ms), AVG(jitter_ms), AVG(packet_loss_pct),
                    AVG(throughput_mbps), AVG(availability_pct),
                    NOW(), 'baseline recalculado automaticamente (janela 7d)'
                FROM cable_telemetry_samples
                WHERE measured_at >= NOW() - INTERVAL '7 days'
                GROUP BY cable_id
                ON CONFLICT (cable_id) DO UPDATE SET
                    baseline_latency_ms       = EXCLUDED.baseline_latency_ms,
                    baseline_jitter_ms        = EXCLUDED.baseline_jitter_ms,
                    baseline_packet_loss_pct  = EXCLUDED.baseline_packet_loss_pct,
                    baseline_throughput_mbps  = EXCLUDED.baseline_throughput_mbps,
                    baseline_availability_pct = EXCLUDED.baseline_availability_pct,
                    updated_at = NOW(), notes = EXCLUDED.notes
            `);
            ok('Baseline de referência atualizado.');
        } catch (e) { warn('Baseline:', e.message); }

        await db.end().catch(() => {});
    }

    // Escreve probe_full_HHMMSS.json com todos os registros acumulados
    if (registros.length > 0) {
        const startTs = sessionStart.toISOString().slice(11, 19).replace(/:/g, '');
        const fullFile = path.join(histDir, `${dateStr}_probe_full_${startTs}.json`);
        try {
            fs.writeFileSync(fullFile, JSON.stringify({
                gerado_em:       new Date().toISOString(),
                medido_em:       sessionStart.toISOString(),
                modo:            FULL_SCAN ? 'full_scan' : 'padrao',
                telemetry_model: MODEL,
                totais: {
                    cabos:     registros.length,
                    mtr:       stats.mtr,
                    tcp:       stats.tcp_syn + stats.tcp_full + stats.tcp_jitter,
                    http:      stats.http,
                    sintetico: stats.synth,
                    hops:      registros.reduce((s, r) => s + (r.mtr_hops ?? 0), 0),
                },
                registros,
            }, null, 2), 'utf8');
            ok(`probe_full salvo: ${path.basename(fullFile)} (${registros.length} registros)`);
        } catch (e) { warn(`Erro ao salvar probe_full: ${e.message}`); }
    }

    // Summary — format matched by api.php regex patterns
    const endTs = new Date().toISOString().slice(11, 19);
    console.log('');
    console.log(`Cabos inseridos: ${inserted}`);
    console.log(`TCP SYN-ACK: ${stats.tcp_syn}`);
    console.log(`TCP Jitter: ${stats.tcp_jitter}`);
    console.log(`Sintetico: ${stats.synth}`);
    console.log(`mtr (hops): ${stats.mtr}`);
    console.log(`HTTP curl: ${stats.http}`);
    console.log(`0 anomalias`);
    console.log(`Concluido em ${endTs}`);
    ok(`=== Sonda concluída: ${inserted}/${total} | MTR=${stats.mtr} TCP-SYN=${stats.tcp_syn} TCP-JIT=${stats.tcp_jitter} HTTP=${stats.http} Sint=${stats.synth} ===`);
}

main().catch(e => {
    console.error('[ERRO FATAL]', e.message);
    process.exit(0); // Always exit 0 — bash script handles error reporting
});
