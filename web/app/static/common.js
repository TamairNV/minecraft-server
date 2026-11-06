const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString('en-GB');
const bytes = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.round((b || 0) / 1e6)} MB`);

function toast(text, ok) {
  const t = document.createElement('p');
  t.className = `toast ${ok ? 'ok' : 'bad'}`;
  t.setAttribute('role', ok ? 'status' : 'alert');
  t.textContent = text;
  $('toasts').append(t);
  setTimeout(() => t.remove(), ok ? 4000 : 9000);
}

// Only touch the page when something changed, so nothing flickers on a refresh.
function setText(el, text) {
  if (el && el.textContent !== text) el.textContent = text;
}

// While an admin action is changing the server, its progress owns the chip.
let chipHeld = false;
function setChip(state, text, hold) {
  chipHeld = !!hold;
  $('chip').className = `chip ${state}`;
  setText($('chip-text'), text);
}

async function refreshChip() {
  // The admin page has its own live status, and this one can be seconds old.
  if (document.body.dataset.page === 'admin') return;
  try {
    const res = await fetch('/api/status');
    if (res.status === 401 || res.status === 403) { location.reload(); return; }
    const s = await res.json();
    if (chipHeld) return;
    setChip(s.online ? 'up' : 'down', s.online ? `${s.count}/${s.max} online` : 'Server offline');
  } catch {
    if (!chipHeld) setChip('', 'Connection lost, retrying');
  }
}
refreshChip();
setInterval(refreshChip, 15000);

function skinHead(player, size) {
  const el = document.createElement('span');
  el.className = 'skinhead';
  el.style.setProperty('--s', `${size / 8}`);
  const url = player.skin ? player.skin.url : `/api/skin/${encodeURIComponent(player.uuid)}.png`;
  if (url) el.style.setProperty('--skin', `url("${url}")`);
  el.style.setProperty('--tint', Figure.tint(player.name || ''));
  Figure.applyHatRule(el, { url });
  return el;
}
