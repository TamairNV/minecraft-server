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
const loaders = { whitelist: loadWhitelist, backups: loadBackups, worlds: loadWorlds, settings: loadSettings, pregen: () => { loadPregen(); loadMapStatus(); }, modpack: loadModpack, network: loadNetwork, system: loadSystem, challenges: loadChallenges };
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
  loadDimensions();
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

async function loadDimensions() {
  const d = await act('dimensions');
  if (!d) return;
  if (!d.installed) {
    $('dim-rows').dataset.sig = '';
    $('dim-rows').replaceChildren(Object.assign(document.createElement('tr'), { innerHTML: '<td class="muted" colspan="3">The DimLock plugin isn\'t installed on the server.</td>' }));
    return;
  }
  fill('dim-rows', d.dims, [d.dims, d.running], (x) => {
    const tr = document.createElement('tr');
    const state = cell('');
    const tag = document.createElement('span');
    tag.className = x.locked ? 'tag warn' : 'tag';
    tag.style.marginLeft = '0';
    tag.textContent = x.locked ? 'Locked' : 'Open';
    state.append(tag);
    const a = cell('', 'act');
    const want = !x.locked;
    const btn = button(want ? `Lock ${x.name}` : `Unlock ${x.name}`, async () => {
      const text = want
        ? `Nobody else will be able to go in through a portal. Players already in ${x.name} can stay and leave.`
        : `Everyone will be able to go in straight away${d.running ? ', and it will be announced in chat and on Discord' : ' once the server starts'}.`;
      if (!await ask(`${want ? 'Lock' : 'Unlock'} ${x.name}?`, text, want ? 'Lock' : 'Unlock')) return;
      const r = await act('dimensions', { dim: x.dim, locked: want });
      if (r) toast(`${x.name} is now ${r.locked ? 'locked' : 'open'}.`, true);
      loadDimensions();
    }, want ? '' : 'primary');
    a.append(btn);
    tr.append(cell(x.name), state, a);
    return tr;
  });
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
      const render = button('Render map', async () => {
        if (await act('map/render', { dim: w.dim }, `Map render queued for the ${w.name}.`)) setTimeout(loadMapStatus, 2000);
      });
      a.append(start, rerun, pause, cont, cancel, render);
      pregenRows[w.dim] = { status, start, rerun, pause, cont, cancel, render, last: w.last };
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
    r.render.disabled = !d.running;
  }
}

// Asking BlueMap means a console command, so it's only polled while a render is running.
let mapBusy = false;
async function loadMapStatus() {
  const d = await act('map');
  if (!d) return;
  mapBusy = !!d.busy;
  let text = d.lines.join('\n');
  if (!d.running) text = 'The server is stopped.';
  else if (!d.lines.length) text = "BlueMap didn't answer. Try again in a moment.";
  setText($('map-status'), text);
}
$('map-check').addEventListener('click', () => busyWhile($('map-check'), loadMapStatus));

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
// htop-style meters: green = programs, red = kernel.
function renderThreads(threads) {
  const box = $('cpu-bars');
  if (!threads || !threads.length) { box.textContent = 'Not available.'; $('cpu-avg').textContent = ''; return; }
  if (box.children.length !== threads.length || !box.firstElementChild.classList.contains('meter')) {
    box.replaceChildren(...threads.map((_, i) => {
      const row = document.createElement('div');
      row.className = 'meter';
      row.innerHTML = '<span class="mi"></span><span class="mt"><span class="mu"></span><span class="ms"></span><span class="mv"></span></span>';
      row.querySelector('.mi').textContent = i;
      return row;
    }));
  }
  let sum = 0;
  threads.forEach((t, i) => {
    const row = box.children[i];
    const total = t.user + t.system;
    sum += total;
    row.querySelector('.mu').style.width = `${t.user}%`;
    row.querySelector('.ms').style.width = `${t.system}%`;
    row.querySelector('.mv').textContent = `${total.toFixed(1)}%`;
    row.title = `Thread ${i}: ${t.user.toFixed(1)}% programs, ${t.system.toFixed(1)}% kernel`;
  });
  const avg = sum / threads.length;
  $('cpu-avg').textContent = `· ${avg.toFixed(1)}% of the whole machine`;
  box.setAttribute('aria-label', `CPU use per thread, ${avg.toFixed(0)}% average`);
}

// 100% = one thread busy, so it goes past 100% when Minecraft uses several (like htop).
let lastServerCpu = '–';
async function pollCpu() {
  let d = null;
  try { d = await api('cpu'); } catch { return; }
  renderThreads(d.threads);
  lastServerCpu = d.server === null ? '–' : `${d.server < 10 ? d.server.toFixed(1) : Math.round(d.server)}%`;
  const card = $('cpu-card');
  if (card) card.textContent = lastServerCpu;
}

function renderSystem(s) {
  const c = s.crafty || {};
  const cards = [
    [c.running ? 'Online' : 'Offline', c.started && c.running ? `since ${c.started}` : 'server'],
    [c.running ? lastServerCpu : '–', 'Minecraft CPU (100% = one thread)', 'cpu-card'],
    [c.running ? size(c.mem) : '–', 'server memory'],
  ];
  if (s.memory) cards.push([size(s.memory.total - s.memory.available), `of ${size(s.memory.total)} machine memory`]);
  for (const d of s.disks) cards.push([size(d.used), `of ${size(d.total)} · ${d.label}`]);
  cards.push([s.last_backup ? when(s.last_backup) : 'None', 'last backup']);
  fill('system-cards', cards, cards, ([big, small, id]) => {
    const div = document.createElement('div');
    div.className = 'fact';
    const b = document.createElement('b');
    const sp = document.createElement('span');
    b.textContent = big; sp.textContent = small;
    if (id) b.id = id;
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
  pollCpu();
}

// ---- challenges
let ch = null;
const chScale = (m) => {
  const keys = (m && m.keys) || [];
  if (m && m.cat === 'custom' && keys.length && keys.every((k) => k.endsWith('_one_cm'))) return 100;
  if (m && m.cat === 'custom' && keys.length && keys.every((k) => k.endsWith('play_time') || k.endsWith('sneak_time'))) return 72000;
  return 1;
};
const itemsToText = (items) => (items || []).map((i) => `${i.count} ${i.item.replace(/^minecraft:/, '')}`).join(', ');
function textToItems(text) {
  return text.split(/,(?![^\[]*\])/).map((p) => p.trim()).filter(Boolean).map((p) => {
    const m = p.match(/^(\d+)\s+(.+)$/);
    const item = (m ? m[2] : p).trim();
    return { item: item.split('[')[0].includes(':') ? item : `minecraft:${item}`, count: m ? Number(m[1]) : 1 };
  });
}
const metricToText = (m) => `${m.cat}: ${m.keys.join(', ')}`;
function textToMetric(t) {
  const [cat, rest] = t.split(':', 2).length > 1 ? [t.slice(0, t.indexOf(':')), t.slice(t.indexOf(':') + 1)] : [t, ''];
  return { cat: cat.trim(), keys: rest.split(',').map((k) => k.trim()).filter(Boolean) };
}
function input(value, label, cls) {
  const i = document.createElement('input');
  i.value = value ?? '';
  i.setAttribute('aria-label', label);
  if (cls) i.className = cls;
  return i;
}

async function loadChallenges() {
  const d = await act('progress');
  if (!d) return;
  ch = d;
  const cur = [...d.daily.map((x) => ['Today', 'daily', x]), ...d.weekly.map((x) => ['This week', 'weekly', x])];
  $('ch-current').replaceChildren(...cur.map(([label, kind, x]) => {
    const tr = document.createElement('tr');
    const sel = document.createElement('select');
    sel.setAttribute('aria-label', `Swap ${x.text}`);
    d.pools[kind].filter((e) => e.enabled !== false && e.id !== x.id).forEach((e) => sel.append(new Option(e.text.replace('{n}', Math.round((e.target / chScale(e.metric)) * 10) / 10), e.id)));
    const swapTd = cell(''); swapTd.append(sel);
    const a = cell('', 'act');
    a.append(button('Swap', async () => {
      if (await act('progress/swap', { iid: x.iid, pool_id: sel.value }, 'Swapped.')) loadChallenges();
    }));
    const rewards = cell('', 'ch-rewards');
    rewards.append(slotsFor(x.reward));
    if (x.bonus && x.bonus.length) {
      const first = slotsFor(x.bonus);
      first.prepend(itemEl('span', 'first-label', 'First:'));
      rewards.append(first);
    }
    tr.append(cell(label), cell(x.text), rewards, swapTd, a);
    return tr;
  }));
  $('ch-log-empty').hidden = d.log.length > 0;
  $('ch-log').replaceChildren(...d.log.map((r) => {
    const tr = document.createElement('tr');
    const a = cell('', 'act');
    a.append(button('Give again', async () => {
      if (!await ask(`Give this reward to ${r.name} again?`, 'They must be online. This gives the items a second time.', 'Give again')) return;
      if (await act('progress/give-again', { key: r.key, name: r.name }, `Given to ${r.name}.`)) loadChallenges();
    }));
    tr.append(cell(when(r.at)), cell(r.name), cell(r.key), cell(r.state === 'done' ? 'Given' : r.state), a);
    return tr;
  }));
  const links = [...d.links.manual.map((l) => ({ ...l, from: 'Here' })), ...d.links.onboard.map((l) => ({ ...l, from: 'Discord sign-up' }))];
  $('ch-links').replaceChildren(...links.map((l) => {
    const tr = document.createElement('tr');
    const a = cell('', 'act');
    if (l.from === 'Here') a.append(button('Remove', async () => { if (await act('progress/links/remove', { email: l.email }, 'Link removed.')) loadChallenges(); }));
    tr.append(cell(l.email), cell(l.name), cell(l.from), a);
    return tr;
  }));
  renderPools(d.pools);
  renderGoals(d.goals);
}

function poolRow(kind, e) {
  const tr = document.createElement('tr');
  const s = chScale(e.metric);
  const f = {
    id: input(e.id, 'Id', 'mono'), text: input(e.text, 'Text'), metric: input(metricToText(e.metric), 'Stats', 'mono'),
    target: input(Math.round((e.target / s) * 10) / 10, 'Target', 'num'), reward: input(itemsToText(e.reward), 'Reward'),
  };
  if (kind === 'weekly') f.bonus = input(itemsToText(e.bonus), 'First-to-finish bonus');
  const on = document.createElement('input');
  on.type = 'checkbox'; on.checked = e.enabled !== false; on.setAttribute('aria-label', 'Enabled');
  tr.read = () => {
    const metric = textToMetric(f.metric.value);
    const out = { id: f.id.value.trim(), text: f.text.value, metric, target: Math.round(Number(f.target.value) * chScale(metric)), reward: textToItems(f.reward.value), enabled: on.checked };
    if (f.bonus) out.bonus = textToItems(f.bonus.value);
    return out;
  };
  const cells = Object.values(f).map((i) => { const td = cell(''); td.append(i); return td; });
  const onTd = cell(''); onTd.append(on);
  const a = cell('', 'act');
  a.append(button('✕', () => tr.remove()));
  tr.append(...cells, onTd, a);
  return tr;
}

function renderPools(pools) {
  $('ch-pools').replaceChildren(...['daily', 'weekly'].map((kind) => {
    const box = document.createElement('div');
    const h = document.createElement('h3');
    h.textContent = kind === 'daily' ? 'Daily pool' : 'Weekly pool';
    h.className = 'gap';
    const t = document.createElement('table');
    t.className = 'list pool';
    t.innerHTML = `<thead><tr><th>Id</th><th>Text ({n} = target)</th><th>Stats</th><th>Target</th><th>Reward</th>${kind === 'weekly' ? '<th>First-to-finish bonus</th>' : ''}<th>On</th><th></th></tr></thead>`;
    const body = document.createElement('tbody');
    body.id = `ch-pool-${kind}`;
    body.append(...pools[kind].map((e) => poolRow(kind, e)));
    t.append(body);
    const add = button('Add challenge', () => body.append(poolRow(kind, { id: `${kind[0]}-new-${body.children.length + 1}`, text: 'Do a thing {n} times', metric: { cat: 'custom', keys: ['minecraft:jump'] }, target: 10, reward: [{ item: 'minecraft:dirt', count: 1 }], bonus: kind === 'weekly' ? [{ item: 'minecraft:diamond', count: 1 }] : undefined, enabled: true })));
    box.append(h, t, add);
    return box;
  }));
}

function goalRow(g) {
  const tr = document.createElement('tr');
  const s = chScale(g.metric);
  const f = { id: input(g.id, 'Id', 'mono'), text: input(g.text, 'Text'), metric: input(metricToText(g.metric), 'Stats', 'mono'), target: input(Math.round((g.target / s) * 10) / 10, 'Target', 'num') };
  tr.read = () => { const metric = textToMetric(f.metric.value); return { id: f.id.value.trim(), text: f.text.value, metric, target: Math.round(Number(f.target.value) * chScale(metric)) }; };
  const a = cell('', 'act');
  a.append(button('✕', () => tr.remove()));
  tr.append(...Object.values(f).map((i) => { const td = cell(''); td.append(i); return td; }), a);
  return tr;
}
function renderGoals(goals) { $('ch-goals').replaceChildren(...goals.map(goalRow)); }

$('ch-goal-add').addEventListener('click', () => $('ch-goals').append(goalRow({ id: `g-new-${$('ch-goals').children.length + 1}`, text: 'Mine {n} blocks together', metric: { cat: 'mined', keys: ['*'] }, target: 100000 })));
$('ch-pools-save').addEventListener('click', (e) => busyWhile(e.currentTarget, async () => {
  const pools = { daily: [...$('ch-pool-daily').children].map((r) => r.read()), weekly: [...$('ch-pool-weekly').children].map((r) => r.read()) };
  if (await act('progress/pools', { pools }, 'Pools saved. New picks use them from the next day or week.')) loadChallenges();
}));
$('ch-goals-save').addEventListener('click', (e) => busyWhile(e.currentTarget, async () => {
  if (await act('progress/goals', { goals: [...$('ch-goals').children].map((r) => r.read()) }, 'Goals saved.')) loadChallenges();
}));
['daily', 'weekly'].forEach((kind) => $(`ch-reroll-${kind}`).addEventListener('click', (e) => busyWhile(e.currentTarget, async () => {
  if (!await ask(`Re-roll ${kind === 'daily' ? "today's" : "this week's"} challenges?`, 'New ones are picked at random. Rewards not yet claimed for the old ones are dropped.', 'Re-roll')) return;
  if (await act('progress/reroll', { kind }, 'New challenges picked.')) loadChallenges();
})));
$('ch-link-form').addEventListener('submit', (e) => {
  e.preventDefault();
  busyWhile(e.submitter, async () => {
    if (await act('progress/links', { email: $('ch-link-email').value, name: $('ch-link-name').value.trim() }, 'Linked.')) { $('ch-link-form').reset(); loadChallenges(); }
  });
});
$('ch-test').addEventListener('click', (e) => busyWhile(e.currentTarget, async () => {
  const items = textToItems($('ch-test-items').value);
  if (!items.length) { toast('Type the items first, like 3 diamond.'); return; }
  await act('progress/test-reward', { items }, 'Given. Check your inventory.');
}));
$('ch-reset').addEventListener('click', (e) => busyWhile(e.currentTarget, async () => {
  const typed = prompt('This starts a new season and clears every leaderboard, award, goal, hall of fame and claim (backed up first). Type reset to confirm.');
  if (typed === null) return;
  if (await act('progress/reset', { confirm: typed }, 'Fresh start. Everything counts from now.')) loadChallenges();
}));

// ---- network
// Numbers come from the netmon containers; this tab only reads them (every 5 s while open).
let netRange = '1h';
try { netRange = localStorage.getItem('netRange') || '1h'; } catch { /* private window */ }
const NET_COLORS = { a: 'var(--grass)', b: 'var(--gold)', c: '#7fb8ff', bad: '#e0604a' };
const mbit = (bps) => (bps === null || bps === undefined ? null : (bps * 8) / 1e6);
const fixed = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const ago = (s) => (s === null || s === undefined ? 'never' : s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`);
const kb = (b) => (b === null || b === undefined ? '–' : b < 1024 ? `${b} B` : b < 1048576 ? `${Math.round(b / 1024)} KB` : `${(b / 1048576).toFixed(1)} MB`);

function nearest(values, i) {
  for (let k = 0; k < values.length; k++) {
    for (const j of [i - k, i + k]) if (values[j] !== null && values[j] !== undefined) return values[j];
  }
  return null;
}

function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v);
}

function timeLabel(t, span) {
  const d = new Date(t * 1000);
  return span > 86400 ? d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric' }) : d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

// Small SVG line chart: lines = [{label, color, values}], bands = indexes drawn as red columns.
function lineChart(el, times, lines, opts = {}) {
  const W = 640, H = 180, L = 40, R = 8, T = 10, B = 22;
  const all = lines.flatMap((l) => l.values).filter((v) => v !== null && v !== undefined);
  if (times.length < 2) { el.innerHTML = '<p class="hint net-nodata">Not enough data yet. Give it a minute.</p>'; return; }
  const max = niceMax(Math.max(opts.min || 0, ...all));
  const t0 = times[0], t1 = times[times.length - 1];
  const x = (t) => L + ((t - t0) / Math.max(1, t1 - t0)) * (W - L - R);
  const y = (v) => T + (1 - v / max) * (H - T - B);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img');
  const mk = (tag, attrs) => { const e = document.createElementNS(ns, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); svg.append(e); return e; };
  const step = (W - L - R) / Math.max(1, times.length - 1);
  (opts.bands || []).forEach((i) => mk('rect', { x: x(times[i]) - step / 2, y: T, width: Math.max(2, step), height: H - T - B, fill: NET_COLORS.bad, opacity: 0.28 }));
  for (let i = 0; i <= 4; i++) {
    const v = (max * i) / 4;
    mk('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), class: 'grid' });
    mk('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end', class: 'axis' }).textContent = v >= 10 ? Math.round(v) : +v.toFixed(1);
  }
  for (let i = 0; i <= 4; i++) {
    const t = t0 + ((t1 - t0) * i) / 4;
    mk('text', { x: x(t), y: H - 6, 'text-anchor': i === 0 ? 'start' : i === 4 ? 'end' : 'middle', class: 'axis' }).textContent = timeLabel(t, t1 - t0);
  }
  for (const l of lines) {
    let d = '';
    l.values.forEach((v, i) => {
      if (v === null || v === undefined) { if (!l.sparse) d += ' '; return; }
      d += `${d && !d.endsWith(' ') ? 'L' : 'M'}${x(times[i]).toFixed(1)},${y(Math.min(v, max)).toFixed(1)}`;
    });
    mk('path', { d: d.trim().replace(/ +/g, ''), fill: 'none', stroke: l.color, 'stroke-width': 2, 'stroke-linejoin': 'round' });
  }
  const cursor = mk('line', { y1: T, y2: H - B, class: 'cursor', visibility: 'hidden' });
  const tip = document.createElement('div');
  tip.className = 'net-tip';
  tip.hidden = true;
  svg.addEventListener('pointermove', (e) => {
    const box = svg.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(times.length - 1, Math.round((px - L) / step)));
    cursor.setAttribute('x1', x(times[i])); cursor.setAttribute('x2', x(times[i])); cursor.setAttribute('visibility', 'visible');
    tip.replaceChildren(Object.assign(document.createElement('b'), { textContent: new Date(times[i] * 1000).toLocaleString('en-GB', { dateStyle: 'short', timeStyle: 'medium' }) }),
      ...lines.map((l) => { const r = document.createElement('div'); r.innerHTML = '<i></i><span></span>'; r.querySelector('i').style.background = l.color; r.querySelector('span').textContent = `${l.label}: ${fixed(l.sparse ? nearest(l.values, i) : l.values[i], opts.digits ?? 1)}`; return r; }));
    if ((opts.bands || []).includes(i)) tip.append(Object.assign(document.createElement('div'), { textContent: 'Public join check failed', className: 'bad' }));
    tip.hidden = false;
    tip.style.left = `${Math.min(box.width - 170, Math.max(0, e.clientX - box.left + 12))}px`;
  });
  svg.addEventListener('pointerleave', () => { cursor.setAttribute('visibility', 'hidden'); tip.hidden = true; });
  const legend = document.createElement('div');
  legend.className = 'net-legend';
  for (const l of lines) { const s = document.createElement('span'); s.innerHTML = '<i></i>'; s.querySelector('i').style.background = l.color; s.append(l.label); legend.append(s); }
  svg.setAttribute('aria-label', `${opts.title || 'Chart'}: ${lines.map((l) => `${l.label} now ${fixed(l.values[l.values.length - 1])}`).join(', ')}`);
  el.replaceChildren(svg, tip, legend);
}

function netCard(big, small, level) {
  const div = document.createElement('div');
  div.className = `fact${level ? ` ${level}` : ''}`;
  const b = document.createElement('b');
  const sp = document.createElement('span');
  b.textContent = big; sp.textContent = small;
  div.append(b, sp);
  return div;
}

const levelFor = (v, warn, crit) => (v === null || v === undefined ? '' : v >= crit ? 'crit' : v >= warn ? 'warn' : '');

function renderNetwork(d) {
  const crit = d.alerts.filter((a) => a.level === 'crit').length;
  $('net-alerts').replaceChildren(...d.alerts.map((a) => {
    const div = document.createElement('div');
    div.className = `net-alert ${a.level}`;
    const b = document.createElement('b'); b.textContent = a.title;
    const p = document.createElement('p'); p.textContent = a.detail;
    div.append(b, p);
    return div;
  }));
  const tab = document.querySelector('.tabs button[data-tab="network"]');
  tab.classList.toggle('alerting', d.alerts.length > 0);
  tab.classList.toggle('critical', crit > 0);

  const s = d.summary || {};
  const keys = new Set(d.alerts.map((a) => a.key.split(':')[0]));
  const hop = (label, sub, bad) => { const li = document.createElement('li'); li.className = bad ? 'bad' : d.running ? 'ok' : ''; li.innerHTML = '<b></b><span></span>'; li.querySelector('b').textContent = label; li.querySelector('span').textContent = sub; return li; };
  $('net-path').replaceChildren(
    hop('Players', `${d.players.length} connected`, false),
    hop(d.public_host || 'Public address', s.public ? (s.public.ok ? `join check ok · ${fixed(s.public.connect, 0)} ms` : 'join check failing') : 'not checked yet', keys.has('public')),
    hop('Proxy', s.isp ? `internet ping ${fixed(s.isp, 0)} ms` : (d.peer || ''), keys.has('proxy') || keys.has('endpoint')),
    hop('WireGuard', s.rtt !== null && s.rtt !== undefined ? `${fixed(s.rtt)} ms · ${fixed(s.loss)}% loss` : 'no replies', keys.has('tunnel') || keys.has('loss')),
    hop('This server', s.local ? (s.local.ok ? 'game answering' : 'game not answering') : '', s.local && !s.local.ok),
  );

  const cards = [
    netCard(!d.running ? 'No data' : crit ? `${crit} down` : d.alerts.length ? `${d.alerts.length} warning${d.alerts.length > 1 ? 's' : ''}` : 'Healthy', d.running ? `updated ${ago(d.stale)}` : 'monitor stopped', !d.running || crit ? 'crit' : d.alerts.length ? 'warn' : 'good'),
    netCard(`${fixed(s.rtt)} ms`, `tunnel ping · jitter ${fixed(s.jitter)} ms`, levelFor(s.rtt, 60, 150)),
    netCard(`${fixed(s.loss)}%`, 'packet loss, last 5 min', levelFor(s.loss, 2, 10)),
    netCard(`${fixed(s.retrans)}%`, 'game traffic resent, 5 min', levelFor(s.retrans, 2, 5)),
    netCard(s.handshake_age === null || s.handshake_age === undefined ? '–' : ago(s.handshake_age).replace(' ago', ''), 'since WireGuard handshake', levelFor(s.handshake_age, 150, 180)),
    netCard(`${fixed(mbit(s.tx))} Mbit/s`, `to players now · peak ${fixed(mbit(s.tx_peak_24h))} today${d.uplink_mbit ? ` of ${d.uplink_mbit}` : ''}`),
    netCard(`${fixed(mbit(s.rx), 2)} Mbit/s`, 'from players now'),
    netCard(s.public ? (s.public.ok ? 'Joinable' : 'Failing') : '–', s.public ? `via ${d.public_host} · ${ago(s.public.age)}` : 'public join check', s.public && !s.public.ok ? 'crit' : ''),
    netCard(s.isp ? `${fixed(s.isp, 0)} ms` : '–', "proxy's internet ping (outside the tunnel)"),
    netCard(s.voice_drops === null || s.voice_drops === undefined ? '–' : String(s.voice_drops), 'voice packets dropped (since start)'),
  ];
  if (s.dns && s.endpoint) cards.push(netCard(s.dns, s.endpoint.startsWith(`${s.dns}:`) ? 'proxy IP, matches tunnel' : `proxy IP · tunnel still uses ${s.endpoint}`, s.endpoint.startsWith(`${s.dns}:`) ? '' : 'warn'));
  $('net-cards').replaceChildren(...cards);

  const span = { '15m': 900, '1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800 }[d.range];
  const pts = d.series;
  const times = pts.map((p) => p.t);
  const bands = pts.map((p, i) => (p.pub_ok === false ? i : -1)).filter((i) => i >= 0);
  lineChart($('chart-latency'), times, [
    { label: 'Tunnel ping', color: NET_COLORS.a, values: pts.map((p) => p.rtt) },
    // Measured every 30 s, so these join up across the gaps
    { label: "Proxy's internet", color: NET_COLORS.b, values: pts.map((p) => p.isp), sparse: true },
    { label: 'Public join connect', color: NET_COLORS.c, values: pts.map((p) => p.pub_ms), sparse: true },
  ], { bands, title: 'Latency', min: 50, span });
  lineChart($('chart-loss'), times, [
    { label: 'Tunnel loss', color: NET_COLORS.bad, values: pts.map((p) => p.loss) },
    { label: 'Game traffic resent', color: NET_COLORS.b, values: pts.map((p) => p.retrans) },
  ], { title: 'Loss', min: 2, digits: 2 });
  lineChart($('chart-traffic'), times, [
    { label: 'To players', color: NET_COLORS.a, values: pts.map((p) => mbit(p.tx)) },
    { label: 'From players', color: NET_COLORS.b, values: pts.map((p) => mbit(p.rx)) },
  ], { title: 'Traffic', min: 0.5, digits: 2 });

  $('net-players-empty').hidden = d.players.length > 0;
  fill('net-players', d.players, d.players, (p) => {
    const tr = document.createElement('tr');
    const name = cell(p.name || 'Unknown');
    if (!p.name) name.className = 'muted';
    const ping = cell(p.rtt === null || p.rtt === undefined ? '–' : `${Math.round(p.rtt)} ms${p.jitter ? ` ±${Math.round(p.jitter)}` : ''}`, 'n');
    ping.dataset.level = levelFor(p.rtt, 150, 250);
    const re = cell(p.retrans_pct === null ? '–' : `${fixed(p.retrans_pct)}%`, 'n');
    re.dataset.level = levelFor(p.retrans_pct, 2, 5);
    const q = cell(kb(p.send_q), 'n');
    q.dataset.level = levelFor(p.send_q, 128 * 1024, 512 * 1024);
    tr.append(name, cell(p.route), ping, re, q, cell(p.delivery ? `${fixed(p.delivery / 1e6)} Mbit/s` : '–', 'n'));
    return tr;
  });

  $('net-events-empty').hidden = d.events.length > 0;
  fill('net-events', d.events, d.events, (e) => {
    const tr = document.createElement('tr');
    const tag = cell(e.state === 'start' ? (e.level === 'crit' ? 'Problem' : 'Warning') : 'Cleared');
    tag.dataset.level = e.state === 'start' ? e.level : '';
    tr.append(cell(when(e.t)), cell(e.title), tag);
    return tr;
  });
  document.querySelectorAll('.net-range button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.range === d.range)));
}

async function loadNetwork() {
  let d;
  try { d = await api(`network?range=${encodeURIComponent(netRange)}`); } catch (e) { $('net-alerts').textContent = e.message; return; }
  renderNetwork(d);
}
document.querySelectorAll('.net-range button').forEach((b) => b.addEventListener('click', () => {
  netRange = b.dataset.range;
  try { localStorage.setItem('netRange', netRange); } catch { /* private window */ }
  loadNetwork();
}));

const start = location.hash.slice(1);
openTab(document.querySelector(`.tabs button[data-tab="${start}"]`) ? start : 'console');
refreshStatus(); refreshJob(); pollLog(); if (currentTab !== 'network') loadNetwork();
setInterval(() => { if (currentTab === 'console') pollLog(); }, 2000);
setInterval(refreshStatus, 10000);
setInterval(() => { if (currentTab === 'system' && !document.hidden) pollCpu(); }, 1500);
setInterval(refreshJob, 3000);
setInterval(() => { if (currentTab === 'network' && !document.hidden) loadNetwork(); }, 5000);
// Elsewhere, refresh now and then so the Network tab's warning dot shows up.
setInterval(() => { if (currentTab !== 'network' && !document.hidden) loadNetwork(); }, 30000);
setInterval(() => { if (currentTab === 'modpack' && packRunning) loadModpack(); }, 2000);
setInterval(() => { if (currentTab === 'pregen') loadPregen(); }, 5000);
setInterval(() => { if (currentTab === 'pregen' && mapBusy) loadMapStatus(); }, 15000);
