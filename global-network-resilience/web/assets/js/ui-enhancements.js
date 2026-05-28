/**
 * RMIFO — UI Enhancements
 * Wires into real DOM IDs. No class-pattern heuristics.
 *
 * Features:
 *  1. Arc progress SVG (daily page)
 *  2. Live scatter chart — latency per cable (daily page, Chart.js)
 *  3. History heatmap — horizontal bars colored by RTT (history page)
 *  4. Cuts SVG graph — network topology of critical cables (cuts page)
 */
(function () {
  'use strict';

  const ARC_R   = 32;
  const ARC_CIRC = 2 * Math.PI * ARC_R;   // 201.06
  const ARC_OFF  = ARC_CIRC / 4;           // 50.27 — start from top

  // Palette mirrors --ok / --warn / --danger / accent tones in app.css
  const C = {
    ok:     '#41da8c',
    green:  '#84cc16',
    warn:   '#f0b24f',
    amber:  '#f97316',
    danger: '#ff6f8d',
    teal:   '#0d9488',
    purple: '#a871ff',
    muted:  'rgba(255,255,255,0.35)',
    border: 'rgba(255,255,255,0.07)',
    bg:     'rgba(8,6,15,0.95)',
  };

  function latColor(ms) {
    if (!ms || ms <= 0) return C.muted;
    if (ms < 50)   return C.ok;
    if (ms < 100)  return C.green;
    if (ms < 150)  return C.warn;
    if (ms < 300)  return C.amber;
    return C.danger;
  }

  // ── 1. Arc progress ──────────────────────────────────────────────────────────
  //    Watches #daily-progress-label for text changes.
  //    Parses "X / Y — Z.Z%" and drives the SVG arc + text nodes.
  function initArcProgress() {
    const lbl  = document.getElementById('daily-progress-label');
    const fill = document.getElementById('daily-arc-fill');
    const pctEl = document.getElementById('daily-arc-pct');
    const subEl = document.getElementById('daily-arc-sub');
    if (!lbl || !fill) return;

    function update() {
      const txt = lbl.textContent || '';
      // Matches: "43 / 699 — 6.2%" or "699 / 699 — 100%"
      const m = txt.match(/(\d+)\s*\/\s*(\d+)\s*[—\-]\s*([\d.]+)%/);
      if (!m) return;
      const cur = parseInt(m[1]);
      const tot = parseInt(m[2]);
      const pct = parseFloat(m[3]);
      const dash = ARC_CIRC * (pct / 100);
      const gap  = ARC_CIRC - dash;
      const color = pct >= 100 ? C.ok : pct >= 50 ? C.teal : C.teal;

      fill.setAttribute('stroke-dasharray',  `${dash.toFixed(1)} ${gap.toFixed(1)}`);
      fill.setAttribute('stroke-dashoffset', ARC_OFF.toFixed(1));
      fill.setAttribute('stroke', color);
      if (pctEl) { pctEl.textContent = pct.toFixed(1) + '%'; pctEl.setAttribute('fill', color); }
      if (subEl) subEl.textContent = `${cur} / ${tot}`;
    }

    new MutationObserver(update).observe(lbl, { childList: true, characterData: true, subtree: true });
  }

  // ── 2. Live scatter chart ────────────────────────────────────────────────────
  //    Watches #daily-tbody for new rows. Each row = one cable probed.
  //    Requires Chart.js loaded before this script.
  let _scatter = null;
  let _scatterN = 0;

  function initLiveScatter() {
    const tbody  = document.getElementById('daily-tbody');
    const card   = document.getElementById('daily-scatter-card');
    const canvas = document.getElementById('daily-scatter-canvas');
    if (!tbody || !card || !canvas) return;
    if (typeof Chart === 'undefined') {
      console.warn('[RMIFO UI] Chart.js not loaded — scatter disabled');
      return;
    }

    canvas.height = 140;

    _scatter = new Chart(canvas, {
      type: 'scatter',
      data: { datasets: [{
        data: [],
        pointBackgroundColor: [],
        pointBorderColor:     'transparent',
        pointRadius:          3.5,
        pointHoverRadius:     5.5,
        showLine:             false,
      }]},
      options: {
        responsive:           true,
        maintainAspectRatio:  false,
        animation:            false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: C.bg,
            borderColor:     C.border,
            borderWidth:     1,
            titleColor:      '#f4efff',
            bodyColor:       C.muted,
            padding:         10,
            cornerRadius:    8,
            callbacks: {
              label: ctx => `${ctx.raw.cable || 'cabo'}: ${(ctx.raw.y ?? 0).toFixed(1)} ms`,
            },
          },
        },
        scales: {
          x: {
            ticks:  { color: C.muted, font: { size: 10 } },
            grid:   { color: C.border },
            border: { display: false },
            title:  { display: true, text: 'Sonda #', color: C.muted, font: { size: 10 } },
          },
          y: {
            ticks:  { color: C.muted, font: { size: 10 } },
            grid:   { color: C.border },
            border: { display: false },
            title:  { display: true, text: 'RTT ms', color: C.muted, font: { size: 10 } },
            min:    0,
          },
        },
      },
    });

    new MutationObserver(() => {
      const first = tbody.firstElementChild;
      if (!first) return;
      const cells = first.querySelectorAll('td');
      if (cells.length < 4) return;

      // columns: #, cable (mono), protocol, latency, hops, switches
      const cable  = cells[1]?.textContent?.trim() || '';
      const latTxt = cells[3]?.textContent?.trim() || '';
      const ms     = parseFloat(latTxt);
      if (isNaN(ms) || ms <= 0) return;

      _scatterN++;
      const ds = _scatter.data.datasets[0];
      ds.data.push({ x: _scatterN, y: ms, cable });
      ds.pointBackgroundColor.push(latColor(ms));
      // Cap at 300 points to avoid performance issues on long runs
      if (ds.data.length > 300) { ds.data.shift(); ds.pointBackgroundColor.shift(); }
      _scatter.update('none');

      card.style.display = '';
      const cnt = document.getElementById('scatter-count');
      if (cnt) cnt.textContent = `${_scatterN} sonda${_scatterN !== 1 ? 's' : ''}`;
    }).observe(tbody, { childList: true });
  }

  // ── 3. History heatmap ───────────────────────────────────────────────────────
  //    Watches #hist-tbody for child mutations.
  //    Builds horizontal bar heatmap above the table.
  function initHeatmap() {
    const tbody  = document.getElementById('hist-tbody');
    const card   = document.getElementById('hist-heatmap-card');
    const barsEl = document.getElementById('hist-heatmap-bars');
    if (!tbody || !card || !barsEl) return;

    new MutationObserver(() => {
      const rows = [...tbody.querySelectorAll('tr')];
      if (!rows.length) { card.style.display = 'none'; return; }

      // columns: #, cable_id (+ BD badge), proto, latency, type-badge
      const entries = rows.map(row => {
        const cells  = row.querySelectorAll('td');
        const rawName = cells[1]?.textContent?.trim() || '';
        // strip "BD" badge suffix
        const name    = rawName.replace(/\s*BD\s*$/, '').trim().slice(0, 30);
        const latTxt  = cells[3]?.textContent?.replace(/\s*ms\s*/i, '').trim() || '';
        const ms      = parseFloat(latTxt);
        return { name, ms: isNaN(ms) ? 0 : ms };
      }).filter(e => e.name);

      if (!entries.length) { card.style.display = 'none'; return; }

      const maxMs = Math.max(...entries.map(e => e.ms), 1);

      barsEl.innerHTML = entries.slice(0, 50).map(({ name, ms }) => {
        const color = latColor(ms);
        const pct   = ((ms / maxMs) * 100).toFixed(1);
        const label = ms > 0 ? ms.toFixed(1) + ' ms' : '—';
        return `<div class="hbar-row">
          <div class="hbar-label" title="${name}">${name}</div>
          <div class="hbar-track">
            <div class="hbar-fill" style="width:${pct}%;background:${color}"></div>
          </div>
          <div class="hbar-val" style="color:${color}">${label}</div>
        </div>`;
      }).join('');

      card.style.display = '';
    }).observe(tbody, { childList: true });
  }

  // ── 4. Cuts SVG graph ────────────────────────────────────────────────────────
  //    Watches #cuts-result for content injection by renderCriticalCuts().
  //    Prepends an SVG network topology above the table.
  function initCutsGraph() {
    const resultEl = document.getElementById('cuts-result');
    if (!resultEl) return;

    function lerp(a, b, t) { return a + (b - a) * t; }

    function buildGraph() {
      const rows = [...resultEl.querySelectorAll('tbody tr')];
      if (!rows.length) return;

      const cables = rows.map(row => {
        const cells = row.querySelectorAll('td');
        return {
          id:     cells[0]?.textContent?.trim() || '',
          name:   cells[1]?.textContent?.trim() || '',
          routes: parseInt(cells[2]?.textContent) || 0,
          impact: (cells[3]?.textContent?.trim() || '').toLowerCase(),
        };
      }).filter(c => c.id || c.name).slice(0, 6);

      if (!cables.length) return;

      // Remove previous graph if re-rendering
      const old = resultEl.querySelector('#cuts-svg-wrap');
      if (old) old.remove();

      const W = 400, H = 190;
      const srcX = 58, dstX = W - 58, midY = H / 2;
      const n    = cables.length;
      const isIsolated = cables.some(c => c.routes > 2 || c.impact === 'high');
      const countryA = document.getElementById('cut-country-a')?.value || 'Origem';
      const countryB = document.getElementById('cut-country-b')?.value || 'Destino';

      let svg = `<defs>
        <marker id="cg-crit" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="4" markerHeight="4" orient="auto">
          <path d="M1 1L7 4L1 7" fill="none" stroke="${C.danger}" stroke-width="1.2"/>
        </marker>
        <marker id="cg-ok" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="4" markerHeight="4" orient="auto">
          <path d="M1 1L7 4L1 7" fill="none" stroke="${C.ok}" stroke-width="1.2"/>
        </marker>
      </defs>
      <circle cx="${srcX}" cy="${midY}" r="22" fill="rgba(168,113,255,.1)" stroke="${C.purple}" stroke-width="2"/>
      <text x="${srcX}" y="${midY-5}" text-anchor="middle" fill="${C.purple}" font-size="8" font-weight="600">${countryA.slice(0,8)}</text>
      <text x="${srcX}" y="${midY+9}" text-anchor="middle" fill="${C.purple}" font-size="9">⬤</text>
      <circle cx="${dstX}" cy="${midY}" r="22" fill="rgba(65,218,140,.08)" stroke="${C.ok}" stroke-width="2"/>
      <text x="${dstX}" y="${midY-5}" text-anchor="middle" fill="${C.ok}" font-size="8" font-weight="600">${countryB.slice(0,8)}</text>
      <text x="${dstX}" y="${midY+9}" text-anchor="middle" fill="${C.ok}" font-size="9">⬤</text>`;

      cables.forEach((cable, i) => {
        const nodeX  = lerp(srcX + 30, dstX - 30, (i + 1) / (n + 1));
        const offset = (i % 2 === 0 ? -1 : 1) * (Math.floor(i / 2) + 1) * 38;
        const nodeY  = midY + offset;
        const isCrit = cable.routes > 2 || cable.impact === 'high';
        const color  = isCrit ? C.danger : C.purple;
        const dash   = isCrit ? '4 3' : '0';
        const marker = isCrit ? 'url(#cg-crit)' : 'url(#cg-ok)';
        const endYSrc = midY + offset * 0.28;
        const endYDst = midY + offset * 0.28;

        svg += `
          <line x1="${srcX+20}" y1="${endYSrc.toFixed(0)}" x2="${nodeX-27}" y2="${nodeY}"
            stroke="${color}" stroke-width="1.3" stroke-dasharray="${dash}" marker-end="${marker}" opacity=".75"/>
          <line x1="${nodeX+27}" y1="${nodeY}" x2="${dstX-20}" y2="${endYDst.toFixed(0)}"
            stroke="${color}" stroke-width="1.3" stroke-dasharray="${dash}" marker-end="${marker}" opacity=".75"/>
          <rect x="${nodeX-27}" y="${nodeY-12}" width="54" height="24" rx="5"
            fill="rgba(8,6,15,.88)" stroke="${color}" stroke-width="1" stroke-dasharray="${dash}"/>
          <text x="${nodeX}" y="${nodeY+1}" text-anchor="middle" dominant-baseline="central"
            fill="${color}" font-size="7.5" font-weight="500">${cable.name.slice(0, 15)}</text>
          <text x="${nodeX}" y="${nodeY+14}" text-anchor="middle"
            fill="${color}" font-size="7" opacity=".8">${cable.routes} rota${cable.routes !== 1 ? 's' : ''}</text>
          ${isCrit ? `<text x="${nodeX}" y="${nodeY-16}" text-anchor="middle" fill="${C.danger}" font-size="7">✕ crítico</text>` : ''}`;
      });

      if (isIsolated) {
        svg += `
          <rect x="${W/2-62}" y="${H-22}" width="124" height="18" rx="3"
            fill="rgba(255,111,141,.08)" stroke="${C.danger}" stroke-width="1"/>
          <text x="${W/2}" y="${H-10}" text-anchor="middle"
            fill="${C.danger}" font-size="9" font-weight="600">⚠ ISOLAMENTO DETECTADO</text>`;
      }

      const wrap = document.createElement('div');
      wrap.id = 'cuts-svg-wrap';
      wrap.className = 'cuts-svg-wrap';
      wrap.innerHTML = `<svg width="100%" height="${H}" viewBox="0 0 ${W} ${H}"
        xmlns="http://www.w3.org/2000/svg" style="display:block">${svg}</svg>`;
      resultEl.insertBefore(wrap, resultEl.firstChild);
    }

    new MutationObserver(() => {
      if (resultEl.querySelector('tbody tr')) buildGraph();
    }).observe(resultEl, { childList: true, subtree: true });
  }

  // ── Bootstrap ────────────────────────────────────────────────────────────────
  function init() {
    initArcProgress();
    initHeatmap();
    initCutsGraph();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
