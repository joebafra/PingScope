/* Shared helpers: API, formatting, status, smoke chart. */
const PS = (() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const store = {
    get(k, d){ try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch(e){ return d; } },
    set(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch(e){} },
  };

  async function api(path, opts = {}){
    const o = {cache: 'no-store', credentials: 'same-origin', ...opts, headers: {...(opts.headers || {})}};
    if (o.body !== undefined && typeof o.body !== 'string'){ o.body = JSON.stringify(o.body); o.headers['content-type'] = 'application/json'; }
    if (o.method && o.method !== 'GET') o.headers['x-pingscope'] = '1';
    const r = await fetch(path, o);
    let data = null;
    try { data = await r.json(); } catch(e){}
    if (!r.ok){
      const d = data && data.detail;
      const err = new Error(typeof d === 'string' ? d : (d && d.message) || `Request failed (${r.status})`);
      err.status = r.status; err.errors = d && d.errors; throw err;
    }
    return data;
  }

  function toast(msg, isErr){
    let box = $('#toasts');
    if (!box){ box = document.createElement('div'); box.id = 'toasts'; document.body.appendChild(box); }
    const t = document.createElement('div'); t.className = 'toast' + (isErr ? ' err' : ''); t.textContent = msg;
    box.appendChild(t); setTimeout(() => t.remove(), isErr ? 7000 : 3500);
  }

  // ---- formatting ----
  const LOCALE = 'en-GB';
  const fmtMs = v => v == null ? '–' : (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)) + ' ms';
  const fmtLoss = p => p == null ? '–' : (p === 0 ? '0%' : p < 1 ? p.toFixed(1) + '%' : Math.round(p) + '%');
  const fmtDT = t => new Date(t * 1000).toLocaleString(LOCALE, {weekday:'short', day:'numeric', month:'short', hour:'2-digit', minute:'2-digit', second:'2-digit'});
  const fmtShort = t => new Date(t * 1000).toLocaleString(LOCALE, {day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'});
  const fmtAxis = (t, span) => new Date(t * 1000).toLocaleString(LOCALE,
    span <= 2 * 86400 ? {hour:'2-digit', minute:'2-digit'} : span <= 40 * 86400 ? {day:'numeric', month:'short'} : {month:'short', year:'2-digit'});
  function ago(t){
    if (!t) return 'never';
    const s = Math.max(0, Math.round(Date.now() / 1000 - t));
    return s < 90 ? s + 's ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago';
  }

  // ---- status ----
  // SmokePing-style loss colours: bright green = no loss, then light blue, blue, magenta, orange, red, dark red
  const LOSS = [[0,'#00d400','0%'],[5,'#00b8ff','≤ 5%'],[10,'#0059ff','≤ 10%'],[25,'#ff00ff','≤ 25%'],[50,'#ff7a00','≤ 50%'],[99.9,'#ff0000','> 50%'],[100,'#a00000','100%']];
  const lossColor = p => { for (const [m, c] of LOSS) if (p <= m) return c; return LOSS[LOSS.length - 1][1]; };
  function status(s){
    if (!s) return 'unknown';
    if (s.unresolved || s.error || (s.last && s.last.loss >= 100)) return 'down';
    if (!s.last) return 'unknown';
    return s.last.loss > 0 ? 'loss' : 'ok';
  }
  const RANK = {down: 3, loss: 2, ok: 1, unknown: 0};
  const worst = list => list.reduce((w, s) => RANK[s] > RANK[w] ? s : w, 'unknown');
  const probeBadge = (p, label) => `<span class="badge ${esc(p)}">${esc(label || p.toUpperCase())}</span>`;

  // ---- chart ----
  const med = q => { const k = q.length; return k % 2 ? q[k >> 1] : (q[k / 2 - 1] + q[k / 2]) / 2; };
  function niceCeil(v){ if (!(v > 0)) return 1; const e = Math.pow(10, Math.floor(Math.log10(v))); for (const m of [1,2,2.5,5,10]) if (m * e >= v) return m * e; return 10 * e; }

  function draw(cv, d){
    if (d) cv._d = d; d = cv._d;
    const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    if (!W || !H) return;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    const c = cv.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
    const st = getComputedStyle(document.documentElement), v = n => st.getPropertyValue(n).trim();
    c.font = v('--chart-font') || '10px system-ui,sans-serif';
    if (!d){ c.fillStyle = v('--faint'); c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('Loading…', W / 2, H / 2); return; }
    const L = 50, R = 8, T = 8, B = 18, pw = W - L - R, ph = H - T - B, span = d.end - d.start;
    const tops = d.points.filter(p => p.q.length).map(p => p.q[p.q.length - 1]).sort((a, b) => a - b);
    const ymax = niceCeil(tops.length ? tops[Math.min(tops.length - 1, Math.floor(tops.length * .95))] * 1.1 : 10);
    const X = t => L + (t - d.start) / span * pw, Y = ms => T + ph - Math.min(ms, ymax) / ymax * ph;
    cv._g = {L, pw, span};
    const plotBg = v('--plot-bg'), axis = v('--axis'), dash = v('--grid-dash');
    if (plotBg){ c.fillStyle = plotBg; c.fillRect(L, T, pw, ph); }
    c.lineWidth = 1; c.strokeStyle = v('--chart-grid') || v('--grid'); c.fillStyle = v('--muted');
    c.textAlign = 'right'; c.textBaseline = 'middle';
    c.setLineDash(dash ? dash.split(/\s+/).map(Number) : []);
    for (let i = 0; i <= 4; i++){ const ms = ymax * i / 4, y = Math.round(Y(ms)) + .5; c.beginPath(); c.moveTo(L, y); c.lineTo(W - R, y); c.stroke(); c.fillText(fmtMs(ms), L - 6, y); }
    c.setLineDash([]);
    if (axis){ c.strokeStyle = axis; c.beginPath(); c.moveTo(L + .5, T); c.lineTo(L + .5, T + ph + .5); c.lineTo(W - R, T + ph + .5); c.stroke(); }
    c.textAlign = 'center'; c.textBaseline = 'top';
    const nx = W < 380 ? 3 : 5;
    for (let i = 0; i < nx; i++){ const f = i / (nx - 1), x = L + pw * f; c.fillText(fmtAxis(d.start + span * f, span), Math.min(Math.max(x, L + 20), W - R - 20), T + ph + 5); }
    if (!d.points.length){ c.fillStyle = v('--faint'); c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('No data yet', L + pw / 2, T + ph / 2); return; }
    const bw = Math.max(1, pw * d.step / span), smoke = v('--smoke');
    // 1) smoke: spread of all probes, darker towards the median
    for (const p of d.points){
      if (!p.sent) continue;
      const x = X(p.t), q = p.q, k = q.length;
      if (!k){ c.fillStyle = lossColor(100); c.fillRect(x, T, bw, 4); continue; }
      c.fillStyle = `rgba(${smoke},${Math.min(.35, .9 / Math.ceil(k / 2))})`;
      for (let i = 0; i < Math.floor(k / 2); i++){ const y1 = Y(q[k - 1 - i]), y2 = Y(q[i]); c.fillRect(x, y1, bw, Math.max(1, y2 - y1)); }
    }
    // 2) median line on top, coloured by packet loss, with a thin dark edge so it stands out on the smoke
    const lw = H >= 160 ? 3 : 2.5, w = Math.max(bw, 2), dark = matchMedia('(prefers-color-scheme: dark)').matches;
    const meds = d.points.filter(p => p.sent && p.q.length).map(p => [X(p.t), Y(med(p.q)), p.lost / p.sent * 100]);
    if (!dark){ c.fillStyle = 'rgba(0,0,0,.45)'; for (const [x, y] of meds) c.fillRect(x - 0.5, y - lw / 2 - 0.75, w + 1, lw + 1.5); }
    for (const [x, y, loss] of meds){ c.fillStyle = lossColor(loss); c.fillRect(x, y - lw / 2, w, lw); }
  }

  function chartStats(d){
    let s = 0, l = 0, mn = Infinity, mx = 0; const ms = [];
    for (const p of d.points){ s += p.sent; l += p.lost; if (p.q.length){ ms.push(med(p.q)); mn = Math.min(mn, p.q[0]); mx = Math.max(mx, p.q[p.q.length - 1]); } }
    if (!ms.length) return {avg: null, min: null, max: null, jit: null, loss: s ? 100 : null, medMin: null, medMax: null, now: null, sd: null};
    const avg = ms.reduce((a, b) => a + b, 0) / ms.length;
    let jit = 0; for (let i = 1; i < ms.length; i++) jit += Math.abs(ms[i] - ms[i - 1]);
    const sd = Math.sqrt(ms.reduce((a, b) => a + (b - avg) * (b - avg), 0) / ms.length);
    return {avg, min: mn, max: mx, jit: ms.length > 1 ? jit / (ms.length - 1) : 0, loss: s ? l / s * 100 : null,
            medMin: Math.min(...ms), medMax: Math.max(...ms), now: ms[ms.length - 1], sd};
  }

  let tip;
  function hookChart(cv, onPick){
    tip = tip || $('#tip') || Object.assign(document.body.appendChild(document.createElement('div')), {id: 'tip'});
    let lastTouch = null;
    const bucketAt = e => {
      const d = cv._d, g = cv._g; if (!d || !g) return null;
      const r = cv.getBoundingClientRect(), t = d.start + (e.clientX - r.left - g.L) / g.pw * g.span;
      if (t < d.start || t > d.end) return null;
      return d.points.find(p => t >= p.t && t < p.t + d.step) || {t, empty: true};
    };
    const show = e => {
      const p = bucketAt(e);
      if (!p || p.empty){ tip.style.display = 'none'; return; }
      const loss = p.sent ? p.lost / p.sent * 100 : 0;
      tip.innerHTML = `<b>${new Date(p.t * 1000).toLocaleString(LOCALE, {dateStyle: 'medium', timeStyle: 'short'})}</b><br>` +
        (p.q.length ? `median ${fmtMs(med(p.q))}<br>min ${fmtMs(p.q[0])} · max ${fmtMs(p.q[p.q.length - 1])}<br>` : 'no replies<br>') +
        `<span class="dot" style="background:${lossColor(loss)};margin-right:5px"></span>loss ${loss.toFixed(1)}% (${p.lost}/${p.sent})` +
        (onPick ? `<br><span style="color:var(--accent)">${e.pointerType === 'touch' ? 'Tap again' : 'Click'} for traceroute</span>` : '');
      tip.style.display = 'block';
      const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8), y = Math.max(8, e.clientY - tip.offsetHeight - 14);
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    };
    cv.addEventListener('pointermove', show);
    cv.addEventListener('pointerdown', show);
    cv.addEventListener('pointerleave', () => tip.style.display = 'none');
    if (onPick){
      cv.style.cursor = 'pointer';
      cv.addEventListener('click', e => {
        const p = bucketAt(e); if (!p) return;
        if (e.pointerType === 'touch' && lastTouch !== p.t){ lastTouch = p.t; return; }
        lastTouch = null; tip.style.display = 'none';
        onPick(p.t + (cv._d.step >> 1));
      });
    }
  }

  function legendHTML(){
    return '<span>Median colour = packet loss:</span>' + LOSS.map(([, c, l]) => `<span><i style="background:${c}"></i>${l}</span>`).join('') +
      '<span>Grey smoke = spread of all probes</span>';
  }

  addEventListener('resize', (() => { let t; return () => { clearTimeout(t); t = setTimeout(() => $$('canvas.chart').forEach(c => draw(c)), 150); }; })());
  matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => $$('canvas.chart').forEach(c => draw(c)));

  // Order targets of one group: by name (natural A→Z, ping before dns before tcp) or by manual order
  const PROBE_ORDER = {ping: 0, dns: 1, tcp: 2};
  const byName = (a, b) => a.name.localeCompare(b.name, undefined, {numeric: true, sensitivity: 'base'}) ||
    (PROBE_ORDER[a.probe] ?? 9) - (PROBE_ORDER[b.probe] ?? 9) || a.id - b.id;
  const sortTargets = (list, mode) => mode === 'name' ? list.slice().sort(byName) : list;

  // SmokePing-style text legend under a graph (classic theme)
  function classicStats(d){
    const x = chartStats(d), f = v => v == null ? '–' : fmtMs(v);
    return `<div>median rtt:&nbsp; <span><b>${f(x.avg)}</b> avg</span> <span><b>${f(x.medMax)}</b> max</span> <span><b>${f(x.medMin)}</b> min</span> <span><b>${f(x.now)}</b> now</span> <span><b>${f(x.sd)}</b> sd</span></div>` +
      `<div>packet loss: <b>${x.loss == null ? '–' : x.loss.toFixed(2) + ' %'}</b> avg</div>` +
      `<div class="lc">loss color: ${LOSS.map(([, c, l]) => `<span><i style="background:${c}"></i>${l}</span>`).join('')}</div>`;
  }

  return {$, $$, esc, store, api, sortTargets, classicStats, toast, fmtMs, fmtLoss, fmtDT, fmtShort, ago, lossColor, status, worst, probeBadge,
          med, draw, chartStats, hookChart, legendHTML, LOCALE};
})();
