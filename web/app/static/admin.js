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
const loaders = { whitelist: loadWhitelist, backups: loadBackups, worlds: loadWorlds, modpack: loadModpack, system: loadSystem };
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
$('pregen').addEventListener('click', async () => {
  if (!await ask('Pre-generate the live world?', 'The server stays up, but the CPU runs high for about an hour.', 'Start')) return;
  await busyWhile($('pregen'), () => act('pregen', {}, 'Pre-generation started. Progress shows in the console.'));
});

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
