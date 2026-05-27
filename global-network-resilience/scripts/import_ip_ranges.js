'use strict';
/**
 * import_ip_ranges.js — Importa lista de IP ranges para ip_range_targets no DB
 *                        e grava data/ip_ranges.json como referência local.
 *
 * Uso:
 *   node scripts/import_ip_ranges.js --input data/minha_lista.json
 *   node scripts/import_ip_ranges.js --input data/minha_lista.json --clear
 *   node scripts/import_ip_ranges.js --list          # lista o que está no banco
 *
 * Formatos aceitos no JSON de entrada:
 *   // Simples (string):
 *   ["203.0.113.0/24", "198.51.100.0/22"]
 *
 *   // Objeto completo:
 *   [
 *     { "cidr": "203.0.113.0/24", "label": "ISP X", "country_code": "BR",
 *       "isp_name": "X Telecom", "priority": 1, "notes": "PoP São Paulo" }
 *   ]
 *
 *   // Misto — strings e objetos podem coexistir na mesma lista
 */

const { Client } = require('pg');
const fs   = require('fs');
const path = require('path');

const argv    = process.argv.slice(2);
const hasFlag = (f) => argv.includes(f);
const getArg  = (f, d) => { const i = argv.indexOf(f); return i !== -1 && argv[i+1] ? argv[i+1] : d; };

const INPUT_FILE = getArg('--input',  '');
const OUT_FILE   = getArg('--out',    path.resolve(__dirname, '..', 'data', 'ip_ranges.json'));
const CLEAR      = hasFlag('--clear');   // apaga todos os ranges antes de inserir
const LIST_ONLY  = hasFlag('--list');    // apenas lista o conteúdo atual do banco

// ── Logging ───────────────────────────────────────────────────────────────────
const ts   = () => new Date().toISOString().slice(11, 19);
const log  = (...a) => console.log(`[${ts()}]`, ...a);
const ok   = (...a) => console.log(`[${ts()}] ✔`, ...a);
const warn = (...a) => console.log(`[${ts()}] ⚠`, ...a);
const err  = (...a) => console.error(`[${ts()}] ✖`, ...a);

// ── Valida e normaliza CIDR ───────────────────────────────────────────────────
function validCidr(cidr) {
    if (!cidr || typeof cidr !== 'string') return false;
    const [ip, bits] = cidr.trim().split('/');
    if (!ip || bits === undefined) return false;
    const prefix = parseInt(bits, 10);
    if (isNaN(prefix) || prefix < 0 || prefix > 32) return false;
    const parts = ip.split('.');
    if (parts.length !== 4) return false;
    return parts.every(p => { const n = parseInt(p, 10); return !isNaN(n) && n >= 0 && n <= 255; });
}

// Masks off host bits so PostgreSQL CIDR type accepts the value
function normalizeCidr(cidr) {
    const [ip, bits] = cidr.split('/');
    const prefix = parseInt(bits, 10);
    const parts   = ip.split('.').map(Number);
    const addr    = ((parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]) >>> 0;
    const mask    = prefix === 0 ? 0 : (~0 << (32 - prefix)) >>> 0;
    const net     = (addr & mask) >>> 0;
    return `${(net >>> 24) & 0xFF}.${(net >>> 16) & 0xFF}.${(net >>> 8) & 0xFF}.${net & 0xFF}/${prefix}`;
}

// ── Normaliza entrada ─────────────────────────────────────────────────────────
function normalizeEntries(raw, metaSource) {
    const entries = [];

    // Country-keyed format: { countries: { "BR": { country, iso2, rir, ipv4_ranges: [] } } }
    if (raw && typeof raw === 'object' && !Array.isArray(raw) && raw.countries && typeof raw.countries === 'object') {
        const source = (raw._meta && (raw._meta.source || raw._meta.description)) || metaSource || 'IANA/RIR';
        for (const [iso2, info] of Object.entries(raw.countries)) {
            if (!info || !Array.isArray(info.ipv4_ranges)) continue;
            const label    = info.country || iso2;
            const cc       = (info.iso2 || iso2).toUpperCase().slice(0, 2);
            const rir      = info.rir || null;
            const notes    = rir ? `RIR: ${rir} | Fonte: ${source}` : `Fonte: ${source}`;
            for (const cidr of info.ipv4_ranges) {
                if (typeof cidr !== 'string') continue;
                const c = cidr.trim();
                if (!validCidr(c)) { warn(`CIDR inválido ignorado (${cc}): ${c}`); continue; }
                entries.push({ cidr: normalizeCidr(c), label, country_code: cc, isp_name: rir || null, priority: 5, notes });
            }
        }
        return entries;
    }

    // Standard formats: plain array, or wrapper objects
    const list = Array.isArray(raw) ? raw : (raw.ranges || raw.ip_ranges || raw.data || []);
    for (const item of list) {
        let entry;
        if (typeof item === 'string') {
            entry = { cidr: item.trim() };
        } else if (item && typeof item === 'object') {
            entry = { ...item };
            if (!entry.cidr && entry.range) entry.cidr = entry.range;
            if (!entry.cidr && entry.subnet) entry.cidr = entry.subnet;
        } else {
            continue;
        }
        if (!entry.cidr) { warn(`Entrada sem CIDR ignorada: ${JSON.stringify(item)}`); continue; }
        entry.cidr = entry.cidr.trim();
        if (!validCidr(entry.cidr)) { warn(`CIDR inválido ignorado: ${entry.cidr}`); continue; }
        entry.cidr = normalizeCidr(entry.cidr);
        entries.push(entry);
    }
    return entries;
}

// ── Conecta DB ────────────────────────────────────────────────────────────────
async function connectDb() {
    const dbUrl = process.env.DATABASE_URL;
    if (!dbUrl) { warn('DATABASE_URL não definido — operação sem DB.'); return null; }
    const db = new Client({ connectionString: dbUrl, ssl: { rejectUnauthorized: false } });
    await db.connect();
    // Garante que a tabela existe
    await db.query(`
        CREATE TABLE IF NOT EXISTS ip_range_targets (
            id           SERIAL PRIMARY KEY,
            cidr         CIDR NOT NULL UNIQUE,
            label        VARCHAR(120),
            country_code CHAR(2),
            isp_name     VARCHAR(200),
            priority     SMALLINT DEFAULT 5,
            active       BOOLEAN DEFAULT TRUE,
            notes        TEXT,
            created_at   TIMESTAMPTZ DEFAULT NOW()
        )
    `);
    return db;
}

// ── Lista ranges do banco ─────────────────────────────────────────────────────
async function listRanges(db) {
    if (!db) { warn('Sem conexão DB.'); return; }
    const res = await db.query(
        `SELECT id, cidr::text AS cidr, label, country_code, isp_name, priority, active, notes
         FROM ip_range_targets ORDER BY priority, id`
    );
    if (!res.rows.length) { log('Tabela ip_range_targets está vazia.'); return; }
    console.log('\n  ID  CIDR                  PRIO  ATIVO  LABEL');
    console.log('  ' + '─'.repeat(68));
    for (const r of res.rows) {
        const prio  = String(r.priority).padEnd(4);
        const ativo = r.active ? 'sim' : 'não';
        const cidr  = (r.cidr || '').padEnd(22);
        const label = r.label || '—';
        console.log(`  ${String(r.id).padEnd(4)}${cidr}  ${prio}  ${ativo}    ${label}`);
    }
    console.log(`\n  Total: ${res.rows.length} ranges\n`);
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
    log('=== import_ip_ranges.js ===');

    let db = null;
    try { db = await connectDb(); if (db) ok('Banco conectado.'); }
    catch (e) { warn(`DB indisponível: ${e.message}`); db = null; }

    if (LIST_ONLY) {
        await listRanges(db);
        if (db) await db.end();
        return;
    }

    if (!INPUT_FILE) {
        err('Informe o arquivo de entrada: --input <arquivo.json>');
        console.log('\nExemplo:\n  node scripts/import_ip_ranges.js --input data/minha_lista.json');
        process.exit(1);
    }

    // Lê e normaliza entradas
    const rawText = fs.readFileSync(path.resolve(INPUT_FILE), 'utf8');
    const raw     = JSON.parse(rawText);
    const entries = normalizeEntries(raw);
    if (!entries.length) { err('Nenhum CIDR válido encontrado no arquivo.'); process.exit(1); }
    log(`${entries.length} CIDRs válidos encontrados.`);

    // Limpa tabela se pedido
    if (CLEAR && db) {
        await db.query('DELETE FROM ip_range_targets');
        ok('Tabela ip_range_targets limpa.');
    }

    // Insere no banco
    let inserted = 0, updated = 0, skipped = 0;
    if (db) {
        for (const e of entries) {
            try {
                const res = await db.query(`
                    INSERT INTO ip_range_targets (cidr, label, country_code, isp_name, priority, notes)
                    VALUES ($1::cidr, $2, $3, $4, $5, $6)
                    ON CONFLICT (cidr) DO UPDATE SET
                        label        = COALESCE(EXCLUDED.label,        ip_range_targets.label),
                        country_code = COALESCE(EXCLUDED.country_code, ip_range_targets.country_code),
                        isp_name     = COALESCE(EXCLUDED.isp_name,     ip_range_targets.isp_name),
                        priority     = COALESCE(EXCLUDED.priority,      ip_range_targets.priority),
                        notes        = COALESCE(EXCLUDED.notes,         ip_range_targets.notes)
                    RETURNING (xmax = 0) AS was_inserted
                `, [
                    e.cidr,
                    e.label        || null,
                    e.country_code || null,
                    e.isp_name     || null,
                    e.priority     ?? 5,
                    e.notes        || null,
                ]);
                if (res.rows[0]?.was_inserted) inserted++; else updated++;
            } catch (dbErr) {
                warn(`Falha ao inserir ${e.cidr}: ${dbErr.message}`);
                skipped++;
            }
        }
        ok(`DB: ${inserted} inseridos, ${updated} atualizados, ${skipped} falhas.`);
    }

    // Grava ip_ranges.json local
    const outDir = path.dirname(OUT_FILE);
    if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
    const outData = {
        gerado_em:  new Date().toISOString(),
        fonte:      path.basename(INPUT_FILE),
        total:      entries.length,
        ip_ranges:  entries.map(e => ({
            cidr:         e.cidr,
            label:        e.label        || null,
            country_code: e.country_code || null,
            isp_name:     e.isp_name     || null,
            priority:     e.priority     ?? 5,
            notes:        e.notes        || null,
        })),
    };
    fs.writeFileSync(OUT_FILE, JSON.stringify(outData, null, 2), 'utf8');
    ok(`JSON local salvo: ${OUT_FILE}`);

    if (db) {
        await listRanges(db);
        await db.end();
    }

    log('Concluído.');
}

main().catch(e => { err('ERRO FATAL:', e.message); process.exit(1); });
