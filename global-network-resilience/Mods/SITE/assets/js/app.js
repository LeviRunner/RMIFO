const state = {
    mode: 'cable_id',
    allCables: [],
    filteredRows: [],
    binarySteps: [],
    binaryIndex: 0,
    binaryTimer: null,
    selectedCable: null,
    countries: [],
};

const API_BASE = 'api.php';

const modes = {
    cable_id: { label: 'ID exato do cabo', placeholder: 'ex: 2africa', type: 'text', api: 'all' },
    name: { label: 'Nome do cabo', placeholder: 'ex: Africa', type: 'text', api: 'name' },
    owners: { label: 'Owner', placeholder: 'ex: AT&T', type: 'text', api: 'owners' },
    year: { label: 'Ano RFS', placeholder: 'ex: 2024', type: 'number', api: 'year' },
    operational: { label: 'Status operacional', placeholder: '', type: 'bool', api: 'operational' },
    landing: { label: 'Quantidade de endpoints', placeholder: 'ex: 4', type: 'number', api: 'landing' },
    suppliers: { label: 'Fornecedor', placeholder: 'ex: ASN', type: 'text', api: 'suppliers' },
    endpoint: { label: 'Nome/cidade do endpoint', placeholder: 'ex: Marseille', type: 'text', api: 'endpoint' },
    country: { label: 'Pais do endpoint', placeholder: 'ex: Brazil ou BR', type: 'text', api: 'country' },
};

const dom = {
    modeList: document.getElementById('mode-list'),
    queryLabel: document.getElementById('query-label'),
    queryInput: document.getElementById('query-input'),
    boolInput: document.getElementById('bool-input'),
    searchBtn: document.getElementById('search-btn'),
    clearBtn: document.getElementById('clear-btn'),
    binaryHint: document.getElementById('binary-hint'),
    resultCount: document.getElementById('result-count'),
    searchError: document.getElementById('search-error'),
    searchEmpty: document.getElementById('search-empty'),
    tableWrap: document.getElementById('search-table-wrap'),
    searchHead: document.getElementById('search-head'),
    searchBody: document.getElementById('search-body'),
    tabs: Array.from(document.querySelectorAll('.tab-btn')),
    panels: {
        search: document.getElementById('tab-search'),
        detail: document.getElementById('tab-detail'),
        'top-routes': document.getElementById('tab-top-routes'),
        'critical-cuts': document.getElementById('tab-critical-cuts'),
        'endpoint-risk': document.getElementById('tab-endpoint-risk'),
        telemetry: document.getElementById('tab-telemetry'),
    },
    binaryBox: document.getElementById('binary-box'),
    binaryStep: document.getElementById('binary-step'),
    binaryProgress: document.getElementById('binary-progress'),
    binaryLog: document.getElementById('binary-log'),
    binaryPlay: document.getElementById('binary-play'),
    binaryPrev: document.getElementById('binary-prev'),
    binaryNext: document.getElementById('binary-next'),
    binaryReset: document.getElementById('binary-reset'),
    detailPicker: document.getElementById('detail-picker'),
    detailDatalist: document.getElementById('cable-list'),
    detailLoad: document.getElementById('detail-load'),
    detailError: document.getElementById('detail-error'),
    detailEmpty: document.getElementById('detail-empty'),
    detailContent: document.getElementById('detail-content'),
    detailSummary: document.getElementById('detail-summary'),
    endpointList: document.getElementById('endpoint-list'),
    routeError: document.getElementById('route-error'),
    routeResult: document.getElementById('route-result'),
    cutsError: document.getElementById('cuts-error'),
    cutsResult: document.getElementById('cuts-result'),
    riskError: document.getElementById('risk-error'),
    riskResult: document.getElementById('risk-result'),
    telemetryError: document.getElementById('telemetry-error'),
    telemetryResult: document.getElementById('telemetry-result'),
    routeCountryA: document.getElementById('route-country-a'),
    routeCountryB: document.getElementById('route-country-b'),
    cutCountryA: document.getElementById('cut-country-a'),
    cutCountryB: document.getElementById('cut-country-b'),
    riskCountry: document.getElementById('risk-country'),
    telemetryCable: document.getElementById('telemetry-cable'),
    btnTopRoutes: document.getElementById('btn-top-routes'),
    btnCriticalCuts: document.getElementById('btn-critical-cuts'),
    btnEndpointRisk: document.getElementById('btn-endpoint-risk'),
    btnTelemetry: document.getElementById('btn-telemetry'),
};

function esc(value) {
    if (value === null || value === undefined || value === '') {
        return '<span class="muted">-</span>';
    }
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function fmtBool(value) {
    if (value === true || value === 'true' || value === 't') {
        return '<span class="badge yes">Sim</span>';
    }
    if (value === false || value === 'false' || value === 'f') {
        return '<span class="badge no">Nao</span>';
    }
    return '<span class="muted">-</span>';
}

function showError(node, message) {
    node.textContent = message;
    node.classList.add('show');
}

function clearError(node) {
    node.textContent = '';
    node.classList.remove('show');
}

async function api(mode, q = '') {
    const response = await fetch(`${API_BASE}?api=1&mode=${encodeURIComponent(mode)}&q=${encodeURIComponent(q)}`);
    const payload = await response.json();
    if (payload.error) {
        throw new Error(payload.error);
    }
    return payload;
}

async function apiWithParams(mode, params = {}) {
    const searchParams = new URLSearchParams({ api: '1', mode });
    Object.entries(params).forEach(([key, value]) => {
        if (value !== null && value !== undefined) {
            searchParams.set(key, String(value));
        }
    });
    const response = await fetch(`${API_BASE}?${searchParams.toString()}`);
    const payload = await response.json();
    if (payload.error) {
        throw new Error(payload.error);
    }
    return payload;
}

function switchTab(tab) {
    dom.tabs.forEach((button) => button.classList.toggle('active', button.dataset.tab === tab));
    Object.entries(dom.panels).forEach(([key, panel]) => panel.classList.toggle('active', key === tab));
}

function switchMode(mode) {
    state.mode = mode;
    const cfg = modes[mode];
    dom.queryLabel.textContent = cfg.label;
    dom.queryInput.placeholder = cfg.placeholder;
    dom.queryInput.type = cfg.type === 'number' ? 'number' : 'text';
    dom.queryInput.style.display = cfg.type === 'bool' ? 'none' : '';
    dom.boolInput.style.display = cfg.type === 'bool' ? '' : 'none';
    dom.binaryHint.style.display = mode === 'cable_id' ? '' : 'none';
    dom.modeList.querySelectorAll('.mode-btn').forEach((button) => {
        button.classList.toggle('active', button.dataset.mode === mode);
    });
    resetSearchSurface();
}

function resetBinary() {
    if (state.binaryTimer) {
        clearInterval(state.binaryTimer);
        state.binaryTimer = null;
    }
    dom.binaryPlay.textContent = 'Reproduzir';
    dom.binaryBox.classList.remove('show');
    dom.binaryStep.innerHTML = '-';
    dom.binaryLog.innerHTML = '';
    dom.binaryProgress.style.width = '0%';
    state.binarySteps = [];
    state.binaryIndex = 0;
}

function resetSearchSurface() {
    clearError(dom.searchError);
    resetBinary();
    dom.searchEmpty.style.display = '';
    dom.tableWrap.style.display = 'none';
    dom.resultCount.textContent = 'Selecione uma busca.';
    dom.searchHead.innerHTML = '';
    dom.searchBody.innerHTML = '';
}

function renderRows(rows) {
    const headers = [
        'acao', '#', 'cable_id', 'cable_name', 'owners', 'length_km', 'rfs_year', 'is_operational', 'landing_points_count', 'suppliers'
    ];
    dom.searchHead.innerHTML = `<tr>${headers.map((header) => `<th>${esc(header)}</th>`).join('')}</tr>`;
    dom.searchBody.innerHTML = rows.map((row, index) => `
        <tr data-cable-id="${esc(row.cable_id)}">
            <td><button class="detail-action" data-open-detail="${esc(row.cable_id)}">Detalhes</button></td>
            <td class="mono">${index}</td>
            <td class="mono">${esc(row.cable_id)}</td>
            <td>${esc(row.cable_name)}</td>
            <td>${esc(row.owners)}</td>
            <td>${esc(row.length_km)}</td>
            <td>${esc(row.rfs_year)}</td>
            <td>${fmtBool(row.is_operational)}</td>
            <td>${esc(row.landing_points_count)}</td>
            <td>${esc(row.suppliers)}</td>
        </tr>
    `).join('');
    dom.searchEmpty.style.display = rows.length ? 'none' : '';
    dom.tableWrap.style.display = rows.length ? '' : 'none';
}

function buildBinarySteps(target) {
    const rows = state.allCables;
    const normalizedTarget = target.toLowerCase();
    let lo = 0;
    let hi = rows.length - 1;
    const steps = [];
    while (lo <= hi) {
        const mid = Math.floor((lo + hi) / 2);
        const current = String(rows[mid].cable_id).toLowerCase();
        const cmp = current.localeCompare(normalizedTarget);
        steps.push({ lo, hi, mid, current, cmp, found: cmp === 0 });
        if (cmp === 0) {
            break;
        }
        if (cmp < 0) {
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    if (!steps.length || !steps[steps.length - 1].found) {
        steps.push({ lo, hi, mid: -1, current: null, cmp: null, found: false, notFound: true });
    }
    return steps;
}

function describeBinaryStep(step, index, total) {
    if (step.notFound) {
        return `Passo ${index + 1}/${total}: intervalo vazio (lo=${step.lo}, hi=${step.hi}). Cabo nao encontrado.`;
    }
    if (step.found) {
        return `Passo ${index + 1}/${total}: midpoint [${step.mid}] = ${step.current}. Cabo encontrado.`;
    }
    if (step.cmp < 0) {
        return `Passo ${index + 1}/${total}: midpoint [${step.mid}] = ${step.current}. Valor menor que o alvo, seguir para a direita.`;
    }
    return `Passo ${index + 1}/${total}: midpoint [${step.mid}] = ${step.current}. Valor maior que o alvo, seguir para a esquerda.`;
}

function applyBinaryStep(index) {
    state.binaryIndex = index;
    const step = state.binarySteps[index];
    const total = state.binarySteps.length;
    const rows = Array.from(dom.searchBody.querySelectorAll('tr'));
    rows.forEach((row, rowIndex) => {
        row.classList.remove('bs-eliminated', 'bs-range', 'bs-mid', 'bs-found');
        if (step.notFound) {
            row.classList.add('bs-eliminated');
            return;
        }
        if (rowIndex < step.lo || rowIndex > step.hi) {
            row.classList.add('bs-eliminated');
        } else if (rowIndex === step.mid) {
            row.classList.add(step.found ? 'bs-found' : 'bs-mid');
        } else {
            row.classList.add('bs-range');
        }
    });
    dom.binaryStep.textContent = describeBinaryStep(step, index, total);
    dom.binaryProgress.style.width = `${((index + 1) / total) * 100}%`;
    dom.binaryLog.innerHTML = state.binarySteps
        .slice(0, index + 1)
        .map((entry, entryIndex) => `<div>${esc(describeBinaryStep(entry, entryIndex, total))}</div>`)
        .join('');
}

function toggleBinaryPlayback() {
    if (state.binaryTimer) {
        clearInterval(state.binaryTimer);
        state.binaryTimer = null;
        dom.binaryPlay.textContent = 'Reproduzir';
        return;
    }
    dom.binaryPlay.textContent = 'Pausar';
    state.binaryTimer = setInterval(() => {
        if (state.binaryIndex < state.binarySteps.length - 1) {
            applyBinaryStep(state.binaryIndex + 1);
        } else {
            clearInterval(state.binaryTimer);
            state.binaryTimer = null;
            dom.binaryPlay.textContent = 'Reproduzir';
        }
    }, 850);
}

async function performSearch() {
    clearError(dom.searchError);
    resetBinary();
    const cfg = modes[state.mode];
    const query = cfg.type === 'bool' ? dom.boolInput.value : dom.queryInput.value.trim();

    try {
        if (state.mode === 'cable_id') {
            if (!state.allCables.length) {
                const payload = await api('all');
                state.allCables = payload.data;
                populateDatalist();
            }
            renderRows(state.allCables);
            state.filteredRows = state.allCables;
            if (!query) {
                dom.resultCount.textContent = `${state.allCables.length} cabos carregados.`;
                return;
            }
            state.binarySteps = buildBinarySteps(query);
            dom.binaryBox.classList.add('show');
            applyBinaryStep(0);
            const found = state.binarySteps[state.binarySteps.length - 1].found;
            dom.resultCount.textContent = found
                ? `1 resultado. Busca binaria em ${state.binarySteps.length} passos.`
                : `0 resultados. Busca binaria encerrada em ${state.binarySteps.length} passos.`;
            return;
        }

        if (!query) {
            throw new Error('Digite um valor para buscar.');
        }

        const payload = await api(cfg.api, query);
        state.filteredRows = payload.data;
        renderRows(payload.data);
        dom.resultCount.textContent = `${payload.total} resultado(s).`;
    } catch (error) {
        showError(dom.searchError, error.message);
    }
}

function resetDetails() {
    clearError(dom.detailError);
    dom.detailEmpty.style.display = '';
    dom.detailContent.style.display = 'none';
    dom.detailSummary.innerHTML = '';
    dom.endpointList.innerHTML = '';
}

function renderSummary(cable) {
    const summaryItems = [
        ['cable_id', cable.cable_id],
        ['nome', cable.cable_name],
        ['owners', cable.owners],
        ['fornecedores', cable.suppliers],
        ['comprimento bruto', cable.raw_length || cable.length_km],
        ['ano RFS', cable.raw_rfs || cable.rfs_year],
        ['operacional', cable.is_operational === null ? null : (cable.is_operational ? 'Sim' : 'Nao')],
        ['planejado', cable.is_planned === null ? null : (cable.is_planned ? 'Sim' : 'Nao')],
        ['endpoints', cable.landing_points_count],
        ['url publica', cable.public_url],
        ['fonte', cable.source_url],
        ['notas', cable.notes],
    ];
    dom.detailSummary.innerHTML = summaryItems.map(([label, value]) => `
        <div class="summary-card">
            <div class="k">${esc(label)}</div>
            <div class="v">${esc(value)}</div>
        </div>
    `).join('');
}

function renderEndpoints(endpoints) {
    dom.endpointList.innerHTML = endpoints.map((endpoint) => {
        const samples = endpoint.ip_samples || [];
        const sampleRows = samples.length ? samples.map((sample) => `
            <tr>
                <td>${esc(sample.sample_order)}</td>
                <td class="mono">${esc(sample.network || `${sample.ip_start} - ${sample.ip_end}`)}</td>
                <td>${esc(sample.city)}</td>
                <td>${esc(sample.region)}</td>
                <td>${esc(sample.country_name)} ${sample.country_code ? `(${esc(sample.country_code)})` : ''}</td>
                <td>${esc(sample.latitude)}, ${esc(sample.longitude)}</td>
            </tr>
        `).join('') : '<tr><td colspan="6" class="muted">Sem amostras de IP publicas para este endpoint.</td></tr>';

        return `
            <div class="endpoint-card">
                <div class="endpoint-head">
                    <div>
                        <h4 class="endpoint-title">${esc(endpoint.endpoint_order)}. ${esc(endpoint.endpoint_name)}</h4>
                        <div class="muted mono">${esc(endpoint.endpoint_id)}</div>
                    </div>
                    <div>${endpoint.is_tbd ? '<span class="badge no">TBD</span>' : '<span class="badge yes">ativo no catalogo</span>'}</div>
                </div>

                <div class="endpoint-meta">
                    <div><div class="k">cidade</div><div>${esc(endpoint.city_name)}</div></div>
                    <div><div class="k">pais</div><div>${esc(endpoint.country_name)} ${endpoint.country_code ? `(${esc(endpoint.country_code)})` : ''}</div></div>
                    <div><div class="k">latitude</div><div>${esc(endpoint.latitude)}</div></div>
                    <div><div class="k">longitude</div><div>${esc(endpoint.longitude)}</div></div>
                    <div><div class="k">fonte geo</div><div>${esc(endpoint.geo_source)}</div></div>
                    <div><div class="k">amostras de IP</div><div>${esc(samples.length)}</div></div>
                </div>

                <div class="subhead">IPs publicos associados por cidade/pais</div>
                <div class="table-wrap">
                    <table class="ip-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>network / faixa</th>
                                <th>cidade</th>
                                <th>regiao</th>
                                <th>pais</th>
                                <th>coordenadas</th>
                            </tr>
                        </thead>
                        <tbody>${sampleRows}</tbody>
                    </table>
                </div>
            </div>
        `;
    }).join('');
}

async function loadDetails(cableId) {
    if (!cableId) {
        showError(dom.detailError, 'Selecione um cabo valido.');
        return;
    }
    clearError(dom.detailError);
    try {
        const payload = await api('details', cableId);
        state.selectedCable = cableId;
        dom.detailPicker.value = cableId;
        renderSummary(payload.cable);
        renderEndpoints(payload.endpoints);
        dom.detailEmpty.style.display = 'none';
        dom.detailContent.style.display = '';
        switchTab('detail');
    } catch (error) {
        showError(dom.detailError, error.message);
    }
}

function populateDatalist() {
    dom.detailDatalist.innerHTML = state.allCables.map((row) => (
        `<option value="${esc(row.cable_id)}">${esc(row.cable_name)}</option>`
    )).join('');
}

function setPanelResult(node, html) {
    node.innerHTML = html;
}

function populateCountrySelects(countries) {
    const options = countries.map((country) => `<option value="${esc(country)}">${esc(country)}</option>`).join('');
    [dom.routeCountryA, dom.routeCountryB, dom.cutCountryA, dom.cutCountryB, dom.riskCountry].forEach((select) => {
        select.innerHTML = options;
    });
    if (countries.length > 1) {
        dom.routeCountryA.value = countries[0];
        dom.cutCountryA.value = countries[0];
        dom.routeCountryB.value = countries[1];
        dom.cutCountryB.value = countries[1];
        dom.riskCountry.value = countries[0];
    }
}

function renderTopRoutes(payload) {
    if (!payload.routes.length) {
        setPanelResult(dom.routeResult, '<div class="muted">Nenhuma rota encontrada para o par de paises selecionado.</div>');
        return;
    }

    const html = payload.routes.map((route, idx) => {
        const cables = route.cables.map((cable) => `${esc(cable.cable_name)} <span class="muted">(${esc(cable.from)} → ${esc(cable.to)})</span>`).join('<br>');
        return `
            <div class="route-card">
                <strong>#${idx + 1}</strong> score ${esc(route.score)} · hops ${esc(route.hops)} · ${esc(route.distance_km)} km<br>
                <div class="muted">${esc(route.countries.join(' → '))}</div>
                <div style="margin-top:8px">${cables}</div>
            </div>
        `;
    }).join('');

    setPanelResult(dom.routeResult, `<h3>Top 3 rotas (${esc(payload.from)} → ${esc(payload.to)})</h3>${html}`);
}

function renderCriticalCuts(payload) {
    if (!payload.critical_cables.length) {
        setPanelResult(dom.cutsResult, '<div class="muted">Nao foram encontrados cabos que isolem completamente o par de paises dentro das rotas analisadas.</div>');
        return;
    }

    const rows = payload.critical_cables.map((cable) => `
        <tr>
            <td class="mono">${esc(cable.cable_id)}</td>
            <td>${esc(cable.cable_name)}</td>
            <td>${esc(cable.routes_hit)}</td>
            <td>${esc(cable.impact)}</td>
        </tr>
    `).join('');

    setPanelResult(dom.cutsResult, `
        <h3>Cabos cujo corte pode isolar ${esc(payload.from)} ↔ ${esc(payload.to)}</h3>
        <div class="table-wrap">
            <table>
                <thead><tr><th>cable_id</th><th>cabo</th><th>rotas impactadas</th><th>impacto</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `);
}

function renderEndpointRisk(payload) {
    if (!payload.endpoints.length) {
        setPanelResult(dom.riskResult, '<div class="muted">Nao ha endpoints suficientes para calcular risco no pais selecionado.</div>');
        return;
    }

    const rows = payload.endpoints.map((endpoint) => `
        <tr>
            <td class="mono">${esc(endpoint.endpoint_id)}</td>
            <td>${esc(endpoint.endpoint_name)}</td>
            <td>${esc(endpoint.city_name)}</td>
            <td>${esc(endpoint.cable_degree)}</td>
            <td>${esc(endpoint.country_reach)}</td>
            <td>${esc(endpoint.risk_score)}</td>
            <td>${esc(endpoint.packet_loss_risk_pct)}%</td>
            <td>${esc(endpoint.congestion_level)}</td>
        </tr>
    `).join('');

    setPanelResult(dom.riskResult, `
        <h3>Endpoints com risco em ${esc(payload.country)}</h3>
        <div class="table-wrap">
            <table>
                <thead><tr><th>endpoint_id</th><th>endpoint</th><th>cidade</th><th>conexoes</th><th>alcance</th><th>risco</th><th>perda pacotes</th><th>nivel</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `);
}

function animateGauge(gaugeId, valueNodeId, target, maxValue, unit) {
    const circle = document.getElementById(gaugeId);
    const valueNode = document.getElementById(valueNodeId);
    const radius = 48;
    const circumference = 2 * Math.PI * radius;
    const clampedTarget = Math.min(target, maxValue);
    const decimalPlaces = unit === 'pps' || unit === 'maq' ? 0 : 1;
    const start = performance.now();

    function tick(now) {
        const progress = Math.min(1, (now - start) / 1300);
        const current = clampedTarget * progress;
        const ratio = current / maxValue;
        const offset = circumference * (1 - ratio);
        circle.style.strokeDasharray = `${circumference}`;
        circle.style.strokeDashoffset = `${offset}`;
        valueNode.textContent = `${current.toFixed(decimalPlaces)} ${unit}`;
        if (progress < 1) {
            requestAnimationFrame(tick);
        }
    }

    requestAnimationFrame(tick);
}

function buildLinePoints(values, width, height, padding) {
    if (!values.length) {
        return '';
    }
    const minVal = Math.min(...values);
    const maxVal = Math.max(...values);
    const span = Math.max(0.001, maxVal - minVal);
    return values.map((value, idx) => {
        const x = padding + (idx * (width - (padding * 2)) / Math.max(1, values.length - 1));
        const y = height - padding - (((value - minVal) / span) * (height - (padding * 2)));
        return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');
}

function buildBandPolygon(values, halfWidth, width, height, padding) {
    if (!values.length || halfWidth <= 0) {
        return '';
    }
    const upper = values.map((v) => v + halfWidth);
    const lower = values.map((v) => Math.max(0, v - halfWidth));
    const combined = upper.concat(lower.slice().reverse());
    const minVal = Math.min(...combined);
    const maxVal = Math.max(...combined);
    const span = Math.max(0.001, maxVal - minVal);

    const upperPoints = upper.map((value, idx) => {
        const x = padding + (idx * (width - (padding * 2)) / Math.max(1, upper.length - 1));
        const y = height - padding - (((value - minVal) / span) * (height - (padding * 2)));
        return `${x.toFixed(2)},${y.toFixed(2)}`;
    });

    const lowerPoints = lower.map((value, idx) => {
        const x = padding + (idx * (width - (padding * 2)) / Math.max(1, lower.length - 1));
        const y = height - padding - (((value - minVal) / span) * (height - (padding * 2)));
        return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).reverse();

    return upperPoints.concat(lowerPoints).join(' ');
}

function renderTelemetryChart(history, ciLatency) {
    const valid = history
        .map((row) => Number(row.latency_ms))
        .filter((value) => Number.isFinite(value));

    if (valid.length < 2) {
        return '<div class="muted">Sem amostras suficientes para gráfico temporal.</div>';
    }

    const width = 920;
    const height = 220;
    const padding = 26;
    const linePoints = buildLinePoints(valid, width, height, padding);

    let halfWidth = 0;
    if (Array.isArray(ciLatency) && ciLatency.length === 2) {
        halfWidth = Math.abs((Number(ciLatency[1]) - Number(ciLatency[0])) / 2);
    }
    const bandPoints = buildBandPolygon(valid, halfWidth, width, height, padding);

    return `
        <div class="telemetry-chart-wrap">
            <svg class="telemetry-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
                <line class="telemetry-axis" x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}"></line>
                <line class="telemetry-axis" x1="${padding}" y1="${padding}" x2="${padding}" y2="${height - padding}"></line>
                ${bandPoints ? `<polygon class="telemetry-band" points="${bandPoints}"></polygon>` : ''}
                <polyline class="telemetry-line" points="${linePoints}"></polyline>
            </svg>
            <div class="telemetry-legend">Latência ao longo da janela de análise${bandPoints ? ' com banda de confiança (95%)' : ''}.</div>
        </div>
    `;
}

function renderTelemetry(payload) {
    const cable = payload.details.cable;
    const telemetry = payload.telemetry;
    const latencyP = telemetry.percentiles?.latency_ms || {};
    const throughputP = telemetry.percentiles?.throughput_mbps || {};
    const packetLossP = telemetry.percentiles?.packet_loss_pct || {};
    const history = Array.isArray(telemetry.history) ? telemetry.history : [];
    const recentRows = history.slice(-10).reverse().map((row) => `
        <tr>
            <td>${esc(row.measured_at || '-')}</td>
            <td>${esc(row.latency_ms)}</td>
            <td>${esc(row.jitter_ms)}</td>
            <td>${esc(row.packet_loss_pct)}</td>
            <td>${esc(row.throughput_mbps)}</td>
            <td>${esc(row.availability_pct)}</td>
            <td>${esc(row.confidence_score)}</td>
            <td>${esc(row.source)}</td>
        </tr>
    `).join('');

    const ciLatency = telemetry.confidence_interval_latency_ms;
    const ciText = Array.isArray(ciLatency) && ciLatency.length === 2
        ? `${esc(ciLatency[0])} ms a ${esc(ciLatency[1])} ms`
        : '<span class="muted">indisponivel</span>';
    const chartHtml = renderTelemetryChart(history, ciLatency);

    setPanelResult(dom.telemetryResult, `
        <h3>Telemetria avançada · ${esc(cable.cable_name)} (${esc(cable.cable_id)})</h3>
        <div class="detail-summary" style="margin-bottom:12px;">
            <div class="summary-card"><div class="k">janela</div><div class="v">${esc(telemetry.window_start)}<br>${esc(telemetry.window_end)}</div></div>
            <div class="summary-card"><div class="k">amostras</div><div class="v">${esc(telemetry.sample_size)}</div></div>
            <div class="summary-card"><div class="k">qualidade dos dados</div><div class="v">${esc(telemetry.data_quality_score)} / 100</div></div>
            <div class="summary-card"><div class="k">origem / método</div><div class="v">${esc(telemetry.source)} · ${esc(telemetry.method)}</div></div>
            <div class="summary-card"><div class="k">modelo</div><div class="v">${esc(telemetry.telemetry_model_version)}</div></div>
            <div class="summary-card"><div class="k">estimado</div><div class="v">${telemetry.estimated ? 'sim' : 'nao'}${telemetry.fallback_used ? ' (fallback)' : ''}</div></div>
        </div>

        ${chartHtml}

        <div class="telemetry-grid">
            <div class="gauge">
                <svg viewBox="0 0 120 120">
                    <circle cx="60" cy="60" r="48" fill="none" stroke="#30363d" stroke-width="10"></circle>
                    <circle id="gauge-latency" cx="60" cy="60" r="48" fill="none" stroke="#58a6ff" stroke-width="10" transform="rotate(-90 60 60)"></circle>
                </svg>
                <div class="value" id="value-latency">0 ms</div>
                <div class="label">latencia estimada</div>
            </div>
            <div class="gauge">
                <svg viewBox="0 0 120 120">
                    <circle cx="60" cy="60" r="48" fill="none" stroke="#30363d" stroke-width="10"></circle>
                    <circle id="gauge-pps" cx="60" cy="60" r="48" fill="none" stroke="#3fb950" stroke-width="10" transform="rotate(-90 60 60)"></circle>
                </svg>
                <div class="value" id="value-pps">0 pps</div>
                <div class="label">pacotes por segundo</div>
            </div>
            <div class="gauge">
                <svg viewBox="0 0 120 120">
                    <circle cx="60" cy="60" r="48" fill="none" stroke="#30363d" stroke-width="10"></circle>
                    <circle id="gauge-machines" cx="60" cy="60" r="48" fill="none" stroke="#f2cc60" stroke-width="10" transform="rotate(-90 60 60)"></circle>
                </svg>
                <div class="value" id="value-machines">0 maq</div>
                <div class="label">maquinas conectadas</div>
            </div>
        </div>
        <div style="margin-top:10px" class="muted">
            trafego atual: ${esc(telemetry.traffic_mbps)} Mbps · endpoints: ${esc(cable.landing_points_count)} · perda de pacote: ${esc(telemetry.packet_loss_pct)}%
        </div>

        <div class="subhead">Percentis para analise</div>
        <div class="table-wrap">
            <table>
                <thead><tr><th>metrica</th><th>p50</th><th>p95</th><th>p99</th></tr></thead>
                <tbody>
                    <tr><td>latencia (ms)</td><td>${esc(latencyP.p50)}</td><td>${esc(latencyP.p95)}</td><td>${esc(latencyP.p99)}</td></tr>
                    <tr><td>throughput (Mbps)</td><td>${esc(throughputP.p50)}</td><td>${esc(throughputP.p95)}</td><td>${esc(throughputP.p99)}</td></tr>
                    <tr><td>perda pacote (%)</td><td>${esc(packetLossP.p50)}</td><td>${esc(packetLossP.p95)}</td><td>${esc(packetLossP.p99)}</td></tr>
                </tbody>
            </table>
        </div>

        <div class="subhead">Confianca estatistica</div>
        <div class="hint" style="margin-top:0;">
            Intervalo de confianca (95%) da latencia media: ${ciText}
        </div>

        <div class="subhead">Historico recente</div>
        <div class="table-wrap">
            <table>
                <thead><tr><th>timestamp</th><th>latencia</th><th>jitter</th><th>perda %</th><th>throughput</th><th>disponibilidade %</th><th>confianca</th><th>fonte</th></tr></thead>
                <tbody>${recentRows || '<tr><td colspan="8" class="muted">Sem histórico disponível.</td></tr>'}</tbody>
            </table>
        </div>

        <div class="subhead">Metodologia</div>
        <div class="hint" style="margin-top:0;">
            Esta resposta já inclui estrutura para pesquisa: janela temporal, tamanho de amostra, percentis p50/p95/p99, intervalo de confiança e score de qualidade.
        </div>
    `);

    animateGauge('gauge-latency', 'value-latency', Number(telemetry.latency_ms), 250, 'ms');
    animateGauge('gauge-pps', 'value-pps', Number(telemetry.packets_per_second), 3000000, 'pps');
    animateGauge('gauge-machines', 'value-machines', Number(telemetry.connected_machines_estimate), 1200000, 'maq');
}

async function runPanelFunction(errorNode, targetTab, handler) {
    clearError(errorNode);
    try {
        await handler();
        switchTab(targetTab);
    } catch (error) {
        showError(errorNode, error.message);
    }
}

dom.modeList.addEventListener('click', (event) => {
    const button = event.target.closest('.mode-btn');
    if (!button) {
        return;
    }
    switchMode(button.dataset.mode);
});

dom.tabs.forEach((button) => {
    button.addEventListener('click', () => switchTab(button.dataset.tab));
});

dom.searchBtn.addEventListener('click', performSearch);
dom.clearBtn.addEventListener('click', () => {
    dom.queryInput.value = '';
    dom.boolInput.value = 'true';
    resetSearchSurface();
});
dom.queryInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
        performSearch();
    }
});
dom.detailLoad.addEventListener('click', () => loadDetails(dom.detailPicker.value.trim()));
dom.detailPicker.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
        loadDetails(dom.detailPicker.value.trim());
    }
});
dom.searchBody.addEventListener('click', (event) => {
    const button = event.target.closest('[data-open-detail]');
    if (!button) {
        return;
    }
    loadDetails(button.dataset.openDetail);
});

dom.binaryPlay.addEventListener('click', toggleBinaryPlayback);
dom.binaryPrev.addEventListener('click', () => {
    if (state.binaryIndex > 0) {
        if (state.binaryTimer) {
            toggleBinaryPlayback();
        }
        applyBinaryStep(state.binaryIndex - 1);
    }
});
dom.binaryNext.addEventListener('click', () => {
    if (state.binaryIndex < state.binarySteps.length - 1) {
        if (state.binaryTimer) {
            toggleBinaryPlayback();
        }
        applyBinaryStep(state.binaryIndex + 1);
    }
});
dom.binaryReset.addEventListener('click', () => {
    if (state.binarySteps.length) {
        if (state.binaryTimer) {
            toggleBinaryPlayback();
        }
        applyBinaryStep(0);
    }
});

dom.btnTopRoutes.addEventListener('click', () => runPanelFunction(dom.routeError, 'top-routes', async () => {
    const payload = await apiWithParams('top_routes', {
        countryA: dom.routeCountryA.value,
        countryB: dom.routeCountryB.value,
    });
    renderTopRoutes(payload);
}));

dom.btnCriticalCuts.addEventListener('click', () => runPanelFunction(dom.cutsError, 'critical-cuts', async () => {
    const payload = await apiWithParams('critical_cables', {
        countryA: dom.cutCountryA.value,
        countryB: dom.cutCountryB.value,
    });
    renderCriticalCuts(payload);
}));

dom.btnEndpointRisk.addEventListener('click', () => runPanelFunction(dom.riskError, 'endpoint-risk', async () => {
    const payload = await api('endpoint_risk', dom.riskCountry.value);
    renderEndpointRisk(payload);
}));

dom.btnTelemetry.addEventListener('click', () => runPanelFunction(dom.telemetryError, 'telemetry', async () => {
    const cableId = dom.telemetryCable.value.trim();
    if (!cableId) {
        throw new Error('Selecione um cabo para a telemetria.');
    }
    const payload = await api('telemetry', cableId);
    renderTelemetry(payload);
}));

(async function init() {
    try {
        const [payload, countriesPayload] = await Promise.all([
            api('all'),
            api('countries'),
        ]);
        state.allCables = payload.data;
        state.countries = countriesPayload.data;
        populateDatalist();
        populateCountrySelects(state.countries);
        dom.resultCount.textContent = `${payload.total} cabos carregados e prontos para busca.`;
    } catch (error) {
        showError(dom.searchError, `Falha ao carregar o catalogo: ${error.message}`);
    }
})();
