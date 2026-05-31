/**
 * RMIFO Radar — radar.js
 * Logica das 6 secoes do Radar integradas ao app.
 * Carregado com defer apos Chart.js e app.js.
 */
'use strict';

(function () {

// ── Configuracao ──────────────────────────────────────────────────────────────
const API  = typeof API_BASE !== 'undefined' ? API_BASE : 'api.php';
const C    = {
  p:'#7F77DD', pL:'#AFA9EC', t:'#1D9E75', tL:'#5DCAA5',
  a:'#EF9F27', co:'#D85A30', r:'#E24B4A', g:'#639922', gr:'#888780',
  bg:'rgba(13,13,26,.95)', bd:'rgba(255,255,255,.08)', tx:'#e2e0d8', tx2:'#888780',
};
const CD = {
  responsive:true, maintainAspectRatio:false,
  plugins:{legend:{display:false},tooltip:{backgroundColor:C.bg,borderColor:C.bd,borderWidth:1,
    titleColor:C.tx,bodyColor:C.tx2,padding:8,cornerRadius:6}},
  scales:{
    x:{ticks:{color:C.tx2,font:{size:9}},grid:{color:'rgba(255,255,255,.04)'},border:{display:false}},
    y:{ticks:{color:C.tx2,font:{size:9}},grid:{color:'rgba(255,255,255,.04)'},border:{display:false}},
  },
};

// ── Helpers ───────────────────────────────────────────────────────────────────
async function af(action, p = {}) {
  const qs = new URLSearchParams({ action, ...p });
  const r  = await fetch(`${API}?${qs}`);
  return r.json();
}
function lc(ms) { return !ms ? C.gr : ms < 50 ? C.tL : ms < 150 ? C.a : C.co; }
function ac(a)  { return a == null ? C.gr : a >= 99 ? C.tL : a >= 95 ? C.a : C.r; }
function ab(a) {
  if (a == null) return `<span class="rdr-bk rdr-in">-</span>`;
  if (a >= 99)   return `<span class="rdr-bk rdr-ok">${(+a).toFixed(2)}%</span>`;
  if (a >= 95)   return `<span class="rdr-bk rdr-wn">${(+a).toFixed(2)}%</span>`;
  return `<span class="rdr-bk rdr-er">${(+a).toFixed(2)}%</span>`;
}
function sb(s) {
  const m = { critical:'rdr-er', high:'rdr-er', medium:'rdr-wn', low:'rdr-in' };
  return `<span class="rdr-bk ${m[s]||'rdr-in'}">${s||'?'}</span>`;
}
function fm(v)  { return v != null ? `${(+v).toFixed(1)} ms` : '-'; }
function fM(v) {
  if (!v) return '-';
  if (v > 1e6) return (v/1e6).toFixed(1) + ' Tbps';
  if (v > 1e3) return (v/1e3).toFixed(1) + ' Gbps';
  return (+v).toFixed(0) + ' Mbps';
}
function br(pct, col) {
  return `<span class="rdr-bar"><span class="rdr-bar-f" style="width:${Math.min(100,pct||0).toFixed(1)}%;background:${col};display:block;height:100%"></span></span>`;
}
function mk(id, cfg) {
  const el = document.getElementById(id);
  if (!el) return null;
  const ex = Chart.getChart(el);
  if (ex) ex.destroy();
  return new Chart(el, cfg);
}
function kpi(lbl, val, color, sub) {
  return `<div class="rdr-kpi"><div class="rdr-kl">${lbl}</div><div class="rdr-kv" style="color:${color}">${val}</div>${sub ? `<div class="rdr-ks">${sub}</div>` : ''}</div>`;
}

// ── Registro de titulos no Router ─────────────────────────────────────────────
if (typeof Router !== 'undefined' && Router.titles) {
  Object.assign(Router.titles, {
    'radar-overview':     'Radar — Overview',
    'radar-connectivity': 'Radar — Connectivity',
    'radar-routing':      'Radar — Routing',
    'radar-tools':        'Radar — Tools',
    'radar-security':     'Radar — Security',
    'radar-traffic':      'Radar — Traffic',
  });
}

// ── Gatilho de carregamento ao navegar ────────────────────────────────────────
const _loaded = {};
document.querySelectorAll('.radar-link').forEach(a => {
  a.addEventListener('click', () => {
    const pg = a.dataset.page;
    if (!_loaded[pg]) {
      const map = {
        'radar-overview':     () => Rdr.ov.load(),
        'radar-connectivity': () => Rdr.cn.load(),
        'radar-routing':      () => Rdr.rt.load(),
        'radar-tools':        () => Rdr.tools.init(),
        'radar-security':     () => Rdr.se.load(),
        'radar-traffic':      () => Rdr.tr.load(),
      };
      if (map[pg]) { map[pg](); _loaded[pg] = true; }
    }
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// RADAR NAMESPACE
// ══════════════════════════════════════════════════════════════════════════════
const Rdr = {

  // ── OVERVIEW ──────────────────────────────────────────────────────────────
  ov: {
    _data: null,
    async load(force) {
      if (this._data && !force) return;
      _loaded['radar-overview'] = true;
      const [cr, ar] = await Promise.all([af('connectivity'), af('alerts')]);
      const cc = cr.cables || [], al = ar.alerts || [];
      this._data = { cc, al };

      const aa = cr.avg_availability_pct || 0;
      const dg = cr.degraded_count || 0;
      document.getElementById('rdr-ov-kpis').innerHTML =
        kpi('Cabos operacionais', cc.length, C.pL, 'de 699 catalogados') +
        kpi('Disponib. media', aa.toFixed(2) + '%', ac(aa), 'baseline 7 dias') +
        kpi('Degradados', dg, dg > 0 ? C.a : C.tL, 'disponib. menor que 99%') +
        kpi('Alertas ativos', al.length, al.length > 0 ? C.r : C.tL, '');

      const bk = {'99.9+':0,'99-99.9':0,'95-99':0,'menos 95':0};
      cc.forEach(c => {
        const a = +c.availability_pct || 0;
        if (a >= 99.9) bk['99.9+']++;
        else if (a >= 99) bk['99-99.9']++;
        else if (a >= 95) bk['95-99']++;
        else bk['menos 95']++;
      });
      mk('rdr-ov-ac', { type:'doughnut',
        data:{ labels:Object.keys(bk), datasets:[{data:Object.values(bk),backgroundColor:[C.tL,C.g,C.a,C.r],borderWidth:0}] },
        options:{ ...CD, plugins:{ ...CD.plugins, legend:{ display:true, position:'right', labels:{color:C.tx2,font:{size:10},boxWidth:10} } } },
      });

      const RM = Object.fromEntries([
        ...['United States','Brazil','Chile','Argentina'].map(k=>[k,'Americas']),
        ...['United Kingdom','France','Germany','Italy','Netherlands'].map(k=>[k,'Europe']),
        ...['Japan','Singapore','Australia','China','India'].map(k=>[k,'Asia-Pac']),
        ...['South Africa','Nigeria','Kenya','Egypt'].map(k=>[k,'Africa']),
      ]);
      const rl = {}, rc = {};
      cc.forEach(c => {
        const reg = RM[c.primary_country] || 'Other';
        rl[reg] = (rl[reg] || 0) + (+c.latency_ms || 0);
        rc[reg] = (rc[reg] || 0) + 1;
      });
      const rg = Object.keys(rl).sort();
      mk('rdr-ov-lc', { type:'bar',
        data:{ labels:rg, datasets:[{ data:rg.map(r => Math.round(rl[r]/rc[r])), backgroundColor:rg.map((_,i)=>[C.p,C.t,C.a,C.co,C.pL,C.tL][i%6]), borderRadius:4 }] },
        options:CD,
      });

      const ae = document.getElementById('rdr-ov-al');
      ae.innerHTML = !al.length
        ? '<div style="color:#5DCAA5;font-size:12px;padding:8px">Nenhum alerta ativo</div>'
        : al.slice(0, 8).map(a => `<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.04)"><span style="font-size:12px">${a.cable_name || a.cable_id}</span><span style="font-size:11px;color:#888780;margin:0 12px">${a.metric || '-'}</span>${sb(a.severity)}</div>`).join('');

      const ws = [...cc].sort((a, b) => (+a.availability_pct || 0) - (+b.availability_pct || 0)).slice(0, 15);
      document.getElementById('rdr-ov-tb').innerHTML = ws.map(c => `
        <tr>
          <td style="font-family:monospace;font-size:11px">${c.cable_name}</td>
          <td style="color:#888780;font-size:11px">${c.primary_country || '-'}</td>
          <td>${ab(+c.availability_pct)}</td>
          <td style="color:${(+c.loss_pct > 1) ? C.r : C.tx2}">${c.loss_pct != null ? (+c.loss_pct).toFixed(2) + '%' : '-'}</td>
          <td style="color:${lc(c.latency_ms)}">${fm(c.latency_ms)}</td>
          <td>${br(+c.availability_pct || 0, ac(+c.availability_pct))}</td>
        </tr>`).join('');
    },
  },

  // ── CONNECTIVITY ──────────────────────────────────────────────────────────
  cn: {
    _data: [], _sort: { col:'availability_pct', asc:true },
    async load(force) {
      if (this._data.length && !force) { this.render(); return; }
      _loaded['radar-connectivity'] = true;
      const res = await af('connectivity');
      this._data = res.cables || [];

      document.getElementById('rdr-cn-kpis').innerHTML =
        kpi('Operacionais', this._data.length, C.pL, '') +
        kpi('Disponib. media', (res.avg_availability_pct||0).toFixed(2)+'%', ac(res.avg_availability_pct), '') +
        kpi('Perda media', (res.avg_loss_pct||0).toFixed(3)+'%', (res.avg_loss_pct||0)>.5?C.a:C.tL, '') +
        kpi('Degradados', res.degraded_count||0, (res.degraded_count||0)>0?C.a:C.tL, '');

      mk('rdr-cn-sc', { type:'scatter',
        data:{ datasets:[{ data:this._data.map(c=>({x:+c.loss_pct||0,y:+c.availability_pct||0,n:c.cable_name})),
          pointBackgroundColor:this._data.map(c=>ac(+c.availability_pct)), pointRadius:3, pointHoverRadius:5, showLine:false }] },
        options:{ ...CD, plugins:{ ...CD.plugins, tooltip:{ ...CD.plugins.tooltip, callbacks:{
          label:ctx=>`${ctx.raw.n}: ${ctx.raw.y.toFixed(2)}% / loss ${ctx.raw.x.toFixed(3)}%`
        }}},
        scales:{ x:{...CD.scales.x,title:{display:true,text:'Packet Loss %',color:C.tx2,font:{size:9}}},
                 y:{...CD.scales.y,title:{display:true,text:'Availability %',color:C.tx2,font:{size:9}}} } },
      });
      const b = {'99.9+':0,'99-99.9':0,'95-99':0,'90-95':0,'menos 90':0};
      this._data.forEach(c => {
        const a = +c.availability_pct||0;
        if(a>=99.9)b['99.9+']++; else if(a>=99)b['99-99.9']++; else if(a>=95)b['95-99']++; else if(a>=90)b['90-95']++; else b['menos 90']++;
      });
      mk('rdr-cn-bc', { type:'bar',
        data:{ labels:Object.keys(b), datasets:[{data:Object.values(b),backgroundColor:[C.tL,C.g,C.a,C.co,C.r],borderRadius:4}] },
        options:CD,
      });
      this.render();
    },
    sort(col) { this._sort.asc = this._sort.col === col ? !this._sort.asc : true; this._sort.col = col; this.render(); },
    render() {
      const q = (document.getElementById('rdr-cn-s')?.value||'').toLowerCase();
      const ft = document.getElementById('rdr-cn-f')?.value || '';
      let rs = this._data.filter(c => {
        if (q && !c.cable_name.toLowerCase().includes(q)) return false;
        const a = +c.availability_pct||0;
        if (ft==='ok' && a<99) return false;
        if (ft==='wn' && (a>=99||a<95)) return false;
        if (ft==='er' && a>=95) return false;
        return true;
      });
      const {col,asc} = this._sort;
      rs.sort((a,b) => {
        const av = +a[col]||0, bv = +b[col]||0;
        return asc ? (typeof a[col]==='string' ? a[col].localeCompare(b[col]) : av-bv)
                   : (typeof b[col]==='string' ? b[col].localeCompare(a[col]) : bv-av);
      });
      document.getElementById('rdr-cn-tb').innerHTML = rs.map(c => `
        <tr>
          <td style="font-family:monospace;font-size:11px">${c.cable_name}</td>
          <td style="color:#888780;font-size:11px">${c.primary_country||'-'}</td>
          <td>${ab(+c.availability_pct)}</td>
          <td style="color:${(+c.loss_pct>1)?C.r:'#888780'}">${c.loss_pct!=null?(+c.loss_pct).toFixed(3)+'%':'-'}</td>
          <td style="color:${lc(c.latency_ms)}">${fm(c.latency_ms)}</td>
          <td style="color:#888780">${c.jitter_ms!=null?(+c.jitter_ms).toFixed(1)+' ms':'-'}</td>
        </tr>`).join('');
    },
  },

  // ── ROUTING ───────────────────────────────────────────────────────────────
  rt: {
    async load(force) {
      if (_loaded['radar-routing'] && !force) return;
      _loaded['radar-routing'] = true;
      const res = await af('routing');
      const hp = res.hops || [], is = res.top_isps || {};

      document.getElementById('rdr-rt-kpis').innerHTML =
        kpi('Total hops', hp.length.toLocaleString(), C.pL, '') +
        kpi('ASNs unicos', res.unique_asns||0, C.tL, '') +
        kpi('Paises', res.unique_countries||0, C.a, '') +
        kpi('ISPs distintos', Object.keys(is).length, C.pL, '');

      const il = Object.keys(is).slice(0, 12);
      mk('rdr-rt-ic', { type:'bar',
        data:{ labels:il, datasets:[{data:il.map(k=>is[k]), backgroundColor:C.p+'88', borderRadius:3}] },
        options:{ ...CD, indexAxis:'y',
          scales:{ x:{...CD.scales.x,title:{display:true,text:'Hops',color:C.tx2,font:{size:9}}},
                   y:{...CD.scales.y,ticks:{...CD.scales.y.ticks,font:{size:9}}} } },
      });

      const rb = Array(10).fill(0);
      hp.forEach(h => { const v = +h.rtt_ms||0; if(v>0&&v<500) rb[Math.min(9,Math.floor(v/50))]++; });
      mk('rdr-rt-rc', { type:'bar',
        data:{ labels:['0-50','50-100','100-150','150-200','200-250','250-300','300-350','350-400','400-450','450+'].map(l=>l+' ms'),
          datasets:[{ data:rb, backgroundColor:[C.tL,C.g,C.a,C.co,C.r,C.r,C.r,C.r,C.r,C.r], borderRadius:3 }] },
        options:CD,
      });

      document.getElementById('rdr-rt-tb').innerHTML = hp.slice(0, 100).map(h => `
        <tr>
          <td style="font-family:monospace;font-size:11px">${h.hop_ip}</td>
          <td style="font-size:11px">${h.cable_name||h.cable_id||'-'}</td>
          <td style="text-align:center;color:#888780">${h.hop_number}</td>
          <td style="color:${lc(h.rtt_ms)}">${fm(h.rtt_ms)}</td>
          <td style="color:${(+h.loss_pct>5)?C.r:'#888780'}">${h.loss_pct!=null?(+h.loss_pct).toFixed(0)+'%':'-'}</td>
          <td style="font-size:11px">${h.country_code||'-'}${h.city?' '+h.city:''}</td>
          <td style="font-size:11px;color:#888780">${h.isp||''}${h.as_number?' AS'+h.as_number:''}</td>
        </tr>`).join('');
    },
  },

  // ── TOOLS ─────────────────────────────────────────────────────────────────
  tools: {
    async init() {
      if (_loaded['radar-tools']) return;
      _loaded['radar-tools'] = true;
      const res = await af('all');
      const sel = document.getElementById('rdr-t-cb');
      if (sel) (res.data||[]).forEach(c => {
        const o = document.createElement('option');
        o.value = c.cable_id; o.textContent = c.cable_name;
        sel.appendChild(o);
      });
    },
    async probe() {
      const el = document.getElementById('rdr-t-pr');
      const cb = document.getElementById('rdr-t-cb')?.value;
      const ip = document.getElementById('rdr-t-ip')?.value.trim();
      if (!cb) { el.textContent = 'Selecione um cabo.'; return; }
      el.textContent = 'Executando probe...';
      try {
        const p = { q:cb }; if (ip) p.ip = ip;
        const r  = await af('probe', p);
        const rs = r.result || r;
        el.textContent = [
          `Cabo:     ${r.cable_id}`,
          `IP:       ${r.target||'-'}`,
          `Metodo:   ${rs.method}`,
          `Latencia: ${fm(rs.avg_rtt_ms)}`,
          `Jitter:   ${fm(rs.jitter_ms)}`,
          `Perda:    ${rs.packet_loss_pct!=null?rs.packet_loss_pct+'%':'-'}`,
          `Portas:   [${(rs.ports_tested||[]).join(', ')}]`,
          `Elapsed:  ${rs.total_elapsed_ms} ms`,
          rs.note ? '\n' + rs.note : '',
        ].filter(Boolean).join('\n');
      } catch(e) { el.textContent = 'Erro: ' + e.message; }
    },
    async bgp() {
      const el = document.getElementById('rdr-t-bg');
      const pf = document.getElementById('rdr-t-pf')?.value.trim();
      if (!pf) { el.textContent = 'Informe um prefixo.'; return; }
      el.textContent = 'Consultando RIPE STAT...';
      try {
        const [vis, rpki] = await Promise.all([
          fetch(`https://stat.ripe.net/data/visibility/data.json?resource=${encodeURIComponent(pf)}`).then(r=>r.json()),
          fetch(`https://stat.ripe.net/data/rpki-validation/data.json?resource=${encodeURIComponent(pf)}`).then(r=>r.json()),
        ]);
        const v4 = vis?.data?.visibilities?.[0]?.ipv4 || {};
        const pk = +v4.ris_peers_seeing||0, tt = +v4.total_ris_peers||1;
        const ro = rpki?.data?.status || 'unknown';
        el.textContent = [
          `Prefixo:    ${pf}`,
          `Peers:      ${pk} / ${tt} (${(pk/tt*100).toFixed(1)}%)`,
          `ROA status: ${ro}`,
          `Hijack:     ${['invalid','not-found'].includes(ro)?'RISCO':'OK'}`,
        ].join('\n');
      } catch(e) { el.textContent = 'Erro: ' + e.message; }
    },
    async asn() {
      const el  = document.getElementById('rdr-t-ar');
      const raw = document.getElementById('rdr-t-as')?.value.trim() || '';
      const val = raw.replace(/^AS/i, '');
      if (!val) { el.textContent = 'Informe um ASN ou IP.'; return; }
      el.textContent = 'Consultando...';
      try {
        const resource = raw.includes('.') ? val : `AS${val}`;
        const r = await fetch(`https://stat.ripe.net/data/prefix-overview/data.json?resource=${encodeURIComponent(resource)}`).then(r=>r.json());
        const asns = r?.data?.asns || [];
        el.textContent = asns.map(a => `ASN:    AS${a.asn}\nHolder: ${a.holder}`).join('\n---\n')
          || JSON.stringify(r?.data||{}, null, 2).slice(0, 400);
      } catch(e) { el.textContent = 'Erro: ' + e.message; }
    },
    async routes() {
      const el = document.getElementById('rdr-t-rr');
      const or = document.getElementById('rdr-t-or')?.value.trim();
      const ds = document.getElementById('rdr-t-ds')?.value.trim();
      if (!or || !ds) { el.textContent = 'Informe origem e destino.'; return; }
      el.textContent = 'Calculando...';
      try {
        const r  = await af('top_routes', { origin:or, dest:ds });
        const rt = r.routes || r.data || [];
        el.textContent = !rt.length
          ? `Nenhuma rota de ${or} para ${ds}.`
          : rt.slice(0, 3).map((x, i) => `Rota ${i+1}:\n${JSON.stringify(x, null, 2)}`).join('\n---\n');
      } catch(e) { el.textContent = 'Erro: ' + e.message; }
    },
  },

  // ── SECURITY ──────────────────────────────────────────────────────────────
  se: {
    async load(force) {
      if (_loaded['radar-security'] && !force) return;
      _loaded['radar-security'] = true;
      const res = await af('security');
      const sv = res.severity_summary || {}, pr = res.privacy_hops || [];

      document.getElementById('rdr-se-kpis').innerHTML =
        kpi('Total alertas', res.alert_count||0, (res.alert_count||0)>0?C.r:C.tL, '') +
        kpi('Criticos', sv.critical||0, C.r, '') +
        kpi('Altos', sv.high||0, C.co, '') +
        kpi('Hops com flags', res.privacy_hop_count||0, C.a, '');

      mk('rdr-se-sc', { type:'doughnut',
        data:{ labels:['Critical','High','Medium','Low'],
          datasets:[{data:[sv.critical||0,sv.high||0,sv.medium||0,sv.low||0],backgroundColor:[C.r,C.co,C.a,C.g],borderWidth:0}] },
        options:{ ...CD, plugins:{ ...CD.plugins, legend:{ display:true, position:'right', labels:{color:C.tx2,font:{size:10},boxWidth:10} } } },
      });

      const pe = document.getElementById('rdr-se-pl');
      pe.innerHTML = !pr.length
        ? '<div style="color:#5DCAA5;font-size:12px;padding:8px">Nenhum hop com flags detectado</div>'
        : pr.map(h => {
            const fl = [h.privacy_vpn&&'VPN',h.privacy_proxy&&'PROXY',h.privacy_tor&&'TOR',h.privacy_hosting&&'HOSTING'].filter(Boolean);
            return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.04)">
              <span style="font-family:monospace;font-size:11px">${h.hop_ip}</span>
              <span style="font-size:11px;color:#888780;margin:0 8px">${h.city||''} ${h.country_code||''}</span>
              <span>${fl.map(f=>`<span class="rdr-bk rdr-wn">${f}</span>`).join(' ')}</span>
            </div>`;
          }).join('');

      const al = res.alerts || [];
      document.getElementById('rdr-se-tb').innerHTML = al.slice(0, 50).map(a => `
        <tr>
          <td style="font-size:11px">${a.cable_name||a.cable_id||'-'}</td>
          <td style="font-family:monospace;font-size:11px">${a.metric||'-'}</td>
          <td>${sb(a.severity)}</td>
          <td style="color:#888780">${a.current_value!=null?(+a.current_value).toFixed(2):'-'}</td>
          <td style="color:#888780">${a.baseline_value!=null?(+a.baseline_value).toFixed(2):'-'}</td>
          <td style="color:#888780;font-size:11px">${a.detected_at?new Date(a.detected_at).toLocaleString('pt-BR'):'-'}</td>
        </tr>`).join('')
        || '<tr><td colspan="6" style="text-align:center;color:#888780;padding:20px">Sem alertas no banco de dados</td></tr>';
    },
  },

  // ── TRAFFIC ───────────────────────────────────────────────────────────────
  tr: {
    async load(force) {
      if (_loaded['radar-traffic'] && !force) return;
      _loaded['radar-traffic'] = true;
      const res = await af('traffic');
      const ca  = res.cables || [];

      document.getElementById('rdr-tr-kpis').innerHTML =
        kpi('Cabos com dados', ca.length, C.pL, '') +
        kpi('Throughput total', fM(res.total_throughput_mbps), C.tL, 'estimado') +
        kpi('Capacidade total', res.total_capacity_gbps!=null?res.total_capacity_gbps+' Tbps':'-', C.a, '') +
        kpi('Utilizacao est.', res.utilization_pct!=null?res.utilization_pct.toFixed(1)+'%':'-', (res.utilization_pct||0)>70?C.r:C.tL, 'via RTT');

      const t15 = ca.slice(0, 15);
      mk('rdr-tr-bc', { type:'bar',
        data:{ labels:t15.map(c=>c.cable_name.length>20?c.cable_name.slice(0,18)+'..':c.cable_name),
          datasets:[{ data:t15.map(c=>+c.throughput_mbps||0), backgroundColor:C.p+'88', borderRadius:3 }] },
        options:{ ...CD, indexAxis:'y',
          scales:{ x:{...CD.scales.x,title:{display:true,text:'Mbps (estimado)',color:C.tx2,font:{size:9}}},
                   y:{...CD.scales.y,ticks:{...CD.scales.y.ticks,font:{size:9}}} } },
      });

      mk('rdr-tr-sc', { type:'scatter',
        data:{ datasets:[{ data:ca.filter(c=>c.throughput_mbps&&c.latency_ms).map(c=>({x:+c.latency_ms,y:+c.throughput_mbps,n:c.cable_name})),
          pointBackgroundColor:C.p+'99', pointRadius:3, pointHoverRadius:5, showLine:false }] },
        options:{ ...CD, plugins:{ ...CD.plugins, tooltip:{ ...CD.plugins.tooltip,
          callbacks:{ label:ctx=>`${ctx.raw.n}: ${ctx.raw.x.toFixed(0)}ms / ${fM(ctx.raw.y)}` } } },
          scales:{ x:{...CD.scales.x,title:{display:true,text:'Latencia (ms)',color:C.tx2,font:{size:9}}},
                   y:{...CD.scales.y,title:{display:true,text:'Throughput (Mbps)',color:C.tx2,font:{size:9}}} } },
      });

      document.getElementById('rdr-tr-tb').innerHTML = ca.map(c => {
        const cp = c.capacity_gbps ? +c.capacity_gbps*1000 : null;
        const tp = +c.throughput_mbps || 0;
        const ut = cp ? Math.min(100, tp/cp*100) : null;
        return `<tr>
          <td style="font-size:11px">${c.cable_name}</td>
          <td style="color:#5DCAA5;font-family:monospace">${fM(tp)}</td>
          <td style="color:#888780">${cp ? fM(cp) : '-'}</td>
          <td>${ut!=null ? br(ut,ut>70?C.r:ut>40?C.a:C.tL)+`<span style="font-size:10px;margin-left:6px">${ut.toFixed(1)}%</span>` : '-'}</td>
          <td style="color:${lc(c.latency_ms)}">${fm(c.latency_ms)}</td>
          <td>${ab(+c.availability_pct)}</td>
        </tr>`;
      }).join('');
    },
  },
};

// Expor globalmente para uso nos botoes onclick do HTML
window.Rdr = Rdr;

})();
