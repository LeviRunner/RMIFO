#!/usr/bin/env python3
"""
compare_report.py — Compara resultados da sonda rodando no Codespace (arquivos locais)
                    versus sonda ativada pelo site/servidor (dados do banco de dados).

Uso:
    python3 scripts/compare_report.py
    python3 scripts/compare_report.py --out data/local/relatorios/

Saídas:
    compare_report_YYYY-MM-DD.txt   — relatório legível
    compare_report_YYYY-MM-DD.json  — dados brutos da comparação
"""

import json, os, glob, sys, math
from collections import defaultdict, Counter
from datetime import datetime

# ── Argumentos simples ───────────────────────────────────────────────────────
args = sys.argv[1:]
get_arg = lambda f, d: args[args.index(f)+1] if f in args else d

BASE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR    = get_arg('--out', os.path.join(BASE, 'data', 'local', 'relatorios'))
TODAY      = datetime.utcnow().strftime('%Y-%m-%d')
DB_EXPORT  = os.path.join(BASE, 'data', 'local', 'db_export')
LOCAL_HIST = os.path.join(BASE, 'data', 'local', 'historico')

os.makedirs(OUT_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CARREGA DADOS
# ═══════════════════════════════════════════════════════════════════════════════

def load_local_probes():
    """Carrega todos os probe_full_*.json do codespace."""
    records = []
    for f in sorted(glob.glob(os.path.join(LOCAL_HIST, '**', '*_probe_full_*.json'), recursive=True)):
        try:
            d = json.load(open(f))
            date = os.path.basename(f)[:10]
            for r in d.get('registros', []):
                r['_fonte'] = 'codespace'
                r['_arquivo'] = os.path.relpath(f, BASE)
                r['_data'] = r.get('measured_at', '')[:10] or date
                records.append(r)
        except Exception as e:
            print(f'  [aviso] {f}: {e}')
    return records

def load_db_samples():
    """Carrega cable_telemetry_samples.json do export do banco."""
    records = []
    for exp_dir in sorted(glob.glob(os.path.join(DB_EXPORT, '*'))):
        f = os.path.join(exp_dir, 'cable_telemetry_samples.json')
        if not os.path.exists(f):
            continue
        try:
            d = json.load(open(f))
            for r in d.get('registros', []):
                r['_fonte'] = 'servidor'
                r['_arquivo'] = os.path.relpath(f, BASE)
                r['_data'] = r.get('measured_at', '')[:10]
                records.append(r)
        except Exception as e:
            print(f'  [aviso] {f}: {e}')
    return records

def load_db_hops():
    """Carrega cable_telemetry_hops.json do export."""
    for exp_dir in sorted(glob.glob(os.path.join(DB_EXPORT, '*'))):
        f = os.path.join(exp_dir, 'cable_telemetry_hops.json')
        if os.path.exists(f):
            return json.load(open(f)).get('registros', [])
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. HELPERS ESTATÍSTICOS
# ═══════════════════════════════════════════════════════════════════════════════

def mean(vals):
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 2) if v else None

def median(vals):
    v = sorted(x for x in vals if x is not None)
    if not v: return None
    m = len(v) // 2
    return round((v[m-1]+v[m])/2 if len(v) % 2 == 0 else v[m], 2)

def stdev(vals):
    v = [x for x in vals if x is not None]
    if len(v) < 2: return None
    mu = sum(v) / len(v)
    return round(math.sqrt(sum((x-mu)**2 for x in v) / len(v)), 2)

def pct(num, total):
    return round(num / total * 100, 1) if total else 0

def proto_class(method):
    m = (method or '').lower()
    if 'mtr' in m:       return 'MTR'
    if 'jitter' in m:    return 'TCP-Jitter'
    if 'tcp' in m:       return 'TCP-SYN'
    if 'http' in m:      return 'HTTP'
    if 'synthetic' in m or 'heuristic' in m: return 'Sintético'
    return 'Outro'

def summarize_group(records):
    lats   = [r.get('latency_ms') for r in records]
    protos = Counter(proto_class(r.get('method','')) for r in records)
    srcs   = Counter(r.get('source','') for r in records)
    est    = sum(1 for r in records if r.get('estimated'))
    mtr_hops = [r.get('mtr_hops') for r in records if r.get('mtr_hops')]
    return {
        'total':        len(records),
        'estimados':    est,
        'lat_media':    mean(lats),
        'lat_mediana':  median(lats),
        'lat_desvio':   stdev(lats),
        'lat_min':      min((x for x in lats if x), default=None),
        'lat_max':      max((x for x in lats if x), default=None),
        'protocolos':   dict(protos.most_common()),
        'sources':      dict(srcs.most_common()),
        'mtr_hops_media': mean(mtr_hops),
        'cabos_unicos': len(set(r.get('cable_id') for r in records)),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. COMPARAÇÃO POR CABO (datas em comum)
# ═══════════════════════════════════════════════════════════════════════════════

def compare_cables(local_recs, db_recs):
    """
    Para cada data em que ambas as fontes têm dados, compara cabo a cabo.
    Retorna divergências e estatísticas de delta de latência.
    """
    local_by_date = defaultdict(dict)
    for r in local_recs:
        local_by_date[r['_data']][r['cable_id']] = r

    db_by_date = defaultdict(dict)
    for r in db_recs:
        db_by_date[r['_data']][r['cable_id']] = r

    datas_comuns = sorted(set(local_by_date) & set(db_by_date))
    resultado = []

    for data in datas_comuns:
        local_d = local_by_date[data]
        db_d    = db_by_date[data]
        cabos_comuns = sorted(set(local_d) & set(db_d))
        so_local = sorted(set(local_d) - set(db_d))
        so_db    = sorted(set(db_d) - set(local_d))

        deltas = []
        divergencias = []
        for cable_id in cabos_comuns:
            lr = local_d[cable_id]
            dr = db_d[cable_id]
            lat_l = lr.get('latency_ms')
            lat_d = dr.get('latency_ms')
            proto_l = proto_class(lr.get('method',''))
            proto_d = proto_class(dr.get('method',''))
            delta = round(abs(lat_l - lat_d), 2) if lat_l and lat_d else None
            if delta is not None:
                deltas.append(delta)
            if proto_l != proto_d or (delta and delta > 50):
                divergencias.append({
                    'cable_id':     cable_id,
                    'proto_local':  proto_l,
                    'proto_db':     proto_d,
                    'lat_local_ms': lat_l,
                    'lat_db_ms':    lat_d,
                    'delta_ms':     delta,
                })

        resultado.append({
            'data':             data,
            'cabos_comuns':     len(cabos_comuns),
            'so_codespace':     len(so_local),
            'so_servidor':      len(so_db),
            'delta_lat_media':  mean(deltas),
            'delta_lat_max':    max(deltas, default=None),
            'divergencias':     sorted(divergencias, key=lambda x: x['delta_ms'] or 0, reverse=True),
        })

    return resultado, datas_comuns


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CAMPOS EXCLUSIVOS POR FONTE
# ═══════════════════════════════════════════════════════════════════════════════

def field_diff(local_recs, db_recs):
    local_fields = set()
    db_fields    = set()
    if local_recs: local_fields = set(k for k in local_recs[0] if not k.startswith('_'))
    if db_recs:    db_fields    = set(k for k in db_recs[0] if not k.startswith('_'))
    return {
        'so_codespace': sorted(local_fields - db_fields),
        'so_servidor':  sorted(db_fields - local_fields),
        'comuns':       sorted(local_fields & db_fields),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ANÁLISE DE HOPS (só servidor tem DB de hops)
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_hops(hops):
    if not hops: return {}
    cables_c = Counter(h['cable_id'] for h in hops)
    hop_nums  = [h.get('hop_number',0) for h in hops]
    roles     = Counter(h.get('hop_role') for h in hops if h.get('hop_role'))
    rtts      = [h.get('rtt_avg_ms') for h in hops if h.get('rtt_avg_ms')]
    return {
        'total_hops':       len(hops),
        'cabos_com_hops':   len(cables_c),
        'hop_max':          max(hop_nums, default=0),
        'hop_medio':        round(sum(hop_nums)/len(hop_nums),1),
        'roles':            dict(roles.most_common()),
        'rtt_medio_ms':     mean(rtts),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. GERA RELATÓRIO TEXTO
# ═══════════════════════════════════════════════════════════════════════════════

def fmt(v, unit=''):
    if v is None: return '—'
    return f'{v}{unit}'

def write_report(path, local_recs, db_recs, compare, datas_comuns, fields, hops_info):
    local_all = summarize_group(local_recs)
    db_all    = summarize_group(db_recs)

    local_by_date = defaultdict(list)
    for r in local_recs: local_by_date[r['_data']].append(r)
    db_by_date = defaultdict(list)
    for r in db_recs: db_by_date[r['_data']].append(r)

    lines = []
    W = 80
    def h1(t): lines.append('=' * W); lines.append(f'  {t}'); lines.append('=' * W)
    def h2(t): lines.append(''); lines.append(f'── {t} ' + '─' * max(0, W-4-len(t)))
    def row(label, v1, v2=''):
        lines.append(f'  {label:<35} {str(v1):<20} {v2}')
    def sep(): lines.append('  ' + '-'*76)

    h1(f'RELATÓRIO DE COMPARAÇÃO — CODESPACE vs SERVIDOR  ({TODAY})')
    lines.append(f'  Gerado em: {datetime.utcnow().isoformat()}Z')
    lines.append(f'  Datas com dados em ambas as fontes: {", ".join(datas_comuns) or "nenhuma"}')

    # ── Resumo geral ──────────────────────────────────────────────────────────
    h2('RESUMO GERAL')
    lines.append(f'  {"Métrica":<35} {"Codespace":^20} {"Servidor (DB)":^20}')
    sep()
    row('Total de registros',    local_all["total"],         db_all["total"])
    row('Cabos únicos',          local_all["cabos_unicos"],  db_all["cabos_unicos"])
    row('Registros estimados',   f'{local_all["estimados"]} ({pct(local_all["estimados"],local_all["total"])}%)',
                                 f'{db_all["estimados"]} ({pct(db_all["estimados"],db_all["total"])}%)')
    row('Latência média (ms)',   fmt(local_all["lat_media"]), fmt(db_all["lat_media"]))
    row('Latência mediana (ms)', fmt(local_all["lat_mediana"]), fmt(db_all["lat_mediana"]))
    row('Latência desvio (ms)',  fmt(local_all["lat_desvio"]), fmt(db_all["lat_desvio"]))
    row('Latência mín (ms)',     fmt(local_all["lat_min"]),  fmt(db_all["lat_min"]))
    row('Latência máx (ms)',     fmt(local_all["lat_max"]),  fmt(db_all["lat_max"]))
    row('Hops MTR médios',       fmt(local_all["mtr_hops_media"]), fmt(db_all["mtr_hops_media"]))

    # ── Datas disponíveis ─────────────────────────────────────────────────────
    h2('DATAS DISPONÍVEIS')
    all_dates = sorted(set(local_by_date) | set(db_by_date))
    lines.append(f'  {"Data":<15} {"Codespace":>12} {"Servidor":>12} {"Status":>15}')
    sep()
    for d in all_dates:
        l = len(local_by_date.get(d, []))
        s = len(db_by_date.get(d, []))
        status = 'AMBOS' if l and s else ('só-codespace' if l else 'só-servidor')
        lines.append(f'  {d:<15} {str(l):>12} {str(s):>12} {status:>15}')

    # ── Protocolos por fonte ──────────────────────────────────────────────────
    h2('DISTRIBUIÇÃO DE PROTOCOLOS')
    all_protos = sorted(set(local_all["protocolos"]) | set(db_all["protocolos"]))
    lines.append(f'  {"Protocolo":<20} {"Codespace":>12}  {"(%)":>6}  {"Servidor":>12}  {"(%)":>6}')
    sep()
    for p in all_protos:
        lc = local_all["protocolos"].get(p, 0)
        dc = db_all["protocolos"].get(p, 0)
        lines.append(f'  {p:<20} {lc:>12}  {pct(lc,local_all["total"]):>5}%  {dc:>12}  {pct(dc,db_all["total"]):>5}%')

    # ── Sources por fonte ─────────────────────────────────────────────────────
    h2('FONTES DE MEDIÇÃO (source)')
    all_srcs = sorted(set(local_all["sources"]) | set(db_all["sources"]))
    lines.append(f'  {"Source":<30} {"Codespace":>12}  {"Servidor":>12}')
    sep()
    for s in all_srcs:
        lc = local_all["sources"].get(s, 0)
        dc = db_all["sources"].get(s, 0)
        lines.append(f'  {s:<30} {lc:>12}  {dc:>12}')

    # ── Comparação por data (cabos comuns) ────────────────────────────────────
    h2('COMPARAÇÃO POR DATA (cabos em comum)')
    if compare:
        lines.append(f'  {"Data":<12} {"Comuns":>8} {"Só-CS":>7} {"Só-SV":>7} {"Δlat.média":>12} {"Δlat.max":>10} {"Divergências":>13}')
        sep()
        for c in compare:
            lines.append(
                f'  {c["data"]:<12} {c["cabos_comuns"]:>8} {c["so_codespace"]:>7} {c["so_servidor"]:>7} '
                f'{fmt(c["delta_lat_media"]," ms"):>12} {fmt(c["delta_lat_max"]," ms"):>10} '
                f'{len(c["divergencias"]):>13}'
            )
    else:
        lines.append('  Nenhuma data em comum entre as duas fontes.')

    # ── Top divergências ──────────────────────────────────────────────────────
    h2('TOP DIVERGÊNCIAS (Δlatência > 50 ms ou protocolo diferente)')
    all_divs = []
    for c in compare:
        for d in c['divergencias']:
            all_divs.append({**d, 'data': c['data']})
    all_divs = sorted(all_divs, key=lambda x: x.get('delta_ms') or 0, reverse=True)[:30]

    if all_divs:
        lines.append(f'  {"Cabo":<40} {"Data":<12} {"Proto-CS":<14} {"Proto-SV":<14} {"Δms":>8}')
        sep()
        for d in all_divs:
            lines.append(
                f'  {d["cable_id"][:39]:<40} {d["data"]:<12} '
                f'{d["proto_local"]:<14} {d["proto_db"]:<14} '
                f'{fmt(d["delta_ms"]," ms"):>8}'
            )
    else:
        lines.append('  Nenhuma divergência significativa encontrada nas datas em comum.')

    # ── Campos exclusivos ─────────────────────────────────────────────────────
    h2('CAMPOS EXCLUSIVOS POR FONTE')
    lines.append('  Só no Codespace (probe_full local):')
    for f in fields['so_codespace']:
        lines.append(f'    + {f}')
    lines.append('')
    lines.append('  Só no Servidor (banco de dados):')
    for f in fields['so_servidor']:
        lines.append(f'    + {f}')
    lines.append('')
    lines.append(f'  Campos em comum: {len(fields["comuns"])}')

    # ── Análise de hops MTR ───────────────────────────────────────────────────
    h2('ANÁLISE DE HOPS MTR (apenas servidor tem tabela de hops)')
    if hops_info:
        for k, v in hops_info.items():
            lines.append(f'  {k:<30} {v}')
    else:
        lines.append('  Dados de hops não disponíveis.')

    # ── Campos novos (topology) ───────────────────────────────────────────────
    h2('CAMPOS DE TOPOLOGIA (novos — probe_full 2026-05-26+)')
    topo_recs = [r for r in local_recs if r.get('topology_segments') is not None]
    if topo_recs:
        lines.append(f'  Registros com topology_segments: {len(topo_recs)}')
        seg_types = Counter(s['segment_type'] for r in topo_recs for s in (r['topology_segments'] or []))
        for t, c in seg_types.most_common():
            lines.append(f'    {t:<20} {c} segmentos')
    else:
        lines.append('  Ainda não há registros com topology_segments nos probe_full locais.')
        lines.append('  (campo disponível a partir da próxima sonda completa com MTR/root)')

    # ── Conclusão ─────────────────────────────────────────────────────────────
    h2('CONCLUSÃO')
    lines.append(f"""
  Codespace:
    • Roda sem banco de dados, salva probe_full_*.json localmente
    • Protocolo dominante: {max(local_all["protocolos"], key=local_all["protocolos"].get, default="—")}
    • Sem privilégios de root → MTR indisponível → mais registros sintéticos
    • Campos adicionais por protocolo (topology_segments, mtr_hubs) no JSON local

  Servidor (DB):
    • Roda com banco de dados (Railway PostgreSQL)
    • Protocolo dominante: {max(db_all["protocolos"], key=db_all["protocolos"].get, default="—")}
    • MTR disponível ({db_all["protocolos"].get("MTR", 0)} registros com MTR de {db_all["total"]} total)
    • {db_all["cabos_unicos"]} cabos únicos persistidos ao longo de {len(db_by_date)} datas
    • Hops individuais salvos em cable_telemetry_hops ({hops_info.get("total_hops",0)} linhas)

  Diferença principal: root/sudo no servidor permite MTR, gerando dados de hops
  mais ricos. O codespace usa TCP-Jitter e HTTP como substitutos com boa precisão.
""")

    lines.append('=' * W)
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print('Carregando dados do Codespace (probe_full locais)...')
    local_recs = load_local_probes()
    print(f'  {len(local_recs)} registros em {len(set(r["_data"] for r in local_recs))} datas')

    print('Carregando dados do Servidor (DB export)...')
    db_recs = load_db_samples()
    print(f'  {len(db_recs)} registros em {len(set(r["_data"] for r in db_recs))} datas')

    print('Carregando hops do servidor...')
    hops = load_db_hops()
    hops_info = analyze_hops(hops)
    print(f'  {len(hops)} hops carregados')

    print('Comparando cabos por data...')
    compare, datas_comuns = compare_cables(local_recs, db_recs)

    print('Analisando campos...')
    fields = field_diff(local_recs, db_recs)

    # Saída texto
    report_txt = write_report(None, local_recs, db_recs, compare, datas_comuns, fields, hops_info)
    txt_path = os.path.join(OUT_DIR, f'compare_report_{TODAY}.txt')
    open(txt_path, 'w').write(report_txt)
    print(report_txt)

    # Saída JSON (dados brutos para análise posterior)
    json_data = {
        'gerado_em':    datetime.utcnow().isoformat() + 'Z',
        'codespace':    summarize_group(local_recs),
        'servidor':     summarize_group(db_recs),
        'hops':         hops_info,
        'campos':       fields,
        'por_data':     compare,
    }
    json_path = os.path.join(OUT_DIR, f'compare_report_{TODAY}.json')
    open(json_path, 'w').write(json.dumps(json_data, indent=2, default=str))

    print(f'\nRelatório salvo em:')
    print(f'  {txt_path}')
    print(f'  {json_path}')

if __name__ == '__main__':
    main()
