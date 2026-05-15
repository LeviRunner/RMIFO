/* ============================================================
   Global Network Resilience — SPA app.js
   Architecture: 6 pages (map, search, detail, routes, telemetry, probe)
   Backend: api.php  |  Globe: globe.gl (loaded before this file)
   ============================================================ */

'use strict';

// ─── Constants ────────────────────────────────────────────────────────────────

const API_BASE = 'api.php';

const ACTION_ALIAS = {
  detail:         'details',
  critical_cuts:  'critical_cables',
  search:         null,
};

// ─── Utility ──────────────────────────────────────────────────────────────────

function esc(value) {
  if (value === null || value === undefined || value === '') {
    return '<span class="muted">–</span>';
  }
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtBool(value) {
  if (value === true || value === 'true' || value === 't' || value === 1) {
    return '<span class="badge yes">Sim</span>';
  }
  if (value === false || value === 'false' || value === 'f' || value === 0) {
    return '<span class="badge no">Não</span>';
  }
  return '<span class="muted">–</span>';
}

function showError(el, msg) {
  if (!el) return;
  el.textContent = msg;
  el.style.display = msg ? '' : 'none';
}

function clearEl(el) {
  if (!el) return;
  el.textContent = '';
  el.style.display = 'none';
}

async function apiFetch(params = {}) {
  const p = { ...params };
  if (p.action !== undefined) {
    const alias = ACTION_ALIAS[p.action];
    p.mode = alias !== undefined ? (alias || p.mode || p.action) : p.action;
    delete p.action;
  }
  if (p.cable_id !== undefined && p.q === undefined) { p.q = p.cable_id; delete p.cable_id; }
  p.api = 1;
  const url = new URLSearchParams(p);
  const response = await fetch(`${API_BASE}?${url.toString()}`);
  const raw = await response.text();
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    const snippet = String(raw).slice(0, 200).replace(/\s+/g, ' ').trim();
    throw new Error(`Resposta inválida da API (${response.status}): ${snippet || 'vazio'}`);
  }
  if (payload && payload.status === 'error') {
    throw new Error(payload.message || 'Erro desconhecido da API.');
  }
  return payload;
}

async function apiPost(formData) {
  const rawAction = formData.get('action') || 'all';
  const alias = ACTION_ALIAS[rawAction];
  const mode = alias !== undefined ? (alias || rawAction) : rawAction;
  const response = await fetch(`${API_BASE}?api=1&mode=${encodeURIComponent(mode)}`, { method: 'POST', body: formData });
  const raw = await response.text();
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    const snippet = String(raw).slice(0, 200).replace(/\s+/g, ' ').trim();
    throw new Error(`Resposta inválida da API (${response.status}): ${snippet || 'vazio'}`);
  }
  if (payload && payload.status === 'error') {
    throw new Error(payload.message || 'Erro desconhecido da API.');
  }
  return payload;
}

function extractData(payload) {
  if (Array.isArray(payload)) return payload;
  if (payload && Array.isArray(payload.data)) return payload.data;
  return payload;
}

// ─── Router ───────────────────────────────────────────────────────────────────

const Router = {
  current: 'map',
  titles: {
    map: 'Mapa Global',
    search: 'Busca de Cabos',
    detail: 'Detalhes do Cabo',
    routes: 'Análise de Rotas',
    telemetry: 'Telemetria',
    probe: 'Sonda de Rede',
  },
  go(page) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const pageEl = document.getElementById('page-' + page);
    if (pageEl) pageEl.classList.add('active');
    document.querySelectorAll('.nav-link').forEach(a => a.classList.remove('active'));
    const navEl = document.querySelector(`[data-page="${page}"]`);
    if (navEl) navEl.classList.add('active');
    const titleEl = document.getElementById('header-page-title');
    if (titleEl) titleEl.textContent = Router.titles[page] || page;
    Router.current = page;
    Drawer.close();
  },
};

// ─── Drawer ───────────────────────────────────────────────────────────────────

const Drawer = {
  el()  { return document.getElementById('drawer'); },
  ov()  { return document.getElementById('drawer-overlay'); },
  open() {
    const d = Drawer.el(), o = Drawer.ov();
    if (d) d.classList.add('open');
    if (o) o.classList.add('open');
  },
  close() {
    const d = Drawer.el(), o = Drawer.ov();
    if (d) d.classList.remove('open');
    if (o) o.classList.remove('open');
  },
  toggle() {
    const d = Drawer.el();
    if (d && d.classList.contains('open')) Drawer.close(); else Drawer.open();
  },
};

// ─── Probe Modal ──────────────────────────────────────────────────────────────

const ProbeModal = {
  get el()      { return document.getElementById('probe-modal'); },
  get titleEl() { return document.getElementById('modal-title'); },
  get bodyEl()  { return document.getElementById('modal-body'); },
  show(title, html) {
    if (this.titleEl) this.titleEl.textContent = title;
    if (this.bodyEl)  this.bodyEl.innerHTML    = html;
    if (this.el)      this.el.classList.add('open');
  },
  hide() {
    if (this.el) this.el.classList.remove('open');
  },
};

// ─── Shared state ─────────────────────────────────────────────────────────────

const AppState = {
  allCables: [],           // [{cable_id, cable_name, ...}]
  countries: [],           // ['Brazil', 'France', ...]
  globe: null,             // globe.gl instance
  globeSelection: null,    // currently selected arc or point on globe
  // Search page
  search: {
    mode: 'cable_id',
    binarySteps: [],
    binaryIndex: 0,
    binaryTimer: null,
  },
  // Telemetry cache
  lastTelemetryHistory: [],
  lastTelemetryCiLatency: null,
};

// ─── Globe page ───────────────────────────────────────────────────────────────

function arcColor(d) {
  if (!d.is_operational) return ['rgba(255,80,100,0.9)', 'rgba(255,80,100,0.5)'];
  if (d.has_alert)       return ['rgba(255,190,60,0.95)', 'rgba(255,190,60,0.55)'];
  return ['rgba(60,230,140,0.95)', 'rgba(60,230,140,0.55)'];
}

function ptColor(d) {
  if (!d.is_operational) return '#ff4f66';
  if (d.has_alert)       return '#ffbe3c';
  return '#41da8c';
}

function statusLabel(isOp, hasAlert) {
  if (!isOp)    return '<span class="badge no">Fora de operação</span>';
  if (hasAlert) return '<span class="badge warn">Alerta ativo</span>';
  return '<span class="badge yes">Operacional</span>';
}

function stopGlobeRotation() {
  if (AppState.globe) {
    AppState.globe.controls().autoRotate = false;
    if (globeAutoRotateTimer) { clearTimeout(globeAutoRotateTimer); globeAutoRotateTimer = null; }
  }
}

function selectFromGlobe(d) {
  if (!d) return;
  AppState.globeSelection = d;
  stopGlobeRotation();

  const panel    = document.getElementById('selection-panel');
  const titleEl  = document.getElementById('sel-title');
  const bodyEl   = document.getElementById('sel-body');
  if (!panel || !titleEl || !bodyEl) return;

  // Determine if arc or point (points have endpoint_id)
  const isPoint = !!d.endpoint_id;

  titleEl.textContent = d.cable_name || 'Cabo';

  let html = '';
  if (isPoint) {
    html = `
      <p>${statusLabel(d.is_operational, d.has_alert)}</p>
      <table class="sel-table">
        <tr><th>Cidade</th><td>${esc(d.city_name)}</td></tr>
        <tr><th>País</th><td>${esc(d.country_name)}</td></tr>
        <tr><th>Cabo</th><td>${esc(d.cable_name)}</td></tr>
        <tr><th>Endpoint ID</th><td class="mono">${esc(d.endpoint_id)}</td></tr>
        ${d.ip_sample ? `<tr><th>IP sample</th><td class="mono">${esc(d.ip_sample)}</td></tr>` : ''}
      </table>`;
  } else {
    html = `
      <p>${statusLabel(d.is_operational, d.has_alert)}</p>
      <table class="sel-table">
        <tr><th>Cabo</th><td>${esc(d.cable_name)}</td></tr>
        <tr><th>ID</th><td class="mono">${esc(d.cable_id)}</td></tr>
        <tr><th>Origem</th><td>${esc(d.startCity)}, ${esc(d.startCountry)}</td></tr>
        <tr><th>Destino</th><td>${esc(d.endCity)}, ${esc(d.endCountry)}</td></tr>
        ${d.ip_sample ? `<tr><th>IP sample</th><td class="mono">${esc(d.ip_sample)}</td></tr>` : ''}
      </table>`;
  }
  bodyEl.innerHTML = html;
  panel.classList.add('open');
}

let globeAutoRotateTimer = null;

function initGlobe() {
  const container = document.getElementById('globe-container');
  const globeTip  = document.getElementById('globe-tip');
  if (!container) return;
  if (typeof Globe === 'undefined') {
    container.innerHTML = '<p style="color:#f0b24f;padding:20px;">Globe.gl não carregado.</p>';
    return;
  }

  const globe = Globe()(container)
    .globeImageUrl('//unpkg.com/three-globe/example/img/earth-night.jpg')
    .backgroundImageUrl('//unpkg.com/three-globe/example/img/night-sky.png')
    .atmosphereColor('rgba(100,50,255,0.5)')
    .atmosphereAltitude(0.20);

  AppState.globe = globe;

  globe.controls().autoRotate      = true;
  globe.controls().autoRotateSpeed = 0.35;

  // Stop on manual drag; restart after 8s if nothing was selected
  container.addEventListener('mousedown', () => {
    globe.controls().autoRotate = false;
    if (globeAutoRotateTimer) clearTimeout(globeAutoRotateTimer);
    globeAutoRotateTimer = setTimeout(() => {
      if (!AppState.globeSelection) globe.controls().autoRotate = true;
    }, 8000);
  });

  // Hover tip
  globe
    .onArcHover(d => {
      if (globeTip) {
        globeTip.textContent = d
          ? `${d.cable_name} · ${d.startCity} → ${d.endCity}`
          : 'Clique em um cabo ou ponto para selecionar';
      }
    })
    .onArcClick(d => selectFromGlobe(d))
    .onPointClick(d => selectFromGlobe(d));

  // Load globe data
  apiFetch({ action: 'globe_data' })
    .then(payload => {
      const arcs = payload.arcs || [];
      const pts  = payload.points || [];

      globe
        .arcsData(arcs)
        .arcStartLat('startLat')
        .arcStartLng('startLng')
        .arcEndLat('endLat')
        .arcEndLng('endLng')
        .arcColor(arcColor)
        .arcStroke(2.0)
        .arcAltitude(0.25)
        .arcAltitudeAutoScale(0.35)
        .arcDashLength(0.6)
        .arcDashGap(0.08)
        .arcDashAnimateTime(2000)
        .pointsData(pts)
        .pointLat('lat')
        .pointLng('lng')
        .pointColor(ptColor)
        .pointAltitude(0.02)
        .pointRadius(0.5)
        .pointsMerge(false)
        .pointLabel(d =>
          `<div style="background:rgba(8,6,15,0.90);border:1px solid rgba(168,113,255,0.4);padding:7px 12px;border-radius:8px;font-size:13px;color:#f4efff;line-height:1.5;">
            <strong>${d.city_name}</strong>, ${d.country_name}<br>
            <span style="color:#a871ff;">${d.cable_name}</span>
            ${d.ip_sample ? `<br><span style="color:#41da8c;font-size:11px;">${d.ip_sample}</span>` : ''}
          </div>`
        );

      // Alert badge
      const alertCount = arcs.filter(a => a.has_alert).length;
      updateAlertBadge(alertCount);

      // Alert ticker
      const alertCables = arcs.filter(a => a.has_alert);
      updateAlertTicker(alertCables);
    })
    .catch(err => {
      if (globeTip) globeTip.textContent = `Erro ao carregar dados do globo: ${err.message}`;
    });
}

function updateAlertBadge(count) {
  const badge = document.getElementById('alert-badge');
  if (!badge) return;
  badge.textContent = count;
  badge.style.display = count > 0 ? '' : 'none';
}

function updateAlertTicker(alertCables) {
  const ticker = document.getElementById('alert-ticker');
  if (!ticker) return;
  if (!alertCables.length) {
    ticker.classList.remove('active');
    return;
  }
  const content = alertCables
    .map(a => `${a.cable_name} · ${a.startCity} → ${a.endCity}`)
    .join('   |   ');
  ticker.textContent = content;
  ticker.classList.add('active');
}

// Selection panel buttons
function initGlobePanel() {
  const closeBtn  = document.getElementById('sel-close');
  const probeBtn  = document.getElementById('sel-probe-btn');
  const detailBtn = document.getElementById('sel-detail-btn');
  const panel     = document.getElementById('selection-panel');

  if (closeBtn) closeBtn.addEventListener('click', () => {
    if (panel) panel.classList.remove('open');
    AppState.globeSelection = null;
    if (AppState.globe) AppState.globe.controls().autoRotate = true;
  });

  if (detailBtn) {
    detailBtn.addEventListener('click', () => {
      const d = AppState.globeSelection;
      if (!d) return;
      const cableId = d.cable_id;
      if (cableId) {
        if (panel) panel.classList.remove('open');
        Router.go('detail');
        // Pre-fill picker and trigger load
        const picker = document.getElementById('detail-picker');
        if (picker) picker.value = cableId;
        loadDetail(cableId);
      }
    });
  }

  if (probeBtn) {
    probeBtn.addEventListener('click', async () => {
      const d = AppState.globeSelection;
      if (!d) return;
      ProbeModal.show('Sondando…', '<p>Aguarde…</p>');
      try {
        const fd = new FormData();
        fd.append('action', 'probe');
        fd.append('cable_id', d.cable_id || '');
        if (d.endpoint_id) fd.append('endpoint_id', d.endpoint_id);
        if (d.ip_sample)   fd.append('ip', d.ip_sample);
        const payload = await apiPost(fd);
        ProbeModal.show('Resultado da Sonda', renderProbeResult(payload));
      } catch (err) {
        ProbeModal.show('Erro na Sonda', `<p class="error-text">${esc(err.message)}</p>`);
      }
    });
  }
}

// ─── Search page ──────────────────────────────────────────────────────────────

const modes = {
  cable_id:    { placeholder: 'ex: 2africa',    type: 'text',   api: 'all' },
  name:        { placeholder: 'ex: Africa',      type: 'text',   api: 'name' },
  owners:      { placeholder: 'ex: AT&T',        type: 'text',   api: 'owners' },
  year:        { placeholder: 'ex: 2024',        type: 'number', api: 'year' },
  operational: { placeholder: '',               type: 'bool',   api: 'operational' },
  landing:     { placeholder: 'ex: 4',           type: 'number', api: 'landing' },
  suppliers:   { placeholder: 'ex: ASN',         type: 'text',   api: 'suppliers' },
  endpoint:    { placeholder: 'ex: Marseille',   type: 'text',   api: 'endpoint' },
  country:     { placeholder: 'ex: Brazil',      type: 'text',   api: 'country' },
};

function initSearchPage() {
  const searchInput   = document.getElementById('query-input');
  const boolInput     = document.getElementById('bool-input');
  const searchBtn     = document.getElementById('search-btn');
  const clearBtn      = document.getElementById('clear-btn');
  const resultCount   = document.getElementById('result-count');
  const searchError   = document.getElementById('search-error');
  const searchHead    = document.getElementById('search-head');
  const searchBody    = document.getElementById('search-body');
  const tableWrap     = document.getElementById('search-table-wrap');
  const searchEmpty   = document.getElementById('search-empty');
  const binaryBox     = document.getElementById('binary-box');
  const binaryLog     = document.getElementById('binary-log');
  const binaryProgress= document.getElementById('binary-progress');
  const binaryStep    = document.getElementById('binary-step');
  const binaryPlay    = document.getElementById('binary-play');
  const binaryPrev    = document.getElementById('binary-prev');
  const binaryNext    = document.getElementById('binary-next');
  const binaryReset   = document.getElementById('binary-reset');

  function getQuery() {
    const mode = AppState.search.mode;
    if (modes[mode] && modes[mode].type === 'bool') {
      return boolInput ? boolInput.value : 'true';
    }
    return searchInput ? searchInput.value.trim() : '';
  }

  function updateModeUI() {
    const mode = AppState.search.mode;
    const cfg  = modes[mode] || modes.cable_id;
    if (searchInput) {
      searchInput.placeholder = cfg.placeholder;
      searchInput.type        = cfg.type === 'number' ? 'number' : 'text';
      searchInput.style.display = cfg.type === 'bool' ? 'none' : '';
    }
    if (boolInput)   boolInput.style.display   = cfg.type === 'bool' ? '' : 'none';
    if (binaryBox)   binaryBox.style.display   = mode === 'cable_id' ? '' : 'none';

    document.querySelectorAll('.mode-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.mode === mode);
    });
  }

  function resetBinary() {
    const s = AppState.search;
    if (s.binaryTimer) { clearInterval(s.binaryTimer); s.binaryTimer = null; }
    if (binaryPlay)    binaryPlay.textContent = 'Reproduzir';
    if (binaryStep)    binaryStep.textContent  = '–';
    if (binaryLog)     binaryLog.innerHTML     = '';
    if (binaryProgress) binaryProgress.style.width = '0%';
    s.binarySteps = [];
    s.binaryIndex = 0;
  }

  function resetSurface() {
    clearEl(searchError);
    resetBinary();
    if (searchEmpty) searchEmpty.style.display = '';
    if (tableWrap)   tableWrap.style.display   = 'none';
    if (resultCount) resultCount.textContent    = 'Selecione uma busca.';
    if (searchHead)  searchHead.innerHTML       = '';
    if (searchBody)  searchBody.innerHTML       = '';
  }

  function renderRows(rows) {
    const headers = ['ação', '#', 'cable_id', 'nome', 'owners', 'comprimento (km)', 'ano RFS', 'operacional', 'endpoints', 'fornecedores'];
    if (searchHead) {
      searchHead.innerHTML = `<tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr>`;
    }
    if (searchBody) {
      searchBody.innerHTML = rows.map((row, i) => `
        <tr data-cable-id="${esc(row.cable_id)}">
          <td><button class="btn-sm detail-action" data-open-detail="${esc(row.cable_id)}">Detalhes</button></td>
          <td class="mono">${i}</td>
          <td class="mono">${esc(row.cable_id)}</td>
          <td>${esc(row.cable_name)}</td>
          <td>${esc(row.owners)}</td>
          <td>${esc(row.length_km)}</td>
          <td>${esc(row.rfs_year)}</td>
          <td>${fmtBool(row.is_operational)}</td>
          <td>${esc(row.landing_points_count)}</td>
          <td>${esc(row.suppliers)}</td>
        </tr>`).join('');
    }
    if (searchEmpty) searchEmpty.style.display = rows.length ? 'none' : '';
    if (tableWrap)   tableWrap.style.display   = rows.length ? '' : 'none';
  }

  // Binary search helpers
  function buildBinarySteps(target) {
    const rows = AppState.allCables;
    const normalizedTarget = target.toLowerCase();
    let lo = 0, hi = rows.length - 1;
    const steps = [];
    while (lo <= hi) {
      const mid     = Math.floor((lo + hi) / 2);
      const current = String(rows[mid].cable_id).toLowerCase();
      const cmp     = current.localeCompare(normalizedTarget);
      steps.push({ lo, hi, mid, current, cmp, found: cmp === 0 });
      if (cmp === 0) break;
      if (cmp < 0)  lo = mid + 1;
      else           hi = mid - 1;
    }
    if (!steps.length || !steps[steps.length - 1].found) {
      steps.push({ lo, hi, mid: -1, current: null, cmp: null, found: false, notFound: true });
    }
    return steps;
  }

  function describeStep(step, index, total) {
    const n = `Passo ${index + 1}/${total}`;
    if (step.notFound)  return `${n}: intervalo vazio (lo=${step.lo}, hi=${step.hi}). Não encontrado.`;
    if (step.found)     return `${n}: midpoint [${step.mid}] = ${step.current}. Encontrado!`;
    if (step.cmp < 0)   return `${n}: midpoint [${step.mid}] = ${step.current}. Valor menor → direita.`;
    return `${n}: midpoint [${step.mid}] = ${step.current}. Valor maior → esquerda.`;
  }

  function applyBinaryStep(index) {
    AppState.search.binaryIndex = index;
    const step  = AppState.search.binarySteps[index];
    const total = AppState.search.binarySteps.length;
    const rows  = searchBody ? Array.from(searchBody.querySelectorAll('tr')) : [];

    rows.forEach((row, ri) => {
      row.classList.remove('bs-eliminated', 'bs-range', 'bs-mid', 'bs-found');
      if (step.notFound) { row.classList.add('bs-eliminated'); return; }
      if (ri < step.lo || ri > step.hi) row.classList.add('bs-eliminated');
      else if (ri === step.mid)         row.classList.add(step.found ? 'bs-found' : 'bs-mid');
      else                               row.classList.add('bs-range');
    });

    if (binaryStep)    binaryStep.textContent     = describeStep(step, index, total);
    if (binaryProgress) binaryProgress.style.width = `${((index + 1) / total) * 100}%`;
    if (binaryLog) {
      binaryLog.innerHTML = AppState.search.binarySteps
        .slice(0, index + 1)
        .map((s, i) => `<div>${esc(describeStep(s, i, total))}</div>`)
        .join('');
    }
  }

  function togglePlayback() {
    const s = AppState.search;
    if (s.binaryTimer) {
      clearInterval(s.binaryTimer);
      s.binaryTimer = null;
      if (binaryPlay) binaryPlay.textContent = 'Reproduzir';
      return;
    }
    if (binaryPlay) binaryPlay.textContent = 'Pausar';
    s.binaryTimer = setInterval(() => {
      if (s.binaryIndex < s.binarySteps.length - 1) {
        applyBinaryStep(s.binaryIndex + 1);
      } else {
        clearInterval(s.binaryTimer);
        s.binaryTimer = null;
        if (binaryPlay) binaryPlay.textContent = 'Reproduzir';
      }
    }, 850);
  }

  async function performSearch() {
    clearEl(searchError);
    resetBinary();
    const mode  = AppState.search.mode;
    const cfg   = modes[mode];
    const query = getQuery();

    try {
      if (mode === 'cable_id') {
        if (!AppState.allCables.length) {
          const payload = await apiFetch({ action: 'search', mode: 'all', q: '' });
          AppState.allCables = extractData(payload);
          populateCableDatalist();
        }
        renderRows(AppState.allCables);
        if (!query) {
          if (resultCount) resultCount.textContent = `${AppState.allCables.length} cabos carregados.`;
          return;
        }
        AppState.search.binarySteps = buildBinarySteps(query);
        applyBinaryStep(0);
        const found = AppState.search.binarySteps[AppState.search.binarySteps.length - 1].found;
        if (resultCount) {
          const n = AppState.search.binarySteps.length;
          resultCount.textContent = found
            ? `1 resultado · busca binária em ${n} passos.`
            : `0 resultados · busca binária encerrada em ${n} passos.`;
        }
        return;
      }

      if (!query && cfg.type !== 'bool') throw new Error('Digite um valor para buscar.');
      const payload = await apiFetch({ action: 'search', mode: cfg.api, q: query });
      const rows    = extractData(payload);
      renderRows(Array.isArray(rows) ? rows : []);
      if (resultCount) {
        const total = payload.total !== undefined ? payload.total : (Array.isArray(rows) ? rows.length : 0);
        resultCount.textContent = `${total} resultado(s).`;
      }
    } catch (err) {
      showError(searchError, err.message);
    }
  }

  // Wire mode buttons
  document.querySelectorAll('.mode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      AppState.search.mode = btn.dataset.mode;
      updateModeUI();
      resetSurface();
    });
  });

  if (searchBtn)  searchBtn.addEventListener('click', performSearch);
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      if (searchInput) searchInput.value = '';
      if (boolInput)   boolInput.value   = 'true';
      resetSurface();
    });
  }
  if (searchInput) {
    searchInput.addEventListener('keydown', e => { if (e.key === 'Enter') performSearch(); });
  }

  // Detail action from table
  if (searchBody) {
    searchBody.addEventListener('click', e => {
      const btn = e.target.closest('[data-open-detail]');
      if (!btn) return;
      Router.go('detail');
      const cableId = btn.dataset.openDetail;
      const picker = document.getElementById('detail-picker');
      if (picker) picker.value = cableId;
      loadDetail(cableId);
    });
  }

  // Binary controls
  if (binaryPlay)  binaryPlay.addEventListener('click', togglePlayback);
  if (binaryPrev) {
    binaryPrev.addEventListener('click', () => {
      if (AppState.search.binaryIndex > 0) {
        if (AppState.search.binaryTimer) togglePlayback();
        applyBinaryStep(AppState.search.binaryIndex - 1);
      }
    });
  }
  if (binaryNext) {
    binaryNext.addEventListener('click', () => {
      const s = AppState.search;
      if (s.binaryIndex < s.binarySteps.length - 1) {
        if (s.binaryTimer) togglePlayback();
        applyBinaryStep(s.binaryIndex + 1);
      }
    });
  }
  if (binaryReset) {
    binaryReset.addEventListener('click', () => {
      if (AppState.search.binarySteps.length) {
        if (AppState.search.binaryTimer) togglePlayback();
        applyBinaryStep(0);
      }
    });
  }

  updateModeUI();
}

// ─── Detail page ──────────────────────────────────────────────────────────────

async function loadDetail(cableId) {
  const picker     = document.getElementById('detail-picker');
  const detailError= document.getElementById('detail-error');
  const detailEmpty= document.getElementById('detail-empty');
  const summary    = document.getElementById('detail-summary');
  const endpointList = document.getElementById('endpoint-list');

  if (!cableId) {
    showError(detailError, 'Selecione um cabo válido.');
    return;
  }
  clearEl(detailError);
  try {
    const payload = await apiFetch({ action: 'detail', cable_id: cableId });
    const cable     = payload.cable     || payload;
    const endpoints = payload.endpoints || [];

    if (picker) picker.value = cableId;

    if (summary) {
      const items = [
        ['cable_id',    cable.cable_id],
        ['nome',        cable.cable_name],
        ['owners',      cable.owners],
        ['fornecedores',cable.suppliers],
        ['comprimento', cable.length_km],
        ['ano RFS',     cable.rfs_year],
        ['operacional', cable.is_operational === null ? null : (cable.is_operational ? 'Sim' : 'Não')],
        ['planejado',   cable.is_planned    === null ? null : (cable.is_planned    ? 'Sim' : 'Não')],
        ['endpoints',   cable.landing_points_count],
        ['notas',       cable.notes],
      ];
      summary.innerHTML = items.map(([k, v]) => `
        <div class="summary-card">
          <div class="k">${esc(k)}</div>
          <div class="v">${esc(v)}</div>
        </div>`).join('');
    }

    if (endpointList) {
      endpointList.innerHTML = endpoints.map(ep => {
        const samples    = ep.ip_samples || [];
        const statusDot  = ep.is_operational ? '🟢' : '🔴';
        const ipRows     = samples.length
          ? samples.map(s => `<tr>
              <td class="mono">${esc(s.network || s.ip_start)}</td>
              <td>${esc(s.city)}</td>
              <td>${esc(s.country_name)}</td>
            </tr>`).join('')
          : '<tr><td colspan="3" class="muted">Sem amostras IP.</td></tr>';

        return `<div class="endpoint-card">
          <div class="endpoint-head">
            <h4>${statusDot} ${esc(ep.endpoint_name)}</h4>
            <span class="muted">${esc(ep.city_name)}, ${esc(ep.country_name)}</span>
          </div>
          ${samples.length ? `<div class="table-wrap"><table>
            <thead><tr><th>rede/IP</th><th>cidade</th><th>país</th></tr></thead>
            <tbody>${ipRows}</tbody>
          </table></div>` : ''}
        </div>`;
      }).join('');
    }

    if (detailEmpty) detailEmpty.style.display = 'none';
  } catch (err) {
    showError(detailError, err.message);
  }
}

function initDetailPage() {
  const picker   = document.getElementById('detail-picker');
  const loadBtn  = document.getElementById('detail-load');
  const detailError = document.getElementById('detail-error');

  if (loadBtn) {
    loadBtn.addEventListener('click', () => {
      const cableId = picker ? picker.value.trim() : '';
      loadDetail(cableId);
    });
  }
  if (picker) {
    picker.addEventListener('keydown', e => {
      if (e.key === 'Enter') loadDetail(picker.value.trim());
    });
  }
}

// ─── Shared: cable datalist & country selects ─────────────────────────────────

function populateCableDatalist() {
  const cablesForDatalist = AppState.allCables;
  document.querySelectorAll('.cable-datalist').forEach(dl => {
    dl.innerHTML = cablesForDatalist.map(c =>
      `<option value="${esc(c.cable_id)}">${esc(c.cable_name)}</option>`
    ).join('');
  });
}

async function loadCableList() {
  try {
    const payload = await apiFetch({ action: 'search', mode: 'all', q: '' });
    AppState.allCables = extractData(payload);
    populateCableDatalist();

    // Populate probe cable select
    const probeCable = document.getElementById('probe-cable');
    if (probeCable) {
      probeCable.innerHTML = AppState.allCables.map(c =>
        `<option value="${esc(c.cable_id)}">${esc(c.cable_name)}</option>`
      ).join('');
    }
  } catch (err) {
    console.warn('loadCableList error:', err.message);
  }
}

async function loadCountries() {
  try {
    const payload = await apiFetch({ action: 'countries' });
    AppState.countries = extractData(payload);

    const options = AppState.countries.map(c =>
      `<option value="${esc(c)}">${esc(c)}</option>`
    ).join('');

    ['route-country-a', 'route-country-b', 'cut-country-a', 'cut-country-b', 'risk-country'].forEach((id, i) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.innerHTML = options;
      // Default second country select to index 1
      if (i % 2 === 1 && AppState.countries.length > 1) {
        el.value = AppState.countries[1];
      }
    });
  } catch (err) {
    console.warn('loadCountries error:', err.message);
  }
}

// ─── Routes page ──────────────────────────────────────────────────────────────

function renderTopRoutes(payload, resultEl) {
  const routes = payload.routes || payload.data || [];
  if (!routes.length) {
    resultEl.innerHTML = '<p class="muted">Nenhuma rota encontrada.</p>';
    return;
  }
  resultEl.innerHTML = `<h3>Top ${routes.length} rotas</h3>` + routes.map((route, i) => {
    const cables = (route.cables || []).map(c =>
      `${esc(c.cable_name)} <span class="muted">(${esc(c.from)} → ${esc(c.to)})</span>`
    ).join('<br>');
    return `<div class="route-card">
      <strong>#${i + 1}</strong>
      ${route.score    !== undefined ? ` · score ${esc(route.score)}` : ''}
      ${route.hops     !== undefined ? ` · hops ${esc(route.hops)}` : ''}
      ${route.distance_km !== undefined ? ` · ${esc(route.distance_km)} km` : ''}
      <div class="muted">${esc((route.countries || []).join(' → '))}</div>
      ${cables ? `<div style="margin-top:6px;">${cables}</div>` : ''}
    </div>`;
  }).join('');
}

function renderCriticalCuts(payload, resultEl) {
  const cables = payload.critical_cables || payload.data || [];
  if (!cables.length) {
    resultEl.innerHTML = '<p class="muted">Nenhum cabo crítico encontrado.</p>';
    return;
  }
  const rows = cables.map(c => `<tr>
    <td class="mono">${esc(c.cable_id)}</td>
    <td>${esc(c.cable_name)}</td>
    <td>${esc(c.routes_hit)}</td>
    <td>${esc(c.impact)}</td>
  </tr>`).join('');
  resultEl.innerHTML = `
    <h3>Cabos críticos</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>ID</th><th>cabo</th><th>rotas impactadas</th><th>impacto</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
}

function renderEndpointRisk(payload, resultEl) {
  const endpoints = payload.endpoints || payload.data || [];
  if (!endpoints.length) {
    resultEl.innerHTML = '<p class="muted">Nenhum dado de risco disponível.</p>';
    return;
  }
  const rows = endpoints.map(ep => `<tr>
    <td class="mono">${esc(ep.endpoint_id)}</td>
    <td>${esc(ep.endpoint_name)}</td>
    <td>${esc(ep.city_name)}</td>
    <td>${esc(ep.cable_degree)}</td>
    <td>${esc(ep.country_reach)}</td>
    <td>${esc(ep.risk_score)}</td>
    <td>${esc(ep.packet_loss_risk_pct)}%</td>
    <td>${esc(ep.congestion_level)}</td>
  </tr>`).join('');
  resultEl.innerHTML = `
    <h3>Risco de endpoints · ${esc(payload.country || '')}</h3>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>endpoint_id</th><th>endpoint</th><th>cidade</th>
        <th>conexões</th><th>alcance</th><th>risco</th>
        <th>perda pct</th><th>congestionamento</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
}

function initRoutesPage() {
  const btnTopRoutes    = document.getElementById('btn-top-routes');
  const btnCriticalCuts = document.getElementById('btn-critical-cuts');
  const btnEndpointRisk = document.getElementById('btn-endpoint-risk');

  if (btnTopRoutes) {
    btnTopRoutes.addEventListener('click', async () => {
      const errEl   = document.getElementById('route-error');
      const resultEl= document.getElementById('route-result');
      clearEl(errEl);
      try {
        const countryA = (document.getElementById('route-country-a') || {}).value || '';
        const countryB = (document.getElementById('route-country-b') || {}).value || '';
        const fd = new FormData();
        fd.append('action', 'top_routes');
        fd.append('country_a', countryA);
        fd.append('country_b', countryB);
        const payload = await apiPost(fd);
        if (resultEl) renderTopRoutes(payload, resultEl);
      } catch (err) {
        showError(errEl, err.message);
      }
    });
  }

  if (btnCriticalCuts) {
    btnCriticalCuts.addEventListener('click', async () => {
      const errEl   = document.getElementById('cuts-error');
      const resultEl= document.getElementById('cuts-result');
      clearEl(errEl);
      try {
        const countryA = (document.getElementById('cut-country-a') || {}).value || '';
        const countryB = (document.getElementById('cut-country-b') || {}).value || '';
        const fd = new FormData();
        fd.append('action', 'critical_cuts');
        fd.append('country_a', countryA);
        fd.append('country_b', countryB);
        const payload = await apiPost(fd);
        if (resultEl) renderCriticalCuts(payload, resultEl);
      } catch (err) {
        showError(errEl, err.message);
      }
    });
  }

  if (btnEndpointRisk) {
    btnEndpointRisk.addEventListener('click', async () => {
      const errEl   = document.getElementById('risk-error');
      const resultEl= document.getElementById('risk-result');
      clearEl(errEl);
      try {
        const country = (document.getElementById('risk-country') || {}).value || '';
        const fd = new FormData();
        fd.append('action', 'endpoint_risk');
        fd.append('country', country);
        const payload = await apiPost(fd);
        if (resultEl) renderEndpointRisk(payload, resultEl);
      } catch (err) {
        showError(errEl, err.message);
      }
    });
  }
}

// ─── Telemetry page ───────────────────────────────────────────────────────────

function buildLinePoints(values, width, height, padding) {
  if (values.length < 2) return '';
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const span = Math.max(0.001, maxV - minV);
  return values.map((v, i) => {
    const x = padding + i * (width  - padding * 2) / Math.max(1, values.length - 1);
    const y = height - padding - ((v - minV) / span) * (height - padding * 2);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
}

function renderTelemetryChart(history, ciLatency, metric = 'latency_ms', label = 'Latência (ms)') {
  const valid = history.map(r => Number(r[metric])).filter(v => isFinite(v));
  if (valid.length < 2) return '<p class="muted">Amostras insuficientes para o gráfico.</p>';

  const W = 920, H = 220, P = 26;
  const pts = buildLinePoints(valid, W, H, P);

  const metricBtns = [
    ['latency_ms',      'Latência'],
    ['jitter_ms',       'Jitter'],
    ['packet_loss_pct', 'Perda %'],
  ].map(([m, lbl]) =>
    `<button class="btn-sm ${m === metric ? 'primary' : ''}"
       onclick="window.__telemetryMetric('${m}','${lbl} (${m})')">
       ${lbl}
     </button>`
  ).join('');

  const ciText = Array.isArray(ciLatency)
    ? `IC 95%: ${ciLatency.join(' – ')} ms`
    : '';

  return `
    <div class="telemetry-chart-wrap">
      <div style="display:flex;gap:6px;margin-bottom:8px;">${metricBtns}</div>
      <svg class="telemetry-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <line class="telemetry-axis" x1="${P}" y1="${H-P}" x2="${W-P}" y2="${H-P}"></line>
        <line class="telemetry-axis" x1="${P}" y1="${P}"   x2="${P}"   y2="${H-P}"></line>
        <polyline class="telemetry-line" points="${pts}"></polyline>
      </svg>
      <div class="telemetry-legend">${esc(label)} ao longo da janela. ${esc(ciText)}</div>
    </div>`;
}

function animateGauge(gaugeId, valueId, target, maxValue, unit) {
  const circle    = document.getElementById(gaugeId);
  const valueNode = document.getElementById(valueId);
  if (!circle || !valueNode) return;
  const radius       = 48;
  const circumference= 2 * Math.PI * radius;
  const clamped      = Math.min(Number(target) || 0, maxValue);
  const dp           = (unit === 'pps' || unit === 'maq') ? 0 : 1;
  const start        = performance.now();

  (function tick(now) {
    const progress = Math.min(1, (now - start) / 1300);
    const curr     = clamped * progress;
    const ratio    = curr / maxValue;
    circle.style.strokeDasharray  = circumference;
    circle.style.strokeDashoffset = circumference * (1 - ratio);
    valueNode.textContent = `${curr.toFixed(dp)} ${unit}`;
    if (progress < 1) requestAnimationFrame(tick);
  })(performance.now());
}

function renderTelemetryResult(payload) {
  const cable     = (payload.details && payload.details.cable) || payload.cable || {};
  const telemetry = payload.telemetry || payload;
  const history   = Array.isArray(telemetry.history) ? telemetry.history : [];
  const latP      = (telemetry.percentiles && telemetry.percentiles.latency_ms)      || {};
  const thrP      = (telemetry.percentiles && telemetry.percentiles.throughput_mbps) || {};
  const plP       = (telemetry.percentiles && telemetry.percentiles.packet_loss_pct) || {};
  const ci        = telemetry.confidence_interval_latency_ms;

  // Cache for metric switching
  AppState.lastTelemetryHistory    = history;
  AppState.lastTelemetryCiLatency  = ci;

  window.__telemetryMetric = (metric, label) => {
    const chartDiv = document.getElementById('telemetry-chart-container');
    if (chartDiv) chartDiv.innerHTML = renderTelemetryChart(AppState.lastTelemetryHistory, AppState.lastTelemetryCiLatency, metric, label);
  };

  const chartHtml = renderTelemetryChart(history, ci);

  const resultEl = document.getElementById('telemetry-result');
  if (!resultEl) return;

  resultEl.innerHTML = `
    <h3>Telemetria · ${esc(cable.cable_name || 'Cabo')}</h3>
    <div class="detail-summary" style="margin-bottom:12px;">
      <div class="summary-card"><div class="k">janela</div><div class="v">${esc(telemetry.window_start)}</div></div>
      <div class="summary-card"><div class="k">qualidade</div><div class="v">${esc(telemetry.data_quality_score)} / 100</div></div>
      <div class="summary-card"><div class="k">origem</div><div class="v">${esc(telemetry.source)}</div></div>
      <div class="summary-card"><div class="k">amostras</div><div class="v">${esc(telemetry.sample_size || history.length)}</div></div>
    </div>
    <div class="telemetry-grid">
      <div class="gauge">
        <svg viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="48" fill="none" stroke="#30363d" stroke-width="10"></circle>
          <circle id="gauge-latency" cx="60" cy="60" r="48" fill="none" stroke="#58a6ff" stroke-width="10" transform="rotate(-90 60 60)"></circle>
        </svg>
        <div class="value" id="value-latency">0 ms</div>
        <div class="label">latência</div>
      </div>
      <div class="gauge">
        <svg viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="48" fill="none" stroke="#30363d" stroke-width="10"></circle>
          <circle id="gauge-pps" cx="60" cy="60" r="48" fill="none" stroke="#3fb950" stroke-width="10" transform="rotate(-90 60 60)"></circle>
        </svg>
        <div class="value" id="value-pps">0 pps</div>
        <div class="label">pacotes/s</div>
      </div>
      <div class="gauge">
        <svg viewBox="0 0 120 120">
          <circle cx="60" cy="60" r="48" fill="none" stroke="#30363d" stroke-width="10"></circle>
          <circle id="gauge-machines" cx="60" cy="60" r="48" fill="none" stroke="#f2cc60" stroke-width="10" transform="rotate(-90 60 60)"></circle>
        </svg>
        <div class="value" id="value-machines">0 maq</div>
        <div class="label">máquinas est.</div>
      </div>
    </div>
    <div class="subhead" style="margin-top:16px;">Série Temporal</div>
    <div id="telemetry-chart-container">${chartHtml}</div>
    <div class="subhead" style="margin-top:16px;">Percentis (Baseline)</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Métrica</th><th>p50</th><th>p95</th><th>p99</th></tr></thead>
        <tbody>
          <tr><td>Latência (ms)</td><td>${latP.p50 ?? '–'}</td><td>${latP.p95 ?? '–'}</td><td>${latP.p99 ?? '–'}</td></tr>
          <tr><td>Throughput (Mbps)</td><td>${thrP.p50 ?? '–'}</td><td>${thrP.p95 ?? '–'}</td><td>${thrP.p99 ?? '–'}</td></tr>
          <tr><td>Perda pct</td><td>${plP.p50 ?? '–'}</td><td>${plP.p95 ?? '–'}</td><td>${plP.p99 ?? '–'}</td></tr>
        </tbody>
      </table>
    </div>`;

  animateGauge('gauge-latency',  'value-latency',  telemetry.latency_ms,                   250,     'ms');
  animateGauge('gauge-pps',      'value-pps',       telemetry.packets_per_second,            3000000, 'pps');
  animateGauge('gauge-machines', 'value-machines',  telemetry.connected_machines_estimate,   1200000, 'maq');
}

function initTelemetryPage() {
  const btn       = document.getElementById('btn-telemetry');
  const errEl     = document.getElementById('telemetry-error');
  const resultEl  = document.getElementById('telemetry-result');

  if (!btn) return;
  btn.addEventListener('click', async () => {
    clearEl(errEl);
    const cableId = (document.getElementById('telemetry-cable') || {}).value
      ? document.getElementById('telemetry-cable').value.trim()
      : '';
    const hours   = parseInt(
      (document.getElementById('telemetry-hours') || {}).value || '24', 10
    ) || 24;

    if (!cableId) { showError(errEl, 'Selecione um cabo.'); return; }
    try {
      const payload = await apiFetch({ action: 'telemetry', cable_id: cableId, hours });
      renderTelemetryResult(payload);
    } catch (err) {
      showError(errEl, err.message);
    }
  });
}

// ─── Probe page ───────────────────────────────────────────────────────────────

function renderProbeResult(payload) {
  const r = payload.result || payload;
  const reachable   = r.reachable !== undefined ? r.reachable : true;
  const statusCls   = reachable ? 'yes' : 'no';
  const statusTxt   = reachable ? 'Acessível' : 'Inacessível';
  const lossClass   = (r.packet_loss_pct ?? 0) > 5  ? 'danger-text' : 'ok-text';
  const jitterClass = (r.jitter_ms ?? 0)       > 20 ? 'danger-text' : 'ok-text';

  const hopsRows = (r.hops || []).map((hop, i) => `<tr>
    <td>${i + 1}</td>
    <td class="mono">${esc(hop.ip || '*')}</td>
    <td>${esc(hop.host || '*')}</td>
    <td>${hop.rtt_ms != null ? hop.rtt_ms + ' ms' : '*'}</td>
  </tr>`).join('');

  const openPorts = (r.open_ports || r.ports_tested || []);
  const portsBadge = openPorts.length
    ? `<div class="muted" style="font-size:12px;margin-top:4px;">portas: ${openPorts.join(' · ')}</div>`
    : '';

  const historyBadge = payload.history_file
    ? `<div style="margin-top:6px;font-size:12px;color:#3fb950;">&#10003; Histórico: <span class="mono">${esc(payload.history_file)}</span></div>`
    : '';

  return `
    <div class="probe-status">
      <span class="badge ${statusCls}">${statusTxt}</span>
      <span class="muted" style="margin-left:10px;">${esc(r.target_ip || r.ip || payload.target || '')}</span>
    </div>
    ${portsBadge}
    ${historyBadge}
    <div class="telemetry-grid" style="margin-top:12px;">
      <div class="gauge-mini"><div class="k">RTT Médio</div>
        <div class="v accent">${r.avg_rtt_ms != null ? r.avg_rtt_ms + ' ms' : r.latency_ms != null ? r.latency_ms + ' ms' : 'N/A'}</div></div>
      <div class="gauge-mini"><div class="k">Jitter</div>
        <div class="v ${jitterClass}">${r.jitter_ms != null ? r.jitter_ms + ' ms' : 'N/A'}</div></div>
      <div class="gauge-mini"><div class="k">Perda de Pacotes</div>
        <div class="v ${lossClass}">${r.packet_loss_pct != null ? r.packet_loss_pct + '%' : 'N/A'}</div></div>
      <div class="gauge-mini"><div class="k">Throughput Est.</div>
        <div class="v">${r.throughput_mbps != null ? r.throughput_mbps + ' Mbps' : 'N/A'}</div></div>
      <div class="gauge-mini"><div class="k">Protocolo</div>
        <div class="v">${esc(r.method || 'synthetic')}</div></div>
      <div class="gauge-mini"><div class="k">Amostras</div>
        <div class="v">${r.samples ?? '–'}</div></div>
    </div>
    ${hopsRows ? `
    <div class="subhead" style="margin-top:14px;">Rota (hops)</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>IP</th><th>Host</th><th>RTT</th></tr></thead>
        <tbody>${hopsRows}</tbody>
      </table>
    </div>` : ''}
    ${r.note ? `<div class="muted" style="margin-top:10px;font-size:12px;">${esc(r.note)}</div>` : ''}`;
}

async function loadProbeEndpoints(cableId) {
  const endpointSel = document.getElementById('probe-endpoint-select');
  const ipInput     = document.getElementById('probe-ip');
  const errEl       = document.getElementById('probe-error');
  if (!cableId || !endpointSel) return;
  try {
    const payload   = await apiFetch({ action: 'endpoints', cable_id: cableId });
    const endpoints = extractData(payload);
    endpointSel.innerHTML = (Array.isArray(endpoints) ? endpoints : []).map(ep => {
      const ip = (ep.ip_samples && ep.ip_samples[0]) ? (ep.ip_samples[0].ip_start || '') : (ep.ip_sample || '');
      return `<option value="${esc(ep.endpoint_id)}" data-ip="${esc(ip)}">${esc(ep.endpoint_name)} — ${esc(ep.city_name)}</option>`;
    }).join('');
    // Auto-fill IP from first endpoint
    if (endpointSel.options.length && ipInput) {
      ipInput.value = endpointSel.options[0].dataset.ip || '';
    }
  } catch (err) {
    showError(errEl, err.message);
  }
}

function initProbePage() {
  const cableSel    = document.getElementById('probe-cable');
  const endpointSel = document.getElementById('probe-endpoint-select');
  const ipInput     = document.getElementById('probe-ip');
  const fullScan    = document.getElementById('probe-full-scan');
  const btn         = document.getElementById('btn-probe');
  const errEl       = document.getElementById('probe-error');
  const resultEl    = document.getElementById('probe-result');

  if (cableSel) {
    cableSel.addEventListener('change', () => loadProbeEndpoints(cableSel.value));
  }

  if (endpointSel) {
    endpointSel.addEventListener('change', () => {
      const opt = endpointSel.options[endpointSel.selectedIndex];
      if (opt && ipInput) ipInput.value = opt.dataset.ip || '';
    });
  }

  if (btn) {
    btn.addEventListener('click', async () => {
      clearEl(errEl);
      const cableId    = cableSel    ? cableSel.value.trim()    : '';
      const endpointId = endpointSel ? endpointSel.value.trim() : '';
      const ip         = ipInput     ? ipInput.value.trim()     : '';
      const full       = fullScan    ? (fullScan.checked ? '1' : '0') : '0';

      if (!cableId) { showError(errEl, 'Selecione um cabo.'); return; }

      btn.disabled    = true;
      btn.textContent = 'Sondando…';

      try {
        const fd = new FormData();
        fd.append('action',      'probe');
        fd.append('cable_id',    cableId);
        fd.append('endpoint_id', endpointId);
        fd.append('ip',          ip);
        fd.append('full',        full);
        const payload = await apiPost(fd);
        const html    = renderProbeResult(payload);

        // Show in inline result area
        if (resultEl) resultEl.innerHTML = html;

        // Also show in floating modal
        ProbeModal.show('Resultado da Sonda — ' + cableId, html);
      } catch (err) {
        showError(errEl, err.message);
      } finally {
        btn.disabled    = false;
        btn.textContent = 'Iniciar Sonda';
      }
    });
  }
}

// ─── Drawer & modal wire-up ───────────────────────────────────────────────────

function initNavigation() {
  const hamburgerBtn = document.getElementById('hamburger-btn');
  const drawerClose  = document.getElementById('drawer-close');
  const drawerOverlay= document.getElementById('drawer-overlay');

  if (hamburgerBtn) hamburgerBtn.addEventListener('click', () => Drawer.toggle());
  if (drawerClose)  drawerClose.addEventListener('click',  () => Drawer.close());
  if (drawerOverlay)drawerOverlay.addEventListener('click', () => Drawer.close());

  // Logo click → back to map
  const logo = document.querySelector('.header-logo');
  if (logo) {
    logo.style.cursor = 'pointer';
    logo.addEventListener('click', () => Router.go('map'));
  }

  document.querySelectorAll('.nav-link').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      Router.go(a.dataset.page);
    });
  });

  // Probe modal close
  const modalClose = document.getElementById('modal-close');
  if (modalClose) modalClose.addEventListener('click', () => ProbeModal.hide());
  const probeModalEl = document.getElementById('probe-modal');
  if (probeModalEl) {
    probeModalEl.addEventListener('click', e => {
      if (e.target === probeModalEl) ProbeModal.hide();
    });
  }
}

// ─── Initialization ───────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  initNavigation();
  Router.go('map');
  initGlobe();
  initGlobePanel();

  // Load shared data
  await Promise.allSettled([loadCableList(), loadCountries()]);

  initSearchPage();
  initDetailPage();
  initRoutesPage();
  initTelemetryPage();
  initProbePage();

  // Periodically refresh alert badge
  setInterval(async () => {
    try {
      const payload = await apiFetch({ action: 'alerts' });
      const alerts  = extractData(payload);
      const count   = Array.isArray(alerts) ? alerts.length : 0;
      updateAlertBadge(count);
      updateAlertTicker(alerts);
    } catch {
      // silent
    }
  }, 60000);
});
