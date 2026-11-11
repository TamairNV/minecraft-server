// Challenges page: daily and weekly challenges, claims, community goal, weekly leaders, hall of fame.
const BOARD_NAMES = { hours: ['Hours played', 'h'], mined: ['Blocks mined', ''], km: ['Distance', ' km'], mob_kills: ['Mobs defeated', ''] };
let data = null;

function itemName(id) {
  const ench = id.match(/stored_enchantments=\{"minecraft:([a-z_]+)":(\d+)\}/);
  if (ench) return `${ench[1].replace(/_/g, ' ')} ${ench[2] > 1 ? ench[2] + ' ' : ''}book`.replace(/^./, (c) => c.toUpperCase());
  const name = id.split('[')[0].replace('minecraft:', '').replace(/_/g, ' ');
  return name.charAt(0).toUpperCase() + name.slice(1);
}
const itemsText = (items) => (items || []).map((i) => `${i.count} × ${itemName(i.item)}`).join(', ');

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
    btn.disabled = false;
    btn.classList.remove('is-busy');
  }
}

function claimButton(key, label) {
  const b = el('button', 'btn primary', label || 'Claim');
  b.type = 'button';
  b.addEventListener('click', () => claim(key, b));
  return b;
}

function card(c, weekly) {
  const art = el('article', 'ch card');
  art.append(el('h3', '', c.text));
  const rw = el('p', 'ch-reward');
  rw.append(el('b', '', 'Reward: '), document.createTextNode(itemsText(c.reward)));
  art.append(rw);
  if (weekly) {
    const bonus = el('p', 'ch-reward bonus');
    bonus.append(el('b', '', 'First to finish: '), document.createTextNode(itemsText(c.bonus)));
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
  $('claims').replaceChildren(...me.claimable.map((c) => {
    const li = el('li');
    const txt = el('div');
    txt.append(el('b', '', `${c.tier === 'bonus' ? 'Bonus: ' : ''}${itemsText(c.items)}`), el('span', 'muted', ` for "${c.title}" · ${left(c.expires).replace('left', 'to claim')}`));
    li.append(txt, claimButton(c.key));
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
  $('daily').replaceChildren(...data.daily.map((c) => card(c, false)));
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
