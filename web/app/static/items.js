// Minecraft item helpers shared by the challenges and admin pages: names, and rewards drawn as inventory slots
// (picture, stack count in the corner, game-style tooltip on hover or tap, enchantment glint).
function itemEl(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function itemName(id) {
  const ench = id.match(/stored_enchantments=\{"minecraft:([a-z_]+)":(\d+)\}/);
  if (ench) return `${ench[1].replace(/_/g, ' ')} ${ench[2] > 1 ? ench[2] + ' ' : ''}book`.replace(/^./, (c) => c.toUpperCase());
  const name = id.split('[')[0].replace('minecraft:', '').replace(/_/g, ' ');
  return name.charAt(0).toUpperCase() + name.slice(1);
}
const itemsText = (items) => (items || []).map((i) => `${i.count} × ${itemName(i.item)}`).join(', ');

// Rewards drawn like inventory slots: picture, stack count in the corner, game-style tooltip on hover or tap.
const ROMAN = ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'];
const ONE_LEVEL = new Set(['mending', 'silk_touch', 'infinity', 'flame', 'channeling', 'multishot', 'aqua_affinity', 'binding_curse', 'vanishing_curse']);
const itemTitle = (s) => s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

function tipLines(id) {
  const ench = id.match(/stored_enchantments=\{"minecraft:([a-z_]+)":(\d+)\}/);
  if (ench) {
    const lvl = ONE_LEVEL.has(ench[1]) && ench[2] === '1' ? '' : ` ${ROMAN[ench[2]] || ench[2]}`;
    return [['Enchanted Book', 'name rare'], [`${itemTitle(ench[1].replace(/_curse$/, ''))}${ench[1].endsWith('_curse') ? ' Curse' : ''}${lvl}`, ench[1].endsWith('_curse') ? 'lore curse' : 'lore']];
  }
  return [[itemTitle(id.split('[')[0].replace('minecraft:', '')), 'name']];
}

// Items the game draws with the purple enchantment glint.
const GLINTY = new Set(['enchanted_book', 'enchanted_golden_apple', 'experience_bottle', 'nether_star', 'end_crystal', 'debug_stick']);
const shines = (id) => GLINTY.has(id.split('[')[0].replace('minecraft:', '')) || /[[,]enchantments=/.test(id);

function placeTip(slot) {
  slot.classList.remove('tip-left');
  const tip = slot.querySelector('.mc-tip');
  if (tip && tip.getBoundingClientRect().right > document.documentElement.clientWidth - 8) slot.classList.add('tip-left');
}

function slotsFor(items) {
  const row = itemEl('div', 'slots');
  (items || []).forEach((i) => {
    const slot = itemEl('span', 'slot');
    slot.tabIndex = 0;
    slot.setAttribute('role', 'img');
    slot.setAttribute('aria-label', `${i.count} × ${itemName(i.item)}`);
    const img = itemEl('img');
    img.src = `/api/icon/${i.item.split('[')[0].replace('minecraft:', '')}.png`;
    img.alt = '';
    img.loading = 'lazy';
    img.addEventListener('error', () => { img.remove(); slot.classList.add('no-pic'); });
    slot.append(img);
    if (shines(i.item)) {
      const glint = itemEl('span', 'glint');
      glint.style.setProperty('--icon', `url("${img.src}")`);
      img.addEventListener('error', () => glint.remove());
      slot.append(glint);
    }
    if (i.count > 1) slot.append(itemEl('span', 'count', String(i.count)));
    const tip = itemEl('span', 'mc-tip');
    tip.setAttribute('aria-hidden', 'true');
    tipLines(i.item).forEach(([text, cls]) => tip.append(itemEl('span', cls, text)));
    slot.append(tip);
    slot.addEventListener('mouseenter', () => placeTip(slot));
    slot.addEventListener('focus', () => placeTip(slot));
    row.append(slot);
  });
  return row;
}

