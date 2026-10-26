/* Shows data entered by staff on the public dashboards:
   warnings (banner on every page), county advisories, forecast corrections,
   and weather stations with their latest readings.
   Data comes from /api/public on this server. If the server API is unreachable,
   the dashboards still work, just without this extra information. */
(function () {
  'use strict';
  const K = window.KFExtras = {};
  let DATA = null;
  const ready = fetch('/api/public', {cache: 'no-store', credentials: 'omit'})
    .then(r => (r.ok ? r.json() : null)).catch(() => null).then(d => (DATA = d));
  const PIDX = {rain: 0, tmax: 1, tmin: 2};
  const UNIT = {rain: 'mm', tmax: '°C', tmin: '°C'};
  const PNAME = {rain: 'rainfall', tmax: 'maximum temperature', tmin: 'minimum temperature'};
  const corrected = [];
  let state = null, startDate = null;

  // ---------- start the dashboard once staff data has arrived (or after 4 seconds without it)
  K.start = (FC, main) => {
    const timeout = new Promise(res => setTimeout(res, 4000));
    Promise.race([ready, timeout]).then(() => {
      try { applyCorrections(FC); } catch (e) { console.error(e); }
      startDate = FC.start;
      main(FC);
    });
  };

  function applyCorrections(FC) {
    if (!DATA) return;
    const start = Date.parse(FC.start + 'T00:00:00Z');
    for (const c of DATA.corrections) {
      const k = Math.round((Date.parse(c.date + 'T00:00:00Z') - start) / 864e5) + 1;
      if (k < 1 || k > FC.days) continue;
      const county = FC.counties.find(x => x.n === c.county);
      if (!county || !(c.parameter in PIDX)) continue;
      const i = (PIDX[c.parameter] * FC.days + k - 1) * 2;
      const old = county.m[i];
      county.m[i] = c.value;
      const ext = county.m[i + 1];
      if (c.parameter === 'tmin' ? c.value < ext : c.value > ext) county.m[i + 1] = c.value;
      corrected.push(Object.assign({day: k, old}, c));
    }
  }

  // ---------- helpers
  const el = (tag, cls, ...kids) => { const e = document.createElement(tag); if (cls) e.className = cls; kids.flat().forEach(k => k != null && e.append(k)); return e; };
  const f1 = v => v == null ? '–' : (Math.round(v * 10) / 10).toFixed(1);
  const fmtWhen = s => new Date(s).toLocaleString('en-GB', {timeZone: 'Africa/Nairobi', weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'});
  const fmtDay = s => new Date(s + 'T00:00:00Z').toLocaleDateString('en-GB', {timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short'});
  const dayDate = (k) => startDate ? new Date(Date.parse(startDate + 'T00:00:00Z') + (k - 1) * 864e5).toISOString().slice(0, 10) : null;

  const css = `
  .kfx-banner{border-radius:12px;padding:12px 16px;margin:0 0 12px;border:1px solid;display:flex;gap:12px;align-items:flex-start}
  .kfx-banner .kfx-lvl{font-size:.75rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;border:1px solid currentColor;border-radius:999px;padding:1px 9px;white-space:nowrap;margin-top:2px}
  .kfx-banner h3{margin:0 0 2px;font-size:1.05rem}.kfx-banner p{margin:0;font-size:.93rem}
  .kfx-banner .kfx-meta{color:var(--muted);font-size:.85rem;margin-top:4px}
  .kfx-warning{background:rgba(220,38,38,.10);border-color:rgba(220,38,38,.55);color:#9b1c1c}
  .kfx-watch{background:rgba(234,138,0,.12);border-color:rgba(234,138,0,.55);color:#8a4600}
  .kfx-advisory{background:rgba(11,110,153,.09);border-color:rgba(11,110,153,.45);color:#0a5a7d}
  @media (prefers-color-scheme:dark){.kfx-warning{color:#ffb4ab}.kfx-watch{color:#f6c27a}.kfx-advisory{color:#9fd6f2}}
  .kfx-banner .kfx-body{color:var(--ink)}
  .kfx-banner.kfx-here{box-shadow:0 0 0 3px currentColor inset}
  .kfx-info{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:0 0 20px;max-width:62rem}
  .kfx-info h3{margin:0 0 6px;font-size:1rem}
  .kfx-info p{margin:0 0 8px}
  .kfx-info .kfx-src{color:var(--muted);font-size:.85rem}
  .kfx-info table{border-collapse:collapse;font-size:.9rem;font-variant-numeric:tabular-nums;margin-top:4px}
  .kfx-info th{color:var(--muted);font-weight:700;text-align:left;padding:4px 12px 4px 0;font-size:.8rem}
  .kfx-info td{padding:4px 12px 4px 0;border-top:1px solid var(--line)}
  .kfx-stn{fill:#0e2a33;stroke:#fff;vector-effect:non-scaling-stroke;stroke-width:1.5px;cursor:help}
  @media (prefers-color-scheme:dark){.kfx-stn{fill:#ffffff;stroke:#0d1c21}}`;

  // ---------- warnings banner (every page)
  function banners() {
    if (!DATA || !DATA.warnings.length) return;
    const wrap = document.querySelector('.wrap'); if (!wrap) return;
    const now = new Date().toISOString();
    const box = el('div', 'kfx-banners'); box.setAttribute('role', 'region'); box.setAttribute('aria-label', 'Weather warnings');
    for (const w of DATA.warnings) {
      const active = new Date(w.starts_at) <= new Date();
      const where = w.counties.includes('ALL') ? 'All counties' : w.counties.join(', ');
      const b = el('div', 'kfx-banner kfx-' + w.level,
        el('span', 'kfx-lvl', w.level),
        el('div', 'kfx-body', el('h3', null, w.title), el('p', null, w.message),
          el('p', 'kfx-meta', where + ' · ' + (active ? 'until ' + fmtWhen(w.ends_at) : 'from ' + fmtWhen(w.starts_at) + ' to ' + fmtWhen(w.ends_at)))));
      b.dataset.counties = w.counties.join('|');
      box.append(b);
    }
    wrap.prepend(box);
    void now;
  }

  // ---------- advisories, corrections and station readings for the selected county and day
  function infoPanel() {
    if (!DATA || !state) return;
    const summary = document.getElementById('summary'); if (!summary) return;
    let panel = document.getElementById('kfx-info');
    if (!panel) { panel = el('section', 'kfx-info'); panel.id = 'kfx-info'; panel.setAttribute('aria-live', 'polite'); summary.after(panel); }
    const day = dayDate(state.day), c = state.county;
    const parts = [];
    const advs = DATA.advisories.filter(a => (a.county === 'ALL' || a.county === c) && (!day || (a.valid_from <= day && a.valid_to >= day)));
    for (const a of advs) parts.push(el('div', null, el('h3', null, 'Forecaster’s advisory · ' + (a.county === 'ALL' ? 'All counties' : a.county)),
      el('p', null, a.summary), el('p', 'kfx-src', 'Valid ' + fmtDay(a.valid_from) + (a.valid_to !== a.valid_from ? ' to ' + fmtDay(a.valid_to) : '') + ' · issued by forecast staff')));
    if (!c) {
      const others = new Set(DATA.advisories.filter(a => a.county !== 'ALL' && (!day || (a.valid_from <= day && a.valid_to >= day))).map(a => a.county));
      if (others.size) parts.push(el('p', 'kfx-src', 'Forecasters have advisories for ' + [...others].sort().join(', ') + ' on this day. Select a county to read them.'));
    }
    const corr = corrected.filter(x => x.day === state.day && (!c || x.county === c) && x.parameter === state.param);
    if (corr.length) parts.push(el('div', null, el('h3', null, 'Adjusted by forecasters'),
      ...corr.map(x => el('p', null, x.county + ': ' + PNAME[x.parameter] + ' set to ' + f1(x.value) + ' ' + UNIT[x.parameter] + ' (the automatic forecast was ' + f1(x.old) + '). ', el('span', 'kfx-src', x.reason)))));
    if (c) {
      const stns = DATA.stations.filter(s => s.county === c);
      if (stns.length) {
        const latest = s => DATA.readings.filter(r => r.station_id === s.id).slice(-1)[0];
        const rowsWith = stns.map(s => [s, latest(s)]).filter(([, r]) => r);
        if (rowsWith.length) parts.push(el('div', null, el('h3', null, 'Latest station readings in ' + c),
          el('table', null, el('tr', null, ...['Station', 'Date', 'Rain (mm)', 'Max (°C)', 'Min (°C)'].map(h => el('th', null, h))),
            ...rowsWith.map(([s, r]) => el('tr', null, el('td', null, s.name), el('td', null, fmtDay(r.date)), el('td', null, f1(r.rain_mm)), el('td', null, f1(r.tmax)), el('td', null, f1(r.tmin)))))));
      }
    }
    document.querySelectorAll('.kfx-banner').forEach(b => b.classList.toggle('kfx-here', !!c && b.dataset.counties.split('|').includes(c)));
    panel.replaceChildren(...parts);
    panel.hidden = !parts.length;
  }

  // ---------- stations on the map
  function stationsOnMap() {
    if (!DATA || !DATA.stations.length) return;
    const svg = document.querySelector('#map svg'); if (!svg || svg.querySelector('.kfx-stations')) return;
    const vb = svg.viewBox.baseVal, rect = svg.getBoundingClientRect(); if (!rect.width) return;
    const k = Math.max(vb.width / rect.width, vb.height / rect.height);
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g'); g.setAttribute('class', 'kfx-stations');
    for (const s of DATA.stations) {
      const r = DATA.readings.filter(x => x.station_id === s.id).slice(-1)[0];
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      c.setAttribute('cx', s.lon); c.setAttribute('cy', -s.lat); c.setAttribute('r', 4.5 * k); c.setAttribute('class', 'kfx-stn');
      const t = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      t.textContent = s.name + ' station (' + s.county + ')' + (r ? '\n' + fmtDay(r.date) + ': rain ' + f1(r.rain_mm) + ' mm, max ' + f1(r.tmax) + ' °C, min ' + f1(r.tmin) + ' °C' : '\nNo recent readings');
      c.append(t); g.append(c);
    }
    svg.append(g);
  }

  function boot() {
    const st = document.createElement('style'); st.textContent = css; document.head.append(st);
    ready.then(() => {
      banners(); infoPanel(); stationsOnMap();
      const map = document.getElementById('map');
      if (map) new MutationObserver(() => stationsOnMap()).observe(map, {childList: true});
    });
  }
  document.addEventListener('kf:render', e => { state = e.detail; if (DATA) { infoPanel(); stationsOnMap(); } });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
