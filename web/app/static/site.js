const SPLASHES = [
  'Punching trees since today!', 'Whitelist only, sorry creepers!', 'Bring your own bed!', 'Now with extra gravel!',
  'Do not dig straight down!', 'One sleeper is enough!', '100% friend-powered!', 'Backups every 6 hours!',
  'The sheep know what you did!', 'Pre-generated with love!', 'It was like that when I got here!',
];

async function loadOverview() {
  const res = await fetch('/api/overview');
  if (res.status === 401 || res.status === 403) { location.reload(); return null; }
  if (!res.ok) throw new Error('bad response');
  return res.json();
}

function ago(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hr ago`;
  const days = Math.round(s / 86400);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

// ---- home page
const figures = new Map();

function setSky() {
  const h = new Date().getHours();
  const time = h >= 21 || h < 6 ? 'night' : h >= 18 ? 'dusk' : h < 8 ? 'dawn' : 'day';
  $('scene').dataset.time = time;
}

function crowdScale(count) {
  const small = matchMedia('(max-width: 640px)').matches;
  const base = count <= 3 ? 7 : count <= 6 ? 5.5 : count <= 10 ? 4.2 : 3.4;
  return small ? Math.min(base, count <= 2 ? 5.5 : 3.6) : base;
}

function renderCrowd(online) {
  const crowd = $('crowd');
  const seen = new Set();
  for (const p of online) {
    seen.add(p.uuid);
    if (!figures.has(p.uuid)) {
      const who = document.createElement('div');
      who.className = 'who pop';
      const tag = document.createElement('span');
      tag.className = 'nametag';
      tag.textContent = p.name;
      who.append(tag, Figure.create(p, 7));
      crowd.append(who);
      figures.set(p.uuid, who);
    }
  }
  for (const [uuid, el] of figures) {
    if (!seen.has(uuid)) { el.remove(); figures.delete(uuid); }
  }
  const scale = crowdScale(online.length);
  crowd.querySelectorAll('.fig').forEach((f) => f.style.setProperty('--s', scale));
}

function renderScene(d) {
  const online = d.players.filter((p) => p.online);
  renderCrowd(online);
  const up = d.status.online;
  setText($('scene-title'), !up ? "The server's having a nap" : online.length === 0 ? 'All quiet out there' : online.length === 1 ? `${online[0].name} is holding the fort` : `${online.length} people are online`);
  $('sign').hidden = online.length > 0;
  setText($('sign-text'), up ? 'Nobody home. Yet.' : 'Back soon');
}

const skinOf = new Map();
function renderAwards(list) {
  const wrap = $('awards');
  $('awards-empty').hidden = list.length > 0;
  const key = JSON.stringify(list);
  if (wrap.dataset.key === key) return;
  wrap.dataset.key = key;
  wrap.replaceChildren(...list.map((a) => {
    const c = document.createElement('article');
    c.className = 'award card';
    const title = document.createElement('h3');
    title.textContent = a.title;
    const who = document.createElement('p');
    who.className = 'award-who';
    who.append(skinHead({ ...a, skin: a.skin || skinOf.get(a.uuid) }, 24), document.createTextNode(a.name));
    const text = document.createElement('p');
    text.className = 'muted';
    text.textContent = a.text;
    c.append(title, who, text);
    return c;
  }));
}

async function refreshHome() {
  let d;
  try { d = await loadOverview(); } catch { return; }
  if (!d) return;
  d.players.forEach((p) => skinOf.set(p.uuid, p.skin));
  renderScene(d);
  renderAwards(d.awards);
  setText($('t-whitelisted'), fmt(d.totals.whitelisted));
  setText($('t-hours'), fmt(d.totals.hours));
  setText($('t-mined'), fmt(d.totals.mined));
  setText($('t-km'), fmt(d.totals.km));
  setText($('t-deaths'), fmt(d.totals.deaths));
  setText($('t-world'), bytes(d.world_bytes));
}

function startHome() {
  setSky();
  setInterval(setSky, 60000);
  $('splash').textContent = SPLASHES[Math.floor(Math.random() * SPLASHES.length)];
  const copy = $('copy');
  if (copy) {
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(copy.dataset.address);
        setText($('copy-hint'), 'Copied!');
      } catch {
        setText($('copy-hint'), 'Copy it by hand');
      }
      setTimeout(() => setText($('copy-hint'), 'Click to copy'), 2000);
    });
  }
  refreshHome();
  setInterval(refreshHome, 15000);
  addEventListener('resize', () => {
    const n = $('crowd').childElementCount;
    $('crowd').querySelectorAll('.fig').forEach((f) => f.style.setProperty('--s', crowdScale(n)));
  });
}

// ---- players page
const cards = new Map();
let latest = [];

function statusText(p) {
  if (p.online) return 'Online now';
  if (!p.played) return "Hasn't joined yet";
  return `Last seen ${ago(p.stats.last_seen)}`;
}

function buildCard(p) {
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'pcard pop';
  const stage = document.createElement('div');
  stage.className = 'pcard-stage';
  stage.append(Figure.create(p, 4.4));
  const name = document.createElement('strong');
  name.className = 'pcard-name';
  name.textContent = p.name;
  const status = document.createElement('span');
  status.className = 'pcard-status';
  const line = document.createElement('span');
  line.className = 'pcard-line muted';
  card.append(stage, name, status, line);
  card.addEventListener('click', () => openPlayer(p.uuid));
  return { card, status, line };
}

function renderRoster(players) {
  latest = players;
  const roster = $('roster');
  const seen = new Set(players.map((p) => p.uuid));
  for (const [uuid, c] of cards) {
    if (!seen.has(uuid)) { c.card.remove(); cards.delete(uuid); }
  }
  players.forEach((p, i) => {
    let c = cards.get(p.uuid);
    if (!c) {
      c = buildCard(p);
      cards.set(p.uuid, c);
    }
    if (roster.children[i] !== c.card) roster.insertBefore(c.card, roster.children[i] || null);
    c.card.classList.toggle('is-online', p.online);
    setText(c.status, statusText(p));
    setText(c.line, p.op ? 'Operator' : p.played ? `${fmt(p.stats.hours)} hours played` : 'Fresh recruit');
  });
  $('roster-empty').hidden = players.length > 0;
  const on = players.filter((p) => p.online).length;
  setText($('crew-count'), `${players.length} on the whitelist, ${on} online right now. Pick anyone for a closer look.`);
}

function openPlayer(uuid) {
  const p = latest.find((x) => x.uuid === uuid);
  if (!p) return;
  const stage = $('pd-stage');
  const fig = Figure.create(p, 8);
  fig.classList.add('big');
  stage.replaceChildren(fig);
  setText($('pd-name'), p.name);
  setText($('pd-status'), `${statusText(p)}${p.op ? ' · Operator' : ''}`);
  const rows = p.played ? [
    ['Hours played', fmt(p.stats.hours)], ['Blocks mined', fmt(p.stats.mined)], ['Travelled', `${fmt(p.stats.km)} km`],
    ['Mobs defeated', fmt(p.stats.mob_kills)], ['Players defeated', fmt(p.stats.player_kills)], ['Deaths', fmt(p.stats.deaths)],
  ] : [];
  $('pd-stats').replaceChildren(...rows.flatMap(([k, v]) => {
    const dt = document.createElement('dt');
    dt.textContent = k;
    const dd = document.createElement('dd');
    dd.textContent = v;
    return [dt, dd];
  }));
  if (!rows.length) {
    const dd = document.createElement('dd');
    dd.className = 'muted wide';
    dd.textContent = 'No stats yet. Their story starts on their first join.';
    $('pd-stats').append(dd);
  }
  $('player-dialog').showModal();
}

function dragToSpin(stage) {
  let last = null;
  let yaw = 0;
  stage.addEventListener('pointerdown', (e) => {
    last = e.clientX;
    stage.setPointerCapture(e.pointerId);
    const spin = stage.querySelector('.fig-spin');
    if (spin) spin.style.animation = 'none';
  });
  stage.addEventListener('pointermove', (e) => {
    if (last === null) return;
    yaw += (e.clientX - last) * 0.8;
    last = e.clientX;
    const spin = stage.querySelector('.fig-spin');
    if (spin) spin.style.transform = `rotateX(-8deg) rotateY(${yaw}deg)`;
  });
  stage.addEventListener('pointerup', () => { last = null; });
  stage.addEventListener('pointercancel', () => { last = null; });
}

async function refreshPlayers() {
  let d;
  try { d = await loadOverview(); } catch {
    if (!latest.length) setText($('crew-count'), "Couldn't load the crew. Retrying…");
    return;
  }
  if (d) renderRoster(d.players);
}

function startPlayers() {
  const dialog = $('player-dialog');
  $('pd-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => $('pd-stage').replaceChildren());
  dragToSpin($('pd-stage'));
  refreshPlayers();
  setInterval(refreshPlayers, 15000);
}

if (document.body.dataset.page === 'home') startHome();
if (document.body.dataset.page === 'players') startPlayers();
