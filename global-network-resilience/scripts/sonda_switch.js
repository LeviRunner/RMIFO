'use strict';
/**
 * sonda_switch.js — Sonda de Comutadores (MTR dedicado)
 *
 * Modo cabo: sonda MTR para cada cabo submarino (usa public_url como destino).
 * Modo IP range: sonda MTR para cada IP dentro de um bloco CIDR selecionado.
 *
 * Classifica cada salto: gateway → isp_edge → isp_transit → isp_server.
 * Geolocaliza IPs públicos → hop_switch_geolocations.
 * Persiste segmentos → probe_path_segments.
 *
 * Em Railway/Docker: start.sh faz setcap cap_net_raw+ep no mtr automaticamente.
 * Fallback automático para --tcp se ICMP não tiver permissão (CAP_NET_RAW).
 *
 * Uso — modo cabo:
 *   node scripts/sonda_switch.js --cables-file data/cables_list.json
 *   node scripts/sonda_switch.js --cable 2africa --mtr-cycles 30
 *
 * Uso — modo IP range:
 *   node scripts/sonda_switch.js --ip-range 203.0.113.0/24 --limit 30
 *   node scripts/sonda_switch.js --ip-ranges-file data/ip_ranges.json --ip-step 4
 *   node scripts/sonda_switch.js --ip-ranges-db --ip-step 8 --limit 200
 *   node scripts/sonda_switch.js --ip-ranges-db --country BR --limit 100
 *   node scripts/sonda_switch.js --ip-ranges-db --rir LACNIC --limit 500
 */

const net   = require('net');
const http  = require('http');
const cp    = require('child_process');
const { Client } = require('pg');
const fs    = require('fs');
const path  = require('path');
const { URL } = require('url');

const argv    = process.argv.slice(2);
const hasFlag = (f) => argv.includes(f);
const getArg  = (f, d) => { const i = argv.indexOf(f); return i !== -1 && argv[i+1] ? argv[i+1] : d; };

const MTR_CYCLES     = parseInt(getArg('--mtr-cycles',    '20'),  10);
const TIMEOUT_S      = parseInt(getArg('--timeout',        '30'),  10);
const LIMIT          = parseInt(getArg('--limit',          '999'), 10);
const IP_STEP        = Math.max(1, parseInt(getArg('--ip-step', '1'), 10)); // sample every Nth IP
const CABLES_FILE    = getArg('--cables-file',    '');
const SINGLE         = getArg('--cable',          '');   // sonda apenas um cabo
const IP_RANGE       = getArg('--ip-range',       '');   // CIDR único, ex: 203.0.113.0/24
const IP_RANGES_FILE = getArg('--ip-ranges-file', '');   // JSON com lista de ranges
const IP_RANGES_DB   = hasFlag('--ip-ranges-db');        // carrega ranges do banco
const FILTER_COUNTRY = getArg('--country',        '').toUpperCase(); // filtra por ISO2, ex: BR
const FILTER_RIR     = getArg('--rir',            '').toUpperCase(); // filtra por RIR, ex: LACNIC

const MODEL       = '1.4.0';
const PROBE_AGENT = `RMIFO-Switch/sonda_switch.js/${MODEL}`;

const BASE_DIR  = process.env.BASE_DIR || path.resolve(__dirname, '..');
const dateStr   = new Date().toISOString().slice(0, 10);
const histDir   = path.join(BASE_DIR, 'data', 'local', 'historico', dateStr);
fs.mkdirSync(histDir, { recursive: true });

const LIVE_FILE   = path.join(histDir, '.switch_live.json');
const SWITCH_JSONL = path.join(histDir, 'switch_live.jsonl');

// ── Logging ───────────────────────────────────────────────────────────────────
const ts   = () => new Date().toISOString().slice(11, 19);
const log  = (...a) => console.log(`[${ts()}]`, ...a);
const ok   = (...a) => console.log(`[${ts()}] ✔`, ...a);
const warn = (...a) => console.log(`[${ts()}] ⚠`, ...a);
const r3   = (n) => n != null ? Math.round(n * 1000) / 1000 : null;
const r1   = (n) => n != null ? Math.round(n * 10)  / 10   : null;

// ── Escrita de live status ─────────────────────────────────────────────────────
function writeLive(status) {
    const tmp = LIVE_FILE + '.tmp';
    try { fs.writeFileSync(tmp, JSON.stringify(status), 'utf8'); fs.renameSync(tmp, LIVE_FILE); } catch {}
}

// ── URL → hostname ─────────────────────────────────────────────────────────────
function extractHost(url) {
    if (!url) return null;
    try { return new URL(url.includes('://') ? url : `https://${url}`).hostname || null; }
    catch { return null; }
}

// ── IP privado ────────────────────────────────────────────────────────────────
function isPrivateIp(ip) {
    if (!ip || ip === '???' || ip === '*') return true;
    return /^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|127\.|169\.254\.|::1|fd[0-9a-f]{2}:|fc[0-9a-f]{2}:)/i.test(ip);
}

// ── Expansão de CIDR ──────────────────────────────────────────────────────────
function expandCidr(cidr, step) {
    step = step || 1;
    const [base, bits] = (cidr || '').split('/');
    const prefix = parseInt(bits, 10);
    if (!base || isNaN(prefix) || prefix < 0 || prefix > 32) return [];
    const parts   = base.split('.').map(Number);
    const baseInt = ((parts[0]||0)<<24 | (parts[1]||0)<<16 | (parts[2]||0)<<8 | (parts[3]||0)) >>> 0;
    const mask    = prefix === 0 ? 0 : (~0 << (32 - prefix)) >>> 0;
    const network = (baseInt & mask) >>> 0;
    const bcast   = (network | (~mask >>> 0)) >>> 0;
    const start   = prefix < 31 ? network + 1 : network;
    const end     = prefix < 31 ? bcast   - 1 : bcast;
    const ips     = [];
    for (let i = start; i <= end; i = (i + step) >>> 0) {
        ips.push([(i>>>24)&0xFF, (i>>>16)&0xFF, (i>>>8)&0xFF, i&0xFF].join('.'));
    }
    return ips;
}

// ── Classificação de papel de rede ─────────────────────────────────────────────
function classifyHopRole(hub, idx, hubs) {
    const ip = hub.host;
    if (!ip || ip === '???' || ip === '*') return 'unknown';
    if (isPrivateIp(ip)) {
        const firstPublicIdx = hubs.findIndex(h => h.host && h.host !== '???' && !isPrivateIp(h.host));
        if (firstPublicIdx === -1 || idx < firstPublicIdx) return 'gateway';
        return 'switch_local';
    }
    const remainingVisible = hubs.slice(idx + 1).some(
        h => h.host && h.host !== '???' && (h['Loss%'] || 0) < 100
    );
    if (!remainingVisible) return 'isp_server';
    const prevPublic = hubs.slice(0, idx).filter(
        h => h.host && h.host !== '???' && !isPrivateIp(h.host)
    ).length;
    return prevPublic === 0 ? 'isp_edge' : 'isp_transit';
}

// ── Segmentos de topologia agregados ──────────────────────────────────────────
function buildTopologySegments(hubs) {
    if (!hubs || !hubs.length) return [];
    const classified = hubs.map((hub, idx) => ({ hub, role: classifyHopRole(hub, idx, hubs) }));
    const pickFirst  = (role) => classified.find(c => c.role === role);
    const pickAll    = (role) => classified.filter(c => c.role === role);

    const segment = (type, items, repItem) => {
        if (!repItem) return null;
        const { hub } = repItem;
        const rtts   = items.map(c => c.hub.Avg).filter(Boolean);
        const bests  = items.map(c => c.hub.Best).filter(Boolean);
        const worsts = items.map(c => c.hub.Wrst).filter(Boolean);
        return {
            segment_type: type,
            hop_number:   items.length === 1 ? hub.count : null,
            hop_ip:       items.length === 1 && hub.host !== '???' ? hub.host : null,
            hop_count:    items.length,
            rtt_avg_ms:   rtts.length   ? r3(rtts.reduce((a, b) => a + b) / rtts.length) : null,
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

// ── Geolocalização em batch ────────────────────────────────────────────────────
async function geoLookupBatch(ips) {
    if (!ips.length) return [];
    return new Promise(res => {
        const body = JSON.stringify(ips.map(ip => ({
            query: ip,
            fields: 'status,query,lat,lon,city,regionName,country,countryCode,isp,org,as,asname',
        })));
        const req = http.request({
            hostname: 'ip-api.com', path: '/batch', method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
        }, resp => {
            let data = '';
            resp.on('data', d => { data += d; });
            resp.on('end', () => { try { res(JSON.parse(data)); } catch { res([]); } });
        });
        req.on('error', () => res([]));
        req.setTimeout(18000, () => { req.destroy(); res([]); });
        req.write(body); req.end();
    });
}

// ── MTR probe ─────────────────────────────────────────────────────────────────
function runMtr(host, cycles, timeoutS, extraArgs = []) {
    return new Promise(res => {
        const t0   = Date.now();
        const args = ['--report', `--report-cycles=${cycles}`, '--no-dns', '--json',
                      '--timeout', String(timeoutS), ...extraArgs, host];
        const proc = cp.spawn('mtr', args);
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
        setTimeout(() => { try { proc.kill(); } catch {} res(null); }, (timeoutS + 20) * 1000);
    });
}

async function mtrProbe(host, cycles, timeoutS) {
    // Try ICMP first; fall back to TCP if permission denied (no CAP_NET_RAW)
    const result = await runMtr(host, cycles, timeoutS);
    if (result) return result;
    return runMtr(host, cycles, timeoutS, ['--tcp']);
}

// ── Schema ─────────────────────────────────────────────────────────────────────
async function ensureSchema(db) {
    const stmts = [
        `CREATE TABLE IF NOT EXISTS cable_telemetry_hops (
            hop_id       BIGSERIAL PRIMARY KEY,
            sample_id    BIGINT,
            cable_id     VARCHAR(255) NOT NULL,
            hop_number   INTEGER NOT NULL,
            hop_ip       INET,
            rtt_last_ms  DOUBLE PRECISION,
            rtt_avg_ms   DOUBLE PRECISION,
            rtt_best_ms  DOUBLE PRECISION,
            rtt_worst_ms DOUBLE PRECISION,
            rtt_stdev_ms DOUBLE PRECISION,
            loss_pct     DOUBLE PRECISION,
            hop_role     VARCHAR(20),
            UNIQUE (sample_id, hop_number)
        )`,
        `CREATE TABLE IF NOT EXISTS hop_switch_geolocations (
            id           BIGSERIAL PRIMARY KEY,
            hop_ip       INET NOT NULL UNIQUE,
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
        )`,
        `CREATE TABLE IF NOT EXISTS probe_path_segments (
            segment_id   BIGSERIAL PRIMARY KEY,
            sample_id    BIGINT,
            cable_id     VARCHAR(255) NOT NULL,
            measured_at  TIMESTAMPTZ NOT NULL,
            segment_type VARCHAR(20) NOT NULL,
            hop_number   INTEGER,
            hop_ip       INET,
            hop_count    INTEGER DEFAULT 1,
            rtt_avg_ms   DOUBLE PRECISION,
            rtt_best_ms  DOUBLE PRECISION,
            rtt_worst_ms DOUBLE PRECISION,
            loss_pct     DOUBLE PRECISION,
            probe_agent  VARCHAR(120),
            UNIQUE (sample_id, segment_type)
        )`,
        `CREATE TABLE IF NOT EXISTS ip_range_targets (
            id           SERIAL PRIMARY KEY,
            cidr         CIDR NOT NULL UNIQUE,
            label        VARCHAR(120),
            country_code CHAR(2),
            isp_name     VARCHAR(200),
            priority     SMALLINT DEFAULT 5,
            active       BOOLEAN DEFAULT TRUE,
            notes        TEXT,
            created_at   TIMESTAMPTZ DEFAULT NOW()
        )`,
        `CREATE INDEX IF NOT EXISTS idx_ipr_priority ON ip_range_targets(priority, id) WHERE active`,
        `CREATE INDEX IF NOT EXISTS idx_hops_cable   ON cable_telemetry_hops(cable_id)`,
        `CREATE INDEX IF NOT EXISTS idx_hops_ip      ON cable_telemetry_hops(hop_ip)`,
        `CREATE INDEX IF NOT EXISTS idx_geo_ip       ON hop_switch_geolocations(hop_ip)`,
        `CREATE INDEX IF NOT EXISTS idx_pps_cable    ON probe_path_segments(cable_id)`,
        `CREATE INDEX IF NOT EXISTS idx_pps_ts       ON probe_path_segments(measured_at DESC)`,
        `ALTER TABLE cable_telemetry_hops    ADD COLUMN IF NOT EXISTS hop_role    VARCHAR(20)`,
        `ALTER TABLE probe_path_segments     ADD COLUMN IF NOT EXISTS probe_agent VARCHAR(120)`,
    ];
    for (const sql of stmts) await db.query(sql).catch(() => {});
}

// ── Constrói lista de alvos ────────────────────────────────────────────────────
// Retorna array de { id, label, host } — host é o endereço para o MTR
async function buildTargets(db) {
    // Modo IP range
    if (IP_RANGE) {
        const ips = expandCidr(IP_RANGE, IP_STEP).slice(0, LIMIT);
        log(`IP range ${IP_RANGE}: ${ips.length} IPs (step=${IP_STEP})`);
        return ips.map(ip => ({ id: IP_RANGE, label: ip, host: ip }));
    }
    if (IP_RANGES_FILE) {
        const raw    = JSON.parse(fs.readFileSync(path.resolve(IP_RANGES_FILE), 'utf8'));
        const ranges = Array.isArray(raw) ? raw : (raw.ranges || raw.ip_ranges || []);
        const targets = [];
        for (const entry of ranges) {
            const cidr  = typeof entry === 'string' ? entry : entry.cidr;
            const label = (typeof entry === 'object' && entry.label) ? entry.label : cidr;
            if (!cidr) continue;
            const perRange = Math.max(1, Math.ceil(LIMIT / ranges.length));
            const ips = expandCidr(cidr, IP_STEP).slice(0, perRange);
            for (const ip of ips) targets.push({ id: cidr, label: `${label}/${ip}`, host: ip });
        }
        log(`IP ranges file: ${ranges.length} ranges → ${targets.length} IPs`);
        return targets;
    }
    if (IP_RANGES_DB && db) {
        const conditions = ['active = TRUE'];
        const params     = [];
        if (FILTER_COUNTRY) { params.push(FILTER_COUNTRY); conditions.push(`country_code = $${params.length}`); }
        if (FILTER_RIR)     { params.push(FILTER_RIR);     conditions.push(`upper(isp_name) = $${params.length}`); }
        const where = conditions.join(' AND ');
        const res = await db.query(
            `SELECT cidr::text AS cidr, label, country_code, isp_name, priority
             FROM ip_range_targets WHERE ${where} ORDER BY priority, id`,
            params
        );
        const targets = [];
        const rows = res.rows;
        const filterDesc = [FILTER_COUNTRY && `country=${FILTER_COUNTRY}`, FILTER_RIR && `rir=${FILTER_RIR}`].filter(Boolean).join(', ');
        for (const row of rows) {
            const perRange = Math.max(1, Math.ceil(LIMIT / Math.max(rows.length, 1)));
            const ips = expandCidr(row.cidr, IP_STEP).slice(0, perRange);
            for (const ip of ips) targets.push({ id: row.cidr, label: `${row.label || row.cidr}/${ip}`, host: ip });
        }
        log(`IP ranges DB${filterDesc ? ` (${filterDesc})` : ''}: ${rows.length} ranges → ${targets.length} IPs`);
        return targets;
    }

    // Modo cabo (padrão)
    let cables = [];
    if (CABLES_FILE) {
        cables = JSON.parse(fs.readFileSync(path.resolve(CABLES_FILE), 'utf8'));
    } else if (db) {
        const res = await db.query(
            `SELECT cable_id, cable_name, public_url FROM cables ORDER BY cable_id`
        );
        cables = res.rows;
    }
    if (SINGLE) cables = cables.filter(c => c.cable_id === SINGLE);
    cables = cables.slice(0, LIMIT);

    const targets = [];

    // Fonte primária: IPs reais dos endpoints no banco (ip_start de endpoint_ip_samples)
    if (db && cables.length) {
        const cableIds = cables.map(c => c.cable_id);
        const res = await db.query(
            `SELECT ce.cable_id, eis.ip_start::text AS ip
             FROM cable_endpoints ce
             JOIN endpoint_ip_samples eis ON eis.endpoint_id = ce.endpoint_id
             WHERE ce.cable_id = ANY($1::text[])
             ORDER BY ce.cable_id, ce.endpoint_order, eis.sample_order`,
            [cableIds]
        ).catch(() => ({ rows: [] }));

        // Primeiro IP por cabo
        const ipByCable = {};
        for (const row of res.rows) {
            if (!ipByCable[row.cable_id]) ipByCable[row.cable_id] = row.ip;
        }

        for (const c of cables) {
            const ip = ipByCable[c.cable_id];
            if (ip) {
                targets.push({ id: c.cable_id, label: c.cable_name || c.cable_id, host: ip });
            } else {
                // Fallback: hostname do public_url quando não há endpoint IP registrado
                const h = extractHost(c.public_url);
                if (h) targets.push({ id: c.cable_id, label: c.cable_name || c.cable_id, host: h });
            }
        }
        const withIp = targets.filter(t => /^\d{1,3}\./.test(t.host)).length;
        log(`Modo cabo: ${targets.length} cabos — ${withIp} com IP real de endpoint, ${targets.length - withIp} com hostname`);
    } else {
        // Sem DB: usa hostname do public_url como fallback
        for (const c of cables) {
            const h = extractHost(c.public_url);
            if (h) targets.push({ id: c.cable_id, label: c.cable_name || c.cable_id, host: h });
        }
        log(`Modo cabo: ${targets.length} cabos (sem DB, usando public_url)`);
    }
    return targets;
}

// ── Main ───────────────────────────────────────────────────────────────────────
async function main() {
    const mode = IP_RANGE ? 'ip-range' : (IP_RANGES_FILE ? 'ip-ranges-file' : (IP_RANGES_DB ? 'ip-ranges-db' : 'cabo'));
    log('=== Sonda de Comutadores (MTR dedicado) ===');
    log(`Modo: ${mode} | Ciclos MTR: ${MTR_CYCLES} | Timeout: ${TIMEOUT_S}s | Step: ${IP_STEP}`);

    // Conexão DB (opcional)
    const dbUrl = process.env.DATABASE_URL;
    let db = null;
    if (dbUrl) {
        db = new Client({ connectionString: dbUrl, ssl: { rejectUnauthorized: false } });
        try { await db.connect(); ok('Banco conectado.'); await ensureSchema(db); }
        catch (e) { warn(`DB indisponível (${e.message}) — sem persistência.`); db = null; }
    } else {
        warn('DATABASE_URL não definido — salvando apenas em arquivo local.');
    }

    const targets = await buildTargets(db);
    if (!targets.length) { warn('Nenhum alvo para sondar. Use --ip-range, --ip-ranges-file, --ip-ranges-db ou --cables-file.'); return; }
    log(`${targets.length} alvos para sondar via MTR.`);

    const total       = targets.length;
    const sessionStart = new Date();
    const geoCache    = new Set();
    const registros   = [];
    let done = 0, found = 0;

    // Base para sample_id único: evita conflito na UNIQUE (sample_id, hop_number)
    const SESSION_BASE = Date.now();

    for (const t of targets) {
        const cableId   = t.id;
        const cableName = t.label;
        const host      = t.host;
        const measAt    = new Date(sessionStart.getTime() + done);
        const sampleId  = SESSION_BASE + done;   // inteiro único por alvo por sessão

        process.stdout.write(`  [${String(done+1).padStart(4)}/${total}]  ${cableId.padEnd(40)} mtr→${host}…`);

        const mr = await mtrProbe(host, MTR_CYCLES, TIMEOUT_S);

        if (!mr || !mr.hubs || !mr.hubs.length) {
            console.log(' sem resposta MTR');
            done++;
            continue;
        }

        // Classifica hops + constrói segmentos
        const hubs     = mr.hubs;
        const segments = buildTopologySegments(hubs);
        const visibleHops = hubs.filter(h => h.host && h.host !== '???' && (h['Loss%'] || 0) < 100);
        const publicIps   = visibleHops.map(h => h.host).filter(ip => !isPrivateIp(ip));
        found += publicIps.length;

        console.log(` ${hubs.length} hops  ${publicIps.length} públicos  rtt=${mr.rtt}ms`);

        // Geo lookup dos IPs novos
        const newIps = publicIps.filter(ip => !geoCache.has(ip));
        let geoMap   = {};
        if (newIps.length) {
            const geoRes = await geoLookupBatch(newIps);
            for (const g of geoRes) {
                if (g.status !== 'success' || !g.lat) continue;
                geoCache.add(g.query);
                geoMap[g.query] = g;
                if (db) {
                    await db.query(`
                        INSERT INTO hop_switch_geolocations
                            (hop_ip, lat, lng, city, region, country, country_code, isp, org, as_number, as_name)
                        VALUES ($1::inet,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                        ON CONFLICT (hop_ip) DO UPDATE SET
                            lat=EXCLUDED.lat, lng=EXCLUDED.lng, city=EXCLUDED.city,
                            country=EXCLUDED.country, isp=EXCLUDED.isp, queried_at=NOW()
                    `, [g.query, g.lat, g.lon, g.city, g.regionName, g.country,
                        g.countryCode, g.isp, g.org, g.as, g.asname]).catch(() => {});
                }
            }
        }

        // Persiste hops no banco
        if (db) {
            for (const [idx, h] of hubs.entries()) {
                const ip   = h.host && h.host !== '???' ? h.host : null;
                const role = classifyHopRole(h, idx, hubs);
                await db.query(`
                    INSERT INTO cable_telemetry_hops
                        (sample_id, cable_id, hop_number, hop_ip, rtt_avg_ms, rtt_best_ms,
                         rtt_worst_ms, rtt_stdev_ms, loss_pct, hop_role)
                    VALUES ($1,$2,$3,$4::inet,$5,$6,$7,$8,$9,$10)
                    ON CONFLICT (sample_id, hop_number) DO NOTHING
                `, [sampleId, cableId, h.count, ip, h.Avg||null, h.Best||null,
                    h.Wrst||null, h.StDev||null, h['Loss%']||0, role]).catch(() => {});
            }

            // Persiste segmentos de topologia
            for (const seg of segments) {
                await db.query(`
                    INSERT INTO probe_path_segments
                        (sample_id, cable_id, measured_at, segment_type, hop_number, hop_ip,
                         hop_count, rtt_avg_ms, rtt_best_ms, rtt_worst_ms, loss_pct, probe_agent)
                    VALUES ($1,$2,$3,$4,$5,$6::inet,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (sample_id, segment_type) DO NOTHING
                `, [sampleId, cableId, measAt, seg.segment_type, seg.hop_number, seg.hop_ip,
                    seg.hop_count, seg.rtt_avg_ms, seg.rtt_best_ms, seg.rtt_worst_ms,
                    seg.loss_pct, PROBE_AGENT]).catch(() => {});
            }
        }

        // Registro local completo
        const record = {
            cable_id:         cableId,
            cable_name:       cableName,
            measured_at:      measAt.toISOString(),
            probe_agent:      PROBE_AGENT,
            mtr_rtt_ms:       mr.rtt,
            mtr_hops:         hubs.length,
            mtr_cycles:       MTR_CYCLES,
            mtr_hubs_raw:     hubs,
            topology_segments: segments,
            gateway_rtt_ms:   segments.find(s => s.segment_type === 'gateway')?.rtt_avg_ms ?? null,
            isp_edge_rtt_ms:  segments.find(s => s.segment_type === 'isp_edge')?.rtt_avg_ms ?? null,
            isp_server_rtt_ms: segments.find(s => s.segment_type === 'isp_server')?.rtt_avg_ms ?? null,
            public_ips:       publicIps,
            geo:              Object.fromEntries(
                publicIps.filter(ip => geoMap[ip]).map(ip => [ip, {
                    city: geoMap[ip].city, country: geoMap[ip].country,
                    isp:  geoMap[ip].isp,  lat: geoMap[ip].lat, lng: geoMap[ip].lon,
                }])
            ),
        };
        registros.push(record);

        // JSONL incremental
        try { fs.appendFileSync(SWITCH_JSONL, JSON.stringify(record) + '\n', 'utf8'); } catch {}

        done++;
        writeLive({
            current: done, total, percent: r1((done / total) * 100),
            switches_found: found, updated_at: new Date().toISOString(),
            recent: registros.slice(-10).map(r => ({
                cable_id: r.cable_id, hops: r.mtr_hops, rtt_ms: r.mtr_rtt_ms,
                segments: r.topology_segments?.length ?? 0,
            })),
        });
    }

    // Aguarda geo queue
    if (db) await db.end().catch(() => {});

    // Salva switch_full_*.json
    if (registros.length) {
        const startTs  = sessionStart.toISOString().slice(11, 19).replace(/:/g, '');
        const fullFile = path.join(histDir, `${dateStr}_switch_full_${startTs}.json`);
        fs.writeFileSync(fullFile, JSON.stringify({
            gerado_em:   new Date().toISOString(),
            medido_em:   sessionStart.toISOString(),
            probe_agent: PROBE_AGENT,
            mtr_cycles:  MTR_CYCLES,
            totais: { cabos: registros.length, hops_publicos: found },
            registros,
        }, null, 2), 'utf8');
        ok(`switch_full salvo: ${path.basename(fullFile)} (${registros.length} cabos, ${found} IPs públicos)`);
    }

    console.log('');
    console.log(`Cabos sondados: ${done}/${total}`);
    console.log(`IPs públicos descobertos: ${found}`);
    console.log(`IPs geolocalizados: ${geoCache.size}`);
    ok('=== Sonda de Comutadores concluída ===');
}

main().catch(e => { console.error('[ERRO FATAL]', e.message); process.exit(0); });
