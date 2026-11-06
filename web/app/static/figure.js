// A player built from their real skin with CSS 3D boxes. No canvas or WebGL, so a
// whole crowd stays cheap. Sizes are multiples of --s, so figures can resize freely.
const Figure = (() => {
  const box = (u, v, w, h, d) => ({ u, v, w, h, d });
  const parts = (slim) => {
    const a = slim ? 3 : 4;
    return {
      head: { x: 0, y: 4, base: box(0, 0, 8, 8, 8), over: box(32, 0, 8, 8, 8), grow: 1.13 },
      body: { x: 0, y: 14, base: box(16, 16, 8, 12, 4), over: box(16, 32, 8, 12, 4), grow: 1.06 },
      armR: { x: -(4 + a / 2), y: 14, pivot: -5, base: box(40, 16, a, 12, 4), over: box(40, 32, a, 12, 4), grow: 1.08 },
      armL: { x: 4 + a / 2, y: 14, pivot: -5, base: box(32, 48, a, 12, 4), over: box(48, 48, a, 12, 4), grow: 1.08 },
      legR: { x: -2, y: 26, pivot: -6, base: box(0, 16, 4, 12, 4), over: box(0, 32, 4, 12, 4), grow: 1.06 },
      legL: { x: 2, y: 26, pivot: -6, base: box(16, 48, 4, 12, 4), over: box(0, 48, 4, 12, 4), grow: 1.06 },
    };
  };
  const px = (n) => `calc(var(--s) * ${n}px)`;

  function tint(name) {
    let h = 0;
    for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
    return `hsl(${h} 45% 45%)`;
  }

  // Old-format skins often fill the hat area solidly. The game treats a completely
  // solid hat as no hat, so do the same or the head comes out as a black box.
  const hatChecks = new Map();
  function hasHat(url) {
    if (!hatChecks.has(url)) {
      hatChecks.set(url, new Promise((resolve) => {
        const img = new Image();
        img.onload = () => {
          if (img.naturalHeight !== 32) { resolve(true); return; }
          const c = document.createElement('canvas');
          c.width = 32; c.height = 16;
          const g = c.getContext('2d', { willReadFrequently: true });
          g.drawImage(img, -32, 0);
          const px_ = g.getImageData(0, 0, 32, 16).data;
          let solid = true;
          for (let i = 3; i < px_.length; i += 4) if (px_[i] !== 255) { solid = false; break; }
          resolve(!solid);
        };
        img.onerror = () => resolve(true);
        img.src = url;
      }));
    }
    return hatChecks.get(url);
  }
  function applyHatRule(el, skin) {
    if (skin && skin.url) {
      hasHat(skin.url).then((yes) => { if (!yes) el.classList.add('no-hat'); });
    }
  }

  function face(b, side, opts, grow) {
    const { u, v, w, h, d } = b;
    const map = {
      front: [u + d, v + d, w, h, `translateZ(${px((d / 2) * grow)})`],
      back: [u + d + w + d, v + d, w, h, `rotateY(180deg) translateZ(${px((d / 2) * grow)})`],
      right: [u, v + d, d, h, `rotateY(-90deg) translateZ(${px((w / 2) * grow)})`],
      left: [u + d + w, v + d, d, h, `rotateY(90deg) translateZ(${px((w / 2) * grow)})`],
      top: [u + d, v, w, d, `rotateX(90deg) translateZ(${px((h / 2) * grow)})`],
      bottom: [u + d + w, v, w, d, `rotateX(-90deg) translateZ(${px((h / 2) * grow)})`],
    };
    const [fx, fy, fw, fh, turn] = map[side];
    const el = document.createElement('i');
    el.style.cssText = `width:${px(fw)};height:${px(fh)};margin:${px(-fh / 2)} 0 0 ${px(-fw / 2)};`
      + `transform:${turn} scale(${grow});`;
    if (opts.url) {
      el.style.backgroundImage = `url("${opts.url}")`;
      el.style.backgroundSize = `${px(64)} ${px(opts.legacy ? 32 : 64)}`;
      el.style.backgroundPosition = `${px(-fx)} ${px(-fy)}`;
    } else if (grow === 1) {
      el.style.backgroundColor = opts.tint;
    }
    return el;
  }

  function create(player, scale) {
    const skin = player.skin || {};
    const opts = { url: skin.url, legacy: skin.legacy, tint: tint(player.name || '') };
    const root = document.createElement('div');
    root.className = 'fig';
    root.style.setProperty('--s', scale);
    root.setAttribute('aria-hidden', 'true');
    const spin = document.createElement('div');
    spin.className = 'fig-spin';
    root.append(spin);
    const all = parts(skin.slim);
    for (const [name, p] of Object.entries(all)) {
      const part = document.createElement('div');
      part.className = `part ${name}`;
      part.style.transform = `translate3d(${px(p.x)}, ${px(p.y - 16)}, 0)`;
      const swing = document.createElement('div');
      swing.className = 'swing';
      if (p.pivot) swing.style.transformOrigin = `0 ${px(p.pivot)}`;
      // Old 64x32 skins have no separate left limbs, so they reuse the right ones.
      let base = p.base;
      let over = p.over;
      if (skin.legacy && name.endsWith('L')) {
        base = all[name.replace('L', 'R')].base;
        over = null;
      }
      if (skin.legacy && name !== 'head') over = null;
      for (const side of ['front', 'back', 'right', 'left', 'top', 'bottom']) {
        swing.append(face(base, side, opts, 1));
        if (over && opts.url) {
          const f = face(over, side, opts, p.grow);
          f.classList.add('over');
          swing.append(f);
        }
      }
      part.append(swing);
      spin.append(part);
    }
    applyHatRule(root, skin);
    return root;
  }

  return { create, tint, applyHatRule };
})();
