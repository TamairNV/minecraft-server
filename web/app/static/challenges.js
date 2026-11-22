// Challenges page: daily and weekly challenges, claims, community goal, weekly leaders, hall of fame.
const BOARD_NAMES = { hours: ['Hours played', 'h'], mined: ['Blocks mined', ''], km: ['Distance', ' km'], mob_kills: ['Mobs defeated', ''] };
let data = null;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function left(ts) {
  const s = Math.max(0, Math.round(ts - Date.now() / 1000));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h left` : h ? `${h}h ${m}m left` : `${m}m left`;
}

function bar(value, target) {
  const wrap = el('div', 'pbar');
  const fill = el('span');
  fill.style.width = `${Math.min(100, (100 * value) / target)}%`;
  wrap.append(fill);
  wrap.setAttribute('role', 'progressbar');
  wrap.setAttribute('aria-valuemin', '0');
  wrap.setAttribute('aria-valuemax', String(target));
  wrap.setAttribute('aria-valuenow', String(Math.min(value, target)));
  return wrap;
}

async function claim(key, btn) {
  btn.disabled = true;
  btn.classList.add('is-busy');
  try {
    const res = await fetch('/api/claim', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ key }) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof d.detail === 'string' ? d.detail : "That didn't work.");
    toast(`Claimed: ${itemsText(d.given)}. Check your inventory!`, true);
    await refresh(true);
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = data && data.me.online === false;
    btn.classList.remove('is-busy');
  }
}

function claimButton(key, label) {
  const b = el('button', 'btn primary', label || 'Claim');
  b.type = 'button';
  if (data && data.me.online === false) {
    b.disabled = true;
    b.title = `Join the server as ${data.me.name} to claim`;
  }
  b.addEventListener('click', () => claim(key, b));
  return b;
}

const TIER_NAMES = { hard: 'Hard', easy: 'Daily' };

function card(c, weekly) {
  const art = el('article', `ch card${c.tier ? ` tier-${c.tier}` : ''}`);
  if (c.tier) art.append(el('span', `tier-badge ${c.tier}`, TIER_NAMES[c.tier]));
  art.append(el('h3', '', c.text));
  const rw = el('div', 'ch-reward');
  rw.append(el('b', '', 'Reward'), slotsFor(c.reward));
  art.append(rw);
  if (weekly) {
    const bonus = el('div', 'ch-reward bonus');
    bonus.append(el('b', '', 'First to finish'), slotsFor(c.bonus));
    if (c.bonus_winner) bonus.append(el('span', 'tag', `won by ${c.bonus_winner}`));
    art.append(bonus);
  }
  if (c.mine) {
    const mine = el('div', 'ch-mine');
    mine.append(el('span', 'muted', c.mine.done ? 'You finished this!' : `You: ${fmt(c.mine.value)} / ${fmt(c.target)}`), bar(c.mine.done ? c.target : c.mine.value, c.target));
    art.append(mine);
    c.mine.claimable.forEach((k) => art.append(claimButton(k, k.endsWith(':bonus') ? 'Claim bonus' : 'Claim reward')));
  }
  const others = c.progress.filter((p) => !c.mine || p.name !== data.me.name).slice(0, 6);
  if (others.length) {
    const ul = el('ul', 'ch-people');
    others.forEach((p) => {
      const li = el('li');
      li.append(el('span', 'who', p.name), bar(p.done ? c.target : p.value, c.target), el('span', 'num', p.done ? '✔' : fmt(p.value)));
      ul.append(li);
    });
    art.append(ul);
  } else if (!c.mine || !c.mine.value) {
    art.append(el('p', 'muted', 'Nobody has started yet.'));
  }
  return art;
}

function renderClaims(me) {
  $('claimbox').hidden = !me.claimable.length;
  const hint = $('claim-hint');
  if (hint) {
    hint.hidden = me.online !== false;
    hint.textContent = `You're not in the game right now. Join the server as ${me.name} to claim.`;
  }
  $('claims').replaceChildren(...me.claimable.map((c) => {
    const li = el('li');
    const txt = el('div');
    const what = el('b', '', `${c.tier === 'bonus' ? 'Bonus for' : 'For'} "${c.title}"`);
    if (c.level) what.prepend(el('span', `tier-badge ${c.level}`, TIER_NAMES[c.level]), ' ');
    txt.append(what, el('span', 'muted', left(c.expires).replace('left', 'to claim')));
    li.append(slotsFor(c.items), txt, claimButton(c.key));
    return li;
  }));
}

function renderBoards(boards) {
  $('boards').replaceChildren(...Object.entries(BOARD_NAMES).map(([key, [label, unit]]) => {
    const box = el('article', 'board card');
    box.append(el('h3', '', label));
    const ol = el('ol');
    (boards[key] || []).forEach((r) => ol.append(el('li', '', `${r.name}: ${fmt(r.value)}${unit}`)));
    if (!ol.childElementCount) ol.append(el('li', 'muted', 'Nobody yet this week'));
    box.append(ol);
    return box;
  }));
}

function renderHall(hall) {
  $('hall-empty').hidden = hall.length > 0;
  $('hall').replaceChildren(...hall.map((w) => {
    const box = el('article', 'board card');
    const d = new Date(w.week + 'T12:00:00');
    box.append(el('h3', '', `Week of ${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}`));
    const ul = el('ul');
    Object.entries(BOARD_NAMES).forEach(([key, [label, unit]]) => {
      const top = (w.boards[key] || [])[0];
      if (top) ul.append(el('li', '', `${label}: ${top.name} (${fmt(top.value)}${unit})`));
    });
    (w.firsts || []).forEach((f) => ul.append(el('li', '', `First to finish "${f.text}": ${f.name}`)));
    box.append(ul);
    return box;
  }));
}

function renderGoal(goal, doneCount) {
  const box = $('goal');
  if (!goal) { box.replaceChildren(el('p', 'muted', 'Every community goal is done. Legends.')); return; }
  const pct = Math.min(100, (100 * goal.value) / goal.target);
  box.replaceChildren(el('h3', '', goal.text), bar(goal.value, goal.target),
    el('p', 'muted', `${fmt(goal.value)} / ${fmt(goal.target)} (${pct.toFixed(pct < 10 ? 1 : 0)}%)${doneCount ? ` · ${doneCount} goal${doneCount > 1 ? 's' : ''} already done` : ''}`));
}

async function refresh(force) {
  try {
    const res = await fetch('/api/challenges');
    if (res.status === 401 || res.status === 403) { location.reload(); return; }
    const d = await res.json();
    const key = JSON.stringify(d);
    if (!force && data && JSON.stringify(data) === key) return;
    data = d;
  } catch {
    return;
  }
  const me = data.me;
  setText($('me-line'), me.linked ? `Playing as ${me.name}. Progress updates every few minutes (when the game saves).`
    : "Your login isn't linked to a Minecraft name yet, so you can watch but not claim. Ask Tamair to link it.");
  renderClaims(me);
  $('daily-hard').replaceChildren(...data.daily.filter((c) => c.tier === 'hard').map((c) => card(c, false)));
  $('daily').replaceChildren(...data.daily.filter((c) => c.tier !== 'hard').map((c) => card(c, false)));
  $('weekly').replaceChildren(...data.weekly.map((c) => card(c, true)));
  if (!data.daily.length) $('daily').replaceChildren(el('p', 'muted', 'Picking today\'s challenges…'));
  renderGoal(data.goal, data.goals_done);
  renderBoards(data.boards);
  renderHall(data.hall);
  tickCountdowns();
}

function tickCountdowns() {
  if (!data) return;
  setText($('day-ends'), left(data.ends.day));
  setText($('week-ends'), left(data.ends.week));
}

refresh();
setInterval(refresh, 30000);
setInterval(tickCountdowns, 30000);
