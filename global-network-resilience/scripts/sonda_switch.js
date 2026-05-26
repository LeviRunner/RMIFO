'use strict';
/**
 * sonda_switch.js — Sonda de Comutadores (MTR dedicado)
 *
 * Mapeia a topologia de rede ponto a ponto para cada cabo submarino usando MTR.
 * Classifica cada salto: gateway → isp_edge → isp_transit → isp_server.
 * Geoloaliza IPs públicos e armazena em cable_telemetry_hops + probe_path_segments.
 *
 * Requer: sudo mtr OU  sudo setcap cap_net_raw+ep $(which mtr)
 *
 * Uso:
 *   sudo node scripts/sonda_switch.js --cables-file data/cables_list.json
 *   sudo node scripts/sonda_switch.js --cables-file data/cables_list.json --mtr-cycles 20 --limit 50
 *   sudo node scripts/sonda_switch.js --cable 2africa --mtr-cycles 30
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

const MTR_CYCLES  = parseInt(getArg('--mtr-cycles', '20'), 10);
const TIMEOUT_S   = parseInt(getArg('--timeout',    '30'), 10);
const LIMIT       = parseInt(getArg('--limit',      '999'), 10);
const CABLES_FILE = getArg('--cables-file', '');
const SINGLE      = getArg('--cable', '');           // sonda apenas um cabo

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
        setTimeout(() => { try { proc.kill(); } catch {} res(null); }, (timeoutS + 20) * 1000);
    });
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

// ── Main ───────────────────────────────────────────────────────────────────────
async function main() {
    log('=== Sonda de Comutadores (MTR dedicado) ===');
    log(`Ciclos MTR: ${MTR_CYCLES} | Timeout: ${TIMEOUT_S}s`);

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

    // Carrega lista de cabos
    let cables = [];
    if (CABLES_FILE) {
        cables = JSON.parse(fs.readFileSync(path.resolve(CABLES_FILE), 'utf8'));
    } else if (db) {
        const res = await db.query(
            `SELECT cable_id, cable_name, length_km, capacity_gbps,
                    landing_points_count, public_url
             FROM cables WHERE public_url IS NOT NULL ORDER BY cable_id`
        );
        cables = res.rows;
    }

    if (SINGLE) cables = cables.filter(c => c.cable_id === SINGLE);
    cables = cables.filter(c => extractHost(c.public_url)).slice(0, LIMIT);

    if (!cables.length) { warn('Nenhum cabo com URL para sondar.'); return; }
    log(`${cables.length} cabos para sondar via MTR.`);

    const total       = cables.length;
    const sessionStart = new Date();
    const geoCache    = new Set();
    const registros   = [];
    let done = 0, found = 0;

    for (const c of cables) {
        const cableId   = c.cable_id;
        const cableName = c.cable_name || cableId;
        const host      = extractHost(c.public_url);
        const measAt    = new Date(sessionStart.getTime() + done);

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
                        (cable_id, hop_number, hop_ip, rtt_avg_ms, rtt_best_ms,
                         rtt_worst_ms, rtt_stdev_ms, loss_pct, hop_role)
                    VALUES ($1,$2,$3::inet,$4,$5,$6,$7,$8,$9)
                    ON CONFLICT (sample_id, hop_number) DO NOTHING
                `, [cableId, h.count, ip, h.Avg||null, h.Best||null,
                    h.Wrst||null, h.StDev||null, h['Loss%']||0, role]).catch(() => {});
            }

            // Persiste segmentos de topologia
            for (const seg of segments) {
                await db.query(`
                    INSERT INTO probe_path_segments
                        (cable_id, measured_at, segment_type, hop_number, hop_ip,
                         hop_count, rtt_avg_ms, rtt_best_ms, rtt_worst_ms, loss_pct, probe_agent)
                    VALUES ($1,$2,$3,$4,$5::inet,$6,$7,$8,$9,$10,$11)
                    ON CONFLICT DO NOTHING
                `, [cableId, measAt, seg.segment_type, seg.hop_number, seg.hop_ip,
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
