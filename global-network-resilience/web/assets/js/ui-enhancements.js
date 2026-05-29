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

  // ── Bootstrap ────────────────────────────────────────────────────────────────
  function init() {
    initArcProgress();
    initHeatmap();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
