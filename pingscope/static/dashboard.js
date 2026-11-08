/* PingScope dashboard */
(() => {
const {$, $$, esc, store, api, sortTargets, classicStats, fmtMs, fmtLoss, fmtDT, fmtShort, ago, lossColor, status, worst, probeBadge, draw, chartStats, hookChart} = PS;
const RANGES = {'1h': 'Last hour', '3h': 'Last 3 hours', '6h': 'Last 6 hours', '12h': 'Last 12 hours', '1d': 'Last 24 hours',
                '7d': 'Last 7 days', '30d': 'Last 30 days', '1y': 'Last year'};
const SHORT = {'1h': '1h', '3h': '3h', '6h': '6h', '12h': '12h', '1d': 'Day', '7d': 'Week', '30d': 'Month', '1y': 'Year'};
// the periods chosen in Admin → Settings; the header buttons and the target page follow them
const periods = () => (S.data?.periods || ['3h', '1d', '7d', '30d', '1y']).filter(r => RANGES[r]);
const S = {
  data: null, groups: new Map(), kids: new Map(), targets: new Map(), byGroup: new Map(),
  fam: store.get('ps_fam', 'all'), range: store.get('ps_range', '1d'), q: '',
  expanded: new Set(store.get('ps_open', [])), sort: store.get('ps_sort', 'order'), sig: '',
  zoom: {},  // range -> {start, end}: graphs of that range show a period selected by dragging
};
const main = $('#main');

// ---------- data indexing ----------
function index(){
  const d = S.data;
  S.groups = new Map(d.groups.map(g => [g.id, g]));
  S.kids = new Map(); S.byGroup = new Map(); S.targets = new Map();
  for (const g of d.groups){ if (!S.kids.has(g.parent_id)) S.kids.set(g.parent_id, []); S.kids.get(g.parent_id).push(g); }
  for (const t of d.targets){
    S.targets.set(t.id, t);
    if (!S.byGroup.has(t.group_id)) S.byGroup.set(t.group_id, []);
    S.byGroup.get(t.group_id).push(t);
  }
  for (const [gid, list] of S.byGroup) S.byGroup.set(gid, sortTargets(list, S.groups.get(gid)?.sort_mode));
  // "Group order" = tree order, then each group's own target order
  S.ordered = [];
  const order = gid => { S.ordered.push(...(S.byGroup.get(gid) || [])); (S.kids.get(gid) || []).forEach(c => order(c.id)); };
  (S.kids.get(null) || []).forEach(g => order(g.id));
  const memo = new Map();
  const walk = gid => {
    if (memo.has(gid)) return memo.get(gid);
    const own = S.byGroup.get(gid) || [];
    let all = [...own];
    for (const c of S.kids.get(gid) || []) all = all.concat(walk(c.id));
    memo.set(gid, all); return all;
  };
  for (const g of d.groups) g._all = walk(g.id);
}
const kids = gid => S.kids.get(gid) || [];
const famSeries = (t, f) => t.series.find(s => s.family === f);
const visibleFams = () => S.fam === 'all' ? [4, 6] : [+S.fam];
const tStatus = t => worst(t.series.filter(s => S.fam === 'all' || s.family === +S.fam).map(status));
function gPath(gid){ const out = []; while (gid && S.groups.has(gid)){ const g = S.groups.get(gid); out.unshift(g); gid = g.parent_id; } return out; }
const pathText = gid => gPath(gid).map(g => g.name).join(' › ');
function matches(t){
  if (!S.q) return true;
  const hay = [t.name, t.host, t.host_v6, pathText(t.group_id), ...t.series.map(s => s.address)].join(' ').toLowerCase();
  return S.q.toLowerCase().split(/\s+/).every(w => hay.includes(w));
}

function valHTML(s){
  if (!s) return '<span class="val none">—</span>';
  if (s.unresolved) return `<span class="val down" title="${esc(s.error || '')}"><span class="dot down"></span>${s.error ? 'unresolved' : 'down'}</span>`;
  if (!s.last) return '<span class="val none"><span class="dot"></span>waiting</span>';
  if (s.last.loss >= 100) return '<span class="val down"><span class="dot down"></span>down</span>';
  return `<span class="val"><span class="dot ${status(s)}"></span>${fmtMs(s.last.median)}${s.last.loss > 0 ? ` <span class="l">· ${fmtLoss(s.last.loss)}<span class="hide-sm"> loss</span></span>` : ''}</span>`;
}

// ---------- navigation ----------
function renderNav(){
  const r = currentRoute();
  const sel = r.view === 'group' ? r.id : r.view === 'target' ? S.targets.get(r.id)?.group_id : null;
  const ancestors = new Set(sel ? gPath(sel).map(g => g.id) : []);
  const problems = S.data.targets.filter(t => ['loss', 'down'].includes(tStatus(t))).length;
  const node = g => {
    const ch = kids(g.id), open = S.expanded.has(g.id) || ancestors.has(g.id);
    const st = worst(g._all.flatMap(t => t.series.filter(s => S.fam === 'all' || s.family === +S.fam).map(status)));
    return `<li class="${open ? 'open' : ''}" data-g="${g.id}"><a href="#/g/${g.id}" class="${sel === g.id ? 'on' : ''}">` +
      `<span class="caret" data-toggle="${g.id}">${ch.length ? '▸' : ''}</span><span class="dot ${st}"></span>` +
      `<span class="name">${esc(g.name)}${g.hidden ? ' <span class="faint" title="Hidden from visitors">🔒</span>' : ''}</span><span class="cnt">${g._all.length}</span></a>` +
      (ch.length ? `<ul>${ch.map(node).join('')}</ul>` : '') + '</li>';
  };
  $('#nav').innerHTML =
    `<ul><li><a href="#/" class="${r.view === 'overview' ? 'on' : ''}"><span class="caret"></span><span class="name">Overview</span><span class="cnt">${S.data.targets.length}</span></a></li>` +
    `<li><a href="#/history" class="${r.view === 'history' ? 'on' : ''}"><span class="caret"></span><span class="dot ${S.data.active_alerts ? 'loss' : 'ok'}"></span><span class="name">History</span><span class="cnt">${S.data.active_alerts || 0}</span></a></li>` +
    `<li><a href="#/problems" class="${r.view === 'problems' ? 'on' : ''}"><span class="caret"></span><span class="dot ${problems ? 'down' : 'ok'}"></span><span class="name">Problems</span><span class="cnt">${problems}</span></a></li></ul>` +
    `<div class="sep"></div><ul>${kids(null).map(node).join('')}</ul>`;
}
$('#nav').addEventListener('click', e => {
  const tg = e.target.closest('[data-toggle]');
  if (tg && tg.textContent){
    e.preventDefault();
    const id = +tg.dataset.toggle, li = tg.closest('li');
    li.classList.toggle('open');
    li.classList.contains('open') ? S.expanded.add(id) : S.expanded.delete(id);
    store.set('ps_open', [...S.expanded]);
    return;
  }
  if (e.target.closest('a')) document.body.classList.remove('menu');
});
$('#menuBtn').onclick = () => document.body.classList.toggle('menu');

// ---------- charts (lazy) ----------
const io = new IntersectionObserver(es => es.forEach(e => {
  const cv = e.target; cv._vis = e.isIntersecting;
  if (e.isIntersecting && !cv._loaded) loadChart(cv);
}), {rootMargin: '400px'});

function chart(key, range, onPick, onData){
  const cv = document.createElement('canvas');
  cv.className = 'chart'; cv.dataset.key = key; cv.dataset.range = range; cv._onData = onData;
  cv._zoom = S.zoom[range] || null;
  hookChart(cv, onPick); io.observe(cv);
  requestAnimationFrame(() => draw(cv));
  return cv;
}
async function loadChart(cv){
  cv._loaded = Date.now();
  try {
    const z = cv._zoom ? `&start=${cv._zoom.start}&end=${cv._zoom.end}` : '';
    const d = await api(`api/series?key=${cv.dataset.key}&range=${cv.dataset.range}${z}&points=${Math.max(100, Math.min(2000, Math.round(cv.clientWidth || 600)))}`);
    draw(cv, d); cv._onData?.(d);
  } catch(e){ cv._loaded = 0; }
}
function clearCharts(){ $$('canvas.chart', main).forEach(c => io.unobserve(c)); }
setInterval(() => {
  const iv = (S.data?.interval || 60) * 1000;
  $$('canvas.chart', main).forEach(c => { if (c._vis && !c._zoom && Date.now() - (c._loaded || 0) > iv - 5000) loadChart(c); });
}, 10000);

// ---------- zoom: drag over a graph to look at that period (all graphs of the same range follow) ----------
function applyZoom(range){
  for (const c of $$('canvas.chart', main)){
    if (c.dataset.range !== range) continue;
    c._zoom = S.zoom[range] || null;
    if (c._vis) loadChart(c); else c._loaded = 0;
  }
  zoomBar();
}
PS.onZoom = (cv, start, end) => { S.zoom[cv.dataset.range] = {start, end}; applyZoom(cv.dataset.range); };
PS.onZoomReset = cv => { if (S.zoom[cv.dataset.range]){ delete S.zoom[cv.dataset.range]; applyZoom(cv.dataset.range); } };
const zoomLabel = z => `${fmtShort(z.start)} – ${z.end - z.start < 86400 && dayLabel(z.start) === dayLabel(z.end) ? hhmm(z.end) : fmtShort(z.end)}`;
function zoomBar(){
  let bar = $('#zoombar');
  const ranges = Object.keys(S.zoom);
  if (!ranges.length){ bar?.remove(); return; }
  if (!bar){ bar = document.createElement('div'); bar.id = 'zoombar'; main.prepend(bar); }
  const many = ranges.length > 1 || currentRoute().view === 'target';
  bar.innerHTML = ranges.map(r => `<div><span>🔍 Zoomed in${many ? ` (${esc(RANGES[r])})` : ''}: <b>${zoomLabel(S.zoom[r])}</b></span><button class="btn sm" data-unzoom="${r}">Reset zoom</button></div>`).join('');
  bar.onclick = e => { const b = e.target.closest('[data-unzoom]'); if (b){ delete S.zoom[b.dataset.unzoom]; applyZoom(b.dataset.unzoom); } };
}

// ---------- routing ----------
function currentRoute(){
  const h = location.hash.replace(/^#\/?/, '');
  let m;
  if ((m = h.match(/^g\/(\d+)/))) return {view: 'group', id: +m[1]};
  if ((m = h.match(/^t\/(\d+)/))) return {view: 'target', id: +m[1]};
  if (h.startsWith('problems')) return {view: 'problems'};
  if (h.startsWith('history')) return {view: 'history'};
  return {view: 'overview'};
}
function route(){
  clearCharts();
  S.zoom = {};
  const r = currentRoute();
  renderNav();
  // the period buttons only mean something where graphs are shown
  $('#range')?.classList.toggle('hidden', r.view !== 'group' && r.view !== 'target');
  if (r.view === 'group') viewGroup(r.id);
  else if (r.view === 'target') viewTarget(r.id);
  else if (r.view === 'history') viewHistory();
  else viewOverview(r.view === 'problems');
  document.title = (r.view === 'target' ? (S.targets.get(r.id)?.name + ' · ') : r.view === 'group' ? (S.groups.get(r.id)?.name + ' · ') : '') + S.data.site.title;
}
addEventListener('hashchange', () => { route(); scrollTo(0, 0); });

// ---------- overview ----------
function viewOverview(problemsOnly){
  const d = S.data;
  main.innerHTML = `<h1>${problemsOnly ? 'Problems' : esc(d.site.title)}</h1>` +
    (!problemsOnly && d.site.remark ? `<div class="muted">${esc(d.site.remark)}</div>` : '') +
    (problemsOnly ? '<div class="muted">Targets with packet loss or not reachable in the last round.</div>' : '') +
    `<div class="tiles" id="tiles"></div>
     <div class="toolbar"><div class="seg" id="sort"><button data-v="order">Group order</button><button data-v="problems">Worst first</button><button data-v="name">Name</button></div>
     <span class="muted small" id="rowcount"></span></div>
     <div class="tblwrap"><table class="tbl"><thead><tr><th>Target</th><th class="col-group">Group</th><th class="col-type">Type</th>
     <th class="c4 col-fam">IPv4</th><th class="c6 col-fam">IPv6</th><th class="col-both">Latency · loss</th></tr></thead><tbody id="rows"></tbody></table></div>
     <div class="legend" style="margin-top:12px">Click a row for graphs and traceroutes.</div>`;
  seg('#sort', () => S.sort, v => { S.sort = v; store.set('ps_sort', v); fillOverview(problemsOnly); });
  $('#rows').addEventListener('click', e => { const tr = e.target.closest('tr[data-t]'); if (tr) location.hash = '#/t/' + tr.dataset.t; });
  fillOverview(problemsOnly);
}
function fillOverview(problemsOnly){
  const d = S.data, all = d.targets;
  const series = all.flatMap(t => t.series);
  const cnt = st => series.filter(s => status(s) === st).length;
  const with6 = all.filter(t => famSeries(t, 6)).length;
  $('#tiles').innerHTML =
    `<div class="tile"><b>${all.length}</b><span>targets · ${series.length} series</span></div>` +
    `<div class="tile"><b style="color:var(--ok)">${cnt('ok')}</b><span>healthy</span></div>` +
    `<div class="tile"><b style="color:var(--warn)">${cnt('loss')}</b><span>with packet loss</span></div>` +
    `<div class="tile"><b style="color:var(--bad)">${cnt('down')}</b><span>down or unresolved</span></div>` +
    `<div class="tile"><b>${all.length ? Math.round(with6 / all.length * 100) : 0}%</b><span>IPv6 coverage · ${with6} of ${all.length}</span><div class="bar"><i style="width:${all.length ? with6 / all.length * 100 : 0}%"></i></div></div>`;
  let rows = (S.ordered || all).filter(matches);
  if (problemsOnly) rows = rows.filter(t => ['loss', 'down'].includes(tStatus(t)));
  const rank = {down: 3, loss: 2, unknown: 1, ok: 0};
  const maxLoss = t => Math.max(0, ...t.series.map(s => s.error ? 100 : s.last?.loss ?? 0));
  if (S.sort === 'problems' || problemsOnly) rows.sort((a, b) => rank[tStatus(b)] - rank[tStatus(a)] || maxLoss(b) - maxLoss(a));
  else if (S.sort === 'name') rows.sort((a, b) => a.name.localeCompare(b.name));
  const fams = visibleFams();
  $$('.c4').forEach(e => e.classList.toggle('hidden', !fams.includes(4)));
  $$('.c6').forEach(e => e.classList.toggle('hidden', !fams.includes(6)));
  $('#rowcount').textContent = `${rows.length} target${rows.length === 1 ? '' : 's'}`;
  $('#rows').innerHTML = rows.length ? rows.map(t =>
    `<tr class="click" data-t="${t.id}"><td><div class="tname">${esc(t.name)} <span class="mobile-only">${probeBadge(t.probe)}</span></div>` +
    (t.host || t.host_v6 ? `<div class="tpath mono">${esc(t.host || t.host_v6)}</div>` : `<div class="tpath mobile-only">${esc(pathText(t.group_id))}</div>`) + '</td>' +
    `<td class="col-group tpath">${esc(pathText(t.group_id))}</td><td class="col-type">${probeBadge(t.probe)}</td>` +
    fams.map(f => `<td class="col-fam">${valHTML(famSeries(t, f))}</td>`).join('') +
    `<td class="col-both">${fams.map(f => `<div class="small"><span class="faint">v${f}</span> ${valHTML(famSeries(t, f))}</div>`).join('')}</td></tr>`).join('')
    : `<tr><td colspan="5" class="empty">${problemsOnly ? 'No problems right now. 🎉' : S.q ? 'No targets match your search.' : 'No targets yet. Add them in the admin panel.'}</td></tr>`;
}

// ---------- group page ----------
// ---------- alert history ----------
const fmtDur = s => s < 3600 ? `${Math.max(1, Math.round(s / 60))} min` : s < 86400 ? `${Math.floor(s / 3600)} h ${Math.round(s % 3600 / 60)} min` : `${Math.floor(s / 86400)} d ${Math.floor(s % 86400 / 3600)} h`;
const dayLabel = t => PS.fmtTime(t, {weekday: 'long', day: 'numeric', month: 'long'});
const hhmm = t => PS.fmtTime(t, {hour: '2-digit', minute: '2-digit'});
let histSeq = 0;
async function viewHistory(){
  const days = store.get('ps_hdays', 7), seq = ++histSeq;
  if (!$('#hbody')){
    main.innerHTML = `<h1>Alert history</h1>
      <div class="muted">Packet loss and outages that triggered an alert, for groups with notifications on.</div>
      <div class="toolbar" style="margin-top:14px"><div class="seg" id="hdays">${[[1, '24 hours'], [7, '7 days'], [30, '30 days'], [90, '90 days']].map(([v, l]) => `<button data-v="${v}">${l}</button>`).join('')}</div><span class="muted small" id="hcount"></span></div>
      <div class="tiles" id="htiles"></div><div id="hbody"><div class="empty">Loading…</div></div>`;
    seg('#hdays', () => String(store.get('ps_hdays', 7)), v => { store.set('ps_hdays', +v); viewHistory(); });
  }
  let r;
  try { r = await api(`api/history?days=${days}`); } catch(e){ $('#hbody').innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; return; }
  if (currentRoute().view !== 'history' || seq !== histSeq || !$('#hbody')) return;
  const q = S.q.toLowerCase().split(/\s+/).filter(Boolean);
  const rows = r.alerts.filter(a => q.every(w => `${a.name} ${a.group}`.toLowerCase().includes(w)));
  const open = rows.filter(a => !a.ended), done = rows.filter(a => a.ended);
  const dur = done.reduce((n, a) => n + (a.ended - a.started), 0);
  $('#htiles').innerHTML =
    `<div class="tile"><b style="color:${open.length ? 'var(--bad)' : 'var(--ok)'}">${open.length}</b><span>active now</span></div>` +
    `<div class="tile"><b>${rows.length}</b><span>alerts in ${r.days === 1 ? '24 hours' : r.days + ' days'}</span></div>` +
    `<div class="tile"><b>${new Set(rows.map(a => a.target_id)).size}</b><span>targets affected</span></div>` +
    `<div class="tile"><b style="color:var(--bad)">${rows.filter(a => a.kind === 'down').length}</b><span>outages (down)</span></div>` +
    `<div class="tile"><b>${done.length ? fmtDur(dur / done.length) : '–'}</b><span>average duration</span></div>`;
  const kind = a => a.kind === 'down' ? 'down' : 'packet loss';
  const row = a => `<tr>
      <td><span class="hkind ${a.ended ? 'ok' : a.kind}">${a.ended ? 'resolved' : kind(a)}</span>${a.ended ? `<div class="faint small">was ${kind(a)}</div>` : ''}</td>
      <td><a href="#/t/${a.target_id}"><b>${esc(a.name)}</b></a> <span class="badge">IPv${a.family}</span><div class="muted small">${esc(a.group)}</div></td>
      <td class="small" style="white-space:nowrap">${a.ended && dayLabel(a.started) !== dayLabel(a.ended) ? fmtShort(a.started) : hhmm(a.started)} → ${a.ended ? (dayLabel(a.started) !== dayLabel(a.ended) ? fmtShort(a.ended) : hhmm(a.ended)) : '<b>now</b>'}<div class="muted">${fmtDur((a.ended || r.now) - a.started)}</div></td>
      <td class="small">worst ${a.peak_loss == null ? '–' : a.peak_loss.toFixed(0) + '%'}${!a.ended && a.last_loss != null ? `<div class="muted">now ${a.last_loss.toFixed(0)}%</div>` : ''}</td></tr>`;
  const table = list => `<div class="tblwrap"><table class="tbl htbl"><thead><tr><th>Status</th><th>Target</th><th>Time</th><th>Loss</th></tr></thead><tbody>${list.map(row).join('')}</tbody></table></div>`;
  let html = open.length ? `<h3>Active now</h3>${table(open)}` : '';
  const byDay = new Map();
  for (const a of done){ const k = dayLabel(a.started); if (!byDay.has(k)) byDay.set(k, []); byDay.get(k).push(a); }
  for (const [k, list] of byDay) html += `<h3>${esc(k)} <span class="muted small" style="text-transform:none;letter-spacing:0">· ${list.length} alert${list.length === 1 ? '' : 's'}</span></h3>${table(list)}`;
  $('#hbody').innerHTML = html || `<div class="empty">${S.q ? 'No alerts match your search.' : 'No alerts in this period. 🎉'}</div>`;
  $('#hcount').textContent = S.q ? `${rows.length} of ${r.alerts.length} alerts` : '';
}
function viewGroup(gid){
  const g = S.groups.get(gid);
  if (!g){ main.innerHTML = '<div class="empty">Group not found.</div>'; return; }
  const path = gPath(gid);
  const sections = [];
  const walk = (x, depth) => {
    const ts = (S.byGroup.get(x.id) || []).filter(matches);
    if (ts.length) sections.push({g: x, ts, depth});
    kids(x.id).forEach(c => walk(c, depth + 1));
  };
  walk(g, 0);
  const shown = sections.reduce((n, s) => n + s.ts.length, 0);
  main.innerHTML =
    `<div class="crumbs"><a href="#/">Overview</a>${path.slice(0, -1).map(p => ` › <a href="#/g/${p.id}">${esc(p.name)}</a>`).join('')}</div>` +
    `<h1>${esc(g.name)}</h1><div class="muted small gsub">${g._all.length} targets${kids(gid).length ? ` · ${kids(gid).length} subgroups` : ''} · ${RANGES[S.range].toLowerCase()}</div>` +
    (kids(gid).length ? `<div class="subs">${kids(gid).map(c => `<a class="chip" href="#/g/${c.id}"><span class="dot ${worst(c._all.flatMap(t => t.series.map(status)))}"></span>${esc(c.name)} <span class="faint">${c._all.length}</span></a>`).join('')}</div>` : '') +
    '<div id="secs"></div><div class="legend" style="margin-top:8px">' + PS.legendHTML() + '</div>';
  const box = $('#secs');
  if (!shown){ box.innerHTML = `<div class="empty">${S.q ? 'No targets match your search.' : 'No targets in this group yet.'}</div>`; return; }
  for (const sec of sections){
    if (sec.g.id !== gid){
      const h = document.createElement('div'); h.className = 'section-h';
      h.innerHTML = `<h2>${esc(gPath(sec.g.id).slice(path.length).map(p => p.name).join(' › '))}</h2>`;
      box.appendChild(h);
    } else if (sections.length > 1){ const h = document.createElement('div'); h.style.height = '10px'; box.appendChild(h); }
    for (const t of sec.ts) box.appendChild(targetRow(t));
  }
}

function targetRow(t){
  const el = document.createElement('div'); el.className = 'trow'; el.dataset.t = t.id;
  el.innerHTML = `<div class="thead"><a class="tname" href="#/t/${t.id}">${esc(t.name)}</a>${probeBadge(t.probe)}` +
    (hostLabel(t) ? `<span class="host">${esc(hostLabel(t))}</span>${PS.copyBtn(t.host || t.host_v6)}` : '') + '</div>';
  let fams = visibleFams();
  if (fams.length === 2){
    const missing = fams.filter(f => !famSeries(t, f));
    if (missing.length === 1){
      fams = fams.filter(f => famSeries(t, f));
      el.querySelector('.thead').insertAdjacentHTML('beforeend', `<span class="fammiss" title="${esc(famBox(t, missing[0], '', false).textContent.replace(/^IPv\d/, '').replace('Add IPv6 address', '').trim())}">no IPv${missing[0]}</span>`);
    }
  }
  const grid = document.createElement('div'); grid.className = 'charts' + (fams.length === 1 ? ' single' : '');
  for (const f of fams) grid.appendChild(famBox(t, f, S.range, false));
  if (fams.length === 1){  // one graph: put its IPv4/IPv6 label and current value in the title row, saving a line
    const lab = grid.querySelector('.clabel'), head = el.querySelector('.thead');
    if (lab && head){
      const fam = lab.querySelector('.badge'); fam?.classList.add('fam');
      if (fam) head.querySelector('.tname').after(fam);
      const addr = lab.querySelector('.addr');  // the same IP as the host next to the name: show it once
      if (addr && [t.host, t.host_v6].includes(addr.textContent)){
        if (addr.nextElementSibling?.matches('button.copy')) addr.nextElementSibling.remove();
        addr.remove();
      }
      for (const x of [...lab.children]) head.appendChild(x);
      lab.remove();
    }
  }
  el.appendChild(grid);
  return el;
}
function hostLabel(t){
  let s = t.host || t.host_v6;
  if (!s) return '';
  if (t.port && t.probe !== 'ping') s += `  :${t.port}`;
  return s;
}

function famBox(t, f, range, big, onPick){
  const s = famSeries(t, f);
  const box = document.createElement('div'); box.className = 'cbox';
  if (!s){
    const priv = t.host !== undefined;
    const why = f === 6 ? (t.families === 'ipv4' ? 'Not measured over IPv6' : !priv ? 'Not measured over IPv6'
      : t.host && /^[\d.]+$/.test(t.host) ? 'No IPv6 address configured' : 'No AAAA record found')
      : (t.families === 'ipv6' || !priv ? 'Not measured over IPv4' : 'No IPv4 address');
    box.innerHTML = `<div class="clabel"><span class="badge">IPv${f}</span></div><div class="nofam"><span>${why}</span>` +
      (S.data.can_edit && f === 6 && t.families !== 'ipv4' ? `<a href="admin#edit=${t.id}">Add IPv6 address</a>` : '') + '</div>';
    return box;
  }
  box.innerHTML = `<div class="clabel"><span class="badge">IPv${f}</span>${s.address ? `<span class="addr">${esc(s.address)}</span>${PS.copyBtn(s.address)}` : ''}` +
    `<span class="now" data-now="${s.key}">${valHTML(s)}</span></div>` +
    (s.error ? `<div class="err">${esc(s.error)}</div>` : '');
  const title = document.createElement('div'); title.className = 'ctitle';
  title.textContent = `${RANGES[range]} · IPv${f} · ${t.name}`;
  box.appendChild(title);
  const cs = document.createElement('div'); cs.className = 'cstats';
  box.appendChild(chart(s.key, range, onPick, d => {
    cs.innerHTML = classicStats(d, probeLine(t));
    title.textContent = `${d.custom ? zoomLabel(d) : RANGES[range]} · IPv${f} · ${t.name}`;
  }));
  box.appendChild(cs);
  return box;
}
// like SmokePing: "20 ICMP Echo Pings (56 Bytes) every 60 seconds"
function probeLine(t){
  if (!t.pings) return '';
  const every = `every ${S.data.interval} seconds`;
  return t.probe === 'ping' ? `${t.pings} ICMP Echo Pings (${t.size} Bytes) ${every}`
    : t.probe === 'dns' ? `${t.pings} DNS Queries ${every}`
    : t.probe === 'tcp' ? `${t.pings} TCP Connects ${every}` : `${t.pings} probes ${every}`;
}

// ---------- target detail ----------
const MTR = {key: null, issues: false, runs: [], fam: null};
function viewTarget(tid){
  const t = S.targets.get(tid);
  if (!t){ main.innerHTML = '<div class="empty">Target not found (it may be disabled).</div>'; return; }
  const path = gPath(t.group_id);
  const s4 = famSeries(t, 4), s6 = famSeries(t, 6);
  main.innerHTML =
    `<div class="crumbs"><a href="#/">Overview</a>${path.map(p => ` › <a href="#/g/${p.id}">${esc(p.name)}</a>`).join('')}</div>` +
    `<div class="row" style="flex-wrap:wrap"><h1 style="margin:0">${esc(t.name)}</h1>${probeBadge(t.probe, S.data.probes[t.probe])}` +
    (S.data.can_edit ? `<a class="btn sm" href="admin#edit=${t.id}" style="margin-left:auto">Edit</a>` : '') + '</div>' +
    (t.host !== undefined ? `<div class="info">` +
      (t.host ? `<span>Host <b>${esc(t.host)}</b>${PS.copyBtn(t.host)}</span>` : '') +
      (s4 ? `<span>IPv4 <b>${esc(s4.address || 'unresolved')}</b>${PS.copyBtn(s4.address)}</span>` : '') +
      (s6 ? `<span>IPv6 <b>${esc(s6.address || 'unresolved')}</b>${PS.copyBtn(s6.address)}</span>` : '') +
      (t.port && t.probe !== 'ping' ? `<span>Port <b>${t.port}</b></span>` : '') + '</div>'
      : `<div class="info"><span>Measured over ${t.series.map(s => 'IPv' + s.family).join(' and ') || '–'}</span></div>`) +
    `<div id="ranges"></div>` +
    `<div class="card pad" id="mtrcard" style="margin-top:16px"></div>` +
    `<div class="legend" style="margin-top:12px">${PS.legendHTML()}</div>`;
  const fams = visibleFams();
  const box = $('#ranges');
  // the period picked in the header comes first, the other periods follow
  const order = [S.range, ...periods().filter(r => r !== S.range)];
  const bigCard = (f, r) => {
    const card = document.createElement('div'); card.className = 'card pad big';
    const fb = famBox(t, f, r, true, ts => showMtrAt(f, ts));
    card.appendChild(fb);
    const cv = fb.querySelector('canvas');
    if (cv){
      const st = document.createElement('div'); st.className = 'stats'; card.appendChild(st);
      const classic = cv._onData;
      cv._onData = d => { classic?.(d); const x = chartStats(d);
        st.innerHTML = `<div>Avg median<b>${fmtMs(x.avg)}</b></div><div>Min<b>${fmtMs(x.min)}</b></div><div>Max<b>${fmtMs(x.max)}</b></div>` +
          `<div>Jitter<b>${fmtMs(x.jit)}</b></div><div>Loss<b style="color:${x.loss == null ? 'inherit' : lossColor(x.loss)}">${x.loss == null ? '–' : x.loss.toFixed(2) + '%'}</b></div>`; };
      if (probeLine(t)) card.insertAdjacentHTML('beforeend', `<div class="pline">probe: ${esc(probeLine(t))}</div>`);
    }
    return card;
  };
  if (fams.length === 1){  // one family: every period side by side in two columns, like SmokePing's target page
    const grid = document.createElement('div'); grid.className = 'pgrid';
    for (const r of order){
      const card = bigCard(fams[0], r);
      card.insertAdjacentHTML('afterbegin', `<h3 class="${r === S.range && order.length > 1 ? 'chosen' : ''}">${esc(RANGES[r])}</h3>`);
      grid.appendChild(card);
    }
    box.appendChild(grid);
  } else for (const r of order){  // IPv4 and IPv6 next to each other, one period per row
    const h = document.createElement('h3'); h.textContent = RANGES[r]; box.appendChild(h);
    if (r === S.range && order.length > 1){ h.classList.add('chosen'); }
    if (r === order[1]){ h.insertAdjacentHTML('beforebegin', '<div class="muted small" style="margin:22px 0 -4px">Other periods</div>'); }
    const grid = document.createElement('div'); grid.className = 'rangegrid';
    for (const f of fams) grid.appendChild(bigCard(f, r));
    box.appendChild(grid);
  }
  renderMtrCard(t);
}

function renderMtrCard(t){
  const card = $('#mtrcard');
  const paths = t.series.filter(s => s.mtr && (S.fam === 'all' || s.family === +S.fam));
  if (!S.data.mtr){ card.innerHTML = '<h2>Traceroutes (mtr)</h2><div class="empty">mtr is disabled or not installed.</div>'; return; }
  if (!paths.length){ card.innerHTML = '<h2>Traceroutes (mtr)</h2><div class="empty">No address to trace.</div>'; return; }
  if (!paths.find(s => s.family === MTR.fam)) MTR.fam = paths[0].family;
  card.innerHTML = `<div class="mtrhead"><h2>Traceroutes (mtr)</h2><div class="row">` +
    (paths.length > 1 ? `<div class="seg" id="mtrfam">${paths.map(s => `<button data-v="${s.family}">IPv${s.family}</button>`).join('')}</div>` : '') +
    `<div class="seg" id="mtrfilter"><button data-v="0">All</button><button data-v="1">Problems only</button></div></div></div>` +
    `<p class="muted small" style="margin:6px 0 4px">Every ${Math.round((S.data.mtr_interval || 900) / 60)} minutes normally, every minute while there is packet loss or the target is down. ` +
    'The label shows why a traceroute ran (<b>loss 5%</b> = the measurement round before it lost 1 in 20 pings); the right side shows what the traceroute itself measured. ' +
    'Click a time to open the report, or click a point in a graph.</p>' +
    `<div id="mtrsel"></div><div id="mtrlist"></div><button class="btn" id="mtrmore" style="display:none;margin-top:8px">Load older</button>`;
  if (paths.length > 1) seg('#mtrfam', () => String(MTR.fam), v => { MTR.fam = +v; MTR.key = paths.find(s => s.family === +v).key; loadMtr(true); });
  seg('#mtrfilter', () => MTR.issues ? '1' : '0', v => { MTR.issues = v === '1'; loadMtr(true); });
  $('#mtrmore').onclick = () => loadMtr(false);
  MTR.key = paths.find(s => s.family === MTR.fam).key;
  loadMtr(true);
}

function mtrEl(r, open){
  const d = document.createElement('details'); d.className = 'mtr'; d.dataset.id = r.id;
  const last = r.hubs[r.hubs.length - 1];
  const why = r.reason === 'scheduled' ? 'Regular traceroute (no problems in the measurement round before it).'
    : `Started because the measurement round just before it had ${r.trigger_loss != null ? fmtLoss(r.trigger_loss) : 'some'} packet loss. ` +
      'The traceroute then measures again for a few seconds, so it can show no loss when the loss was brief.';
  const label = r.reason === 'loss' && r.trigger_loss != null ? `loss ${fmtLoss(r.trigger_loss)}` : r.reason;
  d.innerHTML = `<summary><span class="mt">${fmtDT(r.ts)}</span><span class="reason r-${r.reason}" title="${esc(why)}">${label}</span>` +
    (r.error ? '<span class="mm" style="color:var(--bad)">mtr failed</span>'
      : `<span class="mm"><span class="dot" style="background:${lossColor(r.loss ?? 100)}"></span>traceroute: ${r.loss == null ? '–' : Math.round(r.loss) + '%'} loss at destination · ${r.hops} hops · ${!last || last.host === '???' ? '–' : fmtMs(last.avg)}</span>`) + '</summary>';
  const fill = () => {
    if (d._f) return; d._f = 1;
    if (r.error){ d.insertAdjacentHTML('beforeend', `<div class="hint" style="margin-bottom:10px">mtr error: <code>${esc(r.error)}</code></div>`); return; }
    const rows = r.hubs.map(h => { const none = h.host === '???', masked = h.host === 'hidden' || h.host === 'destination';
      return `<tr class="${none ? 'nores' : ''}"><td>${h.n}</td><td${masked ? ' class="faint" style="font-family:inherit"' : ''}>${esc(h.host)}</td>` +
        `<td style="color:${h.loss > 0 ? lossColor(h.loss) : 'inherit'};font-weight:${h.loss > 0 ? 600 : 400}">${h.loss == null ? '–' : h.loss.toFixed(1) + '%'}</td><td>${h.snt}</td>` +
        ['last', 'avg', 'best', 'wrst', 'stdev'].map(k => `<td>${none || h[k] == null ? '–' : h[k].toFixed(1)}</td>`).join('') + '</tr>'; }).join('');
    d.insertAdjacentHTML('beforeend', `<div class="hops"><table><thead><tr><th>#</th><th>Host</th><th>Loss</th><th>Sent</th><th>Last</th><th>Avg</th><th>Best</th><th>Worst</th><th>StDev</th></tr></thead><tbody>${rows}</tbody></table>` +
      '<div class="hint">Loss at an intermediate hop with no loss at the hops after it is usually ICMP rate limiting on that router, not a real problem.</div></div>');
  };
  d.addEventListener('toggle', () => { if (d.open) fill(); });
  if (open){ fill(); d.open = true; }
  return d;
}
async function loadMtr(reset){
  const box = $('#mtrlist'); if (!box) return;
  if (reset){ MTR.runs = []; box.innerHTML = ''; $('#mtrsel').innerHTML = ''; }
  const before = MTR.runs.length ? MTR.runs[MTR.runs.length - 1].ts : 0;
  try {
    const d = await api(`api/mtr?key=${MTR.key}&limit=30${before ? '&before=' + before : ''}${MTR.issues ? '&only_issues=true' : ''}`);
    box.querySelector('.empty')?.remove();
    for (const r of d.runs){ MTR.runs.push(r); box.appendChild(mtrEl(r)); }
    $('#mtrmore').style.display = d.more ? '' : 'none';
    if (!MTR.runs.length) box.innerHTML = `<div class="empty">${MTR.issues ? 'No traceroutes during problems in the history.' : 'No traceroutes yet; the first one appears within a few minutes.'}</div>`;
  } catch(e){}
}
async function pollMtr(){
  const box = $('#mtrlist'); if (!box || !MTR.key) return;
  const since = MTR.runs.length ? MTR.runs[0].ts + 1 : 0;
  try {
    const d = await api(`api/mtr?key=${MTR.key}&limit=100${since ? '&since=' + since : ''}${MTR.issues ? '&only_issues=true' : ''}`);
    if (!d.runs.length) return;
    box.querySelector('.empty')?.remove();
    for (const r of d.runs.slice().reverse()){ MTR.runs.unshift(r); box.prepend(mtrEl(r)); }
  } catch(e){}
}
async function showMtrAt(fam, t){
  if (!S.data.mtr) return;
  if (MTR.fam !== fam){
    const tgt = S.targets.get(currentRoute().id), s = tgt && famSeries(tgt, fam);
    if (!s?.mtr) return;
    MTR.fam = fam; MTR.key = s.key;
    $$('#mtrfam button').forEach(b => b.classList.toggle('on', +b.dataset.v === fam));
    await loadMtr(true);
  }
  const sel = $('#mtrsel');
  try {
    const d = await api(`api/mtr?key=${MTR.key}&at=${Math.round(t)}`);
    sel.innerHTML = '';
    if (!d.runs.length) sel.innerHTML = `<div class="lbl" style="padding:8px 0">No traceroute within 30 minutes of ${fmtDT(t)}.</div>`;
    else {
      const r = d.runs[0], ex = $(`#mtrlist details[data-id="${r.id}"]`);
      if (ex){ ex.open = true; ex.scrollIntoView({behavior: 'smooth', block: 'start'}); return; }
      sel.innerHTML = `<div class="lbl">Closest traceroute to ${fmtDT(t)}</div>`;
      sel.appendChild(mtrEl(r, true));
    }
    sel.scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch(e){}
}

// ---------- updates ----------
function updateValues(){
  for (const el of $$('[data-now]', main)){
    const [tid] = el.dataset.now.split('-'); const t = S.targets.get(+tid);
    const s = t && t.series.find(x => x.key === el.dataset.now);
    el.innerHTML = valHTML(s);
  }
}
async function refresh(first){
  try {
    const d = await api('api/tree');
    S.data = d; index();
    const theme = d.site.theme || 'modern';
    if (document.documentElement.dataset.theme !== theme){
      document.documentElement.dataset.theme = theme;
      requestAnimationFrame(() => $$('canvas.chart').forEach(c => draw(c)));
    }
    $('#siteTitle').textContent = d.site.title;
    PS.setTimeZone(d.site.tz);
    const plist = periods().join(',');
    if (plist !== S.plist){
      S.plist = plist;
      if (!periods().includes(S.range)) S.range = periods().includes('1d') ? '1d' : periods()[0];
      $('#range').innerHTML = periods().map(r => `<button data-v="${r}">${SHORT[r]}</button>`).join('');
      bindRange();
    }
    $('#ver').innerHTML = esc(d.version || '') + (d.commit ? ` · <a href="https://github.com/joebafra/PingScope/commit/${esc(d.commit)}" target="_blank" rel="noopener noreferrer" title="Commit of the running version">${esc(d.commit)}</a>` : '');
    $('#status').textContent = d.last_run ? 'updated ' + ago(d.last_run) : 'waiting for first round…';
    $('#adminLink').textContent = d.can_edit ? 'Admin' : d.logged_in ? 'Account' : 'Log in';
    const sig = d.targets.map(t => t.id + ':' + t.series.map(s => s.key + (s.address || '')).join(',')).join('|') + '#' + d.groups.map(g => g.id + g.name + g.parent_id).join(',');
    const changed = sig !== S.sig; S.sig = sig;
    if (first || changed) return route();
    renderNav();
    const r = currentRoute();
    if (r.view === 'overview' || r.view === 'problems') fillOverview(r.view === 'problems');
    else if (r.view === 'history') viewHistory();
    else { updateValues(); if (r.view === 'target') pollMtr(); }
  } catch(e){
    if (e.status === 401){ location.href = 'admin?next=' + encodeURIComponent(location.hash); return; }
    $('#status').textContent = 'connection lost';
  }
}

function seg(sel, get, set){
  const el = $(sel); if (!el) return;
  const paint = () => el.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === get()));
  el.onclick = e => { const b = e.target.closest('button'); if (!b) return; set(b.dataset.v); paint(); };
  paint();
}
function bindRange(){
  seg('#range', () => S.range, v => {
    S.range = v; store.set('ps_range', v);
    const view = currentRoute().view;
    if (view === 'group') route();
    else if (view === 'target'){ const y = scrollY; route(); if (y > 0) $('#ranges')?.scrollIntoView({behavior: 'smooth', block: 'start'}); }
  });
}
bindRange();
seg('#fam', () => S.fam, v => { S.fam = v; store.set('ps_fam', v); route(); });
let st;
$('#search').addEventListener('input', e => {
  clearTimeout(st); st = setTimeout(() => {
    S.q = e.target.value.trim();
    const r = currentRoute();
    if (r.view === 'target' && S.q) location.hash = '#/';
    else if (r.view === 'overview' || r.view === 'problems') fillOverview(r.view === 'problems');
    else if (r.view === 'history') viewHistory();
    else route();
  }, 150);
});
addEventListener('keydown', e => { if (e.key === '/' && document.activeElement.tagName !== 'INPUT'){ e.preventDefault(); $('#search').focus(); } });

PS.schemeSwitch($('#scheme'));
refresh(true);
setInterval(refresh, 30000);
})();
