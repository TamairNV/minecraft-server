const cell = (text, cls) => { const td = document.createElement('td'); td.textContent = text ?? ''; if (cls) td.className = cls; return td; };
const when = (ts) => new Date(ts * 1000).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
const size = bytes;
let busy = false;

async function api(path, body) {
  const options = body === undefined ? {} : { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) };
  const res = await fetch(`/admin/api/${path}`, options);
  if (res.status === 401) { location.reload(); throw new Error('Your login has expired.'); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `That didn't work (${res.status}).`);
  return data;
}

async function act(path, body, okText) {
  try {
    const data = await api(path, body);
    if (okText) toast(okText, true);
    return data;
  } catch (e) {
    toast(e.message);
    return null;
  }
}

function button(text, onClick, cls) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = text;
  b.className = `btn ${cls || ''}`;
  b.addEventListener('click', () => busyWhile(b, () => onClick()));
  return b;
}

// A button shows it's working and can't be pressed twice.
async function busyWhile(btn, fn) {
  if (!btn || btn.classList.contains('is-busy')) return undefined;
  btn.classList.add('is-busy');
  btn.disabled = true;
  try {
    return await fn();
  } finally {
    btn.classList.remove('is-busy');
    btn.disabled = false;
  }
}

function submitBusy(form, fn) {
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    busyWhile(form.querySelector('[type=submit]'), () => fn(e));
  });
}

function ask(title, text, yes) {
  return new Promise((resolve) => {
    const d = $('ask-dialog');
    $('ask-title').textContent = title;
    $('ask-text').textContent = text;
    $('ask-yes').textContent = yes;
    const finish = (answer) => {
      d.removeEventListener('close', onClose);
      resolve(answer);
    };
    const onClose = () => finish(d.returnValue === 'yes');
    d.returnValue = '';
    d.addEventListener('close', onClose);
    d.showModal();
  });
}
$('ask-no').addEventListener('click', () => $('ask-dialog').close(''));
$('ask-form').addEventListener('submit', (e) => { e.preventDefault(); $('ask-dialog').close('yes'); });

// Rebuild a table only when its data changed, so scrolling and focus survive refreshes.
function fill(id, items, key, make) {
  const el = $(id);
  const sig = JSON.stringify(key);
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  el.replaceChildren(...items.map(make));
}

// ---- tabs
const loaders = { whitelist: loadWhitelist, backups: loadBackups, worlds: loadWorlds, settings: loadSettings, pregen: loadPregen, modpack: loadModpack, system: loadSystem };
let currentTab = 'console';
function openTab(name) {
  currentTab = name;
  document.querySelectorAll('.tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  document.querySelectorAll('.tab').forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  history.replaceState(null, '', `#${name}`);
  if (loaders[name]) loaders[name]();
}
document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => openTab(b.dataset.tab)));

// ---- status and running jobs
let powerAction = null; // set while a start, stop or restart is still happening
let serverUp = null;

function applyPower() {
  const off = { 'srv-start': serverUp !== false, 'srv-restart': serverUp !== true, 'srv-stop': serverUp !== true };
  for (const [id, no] of Object.entries(off)) {
    const b = $(id);
    b.disabled = busy || powerAction !== null || no;
    b.classList.toggle('is-busy', powerAction !== null && b.dataset.action === powerAction);
  }
}

async function refreshStatus() {
  let s = null;
  try { s = await api('system'); } catch { /* the header chip shows the connection */ }
  if (powerAction === null) {
    serverUp = s && s.crafty ? !!s.crafty.running : null;
    applyPower();
    if (s && s.crafty) {
      setChip(s.joinable ? 'up' : 'down', s.joinable ? `${s.crafty.online}/${s.crafty.max} online` : s.crafty.running ? 'Starting…' : 'Server offline');
    } else {
      setChip('', 'Connection lost, retrying');
    }
  }
  if (currentTab === 'system' && s) renderSystem(s);
  return s;
}

const seenJob = { id: null, status: null };
async function refreshJob() {
  let d;
  try { d = await api('worlds'); } catch { return; }
  const job = d.job;
  busy = d.busy;
  applyPower();
  const el = $('job');
  if (job && (job.status === 'running' || Date.now() / 1000 - job.finished < 600)) {
    el.hidden = false;
    el.className = `job ${job.status}`;
    const what = job.kind === 'swap' ? `Swapping to ${job.target}` : `Creating ${job.target}`;
    el.textContent = job.status === 'running' ? `${what}: ${job.step}…` : job.status === 'done' ? `${what}: finished.` : `${what} failed: ${job.error}`;
  } else {
    el.hidden = true;
  }
  if (job && (job.id !== seenJob.id || job.status !== seenJob.status)) {
    const changed = seenJob.id !== null;
    seenJob.id = job.id; seenJob.status = job.status;
    if (changed && currentTab === 'worlds') loadWorlds();
  }
}

// Waits for the server to really finish the change. Stopping or starting takes
// anywhere from seconds to a minute, so nothing is shown as done until it is.
const POWER = {
  start: { doing: 'Starting…', done: 'Server is up.', finished: (s) => s.joinable },
  stop: { doing: 'Stopping…', done: 'Server stopped.', finished: (s) => s.crafty && !s.crafty.running },
  restart: { doing: 'Restarting…', done: 'Server is back up.', finished: (s, sawDown) => sawDown && s.joinable },
};

async function changeServer(action) {
  const plan = POWER[action];
  powerAction = action;
  applyPower();
  setChip('', plan.doing, true);
  let done = false;
  if (await act(`server/${action}`, {})) {
    let sawDown = false;
    for (let i = 0; i < 90 && !done; i += 1) {
      await new Promise((r) => setTimeout(r, 2000));
      let s = null;
      try { s = await api('system'); } catch { continue; }
      if (!s.joinable) sawDown = true;
      done = !!plan.finished(s, sawDown);
    }
    toast(done ? plan.done : "That's taking longer than expected. Check the console.", done);
  }
  powerAction = null;
  setChip('', 'Checking…');
  await refreshStatus();
}

document.querySelectorAll('.power button').forEach((b) => b.addEventListener('click', async () => {
  const action = b.dataset.action;
  const word = action === 'stop' ? 'Stop' : 'Restart';
  if (action !== 'start' && !await ask(`${word} the server?`, 'Anyone playing gets disconnected.', word)) return;
  await changeServer(action);
}));

// ---- console
const log = $('log');
let logNext = -1;
function appendLog(lines, reset) {
  if (reset) log.replaceChildren();
  const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  for (const line of lines) {
    const row = document.createElement('div');
    row.textContent = line;
    if (/\/(ERROR|FATAL)\]/.test(line)) row.className = 'error';
    else if (/\/WARN\]/.test(line)) row.className = 'warn';
    log.append(row);
  }
  while (log.childElementCount > 3000) log.firstElementChild.remove();
  if (stick || reset) log.scrollTop = log.scrollHeight;
}
async function pollLog() {
  try {
    const d = await api(`logs?after=${logNext}`);
    logNext = d.next;
    if (d.lines.length || d.reset) appendLog(d.lines, d.reset);
  } catch { /* try again on the next tick */ }
}
const history_ = [];
let historyAt = 0;
submitBusy($('command-form'), async () => {
  const input = $('command');
  const text = input.value.trim();
  if (!text) return;
  if (await act('command', { command: text })) {
    history_.push(text);
    historyAt = history_.length;
    input.value = '';
    log.scrollTop = log.scrollHeight;
    setTimeout(pollLog, 300);
  }
  input.focus();
});
$('command').addEventListener('keydown', (e) => {
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
  e.preventDefault();
  historyAt = Math.max(0, Math.min(history_.length, historyAt + (e.key === 'ArrowUp' ? -1 : 1)));
  e.target.value = history_[historyAt] || '';
});

// ---- whitelist
let whitelistNames = [];
async function loadWhitelist() {
  const d = await act('whitelist');
  if (!d) return;
  whitelistNames = d.players.map((p) => (p.name || '').toLowerCase());
  $('whitelist-empty').hidden = d.players.length > 0;
  fill('whitelist-rows', d.players, d.players, (p) => {
    const tr = document.createElement('tr');
    const who = cell('');
    const inner = document.createElement('span');
    inner.className = 'wl-who';
    inner.append(skinHead({ uuid: p.uuid, name: p.name }, 24), document.createTextNode(p.name));
    who.append(inner);
    const act_ = cell('', 'act');
    act_.append(button('Remove', async () => {
      if (!await ask(`Remove ${p.name}?`, "They won't be able to join until they're added again.", 'Remove')) return;
      if (await act('whitelist', { name: p.name, action: 'remove' }, `Removed ${p.name}.`)) await waitForWhitelist(p.name, false);
    }));
    tr.append(who, cell(p.op ? 'Operator' : 'Player'), act_);
    return tr;
  });
}
async function waitForWhitelist(name, wanted) {
  for (let i = 0; i < 8; i += 1) {
    await new Promise((r) => setTimeout(r, 700));
    await loadWhitelist();
    if (whitelistNames.includes(name.toLowerCase()) === wanted) return true;
  }
  return false;
}
submitBusy($('whitelist-form'), async () => {
  const input = $('player');
  const name = input.value.trim();
  if (!await act('whitelist', { name, action: 'add' })) return;
  if (await waitForWhitelist(name, true)) {
    input.value = '';
    toast(`Added ${name}.`, true);
  } else {
    toast(`Couldn't add ${name}. Check the spelling, or look in the console.`);
  }
  input.focus();
});

// ---- backups
let backupsCache = [];
async function loadBackups() {
  const d = await act('backups');
  if (!d) return;
  backupsCache = d.backups;
  $('backup-empty').hidden = d.backups.length > 0;
  fill('backup-rows', d.backups, d.backups, (b) => {
    const tr = document.createElement('tr');
    const a = cell('', 'act');
    a.append(button('Make a world from this', async () => {
      openTab('worlds');
      await loadWorlds();
      $('restore-backup').value = b.file;
      $('restore-name').focus();
    }));
    tr.append(cell(when(b.time)), cell(b.world), cell(size(b.bytes), 'n'), a);
    return tr;
  });
}
$('backup-now').addEventListener('click', () => busyWhile($('backup-now'), async () => {
  const before = backupsCache.length ? backupsCache[0].file : null;
  if (!await act('backups', {}, 'Backup started. It appears here when it finishes.')) return;
  for (let i = 0; i < 90; i += 1) {
    await new Promise((r) => setTimeout(r, 5000));
    await loadBackups();
    if (backupsCache.length && backupsCache[0].file !== before) { toast('Backup finished.', true); return; }
  }
  toast('The backup is taking a while. It will appear here when it finishes.', true);
}));

// ---- worlds
let swapTarget = null;
async function loadWorlds() {
  const d = await act('worlds');
  if (!d) return;
  fill('world-rows', d.worlds, [d.worlds, d.busy], (w) => {
    const tr = document.createElement('tr');
    const name = cell(w.name);
    if (w.active) { const t = document.createElement('span'); t.className = 'tag'; t.textContent = 'Live'; name.append(t); }
    if (w.too_new) { const t = document.createElement('span'); t.className = 'tag warn'; t.textContent = 'Newer version'; name.append(t); }
    const a = cell('', 'act');
    if (!w.active) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn';
      b.textContent = 'Swap to this';
      b.addEventListener('click', () => openSwap(w));
      b.disabled = w.too_new || d.busy;
      a.append(b);
    }
    tr.append(name, cell(w.fresh ? 'Not generated yet' : w.version), cell(w.seed, 'seed'), cell(w.fresh ? '–' : size(w.bytes), 'n'), a);
    return tr;
  });
  const bk = await act('backups');
  if (bk) {
    backupsCache = bk.backups;
    const keep = $('restore-backup').value;
    fill('restore-backup', bk.backups, bk.backups, (b) => {
      const o = document.createElement('option');
      o.value = b.file;
      o.textContent = `${when(b.time)} · ${b.world} · ${size(b.bytes)}`;
      return o;
    });
    if (keep) $('restore-backup').value = keep;
  }
}
function openSwap(w) {
  swapTarget = w;
  $('swap-name').textContent = w.name;
  const parts = ['The server restarts, and players get a 60 second warning first. The current world is backed up and kept, so you can swap back.'];
  if (w.fresh) parts.push('This world is generated from its seed on first start.');
  if (w.will_upgrade) parts.push(`This world was last played on ${w.version}. Opening it upgrades it permanently.`);
  $('swap-text').textContent = parts.join(' ');
  $('swap-confirm').value = '';
  $('swap-dialog').showModal();
}
$('swap-cancel').addEventListener('click', () => $('swap-dialog').close());
submitBusy($('swap-form'), async () => {
  if (await act('worlds/swap', { slug: swapTarget.slug, confirm: $('swap-confirm').value })) {
    $('swap-dialog').close();
    await refreshJob();
    loadWorlds();
  }
});
submitBusy($('new-world-form'), async (e) => {
  if (await act('worlds', { name: $('new-name').value, seed: $('new-seed').value }, 'World added. Swap to it when you\'re ready.')) {
    e.target.reset();
    await loadWorlds();
  }
});
submitBusy($('restore-form'), async () => {
  if (await act('worlds/restore', { name: $('restore-name').value, backup: $('restore-backup').value })) {
    $('restore-name').value = '';
    await refreshJob();
  }
});
document.querySelectorAll('[data-open]').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); openTab(a.dataset.open); }));

// ---- server settings
let settingsOriginal = {};
let settingsLabels = {};
const n = (v) => (v === null || v === undefined ? '–' : fmt(v));

function settingControl(f) {
  const id = `set-${f.key}`;
  let input;
  if (f.kind === 'bool' || f.kind === 'rule') {
    input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = f.value === 'true';
    input.disabled = f.value === null;
  } else if (f.kind === 'enum') {
    input = document.createElement('select');
    for (const c of f.choices) {
      const o = document.createElement('option');
      o.value = c; o.textContent = c[0].toUpperCase() + c.slice(1);
      input.append(o);
    }
    input.value = f.value;
  } else {
    input = document.createElement('input');
    if (f.kind === 'int') { input.type = 'number'; input.min = f.min; input.max = f.max; input.step = 1; input.inputMode = 'numeric'; }
    if (f.kind === 'text') input.maxLength = f.max;
    input.value = f.value ?? '';
  }
  input.id = id;
  input.dataset.key = f.key;
  if (f.locked) input.disabled = true;
  else if (f.value !== null) settingsOriginal[f.key] = f.value;
  settingsLabels[f.key] = f.label;
  const wrap = document.createElement('div');
  wrap.className = `field ${input.type === 'checkbox' ? 'check' : ''}`;
  const label = document.createElement('label');
  label.htmlFor = id;
  label.textContent = f.label;
  const tag = document.createElement('span');
  tag.className = f.locked ? 'tag warn' : f.live ? 'tag' : 'tag dim';
  tag.textContent = f.locked ? 'Read only' : f.live ? 'Live' : 'Restart';
  label.append(tag);
  const help = document.createElement('small');
  help.textContent = f.kind === 'rule' && f.value === null ? 'Start the server to change this.' : f.help;
  if (input.type === 'checkbox') wrap.append(input, label, help);
  else wrap.append(label, input, help);
  return wrap;
}

function settingValue(input) {
  return input.type === 'checkbox' ? String(input.checked) : input.value;
}

function showRestart(keys) {
  $('restart-banner').hidden = !keys.length;
  setText($('restart-text'), `Saved, but these take effect after a restart: ${keys.map((k) => settingsLabels[k] || k).join(', ')}.`);
}

async function loadSettings() {
  const d = await act('settings');
  if (!d) return;
  settingsOriginal = {};
  const groups = d.groups.map((g) => {
    const fs = document.createElement('fieldset');
    const lg = document.createElement('legend');
    lg.textContent = g.name;
    fs.append(lg, ...g.fields.map(settingControl));
    return fs;
  });
  $('settings-groups').replaceChildren(...groups);
  $('settings-advanced').replaceChildren(...d.advanced.map((a) => settingControl({ ...a, label: a.key, kind: 'raw', help: '' })));
  showRestart(d.pending);
  loadBorders();
}

submitBusy($('settings-form'), async () => {
  const values = {};
  for (const input of $('settings-form').querySelectorAll('[data-key]')) {
    const key = input.dataset.key;
    if (input.disabled || !(key in settingsOriginal)) continue;
    const v = settingValue(input);
    if (v === settingsOriginal[key]) continue;
    if (input.type === 'number' && !input.checkValidity()) {
      toast(`${settingsLabels[key]} must be between ${input.min} and ${input.max}.`);
      input.focus();
      return;
    }
    values[key] = v;
  }
  if (!Object.keys(values).length) { toast('Nothing changed.', true); return; }
  const r = await act('settings', { values });
  if (!r) return;
  const parts = [`Saved ${r.changed.length} setting${r.changed.length === 1 ? '' : 's'}.`];
  if (r.live.length) parts.push(`Applied now: ${r.live.map((k) => settingsLabels[k] || k).join(', ')}.`);
  toast(parts.join(' '), true);
  await loadSettings();
});

$('restart-now').addEventListener('click', () => busyWhile($('restart-now'), async () => {
  let online = 0;
  try { const s = await api('system'); online = s.crafty && s.joinable ? s.crafty.online : 0; } catch { /* asked anyway */ }
  const who = online ? `${online} player${online === 1 ? ' is' : 's are'} online and will be disconnected.` : 'Nobody is online right now.';
  if (!await ask('Restart the server?', `${who} It takes about a minute to come back.`, 'Restart')) return;
  await changeServer('restart');
  await loadSettings();
}));

function numberInput(value, label, min) {
  const i = document.createElement('input');
  i.type = 'number';
  i.step = 'any';
  if (min !== undefined) i.min = min;
  i.value = value ?? '';
  i.setAttribute('aria-label', label);
  i.className = 'num';
  return i;
}

let bordersCache = [];
async function loadBorders() {
  const d = await act('borders');
  if (!d) return [];
  bordersCache = d.borders;
  fill('border-rows', d.borders, [d.borders, d.running], (b) => {
    const tr = document.createElement('tr');
    const size = numberInput(b.live ?? b.size, `${b.name} border size`, 1);
    const x = numberInput(b.center_x, `${b.name} centre X`);
    const z = numberInput(b.center_z, `${b.name} centre Z`);
    const cells = [size, x, z].map((i) => { const td = cell(''); td.append(i); return td; });
    const a = cell('', 'act');
    const apply = button('Set border', async () => {
      const body = { dim: b.dim, size: Number(size.value), center_x: Number(x.value), center_z: Number(z.value) };
      if (!(body.size >= 1)) { toast('Enter a border size.'); return; }
      if (!await ask(`Set the ${b.name} border?`, `${fmt(body.size)} blocks wide, centred on ${fmt(body.center_x)}, ${fmt(body.center_z)}. It changes straight away.`, 'Set border')) return;
      if (await act('borders', body, `${b.name} border set.`)) setTimeout(loadBorders, 800);
    });
    apply.disabled = !d.running;
    a.append(apply);
    const now = b.live !== null ? fmt(b.live) : b.size !== null ? `${fmt(b.size)} (last save)` : '–';
    tr.append(cell(b.name), cell(now, 'n'), cell(b.center_x === null ? '–' : `${n(b.center_x)}, ${n(b.center_z)}`), ...cells, a);
    return tr;
  });
  return d.borders;
}

// ---- pre-generate
const pregenRows = {};
async function loadPregen() {
  if (!bordersCache.length) await loadBorders();
  const d = await act('pregen');
  if (!d) return;
  const tbody = $('pregen-rows');
  if (!tbody.dataset.sig) {
    tbody.dataset.sig = 'built';
    tbody.replaceChildren(...d.worlds.map((w) => {
      const border = bordersCache.find((b) => b.dim === w.dim) || {};
      const width = border.live ?? border.size;
      const tr = document.createElement('tr');
      const status = cell('');
      const radius = numberInput(width ? Math.round(width / 2) : 1000, `${w.name} radius`, 16);
      const x = numberInput(border.center_x ?? 0, `${w.name} centre X`);
      const z = numberInput(border.center_z ?? 0, `${w.name} centre Z`);
      const shape = document.createElement('select');
      shape.setAttribute('aria-label', `${w.name} shape`);
      for (const s of ['square', 'circle']) { const o = document.createElement('option'); o.value = s; o.textContent = s[0].toUpperCase() + s.slice(1); shape.append(o); }
      const a = cell('', 'act pregen-act');
      const run = async (settings) => {
        const body = { dim: w.dim, ...settings };
        if (!(body.radius >= 16)) { toast('The radius must be at least 16 blocks.'); return; }
        const online = await api('system').then((s) => (s.joinable ? s.crafty.online : 0)).catch(() => 0);
        const busyNote = online ? ` ${online} player${online === 1 ? ' is' : 's are'} online and may notice lag.` : '';
        const text = `A ${body.shape} of radius ${fmt(body.radius)} around ${fmt(body.center_x)}, ${fmt(body.center_z)}. The CPU runs high until it finishes.${busyNote} This replaces any paused task for this dimension.`;
        if (!await ask(`Pre-generate the ${w.name}?`, text, 'Start')) return;
        if (await act('pregen/start', body, `Pre-generation started in the ${w.name}.`)) setTimeout(loadPregen, 1500);
      };
      const start = button('Start', () => run({ radius: Number(radius.value), center_x: Number(x.value), center_z: Number(z.value), shape: shape.value }), 'primary');
      const rerun = button('Re-run last', () => {
        const last = pregenRows[w.dim].last;
        radius.value = last.radius; x.value = last.center_x; z.value = last.center_z; shape.value = last.shape;
        return run({ radius: last.radius, center_x: last.center_x, center_z: last.center_z, shape: last.shape });
      });
      const control = (action, okText) => button(action[0].toUpperCase() + action.slice(1), async () => {
        if (action === 'cancel' && !await ask(`Cancel the ${w.name} task?`, 'A cancelled task can\'t be continued. Chunks already generated are kept.', 'Cancel task')) return;
        if (await act(`pregen/${action}`, { dim: w.dim }, okText)) setTimeout(loadPregen, 1000);
      }, action === 'cancel' ? 'danger' : '');
      const pause = control('pause', 'Paused. Progress is saved.');
      const cont = control('continue', 'Carrying on.');
      const cancel = control('cancel', 'Task cancelled.');
      a.append(start, rerun, pause, cont, cancel);
      pregenRows[w.dim] = { status, start, rerun, pause, cont, cancel, last: w.last };
      const cells = [radius, x, z, shape].map((i) => { const td = cell(''); td.append(i); return td; });
      tr.append(cell(w.name), status, ...cells, a);
      return tr;
    }));
  }
  for (const w of d.worlds) {
    const r = pregenRows[w.dim];
    if (!r) continue;
    r.last = w.last;
    let text = 'Idle';
    if (w.status === 'running') text = w.eta ? `Running: ${w.percent}% · ${fmt(w.chunks)} chunks · ${w.eta} left · ${Math.round(w.rate)}/s` : 'Running…';
    else if (w.status === 'paused') text = `Paused${w.percent ? ` at ${w.percent}%` : ''}${w.chunks ? ` · ${fmt(w.chunks)} chunks done` : ''}`;
    else if (w.status === 'finished') text = `Finished${w.took ? ` in ${w.took}` : ''} · ${fmt(w.chunks)} chunks`;
    else if (w.status === 'cancelled') text = 'Cancelled';
    else if (w.status === 'started') text = 'Starting…';
    if (w.last) text += ` · last run radius ${fmt(w.last.radius)} on ${when(w.last.time)}`;
    setText(r.status, text);
    const active = w.status === 'running' || w.status === 'started';
    r.start.disabled = !d.running || active;
    r.rerun.disabled = !d.running || active || !w.last;
    r.pause.disabled = !d.running || !active;
    r.cont.disabled = !d.running || !w.can_continue;
    r.cancel.disabled = !d.running || !(active || w.can_continue);
  }
}

// ---- modpack
let packRunning = false;
async function loadModpack() {
  const d = await act('modpack');
  if (!d) return;
  const job = d.job;
  const failed = job && job.status === 'failed';
  packRunning = !!job && job.status === 'running';
  $('pack-build').disabled = packRunning;
  $('pack-build').classList.toggle('is-busy', packRunning);
  const shown = job && job.manifest ? job.manifest : d.current;
  let line = 'No pack has been built yet.';
  if (shown) line = `Current pack: ${shown.file}, Minecraft ${shown.minecraft}, Fabric ${shown.fabric_loader}, ${shown.mods.length} mods, built ${when(shown.built_at)} by ${shown.built_by}.`;
  if (packRunning) line = 'Building the pack…';
  if (failed) line = `Build failed, so the current pack is unchanged: ${job.error}`;
  setText($('pack-status'), line);
  $('pack-status').classList.toggle('bad', !!failed);
  const notes = shown ? [...shown.skipped.map((s) => `Skipped ${s.slug}: ${s.reason}.`), ...shown.warnings] : [];
  $('pack-skipped').hidden = notes.length === 0;
  fill('pack-skipped', notes, notes, (text) => {
    const li = document.createElement('li');
    li.textContent = text;
    return li;
  });
  const mods = shown ? shown.mods : [];
  fill('pack-rows', mods, mods, (m) => {
    const tr = document.createElement('tr');
    tr.append(cell(m.name), cell(m.version, 'seed'), cell(m.type), cell(m.client), cell(m.dependency_of));
    return tr;
  });
  $('pack-empty').hidden = d.builds.length > 0;
  fill('pack-builds', d.builds, d.builds, (b) => {
    const tr = document.createElement('tr');
    tr.append(cell(when(b.built_at)), cell(b.built_by), cell(b.minecraft), cell(b.mods, 'n'), cell(b.file, 'seed'));
    return tr;
  });
}
$('pack-build').addEventListener('click', async () => {
  if (packRunning) return;
  await act('modpack/build', {});
  loadModpack();
});

// ---- system
function renderSystem(s) {
  const c = s.crafty || {};
  const cards = [
    [c.running ? 'Online' : 'Offline', c.started && c.running ? `since ${c.started}` : 'server'],
    [c.running ? `${Math.round(c.cpu)}%` : '–', 'server CPU'],
    [c.running ? size(c.mem) : '–', 'server memory'],
  ];
  if (s.memory) cards.push([size(s.memory.total - s.memory.available), `of ${size(s.memory.total)} machine memory`]);
  for (const d of s.disks) cards.push([size(d.used), `of ${size(d.total)} · ${d.label}`]);
  cards.push([s.last_backup ? when(s.last_backup) : 'None', 'last backup']);
  fill('system-cards', cards, cards, ([big, small]) => {
    const div = document.createElement('div');
    div.className = 'fact';
    const b = document.createElement('b');
    const sp = document.createElement('span');
    b.textContent = big; sp.textContent = small;
    div.append(b, sp);
    return div;
  });
}
async function loadSystem() {
  const a = await act('audit');
  if (!a) return;
  $('audit-empty').hidden = a.entries.length > 0;
  fill('audit-rows', a.entries, a.entries, (x) => {
    const tr = document.createElement('tr');
    tr.append(cell(when(x.time)), cell(x.who), cell(x.action), cell(x.detail));
    return tr;
  });
  refreshStatus();
}

const start = location.hash.slice(1);
openTab(document.querySelector(`.tabs button[data-tab="${start}"]`) ? start : 'console');
refreshStatus(); refreshJob(); pollLog();
setInterval(() => { if (currentTab === 'console') pollLog(); }, 2000);
setInterval(refreshStatus, 10000);
setInterval(refreshJob, 3000);
setInterval(() => { if (currentTab === 'modpack' && packRunning) loadModpack(); }, 2000);
setInterval(() => { if (currentTab === 'pregen') loadPregen(); }, 5000);
