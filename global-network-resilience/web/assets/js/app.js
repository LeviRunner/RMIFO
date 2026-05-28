/* ============================================================
   Global Network Resilience — SPA app.js
   Architecture: 6 pages (map, search, detail, routes, telemetry, probe)
   Backend: api.php  |  Map: MapLibre GL 4 (globe projection, WebGL)
   ============================================================ */

'use strict';

// ─── Constants ────────────────────────────────────────────────────────────────

const API_BASE = 'api.php';

// Inline SVG icon strings — sourced from web/assets/svg/
const ICONS = {
  clock: `<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="6.25"/><polyline points="8 4.75 8 8 10.25 9.25"/></svg>`,
  check: `<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="2.5 8.5 6.5 12.5 13.5 3.5"/></svg>`,
  xmark: `<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><line x1="3.5" y1="3.5" x2="12.5" y2="12.5"/><line x1="12.5" y1="3.5" x2="3.5" y2="12.5"/></svg>`,
  alert: `<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 1.5L1.5 13.5h13z"/><line x1="8" y1="6" x2="8" y2="9.5"/><circle cx="8" cy="11.5" r="0.5" fill="currentColor" stroke="none"/></svg>`,
  play:  `<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><polygon points="4 2.5 13 8 4 13.5"/></svg>`,
  dot:   `<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="8" cy="8" r="4"/></svg>`,
};

const ACTION_ALIAS = {
  detail:         'details',
  critical_cuts:  'critical_cables',
  search:         null,
};

// ─── RMIFO Visual Utilities ───────────────────────────────────────────────────

function rmifoCount(el, target, ms) {
  if (!el) return;
  ms = ms || 650;
  const isFloat = String(target).includes('.');
  const start = performance.now();
  (function tick(now) {
    const p = Math.min((now - start) / ms, 1);
    const v = target * (1 - Math.pow(1 - p, 3));
    el.textContent = isFloat ? v.toFixed(1) : Math.round(v);
    if (p < 1) requestAnimationFrame(tick);
    else el.textContent = isFloat ? Number(target).toFixed(1) : target;
  })(start);
}

function rmifoToast(msg, tipo, ms) {
  tipo = tipo || 'info';
  ms   = ms   || 3000;
  const t = document.createElement('div');
  t.style.cssText = [
    'position:fixed;bottom:24px;right:24px;z-index:9999',
    'background:#120d22;border-radius:10px;padding:12px 18px',
    'font-size:13px;color:#f4efff;pointer-events:none',
    'box-shadow:0 12px 40px rgba(0,0,0,.5)',
    'animation:rmifo-toast-in 260ms cubic-bezier(.16,1,.3,1) both',
    tipo === 'success' ? 'border:1px solid rgba(65,218,140,.45)'  :
    tipo === 'error'   ? 'border:1px solid rgba(255,111,141,.45)' :
    tipo === 'warning' ? 'border:1px solid rgba(240,178,79,.45)'  :
                         'border:1px solid rgba(168,113,255,.35)',
  ].join(';');
  t.textContent = msg;
  if (!document.getElementById('rmifo-toast-style')) {
    const s = document.createElement('style');
    s.id = 'rmifo-toast-style';
    s.textContent = `@keyframes rmifo-toast-in{from{opacity:0;transform:translateY(14px) scale(.94)}to{opacity:1;transform:translateY(0) scale(1)}}`;
    document.head.appendChild(s);
  }
  document.body.appendChild(t);
  setTimeout(() => {
    t.style.transition = 'opacity 200ms ease';
    t.style.opacity = '0';
    setTimeout(() => t.remove(), 200);
  }, ms);
}

function rmifoDebounce(fn, delay) {
  let timer;
  return function() {
    const a = arguments, ctx = this;
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(ctx, a), delay || 300);
  };
}

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
    map:     'Mapa Global',
    search:  'Explorador de Banco de Dados',
    detail:  'Detalhes do Cabo',
    routes:  'Top 3 Rotas',
    risk:    'Risco de Endpoints',
    cuts:    'Cortes Críticos',
    probe:   'Histórico de Janela de Conexão',
    daily:   'Sonda Diária',
    switch:  'IP Range',
    history: 'Histórico',
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
  _stopFn: null,  // callback to stop RT simulation when modal closes
  show(title, html) {
    if (this.titleEl) this.titleEl.textContent = title;
    if (this.bodyEl)  this.bodyEl.innerHTML    = html;
    if (this.el)      this.el.classList.add('open');
  },
  hide() {
    if (this._stopFn) { this._stopFn(); this._stopFn = null; }
    const box = document.getElementById('probe-modal-box');
    box?.classList.remove('rt-mode');
    if (this.el) this.el.classList.remove('open');
  },
};

// ─── Shared state ─────────────────────────────────────────────────────────────

const AppState = {
  allCables: [],           // [{cable_id, cable_name, ...}]
  countries: [],           // ['Brazil', 'France', ...]
  map: null,               // MapLibre GL instance
  globeSelection: null,    // currently selected feature on map
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
  // Daily probe
  probeActiveCable: null,   // cable_id being probed right now
  globePaths: [],           // reference to last loaded globe path array
};

// ─── City / capital dataset ───────────────────────────────────────────────────
// Format: [lat, lng, name, tier]
// tier 1 = capital (always visible), tier 2 = major city, tier 3 = regional/state
const CITY_DATA = [
  // ── World capitals (tier 1) ─────────────────────────────────────────────────
  // Africa
  [36.74,3.06,'Argel',1],[-8.84,13.23,'Luanda',1],[6.37,2.42,'Porto Novo',1],
  [-24.65,25.91,'Gaborone',1],[12.37,-1.53,'Uagadugu',1],[-3.43,29.93,'Gitega',1],
  [3.87,11.52,'Iaundê',1],[14.93,-23.51,'Praia',1],[4.36,18.55,'Bangui',1],
  [12.11,15.05,"N'Djamena",1],[-11.70,43.26,'Moroni',1],[-4.32,15.32,'Kinshasa',1],
  [11.59,43.15,'Jibuti',1],[30.05,31.25,'Cairo',1],[3.75,8.78,'Malabo',1],
  [15.34,38.93,'Asmara',1],[9.02,38.75,'Adis Abeba',1],[0.39,9.45,'Libreville',1],
  [13.45,-16.58,'Banjul',1],[5.55,-0.20,'Acra',1],[9.54,-13.68,'Conacri',1],
  [11.86,-15.60,'Bissau',1],[6.82,-5.28,'Yamussucro',1],[-1.29,36.82,'Nairóbi',1],
  [-29.32,27.48,'Maseru',1],[6.30,-10.80,'Monróvia',1],[32.90,13.18,'Trípoli',1],
  [-18.91,47.54,'Antananarivo',1],[-13.97,33.79,'Lilongue',1],[12.65,-8.00,'Bamaco',1],
  [18.08,-15.97,'Nuaquechote',1],[-20.16,57.50,'Port Louis',1],[34.02,-6.84,'Rabat',1],
  [-25.97,32.58,'Maputo',1],[-22.56,17.08,'Vinduque',1],[13.51,2.12,'Niamei',1],
  [9.07,7.40,'Abuja',1],[-1.95,30.06,'Kigali',1],[0.34,6.73,'São Tomé',1],
  [14.72,-17.47,'Dacar',1],[-4.62,55.45,'Vitória',1],[8.49,-13.23,'Freetown',1],
  [2.05,45.34,'Mogadíscio',1],[-25.74,28.19,'Pretória',1],[4.86,31.60,'Juba',1],
  [15.60,32.53,'Cartum',1],[-6.18,35.74,'Dodoma',1],[6.14,1.22,'Lomé',1],
  [36.82,10.18,'Tunes',1],[0.31,32.58,'Campala',1],[-15.42,28.28,'Lusaca',1],
  [-17.83,31.05,'Harare',1],[-26.32,31.14,'Mbabane',1],
  // Americas
  [17.12,-61.85,"Saint John's",1],[-34.60,-58.38,'Buenos Aires',1],
  [25.06,-77.34,'Nassau',1],[13.10,-59.62,'Bridgetown',1],[17.25,-88.77,'Belmopan',1],
  [-19.03,-65.26,'Sucre',1],[-15.78,-47.93,'Brasília',1],[45.42,-75.69,'Ottawa',1],
  [-33.45,-70.67,'Santiago',1],[4.71,-74.07,'Bogotá',1],[9.93,-84.08,'San José',1],
  [23.13,-82.38,'Havana',1],[15.30,-61.39,'Roseau',1],[18.48,-69.90,'Santo Domingo',1],
  [-0.22,-78.51,'Quito',1],[13.70,-89.19,'San Salvador',1],[12.05,-61.75,"St. George's",1],
  [14.64,-90.51,'Cidade da Guatemala',1],[6.80,-58.16,'Georgetown',1],
  [18.54,-72.34,'Porto Príncipe',1],[14.09,-87.21,'Tegucigalpa',1],
  [17.99,-76.79,'Kingston',1],[19.43,-99.13,'Cidade do México',1],
  [12.13,-86.28,'Manágua',1],[8.99,-79.52,'Cidade do Panamá',1],
  [-25.29,-57.65,'Assunção',1],[-12.05,-77.04,'Lima',1],[17.30,-62.72,'Basseterre',1],
  [14.01,-60.99,'Castries',1],[13.16,-61.22,'Kingstown',1],[5.85,-55.20,'Paramaribo',1],
  [10.65,-61.52,'Porto de Espanha',1],[38.90,-77.04,'Washington',1],
  [-34.90,-56.19,'Montevidéu',1],[10.49,-66.88,'Caracas',1],
  // Asia
  [34.53,69.17,'Cabul',1],[40.18,44.51,'Erevan',1],[40.41,49.87,'Baku',1],
  [26.23,50.57,'Manama',1],[23.72,90.41,'Dacca',1],[27.47,89.64,'Timphu',1],
  [4.94,114.95,'Bandar Seri Begawan',1],[11.56,104.92,'Phnom Penh',1],
  [39.91,116.39,'Pequim',1],[35.17,33.37,'Nicósia',1],[41.69,44.83,'Tbilisi',1],
  [28.61,77.21,'Nova Delhi',1],[-6.21,106.85,'Jacarta',1],[35.69,51.42,'Teerã',1],
  [33.34,44.40,'Bagdá',1],[31.77,35.22,'Jerusalém',1],[35.69,139.69,'Tóquio',1],
  [31.95,35.93,'Amã',1],[51.18,71.45,'Astana',1],[29.37,47.98,'Kuwait',1],
  [42.87,74.59,'Bisqueque',1],[17.97,102.60,'Vientiane',1],[33.89,35.50,'Beirute',1],
  [3.15,101.69,'Kuala Lumpur',1],[4.17,73.51,'Malé',1],[47.91,106.88,'Ulã Bator',1],
  [19.76,96.08,'Naipidó',1],[27.71,85.32,'Catmandu',1],[39.03,125.75,'Piongiang',1],
  [23.59,58.59,'Mascate',1],[33.72,73.04,'Islamabade',1],[14.59,120.98,'Manila',1],
  [25.29,51.53,'Doha',1],[24.69,46.72,'Riade',1],[1.35,103.82,'Singapura',1],
  [37.57,126.98,'Seul',1],[6.93,79.86,'Colombo',1],[33.51,36.29,'Damasco',1],
  [25.05,121.52,'Taipé',1],[38.56,68.77,'Duchambé',1],[13.75,100.52,'Bangcoc',1],
  [-8.56,125.58,'Díli',1],[37.94,58.38,'Ascabade',1],[24.45,54.37,'Abu Dhabi',1],
  [41.30,69.24,'Tashkent',1],[21.03,105.85,'Hanói',1],[15.35,44.21,"Saná'a",1],
  // Europe
  [41.33,19.83,'Tirana',1],[42.51,1.52,'Andorra la Vella',1],[48.21,16.37,'Viena',1],
  [53.90,27.56,'Minsk',1],[50.85,4.35,'Bruxelas',1],[43.85,18.38,'Sarajevo',1],
  [42.70,23.32,'Sofia',1],[45.81,15.98,'Zagreb',1],[50.09,14.42,'Praga',1],
  [55.68,12.57,'Copenhague',1],[59.44,24.73,'Tallin',1],[60.17,24.93,'Helsinque',1],
  [48.86,2.35,'Paris',1],[52.52,13.40,'Berlim',1],[37.97,23.73,'Atenas',1],
  [47.50,19.04,'Budapeste',1],[64.13,-21.82,'Reiquiavique',1],[53.33,-6.25,'Dublin',1],
  [41.90,12.49,'Roma',1],[42.67,21.17,'Pristina',1],[56.95,24.11,'Riga',1],
  [54.69,25.28,'Vilnius',1],[49.61,6.13,'Luxemburgo',1],[35.90,14.51,'Valeta',1],
  [47.00,28.86,'Chisinau',1],[43.73,7.42,'Mônaco',1],[42.44,19.26,'Podgorica',1],
  [52.37,4.90,'Amsterdã',1],[41.99,21.43,'Escópio',1],[59.91,10.75,'Oslo',1],
  [52.23,21.01,'Varsóvia',1],[38.72,-9.14,'Lisboa',1],[44.43,26.10,'Bucareste',1],
  [55.75,37.62,'Moscou',1],[43.94,12.46,'San Marino',1],[44.80,20.46,'Belgrado',1],
  [48.15,17.11,'Bratislava',1],[46.06,14.51,'Liubliana',1],[40.42,-3.70,'Madri',1],
  [59.33,18.07,'Estocolmo',1],[46.95,7.45,'Berna',1],[39.92,32.85,'Ancara',1],
  [50.45,30.52,'Kiev',1],[51.51,-0.13,'Londres',1],[41.90,12.45,'Vaticano',1],
  [47.14,9.52,'Vaduz',1],
  // Oceania
  [-35.28,149.13,'Camberra',1],[-18.14,178.44,'Suva',1],[-9.45,147.19,'Port Moresby',1],
  [-41.29,174.78,'Wellington',1],[-13.83,-171.77,'Ápia',1],[-9.43,160.05,'Honiara',1],
  [-21.14,-175.22,'Nukualofa',1],[-17.73,168.32,'Port Vila',1],
  [-8.52,179.20,'Funafuti',1],[7.50,134.62,'Ngerulmud',1],[6.92,158.16,'Palikir',1],

  // ── Grandes metrópoles não-capitais (tier 2) ────────────────────────────────
  [-23.55,-46.63,'São Paulo',2],[31.23,121.47,'Xangai',2],[19.08,72.88,'Mumbai',2],
  [6.45,3.40,'Lagos',2],[41.01,28.95,'Istambul',2],[34.69,135.50,'Osaka',2],
  [23.13,113.26,'Guangzhou',2],[40.71,-74.01,'Nova York',2],[34.05,-118.24,'Los Angeles',2],
  [41.85,-87.65,'Chicago',2],[22.54,114.06,'Shenzhen',2],[29.56,106.55,'Chongqing',2],
  [12.97,77.59,'Bangalore',2],[31.55,74.34,'Lahore',2],[-22.91,-43.17,'Rio de Janeiro',2],
  [22.57,88.37,'Calcutá',2],[30.60,114.31,'Wuhan',2],[41.38,2.18,'Barcelona',2],
  [45.46,9.19,'Milão',2],[50.11,8.68,'Frankfurt',2],[53.55,9.99,'Hamburgo',2],
  [48.14,11.58,'Munique',2],[43.70,-79.40,'Toronto',2],[45.51,-73.55,'Montreal',2],
  [49.25,-123.12,'Vancouver',2],[-33.87,151.21,'Sydney',2],[-37.81,144.96,'Melbourne',2],
  [6.25,-75.56,'Medellín',2],[20.68,-103.35,'Guadalajara',2],[25.67,-100.31,'Monterrey',2],
  [33.59,-7.62,'Casablanca',2],[31.20,29.91,'Alexandria',2],[-26.20,28.04,'Joanesburgo',2],
  [-33.93,18.42,'Cidade do Cabo',2],[11.99,8.52,'Kano',2],[7.39,3.90,'Ibadan',2],
  [-6.79,39.21,'Dar es Salaam',2],[5.36,-4.01,'Abidjan',2],[29.76,-95.37,'Houston',2],
  [33.45,-112.07,'Phoenix',2],[39.74,-104.98,'Denver',2],[47.61,-122.33,'Seattle',2],
  [43.70,-79.40,'Toronto',2],[30.66,104.07,'Chengdu',2],[30.27,-97.75,'Austin',2],
  [36.17,-86.78,'Nashville',2],[33.75,-84.39,'Atlanta',2],[42.36,-71.06,'Boston',2],
  [26.82,30.80,'Assuã',2],[24.86,67.01,'Carachi',2],[53.48,-2.24,'Manchester',2],
  [52.48,-1.90,'Birmingham',2],[45.76,4.84,'Lyon',2],[-1.29,36.82,'Nairóbi',2],

  // ── Capitais de estados/regiões (tier 3) ────────────────────────────────────
  // Brasil — todas as capitais estaduais
  [-3.12,-60.02,'Manaus',3],[-1.46,-48.50,'Belém',3],[-10.91,-37.05,'Aracaju',3],
  [-3.73,-38.52,'Fortaleza',3],[-9.67,-35.74,'Maceió',3],[-5.79,-35.21,'Natal',3],
  [-7.12,-34.86,'João Pessoa',3],[-8.06,-34.87,'Recife',3],[-12.97,-38.50,'Salvador',3],
  [-19.92,-43.94,'Belo Horizonte',3],[-20.32,-40.34,'Vitória',3],[-16.68,-49.26,'Goiânia',3],
  [-15.60,-56.10,'Cuiabá',3],[-20.44,-54.65,'Campo Grande',3],[-10.18,-48.33,'Palmas',3],
  [-25.43,-49.27,'Curitiba',3],[-27.60,-48.55,'Florianópolis',3],[-30.03,-51.23,'Porto Alegre',3],
  [-9.97,-67.81,'Rio Branco',3],[-8.76,-63.90,'Porto Velho',3],[0.03,-51.07,'Macapá',3],
  [2.82,-60.67,'Boa Vista',3],[-16.44,-39.07,'Porto Seguro',3],
  // EUA — capitais estaduais
  [38.58,-121.49,'Sacramento',3],[39.16,-119.75,'Carson City',3],[35.68,-105.94,'Santa Fé',3],
  [39.80,-89.65,'Springfield',3],[39.79,-86.15,'Indianápolis',3],[42.73,-84.56,'Lansing',3],
  [43.07,-89.40,'Madison',3],[41.59,-93.62,'Des Moines',3],[41.14,-104.82,'Cheyenne',3],
  [46.82,-100.78,'Bismarck',3],[44.37,-100.35,'Pierre',3],[40.81,-96.68,'Lincoln',3],
  [39.05,-95.69,'Topeka',3],[35.47,-97.52,'Oklahoma City',3],[34.75,-92.29,'Little Rock',3],
  [38.58,-92.17,'Jefferson City',3],[30.45,-91.13,'Baton Rouge',3],[32.30,-90.18,'Jackson',3],
  [32.37,-86.30,'Montgomery',3],[30.44,-84.28,'Tallahassee',3],[35.78,-78.64,'Raleigh',3],
  [34.00,-81.03,'Columbia SC',3],[38.97,-76.50,'Annapolis',3],[40.27,-76.88,'Harrisburg',3],
  [42.66,-73.80,'Albany',3],[41.76,-72.68,'Hartford',3],[41.82,-71.42,'Providence',3],
  [44.31,-69.78,'Augusta ME',3],[43.21,-71.54,'Concord NH',3],[44.27,-72.57,'Montpelier',3],
  [46.59,-112.03,'Helena',3],[43.62,-116.24,'Boise',3],[44.93,-123.03,'Salem OR',3],
  [47.04,-122.90,'Olympia',3],[21.31,-157.86,'Honolulu',3],[58.30,-134.42,'Juneau',3],
  [38.20,-84.87,'Frankfort',3],[38.35,-81.63,'Charleston WV',3],
  // Europa — cidades regionais importantes
  [53.34,-6.26,'Dublin',3],[48.78,9.18,'Stuttgart',3],[51.05,13.74,'Dresde',3],
  [51.23,6.78,'Düsseldorf',3],[51.96,7.63,'Münster',3],[48.37,10.90,'Augsburg',3],
  [47.80,13.04,'Salzburgo',3],[47.37,8.54,'Zurique',3],[46.52,6.63,'Lausana',3],
  [45.44,12.33,'Veneza',3],[45.07,7.68,'Turim',3],[40.85,14.27,'Nápoles',3],
  [38.11,13.36,'Palermo',3],[41.65,-0.88,'Saragoça',3],[39.57,2.65,'Palma',3],
  [37.39,-5.99,'Sevilha',3],[37.18,-3.60,'Granada',3],[36.72,-4.42,'Málaga',3],
  [43.30,-1.98,'São Sebastião',3],[43.26,-2.93,'Bilbao',3],
  // Ásia — regionais
  [31.23,121.47,'Xangai',3],[22.27,114.17,'Hong Kong',3],[25.05,121.52,'Taipé',3],
  [34.69,135.50,'Osaka',3],[35.02,135.76,'Quioto',3],[43.06,141.35,'Sapporo',3],
  [33.59,130.40,'Fukuoka',3],[35.18,136.91,'Nagoya',3],[18.97,72.82,'Mumbai',3],
  [13.08,80.27,'Chennai',3],[17.37,78.48,'Hyderabad',3],[22.57,88.37,'Calcutá',3],
  [23.03,72.58,'Ahmedabad',3],[30.73,76.78,'Chandigarh',3],
];

// ─── Globe page ───────────────────────────────────────────────────────────────

function pathColor(d) {
  if (AppState.probeActiveCable && d.cable_id === AppState.probeActiveCable)
    return '#fff03c';
  if (!d.is_operational) return '#ff465f';
  if (d.has_alert)       return '#ffb92d';
  if (d.has_tcp_probe)   return '#a871ff';
  return '#2ddc82';
}

function refreshGlobeProbeHighlight() {
  if (!AppState.map || !AppState.map.getSource('cables')) return;
  // Repaint cables by updating the paint expression (probe cable turns yellow)
  AppState.map.setPaintProperty('cables-layer', 'line-color', buildCableColorExpr());
}

function buildCableColorExpr() {
  // MapLibre paint expression: probe cable = yellow, else use precomputed 'color' property
  if (AppState.probeActiveCable) {
    return [
      'case',
      ['==', ['get', 'cable_id'], AppState.probeActiveCable], '#fff03c',
      ['get', 'color']
    ];
  }
  return ['get', 'color'];
}

function ptColor(d) {
  if (!d.is_operational) return '#ff4f66';
  if (d.has_alert)       return '#ffbe3c';
  if (d.has_tcp_probe)   return '#a871ff';
  return '#41da8c';
}

function statusLabel(isOp, hasAlert) {
  if (!isOp)    return '<span class="badge no">Fora de operação</span>';
  if (hasAlert) return '<span class="badge warn">Alerta ativo</span>';
  return '<span class="badge yes">Operacional</span>';
}

function stopGlobeRotation() {}

function selectFromGlobe(d) {
  if (!d) return;
  AppState.globeSelection = d;
  stopGlobeRotation();

  const panel    = document.getElementById('selection-panel');
  const titleEl  = document.getElementById('sel-title');
  const bodyEl   = document.getElementById('sel-body');
  if (!panel || !titleEl || !bodyEl) return;

  // Points have 'lat'/'lng' directly; paths have 'points' array
  const isPoint = d.points === undefined;

  titleEl.textContent = d.cable_name || 'Cabo';

  function coordLabel(city, country, lat, lng) {
    if (city && country) return `${city}, ${country}`;
    if (city) return city;
    if (lat != null && lng != null) return `${parseFloat(lat).toFixed(2)}°, ${parseFloat(lng).toFixed(2)}°`;
    return '—';
  }

  let html = '';
  if (isPoint) {
    const location = coordLabel(d.city_name, d.country_name, d.lat, d.lng);
    html = `
      <p>${statusLabel(d.is_operational, d.has_alert)}</p>
      <table class="sel-table">
        <tr><th>Localização</th><td>${esc(location)}</td></tr>
        <tr><th>Cabo</th><td>${esc(d.cable_name)}</td></tr>
        ${d.endpoint_id && !d.endpoint_id.includes(',') ? `<tr><th>Endpoint ID</th><td class="mono">${esc(d.endpoint_id)}</td></tr>` : ''}
        ${d.ip_sample ? `<tr><th>IP sample</th><td class="mono">${esc(d.ip_sample)}</td></tr>` : ''}
      </table>`;
  } else {
    const from = coordLabel(d.startCity, d.startCountry, d.startLat, d.startLng);
    const to   = coordLabel(d.endCity,   d.endCountry,   d.endLat,   d.endLng);
    html = `
      <p>${statusLabel(d.is_operational, d.has_alert)}</p>
      <table class="sel-table">
        <tr><th>Cabo</th><td>${esc(d.cable_name)}</td></tr>
        <tr><th>ID</th><td class="mono">${esc(d.cable_id)}</td></tr>
        <tr><th>Origem</th><td>${esc(from)}</td></tr>
        <tr><th>Destino</th><td>${esc(to)}</td></tr>
        ${d.ip_sample ? `<tr><th>IP sample</th><td class="mono">${esc(d.ip_sample)}</td></tr>` : ''}
      </table>`;
  }
  bodyEl.innerHTML = html;
  panel.classList.add('open');
}

// ── Star background ───────────────────────────────────────────────────────────
function initStarBackground() {
  const canvas = document.getElementById('star-bg');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const STAR_COUNT = 120;
  let stars = [];

  function resize() {
    canvas.width  = window.innerWidth;
    canvas.height = window.innerHeight;
    stars = Array.from({ length: STAR_COUNT }, () => ({
      x:     Math.random() * canvas.width,
      y:     Math.random() * canvas.height,
      r:     Math.random() * 1.2 + 0.3,
      base:  Math.random() * 0.55 + 0.25,
      phase: Math.random() * Math.PI * 2,
      speed: Math.random() * 0.0008 + 0.0004,
    }));
  }

  let _mx = 0, _my = 0, _tx = 0, _ty = 0;
  window.addEventListener('mousemove', e => {
    _mx = (e.clientX / window.innerWidth  - 0.5) * 18;
    _my = (e.clientY / window.innerHeight - 0.5) * 10;
  });

  function draw(ts) {
    _tx += (_mx - _tx) * 0.04;
    _ty += (_my - _ty) * 0.04;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const s of stars) {
      const alpha = s.base + Math.sin(s.phase + ts * s.speed) * 0.22;
      const px = s.x + _tx * s.r * 0.8;
      const py = s.y + _ty * s.r * 0.8;
      ctx.beginPath();
      ctx.arc(px, py, s.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(200,190,255,${alpha.toFixed(3)})`;
      ctx.fill();
    }
    requestAnimationFrame(draw);
  }

  resize();
  window.addEventListener('resize', resize);
  requestAnimationFrame(draw);
}

// ── Dark ocean canvas texture for the globe ───────────────────────────────────

// ── Welcome / Homepage ────────────────────────────────────────────────────────
function initWelcomeCard() {
  const card = document.getElementById('welcome-card');
  const btn  = document.getElementById('welcome-dismiss');
  if (!card) return;

  card.classList.add('visible');

  const $    = sel => card.querySelector(sel);
  const setDot = (dotEl, cls) => { dotEl && (dotEl.className = 'wc-status-dot ' + cls); };
  const setText = (sel, v) => { const el = $(sel); if (el) el.textContent = v; };
  const countEl = (sel, n, ms) => { const el = $(sel); if (el && n > 0) rmifoCount(el, n, ms); };

  fetch('api.php?action=summary')
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      const dbDot    = $('[data-stat="db-dot"]');
      const dbStatus = $('[data-stat="db-status"]');
      const alertDot = $('[data-stat="alert-dot"]');

      if (!d || d.error || !d.db_ok) {
        setDot(dbDot, 'wc-dot-err');
        setText('[data-stat="db-status"]', d?.db_ok === false ? 'offline' : 'verificando…');
        setDot(alertDot, 'wc-dot-warn');
        return;
      }

      // DB online
      setDot(dbDot, 'wc-dot-ok');
      setText('[data-stat="db-status"]', 'online');

      // Contadores animados
      countEl('[data-stat="cables"]',         d.cables_total,       900);
      countEl('[data-stat="operational"]',    d.cables_operational, 900);
      countEl('[data-stat="samples"]',        d.telemetry_samples,  1100);
      countEl('[data-stat="landing-points"]', d.landing_points,     800);
      countEl('[data-stat="countries"]',      d.countries,          700);
      countEl('[data-stat="tables"]',         d.tables,             500);
      countEl('[data-stat="switch-geos"]',    d.switch_geos,        1300);
      countEl('[data-stat="cable-lps"]',      d.cable_lps,          1000);

      // Inline na seção "Como funciona"
      const inlineTablesEl = $('[data-stat="tables-inline"]');
      if (inlineTablesEl) inlineTablesEl.textContent = d.tables || '—';

      // Última medição
      if (d.last_measured_at) {
        const dt = new Date(d.last_measured_at);
        setText('[data-stat="last"]',
          dt.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' })
          + ' ' + dt.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
        );
      }

      // Alertas
      const alerts = d.alerts_active ?? 0;
      setText('[data-stat="alerts"]', alerts);
      setDot(alertDot, alerts > 0 ? 'wc-dot-warn' : 'wc-dot-ok');

      // Hops de rota
      const hopsEl = $('[data-stat="hops"]');
      if (hopsEl && d.cable_hops > 0) rmifoCount(hopsEl, d.cable_hops, 1200);
    })
    .catch(() => {
      setDot($('[data-stat="db-dot"]'), 'wc-dot-err');
      setText('[data-stat="db-status"]', 'offline');
    });

  if (btn) btn.addEventListener('click', () => card.classList.remove('visible'));
}

// CARTO dark-matter: estilo completo e mantido, sem source-layers customizados
const GLOBE_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';

async function initGlobe() {
  const container = document.getElementById('globe-container');
  const globeTip  = document.getElementById('globe-tip');
  if (!container) return;
  if (typeof maplibregl === 'undefined') {
    container.innerHTML = '<p style="color:#f0b24f;padding:20px">MapLibre GL não carregado.</p>';
    return;
  }

  // Busca o estilo CARTO e injeta "projection":"globe" diretamente no JSON do estilo.
  // MapLibre GL JS 4.x usa { type: 'globe' } — NÃO { name: 'globe' } (que é Mapbox).
  let mapStyle = GLOBE_STYLE;
  try {
    const styleResp = await fetch(GLOBE_STYLE);
    if (styleResp.ok) {
      const styleJson = await styleResp.json();
      styleJson.projection = { type: 'globe' };
      mapStyle = styleJson;
    }
  } catch (e) {
    console.warn('Pré-carregamento do estilo falhou — usando URL direto:', e);
  }

  const map = new maplibregl.Map({
    container:          'globe-container',
    style:              mapStyle,
    center:             [10, 20],
    zoom:               1.5,
    minZoom:            0.5,
    maxZoom:            14,
    projection:         'globe',   // fallback se a injeção no estilo não funcionar
    attributionControl: false,
    antialias:          true,
  });

  AppState.map = map;

  map.on('load', () => {
    // Reaplica projeção globo após o estilo carregar (garante mesmo quando o
    // estilo remoto sobrescreve o que foi injetado).
    // Usa string 'globe' — forma mais compatível com MapLibre GL JS 4.x.
    try { map.setProjection('globe'); } catch (e) {
      console.warn('setProjection("globe") falhou:', e);
      try { map.setProjection({ type: 'globe' }); } catch (e2) {
        console.warn('setProjection({type:"globe"}) também falhou:', e2);
      }
    }

    // Atmosfera (espaço escuro = esfera visível)
    try {
      map.setFog({
        color:            'rgba(18, 8, 40, 0.9)',
        'high-color':     '#030108',
        'horizon-blend':  0.1,
        'space-color':    '#030108',
        'star-intensity': 0.45,
      });
    } catch (e) {
      console.warn('setFog falhou:', e);
    }

    // ── Dados dos cabos e pontos ──────────────────────────────────────────
    apiFetch({ action: 'globe_data' })
      .then(payload => {
        const paths = payload.paths || [];
        const pts   = payload.points || [];
        AppState.globePaths = paths;

        // Filtro defensivo: descarta cabos sem pontos válidos
        const cableFeatures = paths
          .filter(p => Array.isArray(p.points) && p.points.length >= 2)
          .map(p => ({
            type: 'Feature',
            properties: {
              cable_id:       p.cable_id,
              cable_name:     p.cable_name,
              color:          pathColor(p),
              is_operational: p.is_operational,
              has_alert:      p.has_alert,
              has_tcp_probe:  p.has_tcp_probe
            },
            geometry: {
              type: 'LineString',
              coordinates: p.points  // API já retorna [lng, lat] (formato MapLibre)
            }
          }));

        if (!map.getSource('cables')) {
          map.addSource('cables', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: cableFeatures },
            tolerance: 0.5
          });
        }
        if (!map.getLayer('cables-layer')) {
          map.addLayer({
            id: 'cables-layer', type: 'line', source: 'cables',
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: {
              'line-color':   ['get', 'color'],
              'line-width':   ['interpolate', ['linear'], ['zoom'], 1, 1.2, 5, 2, 10, 3],
              'line-opacity': 0.95
            }
          });
        }

        const pointFeatures = pts
          .filter(p => p.lat != null && p.lng != null && !isNaN(parseFloat(p.lat)))
          .map(p => ({
            type: 'Feature',
            properties: {
              cable_id:       p.cable_id,
              cable_name:     p.cable_name,
              color:          ptColor(p),
              is_operational: p.is_operational,
              has_alert:      p.has_alert,
              has_tcp_probe:  p.has_tcp_probe,
              city_name:      p.city_name    || null,
              country_name:   p.country_name || null,
              ip_sample:      p.ip_sample    || null
            },
            geometry: { type: 'Point', coordinates: [parseFloat(p.lng), parseFloat(p.lat)] }
          }));

        if (!map.getSource('landing-points')) {
          map.addSource('landing-points', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: pointFeatures }
          });
        }
        if (!map.getLayer('points-layer')) {
          map.addLayer({
            id: 'points-layer', type: 'circle', source: 'landing-points',
            paint: {
              'circle-radius':       ['interpolate', ['linear'], ['zoom'], 1, 3, 4, 5, 8, 9],
              'circle-color':        ['get', 'color'],
              'circle-opacity':      0.95,
              'circle-stroke-width': 1,
              'circle-stroke-color': 'rgba(255,255,255,0.4)'
            }
          });
        }

        const alertIds = new Set(paths.filter(p => p.has_alert).map(p => p.cable_id));
        updateAlertBadge(alertIds.size);
        if (globeTip && cableFeatures.length === 0) {
          globeTip.textContent = 'Nenhum dado de cabo disponível.';
        }
      })
      .catch(err => {
        console.error('globe_data error:', err);
        if (globeTip) globeTip.textContent = `Erro ao carregar dados: ${err.message}`;
      });

    // ── Linhas comutador → landing-point (fonte preservada para uso futuro) ──
    apiFetch({ action: 'switch_lp_lines' })
      .then(fc => {
        if (!fc.features || !fc.features.length) return;
        if (map.getSource('switch-lp-lines')) return;
        map.addSource('switch-lp-lines', { type: 'geojson', data: fc });
        // map.addLayer({  // ← Reservado para atualização futura de relacionamento ISP/AS
        //   id: 'switch-lp-lines-layer', type: 'line', source: 'switch-lp-lines',
        //   layout: { 'line-cap': 'round', 'line-join': 'round' },
        //   paint: { 'line-color': '#ff912a', 'line-width': 0.9, 'line-opacity': 0.28, 'line-dasharray': [2, 4] },
        // });
      })
      .catch(() => {});

    // ── ISP / AS nodes + relationship lines (same-AS links) ──────────────
    apiFetch({ action: 'hop_geo_points' })
      .then(fc => {
        if (!fc.features || !fc.features.length) return;
        if (map.getSource('switch-hops')) return;
        map.addSource('switch-hops', { type: 'geojson', data: fc });

        // Pré-computa links ISP/AS (estrela por ASN) — fonte preservada para uso futuro.
        // Para ativar a camada de relacionamento, descomentar addLayer abaixo.
        const byAs = {};
        for (const f of fc.features) {
          const asn = f.properties.as_number;
          if (!asn) continue;
          if (!byAs[asn]) byAs[asn] = [];
          byAs[asn].push(f.geometry.coordinates);
        }
        const asLinkFeatures = [];
        for (const [asn, coords] of Object.entries(byAs)) {
          if (coords.length < 2) continue;
          const cx = coords.reduce((s, c) => s + c[0], 0) / coords.length;
          const cy = coords.reduce((s, c) => s + c[1], 0) / coords.length;
          for (const c of coords) {
            asLinkFeatures.push({
              type: 'Feature',
              properties: { as_number: asn },
              geometry: { type: 'LineString', coordinates: [c, [cx, cy]] },
            });
          }
        }
        map.addSource('switch-as-links', {
          type: 'geojson',
          data: { type: 'FeatureCollection', features: asLinkFeatures },
        });
        // map.addLayer({  // ← Reservado para atualização futura de relacionamento ISP/AS
        //   id: 'switch-as-links-layer', type: 'line', source: 'switch-as-links',
        //   layout: { 'line-cap': 'round', 'line-join': 'round' },
        //   paint: { 'line-color': '#f59e0b', 'line-width': 0.8, 'line-opacity': 0.35, 'line-dasharray': [1, 3] },
        // });

        map.addLayer({
          id: 'switch-hops-layer', type: 'circle', source: 'switch-hops',
          paint: {
            'circle-radius':       ['interpolate', ['linear'], ['zoom'], 1, 2.5, 4, 4.5, 8, 8],
            'circle-color':        ['interpolate', ['linear'], ['get', 'cable_count'], 1, '#f59e0b', 5, '#ef4444', 10, '#dc2626'],
            'circle-opacity':      0.82,
            'circle-stroke-width': 1,
            'circle-stroke-color': 'rgba(255,255,255,0.25)',
          },
        });

        // Wire toggle buttons now that all layers exist
        initLayerToggles(map);
      })
      .catch(() => {});
  }); // end map.on('load')

  // ── Layer visibility toggles ──────────────────────────────────────────────
  const layerGroups = {
    cables:    ['cables-layer'],
    endpoints: ['points-layer'],
    isp:       ['switch-hops-layer'],
  };

  function setLayerGroupVisibility(map, group, visible) {
    const ids = layerGroups[group] || [];
    const v   = visible ? 'visible' : 'none';
    ids.forEach(id => { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', v); });
  }

  let togglesWired = false;
  function initLayerToggles(map) {
    if (togglesWired) {
      // Layers were added after initial wiring — just sync visibility
      document.querySelectorAll('#globe-filters .layer-toggle').forEach(btn => {
        setLayerGroupVisibility(map, btn.dataset.layer, btn.classList.contains('active'));
      });
      return;
    }
    togglesWired = true;
    document.querySelectorAll('#globe-filters .layer-toggle').forEach(btn => {
      const group = btn.dataset.layer;
      setLayerGroupVisibility(map, group, btn.classList.contains('active'));
      btn.addEventListener('click', () => {
        setLayerGroupVisibility(map, group, btn.classList.toggle('active'));
      });
    });
  }

  // Wire toggles once map becomes idle (cables + endpoints already loaded)
  map.once('idle', () => initLayerToggles(map));

  // ── Hover tip com throttle RAF — evita queryRenderedFeatures a 60fps ─────
  let hoverRAF = null;
  map.on('mousemove', e => {
    if (hoverRAF) return;
    hoverRAF = requestAnimationFrame(() => {
      hoverRAF = null;
      if (!globeTip) return;
      const feats = map.queryRenderedFeatures(e.point, { layers: ['cables-layer', 'points-layer', 'switch-hops-layer'] });
      if (!feats.length) {
        globeTip.textContent = 'Clique em um cabo ou ponto para selecionar';
        map.getCanvas().style.cursor = '';
        return;
      }
      map.getCanvas().style.cursor = 'pointer';
      const f = feats[0];
      const p = f.properties;
      if (f.layer.id === 'switch-hops-layer') {
        const loc = p.city && p.country ? `${p.city}, ${p.country}` : p.country || p.ip;
        globeTip.textContent = `⚡ ${p.ip}  ·  ${loc}  ·  ${p.isp || p.org || p.as_name || 'AS?'}  ·  ${p.cable_count} cabo(s)`;
      } else if (f.layer.id === 'cables-layer') {
        const co = f.geometry.coordinates;
        globeTip.textContent = `${p.cable_name}  ·  ${co[0][1].toFixed(1)}°, ${co[0][0].toFixed(1)}° → ${co[co.length-1][1].toFixed(1)}°, ${co[co.length-1][0].toFixed(1)}°`;
      } else {
        const loc = p.city_name && p.country_name ? `${p.city_name}, ${p.country_name}` : p.city_name || '—';
        globeTip.textContent = `${p.cable_name}  ·  ${loc}`;
      }
    });
  });

  map.on('mouseleave', () => {
    if (globeTip) globeTip.textContent = 'Clique em um cabo ou ponto para selecionar';
    map.getCanvas().style.cursor = '';
  });

  // Click: switch/hop geolocations
  map.on('click', 'switch-hops-layer', e => {
    const p      = e.features[0].properties;
    const coords = e.features[0].geometry.coordinates;
    const panel  = document.getElementById('selection-panel');
    const titleEl = document.getElementById('sel-title');
    const bodyEl  = document.getElementById('sel-body');
    if (!panel || !titleEl || !bodyEl) return;
    titleEl.textContent = p.ip || 'ISP / AS';
    const loc = [p.city, p.region, p.country].filter(Boolean).join(', ') || '—';
    bodyEl.innerHTML = `
      <p><span class="badge yes">ISP / AS</span></p>
      <table class="sel-table">
        <tr><th>IP</th><td class="mono">${esc(p.ip)}</td></tr>
        <tr><th>Localização</th><td>${esc(loc)}</td></tr>
        <tr><th>ISP</th><td>${esc(p.isp || '—')}</td></tr>
        <tr><th>Organização</th><td>${esc(p.org || '—')}</td></tr>
        <tr><th>ASN</th><td class="mono">${esc(p.as_number || '—')}</td></tr>
        <tr><th>AS Nome</th><td>${esc(p.as_name || '—')}</td></tr>
        <tr><th>Cabos</th><td>${esc(String(p.cable_count || '—'))}</td></tr>
        <tr><th>Coords</th><td class="mono">${coords[1].toFixed(4)}°, ${coords[0].toFixed(4)}°</td></tr>
      </table>`;
    panel.classList.add('open');
  });

  // Click: cables
  map.on('click', 'cables-layer', e => {
    const p = e.features[0].properties;
    const coords = e.features[0].geometry.coordinates;
    const d = {
      cable_id:       p.cable_id,
      cable_name:     p.cable_name,
      is_operational: p.is_operational,
      has_alert:      p.has_alert,
      has_tcp_probe:  p.has_tcp_probe,
      // path-like: expose start/end as points for selectFromGlobe
      startLat: coords[0][1],  startLng: coords[0][0],
      endLat:   coords[coords.length-1][1], endLng: coords[coords.length-1][0],
      points:   coords  // marks this as a path (not a point)
    };
    stopGlobeRotation();
    selectFromGlobe(d);
  });

  // Click: landing points
  map.on('click', 'points-layer', e => {
    const p = e.features[0].properties;
    const coords = e.features[0].geometry.coordinates;
    const d = {
      cable_id:       p.cable_id,
      cable_name:     p.cable_name,
      is_operational: p.is_operational,
      has_alert:      p.has_alert,
      has_tcp_probe:  p.has_tcp_probe,
      city_name:      p.city_name,
      country_name:   p.country_name,
      ip_sample:      p.ip_sample,
      lat:            coords[1],
      lng:            coords[0]
    };
    stopGlobeRotation();
    selectFromGlobe(d);
  });
}

function updateAlertBadge(count) {
  const badge = document.getElementById('alert-badge');
  if (!badge) return;
  badge.textContent = count;
  badge.style.display = count > 0 ? '' : 'none';
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
      ProbeModal.show('Sondando…', '<p style="color:var(--text-soft);padding:20px;">Iniciando sonda…</p>');
      try {
        const fd = new FormData();
        fd.append('action', 'probe');
        fd.append('cable_id', d.cable_id || '');
        if (d.endpoint_id) fd.append('endpoint_id', d.endpoint_id);
        if (d.ip_sample)   fd.append('ip', d.ip_sample);
        const payload = await apiPost(fd);
        startProbeRealtime(d, payload);
      } catch (err) {
        ProbeModal.show('Erro na Sonda', `<p class="error-text">${esc(err.message)}</p>`);
      }
    });
  }
}

// ─── DB Explorer (substitui Search page) ─────────────────────────────────────

function initSearchPage() {  // kept as alias — delegates to initDbExplorer
  initDbExplorer();
}

function initDbExplorer() {
  // ── state ──────────────────────────────────────────────────────────────────
  let currentTable  = 'cables';
  let currentPage   = 1;
  let currentTotal  = 0;
  let currentLimit  = 25;
  let currentSort   = { col: '', dir: 'ASC' };
  let currentFilter = { col: '', op: 'ilike', val: '' };

  // ── element refs ───────────────────────────────────────────────────────────
  const tableList    = document.getElementById('db-table-list');
  const currentLabel = document.getElementById('db-current-table');
  const filterCol    = document.getElementById('db-filter-col');
  const filterOp     = document.getElementById('db-filter-op');
  const filterVal    = document.getElementById('db-filter-val');
  const filterBtn    = document.getElementById('db-filter-btn');
  const filterClear  = document.getElementById('db-filter-clear');
  const resultInfo   = document.getElementById('db-result-info');
  const pageLabel    = document.getElementById('db-page-label');
  const prevBtn      = document.getElementById('db-prev-page');
  const nextBtn      = document.getElementById('db-next-page');
  const thead        = document.getElementById('db-thead');
  const tbody        = document.getElementById('db-tbody');
  const tableWrap    = document.getElementById('db-table-wrap');
  const emptyEl      = document.getElementById('db-empty');
  const errorEl      = document.getElementById('db-error');


  // ── helpers ────────────────────────────────────────────────────────────────
  function fmtCell(v) {
    if (v === null || v === undefined) return '<span class="db-null">NULL</span>';
    const s = String(v);
    if (s.length > 80) return `<span title="${esc(s)}">${esc(s.slice(0, 80))}…</span>`;
    return esc(s);
  }

  function renderTable(columns, rows, theadEl, tbodyEl, wrapEl, emEl, sortable) {
    if (!theadEl || !tbodyEl) return;
    theadEl.innerHTML = `<tr>${columns.map(c => {
      if (!sortable) return `<th>${esc(c)}</th>`;
      const isActive = currentSort.col === c;
      const dir      = isActive && currentSort.dir === 'ASC' ? '↑' : isActive ? '↓' : '';
      return `<th class="db-th-sort ${isActive ? 'db-th-active' : ''}" data-col="${esc(c)}">${esc(c)} <span class="db-sort-arrow">${dir}</span></th>`;
    }).join('')}</tr>`;
    tbodyEl.innerHTML = rows.map(row =>
      `<tr>${columns.map(c => `<td>${fmtCell(row[c])}</td>`).join('')}</tr>`
    ).join('');
    if (wrapEl)  wrapEl.style.display  = rows.length ? '' : 'none';
    if (emEl)    emEl.style.display    = rows.length ? 'none' : '';
  }

  function updateFilterCols(cols) {
    if (!filterCol) return;
    filterCol.innerHTML = cols.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
    if (currentFilter.col && cols.includes(currentFilter.col)) filterCol.value = currentFilter.col;
    else if (cols.length) filterCol.value = cols[0];
  }

  function updatePagination(total, page, limit) {
    const pages = Math.max(1, Math.ceil(total / limit));
    if (pageLabel) pageLabel.textContent = `${page} / ${pages}`;
    if (prevBtn) prevBtn.disabled = page <= 1;
    if (nextBtn) nextBtn.disabled = page >= pages;
    if (resultInfo) resultInfo.textContent = `${total.toLocaleString()} linha(s) · página ${page} de ${pages}`;
  }

  // ── load table data ────────────────────────────────────────────────────────
  async function loadTable(table, page, sortCol, sortDir, fCol, fOp, fVal) {
    if (errorEl) clearEl(errorEl);
    if (resultInfo) resultInfo.textContent = 'Carregando…';

    try {
      const params = { action: 'db_browse', table, page, limit: currentLimit };
      if (sortCol) { params.sort_col = sortCol; params.sort_dir = sortDir; }
      if (fCol && fVal) { params.filter_col = fCol; params.filter_op = fOp; params.filter_val = fVal; }

      const data = await apiFetch(params);
      if (data.error) throw new Error(data.error);

      currentTotal = data.total;
      updateFilterCols(data.columns);
      renderTable(data.columns, data.rows, thead, tbody, tableWrap, emptyEl, true);
      updatePagination(data.total, data.page, data.limit);
      if (currentLabel) currentLabel.textContent = table;

      // Sort click handlers
      if (thead) {
        thead.querySelectorAll('.db-th-sort').forEach(th => {
          th.addEventListener('click', () => {
            const col = th.dataset.col;
            const dir = currentSort.col === col && currentSort.dir === 'ASC' ? 'DESC' : 'ASC';
            currentSort = { col, dir };
            loadTable(currentTable, currentPage, col, dir, currentFilter.col, currentFilter.op, currentFilter.val);
          });
        });
      }

      // Update sidebar row count badge
      const badge = document.getElementById(`dbrows-${table}`);
      if (badge && badge.textContent === '') badge.textContent = data.total.toLocaleString();

    } catch (err) {
      if (errorEl) showError(errorEl, err.message);
      if (resultInfo) resultInfo.textContent = 'Erro ao carregar.';
    }
  }

  // ── sidebar table selection ────────────────────────────────────────────────
  if (tableList) {
    tableList.addEventListener('click', e => {
      const item = e.target.closest('.db-table-item');
      if (!item) return;
      tableList.querySelectorAll('.db-table-item').forEach(i => i.classList.remove('active'));
      item.classList.add('active');
      currentTable  = item.dataset.table;
      currentPage   = 1;
      currentSort   = { col: '', dir: 'ASC' };
      currentFilter = { col: '', op: 'ilike', val: '' };
      if (filterVal) filterVal.value = '';
      loadTable(currentTable, 1, '', 'ASC', '', 'ilike', '');
    });
  }

  // ── filter ─────────────────────────────────────────────────────────────────
  if (filterBtn) {
    filterBtn.addEventListener('click', () => {
      currentFilter = {
        col: filterCol?.value || '',
        op:  filterOp?.value  || 'ilike',
        val: filterVal?.value.trim() || '',
      };
      currentPage = 1;
      loadTable(currentTable, 1, currentSort.col, currentSort.dir, currentFilter.col, currentFilter.op, currentFilter.val);
    });
  }
  if (filterClear) {
    filterClear.addEventListener('click', () => {
      if (filterVal) filterVal.value = '';
      currentFilter = { col: '', op: 'ilike', val: '' };
      currentPage = 1;
      loadTable(currentTable, 1, currentSort.col, currentSort.dir, '', 'ilike', '');
    });
  }
  if (filterVal) {
    filterVal.addEventListener('keydown', e => { if (e.key === 'Enter') filterBtn?.click(); });
  }

  // ── pagination ─────────────────────────────────────────────────────────────
  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      if (currentPage > 1) {
        currentPage--;
        loadTable(currentTable, currentPage, currentSort.col, currentSort.dir, currentFilter.col, currentFilter.op, currentFilter.val);
      }
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      const pages = Math.ceil(currentTotal / currentLimit);
      if (currentPage < pages) {
        currentPage++;
        loadTable(currentTable, currentPage, currentSort.col, currentSort.dir, currentFilter.col, currentFilter.op, currentFilter.val);
      }
    });
  }

  // ── Initial load ───────────────────────────────────────────────────────────
  // Load when the page becomes visible for the first time
  document.querySelector('[data-page="search"]')?.addEventListener('click', () => {
    if (!thead || thead.innerHTML) return; // already loaded
    loadTable('cables', 1, '', 'ASC', '', 'ilike', '');
  });
}

// ─── Detail page ──────────────────────────────────────────────────────────────

async function loadDetail(cableId) {
  const picker       = document.getElementById('detail-picker');
  const detailError  = document.getElementById('detail-error');
  const detailEmpty  = document.getElementById('detail-empty');
  const detailContent= document.getElementById('detail-content');
  const summary      = document.getElementById('detail-summary');
  const endpointList = document.getElementById('endpoint-list');

  if (!cableId) {
    showError(detailError, 'Selecione um cabo válido.');
    return;
  }
  clearEl(detailError);
  if (detailContent) detailContent.style.display = 'none';

  try {
    const payload   = await apiFetch({ action: 'detail', cable_id: cableId });
    const cable     = payload.cable     || payload;
    const endpoints = payload.endpoints || [];

    if (picker) picker.value = cableId;

    if (summary) {
      const items = [
        ['ID',           cable.cable_id],
        ['Nome',         cable.cable_name],
        ['Proprietários',cable.owners],
        ['Fornecedores', cable.suppliers],
        ['Comprimento',  cable.length_km ? `${cable.length_km.toLocaleString()} km` : null],
        ['Ano RFS',      cable.rfs_year],
        ['Operacional',  cable.is_operational == null ? null : (cable.is_operational ? 'Sim' : 'Não')],
        ['Endpoints',    cable.landing_points_count],
        ['Notas',        cable.notes],
      ].filter(([, v]) => v != null && v !== '');

      summary.innerHTML = `<div class="summary-grid">${
        items.map(([k, v]) => `
          <div class="summary-card">
            <div class="k">${esc(k)}</div>
            <div class="v">${esc(v)}</div>
          </div>`).join('')
      }</div>`;
    }

    if (endpointList) {
      if (!endpoints.length) {
        endpointList.innerHTML = '<p class="muted">Nenhum endpoint cadastrado.</p>';
      } else {
        endpointList.innerHTML = endpoints.map(ep => {
          const samples   = ep.ip_samples || [];
          const statusDot = ep.is_operational === false ? 'Inativo' : 'Ativo';
          const location  = [ep.city_name, ep.country_name].filter(Boolean).join(', ') || ep.endpoint_id || '';
          const ipRows    = samples.length
            ? samples.map(s => `<tr>
                <td class="mono">${esc(s.network || s.ip_start || '')}</td>
                <td>${esc(s.city || '')}</td>
                <td>${esc(s.country_name || '')}</td>
              </tr>`).join('')
            : '';

          return `<div class="endpoint-card">
            <div class="endpoint-head">
              <span class="ep-status">${statusDot}</span>
              <strong>${esc(ep.endpoint_name || ep.city_name || ep.endpoint_id)}</strong>
              ${location ? `<span class="muted ep-loc">${esc(location)}</span>` : ''}
            </div>
            ${samples.length ? `<div class="table-wrap"><table>
              <thead><tr><th>rede / IP</th><th>cidade</th><th>país</th></tr></thead>
              <tbody>${ipRows}</tbody>
            </table></div>` : '<p class="muted" style="margin:4px 0 0">Sem amostras IP.</p>'}
          </div>`;
        }).join('');
      }
    }

    if (detailEmpty)   detailEmpty.style.display   = 'none';
    if (detailContent) detailContent.style.display = '';

  } catch (err) {
    showError(detailError, err.message);
    if (detailEmpty) detailEmpty.style.display = '';
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
  const opts = AppState.allCables.map(c =>
    `<option value="${esc(c.cable_id)}">${esc(c.cable_name)}</option>`
  ).join('');
  document.querySelectorAll('datalist[id="cable-list"], datalist.cable-datalist').forEach(dl => {
    dl.innerHTML = opts;
  });
}

async function loadCableList() {
  try {
    const payload = await apiFetch({ action: 'search', mode: 'all', q: '' });
    AppState.allCables = extractData(payload);
    populateCableDatalist();

    // Populate probe cable datalist
    const probeCableList = document.getElementById('probe-cable-list');
    if (probeCableList) {
      probeCableList.innerHTML = AppState.allCables.map(c =>
        `<option value="${esc(c.cable_id)}">${esc(c.cable_name || c.cable_id)}</option>`
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

function renderTelemetryChart(history, ciLatency, metric = 'latency_ms', label = 'Latência (ms)', realHistory = []) {
  const valid = history.map(r => Number(r[metric])).filter(v => isFinite(v));
  const realValid = (realHistory || []).map(r => Number(r[metric])).filter(v => isFinite(v));
  if (valid.length < 2 && realValid.length < 2) return '<p class="muted">Amostras insuficientes para o gráfico.</p>';

  const W = 920, H = 220, P = 26;

  // Unified scale so both series share the same Y axis
  const allVals = [...valid, ...realValid];
  const minVal = Math.min(...allVals);
  const maxVal = Math.max(...allVals);
  const range  = maxVal - minVal || 1;

  function buildPts(vals) {
    if (vals.length < 2) return '';
    return vals.map((v, i) => {
      const x = P + (i / (vals.length - 1)) * (W - P * 2);
      const y = (H - P) - ((v - minVal) / range) * (H - P * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
  }

  const ptsDB   = buildPts(valid);
  const ptsReal = buildPts(realValid);

  const metricBtns = [
    ['latency_ms',      'Latência'],
    ['jitter_ms',       'Jitter'],
    ['packet_loss_pct', 'Perda %'],
    ['throughput_mbps', 'Throughput'],
    ['availability_pct','Disponib.'],
  ].map(([m, lbl]) =>
    `<button class="btn-sm ${m === metric ? 'primary' : ''}"
       onclick="window.__telemetryMetric('${m}','${lbl} (${m})')">
       ${lbl}
     </button>`
  ).join('');

  const ciText = Array.isArray(ciLatency)
    ? ` · IC 95%: ${ciLatency.join(' – ')} ms`
    : '';

  const realLegend = realValid.length
    ? `<span class="tel-legend-real">&#9632; TCP Probe (${realValid.length} amostras reais)</span>`
    : '';
  const dbLegend = valid.length
    ? `<span class="tel-legend-db">&#9632; DB / Sintético (${valid.length} pts)</span>`
    : '';

  return `
    <div class="telemetry-chart-wrap">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:8px;">
        ${metricBtns}
        <span style="flex:1"></span>
        ${dbLegend}${realLegend}
      </div>
      <svg class="telemetry-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <line class="telemetry-axis" x1="${P}" y1="${H-P}" x2="${W-P}" y2="${H-P}"></line>
        <line class="telemetry-axis" x1="${P}" y1="${P}"   x2="${P}"   y2="${H-P}"></line>
        ${ptsDB   ? `<polyline class="telemetry-line telemetry-line-db"   points="${ptsDB}"></polyline>`   : ''}
        ${ptsReal ? `<polyline class="telemetry-line telemetry-line-real" points="${ptsReal}"></polyline>` : ''}
        ${realValid.length === 1 ? `<circle class="tel-real-dot" cx="${P}" cy="${((H-P) - ((realValid[0]-minVal)/range)*(H-P*2)).toFixed(1)}" r="5"></circle>` : ''}
      </svg>
      <div class="telemetry-legend">${esc(label)} ao longo da janela.${esc(ciText)}</div>
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

function fmtNum(v, dec = 1) {
  return (v !== null && v !== undefined && isFinite(v)) ? Number(v).toFixed(dec) : '—';
}

function renderTelemetryResult(payload) {
  const cable     = (payload.details && payload.details.cable) || payload.cable || {};
  const telemetry = payload.telemetry || payload;
  const history   = Array.isArray(telemetry.history) ? telemetry.history : [];
  const realAvg   = payload.real_avg || null;
  const realHist  = (realAvg && Array.isArray(realAvg.history)) ? realAvg.history : [];
  const latP      = (telemetry.percentiles && telemetry.percentiles.latency_ms)      || {};
  const thrP      = (telemetry.percentiles && telemetry.percentiles.throughput_mbps) || {};
  const plP       = (telemetry.percentiles && telemetry.percentiles.packet_loss_pct) || {};
  const ci        = telemetry.confidence_interval_latency_ms;

  // Cache for metric switching (includes real history)
  AppState.lastTelemetryHistory    = history;
  AppState.lastTelemetryCiLatency  = ci;
  AppState.lastRealHistory         = realHist;

  window.__telemetryMetric = (metric, label) => {
    const chartDiv = document.getElementById('telemetry-chart-container');
    if (chartDiv) chartDiv.innerHTML = renderTelemetryChart(
      AppState.lastTelemetryHistory, AppState.lastTelemetryCiLatency, metric, label, AppState.lastRealHistory
    );
  };

  const chartHtml = renderTelemetryChart(history, ci, 'latency_ms', 'Latência (ms)', realHist);

  // Protocol breakdown counters
  function countByProto(arr) {
    const c = { tcp: 0, mtr: 0, icmp: 0, http: 0, synth: 0 };
    for (const r of arr) {
      const m = (r.method || r.proto || r.source || '').toLowerCase();
      if (m.includes('mtr') || m.includes('traceroute')) c.mtr++;
      else if (m.includes('tcp'))  c.tcp++;
      else if (m.includes('icmp')) c.icmp++;
      else if (m.includes('http')) c.http++;
      else                         c.synth++;
    }
    return c;
  }
  const dbCounts   = countByProto(history);
  const realCounts = countByProto(realHist);

  function protoChip(label, count, colorClass, filterKey) {
    if (!count) return '';
    return `<button class="tel-proto-chip ${colorClass}"
      onclick="window.__telemetryFilter('${filterKey}')"
      title="Filtrar por ${label}">${label}<span class="tel-proto-n">${count}</span></button>`;
  }

  const protoBar = `<div class="tel-proto-bar">
    <span class="tel-proto-label">Protocolos:</span>
    ${protoChip('TCP',         realCounts.tcp  + dbCounts.tcp,  'tpc', 'tcp')}
    ${protoChip('MTR',         realCounts.mtr  + dbCounts.mtr,  'mtr', 'mtr')}
    ${protoChip('ICMP',        realCounts.icmp + dbCounts.icmp, 'icm', 'icmp')}
    ${protoChip('HTTP',        realCounts.http + dbCounts.http, 'htp', 'http')}
    ${protoChip('Sintético',   dbCounts.synth,                  'syn', 'synth')}
    <span class="tel-proto-sep">|</span>
    <button class="tel-proto-chip" onclick="window.__telemetryFilter('')" title="Mostrar todos">Todos <span class="tel-proto-n">${history.length + realHist.length}</span></button>
    <span class="tel-proto-src">
      <span class="tel-legend-db">■ BD ${history.length}</span>
      <span class="tel-legend-real">■ TCP Probe ${realHist.length}</span>
    </span>
  </div>`;

  // Wire filter: subset history by protocol and re-render chart
  window.__telemetryFilter = (proto) => {
    const chartDiv = document.getElementById('telemetry-chart-container');
    if (!chartDiv) return;
    const fHist = proto
      ? AppState.lastTelemetryHistory.filter(r => {
          const m = (r.method || r.proto || r.source || '').toLowerCase();
          if (proto === 'synth') return !m.includes('tcp') && !m.includes('mtr') && !m.includes('icmp') && !m.includes('http');
          return m.includes(proto);
        })
      : AppState.lastTelemetryHistory;
    const fReal = proto && proto !== 'synth'
      ? AppState.lastRealHistory.filter(r => {
          const m = (r.method || r.proto || '').toLowerCase();
          return m.includes(proto) || (!proto && true);
        })
      : (proto === 'synth' ? [] : AppState.lastRealHistory);
    chartDiv.innerHTML = renderTelemetryChart(fHist, AppState.lastTelemetryCiLatency, 'latency_ms', 'Latência (ms)', fReal);
    // Highlight active chip
    document.querySelectorAll('.tel-proto-chip').forEach(b => b.classList.remove('active'));
    if (proto) {
      const active = document.querySelector(`.tel-proto-chip[onclick*="'${proto}'"]`);
      if (active) active.classList.add('active');
    }
  };

  // Real-data card HTML (shown only when history files have data)
  const realCardHtml = realAvg && realAvg.sample_count > 0 ? `
    <div class="subhead real-subhead" style="margin-top:16px;">
      Média Real · TCP Probe
      <span class="real-badge">${realAvg.sample_count} amostras</span>
    </div>
    <div class="real-avg-grid">
      <div class="real-avg-card">
        <div class="rac-label">Latência</div>
        <div class="rac-value lat">${fmtNum(realAvg.avg_latency_ms)} <span>ms</span></div>
      </div>
      <div class="real-avg-card">
        <div class="rac-label">Jitter</div>
        <div class="rac-value jit">${fmtNum(realAvg.avg_jitter_ms)} <span>ms</span></div>
      </div>
      <div class="real-avg-card">
        <div class="rac-label">Perda</div>
        <div class="rac-value loss">${fmtNum(realAvg.avg_packet_loss_pct, 3)} <span>%</span></div>
      </div>
      <div class="real-avg-card">
        <div class="rac-label">Throughput</div>
        <div class="rac-value thr">${fmtNum(realAvg.avg_throughput_mbps, 1)} <span>Mbps</span></div>
      </div>
      <div class="real-avg-card">
        <div class="rac-label">Disponib.</div>
        <div class="rac-value avail">${fmtNum(realAvg.avg_availability_pct, 2)} <span>%</span></div>
      </div>
      <div class="real-avg-card">
        <div class="rac-label">Qualidade</div>
        <div class="rac-value qual">${fmtNum(realAvg.quality_score, 1)} <span>/ 100</span></div>
      </div>
    </div>` : '';

  const resultEl = document.getElementById('telemetry-result');
  if (!resultEl) return;

  resultEl.innerHTML = `
    <h3>Telemetria · ${esc(cable.cable_name || 'Cabo')}</h3>
    ${protoBar}
    ${realCardHtml}
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

}

function initTelemetryPage() { /* merged into initProbePage */ }

// ─── Single cable probe (realtime mode) ──────────────────────────────────────

function renderSingleProbeResult(payload, setLegend) {
  const r      = payload.result || {};
  const method = r.method || 'synthetic_fallback';
  const isReal = method !== 'synthetic_fallback';

  // Protocol label
  let proto = 'Sintético';
  let chipCls = 'synth';
  if (method.includes('tcp'))  { proto = 'TCP SYN-ACK';  chipCls = ''; }
  else if (method.includes('icmp')) { proto = 'ICMP Echo'; chipCls = 'mtr'; }
  else if (method.includes('http')) { proto = 'HTTP';      chipCls = 'http'; }

  const portM  = method.match(/port(\d+)/);
  const portStr = portM ? ` · porta ${portM[1]}` : '';
  const steps  = (r.probe_steps || []).join(' → ').toUpperCase();

  const lat  = r.avg_rtt_ms       != null ? r.avg_rtt_ms       : '–';
  const jit  = r.jitter_ms        != null ? r.jitter_ms        : '–';
  const loss = r.packet_loss_pct  != null ? r.packet_loss_pct  : '–';
  const tput = r.throughput_mbps  != null ? Number(r.throughput_mbps).toFixed(1) : '–';
  const samp = r.samples          != null ? r.samples          : 1;

  // Legend update
  if (setLegend) {
    setLegend(
      new Date().toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }),
      isReal ? '●' : '◌',
      isReal ? (proto + portStr) : 'heuristic',
      samp
    );
  }

  // Hops
  const hops = Array.isArray(r.hops) ? r.hops : [];
  const hopsHtml = hops.length ? `
    <div class="subhead" style="margin-top:18px;">Rota de Rede (${hops.length} saltos)</div>
    <div class="spr-hops">
      ${hops.map((h, i) => `
        <div class="spr-hop">
          <span class="spr-hop-n">${i + 1}</span>
          <span class="spr-hop-ip">${esc(h.ip || h.host || '* * *')}</span>
          <span class="spr-hop-rtt">${h.rtt_ms != null ? h.rtt_ms + ' ms' : '—'}</span>
        </div>`).join('')}
    </div>` : '';

  const el = document.getElementById('telemetry-result');
  if (!el) return;

  el.innerHTML = `
    <div class="single-probe-result">
      <div class="spr-header">
        <span class="probe-badge ${isReal ? 'probe-badge-real' : 'probe-badge-synth'}">
          ${isReal ? '● MEDIÇÃO REAL' : '◌ ESTIMATIVA SINTÉTICA'}
        </span>
        <span class="spr-method-chip proto-chip${chipCls ? ' ' + chipCls : ''}">${proto}${portStr}</span>
        ${steps ? `<span class="spr-steps">Tentativas: ${steps}</span>` : ''}
      </div>
      <div class="spr-metrics">
        <div class="spr-metric">
          <div class="spr-val">${lat}</div>
          <div class="spr-unit">ms · Latência</div>
        </div>
        <div class="spr-metric">
          <div class="spr-val">${jit}</div>
          <div class="spr-unit">ms · Jitter</div>
        </div>
        <div class="spr-metric">
          <div class="spr-val">${loss}</div>
          <div class="spr-unit">% · Perda</div>
        </div>
        <div class="spr-metric">
          <div class="spr-val">${tput}</div>
          <div class="spr-unit">Mbps · Throughput</div>
        </div>
      </div>
      ${r.note ? `<div class="spr-note">${esc(r.note)}</div>` : ''}
      ${hopsHtml}
    </div>`;
}

// ─── Probe page ───────────────────────────────────────────────────────────────

// Real-time 3D probe dashboard launched from globe selection panel
function startProbeRealtime(d, basePayload, containerEl = null, probeInfo = null) {
  const cableName = d.cable_name || d.cable_id || 'Cabo';
  const r = basePayload?.result || basePayload || {};
  const baseLat  = +(r.avg_rtt_ms ?? r.latency_ms ?? 75);
  const baseJit  = +(r.jitter_ms ?? 12);
  const baseLoss = +(r.packet_loss_pct ?? 0.4);
  const baseThru = +(r.throughput_mbps ?? 420);

  // ── Badge for inline mode ─────────────────────────────────────────────────
  let badgeHtml = '';
  if (probeInfo) {
    const pm     = probeInfo.method || 'synthetic_fallback';
    const isReal = pm !== 'synthetic_fallback';
    let proto    = 'Sintético';
    let chipCls  = 'synth';
    if (pm.includes('tcp'))  { proto = 'TCP SYN-ACK'; chipCls = ''; }
    else if (pm.includes('icmp')) { proto = 'ICMP Echo'; chipCls = 'mtr'; }
    else if (pm.includes('http')) { proto = 'HTTP';     chipCls = 'http'; }
    const portM   = pm.match(/port(\d+)/);
    const portStr = portM ? ` · porta ${portM[1]}` : '';
    const steps   = (probeInfo.probe_steps || []).join(' → ').toUpperCase();
    badgeHtml = `
      <div class="rt-badge-bar">
        <span class="probe-badge ${isReal ? 'probe-badge-real' : 'probe-badge-synth'}">
          ${isReal ? '● MEDIÇÃO REAL' : '◌ ESTIMATIVA SINTÉTICA'}
        </span>
        <span class="spr-method-chip proto-chip${chipCls ? ' ' + chipCls : ''}">${proto}${portStr}</span>
        ${steps ? `<span class="spr-steps">Tentativas: ${steps}</span>` : ''}
      </div>`;
  }

  const dashHtml = `
    <div class="rt-dash">
      ${badgeHtml}

      <!-- ── Main area: charts left + rings sidebar right ─────────────── -->
      <div class="rt-main">

        <!-- Charts 2-column layout -->
        <div class="rt-cols">

          <!-- Left: Interferência + Latência -->
          <div class="rt-col">
            <div class="rt-panel">
              <div class="rt-label">Interferência — eventos 3D</div>
              <div id="rt-int" class="rt-chart"></div>
            </div>
            <div class="rt-panel">
              <div class="rt-label">Latência — superfície 3D por frequência</div>
              <div id="rt-lat" class="rt-chart"></div>
            </div>
          </div>

          <!-- Right charts: Jitter + Perda de Pacotes -->
          <div class="rt-col">
            <div class="rt-panel">
              <div class="rt-label">Jitter (ms)</div>
              <div id="rt-jit" class="rt-chart"></div>
            </div>
            <div class="rt-panel">
              <div class="rt-label">Perda de Pacotes (%)</div>
              <div id="rt-loss" class="rt-chart"></div>
            </div>
          </div>

        </div>

        <!-- ── Ring-gauge sidebar (right) ──────────────────────────────── -->
        <div class="rt-rings">

          <div class="rt-ring-panel">
            <div class="rt-ring-wrap">
              <svg class="rt-ring-svg" viewBox="0 0 96 96">
                <circle class="rt-ring-bg"   cx="48" cy="48" r="38"/>
                <circle class="rt-ring-fill" cx="48" cy="48" r="38" id="ring-lat"
                  stroke="#f0b24f" stroke-dasharray="238.76" stroke-dashoffset="238.76"/>
              </svg>
              <div class="rt-ring-center">
                <div class="rt-ring-value" id="rv-lat">—</div>
                <div class="rt-ring-unit">ms</div>
              </div>
            </div>
            <div class="rt-ring-label">Latência</div>
            <div class="rt-ring-pct" id="rp-lat">—</div>
          </div>

          <div class="rt-ring-divider"></div>

          <div class="rt-ring-panel">
            <div class="rt-ring-wrap">
              <svg class="rt-ring-svg" viewBox="0 0 96 96">
                <circle class="rt-ring-bg"   cx="48" cy="48" r="38"/>
                <circle class="rt-ring-fill" cx="48" cy="48" r="38" id="ring-loss"
                  stroke="#2ddc82" stroke-dasharray="238.76" stroke-dashoffset="238.76"/>
              </svg>
              <div class="rt-ring-center">
                <div class="rt-ring-value" id="rv-loss">—</div>
                <div class="rt-ring-unit">%</div>
              </div>
            </div>
            <div class="rt-ring-label">Perda</div>
            <div class="rt-ring-pct" id="rp-loss">—</div>
          </div>

          <div class="rt-ring-divider"></div>

          <div class="rt-ring-panel">
            <div class="rt-ring-wrap">
              <svg class="rt-ring-svg" viewBox="0 0 96 96">
                <circle class="rt-ring-bg"   cx="48" cy="48" r="38"/>
                <circle class="rt-ring-fill" cx="48" cy="48" r="38" id="ring-jit"
                  stroke="#f0b24f" stroke-dasharray="238.76" stroke-dashoffset="238.76"/>
              </svg>
              <div class="rt-ring-center">
                <div class="rt-ring-value" id="rv-jit">—</div>
                <div class="rt-ring-unit">ms</div>
              </div>
            </div>
            <div class="rt-ring-label">Jitter</div>
            <div class="rt-ring-pct" id="rp-jit">—</div>
          </div>

          <div class="rt-ring-divider"></div>

          <div class="rt-ring-panel">
            <div class="rt-ring-wrap">
              <svg class="rt-ring-svg" viewBox="0 0 96 96">
                <circle class="rt-ring-bg"   cx="48" cy="48" r="38"/>
                <circle class="rt-ring-fill" cx="48" cy="48" r="38" id="ring-thr"
                  stroke="#a871ff" stroke-dasharray="238.76" stroke-dashoffset="238.76"/>
              </svg>
              <div class="rt-ring-center">
                <div class="rt-ring-value" id="rv-thr">—</div>
                <div class="rt-ring-unit">Mbps</div>
              </div>
            </div>
            <div class="rt-ring-label">Throughput</div>
            <div class="rt-ring-pct" id="rp-thr">—</div>
          </div>

        </div>
      </div>

      <!-- ── Footer ────────────────────────────────────────────────────── -->
      <div class="rt-footer">
        <span class="rt-dot" id="rt-dot"></span>
        <span class="rt-info" id="rt-info">${esc(cableName)} · simulando…</span>
        <button class="btn rt-stop" id="rt-stop">Parar</button>
      </div>

    </div>
  `;

  if (containerEl) {
    containerEl.innerHTML = dashHtml;
    containerEl.style.display = '';
  } else {
    ProbeModal.show(`Tempo Real — ${esc(cableName)}`, dashHtml);
    const box = document.getElementById('probe-modal-box');
    box?.classList.add('rt-mode');
  }

  function initRtCharts() {

    const N        = 120;  // time steps (120 × 500 ms = 60 s)
    const INTERVAL = 500;  // ms between updates

    const xs = Array.from({ length: N }, (_, i) => i);

    // Rolling history arrays
    let latHist  = Array(N).fill(baseLat);
    let jitHist  = Array(N).fill(baseJit);
    let lossHist = Array(N).fill(baseLoss);
    let intHist  = Array(N).fill(0);

    // EMA for latency smoothing (2D line)
    let latEMA = baseLat;

    let tick = 0;
    let interferenceActive = false;
    let running = true;

    // ── Shared layout base ─────────────────────────────────────────────────
    const BG    = 'rgba(100,55,185,0.20)';  // roxo claro uniforme
    const GRID  = 'rgba(168,113,255,0.22)';
    const FTEXT = '#c8c0d8';
    const layout = {
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor:  BG,
      font:   { color: FTEXT, family: 'Space Grotesk, sans-serif', size: 9 },
      margin: { l: 38, r: 10, t: 8, b: 30 },
      autosize: true,
    };
    const ax2d = { gridcolor: GRID, color: '#a871ff', zeroline: false };
    const cfg  = { displayModeBar: false, responsive: true };

    // ── Chart 1: Latência 2D linha suavizada ──────────────────────────────
    Plotly.newPlot('rt-lat', [{
      type: 'scatter',
      mode: 'lines',
      x: xs.slice(),
      y: latHist.slice(),
      fill: 'tozeroy',
      fillcolor: 'rgba(240,178,79,0.14)',
      line: { color: '#f0b24f', width: 2, shape: 'spline', smoothing: 1.3 },
    }], {
      ...layout,
      xaxis: { ...ax2d, title: 'tempo (s)' },
      yaxis: { ...ax2d, title: 'ms' },
    }, cfg);

    // ── Chart 2: Jitter 2D linha ──────────────────────────────────────────
    Plotly.newPlot('rt-jit', [{
      type: 'scatter',
      mode: 'lines',
      x: xs.slice(),
      y: jitHist.slice(),
      fill: 'tozeroy',
      fillcolor: 'rgba(168,113,255,0.16)',
      line: { color: '#a871ff', width: 2, shape: 'spline', smoothing: 1.1 },
    }], {
      ...layout,
      xaxis: { ...ax2d },
      yaxis: { ...ax2d, title: 'ms' },
    }, cfg);

    // ── Chart 3: Perda de Pacotes 2D barras ──────────────────────────────
    Plotly.newPlot('rt-loss', [{
      type: 'bar',
      x: xs.slice(),
      y: lossHist.slice(),
      marker: { color: lossHist.map(colorFromLoss) },
    }], {
      ...layout,
      xaxis: { ...ax2d },
      yaxis: { ...ax2d, title: '%', range: [0, 30] },
    }, cfg);

    // ── Chart 4: Interferência 2D linha + área ────────────────────────────
    Plotly.newPlot('rt-int', [{
      type: 'scatter',
      mode: 'lines',
      x: xs.slice(),
      y: intHist.slice(),
      fill: 'tozeroy',
      fillcolor: 'rgba(255,70,95,0.14)',
      line: { color: '#ff465f', width: 2, shape: 'spline', smoothing: 1.2 },
    }], {
      ...layout,
      xaxis: { ...ax2d, title: 'tempo (s)' },
      yaxis: { ...ax2d, title: 'intensidade', range: [0, 35] },
    }, cfg);

    function colorFromLoss(v) {
      const t = Math.min(1, v / 30);
      const r = Math.round(45  + (255 - 45)  * t);
      const g = Math.round(220 + (26  - 220) * t);
      const b = Math.round(130 + (108 - 130) * t);
      return `rgb(${r},${g},${b})`;
    }

    // ── Noise helper ──────────────────────────────────────────────────────
    function noise(base, sigma, min = 0, max = Infinity) {
      return Math.min(max, Math.max(min, base + (Math.random() - 0.5) * 2 * sigma));
    }

    // ── Simulation tick ───────────────────────────────────────────────────
    function simulate() {
      if (!running || !document.getElementById('rt-lat')) return;

      tick++;

      // Random interference bursts
      if (!interferenceActive && Math.random() < 0.07) {
        interferenceActive = true;
        setTimeout(() => { interferenceActive = false; }, 1800 + Math.random() * 2200);
      }

      const spike = interferenceActive ? (2.5 + Math.random() * 4) : 1;

      const newLat  = noise(baseLat  * spike, 14 * spike, 1);
      const newJit  = noise(baseJit  * spike, 5  * spike, 0);
      const newLoss = interferenceActive ? noise(14, 7, 0, 100) : noise(baseLoss, 0.4, 0);
      const newThru = interferenceActive ? noise(baseThru * 0.3, 50, 0) : noise(baseThru, 30, 0);

      // EMA suaviza a linha de latência (α=0.25)
      latEMA = latEMA * 0.75 + newLat * 0.25;

      // Roll histories
      latHist  = [...latHist.slice(1),  latEMA];
      jitHist  = [...jitHist.slice(1),  newJit];
      lossHist = [...lossHist.slice(1), newLoss];

      // Interferência: intensidade alta durante burst, ruído baixo fora
      const intIntensity = interferenceActive ? noise(22, 7, 5, 35) : noise(1.5, 1, 0, 5);
      intHist = [...intHist.slice(1), intIntensity];

      // ── Push updates to Plotly ────────────────────────────────────────
      Plotly.restyle('rt-lat',  { y: [latHist.slice()] });
      Plotly.restyle('rt-jit',  { y: [jitHist.slice()] });
      Plotly.restyle('rt-loss', { y: [lossHist.slice()], 'marker.color': [lossHist.map(colorFromLoss)] });
      Plotly.restyle('rt-int',  { y: [intHist.slice()] });

      // ── Ring-gauge updates ────────────────────────────────────────────
      const CIRC = 238.76;
      function setRing(id, pct, color) {
        const el = document.getElementById(id);
        if (!el) return;
        el.style.strokeDashoffset = CIRC * (1 - Math.min(1, Math.max(0, pct)));
        if (color) el.style.stroke = color;
      }
      function ringColorLat(ms) {
        if (ms < 60)  return '#2ddc82';
        if (ms < 180) return '#f0b24f';
        if (ms < 350) return '#ff7040';
        return '#ff1a6c';
      }
      function ringColorLoss(pct) {
        if (pct < 1)  return '#2ddc82';
        if (pct < 5)  return '#f0b24f';
        if (pct < 15) return '#ff7040';
        return '#ff1a6c';
      }
      const latPct  = newLat  / 500;
      const jitPct  = newJit  / 200;
      const lossPct = newLoss / 30;
      const thrPct  = newThru / 1000;
      setRing('ring-lat',  latPct,  ringColorLat(newLat));
      setRing('ring-jit',  jitPct,  '#f0b24f');
      setRing('ring-loss', lossPct, ringColorLoss(newLoss));
      setRing('ring-thr',  thrPct,  '#a871ff');
      const setText = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
      setText('rv-lat',  newLat.toFixed(0));   setText('rp-lat',  (latPct  * 100).toFixed(0) + '%');
      setText('rv-jit',  newJit.toFixed(1));   setText('rp-jit',  (jitPct  * 100).toFixed(0) + '%');
      setText('rv-loss', newLoss.toFixed(2));  setText('rp-loss', (lossPct * 100).toFixed(0) + '%');
      setText('rv-thr',  newThru.toFixed(0));  setText('rp-thr',  (thrPct  * 100).toFixed(0) + '%');

      // ── Footer status ─────────────────────────────────────────────────
      const dot = document.getElementById('rt-dot');
      if (dot) dot.className = 'rt-dot' + (interferenceActive ? ' alert' : '');
      const infoEl = document.getElementById('rt-info');
      if (infoEl) infoEl.textContent = `${cableName}${interferenceActive ? ' · [ALERTA] interferência detectada' : ' · sonda ativa'}`;

      if (running) setTimeout(simulate, INTERVAL);
    }

    // Register stop callback so ProbeModal.hide() cleans up
    if (!containerEl) ProbeModal._stopFn = () => { running = false; };

    // ── Stop button ───────────────────────────────────────────────────────
    const stopBtn = document.getElementById('rt-stop');
    if (stopBtn) stopBtn.addEventListener('click', () => {
      running = false;
      if (!containerEl) ProbeModal.hide();
    });

    setTimeout(simulate, 400);
  }

  // Load Plotly on demand (avoids conflict with globe.gl OrbitControls at page load)
  if (window.Plotly) {
    setTimeout(initRtCharts, 150);
  } else {
    const s = document.createElement('script');
    s.src = 'https://cdn.plot.ly/plotly-2.35.2.min.js';
    s.onload = () => setTimeout(initRtCharts, 150);
    s.onerror = () => {
      const info = document.getElementById('rt-info');
      if (info) info.textContent = 'Erro ao carregar Plotly.js';
    };
    document.head.appendChild(s);
  }
}

function renderProbeResult(payload) {
  const r = payload.result || payload;
  const reachable   = r.reachable !== undefined ? r.reachable : true;
  const statusCls   = reachable ? 'yes' : 'no';
  const statusTxt   = reachable ? 'Acessível' : 'Inacessível';
  const lossClass   = (r.packet_loss_pct ?? 0) > 5  ? 'danger-text' : 'ok-text';
  const jitterClass = (r.jitter_ms ?? 0)       > 20 ? 'danger-text' : 'ok-text';

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
    <div id="hop-trace-panel" class="hop-trace-panel">
      <div class="hop-trace-loading">Rastreando rota salto a salto…</div>
    </div>
    ${r.note ? `<div class="muted" style="margin-top:10px;font-size:12px;">${esc(r.note)}</div>` : ''}`;
}

function renderHopTrace(hops, targetIp) {
  const panel = document.getElementById('hop-trace-panel');
  if (!panel) return;
  if (!hops || !hops.length) {
    panel.innerHTML = '<div class="hop-trace-empty">Rastreamento indisponível para este endpoint.</div>';
    return;
  }

  // Find bottleneck: hop with largest positive delta_ms
  let bottleneckIdx = -1, maxDelta = 0;
  hops.forEach((h, i) => {
    if ((h.delta_ms || 0) > maxDelta) { maxDelta = h.delta_ms; bottleneckIdx = i; }
  });

  // Max RTT for bar scaling
  const maxRtt = Math.max(...hops.map(h => h.rtt_avg || h.rtt_avg_ms || 0), 1);

  const rows = hops.map((h, i) => {
    const rtt     = h.rtt_avg   ?? h.rtt_avg_ms   ?? 0;
    const best    = h.rtt_best  ?? h.rtt_best_ms  ?? null;
    const worst   = h.rtt_worst ?? h.rtt_worst_ms ?? null;
    const loss    = h.loss_pct  ?? 0;
    const delta   = h.delta_ms  ?? null;
    const ip      = h.ip        ?? '*';
    const barPct  = Math.round((rtt / maxRtt) * 100);
    const isBottleneck = i === bottleneckIdx;
    const lossFlag = loss >= 10 ? 'hop-loss-high' : loss >= 1 ? 'hop-loss-mid' : '';
    const barCls   = isBottleneck ? 'hop-bar-bottleneck' : loss >= 10 ? 'hop-bar-loss' : 'hop-bar-ok';
    const deltaHtml = delta !== null
      ? `<span class="hop-delta ${delta > 20 ? 'hop-delta-warn' : ''}">${delta > 0 ? '+' : ''}${delta} ms</span>`
      : '';

    return `<tr class="${isBottleneck ? 'hop-row-bottleneck' : ''}">
      <td class="hop-n">${h.hop ?? i + 1}</td>
      <td class="mono hop-ip">${esc(ip)}</td>
      <td class="hop-rtt-cell">
        <div class="hop-bar-wrap">
          <div class="hop-bar ${barCls}" style="width:${barPct}%"></div>
        </div>
        <span class="hop-rtt-val">${rtt > 0 ? rtt + ' ms' : '*'}</span>
        ${deltaHtml}
      </td>
      <td class="hop-range">${best !== null && worst !== null ? best + ' – ' + worst + ' ms' : '—'}</td>
      <td class="hop-loss ${lossFlag}">${loss > 0 ? loss + '%' : '0%'}</td>
      ${isBottleneck ? '<td class="hop-flag">⚡ gargalo</td>' : '<td></td>'}
    </tr>`;
  }).join('');

  panel.innerHTML = `
    <div class="hop-trace-header">
      <span class="hop-trace-title">Rastreamento de Rota</span>
      <span class="hop-trace-meta">${hops.length} saltos · destino: <span class="mono">${esc(targetIp || '')}</span></span>
    </div>
    <div class="table-wrap hop-trace-table">
      <table>
        <thead><tr><th>#</th><th>IP</th><th>RTT Médio</th><th>Min – Max</th><th>Perda</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
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
  const cableInput    = document.getElementById('probe-cable-input');
  const cableList     = document.getElementById('probe-cable-list');
  const modeHistory   = document.getElementById('probe-mode-history');
  const modeRT        = document.getElementById('probe-mode-realtime');
  const windowWrap    = document.getElementById('probe-window-wrap');
  const realtimeWrap  = document.getElementById('probe-realtime-wrap');
  const btnSingleProbe = document.getElementById('btn-single-probe');
  const hoursEl       = document.getElementById('telemetry-hours');
  const btnTel        = document.getElementById('btn-telemetry');
  const errEl         = document.getElementById('probe-error');
  const resultEl      = document.getElementById('probe-result');
  const telResultEl   = document.getElementById('telemetry-result');

  function setLegend(janela, qualidade, origem, amostras) {
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v ?? '—'; };
    set('legend-janela',    janela);
    set('legend-qualidade', qualidade);
    set('legend-origem',    origem);
    set('legend-amostras',  amostras);
  }

  function resetContent(placeholder) {
    clearEl(errEl);
    if (telResultEl) telResultEl.innerHTML = '';
    if (resultEl) { resultEl.textContent = placeholder; resultEl.style.display = ''; }
  }

  function applyMode() {
    const isHistory = modeHistory && modeHistory.checked;
    if (windowWrap)   windowWrap.style.display   = isHistory ? '' : 'none';
    if (realtimeWrap) realtimeWrap.style.display = isHistory ? 'none' : '';
  }

  function populateCableList() {
    if (!cableList || !AppState.allCables || !AppState.allCables.length) return;
    cableList.innerHTML = AppState.allCables
      .map(c => `<option value="${esc(c.cable_id)}">${esc(c.cable_name || c.cable_id)}</option>`)
      .join('');
  }

  function tryPopulate() {
    if (AppState.allCables && AppState.allCables.length) populateCableList();
    else setTimeout(tryPopulate, 600);
  }
  tryPopulate();

  applyMode();

  // ── Mode switch ────────────────────────────────────────────────────────
  if (modeHistory) {
    modeHistory.addEventListener('change', () => {
      applyMode();
      resetContent('Clique em Carregar para ver o histórico de janela.');
      setLegend('—', '—', '—', '—');
    });
  }

  if (modeRT) {
    modeRT.addEventListener('change', () => {
      applyMode();
      resetContent('');
      setLegend('Tempo real', '—', '—', '—');
    });
  }

  // ── Sondar Agora (realtime single-cable probe) ─────────────────────────────
  if (btnSingleProbe) {
    btnSingleProbe.addEventListener('click', async () => {
      const cableId = cableInput ? cableInput.value.trim() : '';
      if (!cableId) { showError(errEl, 'Digite o nome ou ID do cabo primeiro.'); return; }

      btnSingleProbe.disabled    = true;
      btnSingleProbe.textContent = 'Sondando…';
      clearEl(errEl);
      resetContent('');

      try {
        // Fetch first endpoint to get a usable IP from the DB
        let endpointId = '';
        try {
          const eps   = await apiFetch({ mode: 'cable_endpoints', q: cableId });
          const first = (eps.endpoints || [])[0];
          if (first) endpointId = first.endpoint_id;
        } catch {}

        const result = await apiFetch({
          mode: 'probe', q: cableId, endpoint_id: endpointId, full: '1',
        });

        if (resultEl) { resultEl.innerHTML = ''; resultEl.style.display = 'none'; }
        const d2 = { cable_name: cableId, cable_id: cableId };
        startProbeRealtime(d2, result, telResultEl, result.result || result);

        // Hop-by-hop trace (async, does not block probe result display)
        const targetIp = result.target || (result.result || {}).target_ip || '';
        apiFetch({ action: 'hop_trace', cable_id: cableId, endpoint_id: endpointId, ip: targetIp, cycles: '5' })
          .then(hp => renderHopTrace(hp.hops || [], hp.target_ip || targetIp))
          .catch(() => {
            const p = document.getElementById('hop-trace-panel');
            if (p) p.innerHTML = '<div class="hop-trace-empty">Rastreamento indisponível.</div>';
          });
      } catch (err) {
        showError(errEl, err.message);
      } finally {
        btnSingleProbe.disabled    = false;
        btnSingleProbe.textContent = 'Sondar Agora';
      }
    });
  }

  // ── Carregar histórico ─────────────────────────────────────────────────
  if (btnTel) {
    btnTel.addEventListener('click', async () => {
      clearEl(errEl);
      const cableId = cableInput ? cableInput.value.trim() : '';
      const hours   = parseInt((hoursEl || {}).value || '24', 10) || 24;
      if (!cableId) { showError(errEl, 'Digite o nome ou ID do cabo.'); return; }

      btnTel.disabled    = true;
      btnTel.textContent = 'Carregando…';
      try {
        const payload  = await apiFetch({ action: 'telemetry', cable_id: cableId, hours });
        const tel      = payload.telemetry || payload;
        const realAvg  = payload.real_avg || null;
        const hasReal  = realAvg && realAvg.sample_count > 0;

        // Legend prefers real TCP probe data; falls back to DB/synthetic
        if (hasReal) {
          const wStart = realAvg.window_start
            ? new Date(realAvg.window_start).toLocaleString('pt-BR', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' })
            : `${hours} h`;
          setLegend(
            wStart,
            realAvg.quality_score != null ? `${realAvg.quality_score} / 100` : '—',
            'TCP Probe',
            realAvg.sample_count
          );
        } else {
          const hist = Array.isArray(tel.history) ? tel.history : [];
          const windowLabel = tel.window_start
            ? new Date(tel.window_start).toLocaleString('pt-BR', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' })
            : `${hours} h`;
          setLegend(
            windowLabel,
            tel.data_quality_score != null ? `${tel.data_quality_score} / 100` : '—',
            tel.source || '—',
            tel.sample_size ?? hist.length
          );
        }

        if (resultEl) { resultEl.innerHTML = ''; resultEl.style.display = 'none'; }
        if (telResultEl) { telResultEl.innerHTML = ''; renderTelemetryResult(payload); }
      } catch (err) {
        showError(errEl, err.message);
      } finally {
        btnTel.disabled    = false;
        btnTel.textContent = 'Carregar';
      }
    });
  }
}

// ─── Daily probe page ─────────────────────────────────────────────────────────

function initDailyPage() {
  const btnStart     = document.getElementById('btn-daily-start');
  const btnCancel    = document.getElementById('btn-daily-cancel');
  const statusBadge  = document.getElementById('daily-status-badge');
  const progressWrap = document.getElementById('daily-progress-wrap');
  const progressBar  = document.getElementById('daily-progress-inner');
  const progressLbl  = document.getElementById('daily-progress-label');
  const errEl        = document.getElementById('daily-error');

  let pollTimer      = null;
  let currentLog     = null;
  let probeStartTime = null;

  // ── localStorage cache ───────────────────────────────────────────────────
  // Persiste progresso + contadores entre recargas e navegações.
  // Buffer de log acumula entradas e só grava no servidor a cada FLUSH_EVERY cabos.
  const CACHE_KEY  = 'rmifo_daily_cache';
  const FLUSH_EVERY = 10;
  let logBuffer = [];

  function saveCache(progress, statsSnap) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({
        ts: Date.now(),
        progress,
        stats: statsSnap,
        log: currentLog,
      }));
    } catch { /* quota — silencioso */ }
  }

  function loadCache() {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (!raw) return null;
      const d = JSON.parse(raw);
      if (Date.now() - d.ts > 86400000) { localStorage.removeItem(CACHE_KEY); return null; } // expirado após 24h
      return d;
    } catch { return null; }
  }

  function flushLogBuffer() {
    if (!logBuffer.length || !currentLog) return;
    const batch = logBuffer.splice(0);
    apiFetch({ action: 'daily_log_batch', log: currentLog, entries: batch }).catch(() => {
      logBuffer.unshift(...batch); // re-enfileira se falhar
    });
  }

  function appendLog(entry) {
    logBuffer.push({ ts: Date.now(), ...entry });
    if (logBuffer.length >= FLUSH_EVERY) flushLogBuffer();
  }

  // ── UI helpers ───────────────────────────────────────────────────────────
  function setRunningUI(running) {
    if (btnStart)  { btnStart.disabled = running; btnStart.innerHTML = running ? ICONS.dot + ' Rodando…' : ICONS.play + ' Iniciar Sonda'; }
    if (btnCancel) { btnCancel.style.display = running ? '' : 'none'; }
  }

  function updateBadge(status) {
    if (!statusBadge) return;
    const map = { running: ICONS.dot + ' Em execução', completed: ICONS.check + ' Concluído', idle: '', already_running: ICONS.dot + ' Em execução' };
    statusBadge.innerHTML = map[status] || '';
    statusBadge.className = 'daily-status-badge ' + (status === 'completed' ? 'done' : status === 'running' || status === 'already_running' ? 'running' : '');
  }

  function updateProgress(p) {
    if (!p) return;
    const pct = p.percent || 0;
    if (progressWrap) progressWrap.style.display = '';
    if (progressBar)  progressBar.style.width = pct + '%';
    if (progressLbl)  progressLbl.textContent = `${p.current} / ${p.total} — ${pct.toFixed(1)}%`;
    const dstatSw = document.getElementById('dstat-switches');
    if (dstatSw && p.switch_count > 0) dstatSw.textContent = p.switch_count;
  }

  function setStats(summary, progress, live) {
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v ?? '—'; };
    const setPct = (id, n, base) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = (n != null && base > 0) ? `${((n / base) * 100).toFixed(1)}%` : '—';
    };
    const vc     = progress?.var_counts ?? {};
    const base   = summary?.inserted ?? progress?.total ?? null;
    const tcp    = summary?.tcp        ?? vc.tcp_syn    ?? live?.tcp   ?? null;
    const jitter = summary?.tcp_jitter ?? vc.tcp_jitter ?? null;
    const synth  = summary?.synthetic  ?? vc.synthetic  ?? live?.synth ?? null;
    const mtr    = summary?.mtr        ?? vc.mtr_udp    ?? null;
    const http   = summary?.http       ?? vc.http_curl  ?? live?.http  ?? null;
    const anom   = summary?.anomalies  ?? null;
    set('dstat-total',     progress ? `${progress.current} / ${progress.total}` : (summary?.inserted ?? null));
    set('dstat-tcp',       tcp);    setPct('dstat-pct-tcp',    tcp,    base);
    set('dstat-jitter',    jitter); setPct('dstat-pct-jitter', jitter, base);
    set('dstat-mtr',       mtr);    setPct('dstat-pct-mtr',    mtr,    base);
    set('dstat-http',      http);   setPct('dstat-pct-http',   http,   base);
    set('dstat-synth',     synth);  setPct('dstat-pct-synth',  synth,  base);
    set('dstat-anomalies', anom);

    // Persiste snapshot dos contadores no cache
    saveCache(progress, { tcp, jitter, synth, mtr, http, anom, base });
  }

  // ── Restore cache on page open ───────────────────────────────────────────
  function restoreFromCache() {
    const c = loadCache();
    if (!c) return;
    if (c.progress) updateProgress(c.progress);
    if (c.stats) {
      const s = c.stats;
      const set = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.textContent = v; };
      const setPct = (id, n, base) => {
        const el = document.getElementById(id);
        if (el && n != null && base > 0) el.textContent = `${((n / base) * 100).toFixed(1)}%`;
      };
      set('dstat-total',     c.progress ? `${c.progress.current} / ${c.progress.total}` : s.base);
      set('dstat-tcp',       s.tcp);    setPct('dstat-pct-tcp',    s.tcp,    s.base);
      set('dstat-jitter',    s.jitter); setPct('dstat-pct-jitter', s.jitter, s.base);
      set('dstat-mtr',       s.mtr);    setPct('dstat-pct-mtr',    s.mtr,    s.base);
      set('dstat-http',      s.http);   setPct('dstat-pct-http',   s.http,   s.base);
      set('dstat-synth',     s.synth);  setPct('dstat-pct-synth',  s.synth,  s.base);
      set('dstat-anomalies', s.anom);
    }
    if (c.log) currentLog = c.log;
  }

  // ── Poll ─────────────────────────────────────────────────────────────────
  async function poll() {
    if (!currentLog) return;
    try {
      const data = await apiFetch({ action: 'daily_status', log: currentLog });
      const live = { tcp: data.live_tcp || null, synth: data.live_synth || null };

      updateBadge(data.status);
      if (data.progress) updateProgress(data.progress);
      if (data.summary)  setStats(data.summary, data.progress, live);
      else if (data.progress || live.tcp) setStats(null, data.progress, live);

      // Acumula entradas recentes no buffer de log local
      if (data.progress?.recent?.length) {
        data.progress.recent.forEach(r => appendLog({ cable_id: r.cable_id, latency_ms: r.latency_ms, proto: r.proto, hops: r.hops }));
      }

      if (data.status === 'starting') {
        if (progressLbl) progressLbl.textContent = 'Iniciando sonda…';
        pollTimer = setTimeout(poll, 4000);
      } else if (data.status === 'running' || data.status === 'already_running') {
        if (!data.progress && progressLbl) progressLbl.textContent = 'Sonda em andamento…';
        pollTimer = setTimeout(poll, 7000);
      } else if (data.status === 'idle') {
        const elapsed = probeStartTime ? (Date.now() - probeStartTime) / 1000 : 9999;
        if (elapsed < 900) {
          if (progressLbl) progressLbl.textContent = elapsed < 120 ? 'Iniciando sonda…' : 'Processando…';
          pollTimer = setTimeout(poll, elapsed < 60 ? 3000 : 6000);
        } else {
          flushLogBuffer();
          setRunningUI(false);
        }
      } else if (data.status === 'completed') {
        flushLogBuffer();
        AppState.probeActiveCable = null;
        if (AppState.globePaths.length) refreshGlobeProbeHighlight();
        if (progressBar) progressBar.style.width = '100%';
        if (progressLbl) progressLbl.textContent = `${data.summary?.inserted ?? '699'} / ${data.summary?.inserted ?? '699'} — 100%`;
        setRunningUI(false);
        probeStartTime = null;
      }
    } catch (err) {
      if (errEl) { errEl.textContent = err.message; errEl.style.display = ''; }
      pollTimer = setTimeout(poll, 8000);
    }
  }

  // ── Start ────────────────────────────────────────────────────────────────
  if (btnStart) {
    btnStart.addEventListener('click', async () => {
      const fullScan = document.getElementById('daily-full-scan')?.checked ? '1' : '0';
      setRunningUI(true);
      btnStart.innerHTML = ICONS.clock + ' Iniciando…';
      if (errEl) errEl.style.display = 'none';
      logBuffer = [];
      try {
        const data = await apiFetch({ action: 'daily_start', full_scan: fullScan, mtr_cycles: fullScan === '1' ? '30' : '15' });
        if (data.status === 'error') {
          if (errEl) { errEl.textContent = data.message; errEl.style.display = ''; }
          setRunningUI(false);
          return;
        }
        currentLog     = data.log_file || null;
        probeStartTime = Date.now();
        updateBadge(data.status === 'already_running' ? 'already_running' : 'running');
        if (pollTimer) clearTimeout(pollTimer);
        pollTimer = setTimeout(poll, 4000);
      } catch (err) {
        if (errEl) { errEl.textContent = err.message; errEl.style.display = ''; }
        setRunningUI(false);
      }
    });
  }

  // ── Cancel ───────────────────────────────────────────────────────────────
  if (btnCancel) {
    btnCancel.addEventListener('click', async () => {
      btnCancel.disabled = true;
      btnCancel.textContent = 'Cancelando…';
      try { await apiFetch({ action: 'daily_cancel' }); } catch { /* best-effort */ }
      if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
      flushLogBuffer();
      currentLog     = null;
      probeStartTime = null;
      logBuffer      = [];
      AppState.probeActiveCable = null;
      if (AppState.globePaths.length) refreshGlobeProbeHighlight();
      setRunningUI(false);
      updateBadge('idle');
      if (progressWrap) progressWrap.style.display = 'none';
      btnCancel.disabled = false;
    });
  }

  // ── Navegação para a página: restaura cache e verifica status ────────────
  document.querySelector('[data-page="daily"]')?.addEventListener('click', async () => {
    restoreFromCache(); // mostra dados anteriores instantaneamente
    try {
      const data = await apiFetch({ action: 'daily_status' });
      const live = { tcp: data.live_tcp || null, synth: data.live_synth || null };
      if ((data.status === 'running' || data.status === 'already_running') && data.log_file) {
        currentLog     = data.log_file;
        probeStartTime = probeStartTime ?? Date.now();
        updateBadge('running');
        setRunningUI(true);
        if (data.progress) { if (progressWrap) progressWrap.style.display = ''; updateProgress(data.progress); }
        if (data.summary)  setStats(data.summary, data.progress, live);
        else if (live.tcp) setStats(null, data.progress, live);
        if (!pollTimer) pollTimer = setTimeout(poll, 3000);
      } else if (data.status === 'completed') {
        updateBadge('completed');
        if (data.summary) setStats(data.summary, null, null);
        if (progressBar) progressBar.style.width = '100%';
      }
    } catch { /* silent — cache já exibido */ }
  });
}

// ─── Switch probe page ────────────────────────────────────────────────────────

function initSwitchPage() {
  const runBtn      = document.getElementById('btn-sw-run');
  const cancelBtn   = document.getElementById('btn-sw-cancel');
  const cyclesInput = document.getElementById('sw-mtr-cycles');
  const stepInput   = document.getElementById('sw-ip-step');
  const errorEl     = document.getElementById('sw-error');
  const statusDot   = document.getElementById('sw-status-dot');
  const phaseBar    = document.getElementById('sw-phase-bar');

  let liveTimer    = null;
  let isRunning    = false;
  let lastProgress = { cur: 0, tot: 0 };

  // Acumula IPs/hops/subnets/ISPs em tempo real a partir de live.recent
  const liveIPs     = new Set();
  const liveHops    = new Set();
  const liveSubnets = new Set();
  const liveISPs    = new Set();

  // ── Cache localStorage ───────────────────────────────────────────────────
  const CACHE_KEY = 'rmifo_sw_cache';

  function saveCache() {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({
        ts: Date.now(), progress: lastProgress,
        ips: liveIPs.size, hops: liveHops.size,
        subnets: liveSubnets.size, isps: liveISPs.size,
      }));
    } catch {}
  }

  function restoreFromCache() {
    try {
      const d = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null');
      if (!d || Date.now() - d.ts > 86400000) return;
      updateCounters(d.progress, d.ips, d.hops, d.subnets, d.isps);
    } catch {}
  }

  // ── Phase bar ────────────────────────────────────────────────────────────
  function updatePhaseBar(pct) {
    const steps = {
      'swpb-start': pct === 0,
      'swpb-mtr':   pct > 0  && pct < 90,
      'swpb-geo':   pct >= 90 && pct < 96,
      'swpb-db':    pct >= 96 && pct < 100,
      'swpb-done':  pct >= 100,
    };
    Object.entries(steps).forEach(([id, active]) => {
      const el = document.getElementById(id);
      if (el) el.className = 'dpb-step' + (active ? ' dpb-active' : (pct >= 100 ? ' dpb-done' : ''));
    });
  }

  // ── Contadores ───────────────────────────────────────────────────────────
  // Aceita valores diretos (número) para permitir restore do cache sem Sets
  function updateCounters(progress, ips, hops, subnets, isps) {
    const g = id => document.getElementById(id);
    const { cur, tot } = progress || lastProgress;
    const pct = tot > 0 ? Math.round((cur / tot) * 100) : 0;

    if (g('swstat-progress'))     g('swstat-progress').textContent     = tot > 0 ? `${cur} / ${tot}` : '—';
    if (g('swstat-pct-progress')) g('swstat-pct-progress').textContent = tot > 0 ? `${pct}%` : '—';
    if (g('swstat-ips'))          g('swstat-ips').textContent          = ips  || '—';
    if (g('swstat-hops'))         g('swstat-hops').textContent         = hops || '—';
    if (g('swstat-pct-hops'))     g('swstat-pct-hops').textContent     = (ips && hops) ? `${Math.round((hops / ips) * 100)}%` : '—';
    if (g('swstat-subnets'))      g('swstat-subnets').textContent      = subnets || '—';
    if (g('swstat-isp'))          g('swstat-isp').textContent          = isps || '—';

    saveCache();
  }

  // ── Absorve entradas de live.recent nos Sets de tempo real ───────────────
  function absorbRecent(recent) {
    if (!Array.isArray(recent)) return;
    recent.forEach(r => {
      if (r.ip)     liveIPs.add(r.ip);
      if (r.hop_ip) liveHops.add(r.hop_ip);
      const subnet = (r.ip || r.hop_ip || '').split('.').slice(0, 3).join('.');
      if (subnet)   liveSubnets.add(subnet);
      if (r.isp || r.org || r.as_name) liveISPs.add(r.isp || r.org || r.as_name);
    });
  }

  // ── Carrega dados do DB após conclusão ───────────────────────────────────
  async function loadData() {
    try {
      const [segs, geo] = await Promise.all([
        apiFetch({ action: 'switch_segments' }),
        apiFetch({ action: 'switch_geo' }),
      ]);
      const allSegs = Array.isArray(segs) ? segs : (segs?.registros || []);
      const allGeo  = Array.isArray(geo)  ? geo  : (geo?.registros  || []);
      const dbIPs     = allGeo.length;
      const dbHops    = new Set(allSegs.map(s => s.hop_ip).filter(Boolean)).size;
      const dbSubnets = new Set(allGeo.map(g => (g.hop_ip || '').split('.').slice(0, 3).join('.')).filter(Boolean)).size;
      const dbISPs    = new Set(allGeo.map(g => g.isp || g.org || g.as_name).filter(Boolean)).size;
      updateCounters(lastProgress, dbIPs, dbHops, dbSubnets, dbISPs);
    } catch (e) {
      if (errorEl) errorEl.textContent = 'Erro ao carregar dados: ' + e.message;
    }
  }

  // ── Status dot ───────────────────────────────────────────────────────────
  function setDot(state) {
    if (!statusDot) return;
    statusDot.className = 'sw-status-dot';
    if (state === 'running') { statusDot.className += ' dot-running'; statusDot.title = 'Rodando…'; }
    if (state === 'done')    { statusDot.className += ' dot-done';    statusDot.title = 'Concluído'; }
    if (state === 'idle')    { statusDot.title = 'Aguardando'; }
  }

  function setRunningUI(running) {
    isRunning = running;
    if (runBtn) {
      runBtn.disabled  = running;
      runBtn.innerHTML = running
        ? '<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="8" cy="8" r="5"/></svg> Rodando…'
        : '<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><polygon points="4 2.5 13 8 4 13.5"/></svg> Executar MTR';
    }
    if (cancelBtn) cancelBtn.style.display = running ? '' : 'none';
  }

  // ── Polling live ─────────────────────────────────────────────────────────
  function pollLive() {
    if (liveTimer) clearInterval(liveTimer);
    liveTimer = setInterval(async () => {
      try {
        const live = await apiFetch({ action: 'switch_live' });
        if (!live) return;

        const status = live.status || (live.current ? 'running' : 'starting');

        if (status === 'starting') {
          updatePhaseBar(0);
          setRunningUI(true);
          setDot('running');
          return;
        }

        if (status === 'running' || live.current) {
          const cur = live.current || 0;
          const tot = live.total   || 1;
          const pct = Math.round(live.percent ?? (cur / tot) * 100);
          lastProgress = { cur, tot };
          updatePhaseBar(pct);
          setRunningUI(true);
          setDot('running');
          // Acumula IPs/hops de live.recent em tempo real
          absorbRecent(live.recent);
          updateCounters({ cur, tot }, liveIPs.size, liveHops.size, liveSubnets.size, liveISPs.size);
        }

        if (status === 'completed' || (live.current && live.current >= (live.total || Infinity))) {
          clearInterval(liveTimer); liveTimer = null;
          const cur = live.current || 0;
          const tot = live.total   || cur;
          lastProgress = { cur, tot };
          updatePhaseBar(100);
          setRunningUI(false);
          setDot('done');
          loadData(); // substitui contadores pelos valores finais do DB
          return;
        }

        if (status === 'idle') {
          clearInterval(liveTimer); liveTimer = null;
          setRunningUI(false);
          setDot('idle');
        }
      } catch { /* ignora erros de rede */ }
    }, 3000);
  }

  // ── Executar ─────────────────────────────────────────────────────────────
  runBtn?.addEventListener('click', async () => {
    if (isRunning) return;
    const cycles = parseInt(cyclesInput?.value || '5', 10);
    const step   = parseInt(stepInput?.value   || '8', 10);
    setRunningUI(true);
    setDot('running');
    updatePhaseBar(0);
    liveIPs.clear(); liveHops.clear(); liveSubnets.clear(); liveISPs.clear();
    if (errorEl) errorEl.textContent = '';
    try {
      // Build IP ranges from DB before starting (silent — failure is non-blocking)
      await apiFetch({ action: 'build_ip_ranges' }).catch(() => {});
      const res = await apiFetch({ action: 'switch_start', mtr_cycles: cycles, probe_mode: 'ip_ranges', ip_step: step });
      if (res?.status === 'error') {
        if (errorEl) errorEl.textContent = res.message || 'Erro ao iniciar.';
        setRunningUI(false); setDot('idle'); return;
      }
      pollLive();
    } catch (e) {
      if (errorEl) errorEl.textContent = e.message;
      setRunningUI(false); setDot('idle');
    }
  });

  // ── Parar ────────────────────────────────────────────────────────────────
  cancelBtn?.addEventListener('click', async () => {
    cancelBtn.disabled = true;
    try { await apiFetch({ action: 'switch_cancel' }); } catch {}
    if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
    setRunningUI(false);
    setDot('idle');
    cancelBtn.disabled = false;
  });

  // ── Detecta sonda ativa ao abrir a página ────────────────────────────────
  async function detectRunning() {
    try {
      const live = await apiFetch({ action: 'switch_live' });
      if (live && (live.status === 'running' || live.status === 'starting')) {
        setRunningUI(true);
        setDot('running');
        const pct = live.percent ?? 0;
        updatePhaseBar(pct);
        pollLive();
      }
    } catch {}
  }

  document.querySelectorAll('.nav-link[data-page="switch"]').forEach(a => {
    a.addEventListener('click', () => {
      restoreFromCache();
      loadData();
      detectRunning();
    });
  });

  restoreFromCache();
  loadData();
  detectRunning();
}

// ─── History page ─────────────────────────────────────────────────────────────

function initHistoryPage() {
  const dateSelect    = document.getElementById('hist-date-sel');
  const filterCable   = document.getElementById('hist-filter-cable');
  const filterProto   = document.getElementById('hist-filter-proto');
  const filterStatus  = document.getElementById('hist-filter-status');
  const filterLatMin  = document.getElementById('hist-filter-lat-min');
  const filterLatMax  = document.getElementById('hist-filter-lat-max');
  const filterAnom    = document.getElementById('hist-filter-anom');
  const btnLoad       = document.getElementById('btn-hist-load');
  const errEl         = document.getElementById('hist-error');
  const summaryEl     = document.getElementById('hist-summary');
  const tableWrap     = document.getElementById('hist-table-wrap');
  const tbody         = document.getElementById('hist-tbody');

  let allCables   = [];
  let vecCableIds = { A: new Set(), B: new Set(), C: new Set(), D: new Set(), E: new Set() };
  let activeVecs  = new Set(); // empty = show all

  // ── Carrega conjuntos de cable_ids por vetor avançado ────────────────────
  async function loadVectorCableIds() {
    const wrap = document.getElementById('hist-vector-filters');
    if (!wrap) return;
    try {
      const data = await apiFetch({ action: 'vector_cable_ids' });
      for (const v of ['A', 'B', 'C', 'D', 'E']) {
        vecCableIds[v] = new Set(data[v] || []);
        const chip = wrap.querySelector(`[data-vec="${v}"]`);
        if (chip) {
          const n = vecCableIds[v].size;
          chip.textContent = `${v}${n ? ' ' + n : ''}`;
          chip.disabled    = n === 0;
        }
      }
    } catch { /* tabelas ainda não existem */ }
  }

  // ── Label humanizado para cada valor de proto ─────────────────────────────
  function protoLabel(p) {
    if (!p) return 'Todos os protocolos';
    if (p === 'tcp')                return 'TCP (qualquer porta)';
    if (p === 'icmp')               return 'ICMP Echo';
    if (p === 'http')               return 'HTTP / HTTPS';
    if (p === 'mtr')                return 'MTR Traceroute';
    if (p === 'heuristic')          return 'Sintético · Heurístico';
    if (p === 'heuristic-fallback') return 'Sintético · Fallback';
    if (p === 'synthetic_fallback') return 'Sintético · Fallback';
    if (p === 'icmp-echo')          return 'ICMP Echo';
    if (p === 'mtr-traceroute')     return 'MTR Traceroute';
    if (p === 'tcp-full-scan-port80') return 'TCP · varredura completa';
    // tcp-100pkt-ports[80, 443]  →  TCP · 100pkt · portas 80, 443
    const tcpPkt = p.match(/^tcp-(\d+pkt)-ports\[([^\]]+)\]$/);
    if (tcpPkt) return `TCP · ${tcpPkt[1]} · portas ${tcpPkt[2]}`;
    if (p.startsWith('tcp-connect-port')) return `TCP · porta ${p.replace('tcp-connect-port', '')}`;
    if (p.startsWith('tcp-'))       return `TCP · ${p.replace(/^tcp-/, '')}`;
    if (p.startsWith('http-head-')) return `HTTP · ${p.replace('http-head-', '').toUpperCase()}`;
    return p;
  }

  // ── Popula o select de protocolo a partir dos dados reais do dia ──────────
  function populateProtoFilter(protoList) {
    if (!filterProto) return;
    const prev = filterProto.value;

    // Categorias fixas de agrupamento (aparecem primeiro)
    const cats = [{ val: '', label: 'Todos os protocolos' }];
    const hasCat = (key) => (protoList || []).some(p => p.includes(key));

    if (hasCat('tcp'))       cats.push({ val: 'tcp',      label: 'TCP (qualquer porta)' });
    if (hasCat('icmp'))      cats.push({ val: 'icmp',     label: 'ICMP Echo' });
    if (hasCat('http'))      cats.push({ val: 'http',     label: 'HTTP / HTTPS' });
    if (hasCat('mtr'))       cats.push({ val: 'mtr',      label: 'MTR Traceroute' });
    if (hasCat('heuristic') || hasCat('synthetic'))
                             cats.push({ val: 'heuristic', label: 'Sintético' });

    // Protocolos específicos (p.ex. tcp-connect-port22)
    const grouped = new Set(cats.map(c => c.val));
    for (const p of (protoList || [])) {
      if (!grouped.has(p)) {
        cats.push({ val: p, label: protoLabel(p) });
        grouped.add(p);
      }
    }

    filterProto.innerHTML = cats.map(c =>
      `<option value="${esc(c.val)}">${esc(c.label)}</option>`
    ).join('');

    // Restaura seleção anterior se ainda válida
    if (prev && [...filterProto.options].some(o => o.value === prev)) {
      filterProto.value = prev;
    }
  }

  // ── Tipo de badge por proto ───────────────────────────────────────────────
  function protoBadge(proto) {
    if (proto.includes('tcp'))        return ['TCP',       'yes'];
    if (proto.includes('icmp'))       return ['ICMP',      'warn'];
    if (proto.includes('http'))       return ['HTTP',      ''];
    if (proto.includes('mtr'))        return ['MTR',       ''];
    if (proto.includes('heuristic') || proto.includes('synthetic')) return ['Sintético', 'no'];
    return [proto, ''];
  }

  function fmtMs(v) {
    return v != null ? `${v} ms` : '<span class="muted">—</span>';
  }

  function renderTable(cables) {
    if (!tbody) return;
    tbody.innerHTML = cables.map(c => {
      const [typeLabel, typeCls] = protoBadge(c.proto || '');
      const latClass   = (c.latency_ms ?? 0) > 300 ? 'danger-text' : (c.latency_ms ?? 0) > 150 ? '' : 'ok-text';
      const hasHops    = c.mtr_hops > 0;
      const methShort  = (c.proto || 'heuristic').split('+').map(p => p.split('-')[0]).join('+');
      const tcpPortStr = c.tcp_ports_open ? c.tcp_ports_open.join(',') : null;
      return `<tr class="hist-row" data-cable="${esc(c.cable_id)}" style="cursor:pointer;"
              title="Clique para ver rota ponto a ponto">
        <td>${c.index}</td>
        <td class="mono" style="font-size:12px;">${esc(c.cable_id)}</td>
        <td class="${c.mtr_rtt_ms != null ? 'ok-text' : 'muted'}">${fmtMs(c.mtr_rtt_ms)}</td>
        <td class="mono">${hasHops ? c.mtr_hops : '<span class="muted">—</span>'}</td>
        <td class="${c.tcp_jitter_mean_ms != null ? '' : 'muted'}">${fmtMs(c.tcp_jitter_mean_ms)}${tcpPortStr ? `<span class="muted" style="font-size:10px;"> p:${tcpPortStr}</span>` : ''}</td>
        <td class="${(c.tcp_jitter_p95_ms ?? 0) > 200 ? 'danger-text' : ''}">${fmtMs(c.tcp_jitter_p95_ms)}</td>
        <td class="${c.http_rtt_ms != null ? '' : 'muted'}">${fmtMs(c.http_rtt_ms)}</td>
        <td class="${latClass}">${fmtMs(c.latency_ms)}</td>
        <td style="font-size:10px;max-width:160px;overflow:hidden;text-overflow:ellipsis;" title="${esc(c.proto || '')}">${esc(methShort)}</td>
        <td><span class="badge ${typeCls}">${typeLabel}</span></td>
      </tr>`;
    }).join('');
    if (tableWrap) tableWrap.style.display = cables.length ? '' : 'none';
  }

  // ── Hop-path panel ────────────────────────────────────────────────────────
  const hopPanel    = document.getElementById('hist-hop-panel');
  const hopContent  = document.getElementById('hist-hop-content');
  const hopCableEl  = document.getElementById('hist-hop-cable');
  const hopCloseBtn = document.getElementById('hist-hop-close');

  if (hopCloseBtn) hopCloseBtn.addEventListener('click', () => {
    if (hopPanel) hopPanel.style.display = 'none';
  });

  async function showHopPath(cableId, date) {
    if (!hopPanel || !hopContent) return;
    hopPanel.style.display = '';
    if (hopCableEl) hopCableEl.textContent = `${cableId} · ${date}`;
    hopContent.innerHTML = '<p class="muted" style="padding:12px;">Carregando rota…</p>';
    try {
      const data = await apiFetch({ action: 'cable_hops_day', cable_id: cableId, date });
      const hops = data.hops || [];
      if (!hops.length) {
        hopContent.innerHTML = '<p class="muted" style="padding:12px;">Nenhum hop MTR registrado para este cabo nesta data.</p>';
        return;
      }
      const maxRtt = Math.max(...hops.map(h => h.rtt_avg_ms || 0), 1);
      const rows = hops.map(h => {
        const bar  = Math.round(((h.rtt_avg_ms || 0) / maxRtt) * 100);
        const loc  = [h.city, h.country].filter(Boolean).join(', ') || '—';
        const isp  = h.isp || h.org || h.as_name || '—';
        const loss = h.loss_pct > 0 ? `<span class="hop-loss ${h.loss_pct >= 10 ? 'hop-loss-high' : 'hop-loss-mid'}">${h.loss_pct}%</span>` : '';
        return `<tr>
          <td class="hop-n">${h.hop_number}</td>
          <td class="mono hop-ip">${esc(h.hop_ip || '*')}</td>
          <td class="hop-rtt-cell">
            <div class="hop-bar-wrap"><div class="hop-bar hop-bar-ok" style="width:${bar}%"></div></div>
            <span class="hop-rtt-val">${h.rtt_avg_ms != null ? h.rtt_avg_ms + ' ms' : '*'}</span>
          </td>
          <td>${esc(loc)}</td>
          <td style="font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;">${esc(isp)}</td>
          <td>${loss}</td>
        </tr>`;
      }).join('');
      hopContent.innerHTML = `<div class="table-wrap hop-trace-table" style="margin-top:8px;">
        <table>
          <thead><tr><th>#</th><th>IP</th><th>RTT Médio</th><th>Localização</th><th>ISP / AS</th><th>Perda</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
    } catch (err) {
      hopContent.innerHTML = `<p class="error-text" style="padding:12px;">${esc(err.message)}</p>`;
    }
  }

  // Click delegation on tbody for hop path
  if (tbody) {
    tbody.addEventListener('click', e => {
      const row = e.target.closest('.hist-row');
      if (!row) return;
      const cableId = row.dataset.cable;
      const date    = dateSelect?.value || '';
      if (cableId && date) showHopPath(cableId, date);
    });
  }

  function applyFilters() {
    const cq      = (filterCable?.value  || '').toLowerCase().trim();
    const pq      = filterProto?.value   || '';
    const sq      = filterStatus?.value  || '';
    const latMin  = parseFloat(filterLatMin?.value) || null;
    const latMax  = parseFloat(filterLatMax?.value) || null;
    const anomOnly= filterAnom?.checked  || false;

    let filtered = allCables;
    if (cq) filtered = filtered.filter(c => c.cable_id.toLowerCase().includes(cq));
    if (pq) filtered = filtered.filter(c => (c.proto || '').includes(pq));
    if (sq === 'real')  filtered = filtered.filter(c => c.source !== 'db' && !(c.proto || '').includes('heuristic') && !(c.proto || '').includes('synthetic'));
    if (sq === 'synth') filtered = filtered.filter(c => c.source === 'db' || (c.proto || '').includes('heuristic') || (c.proto || '').includes('synthetic'));
    if (latMin !== null) filtered = filtered.filter(c => (c.latency_ms ?? 0) >= latMin);
    if (latMax !== null) filtered = filtered.filter(c => (c.latency_ms ?? 9999) <= latMax);
    if (anomOnly) filtered = filtered.filter(c => (c.latency_ms ?? 0) > 300);
    if (activeVecs.size > 0) {
      const allowed = new Set();
      for (const v of activeVecs) { for (const id of vecCableIds[v]) allowed.add(id); }
      filtered = filtered.filter(c => allowed.has(c.cable_id));
    }
    renderTable(filtered);
  }

  function setSummary(s) {
    if (!s || !summaryEl) return;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v ?? '—'; };
    set('h-total',     s.inserted ?? allCables.length);
    set('h-tcp',       s.tcp        ?? '—');
    set('h-jitter',    s.tcp_jitter ?? '—');
    set('h-mtr',       s.mtr        ?? '—');
    set('h-http',      s.http       ?? '—');
    set('h-synth',     s.synthetic  ?? '—');
    set('h-anomalies', s.anomalies  ?? 0);
    if (s.completed_at) {
      try {
        const d = new Date(s.completed_at);
        set('h-completed', isNaN(d) ? s.completed_at : d.toLocaleString('pt-BR', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' }));
      } catch { set('h-completed', String(s.completed_at).slice(0, 16)); }
    } else {
      set('h-completed', '—');
    }
    summaryEl.style.display = '';
  }

  async function loadHistoryDates() {
    if (!dateSelect) return;
    try {
      const data  = await apiFetch({ action: 'history_list' });
      const dates = data.dates || [];
      if (!dates.length) {
        dateSelect.innerHTML = '<option value="">Nenhum histórico encontrado</option>';
        if (errEl) { errEl.textContent = 'Nenhum histórico encontrado. Execute a Sonda Diária primeiro.'; errEl.style.display = ''; }
        return;
      }
      dateSelect.innerHTML = '<option value="">— Selecione uma data —</option>'
        + dates.map(d => {
            const info = d.run_count > 0
              ? (d.samples ? `${d.run_count} exec. · ${d.samples} amostras` : `${d.run_count} exec.`)
              : `${d.samples ?? '?'} amostras BD`;
            return `<option value="${esc(d.date)}">${esc(d.date)} (${info})</option>`;
          }).join('');

      // Auto-seleciona hoje e carrega protocolos para popular o filtro
      const today = new Date().toISOString().slice(0, 10);
      const todayOpt = [...dateSelect.options].find(o => o.value === today);
      if (todayOpt) {
        dateSelect.value = today;
        loadDayData(today, true); // só proto_list, sem mostrar tabela
      }
    } catch (err) {
      if (errEl) { errEl.textContent = 'Erro ao carregar datas: ' + err.message; errEl.style.display = ''; }
    }
  }

  // Carrega dados de um dia; protoOnly=true apenas popula o filtro sem exibir tabela
  async function loadDayData(date, protoOnly = false) {
    try {
      const data = await apiFetch({ action: 'history_day', date });
      populateProtoFilter(data.proto_list || []);
      if (!protoOnly) {
        allCables = data.cables || [];
        setSummary(data.summary);
        applyFilters();
      }
    } catch {
      if (!protoOnly && errEl) {
        // mostra erro apenas quando o usuário pediu explicitamente
      }
    }
  }

  // Carrega datas e vetores ao abrir a página (nav click)
  document.querySelector('[data-page="history"]')?.addEventListener('click', () => {
    if (dateSelect && dateSelect.options.length <= 1) loadHistoryDates();
    if (Object.values(vecCableIds).every(s => s.size === 0)) loadVectorCableIds();
  });

  if (filterCable)  filterCable.addEventListener('input',  applyFilters);
  if (filterProto)  filterProto.addEventListener('change', applyFilters);
  if (filterStatus) filterStatus.addEventListener('change', applyFilters);
  if (filterLatMin) filterLatMin.addEventListener('input',  applyFilters);
  if (filterLatMax) filterLatMax.addEventListener('input',  applyFilters);
  if (filterAnom)   filterAnom.addEventListener('change',  applyFilters);

  document.getElementById('hist-vector-filters')?.addEventListener('click', e => {
    const chip = e.target.closest('[data-vec]');
    if (!chip || chip.disabled) return;
    const v = chip.dataset.vec;
    activeVecs.has(v) ? activeVecs.delete(v) : activeVecs.add(v);
    chip.classList.toggle('active', activeVecs.has(v));
    applyFilters();
  });

  if (btnLoad) {
    btnLoad.addEventListener('click', async () => {
      const date = dateSelect?.value || '';
      if (!date) { if (errEl) { errEl.textContent = 'Selecione uma data.'; errEl.style.display = ''; } return; }
      if (errEl) errEl.style.display = 'none';
      if (summaryEl) summaryEl.style.display = 'none';
      if (tableWrap) tableWrap.style.display = 'none';
      btnLoad.disabled = true; btnLoad.textContent = 'Carregando…';
      try {
        const data = await apiFetch({ action: 'history_day', date });
        allCables = data.cables || [];
        populateProtoFilter(data.proto_list || []);
        setSummary(data.summary);
        applyFilters();
      } catch (err) {
        if (errEl) { errEl.textContent = err.message; errEl.style.display = ''; }
      } finally {
        btnLoad.disabled = false; btnLoad.textContent = 'Carregar';
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
  initStarBackground();
  initWelcomeCard();
  initGlobe();
  initGlobePanel();

  // Load shared data
  await Promise.allSettled([loadCableList(), loadCountries()]);

  initSearchPage();
  initDetailPage();
  initRoutesPage();
  initTelemetryPage();
  initProbePage();
  initDailyPage();
  initSwitchPage();
  initHistoryPage();

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
