/* PingScope admin panel */
(() => {
const {$, $$, esc, api, sortTargets, toast, probeBadge, status, fmtMs, fmtLoss, fmtShort, ago, store} = PS;
const main = $('#main');
const A = {data: null, user: null, tab: 'targets', gid: store.get('psa_gid', 'all'), sel: new Set(), q: '',
           ipv6: null, preview: null, previewText: null};

// ---------- auth ----------
async function boot(){
  const s = await api('api/session');
  $('#siteTitle').textContent = s.site_title;
  if (s.setup_required) return renderSetup();
  if (!s.user) return renderLogin();
  A.user = s.user;
  start();
}
function authCard(title, sub, fields, btn, onSubmit){
  $('#tabs').classList.add('hidden');
  main.innerHTML = `<div class="auth"><div class="card"><h1>${title}</h1><p class="muted" style="margin:0 0 16px">${sub}</p>
    <form id="authf">${fields}<button class="btn primary" style="width:100%;justify-content:center;padding:9px">${btn}</button>
    <div class="inline-msg" id="authmsg" style="color:var(--bad)"></div></form></div></div>`;
  $('#authf').onsubmit = async e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    try { await onSubmit(fd); }
    catch(err){ $('#authmsg').textContent = err.message; }
  };
  $('#authf input').focus();
}
function afterLogin(r){
  const next = new URLSearchParams(location.search).get('next');
  if (next) location.href = './' + next;
  else if (r?.user?.role === 'viewer') location.href = './';  // viewers have nothing to do here
  else location.reload();
}
function renderLogin(){
  authCard('Log in', 'Log in to manage targets and settings.',
    `<label class="field"><span>Username</span><input name="username" autocomplete="username" required></label>
     <label class="field"><span>Password</span><input name="password" type="password" autocomplete="current-password" required></label>`,
    'Log in', async fd => { afterLogin(await api('api/login', {method: 'POST', body: fd})); });
}
function renderSetup(){
  authCard('Welcome to PingScope', 'Create the first admin account. The setup code is printed in the PingScope log (<code>docker logs pingscope</code>).',
    `<label class="field"><span>Setup code</span><input name="code" autocomplete="off" required style="text-transform:uppercase;font-family:var(--mono)"></label>
     <label class="field"><span>Username</span><input name="username" autocomplete="username" required></label>
     <label class="field"><span>Password <span class="faint">(min. 10 characters)</span></span><input name="password" type="password" autocomplete="new-password" minlength="10" required></label>`,
    'Create account', async fd => { await api('api/setup', {method: 'POST', body: fd}); afterLogin(); });
}
$('#logout').onclick = async () => { await api('api/logout', {method: 'POST'}); location.href = './'; };

async function start(){
  $('#who').textContent = A.user.username; $('#who').classList.remove('hidden');
  $('#logout').classList.remove('hidden');
  if (A.user.role !== 'admin') return viewAccount();
  $('#tabs').classList.remove('hidden');
  $('#tabs').onclick = e => { const b = e.target.closest('button'); if (b) location.hash = b.dataset.tab; };
  await load();
  route();
  addEventListener('hashchange', route);
  setInterval(async () => { if (A.tab === 'targets' && !$('#tdlg').open){ await load(); renderTargetsPane(); renderTree(); } }, 30000);
}
async function load(){
  try { A.data = await api('api/admin/data'); }
  catch(e){ if (e.status === 401) return renderLogin(); throw e; }
  A.groupMap = new Map(A.data.groups.map(g => [g.id, g]));
  A.kids = new Map();
  for (const g of A.data.groups){ if (!A.kids.has(g.parent_id)) A.kids.set(g.parent_id, []); A.kids.get(g.parent_id).push(g); }
  A.probeMap = Object.fromEntries(A.data.probes.map(p => [p.name, p]));
  if (A.gid !== 'all' && A.gid !== 'offline' && !A.groupMap.has(A.gid)) A.gid = 'all';
}
function route(){
  const h = location.hash.slice(1);
  let tab = h.split(/[=/]/)[0] || 'targets';
  if (h.startsWith('edit=')) tab = 'targets';
  if (!['targets', 'ipv6', 'import', 'alerts', 'settings', 'users'].includes(tab)) tab = 'targets';
  A.tab = tab;
  main.onclick = null; main.onchange = null;
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  ({targets: viewTargets, ipv6: viewIpv6, import: viewImport, alerts: viewAlerts, settings: viewSettings, users: viewUsers})[tab]();
  const m = h.match(/^edit=(\d+)/);
  if (m){
    const t = A.data.targets.find(x => x.id === +m[1]);
    if (t){ A.gid = t.group_id; renderTree(); renderTargetsPane(); openTarget(t); }
    history.replaceState(null, '', '#targets');
  }
}

// ---------- helpers ----------
const kids = gid => A.kids.get(gid) || [];
function gPath(gid){ const out = []; while (gid && A.groupMap.has(gid)){ const g = A.groupMap.get(gid); out.unshift(g); gid = g.parent_id; } return out; }
const pathText = gid => gPath(gid).map(g => g.name).join(' › ');
function descendants(gid){ const out = new Set(); const w = id => kids(id).forEach(c => { out.add(c.id); w(c.id); }); w(gid); return out; }
function groupOptions(selected, {exclude, top} = {}){
  const ex = exclude ? new Set([exclude, ...descendants(exclude)]) : new Set();
  let html = top ? `<option value="">(top level)</option>` : '';
  const walk = (pid, depth) => kids(pid).forEach(g => {
    if (ex.has(g.id)) return;
    html += `<option value="${g.id}" ${g.id === selected ? 'selected' : ''}>${'   '.repeat(depth)}${esc(g.name)}</option>`;
    walk(g.id, depth + 1);
  });
  walk(null, 0);
  return html;
}
function seriesHTML(t){
  const xs = A.data.series[t.id] || [];
  if (!t.enabled) return '<span class="faint small">disabled</span>';
  if (!xs.length) return '<span class="faint small">waiting</span>';
  return xs.map(s => {
    const st = status(s);
    const v = s.error && !s.address ? 'unresolved' : !s.last ? 'waiting' : s.last.loss >= 100 ? 'down' : fmtMs(s.last.median) + (s.last.loss > 0 ? ' · ' + fmtLoss(s.last.loss) : '');
    return `<span class="st" title="${esc(s.address || s.error || '')}"><span class="dot ${st}"></span>v${s.family} ${v}</span>`;
  }).join('');
}
function markErrors(form, errors){
  $$('.field', form).forEach(f => { f.classList.remove('err'); f.querySelector('.errmsg')?.remove(); });
  for (const [k, msg] of Object.entries(errors || {})){
    const f = $(`[data-f="${k}"]`, form); if (!f) continue;
    f.classList.add('err'); f.insertAdjacentHTML('beforeend', `<div class="errmsg">${esc(msg)}</div>`);
  }
}

// ---------- targets tab ----------
function viewTargets(){
  main.innerHTML = `${A.data.warnings.map(w => `<div class="warnbox">${esc(w)}</div>`).join('')}` +
    (!A.data.status.fping_available ? '<div class="warnbox">fping is not installed on this server; ping targets can\'t be measured.</div>' : '') +
    `<div class="split"><div class="card tree" id="gtree"></div><div id="tpane"></div></div>`;
  renderTree(); renderTargetsPane();
}
function renderTree(){
  const box = $('#gtree'); if (!box) return;
  const count = gid => { const ids = new Set([gid, ...descendants(gid)]); return A.data.targets.filter(t => ids.has(t.group_id)).length; };
  const node = g => `<li><a href="#" data-g="${g.id}" class="${A.gid === g.id ? 'on' : ''}"${g.hidden_effective ? ' title="Hidden from visitors"' : ''}><span class="name">${esc(g.name)}${g.hidden ? ' <span class="faint" aria-label="hidden">🔒</span>' : g.hidden_effective ? ' <span class="faint">(hidden)</span>' : ''}${g.alert_mode === 'on' ? ' <span title="Notifications on" aria-label="notifications on">🔔</span>' : ''}</span><span class="cnt">${count(g.id)}</span></a>` +
    (kids(g.id).length ? `<ul>${kids(g.id).map(node).join('')}</ul>` : '') + '</li>';
  box.innerHTML = `<div class="row" style="padding:2px 4px 8px"><b class="grow">Groups</b><button class="btn sm" id="addTop">+ Group</button></div>` +
    `<ul><li><a href="#" data-g="all" class="${A.gid === 'all' ? 'on' : ''}"><span class="name">All targets</span><span class="cnt">${A.data.targets.length}</span></a></li>` +
    `<li><a href="#" data-g="offline" class="${A.gid === 'offline' ? 'on' : ''}"><span class="name"><span class="dot ${offlineCount() ? 'down' : 'ok'}" style="margin-right:6px"></span>Offline</span><span class="cnt">${offlineCount()}</span></a></li>` +
    `${kids(null).map(node).join('')}</ul>` +
    (A.data.groups.length ? '' : '<p class="muted small" style="padding:4px 8px">No groups yet. Create one, or import your SmokePing config under <a href="#import">Import &amp; export</a>.</p>');
  box.onclick = e => {
    const a = e.target.closest('a[data-g]'); if (!a) return;
    e.preventDefault();
    A.gid = ['all', 'offline'].includes(a.dataset.g) ? a.dataset.g : +a.dataset.g; store.set('psa_gid', A.gid); A.sel.clear();
    renderTree(); renderTargetsPane();
  };
  $('#addTop').onclick = () => openGroup(null, null);
  // drag targets onto a group to move them
  $$('a[data-g]', box).forEach(a => {
    if (a.dataset.g === 'all' || a.dataset.g === 'offline') return;
    a.addEventListener('dragover', e => { e.preventDefault(); a.classList.add('drop'); });
    a.addEventListener('dragleave', () => a.classList.remove('drop'));
    a.addEventListener('drop', async e => {
      e.preventDefault(); a.classList.remove('drop');
      const ids = JSON.parse(e.dataTransfer.getData('text/plain') || '[]');
      if (ids.length) await bulk('move', ids, {group_id: +a.dataset.g});
    });
  });
}
function paneTargets(){
  let ts = A.data.targets;
  if (A.gid !== 'all') ts = sortTargets(ts.filter(t => t.group_id === A.gid), A.groupMap.get(A.gid)?.sort_mode);
  if (A.q){
    const words = A.q.toLowerCase().split(/\s+/);
    ts = A.data.targets.filter(t => { const hay = [t.name, t.host, t.host_v6, t.dns_query, t.note, pathText(t.group_id)].join(' ').toLowerCase(); return words.every(w => hay.includes(w)); });
  }
  return ts;
}
function isOffline(t){
  const xs = A.data.series[t.id] || [];
  return t.enabled && xs.length > 0 && xs.every(s => status(s) === 'down');
}
const offlineCount = () => A.data.targets.filter(isOffline).length;

function fmtDuration(sec){
  if (sec < 3600) return Math.max(1, Math.round(sec / 60)) + ' min';
  if (sec < 86400) return Math.round(sec / 3600) + ' h';
  return Math.round(sec / 86400) + ' days';
}

async function renderOfflinePane(){
  const pane = $('#tpane'); if (!pane) return;
  const minDays = +store.get('psa_offmin', 0);
  pane.innerHTML = `<div class="phead"><div style="margin-right:auto"><h2 style="margin:0">Offline targets</h2>
      <div class="muted small">Enabled targets that got no reply at all in the last round (all address families), or whose hostname no longer resolves.</div></div>
      <label class="row small muted" style="gap:6px">Offline for at least
        <select id="offMin" style="width:auto">${[[0, 'any time'], [1 / 24, '1 hour'], [1, '1 day'], [7, '7 days'], [30, '30 days']].map(([v, l]) => `<option value="${v}" ${v === minDays ? 'selected' : ''}>${l}</option>`).join('')}</select></label></div>
    <div id="bulkbar"></div><div id="offbody" class="muted">Loading…</div>`;
  $('#offMin').onchange = e => { store.set('psa_offmin', +e.target.value); renderOfflinePane(); };
  let r;
  try { r = await api('api/admin/offline'); } catch(e){ $('#offbody').textContent = e.message; return; }
  if (A.gid !== 'offline') return;
  const byId = new Map(A.data.targets.map(t => [t.id, t]));
  const rows = r.targets.filter(o => byId.has(o.id) && (r.now - (o.since || r.now)) >= minDays * 86400);
  A.offlineIds = rows.map(o => o.id);
  $('#offbody').outerHTML = `<div class="tblwrap"><table class="tbl ttbl"><thead><tr><th style="width:28px"><input type="checkbox" id="selAll"></th><th>Name</th><th class="hide-sm">Group</th><th>Type</th><th class="hide-sm">Host</th><th>Offline since</th><th>Reason</th><th></th></tr></thead><tbody>` +
    (rows.length ? rows.map(o => { const t = byId.get(o.id);
      return `<tr data-t="${t.id}"><td><input type="checkbox" class="sel" ${A.sel.has(t.id) ? 'checked' : ''}></td>
        <td class="nm"><a href="#" class="tedit"><b>${esc(t.name)}</b></a>${t.alert_skip ? ' <span title="No alerts for this target">🔕</span>' : ''}</td>
        <td class="hide-sm small muted grp">${esc(pathText(t.group_id))}</td><td>${probeBadge(t.probe)}</td>
        <td class="hide-sm mono small wrap">${esc(t.host || t.host_v6)}</td>
        <td class="nowrap">${o.never ? `<span style="color:var(--bad)">never replied</span>${o.since ? `<div class="faint small">added ${fmtShort(o.since)}</div>` : ''}`
          : `<b style="color:var(--bad)">${fmtDuration(r.now - o.since)}</b><div class="faint small">since ${fmtShort(o.since)}</div>`}</td>
        <td class="small">${o.reason === 'unresolved' ? 'hostname does not resolve' : 'no reply'}</td>
        <td class="acts"><button class="btn sm tedit">Edit</button></td></tr>`; }).join('')
      : `<tr><td colspan="8" class="empty">${minDays ? 'No targets offline for that long.' : 'All targets are online. 🎉'}</td></tr>`) +
    '</tbody></table></div><p class="muted small">Tip: select targets and use <b>Delete</b> or <b>Disable</b> above the list. Disabled targets keep their history and stop being measured.</p>';
  renderBulk();
  const sa = $('#selAll');
  sa.checked = rows.length > 0 && rows.every(o => A.sel.has(o.id));
  sa.onchange = e => {
    rows.forEach(o => e.target.checked ? A.sel.add(o.id) : A.sel.delete(o.id));
    $$('#tpane tbody .sel').forEach(c => { c.checked = e.target.checked; });
    renderBulk();
  };
  pane.querySelector('tbody').onclick = e => {
    const tr = e.target.closest('tr[data-t]'); if (!tr) return;
    const t = byId.get(+tr.dataset.t);
    if (e.target.classList.contains('sel')){ e.target.checked ? A.sel.add(t.id) : A.sel.delete(t.id); renderBulk(); return; }
    if (e.target.closest('.tedit')){ e.preventDefault(); openTarget(t); }
  };
}

function renderTargetsPane(){
  if (A.gid === 'offline') return renderOfflinePane();
  const pane = $('#tpane'); if (!pane) return;
  const g = A.gid === 'all' ? null : A.groupMap.get(A.gid);
  const ts = paneTargets();
  const showGroup = A.gid === 'all' || A.q;
  const focus = document.activeElement?.id === 'tq';
  pane.innerHTML = `<div class="phead"><div style="margin-right:auto;min-width:0">` +
      (g ? `<div class="muted small">${esc(gPath(g.id).slice(0, -1).map(x => x.name).join(' › ') || 'Top level')}</div>` : '') +
      `<h2 style="margin:0">${g ? esc(g.name) : 'All targets'}</h2>` +
      (g ? `<div class="muted small">${g.hidden_effective ? '🔒 Hidden from visitors · ' : ''}${alertSummary(g)} · ${g.sort_mode === 'name' ? 'Targets sorted by name (A → Z)' : 'Manual order'} · <a href="#" id="gSort">${g.sort_mode === 'name' ? 'switch to manual' : 'sort A → Z'}</a></div>` : '') + '</div>' +
      (g ? `<button class="btn" id="gEdit">Edit group</button><button class="btn" id="gSub">+ Subgroup</button>` : '') +
      `<button class="btn primary" id="tAdd" ${A.data.groups.length ? '' : 'disabled'}>+ Add target</button></div>` +
    `<div class="row" style="margin-bottom:10px"><input id="tq" type="search" placeholder="Search all targets…" value="${esc(A.q)}"></div>` +
    `<div id="bulkbar"></div>` +
    `<div class="tblwrap"><table class="tbl ttbl"><thead><tr><th style="width:28px"><input type="checkbox" id="selAll"></th><th>Name</th>` +
      (showGroup ? '<th class="hide-sm">Group</th>' : '') +
      `<th>Type</th><th class="hide-sm">Host</th><th class="hide-sm hide-md">IPv6</th><th>Status</th><th></th></tr></thead><tbody>` +
    (ts.length ? ts.map((t, i) => `<tr data-t="${t.id}" draggable="true" class="${t.enabled ? '' : 'off'}">
        <td><input type="checkbox" class="sel" ${A.sel.has(t.id) ? 'checked' : ''}></td>
        <td class="nm"><a href="#" class="tedit"><b>${esc(t.name)}</b></a>${t.alert_skip ? ' <span title="No alerts for this target">🔕</span>' : ''}${t.note ? `<div class="faint small">${esc(t.note.slice(0, 80))}</div>` : ''}</td>
        ${showGroup ? `<td class="hide-sm small muted grp">${esc(pathText(t.group_id))}</td>` : ''}
        <td>${probeBadge(t.probe)}</td>
        <td class="hide-sm mono small wrap">${esc(t.host)}${t.port && t.probe !== 'ping' ? `<span class="faint">:${t.port}</span>` : ''}</td>
        <td class="hide-sm hide-md mono small wrap v6">${t.families === 'ipv4' ? '<span class="faint">off</span>' : t.host_v6 ? esc(t.host_v6) : (A.data.series[t.id] || []).some(x => x.family === 6) ? '<span class="faint">via AAAA</span>' : t.host && !/^[\d.]+$/.test(t.host) ? '<span class="faint">no AAAA</span>' : '<span class="faint">—</span>'}</td>
        <td>${seriesHTML(t)}</td>
        <td class="acts">${!showGroup && g?.sort_mode !== 'name' ? `<button class="btn ghost up" title="Move up" ${i ? '' : 'disabled'}>↑</button><button class="btn ghost down" title="Move down" ${i < ts.length - 1 ? '' : 'disabled'}>↓</button>` : ''}<button class="btn sm tedit">Edit</button></td></tr>`).join('')
      : `<tr><td colspan="8" class="empty">${A.q ? 'No targets match.' : g ? 'No targets in this group yet.' : 'No targets yet.'}</td></tr>`) +
    '</tbody></table></div><p class="muted small">Tip: drag targets onto a group to move them.</p>';
  renderBulk();
  const q = $('#tq'); if (focus){ q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  q.oninput = () => { A.q = q.value.trim(); renderTargetsPane(); };
  $('#tAdd').onclick = () => openTarget(null, g ? g.id : (A.data.groups[0] && A.data.groups[0].id));
  if (g){
    $('#gEdit').onclick = () => openGroup(g); $('#gSub').onclick = () => openGroup(null, g.id);
    $('#gSort').onclick = async e => {
      e.preventDefault();
      try { await api(`api/admin/groups/${g.id}`, {method: 'PUT', body: {sort_mode: g.sort_mode === 'name' ? 'manual' : 'name'}}); await load(); renderTargetsPane(); }
      catch(err){ toast(err.message, true); }
    };
  }
  $('#selAll').onchange = e => { ts.forEach(t => e.target.checked ? A.sel.add(t.id) : A.sel.delete(t.id)); renderTargetsPane(); };
  pane.querySelector('tbody').onclick = async e => {
    const tr = e.target.closest('tr[data-t]'); if (!tr) return;
    const t = A.data.targets.find(x => x.id === +tr.dataset.t);
    if (e.target.classList.contains('sel')){ e.target.checked ? A.sel.add(t.id) : A.sel.delete(t.id); renderBulk(); return; }
    if (e.target.closest('.tedit')){ e.preventDefault(); openTarget(t); return; }
    const mv = e.target.closest('.up,.down');
    if (mv){
      const ids = ts.map(x => x.id), i = ids.indexOf(t.id), j = i + (mv.classList.contains('up') ? -1 : 1);
      [ids[i], ids[j]] = [ids[j], ids[i]];
      await api('api/admin/reorder', {method: 'POST', body: {kind: 'target', ids}});
      await load(); renderTargetsPane();
    }
  };
  $$('tr[data-t]', pane).forEach(tr => tr.addEventListener('dragstart', e => {
    const id = +tr.dataset.t, ids = A.sel.has(id) ? [...A.sel] : [id];
    e.dataTransfer.setData('text/plain', JSON.stringify(ids)); e.dataTransfer.effectAllowed = 'move';
  }));
}
function renderBulk(){
  const box = $('#bulkbar'); if (!box) return;
  if (!A.sel.size){ box.innerHTML = ''; return; }
  box.innerHTML = `<div class="bulk"><b>${A.sel.size} selected</b>
    <select id="bulkGroup"><option value="">Move to group…</option>${groupOptions(null)}</select>
    <button class="btn sm" data-a="enable">Enable</button><button class="btn sm" data-a="disable">Disable</button>
    <button class="btn sm" data-a="alerts_off">🔕 No alerts</button><button class="btn sm" data-a="alerts_on">🔔 Alerts on</button>
    <button class="btn sm danger" data-a="delete">Delete</button><button class="btn sm ghost" data-a="clear">Clear selection</button></div>`;
  $('#bulkGroup').onchange = e => e.target.value && bulk('move', [...A.sel], {group_id: +e.target.value});
  box.onclick = e => {
    const a = e.target.closest('[data-a]')?.dataset.a; if (!a) return;
    if (a === 'clear'){ A.sel.clear(); renderTargetsPane(); return; }
    if (a === 'delete' && !confirm(`Delete ${A.sel.size} target(s) and their measurement history?`)) return;
    bulk(a, [...A.sel]);
  };
}
async function bulk(action, ids, extra = {}){
  try {
    await api('api/admin/targets/bulk', {method: 'POST', body: {action, ids, ...extra}});
    toast({move: 'Moved', enable: 'Enabled', disable: 'Disabled', delete: 'Deleted', alerts_off: 'Alerts off for', alerts_on: 'Alerts on for'}[action] + ` ${ids.length} target(s)`);
    if (action === 'delete' || action === 'move') A.sel.clear();
    await load(); renderTree(); renderTargetsPane();
  } catch(e){ toast(e.message, true); }
}

// ---------- target dialog ----------
const tdlg = $('#tdlg'), tform = $('#tform');
let editing = null;
function openTarget(t, gid){
  editing = t;
  const p = t || {name: '', probe: 'ping', host: '', host_v6: '', families: 'auto', dns_query: '', dns_type: 'A', port: '', count: '', mtr: true, enabled: true, alert_skip: false, alert_chat_ids: '', alert_rounds: null, note: '', group_id: gid};
  $('#tdlgTitle').textContent = t ? 'Edit target' : 'Add target';
  tform.group_id.innerHTML = groupOptions(p.group_id);
  tform.probe.innerHTML = A.data.probes.map(x => `<option value="${x.name}">${esc(x.label)}</option>`).join('');
  tform.families.innerHTML = Object.entries(A.data.families).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
  tform.dns_type.innerHTML = A.data.dns_types.map(x => `<option>${x}</option>`).join('');
  for (const k of ['name', 'probe', 'host', 'host_v6', 'families', 'dns_query', 'dns_type', 'note']) tform[k].value = p[k] ?? '';
  tform.port.value = p.port ?? ''; tform.count.value = p.count ?? '';
  tform.mtr.checked = !!p.mtr; tform.enabled.checked = !!p.enabled; tform.alert_skip.checked = !!p.alert_skip; tform.alert_rounds.value = p.alert_rounds ?? '';
  chatPicker('#tgPick', tform.alert_chat_ids, p.alert_chat_ids, A.groupMap.get(p.group_id)?.alert_effective?.chats || []);
  $('#tgTest').onclick = async () => {
    const chats = pickedChats('#tgPick', tform.alert_chat_ids, true);
    if (!chats) return toast('Select a contact or enter a chat ID first', true);
    try { const x = await api('api/admin/alerts/test-alert', {method: 'POST', body: {target_id: editing?.id, chat_ids: chats}}); toast(`Test alert sent: ${x.sent.join(', ')}` + (x.errors.length ? ` — failed: ${x.errors.join('; ')}` : ''), !!x.errors.length); }
    catch(err){ toast(err.message, true); }
  };
  $('#tDelete').classList.toggle('hidden', !t);
  $('#testRes').innerHTML = ''; $('#lookupRes').innerHTML = '';
  markErrors(tform, {});
  probeChanged();
  tdlg.showModal();
  (t ? tform.host : tform.name).focus();
}
function probeChanged(){
  const pr = A.probeMap[tform.probe.value];
  $('#probeHelp').textContent = pr.description;
  $('#dnsFields').classList.toggle('hidden', !pr.fields.includes('dns_query'));
  $('#portField').classList.toggle('hidden', !pr.fields.includes('port'));
  tform.port.placeholder = pr.default_port || '';
  const key = {ping: 'ping_count', dns: 'dns_count', tcp: 'tcp_count'}[pr.name];
  tform.count.placeholder = key ? `default: ${A.data.settings[key]}` : '';
}
tform.probe.onchange = probeChanged;
function formData(){
  const fd = Object.fromEntries(new FormData(tform));
  fd.mtr = tform.mtr.checked; fd.enabled = tform.enabled.checked; fd.alert_skip = tform.alert_skip.checked; fd.alert_chat_ids = pickedChats('#tgPick', tform.alert_chat_ids); fd.alert_rounds = tform.alert_rounds.value;
  fd.group_id = +fd.group_id;
  return fd;
}
tform.addEventListener('submit', async e => {
  if (e.submitter?.value !== 'save') return;
  e.preventDefault();
  const fd = formData();
  try {
    if (editing) await api(`api/admin/targets/${editing.id}`, {method: 'PUT', body: fd});
    else await api('api/admin/targets', {method: 'POST', body: fd});
    tdlg.close(); toast(editing ? 'Target saved' : 'Target added — first results within a minute');
    await load(); renderTree(); renderTargetsPane();
  } catch(err){ markErrors(tform, err.errors); if (!err.errors) toast(err.message, true); }
});
$('#tDelete').onclick = async () => {
  if (!editing || !confirm(`Delete "${editing.name}" and its measurement history?`)) return;
  tdlg.close(); await bulk('delete', [editing.id]);
};
$('#tTest').onclick = async () => {
  const box = $('#testRes'); box.innerHTML = '<span class="muted">Testing…</span>';
  markErrors(tform, {});
  try {
    const r = await api('api/admin/test', {method: 'POST', body: formData()});
    box.innerHTML = r.results.length ? r.results.map(x => {
      const ok = x.sent - x.lost;
      const st = !x.address ? 'down' : ok === 0 ? 'down' : x.lost ? 'loss' : 'ok';
      return `<div><span class="dot ${st}"></span><b>IPv${x.family}</b><span class="mono small">${esc(x.address || '')}</span>` +
        (x.address ? `<span>${ok}/${x.sent} replies${x.median != null ? ` · median ${fmtMs(x.median)} (min ${fmtMs(x.min)}, max ${fmtMs(x.max)})` : ''}</span>` : `<span class="muted">${esc(x.error || 'not resolved')}</span>`) + '</div>';
    }).join('') : '<span class="muted">Nothing to measure: no address for the selected families.</span>';
  } catch(err){ markErrors(tform, err.errors); box.innerHTML = `<span style="color:var(--bad)">${esc(err.message)}</span>`; }
};
$('#lookupBtn').onclick = async () => {
  const host = tform.host.value.trim(), name = tform.name.value.trim();
  const isIp = s => /^[\d.]+$/.test(s) || s.includes(':');
  const cands = [host, ...name.split(/[\s,()]+/)].filter(s => s && !isIp(s) && s.includes('.'));
  const box = $('#lookupRes');
  if (!cands.length){ box.innerHTML = '<span class="muted small">Enter a hostname as host or name to look up.</span>'; return; }
  box.innerHTML = '<span class="muted small">Looking up…</span>';
  const found = [];
  for (const n of [...new Set(cands)]){
    try { const r = await api('api/admin/resolve', {method: 'POST', body: {name: n}}); r.aaaa.forEach(a => found.push({a, n})); } catch(e){}
  }
  box.innerHTML = found.length ? found.map(f => `<button type="button" data-a="${esc(f.a)}" title="AAAA of ${esc(f.n)}">${esc(f.a)}</button>`).join('') +
    `<span class="muted small">AAAA of ${esc([...new Set(found.map(f => f.n))].join(', '))} — click to use</span>`
    : `<span class="muted small">No AAAA record found for ${esc(cands.join(', '))}.</span>`;
  box.onclick = e => { const b = e.target.closest('button[data-a]'); if (b){ tform.host_v6.value = b.dataset.a; } };
};

// Telegram chat picker: saved contacts as checkboxes + a field for other IDs.
// inherited = chats that already come from (parent) groups: shown ticked and locked.
function chatPicker(box, input, value, inherited = []){
  const ids = String(value || '').split(/[\s,;]+/).filter(Boolean), cts = A.data.contacts || [];
  const known = new Set(cts.map(c => c.chat_id)), inh = new Set(inherited);
  $(box).innerHTML = cts.length ? cts.map(c => {
    const via = inh.has(c.chat_id) && !ids.includes(c.chat_id);
    return `<label title="${esc(c.chat_id)}"><input type="checkbox" value="${esc(c.chat_id)}" ${ids.includes(c.chat_id) || via ? 'checked' : ''} ${via ? 'disabled data-via="1"' : ''}> ${esc(c.label)}${via ? ' <span class="faint small">(via group)</span>' : c.is_default ? ' <span class="faint small">(gets all alerts)</span>' : ''}</label>`;
  }).join('') : '<span class="muted small">No saved Telegram contacts yet — add them under Admin → Alerts, or enter chat IDs below.</span>';
  const other = [...inh].filter(i => !known.has(i));
  if (other.length) $(box).insertAdjacentHTML('beforeend', `<span class="faint small">via group: ${other.map(esc).join(', ')}</span>`);
  input.value = ids.filter(i => !known.has(i)).join(', ');
}
function pickedChats(box, input, withInherited){
  const picked = $$(`${box} input:checked`).filter(i => withInherited || !i.dataset.via).map(i => i.value);
  return [...new Set([...picked, ...input.value.split(/[\s,;]+/).filter(Boolean)])].join(', ');
}

// ---------- group dialog ----------
const gdlg = $('#gdlg'), gform = $('#gform');
let gEditing = null;
function openGroup(g, parentId){
  gEditing = g;
  $('#gdlgTitle').textContent = g ? 'Edit group' : 'New group';
  gform.name.value = g ? g.name : '';
  gform.parent_id.innerHTML = groupOptions(g ? g.parent_id : parentId, {exclude: g?.id, top: true});
  gform.parent_id.value = (g ? g.parent_id : parentId) ?? '';
  gform.sort_mode.value = g ? (g.sort_mode || 'manual') : (A.groupMap.get(parentId)?.sort_mode || 'manual');
  gform.sort_subgroups.checked = false;
  gform.hidden.checked = !!(g && g.hidden);
  gform.alert_mode.value = g ? g.alert_mode : 'inherit';
  gform.alert_loss.value = g?.alert_loss ?? '';
  gform.alert_down.checked = g ? g.alert_down : true;
  gform.alert_email.checked = g ? g.alert_email : true;
  gform.alert_telegram.checked = g ? g.alert_telegram : true;
  const parentChats = A.groupMap.get(g ? g.parent_id : parentId)?.alert_effective?.chats || [];
  chatPicker('#gTgPick', gform.alert_chat_ids, g?.alert_chat_ids, parentChats);
  $('#gTgTest').onclick = async () => {
    const chats = pickedChats('#gTgPick', gform.alert_chat_ids, true);
    if (!chats) return toast('Select a contact or enter a chat ID first', true);
    const t = g && A.data.targets.find(x => new Set([g.id, ...descendants(g.id)]).has(x.group_id));
    try { const x = await api('api/admin/alerts/test-alert', {method: 'POST', body: {target_id: t?.id, chat_ids: chats}}); toast(`Test alert sent: ${x.sent.join(', ')}` + (x.errors.length ? ` — failed: ${x.errors.join('; ')}` : ''), !!x.errors.length); }
    catch(err){ toast(err.message, true); }
  };
  gform.alert_rounds.value = g?.alert_rounds ?? ''; gform.alert_rounds.placeholder = `default: ${A.data.settings.alert_after_rounds}`;
  const parent = () => A.groupMap.get(gform.parent_id.value ? +gform.parent_id.value : null);
  const syncAlert = () => {
    const m = gform.alert_mode.value;
    $('#alertFields').classList.toggle('hidden', m !== 'on');
    const p0 = parent(), eff = m === 'on' || (m === 'inherit' && p0?.alert_effective?.on);
    $('#alertChatsField').classList.toggle('hidden', !eff);
    const p = parent();
    $('#alertInherit').textContent = m !== 'inherit' ? '' : p ? `Now: ${alertSummary(p, true)} (from ${p.alert_effective.from ? A.groupMap.get(p.alert_effective.from)?.name : 'default'})` : 'Top-level group: inherit means off.';
  };
  const preview = () => { clearTimeout(gPrevTimer); gPrevTimer = setTimeout(() => loadPreview(g), 350); };
  gform.alert_mode.onchange = () => { syncAlert(); preview(); }; gform.parent_id.onchange = syncAlert; syncAlert();
  gform.alert_loss.oninput = preview; gform.alert_down.onchange = preview; gform.alert_rounds.oninput = preview;
  $('#alertPreview').innerHTML = ''; preview();
  $('#sortSubWrap').classList.toggle('hidden', !g || !kids(g.id).length);
  let del = $('#gDel');
  if (!del){ del = document.createElement('button'); del.type = 'button'; del.id = 'gDel'; del.className = 'btn danger'; del.style.marginRight = 'auto'; del.textContent = 'Delete group'; gform.querySelector('.dfoot').prepend(del); }
  del.classList.toggle('hidden', !g);
  del.onclick = async () => {
    const n = A.data.targets.filter(t => new Set([g.id, ...descendants(g.id)]).has(t.group_id)).length;
    if (!confirm(`Delete group "${g.name}"${kids(g.id).length ? ', its subgroups' : ''} and ${n} target(s) with their history?`)) return;
    try { await api(`api/admin/groups/${g.id}`, {method: 'DELETE'}); gdlg.close(); toast('Group deleted'); A.gid = 'all'; await load(); viewTargets(); }
    catch(e){ toast(e.message, true); }
  };
  gdlg.showModal(); gform.name.focus();
}
gdlg.addEventListener('close', () => { if (A.skipChanged){ A.skipChanged = false; renderTargetsPane(); } });
gform.addEventListener('submit', async e => {
  if (e.submitter?.value !== 'save') return;
  e.preventDefault();
  const body = {name: gform.name.value, parent_id: gform.parent_id.value ? +gform.parent_id.value : null,
                sort_mode: gform.sort_mode.value, sort_subgroups: gform.sort_subgroups.checked, hidden: gform.hidden.checked,
                alert_mode: gform.alert_mode.value, alert_loss: gform.alert_loss.value, alert_down: gform.alert_down.checked,
                alert_email: gform.alert_email.checked, alert_telegram: gform.alert_telegram.checked, alert_rounds: gform.alert_rounds.value,
                alert_chat_ids: pickedChats('#gTgPick', gform.alert_chat_ids)};
  try {
    if (gEditing) await api(`api/admin/groups/${gEditing.id}`, {method: 'PUT', body});
    else { const r = await api('api/admin/groups', {method: 'POST', body}); A.gid = r.id; store.set('psa_gid', r.id); }
    gdlg.close(); toast('Group saved'); await load(); renderTree(); renderTargetsPane();
  } catch(err){ toast(err.message, true); }
});

let gPrevTimer, gPrevDays = 7, gPrevSeq = 0;
async function loadPreview(g){
  const box = $('#alertPreview');
  if (!g || gform.alert_mode.value !== 'on'){ box.innerHTML = ''; return; }
  const seq = ++gPrevSeq;
  box.innerHTML = '<span class="muted">Calculating…</span>';
  let r;
  try { r = await api('api/admin/alerts/preview', {method: 'POST', body: {group_id: g.id, alert_loss: gform.alert_loss.value, alert_down: gform.alert_down.checked, alert_rounds: gform.alert_rounds.value, days: gPrevDays}}); }
  catch(err){ if (seq === gPrevSeq) box.innerHTML = `<span style="color:var(--bad)">${esc(err.message)}</span>`; return; }
  if (seq !== gPrevSeq) return;
  const days = `<select id="pvDays" style="width:auto;padding:2px 6px;font-size:13px">${[[1, 'last 24 hours'], [7, 'last 7 days'], [14, 'last 14 days']].map(([v, l]) => `<option value="${v}" ${v === gPrevDays ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
  const perDay = r.emails / r.days;
  let html = `<div class="muted small">Preview with these settings · ${days}</div>`;
  if (!r.has_data) html += `<div class="big">No measurement data for this period yet.</div>`;
  else html += `<div class="big"><b>${r.targets_alerting}</b> of ${r.targets} targets would have alerted · <b>${r.alerts}</b> alert${r.alerts === 1 ? '' : 's'}</div>` +
    `<div class="muted small">≈ ${r.emails} message${r.emails === 1 ? '' : 's'} (${perDay < 1 ? perDay.toFixed(1) : Math.round(perDay)} per day), counting ${r.need} rounds in a row before an alert${r.rows.some(x => x.rounds) ? ' (or the target\'s own setting)' : ''}${r.skipped.length ? ` · ${r.skipped.length} target(s) skipped` : ''}</div>`;
  if (r.rows.length) html += `<details ${r.rows.length <= 8 ? 'open' : ''}><summary>Show targets</summary><div class="plist"><table>${r.rows.map(x => `<tr>
      <td><b>${esc(x.name)}</b> <span class="badge">IPv${x.family}</span>${x.open ? ' <span class="akind loss" style="font-size:11px">now</span>' : ''}<div class="muted small">${esc(x.group)}</div></td>
      <td class="small" style="white-space:nowrap">${x.alerts}× · ${fmtDur(x.minutes * 60)}<div class="muted">worst ${x.worst.toFixed(0)}%${x.down ? ` · ${x.down}× down` : ''}${x.rounds ? ` · own: ${x.rounds} rounds` : ''}</div></td>
      <td style="text-align:right"><button type="button" class="btn sm" data-skip="${x.target_id}">🔕 Skip</button></td></tr>`).join('')}</table></div></details>`;
  if (r.skipped.length) html += `<details><summary>Skipped targets (${r.skipped.length})</summary><div class="plist"><table>${r.skipped.map(x => `<tr><td>🔕 ${esc(x.name)}</td>
      <td style="text-align:right"><button type="button" class="btn sm" data-unskip="${x.id}">Include again</button></td></tr>`).join('')}</table></div></details>`;
  box.innerHTML = html;
  $('#pvDays').onchange = e => { gPrevDays = +e.target.value; loadPreview(g); };
  box.onclick = async e => {
    const b = e.target.closest('[data-skip],[data-unskip]'); if (!b) return;
    const skip = !!b.dataset.skip, id = +(b.dataset.skip || b.dataset.unskip);
    try {
      await api('api/admin/targets/bulk', {method: 'POST', body: {action: skip ? 'alerts_off' : 'alerts_on', ids: [id]}});
      toast(skip ? 'No alerts for this target' : 'Alerts on for this target');
      const t = A.data.targets.find(x => x.id === id); if (t) t.alert_skip = skip;
      A.skipChanged = true;
      loadPreview(g);
    } catch(err){ toast(err.message, true); }
  };
}

// ---------- IPv6 tab ----------
function viewIpv6(){
  const total = A.data.targets.length;
  const have = A.data.targets.filter(t => (A.data.series[t.id] || []).some(s => s.family === 6)).length;
  main.innerHTML = `<div class="card pad" style="max-width:1100px">
    <h2>IPv6 coverage</h2>
    <p class="muted" style="margin:4px 0 14px">Targets with an IPv6 address (or a hostname with an AAAA record) are measured over IPv4 and IPv6 side by side.</p>
    <div><span class="stat"><b>${have}</b><span class="muted small">of ${total} targets on IPv6</span></span>
      <span class="stat"><b>${total ? Math.round(have / total * 100) : 0}%</b><span class="muted small">coverage</span></span></div>
    <div class="bar" style="height:6px;border-radius:3px;background:var(--grid);overflow:hidden;margin:10px 0 16px"><i style="display:block;height:100%;width:${total ? have / total * 100 : 0}%;background:var(--accent)"></i></div>
    <p>Targets that are configured with only an IPv4 address can't be measured over IPv6 automatically. PingScope can look up AAAA records for their hostname and for hostnames in their name (for example <code>ns1.example.nl</code>) and suggest an address. Check each suggestion: the AAAA of a website name is not always the same machine as the IPv4 address you monitor.</p>
    <button class="btn primary" id="find6">Find IPv6 addresses</button> <span class="muted small" id="find6msg"></span>
    <div id="ipv6list" style="margin-top:16px"></div></div>`;
  $('#find6').onclick = loadIpv6;
  if (A.ipv6) renderIpv6();
}
async function loadIpv6(){
  $('#find6msg').textContent = 'Looking up AAAA records…'; $('#find6').disabled = true;
  try { A.ipv6 = await api('api/admin/ipv6'); renderIpv6(); $('#find6msg').textContent = ''; }
  catch(e){ $('#find6msg').textContent = e.message; }
  $('#find6').disabled = false;
}
function renderIpv6(){
  const items = A.ipv6.items, withC = items.filter(i => i.candidates.length);
  const box = $('#ipv6list');
  if (!items.length){ box.innerHTML = '<div class="empty">Every target is already measured over IPv6. 🎉</div>'; return; }
  const row = i => `<tr data-id="${i.id}"><td><input type="checkbox" class="pick"></td>
    <td><b>${esc(i.name)}</b> ${probeBadge(i.probe)}<div class="faint small">${esc(i.group)}</div></td>
    <td class="mono small hide-sm">${esc(i.host)}</td>
    <td>${i.candidates.length ? `<select class="cand">${i.candidates.flatMap(c => c.addresses.map(a => `<option value="${esc(a)}">${esc(a)} — AAAA of ${esc(c.name)}</option>`)).join('')}<option value="">other…</option></select>` : ''}
      <input class="manual mono ${i.candidates.length ? 'hidden' : ''}" placeholder="enter IPv6 address" style="margin-top:4px"></td></tr>`;
  box.innerHTML = `<p><b>${withC.length}</b> of ${items.length} IPv4-only targets have a suggestion.</p>
    <div class="tblwrap"><table class="tbl"><thead><tr><th style="width:28px"><input type="checkbox" id="pickAll"></th><th>Target</th><th class="hide-sm">Host</th><th>IPv6 address</th></tr></thead>
    <tbody>${withC.map(row).join('')}${items.filter(i => !i.candidates.length).map(row).join('')}</tbody></table></div>
    <div class="row" style="margin-top:12px"><button class="btn primary" id="apply6">Apply to selected</button><span class="muted small" id="apply6msg"></span></div>`;
  box.onchange = e => {
    if (e.target.id === 'pickAll') $$('.pick', box).forEach(c => { if (c.closest('tr').querySelector('.cand') || c.closest('tr').querySelector('.manual').value) c.checked = e.target.checked; });
    if (e.target.classList.contains('cand')){ const m = e.target.closest('tr').querySelector('.manual'); m.classList.toggle('hidden', !!e.target.value); }
  };
  box.oninput = e => { if (e.target.classList.contains('manual')) e.target.closest('tr').querySelector('.pick').checked = !!e.target.value.trim(); };
  $('#apply6').onclick = async () => {
    const items = $$('tr[data-id]', box).filter(tr => tr.querySelector('.pick').checked).map(tr => {
      const c = tr.querySelector('.cand'); const v = c && c.value ? c.value : tr.querySelector('.manual').value.trim();
      return {id: +tr.dataset.id, host_v6: v};
    }).filter(x => x.host_v6);
    if (!items.length){ $('#apply6msg').textContent = 'Select at least one target with an address.'; return; }
    try {
      await api('api/admin/targets/bulk', {method: 'POST', body: {action: 'set_ipv6', ids: items.map(i => i.id), items}});
      toast(`IPv6 added to ${items.length} target(s)`);
      await load(); A.ipv6 = null; viewIpv6(); loadIpv6();
    } catch(e){ toast(e.message, true); }
  };
}

// ---------- import / export ----------
function viewImport(){
  main.innerHTML = `<div class="card pad" style="max-width:1000px;margin-bottom:16px">
    <h2>Import</h2>
    <p class="muted" style="margin:4px 0 14px">Paste the <b>Targets</b> section of a SmokePing config (a complete config file also works), or a PingScope JSON export. Nested groups, FPing, FPing6, DNS and TCPPing probes are converted.</p>
    <div class="row" style="margin-bottom:8px"><input type="file" id="ifile" accept=".txt,.conf,.json,text/*" style="width:auto"><span class="muted small">or paste below</span></div>
    <textarea id="itext" rows="12" placeholder="*** Targets ***&#10;probe = FPing&#10;&#10;+ dns&#10;menu = DNS&#10;title = DNS servers&#10;&#10;++ cloudflare&#10;host = 1.1.1.1"></textarea>
    <div style="margin:12px 0">
      <label class="check"><input type="radio" name="imode" value="merge" checked> <span><b>Add to existing</b> — groups with the same name are reused; identical targets are skipped.</span></label>
      <label class="check"><input type="radio" name="imode" value="replace"> <span><b>Replace everything</b> — deletes all current groups, targets and their measurement history first.</span></label>
      <label class="check"><input type="checkbox" id="iknown" checked> <span>Add the known IPv6 addresses of root servers and public resolvers (Google, Cloudflare, Quad9, OpenDNS, NextDNS)</span></label>
      <label class="check"><input type="checkbox" id="ititle"> <span>Use the title and welcome text from the file for this site</span></label>
      <label class="check hidden" id="isetwrap"><input type="checkbox" id="isettings"> <span>Also restore the settings from the export</span></label>
    </div>
    <div class="row"><button class="btn" id="ipreview">Preview</button><button class="btn primary" id="iapply" disabled>Import</button><span class="muted small" id="imsg"></span></div>
    <div id="ipreviewbox" style="margin-top:16px"></div></div>
    <div class="card pad" style="max-width:1000px"><h2>Export</h2>
    <p class="muted" style="margin:4px 0 12px">Download all groups, targets and settings as JSON. Use it as a backup or to move to another server (import it above). Measurement history is not included; copy the database file for that.</p>
    <a class="btn" href="api/admin/export" download>Download JSON export</a></div>`;
  const text = $('#itext');
  $('#ifile').onchange = async e => { const f = e.target.files[0]; if (f){ text.value = await f.text(); invalidate(); } };
  const invalidate = () => { $('#iapply').disabled = true; $('#isetwrap').classList.toggle('hidden', !text.value.trim().startsWith('{')); };
  text.oninput = invalidate; $('#iknown').onchange = () => { if (A.previewText) preview(); };
  $('#ipreview').onclick = preview;
  $('#iapply').onclick = async () => {
    const mode = $('input[name=imode]:checked').value;
    if (mode === 'replace' && !confirm('Replace everything? All current groups, targets and measurement history will be deleted.')) return;
    try {
      const r = await api('api/admin/import', {method: 'POST', body: {text: text.value, mode, add_known_ipv6: $('#iknown').checked,
        use_site_title: $('#ititle').checked, restore_settings: $('#isettings').checked}});
      toast(`Imported ${r.added.targets} targets in ${r.added.groups} new groups${r.added.skipped ? ` (${r.added.skipped} skipped as duplicates)` : ''}`);
      await load(); A.gid = 'all'; location.hash = 'targets';
    } catch(e){ toast(e.message, true); }
  };
  async function preview(){
    $('#imsg').textContent = '';
    try {
      const r = await api('api/admin/import/preview', {method: 'POST', body: {text: text.value, add_known_ipv6: $('#iknown').checked}});
      A.previewText = text.value;
      const st = r.stats;
      const tree = list => `<ul>${list.map(g => `<li>📁 ${esc(g.name)} <span class="faint">${g.targets ? g.targets + ' targets' : ''}</span>${g.groups.length ? tree(g.groups) : ''}</li>`).join('')}</ul>`;
      $('#ipreviewbox').innerHTML = `<div style="margin-bottom:10px">
        <span class="stat"><b>${st.groups}</b><span class="muted small">groups</span></span>
        <span class="stat"><b>${st.targets}</b><span class="muted small">targets (${Object.entries(st.by_probe).map(([k, v]) => `${v} ${k}`).join(', ')})</span></span>
        <span class="stat"><b>${st.known_ipv6 + st.hostnames}</b><span class="muted small">with IPv6 (${st.known_ipv6} known addresses, ${st.hostnames} hostnames via AAAA)</span></span>
        <span class="stat"><b>${st.ipv4_only}</b><span class="muted small">IPv4 only — see the IPv6 tab afterwards</span></span></div>
        ${r.site && r.site.title ? `<p class="small muted">Title in file: <b>${esc(r.site.title)}</b>${r.site.remark ? ' — ' + esc(r.site.remark) : ''}</p>` : ''}
        <div class="outline">${tree(r.outline)}</div>
        ${r.warning_count ? `<p class="small" style="margin:10px 0 0"><b>${r.warning_count} note(s):</b></p><ul class="warnlist">${r.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}`;
      $('#iapply').disabled = false;
    } catch(e){ $('#ipreviewbox').innerHTML = ''; $('#imsg').textContent = e.message; $('#imsg').style.color = 'var(--bad)'; }
  }
}

// ---------- settings ----------
function viewSettings(){
  const sch = A.data.settings_schema, s = A.data.settings, st = A.data.status;
  const sections = [...new Set(sch.map(x => x.section))];
  main.innerHTML = `<div style="max-width:760px">
    <div class="card pad" style="margin-bottom:16px"><h2>Status</h2><p class="muted small" style="margin:6px 0 0">
      Last round: ${st.last_run ? `${fmtShort(st.last_run)} (${ago(st.last_run)}, took ${st.duration}s)` : 'not yet'} ·
      fping: ${st.fping_available ? 'installed' : '<b style="color:var(--bad)">missing</b>'} ·
      mtr: ${st.mtr_available ? 'installed' : '<b style="color:var(--bad)">missing</b>'}</p></div>
    <div class="card pad" style="margin-bottom:16px" id="httpsCard"><h2>HTTPS / SSL certificate</h2><p class="muted small" style="margin:6px 0 0">Checking…</p></div>
    <div id="swarn">${A.data.warnings.map(w => `<div class="warnbox">${esc(w)}</div>`).join('')}</div>
    <form id="sform">${sections.map(sec => `<div class="card pad settings-sec"><h3>${esc(sec)}</h3>` +
      sch.filter(x => x.section === sec).map(x => x.type === 'bool'
        ? `<label class="check"><input type="checkbox" name="${x.key}" ${s[x.key] ? 'checked' : ''}> <span><b>${esc(x.label)}</b>${x.help ? `<br><span class="muted small">${esc(x.help)}</span>` : ''}</span></label>`
        : `<label class="field" data-f="${x.key}"><span>${esc(x.label)}</span>` +
          (x.options ? `<select name="${x.key}">${x.options.map(([v, l]) => `<option value="${v}" ${s[x.key] === v ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`
            : x.key === 'site_remark' ? `<textarea name="${x.key}" rows="2" style="font-family:inherit;font-size:14px">${esc(s[x.key])}</textarea>`
            : x.type === 'secret' ? `<input name="${x.key}" type="password" autocomplete="new-password" placeholder="${s[x.key + '_set'] ? '•••••••• saved, leave empty to keep' : 'not set'}">`
            : `<input name="${x.key}" ${x.type === 'int' ? `type="number" min="${x.min ?? ''}" max="${x.max ?? ''}"` : ''} value="${esc(s[x.key])}">`) +
          (x.help ? `<div class="help">${esc(x.help)}</div>` : '') + '</label>').join('') + '</div>').join('')}
    <div class="row"><button class="btn primary">Save settings</button></div></form></div>`;
  loadHttps();
  $('#sform').onsubmit = async e => {
    e.preventDefault();
    const body = {};
    for (const x of sch){ const el = e.target[x.key]; body[x.key] = x.type === 'bool' ? el.checked : el.value; }
    try {
      const r = await api('api/admin/settings', {method: 'PUT', body});
      A.data.settings = r.settings; A.data.warnings = r.warnings;
      $('#swarn').innerHTML = r.warnings.map(w => `<div class="warnbox">${esc(w)}</div>`).join('');
      $('#siteTitle').textContent = r.settings.site_title;
      for (const x of sch) if (x.type === 'secret'){ const el = e.target[x.key]; el.value = ''; el.placeholder = r.settings[x.key + '_set'] ? '•••••••• saved, leave empty to keep' : 'not set'; }
      toast('Settings saved');
    } catch(err){ toast(err.message, true); }
  };
}

async function loadHttps(){
  const box = $('#httpsCard'); if (!box) return;
  let r;
  try { r = await api('api/admin/https'); } catch(e){ box.querySelector('p').textContent = e.message; return; }
  const line = (ok, txt) => `<div style="display:flex;gap:8px;align-items:center;padding:3px 0"><span class="dot ${ok === true ? 'ok' : ok === false ? 'down' : ''}"></span><span>${txt}</span></div>`;
  let html = '<h2>HTTPS / SSL certificate</h2>';
  if (!r.configured){
    html += `<p class="muted small" style="margin:6px 0 10px">No hostname is configured, so PingScope runs without HTTPS${r.this_request_https ? ' (this page is served over HTTPS by another proxy)' : ''}.</p>` +
      '<p class="small" style="margin:0">To enable HTTPS with a free Let\'s Encrypt certificate, run <code>sudo ./install.sh</code> again on the server and enter your hostname, or set <code>PINGSCOPE_DOMAIN</code> and <code>COMPOSE_PROFILES=https</code> in <code>.env</code> and run <code>docker compose up -d</code>. See docs/INSTALL.md, section "HTTPS / SSL".</p>';
  } else {
    for (const d of r.domains){
      const c = d.certificate;
      html += `<div style="margin-top:10px"><b class="mono">${esc(d.domain)}</b>` +
        (c && c.valid ? line(c.days_left > 14, `Certificate valid · ${esc(c.issuer)} · expires ${fmtShort(c.expires)} (${c.days_left} days, renewed automatically)`)
          : line(false, esc(d.error || 'No valid certificate yet'))) +
        line(d.redirect, d.redirect ? 'HTTP redirects to HTTPS' : 'HTTP does not redirect to HTTPS') + '</div>';
    }
    html += '<p class="muted small" style="margin:10px 0 0">The hostname is set on the server in <code>.env</code> (<code>PINGSCOPE_DOMAIN</code>). Certificates come from Let\'s Encrypt via Caddy and renew automatically about 30 days before they expire.</p>';
  }
  box.innerHTML = html;
}

// ---------- alerts ----------
function alertSummary(g, plain){
  const e = g.alert_effective;
  if (!e?.on) return plain ? 'notifications off' : 'Notifications off';
  const parts = [];
  if (e.loss) parts.push(`loss ≥ ${e.loss}%`);
  if (e.down) parts.push('down');
  const ch = [e.telegram && 'Telegram', e.email && 'email'].filter(Boolean);
  return `${plain ? '' : '🔔 '}alerts on ${parts.join(' or ')} after ${e.rounds || A.data.settings.alert_after_rounds} rounds, ${ch.length ? 'via ' + ch.join(' + ') : 'not sent'}`;
}
const fmtDur = s => s < 3600 ? `${Math.max(1, Math.round(s / 60))} min` : s < 86400 ? `${Math.floor(s / 3600)} h ${Math.round(s % 3600 / 60)} min` : `${Math.floor(s / 86400)} d ${Math.floor(s % 86400 / 3600)} h`;
async function viewAlerts(){
  const r = await api('api/admin/alerts');
  const open = r.alerts.filter(a => !a.ended), done = r.alerts.filter(a => a.ended);
  const row = a => `<tr><td>${a.ended ? '<span class="akind ok">resolved</span>' : `<span class="akind ${a.kind}">${a.kind === 'down' ? 'down' : 'packet loss'}</span>`}</td>
    <td><a href="./#/t/${a.target_id}"><b>${esc(a.name)}</b></a> <span class="badge">IPv${a.family}</span><div class="muted small">${esc(a.group)}</div></td>
    <td class="muted small">${fmtShort(a.started)}${a.ended ? ` → ${fmtShort(a.ended)}` : ''}<br>${fmtDur((a.ended || r.now) - a.started)}</td>
    <td class="small">${a.kind === 'down' && !a.ended ? '100% loss' : `now ${a.last_loss == null ? '–' : a.last_loss.toFixed(0) + '%'} · worst ${a.peak_loss == null ? '–' : a.peak_loss.toFixed(0) + '%'}`}</td>
    <td class="small muted">${a.emailed ? '✓ sent' : '<span title="No message was sent for this alert (no channel set up, or sending failed)">not sent</span>'}</td></tr>`;
  const table = (rows, empty) => rows.length ? `<div class="tblwrap"><table class="tbl"><thead><tr><th>Status</th><th>Target</th><th>When</th><th>Loss</th><th></th></tr></thead><tbody>${rows.map(row).join('')}</tbody></table></div>` : `<p class="muted">${empty}</p>`;
  main.innerHTML = `<div style="max-width:1000px">
    <div class="card pad" style="margin-bottom:16px"><h2>Notifications</h2>
      <p class="muted small" style="margin:6px 0 10px">Alerts are set per group: open a group under <a href="#targets">Targets</a> → Edit group → Notifications. ${r.groups_on ? `${r.groups_on} group(s) have notifications on.` : '<b>No group has notifications on yet.</b>'} Telegram and email settings are under <a href="#settings">Settings</a> → Notifications.</p>
      <div class="small" style="margin:0 0 10px">Telegram: ${r.telegram_problem ? `<span class="muted">not set up (${esc(r.telegram_problem)})</span>` : '<b style="color:var(--ok)">ready</b>'} ·
        Email: ${r.email_problem ? `<span class="muted">not set up (${esc(r.email_problem)})</span>` : '<b style="color:var(--ok)">ready</b>'}
        ${r.target_chats || r.group_chats ? ` · own chat IDs on ${[r.group_chats && `${r.group_chats} group(s)`, r.target_chats && `${r.target_chats} target(s)`].filter(Boolean).join(' and ')}` : ''}</div>
      ${r.last_error ? `<div class="warnbox">${esc(r.last_error)}</div>` : ''}
      <div class="row" style="gap:10px;align-items:center;flex-wrap:wrap"><button class="btn primary" id="testAlert">Send test alert</button><button class="btn" id="findChats">Find chat IDs</button>
      <span class="muted small">${r.last_sent ? `Last alert sent: ${fmtShort(r.last_sent)}` : ''}</span></div>
      <div class="muted small" style="margin-top:6px">The test alert looks exactly like a real one (marked TEST) and goes to every admin channel: Telegram contacts that get all alerts, the chat IDs in Settings, and email if set up.</div>
      <div id="chatList"></div></div>
    <div class="card pad" style="margin-bottom:16px"><h2>Telegram contacts</h2>
      <p class="muted small" style="margin:4px 0 10px">Save chat IDs once with a name, then tick them on a target (Edit target → "Also send alerts for this target to"). <b>Gets all alerts</b> = receives every alert, like the chat IDs in Settings.</p>
      <div id="contacts"></div></div>
    <div class="card pad" style="margin-bottom:16px"><h2>Active alerts (${open.length})</h2><div style="margin-top:10px">${table(open, 'Everything is fine.')}</div></div>
    <div class="card pad"><h2>History</h2><p class="muted small" style="margin:4px 0 10px">Resolved alerts of the last 90 days.</p>${table(done, 'No resolved alerts yet.')}</div></div>`;
  $('#testAlert').onclick = async () => {
    try { const x = await api('api/admin/alerts/test-alert', {method: 'POST', body: {}}); toast(`Test alert sent: ${x.sent.join(', ')}` + (x.errors.length ? ` — failed: ${x.errors.join('; ')}` : ''), !!x.errors.length); }
    catch(err){ toast(err.message, true); }
  };
  renderContacts();
  $('#findChats').onclick = async () => {
    const box = $('#chatList'); box.innerHTML = '<p class="muted small">Asking Telegram…</p>';
    try {
      const {chats} = await api('api/admin/telegram/chats');
      box.innerHTML = chats.length ? `<p class="small" style="margin:12px 0 6px">Chats that recently wrote to your bot:</p><div class="tblwrap"><table class="tbl"><tbody>${chats.map(c => `<tr><td><b>${esc(c.name || '(no name)')}</b> <span class="muted small">${esc(c.type || '')}</span></td><td class="mono">${c.id}</td><td class="acts">${(A.data.contacts || []).some(x => x.chat_id === String(c.id)) ? '<span class="muted small">saved</span>' : `<button class="btn sm" data-save="${c.id}" data-name="${esc(c.name || '')}">Save as contact</button>`} <button class="btn sm ghost" data-copy="${c.id}">Copy</button></td></tr>`).join('')}</tbody></table></div><p class="muted small">Save yourself as a contact with "Gets all alerts" on; save a customer and tick them on their target.</p>`
        : '<p class="small" style="margin:12px 0 0">No chats found. Open your bot in Telegram, send <b>/start</b> (or add the bot to a group and send a message there), then click Find chat IDs again. Telegram only remembers messages of the last 24 hours.</p>';
      box.onclick = async e => {
        const b = e.target.closest('[data-copy]'); if (b){ navigator.clipboard?.writeText(b.dataset.copy); toast('Copied ' + b.dataset.copy); }
        const sv = e.target.closest('[data-save]');
        if (sv){
          try { await api('api/admin/contacts', {method: 'POST', body: {chat_id: sv.dataset.save, label: sv.dataset.name || sv.dataset.save, is_default: !(A.data.contacts || []).length}}); toast('Contact saved — rename it below if you like'); sv.outerHTML = '<span class="muted small">saved</span>'; await renderContacts(); }
          catch(err){ toast(err.message, true); }
        }
      };
    } catch(err){ box.innerHTML = `<p class="small" style="color:var(--bad)">${esc(err.message)}</p>`; }
  };
}

async function renderContacts(){
  const box = $('#contacts'); if (!box) return;
  const {contacts} = await api('api/admin/contacts');
  A.data.contacts = contacts;
  box.innerHTML = (contacts.length ? `<div class="tblwrap"><table class="tbl ctbl"><thead><tr><th>Name</th><th>Chat ID</th><th>Gets all alerts</th><th></th></tr></thead><tbody>${contacts.map(c => `<tr data-c="${c.id}">
      <td><input type="text" class="cl" value="${esc(c.label)}" style="min-width:140px"></td>
      <td class="mono small">${esc(c.chat_id)}</td>
      <td><input type="checkbox" class="cd" ${c.is_default ? 'checked' : ''} style="width:auto"></td>
      <td class="acts"><button class="btn sm" data-ct="test">Test</button> <button class="btn sm danger" data-ct="del">Delete</button></td></tr>`).join('')}</tbody></table></div>`
    : '<p class="muted small" style="margin:0 0 8px">No contacts yet. Use <b>Find chat IDs</b> above and click <b>Save as contact</b>, or add one here.</p>') +
    `<form id="cform" class="row" style="margin-top:10px;flex-wrap:wrap;gap:8px"><input name="label" placeholder="Name, e.g. NOC on-call or Customer Groningen" required style="flex:2;min-width:160px">
      <input name="chat_id" placeholder="Chat ID" required style="flex:1;min-width:120px" spellcheck="false">
      <label class="check" style="margin:0"><input type="checkbox" name="is_default"> <span>Gets all alerts</span></label><button class="btn">Add contact</button></form>`;
  const save = async tr => {
    const c = contacts.find(x => x.id === +tr.dataset.c);
    try { await api(`api/admin/contacts/${c.id}`, {method: 'PUT', body: {chat_id: c.chat_id, label: tr.querySelector('.cl').value, is_default: tr.querySelector('.cd').checked}}); toast('Contact saved'); await renderContacts(); }
    catch(err){ toast(err.message, true); }
  };
  box.onchange = e => { const tr = e.target.closest('tr[data-c]'); if (tr) save(tr); };
  box.onclick = async e => {
    const b = e.target.closest('[data-ct]'); if (!b) return;
    const c = contacts.find(x => x.id === +b.closest('tr').dataset.c);
    if (b.dataset.ct === 'test'){
      try { await api('api/admin/alerts/test-alert', {method: 'POST', body: {chat_ids: c.chat_id}}); toast(`Test alert sent to ${c.label}`); } catch(err){ toast(err.message, true); }
    } else if (confirm(`Delete contact "${c.label}"? It is also removed from the targets it is ticked on.`)){
      try { const r = await api(`api/admin/contacts/${c.id}`, {method: 'DELETE'}); toast(`Deleted${r.removed_from_targets ? `, removed from ${r.removed_from_targets} target(s)` : ''}`); await load(); await renderContacts(); } catch(err){ toast(err.message, true); }
    }
  };
  $('#cform').onsubmit = async e => {
    e.preventDefault();
    const f = e.target;
    try { await api('api/admin/contacts', {method: 'POST', body: {label: f.label.value, chat_id: f.chat_id.value, is_default: f.is_default.checked}}); toast('Contact added'); await renderContacts(); }
    catch(err){ toast(err.message, true); }
  };
}

// ---------- users ----------
const ROLE_OPTS = [['viewer', 'Viewer'], ['admin', 'Admin']];
const PW_CARD = `<div class="card pad"><h2>Change your password</h2>`;
function bindPassword(){
  $('#pform').onsubmit = async e => {
    e.preventDefault();
    try { await api('api/password', {method: 'POST', body: Object.fromEntries(new FormData(e.target))}); toast('Password changed'); e.target.reset(); }
    catch(err){ toast(err.message, true); }
  };
}
function pwForm(){
  return `${PW_CARD}<form id="pform" style="margin-top:12px;max-width:360px">
      <label class="field"><span>Current password</span><input name="current" type="password" autocomplete="current-password" required></label>
      <label class="field"><span>New password <span class="faint">(min. 10 characters)</span></span><input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
      <button class="btn primary">Change password</button></form></div>`;
}
// viewers: no admin tabs, only their own account
function viewAccount(){
  $('#tabs').classList.add('hidden');
  $('.brand small').textContent = 'Account';
  main.innerHTML = `<div style="max-width:760px">
    <div class="card pad" style="margin-bottom:16px"><h2>View-only account</h2>
    <p class="muted" style="margin:6px 0 12px">You are logged in as <b>${esc(A.user.username)}</b>. You can see everything on the dashboard, including hostnames, IP addresses, traceroute hops and hidden groups, but you can't change targets or settings.</p>
    <a class="btn primary" href="./">Go to the dashboard</a></div>
    ${pwForm()}</div>`;
  bindPassword();
}
async function viewUsers(){
  const {users} = await api('api/admin/users');
  main.innerHTML = `<div style="max-width:760px">
    <div class="card pad" style="margin-bottom:16px"><h2>Users</h2><p class="muted small" style="margin:4px 0 12px"><b>Admin</b>: full access to this admin panel. <b>Viewer</b>: view-only; sees the dashboard with hostnames, IP addresses, traceroute hops and hidden groups, but can't change anything.</p>
    <div class="tblwrap"><table class="tbl"><thead><tr><th>Username</th><th>Role</th><th>Created</th><th></th></tr></thead><tbody>
    ${users.map(u => `<tr><td><b>${esc(u.username)}</b>${u.id === A.user.id ? ' <span class="badge">you</span>' : ''}</td>
      <td>${u.id === A.user.id ? 'Admin' : `<select data-role="${u.id}" style="width:auto">${ROLE_OPTS.map(([v, l]) => `<option value="${v}" ${u.role === v ? 'selected' : ''}>${l}</option>`).join('')}</select>`}</td>
      <td class="muted">${fmtShort(u.created)}</td>
      <td class="acts">${u.id === A.user.id ? '' : `<button class="btn sm danger" data-del="${u.id}" data-name="${esc(u.username)}">Delete</button>`}</td></tr>`).join('')}
    </tbody></table></div>
    <form id="uform" class="row" style="margin-top:12px;flex-wrap:wrap"><input name="username" placeholder="Username" required style="flex:1;min-width:140px">
      <input name="password" type="password" placeholder="Password (min. 10)" minlength="10" required autocomplete="new-password" style="flex:1;min-width:160px">
      <select name="role" style="width:auto">${ROLE_OPTS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select>
      <button class="btn">Add user</button></form></div>
    ${PW_CARD}
    <form id="pform" style="margin-top:12px;max-width:360px">
      <label class="field"><span>Current password</span><input name="current" type="password" autocomplete="current-password" required></label>
      <label class="field"><span>New password <span class="faint">(min. 10 characters)</span></span><input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
      <button class="btn primary">Change password</button></form></div></div>`;
  main.onclick = async e => {
    const b = e.target.closest('[data-del]'); if (!b) return;
    if (!confirm(`Delete user "${b.dataset.name}"?`)) return;
    try { await api(`api/admin/users/${b.dataset.del}`, {method: 'DELETE'}); toast('User deleted'); viewUsers(); } catch(err){ toast(err.message, true); }
  };
  main.onchange = async e => {
    const s = e.target.closest('[data-role]'); if (!s) return;
    try { await api(`api/admin/users/${s.dataset.role}`, {method: 'PUT', body: {role: s.value}}); toast('Role changed'); }
    catch(err){ toast(err.message, true); viewUsers(); }
  };
  $('#uform').onsubmit = async e => {
    e.preventDefault();
    try { await api('api/admin/users', {method: 'POST', body: Object.fromEntries(new FormData(e.target))}); toast('User added'); viewUsers(); }
    catch(err){ toast(err.message, true); }
  };
  bindPassword();
}

PS.schemeSwitch($('#scheme'));
boot().catch(e => { main.innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; });
})();
