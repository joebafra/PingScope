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
    o.headers['x-pingscope'] = '1';  // CSRF protection for changes; for reads it marks the request as the dashboard's own
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
  // Times are shown in the visitor's own time zone, or in the server's (Admin → Settings → Time zone): TZ is then
  // an IANA name like "Europe/Amsterdam".
  let TZ;
  const setTimeZone = z => { if ((z || undefined) !== TZ){ TZ = z || undefined; tzFmt = null; } };
  const fmtTime = (t, o) => new Date(t * 1000).toLocaleString(LOCALE, TZ ? {...o, timeZone: TZ} : o);
  const fmtDT = t => fmtTime(t, {weekday:'short', day:'numeric', month:'short', hour:'2-digit', minute:'2-digit', second:'2-digit'});
  const fmtShort = t => fmtTime(t, {day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'});
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
  // smallest 1, 2 or 5 × 10^n that is at least v (round steps, like SmokePing's axis)
  function niceStep(v){ if (!(v > 0)) return 1; const e = Math.pow(10, Math.floor(Math.log10(v))); for (const m of [1, 2, 5, 10]) if (m * e >= v * (1 - 1e-9)) return m * e; return 10 * e; }

  // Y axis in round steps (1, 2, 5 × 10^n ms), as many grid lines as fit the graph's height. The top follows the
  // bulk of the pings (90th percentile per bucket), so a few spikes don't squash the graph; they are cut off at the
  // top, like SmokePing does. The axis starts at 0, unless every value sits high above it (a steady 8 ms line):
  // then it starts just below the data, so the graph isn't mostly empty space.
  function yRange(d, ph){
    const pts = d.points.filter(p => p.q.length), pick = (a, f) => a[Math.min(a.length - 1, Math.floor(a.length * f))];
    const lines = Math.max(3, Math.min(10, Math.floor(ph / 18)));
    let top = 10, bottom = 0;
    if (pts.length){
      const his = pts.map(p => pick(p.q, .9)).sort((a, b) => a - b), lows = pts.map(p => p.q[0]).sort((a, b) => a - b);
      const hi = Math.max(pick(his, .95), 0.01), lo = pick(lows, .05);
      top = hi * 1.12;
      if (lo > hi * .6){ const range = Math.max(hi - lo, hi * .2); bottom = Math.max(0, lo - range * .15); }
    }
    for (let step = niceStep((top - bottom) / lines); ; step = niceStep(step * 1.01)){
      const ymin = Math.floor(bottom / step + 1e-9) * step, ymax = Math.max(ymin + step, Math.ceil(top / step - 1e-9) * step);
      if ((ymax - ymin) / step <= lines + 1) return {ymin, ymax, step};
    }
  }
  const fmtTick = (v, step) => `${v.toFixed(Math.max(0, -Math.floor(Math.log10(step) + 1e-9)))} ms`;

  // X axis: ticks on round wall-clock times (00:00, 06:00, Mondays, the 1st of a month) in the shown time zone
  let tzFmt = null;
  function tzOffset(t){  // seconds the shown time zone is ahead of UTC at time t
    if (!TZ) return -new Date(t * 1000).getTimezoneOffset() * 60;
    tzFmt = tzFmt || new Intl.DateTimeFormat('en-GB', {timeZone: TZ, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric'});
    const p = Object.fromEntries(tzFmt.formatToParts(new Date(t * 1000)).map(x => [x.type, x.value]));
    return Date.UTC(+p.year, p.month - 1, +p.day, +p.hour % 24, +p.minute) / 1000 - Math.floor(t / 60) * 60;
  }
  const DAY = 86400, TSTEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, DAY, 2 * DAY, 7 * DAY];
  function timeTicks(start, end, maxTicks){
    const span = end - start, step = TSTEPS.find(s => span / s <= maxTicks), ticks = [];
    if (step){
      const off = tzOffset(start), base = step === 7 * DAY ? 4 * DAY : 0;  // weeks start on Monday (1 Jan 1970 was a Thursday)
      for (let t = Math.ceil((start + off - base) / step) * step + base - off; t <= end; t += step) ticks.push(t);
      const label = step < DAY ? (t => span > DAY && (t + tzOffset(t)) % DAY === 0 ? fmtTime(t, {weekday: 'short', day: 'numeric'}) : fmtTime(t, {hour: '2-digit', minute: '2-digit'}))
        : (t => fmtTime(t, {day: 'numeric', month: 'short'}));
      return ticks.map(t => [t, label(t)]);
    }
    const months = [1, 2, 3, 6, 12].find(m => span / (m * 30.4 * DAY) <= maxTicks) || 12;
    const s0 = new Date((start + tzOffset(start)) * 1000);
    for (let y = s0.getUTCFullYear(), m = s0.getUTCMonth() + 1; ; m += 1){
      const wall = Date.UTC(y, m, 1) / 1000, t = wall - tzOffset(wall);
      if (t > end) break;
      const mm = new Date(wall * 1000).getUTCMonth();
      if (t >= start && mm % months === 0) ticks.push([t, fmtTime(t, mm === 0 ? {month: 'short', year: 'numeric'} : {month: 'short'})]);
    }
    return ticks;
  }

  // draw = graph (kept as a bitmap in cv._base) + overlay (crosshair, zoom selection) on top
  function draw(cv, d){
    drawBase(cv, d);
    if (!cv.width) return;
    const b = cv._base || (cv._base = document.createElement('canvas'));
    b.width = cv.width; b.height = cv.height;
    b.getContext('2d').drawImage(cv, 0, 0);
    if (cv._hx != null || cv._sel) overlay(cv);
  }

  function overlay(cv){
    const b = cv._base, g = cv._g; if (!b || !g) return;
    const dpr = window.devicePixelRatio || 1, c = cv.getContext('2d');
    c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, cv.width, cv.height); c.drawImage(b, 0, 0);
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    const st = getComputedStyle(document.documentElement), fg = st.getPropertyValue('--fg').trim() || '#000';
    if (cv._sel){
      const x0 = Math.max(g.L, Math.min(cv._sel[0], cv._sel[1])), x1 = Math.min(g.L + g.pw, Math.max(cv._sel[0], cv._sel[1]));
      c.fillStyle = st.getPropertyValue('--accent').trim() || '#2563eb';
      c.globalAlpha = .16; c.fillRect(x0, g.T, x1 - x0, g.ph);
      c.globalAlpha = .8; c.fillRect(x0, g.T, 1, g.ph); c.fillRect(x1 - 1, g.T, 1, g.ph);
      c.globalAlpha = 1;
    }
    if (cv._hx != null){
      const x = Math.round(cv._hx) + .5;
      c.strokeStyle = fg; c.globalAlpha = .55; c.lineWidth = 1; c.setLineDash([3, 3]);
      c.beginPath(); c.moveTo(x, g.T); c.lineTo(x, g.T + g.ph); c.stroke();
      c.setLineDash([]); c.globalAlpha = 1;
      if (cv._hy != null){
        c.beginPath(); c.arc(x, cv._hy, 4, 0, 2 * Math.PI);
        c.fillStyle = cv._hc || fg; c.fill(); c.lineWidth = 1.5; c.strokeStyle = st.getPropertyValue('--panel').trim() || '#fff'; c.stroke();
      }
    }
  }

  // crosshair: shown at the same moment on every visible graph that covers it
  let hoverRaf = 0, hoverT = null;
  function setHover(t){
    hoverT = t;
    if (hoverRaf) return;
    hoverRaf = requestAnimationFrame(() => {
      hoverRaf = 0;
      for (const cv of $$('canvas.chart')){
        const d = cv._d, g = cv._g;
        const had = cv._hx != null;
        cv._hx = cv._hy = null;
        if (hoverT != null && d && g && cv._vis !== false && hoverT >= d.start && hoverT <= d.end){
          const p = d.points.find(q => hoverT >= q.t && hoverT < q.t + d.step);
          cv._hx = g.L + ((p ? p.t + d.step / 2 : hoverT) - d.start) / g.span * g.pw;
          if (p && p.q.length){ cv._hy = g.Y(med(p.q)); cv._hc = lossColor(p.sent ? p.lost / p.sent * 100 : 0); }
        }
        if (had || cv._hx != null) overlay(cv);
      }
    });
  }

  function drawBase(cv, d){
    if (d) cv._d = d; d = cv._d;
    const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    if (!W || !H) return;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    const c = cv.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
    const st = getComputedStyle(document.documentElement), v = n => st.getPropertyValue(n).trim();
    c.font = v('--chart-font') || '10px system-ui,sans-serif';
    if (!d){ c.fillStyle = v('--faint'); c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('Loading…', W / 2, H / 2); return; }
    const L = 50, R = 8, T = 8, B = 18, pw = W - L - R, ph = H - T - B, span = d.end - d.start;
    const {ymin, ymax, step: ystep} = yRange(d, ph);
    const X = t => L + (t - d.start) / span * pw, Y = ms => T + ph - (Math.min(Math.max(ms, ymin), ymax) - ymin) / (ymax - ymin) * ph;
    cv._g = {L, pw, span, T, ph, Y};
    const plotBg = v('--plot-bg'), axis = v('--axis'), dash = v('--grid-dash');
    if (plotBg){ c.fillStyle = plotBg; c.fillRect(L, T, pw, ph); }
    // grid: a horizontal line per Y step and a vertical line per time tick, so the plot gets grid squares
    const ticks = timeTicks(d.start, d.end, Math.max(2, Math.floor(pw / 72)));
    c.lineWidth = 1; c.strokeStyle = v('--chart-grid') || v('--grid'); c.fillStyle = v('--muted');
    c.setLineDash(dash ? dash.split(/\s+/).map(Number) : []);
    c.beginPath();
    for (let ms = ymin; ms <= ymax + ystep / 2; ms += ystep){ const y = Math.round(Y(ms)) + .5; c.moveTo(L, y); c.lineTo(W - R, y); }
    for (const [t] of ticks){ const x = Math.round(X(t)) + .5; c.moveTo(x, T); c.lineTo(x, T + ph); }
    c.stroke();
    c.setLineDash([]);
    if (axis){ c.strokeStyle = axis; c.beginPath(); c.moveTo(L + .5, T); c.lineTo(L + .5, T + ph + .5); c.lineTo(W - R, T + ph + .5); c.stroke(); }
    c.textAlign = 'right'; c.textBaseline = 'middle';
    for (let ms = ymin; ms <= ymax + ystep / 2; ms += ystep) c.fillText(fmtTick(ms, ystep), L - 6, Math.round(Y(ms)) + .5);
    c.textAlign = 'center'; c.textBaseline = 'top';
    for (const [t, label] of ticks){ const w = c.measureText(label).width / 2; c.fillText(label, Math.min(Math.max(X(t), L + w), W - R - w), T + ph + 5); }
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
    // 2) median line on top, like SmokePing: a level across each bucket in the packet-loss colour, joined to the
    //    next bucket by a thin grey vertical step. Missing measurements stay a gap.
    const lw = 2;
    const runs = [];  // stretches of consecutive buckets with a median
    for (const p of d.points){
      if (!p.sent || !p.q.length) continue;
      const m = {t: p.t, x: X(p.t), y: Y(med(p.q)), col: lossColor(p.lost / p.sent * 100)};
      const run = runs[runs.length - 1], prev = run && run[run.length - 1];
      if (prev && m.t - prev.t <= d.step * 1.5) run.push(m); else runs.push([m]);
    }
    // a) the steps: 1px grey, snapped to whole device pixels so they stay crisp
    const crisp = x => (Math.round(x * dpr - dpr / 2) + dpr / 2) / dpr;
    c.lineCap = 'butt'; c.lineWidth = 1; c.strokeStyle = v('--muted');
    c.beginPath();
    for (const run of runs) for (let i = 1; i < run.length; i++){
      if (Math.abs(run[i].y - run[i - 1].y) < 0.5) continue;
      const x = crisp(run[i].x);
      c.moveTo(x, run[i - 1].y); c.lineTo(x, run[i].y);
    }
    c.stroke();
    // b) the levels on top, one path per loss colour
    c.lineWidth = lw;
    const byCol = new Map();
    for (const run of runs) for (const m of run){ if (!byCol.has(m.col)) byCol.set(m.col, []); byCol.get(m.col).push(m); }
    for (const [col, ms] of byCol){
      c.beginPath();
      for (const m of ms){ c.moveTo(m.x, m.y); c.lineTo(m.x + Math.max(bw, 1), m.y); }
      c.strokeStyle = col; c.stroke();
    }
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
      tip.innerHTML = `<b>${fmtTime(p.t, {dateStyle: 'medium', timeStyle: 'short'})}</b><br>` +
        (p.q.length ? `median ${fmtMs(med(p.q))}<br>min ${fmtMs(p.q[0])} · max ${fmtMs(p.q[p.q.length - 1])}<br>` : 'no replies<br>') +
        `<span class="dot" style="background:${lossColor(loss)};margin-right:5px"></span>loss ${loss.toFixed(1)}% (${p.lost}/${p.sent})` +
        (onPick ? `<br><span style="color:var(--accent)">${e.pointerType === 'touch' ? 'Tap again' : 'Click'} for traceroute</span>` : '') +
        (e.pointerType !== 'touch' && PS.onZoom ? `<br><span class="faint">Drag to zoom${cv._d?.custom ? ' · double-click to reset' : ''}</span>` : '');
      tip.style.display = 'block';
      const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8), y = Math.max(8, e.clientY - tip.offsetHeight - 14);
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    };
    const timeAt = x => { const d = cv._d, g = cv._g; return d.start + Math.max(0, Math.min(g.pw, x - g.L)) / g.pw * g.span; };
    const localX = e => e.clientX - cv.getBoundingClientRect().left;
    let drag = null, suppress = false;
    cv.style.cursor = 'crosshair';
    cv.style.touchAction = 'pan-y';
    cv.addEventListener('pointermove', e => {
      if (drag){
        const x = localX(e);
        if (Math.abs(x - drag) > 4){ cv._sel = [drag, x]; tip.style.display = 'none'; overlay(cv); return; }
      }
      show(e);
      const p = bucketAt(e); setHover(p ? p.t + (cv._d.step >> 1) : null);
    });
    cv.addEventListener('pointerdown', e => {
      show(e);
      if (e.pointerType !== 'touch' && e.button === 0 && cv._d && PS.onZoom){ drag = localX(e); cv.setPointerCapture?.(e.pointerId); }
    });
    cv.addEventListener('pointerup', e => {
      if (cv._sel){
        const [a, b] = cv._sel, t0 = timeAt(Math.min(a, b)), t1 = timeAt(Math.max(a, b));
        cv._sel = null; overlay(cv); suppress = true; setTimeout(() => suppress = false, 50);
        if (t1 - t0 >= (cv._d.step || 60)) PS.onZoom(cv, Math.floor(t0), Math.ceil(t1));
      }
      drag = null;
    });
    cv.addEventListener('pointercancel', () => { drag = null; cv._sel = null; overlay(cv); });
    cv.addEventListener('pointerleave', () => { tip.style.display = 'none'; if (!drag) setHover(null); });
    cv.addEventListener('dblclick', () => PS.onZoomReset?.(cv));
    if (onPick){
      cv.addEventListener('click', e => {
        if (suppress) return;
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

  // Colour scheme per visitor: follow the system (default), or always light / always dark.
  // The page <head> applies the stored choice before the CSS loads, so there is no flash.
  const SCHEMES = [
    ['system', 'Follow the system setting', '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>'],
    ['light', 'Light', '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'],
    ['dark', 'Dark', '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'],
  ];
  const getScheme = () => { const v = store.get('ps_scheme', 'system'); return SCHEMES.some(x => x[0] === v) ? v : 'system'; };
  function applyScheme(v = getScheme()){
    const r = document.documentElement;
    if (v === 'light' || v === 'dark') r.dataset.scheme = v; else delete r.dataset.scheme;
    requestAnimationFrame(() => $$('canvas.chart').forEach(c => draw(c)));
  }
  function schemeSwitch(el){
    if (!el) return;
    el.classList.add('seg', 'icons');
    el.setAttribute('role', 'group'); el.setAttribute('aria-label', 'Colour scheme');
    el.innerHTML = SCHEMES.map(([v, label, path]) => `<button type="button" data-v="${v}" title="${label}" aria-label="${label}">` +
      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg></button>`).join('');
    const paint = () => el.querySelectorAll('button').forEach(b => { const on = b.dataset.v === getScheme(); b.classList.toggle('on', on); b.setAttribute('aria-pressed', on); });
    el.onclick = e => { const b = e.target.closest('button[data-v]'); if (!b) return; store.set('ps_scheme', b.dataset.v); applyScheme(b.dataset.v); paint(); };
    paint();
  }

  // Small monochrome line icons, in the text colour (instead of emoji, which look different on every system)
  const ICONS = {
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    'bell-off': '<path d="M8.7 3A6 6 0 0 1 18 8c0 2 .2 3.6.6 5"/><path d="M17 17H3s3-2 3-9c0-.6.1-1.2.3-1.7"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/><path d="m2 2 20 20"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  };
  // Hidden by CSS when Settings → Show icons is off (<html data-icons="off">); `alt` is a short text shown instead,
  // for icons that mean something on their own (a lock next to a group name).
  const icon = (name, title, alt) => `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"` +
    (title ? ` role="img" aria-label="${esc(title)}"><title>${esc(title)}</title>` : ' aria-hidden="true">') + `${ICONS[name]}</svg>` +
    (alt ? `<span class="ico-alt">${esc(alt)}</span>` : '');
  const setIcons = v => { document.documentElement.dataset.icons = v === 'off' || v === false ? 'off' : 'on'; };

  // Tiny copy button next to an address. navigator.clipboard only works over HTTPS (or localhost), so plain
  // http://<ip> falls back to a hidden textarea + execCommand('copy').
  const ICON_COPY = '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>';
  const ICON_DONE = '<path d="M20 6 9 17l-5-5"/>';
  const svg = p => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
  const copyBtn = text => text ? `<button type="button" class="copy" data-copy="${esc(text)}" title="Copy" aria-label="Copy ${esc(text)}">${svg(ICON_COPY)}</button>` : '';
  async function copyText(text){
    try { if (navigator.clipboard && window.isSecureContext){ await navigator.clipboard.writeText(text); return true; } } catch(e){}
    const ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch(e){}
    ta.remove();
    return ok;
  }
  document.addEventListener('click', async e => {
    const b = e.target.closest('button.copy'); if (!b) return;
    e.preventDefault(); e.stopPropagation();
    const ok = await copyText(b.dataset.copy);
    b.classList.toggle('done', ok); b.classList.toggle('fail', !ok);
    b.innerHTML = svg(ok ? ICON_DONE : ICON_COPY); b.title = ok ? 'Copied' : 'Copy failed: select the text instead';
    clearTimeout(b._t); b._t = setTimeout(() => { b.classList.remove('done', 'fail'); b.innerHTML = svg(ICON_COPY); b.title = 'Copy'; }, 1500);
  });

  // Order targets of one group: by name (natural A→Z, ping before dns before tcp) or by manual order
  const PROBE_ORDER = {ping: 0, dns: 1, tcp: 2};
  const byName = (a, b) => a.name.localeCompare(b.name, undefined, {numeric: true, sensitivity: 'base'}) ||
    (PROBE_ORDER[a.probe] ?? 9) - (PROBE_ORDER[b.probe] ?? 9) || a.id - b.id;
  const sortTargets = (list, mode) => mode === 'name' ? list.slice().sort(byName) : list;

  // SmokePing-style text legend under a graph (classic theme)
  function classicStats(d, probe){
    const x = chartStats(d), f = v => v == null ? '–' : fmtMs(v);
    return `<div>median rtt:&nbsp; <span><b>${f(x.avg)}</b> avg</span> <span><b>${f(x.medMax)}</b> max</span> <span><b>${f(x.medMin)}</b> min</span> <span><b>${f(x.now)}</b> now</span> <span><b>${f(x.sd)}</b> sd</span></div>` +
      `<div>packet loss: <b>${x.loss == null ? '–' : x.loss.toFixed(2) + ' %'}</b> avg</div>` +
      `<div class="lc">loss color: ${LOSS.map(([, c, l]) => `<span><i style="background:${c}"></i>${l}</span>`).join('')}</div>` +
      (probe ? `<div>probe: ${esc(probe)}</div>` : '');
  }

  return {$, $$, esc, store, api, sortTargets, classicStats, toast, fmtMs, fmtLoss, fmtDT, fmtShort, ago, lossColor, status, worst, probeBadge,
          med, draw, chartStats, hookChart, legendHTML, LOCALE, schemeSwitch, applyScheme, copyBtn, fmtTime, setTimeZone, icon, setIcons};
})();
