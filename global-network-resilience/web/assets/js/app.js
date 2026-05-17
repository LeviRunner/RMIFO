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
    map:     'Mapa Global',
    search:  'Busca de Cabos',
    detail:  'Detalhes do Cabo',
    routes:  'Top 3 Rotas',
    risk:    'Risco de Endpoints',
    cuts:    'Cortes Críticos',
    probe:   'Sonda & Telemetria',
    daily:   'Sonda Diária',
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
    return 'rgba(255,240,60,1.0)';
  if (!d.is_operational) return 'rgba(255,70,95,0.85)';
  if (d.has_alert)       return 'rgba(255,185,45,0.90)';
  if (d.has_tcp_probe)   return 'rgba(168,113,255,0.88)';
  return 'rgba(45,220,130,0.82)';
}

function refreshGlobeProbeHighlight() {
  if (AppState.globe && AppState.globePaths.length) {
    AppState.globe.pathsData(AppState.globePaths);
  }
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
    .globeImageUrl('//unpkg.com/three-globe/example/img/earth-day.jpg')
    .backgroundImageUrl('//unpkg.com/three-globe/example/img/night-sky.png')
    .atmosphereColor('rgba(80,140,255,0.4)')
    .atmosphereAltitude(0.15);

  AppState.globe = globe;

  globe.controls().autoRotate      = true;
  globe.controls().autoRotateSpeed = 0.35;

  // Country borders — drawn map style
  fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json')
    .then(r => r.json())
    .then(world => {
      const countries = topojson.feature(world, world.objects.countries);
      globe
        .polygonsData(countries.features)
        .polygonCapColor(() => 'rgba(0,0,0,0)')
        .polygonSideColor(() => 'rgba(0,0,0,0)')
        .polygonStrokeColor(() => 'rgba(80,60,30,0.55)')
        .polygonAltitude(0.001);
    })
    .catch(() => {});

  // City labels — tier 1 (capitals) visible from the start
  globe
    .labelsData(CITY_DATA.filter(c => c[3] === 1))
    .labelLat(d => d[0])
    .labelLng(d => d[1])
    .labelText(d => d[2])
    .labelSize(d => d[3] === 1 ? 0.55 : d[3] === 2 ? 0.38 : 0.26)
    .labelColor(d => d[3] === 1 ? 'rgba(255,255,230,0.95)' : d[3] === 2 ? 'rgba(255,255,200,0.80)' : 'rgba(220,220,190,0.65)')
    .labelDotRadius(d => d[3] === 1 ? 0.35 : 0.18)
    .labelDotOrientation(() => 'bottom')
    .labelAltitude(0.006)
    .labelResolution(2);

  // Zoom-based label filtering
  let lastLabelTier = 1;
  globe.controls().addEventListener('change', () => {
    const dist = globe.camera().position.length();
    const tier = dist > 320 ? 1 : dist > 180 ? 2 : 3;
    if (tier !== lastLabelTier) {
      lastLabelTier = tier;
      globe.labelsData(CITY_DATA.filter(c => c[3] <= tier));
    }
  });

  // Stop on manual drag; restart after 8s if nothing was selected
  container.addEventListener('mousedown', () => {
    globe.controls().autoRotate = false;
    if (globeAutoRotateTimer) clearTimeout(globeAutoRotateTimer);
    globeAutoRotateTimer = setTimeout(() => {
      if (!AppState.globeSelection) globe.controls().autoRotate = true;
    }, 8000);
  });

  // Hover tip for paths and points
  globe
    .onPathHover(d => {
      if (!globeTip) return;
      if (!d) { globeTip.textContent = 'Clique em um cabo ou ponto para selecionar'; return; }
      const from = `${d.startLat.toFixed(1)}°, ${d.startLng.toFixed(1)}°`;
      const to   = `${d.endLat.toFixed(1)}°, ${d.endLng.toFixed(1)}°`;
      globeTip.textContent = `${d.cable_name}  ·  ${from} → ${to}`;
    })
    .onPathClick(d => selectFromGlobe(d))
    .onPointClick(d => selectFromGlobe(d));

  // Load globe data
  apiFetch({ action: 'globe_data' })
    .then(payload => {
      const paths = payload.paths || [];
      const pts   = payload.points || [];
      AppState.globePaths = paths;

      globe
        // ── Flat cable paths on globe surface ──────────────────────────────
        .pathsData(paths)
        .pathPoints('points')
        .pathPointLat(p => p[0])
        .pathPointLng(p => p[1])
        .pathPointAlt(() => 0)          // altitude 0 = flush on globe surface
        .pathColor(pathColor)
        .pathStroke(0.5)
        .pathDashLength(1)              // solid line (no gaps)
        .pathDashGap(0)
        .pathDashAnimateTime(0)
        // ── Landing-point dots ─────────────────────────────────────────────
        .pointsData(pts)
        .pointLat('lat')
        .pointLng('lng')
        .pointColor(ptColor)
        .pointAltitude(0.004)
        .pointRadius(0.22)
        .pointsMerge(false)
        .pointLabel(d => {
          const loc = (d.city_name && d.country_name)
            ? `<strong>${d.city_name}</strong>, ${d.country_name}`
            : (d.city_name || `${parseFloat(d.lat).toFixed(2)}°, ${parseFloat(d.lng).toFixed(2)}°`);
          return `<div style="background:rgba(8,6,15,0.92);border:1px solid rgba(168,113,255,0.45);padding:7px 12px;border-radius:8px;font-size:13px;color:#f4efff;line-height:1.5;">
            ${loc}<br>
            <span style="color:#a871ff;font-size:12px;">${d.cable_name}</span>
            ${d.ip_sample ? `<br><span style="color:#41da8c;font-size:11px;">${d.ip_sample}</span>` : ''}
          </div>`;
        });

      // Alert badge — count unique cables with active alerts
      const alertIds  = new Set(paths.filter(p => p.has_alert).map(p => p.cable_id));
      updateAlertBadge(alertIds.size);

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

    // Map each visible row to its index in the full sorted allCables array
    rows.forEach(row => {
      const ri = AppState.allCables.findIndex(c => c.cable_id === row.dataset.cableId);
      row.classList.remove('bs-eliminated', 'bs-range', 'bs-mid', 'bs-found');
      if (ri === -1) return;
      if (step.notFound) { row.classList.add('bs-eliminated'); return; }
      if (ri < step.lo || ri > step.hi) row.classList.add('bs-eliminated');
      else if (ri === step.mid)          row.classList.add(step.found ? 'bs-found' : 'bs-mid');
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
        if (!query) {
          renderRows(AppState.allCables);
          if (resultCount) resultCount.textContent = `${AppState.allCables.length} cabos — sem filtro.`;
          return;
        }
        // Filter to cables similar to the query (substring match on id or name)
        const q = query.toLowerCase();
        const similar = AppState.allCables.filter(c =>
          String(c.cable_id).toLowerCase().includes(q) ||
          String(c.cable_name || '').toLowerCase().includes(q)
        );
        renderRows(similar);

        // Binary search runs on the full sorted array for visualization
        AppState.search.binarySteps = buildBinarySteps(query);
        applyBinaryStep(0);
        const found = AppState.search.binarySteps[AppState.search.binarySteps.length - 1].found;
        if (resultCount) {
          const n = AppState.search.binarySteps.length;
          const exact = found ? ' · exato encontrado' : ' · exato não encontrado';
          resultCount.textContent = `${similar.length} resultado(s) semelhantes · busca binária: ${n} passos${exact}.`;
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

// ─── Probe page ───────────────────────────────────────────────────────────────

// Real-time 3D probe dashboard launched from globe selection panel
function startProbeRealtime(d, basePayload) {
  const cableName = d.cable_name || d.cable_id || 'Cabo';
  const r = basePayload?.result || basePayload || {};
  const baseLat  = +(r.avg_rtt_ms ?? r.latency_ms ?? 75);
  const baseJit  = +(r.jitter_ms ?? 12);
  const baseLoss = +(r.packet_loss_pct ?? 0.4);
  const baseThru = +(r.throughput_mbps ?? 420);

  ProbeModal.show(`Tempo Real — ${esc(cableName)}`, `
    <div class="rt-dash">

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
  `);

  const box = document.getElementById('probe-modal-box');
  box?.classList.add('rt-mode');

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
    ProbeModal._stopFn = () => { running = false; };

    // ── Stop button ───────────────────────────────────────────────────────
    const stopBtn = document.getElementById('rt-stop');
    if (stopBtn) stopBtn.addEventListener('click', () => ProbeModal.hide());

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
  const cableInput  = document.getElementById('probe-cable-input');
  const cableList   = document.getElementById('probe-cable-list');
  const modeHistory = document.getElementById('probe-mode-history');
  const modeRT      = document.getElementById('probe-mode-realtime');
  const windowWrap  = document.getElementById('probe-window-wrap');
  const hoursEl     = document.getElementById('telemetry-hours');
  const btnTel      = document.getElementById('btn-telemetry');
  const errEl       = document.getElementById('probe-error');
  const resultEl    = document.getElementById('probe-result');
  const telResultEl = document.getElementById('telemetry-result');

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
    if (windowWrap) windowWrap.style.display = isHistory ? '' : 'none';
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
      const cableId = cableInput ? cableInput.value.trim() : '';
      if (!cableId) {
        showError(errEl, 'Digite o nome ou ID do cabo primeiro.');
        if (modeHistory) { modeHistory.checked = true; applyMode(); }
        return;
      }
      resetContent('');
      setLegend('Tempo real · 60 s', '—', cableId, '—');
      startProbeRealtime({ cable_id: cableId, cable_name: cableId }, {});
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
  const cablePanel   = document.getElementById('daily-cable-panel');
  const pvEpA        = document.getElementById('pv-ep-a');
  const pvEpB        = document.getElementById('pv-ep-b');
  const pvCable      = document.getElementById('pv-cable-name');
  const pvLat        = document.getElementById('pv-lat');
  const pvProto      = document.getElementById('pv-proto');
  const tableWrap    = document.getElementById('daily-table-wrap');
  const tbody        = document.getElementById('daily-tbody');
  const errEl        = document.getElementById('daily-error');

  let pollTimer      = null;
  let currentLog     = null;
  let tableRows      = [];
  let probeStartTime = null;

  function setRunningUI(running) {
    if (btnStart)  { btnStart.disabled  = running; btnStart.textContent = running ? '● Rodando…' : '▶ Iniciar Sonda'; }
    if (btnCancel) { btnCancel.style.display = running ? '' : 'none'; }
  }

  function setStats(summary, progress, live) {
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v ?? '—'; };
    const total = summary?.inserted  ?? progress?.total   ?? null;
    const tcp   = summary?.tcp       ?? live?.tcp         ?? null;
    const synth = summary?.synthetic ?? live?.synth       ?? null;
    const mtr   = summary?.mtr       ?? null;
    const anom  = summary?.anomalies ?? null;
    set('dstat-total',     progress ? `${progress.current}/${progress.total}` : total);
    set('dstat-tcp',       tcp);
    set('dstat-synth',     synth);
    set('dstat-mtr',       mtr);
    set('dstat-anomalies', anom);
  }

  function updateBadge(status) {
    if (!statusBadge) return;
    const map = { running: '● Em execução', completed: '✓ Concluído', idle: '', already_running: '● Em execução' };
    statusBadge.textContent = map[status] || '';
    statusBadge.className = 'daily-status-badge ' + (status === 'completed' ? 'done' : status === 'running' || status === 'already_running' ? 'running' : '');
  }

  function updateProgress(p) {
    if (!p) return;
    const pct = p.percent || 0;
    if (progressWrap) progressWrap.style.display = '';
    if (progressBar)  progressBar.style.width = pct + '%';
    if (progressLbl)  progressLbl.textContent = `${p.current} / ${p.total} — ${pct.toFixed(1)}%`;

    // packet animation panel
    if (cablePanel) cablePanel.style.display = '';
    if (pvCable)  pvCable.textContent  = p.cable || '—';
    if (pvLat)    pvLat.textContent    = p.latency_ms != null ? p.latency_ms + ' ms' : '—';
    if (pvProto)  pvProto.textContent  = p.proto ? p.proto.replace(/tcp-\d+pkt-ports\[([^\]]+)\]-p95=[\d.]+ms/, 'TCP [$1]') : '—';

    // endpoint labels from AppState.globePaths if available
    if (AppState.globePaths.length) {
      const seg = AppState.globePaths.find(pp => pp.cable_id === p.cable);
      if (seg) {
        const fmtCoord = (lat, lng) => `${parseFloat(lat).toFixed(1)}°, ${parseFloat(lng).toFixed(1)}°`;
        if (pvEpA) pvEpA.textContent = fmtCoord(seg.startLat, seg.startLng);
        if (pvEpB) pvEpB.textContent = fmtCoord(seg.endLat, seg.endLng);
      }
    }

    // highlight cable on globe
    if (AppState.probeActiveCable !== p.cable) {
      AppState.probeActiveCable = p.cable;
      if (Router.current === 'map') refreshGlobeProbeHighlight();
    }

    // live table (last 15 rows)
    const proto = p.proto || '—';
    const isTcp = proto.includes('tcp');
    tableRows.unshift({ index: p.current, cable: p.cable, proto, lat: p.latency_ms, isTcp });
    if (tableRows.length > 15) tableRows.pop();
    if (tableWrap) tableWrap.style.display = '';
    if (tbody) {
      tbody.innerHTML = tableRows.map(r => `<tr>
        <td>${r.index}</td>
        <td class="mono">${esc(r.cable)}</td>
        <td><span class="badge ${r.isTcp ? 'yes' : ''}">${esc(r.isTcp ? 'TCP' : 'Sintético')}</span></td>
        <td class="${r.lat > 200 ? 'danger-text' : 'ok-text'}">${r.lat} ms</td>
      </tr>`).join('');
    }
  }

  async function poll() {
    if (!currentLog) return;
    try {
      const data = await apiFetch({ action: 'daily_status', log: currentLog });
      const live = { tcp: data.live_tcp || null, synth: data.live_synth || null };
      updateBadge(data.status);
      if (data.progress) updateProgress(data.progress);
      if (data.summary)  setStats(data.summary, data.progress, live);
      else if (data.progress || live.tcp) setStats(null, data.progress, live);

      if (data.status === 'starting') {
        if (progressWrap) progressWrap.style.display = '';
        if (progressLbl) progressLbl.textContent = 'Iniciando script…';
        pollTimer = setTimeout(poll, 4000);
      } else if (data.status === 'running' || data.status === 'already_running') {
        if (!data.progress && progressLbl) progressLbl.textContent = 'Aguardando primeiro cabo…';
        pollTimer = setTimeout(poll, 3000);
      } else if (data.status === 'idle') {
        // Rollup phase: script is running but hasn't written progress lines yet
        const elapsed = probeStartTime ? (Date.now() - probeStartTime) / 1000 : 9999;
        if (elapsed < 180) {
          if (progressLbl) progressLbl.textContent = 'Calculando rollups iniciais…';
          pollTimer = setTimeout(poll, 5000);
        } else {
          // Truly idle — reset UI
          setRunningUI(false);
        }
      } else if (data.status === 'completed') {
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

  if (btnStart) {
    btnStart.addEventListener('click', async () => {
      const fullScan = document.getElementById('daily-full-scan')?.checked ? '1' : '0';
      setRunningUI(true);
      btnStart.textContent = 'Iniciando…';
      if (errEl) errEl.style.display = 'none';
      tableRows = [];
      if (tbody) tbody.innerHTML = '';
      try {
        const data = await apiFetch({ action: 'daily_start', full_scan: fullScan, mtr_cycles: fullScan === '1' ? '10' : '3' });
        if (data.status === 'error') {
          if (errEl) { errEl.textContent = data.message; errEl.style.display = ''; }
          setRunningUI(false);
          return;
        }
        currentLog     = data.log_file || null;
        probeStartTime = Date.now();
        updateBadge(data.status === 'already_running' ? 'already_running' : 'running');
        if (progressWrap) progressWrap.style.display = '';
        if (cablePanel)   cablePanel.style.display   = '';
        if (pollTimer) clearTimeout(pollTimer);
        pollTimer = setTimeout(poll, 4000);
      } catch (err) {
        if (errEl) { errEl.textContent = err.message; errEl.style.display = ''; }
        setRunningUI(false);
      }
    });
  }

  if (btnCancel) {
    btnCancel.addEventListener('click', async () => {
      btnCancel.disabled = true;
      btnCancel.textContent = 'Cancelando…';
      try {
        await apiFetch({ action: 'daily_cancel' });
      } catch { /* best-effort */ }
      if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
      currentLog     = null;
      probeStartTime = null;
      tableRows      = [];
      AppState.probeActiveCable = null;
      if (AppState.globePaths.length) refreshGlobeProbeHighlight();
      setRunningUI(false);
      updateBadge('idle');
      if (progressWrap) progressWrap.style.display = 'none';
      if (cablePanel)   cablePanel.style.display   = 'none';
      if (tableWrap)    tableWrap.style.display     = 'none';
      if (tbody) tbody.innerHTML = '';
      btnCancel.disabled = false;
    });
  }

  // Check if already running when page is opened
  document.querySelector('[data-page="daily"]')?.addEventListener('click', async () => {
    try {
      const data = await apiFetch({ action: 'daily_status' });
      const live = { tcp: data.live_tcp || null, synth: data.live_synth || null };
      if ((data.status === 'running' || data.status === 'already_running') && data.log_file) {
        currentLog     = data.log_file;
        probeStartTime = probeStartTime ?? Date.now();
        updateBadge('running');
        setRunningUI(true);
        if (progressWrap) progressWrap.style.display = '';
        if (cablePanel)   cablePanel.style.display   = '';
        if (data.progress) updateProgress(data.progress);
        if (data.summary)  setStats(data.summary, data.progress, live);
        else if (live.tcp) setStats(null, data.progress, live);
        if (!pollTimer) pollTimer = setTimeout(poll, 3000);
      } else if (data.status === 'completed') {
        updateBadge('completed');
        if (data.summary) setStats(data.summary, null, null);
        if (progressBar) progressBar.style.width = '100%';
      }
    } catch { /* silent */ }
  });
}

// ─── History page ─────────────────────────────────────────────────────────────

function initHistoryPage() {
  const dateSelect = document.getElementById('hist-date-sel');
  const filterCable= document.getElementById('hist-filter-cable');
  const filterProto= document.getElementById('hist-filter-proto');
  const btnLoad    = document.getElementById('btn-hist-load');
  const errEl      = document.getElementById('hist-error');
  const summaryEl  = document.getElementById('hist-summary');
  const tableWrap  = document.getElementById('hist-table-wrap');
  const tbody      = document.getElementById('hist-tbody');

  let allCables = [];

  function renderTable(cables) {
    if (!tbody) return;
    tbody.innerHTML = cables.map(c => {
      const isTcp = c.proto.includes('tcp');
      const latClass = c.latency_ms > 300 ? 'danger-text' : c.latency_ms > 150 ? '' : 'ok-text';
      return `<tr>
        <td>${c.index}</td>
        <td class="mono">${esc(c.cable_id)}</td>
        <td style="font-size:11px;max-width:160px;overflow:hidden;text-overflow:ellipsis;">${esc(c.proto)}</td>
        <td class="${latClass}">${c.latency_ms} ms</td>
        <td><span class="badge ${isTcp ? 'yes' : ''}">${isTcp ? 'TCP' : 'Sintético'}</span></td>
      </tr>`;
    }).join('');
    if (tableWrap) tableWrap.style.display = cables.length ? '' : 'none';
  }

  function applyFilters() {
    const cq = (filterCable?.value || '').toLowerCase().trim();
    const pq = filterProto?.value || '';
    let filtered = allCables;
    if (cq) filtered = filtered.filter(c => c.cable_id.toLowerCase().includes(cq));
    if (pq === 'tcp')       filtered = filtered.filter(c =>  c.proto.includes('tcp'));
    if (pq === 'heuristic') filtered = filtered.filter(c => !c.proto.includes('tcp'));
    renderTable(filtered);
  }

  function setSummary(s) {
    if (!s || !summaryEl) return;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v ?? '—'; };
    set('h-total',     s.inserted);
    set('h-tcp',       s.tcp);
    set('h-synth',     s.synthetic);
    set('h-anomalies', s.anomalies);
    if (s.completed_at) {
      try {
        const d = new Date(s.completed_at);
        set('h-completed', d.toLocaleString('pt-BR', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' }));
      } catch { set('h-completed', s.completed_at.slice(0,16)); }
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
      if (dates.length === 0) {
        dateSelect.innerHTML = '<option value="">Nenhum histórico encontrado</option>';
        if (errEl) { errEl.textContent = 'Nenhuma execução encontrada em data/local/historico/. Execute a Sonda Diária primeiro.'; errEl.style.display = ''; }
        return;
      }
      dateSelect.innerHTML = '<option value="">— Selecione uma data —</option>'
        + dates.map(d => `<option value="${esc(d.date)}">${esc(d.date)} (${d.run_count} execução${d.run_count > 1 ? 'ões' : ''})</option>`).join('');
    } catch (err) {
      if (errEl) { errEl.textContent = 'Erro ao carregar datas: ' + err.message; errEl.style.display = ''; }
    }
  }

  // Carrega datas ao abrir a página (nav click)
  document.querySelector('[data-page="history"]')?.addEventListener('click', () => {
    if (dateSelect && dateSelect.options.length <= 1) loadHistoryDates();
  });

  if (filterCable) filterCable.addEventListener('input', applyFilters);
  if (filterProto) filterProto.addEventListener('change', applyFilters);

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
