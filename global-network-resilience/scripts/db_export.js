'use strict';
const { Client } = require('pg');
const fs   = require('fs');
const path = require('path');

const argv    = process.argv.slice(2);
const hasFlag = (f) => argv.includes(f);
const getArg  = (f, d) => { const i = argv.indexOf(f); return i !== -1 && argv[i + 1] ? argv[i + 1] : d; };

const DATABASE_URL = process.env.DATABASE_URL || getArg('--db', '');
const DATE_STR     = getArg('--date', new Date().toISOString().slice(0, 10));
const OUT_DIR      = path.resolve(getArg('--out', path.join(__dirname, '..', 'data', 'local', 'db_export', DATE_STR)));
const PRETTY       = !hasFlag('--compact');

if (!DATABASE_URL) {
    console.error('Erro: DATABASE_URL não definida.');
    console.error('Use: DATABASE_URL="postgresql://..." node scripts/db_export.js');
    console.error('  ou: node scripts/db_export.js --db "postgresql://..."');
    process.exit(1);
}

// Colunas preferenciais por tabela — garante ordenação lógica dos campos no JSON
// (PostgreSQL não garante ordem de colunas sem SELECT explícito)
const SAMPLE_COLS = `
    sample_id, cable_id, measured_at, sent_at, received_at,
    latency_ms, jitter_ms, packet_loss_pct, throughput_mbps,
    packets_per_second, availability_pct, route_error_count,
    source, method, confidence_score, estimated, telemetry_model_version,
    -- rastreabilidade (equivalente ao probe_full local)
    open_ports, packets_per_route, probe_agent,
    -- por protocolo bruto
    mtr_rtt_ms, mtr_hops, mtr_hubs_raw,
    tcp_ports_open, tcp_rtt_ms,
    tcp_jitter_mean_ms, tcp_jitter_p95_ms, tcp_jitter_loss_pct, tcp_jitter_samples,
    http_rtt_ms,
    -- topologia de rede
    gateway_rtt_ms, isp_edge_rtt_ms, isp_server_rtt_ms,
    metadata
`.replace(/\s*--[^\n]*/g, '').replace(/\s+/g, ' ').trim();

const TABLES = [
    { name: 'cables',                    order: 'cable_id' },
    { name: 'cable_metadata',            order: 'cable_id' },
    { name: 'cable_landing_points',      order: 'cable_id, endpoint_id' },
    { name: 'landing_points',            order: 'endpoint_id' },
    { name: 'endpoints',                 order: 'cable_id, endpoint_id' },
    { name: 'cable_telemetry_samples',   order: 'cable_id, measured_at', cols: SAMPLE_COLS },
    { name: 'cable_telemetry_hops',      order: 'hop_id' },
    { name: 'cable_telemetry_reference', order: 'cable_id' },
    { name: 'endpoint_ip_samples',       order: 'endpoint_id, sample_order' },
    { name: 'hop_switch_geolocations',   order: 'hop_ip' },
    { name: 'probe_path_segments',       order: 'segment_id' },
    { name: 'telemetry_rollup_1h',       order: 'cable_id, bucket' },
    { name: 'telemetry_rollup_5m',       order: 'cable_id, bucket' },
];

async function exportTable(db, t, now) {
    process.stdout.write('  ' + t.name.padEnd(35) + '...');
    const cols = t.cols || '*';
    // Colunas com SELECT explícito podem não existir ainda (novas migrações) — fallback para *
    let res;
    try {
        res = await db.query(`SELECT ${cols} FROM ${t.name} ORDER BY ${t.order}`);
    } catch {
        res = await db.query(`SELECT * FROM ${t.name} ORDER BY ${t.order}`);
    }
    const out = {
        tabela:       t.name,
        exportado_em: now,
        total:        res.rows.length,
        registros:    res.rows,
    };
    const file = path.join(OUT_DIR, t.name + '.json');
    fs.writeFileSync(file, PRETTY ? JSON.stringify(out, null, 2) : JSON.stringify(out), 'utf8');
    const kb = (fs.statSync(file).size / 1024).toFixed(1);
    console.log(res.rows.length.toString().padStart(7) + ' registros  (' + kb + ' KB)');
    return { tabela: t.name, registros: res.rows.length, arquivo: t.name + '.json', tamanho_kb: parseFloat(kb) };
}

async function main() {
    console.log('=== DB Export RMIFO ===');
    console.log('Data:    ', DATE_STR);
    console.log('Destino: ', OUT_DIR);
    console.log('');

    fs.mkdirSync(OUT_DIR, { recursive: true });

    const db = new Client({ connectionString: DATABASE_URL, ssl: { rejectUnauthorized: false } });
    await db.connect();
    console.log('Banco conectado. Exportando', TABLES.length, 'tabelas...\n');

    const now = new Date().toISOString();
    const manifest = { exportado_em: now, date: DATE_STR, tabelas: [] };
    const skipped  = [];

    for (const t of TABLES) {
        // Verifica se tabela existe antes de tentar exportar
        const exists = await db.query(
            `SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=$1`,
            [t.name]
        );
        if (!exists.rows.length) {
            console.log('  ' + t.name.padEnd(35) + '... (tabela não existe, pulando)');
            skipped.push(t.name);
            continue;
        }
        const entry = await exportTable(db, t, now);
        manifest.tabelas.push(entry);
    }

    const total_kb = manifest.tabelas.reduce((s, t) => s + t.tamanho_kb, 0).toFixed(1);
    manifest.total_kb      = parseFloat(total_kb);
    manifest.total_registros = manifest.tabelas.reduce((s, t) => s + t.registros, 0);
    manifest.tabelas_puladas = skipped;

    fs.writeFileSync(path.join(OUT_DIR, '_manifest.json'), JSON.stringify(manifest, null, 2), 'utf8');

    console.log('');
    console.log('Resumo:');
    console.log('  Tabelas exportadas:', manifest.tabelas.length);
    console.log('  Tabelas puladas:   ', skipped.length, skipped.length ? '(' + skipped.join(', ') + ')' : '');
    console.log('  Total registros:   ', manifest.total_registros.toLocaleString());
    console.log('  Tamanho total:     ', total_kb, 'KB');
    console.log('  Manifesto:         ', path.join(OUT_DIR, '_manifest.json'));
    console.log('');
    console.log('Export concluído.');

    await db.end();
}

main().catch(e => {
    console.error('[ERRO]', e.message);
    process.exit(1);
});
