/* 漫画面板编辑器
 *
 * 设计取舍
 *  ① 渲染在服务端：前端只发坐标，选择框直接用服务端返回的包围盒 ——
 *     前后端不会各算一套排版而错位
 *  ② 拖动时先本地跟手，松手才 PATCH；避免每移动 1px 打一次接口
 *  ③ 气泡高度由文字自动算（服务端），前端只改宽度
 *  ④ 页码是身份、顺序是 order：调顺序不动 number
 */
'use strict';

const API = (() => {
  const b = document.querySelector('base');
  let h = b ? (b.getAttribute('href') || '/') : '/';
  return h.endsWith('/') ? h : h + '/';
})();

const S = {
  projects: [],
  proj: null,          // 项目数据
  page: null,          // 当前页
  sel: null,           // 主选中元素 id（属性面板显示它）
  sels: new Set(),     // ★ 多选集合（含 sel）
  boxes: {},           // element_id -> [x,y,w,h] 归一化（服务端给的）
  drag: null,
  zoom: 1,             // 1 = 适应窗口
  busy: false,
  revision: null,      // ★ 乐观并发：服务端当前版本号
  guides: [],          // ★ 当前显示的对齐参考线 {x|y, pos}
};

//: 吸附阈值（画布宽/高的比例）
const SNAP = 0.008;
//: 对齐线候选（相对画布）：左 / 中 / 右、上 / 中 / 下
const SNAP_X = [0, 0.5, 1];
const SNAP_Y = [0, 0.5, 1];

//: 裁剪后每个方向至少保留的比例
//  ★ 服务端按 int() 取整（render.py:135），保留太少会被截成 0 像素 ——
//    那时 `box[2] > box[0]` 不成立，整段裁剪被**静默忽略**，用户会以为功能坏了。
const CROP_MIN = 0.03;

//: 当前裁剪会话（null = 不在裁剪中）。见「裁剪」一节。
let cropState = null;

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const el = (t, c, h) => {
  const n = document.createElement(t);
  if (c) n.className = c;
  if (h !== undefined) n.innerHTML = h;
  return n;
};
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const round = (v, p = 4) => Math.round(v * 10 ** p) / 10 ** p;

function url(p) { return API + String(p).replace(/^\/+/, ''); }

async function api(path, opt) {
  const r = await fetch(url(path), opt);
  const ct = r.headers.get('content-type') || '';
  if (!r.ok) {
    let m = r.statusText;
    try { m = (await r.json()).detail || m; } catch (e) { /* ignore */ }
    throw new Error(m);
  }
  return ct.includes('json') ? r.json() : r.text();
}
const jpost = (p, body) => api(p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body || {}),
});
const jpatch = (p, body) => api(p, {
  method: 'PATCH', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body || {}),
});

let toastTimer;
function toast(msg, isErr) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast on' + (isErr ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), isErr ? 4200 : 2000);
}

function status(msg) {
  $('#statusMsg').innerHTML = S.busy
    ? `<span class="spinner"></span>${msg}` : (msg || '—');
}

/* ══════════════════════════════════════════════════
 * 项目
 * ══════════════════════════════════════════════════ */
async function loadProjects(keepSel) {
  S.projects = await api('/api/edit/projects');
  const sel = $('#projectSel');
  const cur = keepSel || (S.proj && S.proj.name);
  sel.innerHTML = '';
  if (!S.projects.length) {
    sel.appendChild(el('option', '', '（还没有项目）'));
    return;
  }
  S.projects.forEach(p => {
    const o = el('option', '', `${p.title || p.name}（${p.pages}页）`);
    o.value = p.name;
    if (p.name === cur) o.selected = true;
    sel.appendChild(o);
  });
}

async function openProject(name) {
  S.busy = true; status('加载中…');
  try {
    S.proj = await api('/api/edit/projects/' + encodeURIComponent(name));
    const pages = orderedPages();
    S.page = pages[0] || null;
    S.revision = S.proj.revision != null ? S.proj.revision : null;
    setSelection([], null);
    await loadProjects(name);
    renderAssets();
    renderPages();
    await showPage(S.page ? S.page.id : null);
  } catch (e) { toast('加载失败：' + e.message, true); }
  finally { S.busy = false; status(''); }
}

function orderedPages() {
  return (S.proj.pages || []).slice().sort((a, b) =>
    (a.order - b.order) || (a.number - b.number));
}

function pageIndex() {
  return orderedPages().findIndex(p => S.page && p.id === S.page.id);
}

/* ══════════════════════════════════════════════════
 * 画布
 * ══════════════════════════════════════════════════ */
async function showPage(pageId) {
  if (!pageId) { renderEmpty(); return; }
  const p = S.proj.pages.find(x => x.id === pageId);
  if (!p) { renderEmpty(); return; }
  S.page = p;
  setSelection([], null);
  renderPages();
  renderLayers();
  renderProps();
  await drawCanvas();
}

function renderEmpty() {
  S.page = null;
  $('#canvasHost').innerHTML = `
    <div class="empty-canvas">
      <div class="ec-icon">▢</div>
      <div class="ec-title">空白画布</div>
      <div class="ec-sub">左侧上传漫画图片，然后拖到画布上</div>
      <div class="ec-actions">
        <button class="primary" onclick="document.getElementById('fileInput').click()">上传图片</button>
        <button class="ghost" onclick="document.getElementById('btnAddPage').click()">新建一页</button>
      </div>
    </div>`;
  $('#pageLabel').textContent = '—';
  $('#layerCount').textContent = '0';
}

const EMPTY_HTML = `
  <div class="empty-canvas">
    <div class="ec-icon">▢</div>
    <div class="ec-title">这一页还是空的</div>
    <div class="ec-sub">从左边拖一张图进来，或者加一个气泡</div>
    <div class="ec-actions">
      <button class="primary" id="ecUpload">上传图片</button>
      <button class="ghost" id="ecBubble">加气泡</button>
      <button class="ghost" id="ecText">加标题</button>
    </div>
  </div>`;

async function drawCanvas(fast) {
  if (!S.page) { renderEmpty(); return; }
  const host = $('#canvasHost');

  if (!S.page.elements.length) {
    if (cropState) exitCrop();          // 页里没东西了，裁剪框就没有意义了
    host.innerHTML = EMPTY_HTML;
    const bind = (id, fn) => { const b = $(id); if (b) b.onclick = fn; };
    bind('#ecUpload', () => $('#fileInput').click());
    bind('#ecBubble', () => addElement('bubble'));
    bind('#ecText', () => addElement('text'));
    $('#layerCount').textContent = '0';
    updatePageLabel();
    return;
  }

  // ① 拿服务端渲染图 + 元素包围盒
  const bearer = `?w=${Math.round(1240)}&boxes=1&t=${Date.now()}`;
  let img, boxes = {};
  const resp = await fetch(url(`/api/edit/projects/${S.proj.name}/pages/${S.page.id}/render.png${bearer}`));
  if (!resp.ok) { toast('渲染失败', true); return; }
  const hdr = resp.headers.get('X-Element-Boxes');
  if (hdr) {
    try { boxes = JSON.parse(atob(hdr)); } catch (e) { boxes = {}; }
  }
  S.boxes = boxes;
  const blob = await resp.blob();
  img = URL.createObjectURL(blob);

  // ② 组 DOM
  const holder = el('div', 'page-holder');
  const im = el('img');
  im.src = img;
  im.onload = () => { applyZoom(holder); };
  holder.appendChild(im);
  host.innerHTML = '';
  host.appendChild(holder);

  // ③ 元素覆盖层
  const natW = S.page.width;
  orderedByZ().forEach(e => {
    const b = boxes[e.id];
    if (!b) return;
    holder.appendChild(makeOverlay(e, b));
  });

  // ④ 点空白：取消选择，拖动则框选
  holder.onpointerdown = (ev) => {
    if (ev.target !== holder && ev.target !== im) return;
    if (cropState) return;              // 裁剪中点空白不算取消选择（否则框会突然消失）
    if (!ev.shiftKey) setSelection([], null);
    renderProps(); renderLayers(); refreshSel();
    startMarquee(ev, holder);
  };

  // ⑤ 裁剪框（重绘会把 holder 换掉，所以这里重新挂一次）
  if (cropState) attachCropUi();

  $('#layerCount').textContent = String(S.page.elements.length);
  updatePageLabel();
  renderLayers();
  renderProps();
}

function orderedByZ() {
  if (!S.page) return [];
  return S.page.elements.slice().sort((a, b) => (a.z - b.z) || (a.id < b.id ? -1 : 1));
}

function makeOverlay(e, box) {
  const [x, y, w, h] = box;
  const n = el('div', 'ov' + (isSelected(e.id) ? ' sel' : '') + (e.locked ? ' locked' : ''));
  n.dataset.eid = e.id;
  n.style.cssText =
    `left:${x * 100}%;top:${y * 100}%;width:${w * 100}%;height:${h * 100}%`;
  const tag = el('div', 'tag');
  tag.textContent = label(e);
  n.appendChild(tag);

  if (!e.locked) {
    const spec = e.kind === 'bubble'
      ? ['e']                       // 气泡只横向缩放（高度自动）
      : e.kind === 'text' ? ['e']
        : e.kind === 'shape' ? ['se', 'e', 's'] : ['se'];
    spec.forEach(k => {
      const hd = el('div', 'handle ' + k);
      hd.onpointerdown = (ev) => startResize(ev, e, k);
      n.appendChild(hd);
    });
    n.onpointerdown = (ev) => {
      if (ev.target.classList.contains('handle')) return;
      startDrag(ev, e);
    };
  }

  // 气泡箭头：单独一个小圆点
  if (e.kind === 'bubble' && e.tail && !e.locked) {
    const dot = el('div', 'tail-dot');
    dot.style.cssText =
      `left:${e.tail[0] * 100}%;top:${e.tail[1] * 100}%`;
    dot.title = '拖动改变箭头指向';
    dot.onpointerdown = (ev) => startTailDrag(ev, e);
    n.appendChild(dot);
  }
  return n;
}

function label(e) {
  if (e.kind === 'image') return '图 ' + (e.name || e.asset_id.slice(0, 6));
  if (e.kind === 'bubble') return (e.speaker ? e.speaker + '：' : '') +
    (e.text || '').slice(0, 12);
  if (e.kind === 'text') return '字 ' + (e.text || '').slice(0, 12);
  return '色块';
}

function applyZoom(holder) {
  const host = $('#canvasHost');
  if (!holder) return;
  const img = holder.querySelector('img');
  if (!img || !img.naturalWidth) return;
  const avail = host.clientWidth - 40;
  const availH = host.clientHeight - 40;
  const fit = Math.min(avail / img.naturalWidth, availH / img.naturalHeight);
  const k = S.zoom === 1 ? fit : S.zoom;
  holder.style.width = Math.round(img.naturalWidth * k) + 'px';
  $('#zoomLabel').textContent = Math.round(k * 100) + '%';
}

function refreshSel() {
  $$('.ov').forEach(n => n.classList.toggle('sel', isSelected(n.dataset.eid)));
  refreshSelInfo();
}

/** 画布下方显示「选中了几个」 */
function refreshSelInfo() {
  const b = selectionBounds();
  if (!b) { status(''); return; }
  if (b.n > 1) {
    status(`已选中 ${b.n} 个元素　可以一起拖动；Del 批量删除；Esc 取消`);
  }
}

/* ── 拖动 ─────────────────────────────────────── */
function startDrag(ev, e) {
  // Shift 点选：加/减选，不进入拖动
  if (ev.shiftKey) {
    ev.preventDefault(); ev.stopPropagation();
    const next = new Set(S.sels);
    if (next.has(e.id)) next.delete(e.id); else next.add(e.id);
    setSelection(Array.from(next), next.has(e.id) ? e.id : null);
    refreshSel(); renderProps(); renderLayers();
    return;
  }
  if (e.locked) { toast('该元素已锁定'); return; }
  ev.preventDefault(); ev.stopPropagation();

  // 点未选中的元素 → 只选它；点已选中的 → 保持整组
  if (!isSelected(e.id)) setSelection([e.id], e.id);
  else S.sel = e.id;
  refreshSel(); renderProps(); renderLayers();

  const holder = $('.page-holder');
  const rect = holder.getBoundingClientRect();
  const group = selectedEls();
  const bounds0 = selectionBounds();
  // 其他元素的盒子（吸附参考）
  const others = S.page.elements
    .filter(x => !S.sels.has(x.id) && S.boxes[x.id])
    .map(x => S.boxes[x.id]);

  const start = {
    mx: ev.clientX, my: ev.clientY,
    pos: group.map(x => ({ el: x, x: x.x, y: x.y })),
    bounds: bounds0,
  };
  let moved = false;

  const onMove = (ev2) => {
    let dx = (ev2.clientX - start.mx) / rect.width;
    let dy = (ev2.clientY - start.my) / rect.height;
    if (Math.abs(dx) > 0.002 || Math.abs(dy) > 0.002) moved = true;

    // ★ 吸附：把整组的移动后包围盒拉到对齐线上
    if (start.bounds) {
      const moved_b = {
        x0: start.bounds.x0 + dx, y0: start.bounds.y0 + dy,
        x1: start.bounds.x1 + dx, y1: start.bounds.y1 + dy,
      };
      const snap = snapMove(moved_b, others);
      dx += snap.dx; dy += snap.dy;
      S.guides = snap.guides;
      drawGuides(holder);
    }

    start.pos.forEach(({ el: x, x: sx, y: sy }) => {
      x.x = round(clamp(sx + dx, -0.5, 1.5));
      x.y = round(clamp(sy + dy, -0.5, 1.5));
      const n2 = holder.querySelector(`.ov[data-eid="${x.id}"]`);
      if (n2) { n2.style.left = x.x * 100 + '%'; n2.style.top = x.y * 100 + '%'; }
      syncTail(x);
    });
    const b = selectionBounds();
    if (b) {
      status(b.n > 1
        ? `已选 ${b.n} 个　整体 x ${(b.x0 * 100).toFixed(1)}% y ${(b.y0 * 100).toFixed(1)}%`
        : `x ${(e.x * 100).toFixed(1)}%  y ${(e.y * 100).toFixed(1)}%`);
    }
  };
  const onUp = async () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    clearGuides();
    if (!moved) { status(''); return; }
    // 多个元素用 batch-move 一次提交（一条请求，一个 revision）
    const moves = {};
    start.pos.forEach(({ el: x }) => { moves[x.id] = { x: x.x, y: x.y }; });
    if (Object.keys(moves).length === 1) {
      await saveProps(e.id, { x: e.x, y: e.y });
    } else {
      await batchMove(moves);
    }
    status('');
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

/** 一次提交多个元素的位置 */
async function batchMove(moves) {
  try {
    const r = await api(url(`/api/edit/projects/${S.proj.name}/elements/batch-move`
      + (S.revision != null ? `?revision=${S.revision}` : '')), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ moves }),
    });
    if (r.revision != null) S.revision = r.revision;
  } catch (e) { toast('移动失败：' + e.message + '（F5 刷新可看到服务器上的最新内容）', true); }
}

/** 框选：在空白处按下拖动，画一个矩形选住相交的元素 */
function startMarquee(ev, holder) {
  const rect = holder.getBoundingClientRect();
  const box = el('div', 'marquee');
  holder.appendChild(box);
  const x0 = ev.clientX - rect.left, y0 = ev.clientY - rect.top;
  const base = new Set(S.sels);

  const onMove = (ev2) => {
    const x1 = ev2.clientX - rect.left, y1 = ev2.clientY - rect.top;
    const L = Math.min(x0, x1), T = Math.min(y0, y1);
    const W = Math.abs(x1 - x0), H = Math.abs(y1 - y0);
    box.style.cssText = `left:${L}px;top:${T}px;width:${W}px;height:${H}px`;
    // 归一化后与元素盒子求交
    const a = { x0: L / rect.width, y0: T / rect.height,
                x1: (L + W) / rect.width, y1: (T + H) / rect.height };
    const hits = [];
    S.page.elements.forEach(e => {
      const b = S.boxes[e.id];
      if (!b) return;
      if (b[0] < a.x1 && b[0] + b[2] > a.x0 &&
          b[1] < a.y1 && b[1] + b[3] > a.y0) hits.push(e.id);
    });
    const next = new Set(ev2.shiftKey ? base : []);
    hits.forEach(id => next.add(id));
    setSelection(Array.from(next), hits[0] || S.sel);
    refreshSel(); renderProps(); renderLayers();
  };
  const onUp = () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    box.remove();
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

function syncTail(e) {
  const node = $('.page-holder') &&
    document.querySelector(`.ov[data-eid="${e.id}"] .tail-dot`);
  if (node && e.tail) {
    node.style.left = e.tail[0] * 100 + '%';
    node.style.top = e.tail[1] * 100 + '%';
  }
}

function startResize(ev, e, k) {
  ev.preventDefault(); ev.stopPropagation();
  S.sel = e.id; refreshSel(); renderProps();

  const holder = $('.page-holder');
  const rect = holder.getBoundingClientRect();
  const node = holder.querySelector(`.ov[data-eid="${e.id}"]`);
  const start = {
    mx: ev.clientX, my: ev.clientY,
    w: e.w, h: e.h || 0, x: e.x, y: e.y,
  };
  const aspect = S.boxes[e.id] ? (S.boxes[e.id][2] / (S.boxes[e.id][3] || 1)) : 1;

  const onMove = (ev2) => {
    const dx = (ev2.clientX - start.mx) / rect.width;
    const dy = (ev2.clientY - start.my) / rect.height;

    if (e.kind === 'image') {
      // 等比缩放（拿高当驱动，因为图片宽高比固定）
      const nw = clamp(start.w + dx, 0.02, 1.6);
      e.w = round(nw);
      if (start.w > 0) {
        const k2 = nw / start.w;
        e.h = round(start.h * k2);
      }
      if (k.includes('n') || k.includes('s')) { /* 只调大小不调位置 */ }
    } else if (e.kind === 'bubble' || e.kind === 'text') {
      e.w = round(clamp(start.w + dx, 0.03, 1.4));
    } else {
      e.w = round(clamp(start.w + dx, 0.02, 1.6));
      e.h = round(clamp(start.h + dy, 0.01, 1.6));
    }
    node.style.width = e.w * 100 + '%';
    if (e.h) node.style.height = e.h * 100 + '%';
    status(`宽 ${(e.w * 100).toFixed(1)}%` + (e.h ? `  高 ${(e.h * 100).toFixed(1)}%` : ''));
  };
  const onUp = async () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    const props = { w: e.w };
    if (e.h) props.h = e.h;
    await saveProps(e.id, props);
    status('');
    await drawCanvas();          // 气泡高度变了，要重新取服务端几何
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

function startTailDrag(ev, e) {
  ev.preventDefault(); ev.stopPropagation();
  const holder = $('.page-holder');
  const rect = holder.getBoundingClientRect();
  const dot = ev.target;
  const onMove = (ev2) => {
    const x = round(clamp((ev2.clientX - rect.left) / rect.width, -0.2, 1.2));
    const y = round(clamp((ev2.clientY - rect.top) / rect.height, -0.2, 1.2));
    e.tail = [x, y];
    dot.style.left = x * 100 + '%';
    dot.style.top = y * 100 + '%';
  };
  const onUp = async () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    await saveProps(e.id, { tail: e.tail });
    await drawCanvas();
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

/* ══════════════════════════════════════════════════
 * 裁剪（图片元素）
 *
 * crop 的真实语义（读 packages/editor/render.py:132-137 确认）：
 *     crop = [l, t, r, b] —— **四边相对原图的内缩比例**，不是包围盒。
 *     渲染时先按它把原图四周裁掉一圈，再把裁完的结果 fit 进元素盒子
 *     （render.py:139-149）。
 *
 * ★ ①②两件事必须先知道，否则这个界面一定是错的：
 *   ① 盒子**不会**因为裁剪变小 —— 盒子的高是按**原图**宽高比算的
 *      （render.py:446），所以裁完之后保留的部分会被放大铺满原来的框。
 *   ② fit 是在裁剪**之后**做的，于是盒子的边缘未必是原图的边缘 ——
 *      cover 早就把两侧切掉了、contain 还留着黑边、fill 会把整张拉伸进盒子。
 *      所以不能把「框在盒子里的比例」直接当成 crop 数值：那是「所见非所切」，
 *      用户把框对到画面里的东西上，切掉的却是别处。
 *      viewMap() 按渲染器同一套数学反解，保证「框住哪块，写进去就是哪块」。
 * ══════════════════════════════════════════════════ */

/** 裁剪目标元素（会话期间它可能被别处删掉，所以每次都现查） */
function cropEl() {
  if (!cropState || !S.page) return null;
  return S.page.elements.find(x => x.id === cropState.eid) || null;
}

/**
 * 复刻 render.py 的 fit 数学，得到「盒子比例 ↔ 原图比例」的互换函数
 *
 * @param e    图片元素
 * @param crop 画布上**这一份渲染**所对应的裁剪（屏幕方向，flip_h 已折算）
 * @returns {ux, vy, bx, by}；素材或盒子缺一就返回 null
 */
function viewMap(e, crop) {
  const box = S.boxes[e.id];
  const a = (S.proj.assets || []).find(x => x.id === e.asset_id);
  if (!box || !a || !a.width || !a.height) return null;
  const [l, t, r, b] = crop || [0, 0, 0, 0];
  // 单位取页面像素即可 —— 这里只用得到比例
  const pw = S.page.width || 1400, ph = S.page.height || 2000;
  const bw = box[2] * pw, bh = box[3] * ph;
  const aw = a.width, ah = a.height;
  const kw = aw * (1 - l - r), kh = ah * (1 - t - b);
  if (kw <= 1e-6 || kh <= 1e-6) return null;
  // render.py:140-149：fill 两轴各自拉伸；cover / contain 共用一个等比系数
  let sx, sy;
  if (e.fit === 'fill') { sx = bw / kw; sy = bh / kh; }
  else {
    const s = e.fit === 'contain' ? Math.min(bw / kw, bh / kh)
      : Math.max(bw / kw, bh / kh);
    sx = sy = s;
  }
  // 缩放后居中摆放，超出盒子的部分被裁掉（render.py:148-149）
  const dx = (bw - kw * sx) / 2, dy = (bh - kh * sy) / 2;
  return {
    ux: (p) => l + (p * bw - dx) / (aw * sx),   // 盒子横向比例 → 原图横向比例
    vy: (q) => t + (q * bh - dy) / (ah * sy),
    bx: (u) => (dx + (u - l) * aw * sx) / bw,   // 原图横向比例 → 盒子横向比例
    by: (v) => (dy + (v - t) * ah * sy) / bh,
  };
}

/** crop 数据 ↔ 屏幕方向
 *
 *  flip_h 是**裁完之后**才翻转的（render.py:151），所以数据里的 l（原图左边）
 *  在屏幕上跑到了右边 —— 互换 l/r 就是两个方向的换算，做两次等于没做。 */
const flipCrop = (c) => [c[2], c[1], c[0], c[3]];

/** 进入裁剪模式（属性面板的「裁剪」按钮） */
function enterCrop(e) {
  if (!e || e.kind !== 'image') return;
  if (cropState) {
    if (cropState.eid === e.id) return;      // 已经在裁它了
    exitCrop();
  }
  if (!S.boxes[e.id]) { toast('这张图还没渲染出来，稍等一下再裁', true); return; }
  if (!$('.page-holder')) { toast('先选中画布上的一张图', true); return; }

  // ★ 基准：画布这一份渲染对应的那份裁剪。
  //   会话期间**不能变** —— 一变，换算就和屏幕上的像素对不上了。
  const data = (e.crop && e.crop.length === 4) ? e.crop.slice() : [0, 0, 0, 0];
  const ref = e.flip_h ? flipCrop(data) : data;
  const m = viewMap(e, ref);
  if (!m) { toast('取不到这张图的尺寸，没法裁剪', true); return; }

  // 初始框 = 画面里**真正看得见**的那一块，而不是「整个盒子」：
  //   cover 已经切掉两侧、contain 还留着黑边。
  //   这样「什么都不动直接完成」等于画面不变，不会平白多出一个裁剪。
  const vl = clamp(m.ux(0), ref[0], 1 - ref[2]);
  const vr = clamp(m.ux(1), ref[0], 1 - ref[2]);
  const vt = clamp(m.vy(0), ref[1], 1 - ref[3]);
  const vb = clamp(m.vy(1), ref[1], 1 - ref[3]);
  cropState = {
    eid: e.id, ref, holder: null, frame: null, keep: null, bar: null, ro: null,
    sl: clamp(m.bx(vl), 0, 1), st: clamp(m.by(vt), 0, 1),
    sr: clamp(1 - m.bx(vr), 0, 1), sb: clamp(1 - m.by(vb), 0, 1),
  };
  buildCropBar();
  attachCropUi();
  if (e.rotation) toast('这张图带旋转角度，裁剪框按旋转前的原图算，画面对不上是正常的');
  status('裁剪中：拖四条边圈出要保留的部分；Esc 取消');
}

/** 退出裁剪模式（不提交任何改动；可重复调用） */
function exitCrop() {
  if (!cropState) return;
  const holder = cropState.holder || $('.page-holder');
  if (holder) holder.classList.remove('cropping');
  // 全局扫一遍：holder 可能已经被 drawCanvas 换掉了，别留个孤儿框在画布上
  $$('.crop-frame').forEach(n => n.remove());
  const bar = $('.crop-bar');
  if (bar) bar.remove();
  cropState = null;
}

/** 把裁剪框挂到画布上（drawCanvas 会换掉 holder，所以要能重复调用） */
function attachCropUi() {
  const c = cropState; if (!c) return;
  const e = cropEl();
  const holder = $('.page-holder');
  const box = e ? S.boxes[e.id] : null;
  if (!e || !holder || !box) { exitCrop(); return; }
  const old = holder.querySelector('.crop-frame');
  if (old) old.remove();

  // 框本身 = 元素盒子；框内再摆四个遮罩（框外压暗）和四条可拖的边
  const f = el('div', 'crop-frame');
  f.style.cssText = `left:${box[0] * 100}%;top:${box[1] * 100}%;` +
    `width:${box[2] * 100}%;height:${box[3] * 100}%`;
  ['t', 'b', 'l', 'r'].forEach(k => f.appendChild(el('div', 'crop-mask ' + k)));
  const keep = el('div', 'crop-keep');
  const tip = { n: '上边', s: '下边', w: '左边', e: '右边' };
  ['n', 's', 'w', 'e'].forEach(k => {
    const hd = el('div', 'crop-edge ' + k);
    hd.title = '拖动' + tip[k] + '（至少保留 ' + Math.round(CROP_MIN * 100) + '%）';
    hd.onpointerdown = (ev) => startCropDrag(ev, k);
    keep.appendChild(hd);
  });
  f.appendChild(keep);
  holder.appendChild(f);
  // ★ 裁剪中其它元素的手柄不响应点击 —— 否则一拖就变成了挪元素
  holder.classList.add('cropping');
  c.holder = holder; c.frame = f; c.keep = keep;
  paintCrop();
}

/** 按 cropState 里的框位置重画遮罩/边/读数 */
function paintCrop() {
  const c = cropState; if (!c || !c.keep) return;
  const L = c.sl * 100, T = c.st * 100;
  const W = (1 - c.sl - c.sr) * 100, H = (1 - c.st - c.sb) * 100;
  const q = (s) => c.frame.querySelector(s);
  q('.crop-mask.t').style.cssText = `left:0;right:0;top:0;height:${T}%`;
  q('.crop-mask.b').style.cssText = `left:0;right:0;bottom:0;height:${c.sb * 100}%`;
  q('.crop-mask.l').style.cssText = `left:0;top:${T}%;bottom:${c.sb * 100}%;width:${L}%`;
  q('.crop-mask.r').style.cssText = `right:0;top:${T}%;bottom:${c.sb * 100}%;width:${c.sr * 100}%`;
  c.keep.style.cssText = `left:${L}%;top:${T}%;width:${W}%;height:${H}%`;
  if (c.ro) c.ro.textContent = cropReadout();
}

/** 框 → crop 数据（原图四边内缩比例，0–1）
 *
 *  基准用 cropState.ref，也就是**画布上那份渲染**对应的裁剪：
 *  会话开始后就固定，拖动时不会因为「框变了→比例变了→框又变了」而抖动。 */
function cropFromFrame() {
  const c = cropState; if (!c) return null;
  const e = cropEl(); if (!e) return null;
  const m = viewMap(e, c.ref); if (!m) return null;
  const l = clamp(m.ux(c.sl), 0, 1);
  const r = clamp(1 - m.ux(1 - c.sr), 0, 1);
  const t = clamp(m.vy(c.st), 0, 1);
  const b = clamp(1 - m.vy(1 - c.sb), 0, 1);
  if (l + r >= 0.999 || t + b >= 0.999) return null;      // 太窄，服务端会忽略
  const d = e.flip_h ? flipCrop([l, t, r, b]) : [l, t, r, b];
  return [round(d[0]), round(d[1]), round(d[2]), round(d[3])];
}

/** 框是否还贴着盒子四条边（= 一个像素都没裁） */
function cropFrameIsFull() {
  const c = cropState;
  if (!c) return false;
  return c.sl < 1e-4 && c.st < 1e-4 && c.sr < 1e-4 && c.sb < 1e-4;
}

/** 工具条上的读数：直接写将要存进去的 crop 数值（不是屏幕上框的比例） */
function cropReadout() {
  if (cropFrameIsFull()) {
    // ★ 框贴着四条边 = 一个像素都没裁。这里必须照实说：
    //   否则「完成」明明什么都没写，读数却报个 26%，用户会以为裁了
    return cropState.ref.every(x => Math.abs(x) < 1e-4)
      ? '不裁剪（整张图）' : '保持原样（框没动）';
  }
  const v = cropFromFrame();
  if (!v) return '（范围太小）';
  return '左 ' + Math.round(v[0] * 100) + '% · 上 ' + Math.round(v[1] * 100) +
    '% · 右 ' + Math.round(v[2] * 100) + '% · 下 ' + Math.round(v[3] * 100) + '%';
}

/** 改框（拖动中调用）：只动本地，不发请求 */
function setCropFrame(n) {
  const c = cropState; if (!c) return;
  c.sl = n.sl; c.st = n.st; c.sr = n.sr; c.sb = n.sb;
  paintCrop();
}

/** 拖一条边：对边固定，只让被拖的这条动，且至少留 CROP_MIN */
function startCropDrag(ev, which) {
  ev.preventDefault(); ev.stopPropagation();
  const c = cropState;
  if (!c || !c.frame) return;
  const rect = c.frame.getBoundingClientRect();
  const x0 = ev.clientX, y0 = ev.clientY;
  const s0 = { sl: c.sl, st: c.st, sr: c.sr, sb: c.sb };

  const onMove = (ev2) => {
    const dx = (ev2.clientX - x0) / Math.max(1, rect.width);
    const dy = (ev2.clientY - y0) / Math.max(1, rect.height);
    const n = { sl: s0.sl, st: s0.st, sr: s0.sr, sb: s0.sb };
    if (which === 'w') n.sl = clamp(s0.sl + dx, 0, 1 - s0.sr - CROP_MIN);
    if (which === 'e') n.sr = clamp(s0.sr - dx, 0, 1 - s0.sl - CROP_MIN);
    if (which === 'n') n.st = clamp(s0.st + dy, 0, 1 - s0.sb - CROP_MIN);
    if (which === 's') n.sb = clamp(s0.sb - dy, 0, 1 - s0.st - CROP_MIN);
    setCropFrame(n);
    status('裁剪中：左 ' + Math.round(n.sl * 100) + '% · 上 ' +
      Math.round(n.st * 100) + '% · 右 ' + Math.round(n.sr * 100) + '% · 下 ' +
      Math.round(n.sb * 100) + '%');
  };
  const onUp = () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    status('裁剪中：拖四条边圈出要保留的部分；Esc 取消');
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

/** 完成：把框写成 crop */
async function commitCrop() {
  const c = cropState; if (!c) return;
  const e = cropEl();
  if (!e) { exitCrop(); return; }

  // ★ 框贴着四条边 = 用户一个像素都没裁 → **一个请求都不发**。
  //   为什么不写个「等价」的值回去：盒子看不全整张图（cover）是 fit 的事，
  //   不代表用户想裁掉看不见的部分；而带着旧 crop 进来再原样写回去，
  //   只会白白抬一次 revision，跟别的标签页更容易撞乐观并发。
  if (cropFrameIsFull()) {
    const wasFull = c.ref.every(x => Math.abs(x) < 1e-4);
    exitCrop(); status('');
    toast(wasFull ? '没有裁掉任何东西' : '裁剪没有改动');
    return;
  }

  const v = cropFromFrame();
  if (!v) { toast('裁剪范围太小，应用不了', true); return; }
  const ok = await saveProps(e.id, { crop: v });
  if (!ok) return;                    // 存不上就留在裁剪里，别把用户调好的框丢了
  exitCrop();
  await drawCanvas();                 // 让服务端按新 crop 重画 —— 眼见为实
  status('');
  toast('已应用裁剪');
}

/** 重置 = 去掉裁剪（crop: null） */
async function resetCrop() {
  const c = cropState; if (!c) return;
  const e = cropEl();
  if (!e) { exitCrop(); return; }
  const had = !!e.crop;
  const ok = await saveProps(e.id, { crop: null });
  if (!ok) return;
  exitCrop();
  await drawCanvas();
  status('');
  toast(had ? '已重置裁剪，恢复整张图' : '这张图本来就没有裁剪');
}

/** 画布下方的裁剪工具条
 *
 *  挂到 .canvas-wrap 而不是画布里：drawCanvas 每次都会重建画布内容，
 *  挂在里面会被清掉，而且跟着滚动会看不见。 */
function buildCropBar() {
  const wrap = $('.canvas-wrap');
  if (!wrap || !cropState) return;
  const old = $('.crop-bar');
  if (old) old.remove();
  const bar = el('div', 'crop-bar');
  bar.appendChild(el('span', 'crop-ico', '✂'));
  bar.appendChild(el('span', 'crop-name', '裁剪图片'));
  const ro = el('span', 'crop-readout', '');
  ro.title = 'crop = [左, 上, 右, 下]，各边相对**原图**内缩的比例（render.py:132）';
  bar.appendChild(ro);
  bar.appendChild(el('span', 'crop-tip',
    '拖四条边圈出要保留的部分；完成后它会放大铺满原来的框'));
  const mk = (t, fn, cls) => { const b = el('button', cls || '', t); b.onclick = fn; return b; };
  const ops = el('div', 'crop-ops');
  ops.appendChild(mk('重置', resetCrop));
  ops.appendChild(mk('完成', commitCrop, 'primary'));
  ops.appendChild(mk('取消', () => { exitCrop(); status('已取消裁剪'); }));
  bar.appendChild(ops);
  wrap.insertBefore(bar, wrap.querySelector('.canvas-status') || null);
  cropState.bar = bar; cropState.ro = ro;
}

/** 保存元素属性（本地也同步，避免整页重载） */
async function saveProps(eid, props) {
  try {
    const q = S.revision != null ? `?revision=${S.revision}` : '';
    const r = await jpatch(
      `/api/edit/projects/${S.proj.name}/elements/${eid}${q}`, { props });
    if (r.revision != null) S.revision = r.revision;
    const i = S.page.elements.findIndex(x => x.id === eid);
    if (i >= 0) {
      const merged = Object.assign({}, S.page.elements[i], r.element);
      S.page.elements[i] = merged;
    }
    syncProjectCache(eid, r.element);
    return true;
  } catch (e) {
    if (String(e.message).includes('已被其他人修改')) {
      toast('这个项目在别处也被改了。刷新页面（F5）载入最新版本后再继续。', true);
    } else {
      toast('保存失败：' + e.message, true);
    }
    return false;
  }
}

function syncProjectCache(eid, ne) {
  for (const p of S.proj.pages) {
    const i = p.elements.findIndex(x => x.id === eid);
    if (i >= 0) { p.elements[i] = Object.assign({}, p.elements[i], ne); return; }
  }
}

/* ══════════════════════════════════════════════════
 * 添加元素
 * ══════════════════════════════════════════════════ */
async function addElement(kind, extra) {
  if (!S.page) { toast('先新建一页'); return; }
  S.busy = true; status('添加中…');
  try {
    const r = await jpost(
      `/api/edit/projects/${S.proj.name}/pages/${S.page.id}/elements`,
      Object.assign({ kind, auto_place: kind === 'bubble' }, extra || {}));
    S.page.elements.push(r.element);
    S.sel = r.element.id;
    await drawCanvas();
    toast(kind === 'bubble' ? '已加一个气泡（已自动避开人物）' : '已添加');
  } catch (e) { toast('添加失败：' + e.message, true); }
  finally { S.busy = false; status(''); }
}

async function uploadFiles(files) {
  if (!files || !files.length) return;
  const fd = new FormData();
  Array.from(files).forEach(f => fd.append('files', f));
  S.busy = true; status(`上传 ${files.length} 个文件…`);
  try {
    const r = await fetch(url(`/api/edit/projects/${S.proj.name}/assets`),
      { method: 'POST', body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const d = await r.json();
    S.proj.assets.push(...d.added);
    renderAssets();
    toast(`已上传 ${d.added.length} 张` +
      (d.failed.length ? `，${d.failed.length} 张失败` : ''));
  } catch (e) { toast('上传失败：' + e.message, true); }
  finally { S.busy = false; status(''); }
}

/* ══════════════════════════════════════════════════
 * 左栏
 * ══════════════════════════════════════════════════ */
function renderAssets() {
  const host = $('#assetList');
  const list = S.proj.assets || [];
  $('#assetCount').textContent = String(list.length);
  host.innerHTML = '';
  if (!list.length) {
    host.innerHTML = '<div class="hint pad">还没有素材</div>';
    return;
  }
  list.forEach(a => {
    const c = el('div', 'asset-card');
    c.draggable = true;
    c.title = `${a.filename}\n${a.width}×${a.height}\n拖到画布上添加`;
    const im = el('img');
    im.src = url(`/api/edit/projects/${S.proj.name}/assets/${a.id}/raw?w=240`);
    im.loading = 'lazy';
    c.appendChild(im);
    c.appendChild(el('div', 'nm', a.filename));
    const del = el('button', 'del', '×');
    del.title = '删除素材';
    del.onclick = (ev) => { ev.stopPropagation(); deleteAsset(a); };
    c.appendChild(del);

    c.ondragstart = (ev) => {
      ev.dataTransfer.setData('text/asset-id', a.id);
      ev.dataTransfer.effectAllowed = 'copy';
    };
    c.onclick = () => addElement('image', { asset_id: a.id });
    host.appendChild(c);
  });
}

async function deleteAsset(a) {
  const used = S.proj.pages.some(p => p.elements.some(
    e => e.kind === 'image' && e.asset_id === a.id));
  if (used) { toast('该素材正在被使用，先删掉用它的元素', true); return; }
  if (!confirm(`删除素材「${a.filename}」？`)) return;
  try {
    await api(url(`/api/edit/projects/${S.proj.name}/assets/${a.id}`),
      { method: 'DELETE' });
    S.proj.assets = S.proj.assets.filter(x => x.id !== a.id);
    renderAssets();
    toast('已删除');
  } catch (e) { toast('删除失败：' + e.message, true); }
}

function renderPages() {
  const host = $('#pageList');
  const pages = orderedPages();
  $('#pageCount').textContent = String(pages.length);
  host.innerHTML = '';
  pages.forEach((p, i) => {
    const it = el('div', 'page-item' + (S.page && p.id === S.page.id ? ' on' : ''));
    it.appendChild(el('span', 'num', '#' + p.number));
    it.appendChild(el('span', 'ttl', p.title || ''));
    const ops = el('div', 'ops');
    const mk = (txt, title, fn) => {
      const b = el('button', '', txt); b.title = title;
      b.onclick = (ev) => { ev.stopPropagation(); fn(); };
      return b;
    };
    ops.appendChild(mk('↑', '上移', () => movePage(p.id, -1)));
    ops.appendChild(mk('↓', '下移', () => movePage(p.id, 1)));
    ops.appendChild(mk('⧉', '复制此页', () => duplicatePage(p.id)));
    ops.appendChild(mk('×', '删除此页', () => deletePage(p.id)));
    it.appendChild(ops);
    it.onclick = () => showPage(p.id);
    host.appendChild(it);
  });
  if (!pages.length) host.innerHTML = '<div class="hint pad">还没有页面</div>';
}

function updatePageLabel() {
  const pages = orderedPages();
  const i = pageIndex();
  $('#pageLabel').textContent = S.page
    ? `#${S.page.number} · ${i + 1}/${pages.length}`
    : '—';
}

/* ══════════════════════════════════════════════════
 * 右栏：属性
 * ══════════════════════════════════════════════════ */
/* ── 多选与对齐（Office 式）────────────────────── */

/** 把 S.sel 与 S.sels 同步：sel 必须属于 sels */
function setSelection(ids, primary) {
  // 裁剪中换了目标就没法画框了 —— 先把裁剪收掉（不提交）。
  // 不放在各个调用点是因为改选的入口太多（图层面板、框选、翻页…）。
  if (cropState && !(ids || []).includes(cropState.eid)) exitCrop();
  S.sels = new Set(ids || []);
  S.sel = primary || (S.sels.size ? Array.from(S.sels)[0] : null);
  if (S.sel && !S.sels.has(S.sel)) S.sels.add(S.sel);
}

function isSelected(id) { return S.sels.has(id); }

/** 当前选中的所有元素对象 */
function selectedEls() {
  if (!S.page) return [];
  return S.page.elements.filter(e => S.sels.has(e.id));
}

/** 选中元素的整体包围盒（归一化） */
function selectionBounds() {
  const els = selectedEls().filter(e => S.boxes[e.id]);
  if (!els.length) return null;
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  els.forEach(e => {
    const b = S.boxes[e.id];
    x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]);
    x1 = Math.max(x1, b[0] + b[2]); y1 = Math.max(y1, b[1] + b[3]);
  });
  return { x0, y0, x1, y1, w: x1 - x0, h: y1 - y0, n: els.length };
}

/**
 * 吸附：把一组「移动中的位置」拉到对齐线上
 * @returns {{dx:number, dy:number, guides:Array}}
 */
function snapMove(bounds, others) {
  const cands = [];
  // 被拖元素的左/中/右、上/中/下
  const xs = [
    { edge: 'left', v: bounds.x0 },
    { edge: 'center', v: (bounds.x0 + bounds.x1) / 2 },
    { edge: 'right', v: bounds.x1 },
  ];
  const ys = [
    { edge: 'top', v: bounds.y0 },
    { edge: 'center', v: (bounds.y0 + bounds.y1) / 2 },
    { edge: 'bottom', v: bounds.y1 },
  ];
  // 目标线：画布 + 其他元素
  const tx = SNAP_X.slice();
  const ty = SNAP_Y.slice();
  (others || []).forEach(b => {
    tx.push(b[0], b[0] + b[2] / 2, b[0] + b[2]);
    ty.push(b[1], b[1] + b[3] / 2, b[1] + b[3]);
  });

  let dx = 0, bestX = SNAP;
  xs.forEach(c => tx.forEach(t => {
    const d = t - c.v;
    if (Math.abs(d) < bestX) { bestX = Math.abs(d); dx = d; }
  }));
  let dy = 0, bestY = SNAP;
  ys.forEach(c => ty.forEach(t => {
    const d = t - c.v;
    if (Math.abs(d) < bestY) { bestY = Math.abs(d); dy = d; }
  }));

  // 记录要画的参考线（只画真正吸上的那几条）
  const guides = [];
  if (dx !== 0 || bestX < SNAP) {
    const v = (bounds.x0 + dx);
    xs.forEach(c => {
      const t = c.v + dx;
      if (tx.some(q => Math.abs(q - t) < 1e-6)) guides.push({ axis: 'x', pos: t });
    });
  }
  if (dy !== 0 || bestY < SNAP) {
    ys.forEach(c => {
      const t = c.v + dy;
      if (ty.some(q => Math.abs(q - t) < 1e-6)) guides.push({ axis: 'y', pos: t });
    });
  }
  return { dx, dy, guides };
}

/** 把参考线画到画布上（只是视觉提示，不参与渲染） */
function drawGuides(holder) {
  holder.querySelectorAll('.guide').forEach(n => n.remove());
  S.guides.forEach(g => {
    const n = el('div', 'guide ' + g.axis);
    if (g.axis === 'x') n.style.cssText = `left:${g.pos * 100}%`;
    else n.style.cssText = `top:${g.pos * 100}%`;
    holder.appendChild(n);
  });
}

function clearGuides() {
  S.guides = [];
  const h = $('.page-holder');
  if (h) h.querySelectorAll('.guide').forEach(n => n.remove());
}

function curEl() {
  if (!S.page || !S.sel) return null;
  return S.page.elements.find(e => e.id === S.sel) || null;
}

function renderProps() {
  const e = curEl();
  const host = $('#props');
  const none = $('#noSel');
  host.innerHTML = '';
  if (!e) { none.hidden = false; return; }
  none.hidden = true;

  // ── 通用：位置与大小
  const g1 = grp('位置与大小');
  g1.appendChild(numRow('X', e.x * 100, v => { e.x = round(v / 100); saveAnd(e, { x: e.x }); }, 0, 200, 0.1));
  g1.appendChild(numRow('Y', e.y * 100, v => { e.y = round(v / 100); saveAnd(e, { y: e.y }); }, 0, 200, 0.1));
  g1.appendChild(numRow('宽', e.w * 100, v => { e.w = round(v / 100); saveAnd(e, { w: e.w }, true); }, 1, 160, 0.1));
  if (e.h) {
    g1.appendChild(numRow('高', e.h * 100, v => { e.h = round(v / 100); saveAnd(e, { h: e.h }); }, 1, 160, 0.1));
  }
  g1.appendChild(rangeRow('不透明', e.opacity * 100, v => {
    e.opacity = round(v / 100); saveAnd(e, { opacity: e.opacity });
  }, 10, 100));
  host.appendChild(g1);

  // ── 按类型
  if (e.kind === 'bubble') host.appendChild(bubbleProps(e));
  else if (e.kind === 'image') host.appendChild(imageProps(e));
  else if (e.kind === 'text') host.appendChild(textProps(e));
  else host.appendChild(shapeProps(e));

  // ── 操作
  const g4 = grp('操作');
  const btns = el('div', 'btns');
  const mk = (t, fn, cls) => {
    const b = el('button', cls || '', t); b.onclick = fn; return b;
  };
  btns.appendChild(mk(e.locked ? '🔓 解锁' : '🔒 锁定', async () => {
    await saveProps(e.id, { locked: !e.locked });
    await drawCanvas();
  }));
  btns.appendChild(mk('⧉ 复制', () => duplicateElement(e.id)));
  btns.appendChild(mk('置顶', () => moveZ(e.id, 999)));
  btns.appendChild(mk('置底', () => moveZ(e.id, -999)));
  btns.appendChild(mk('↑ 上一层', () => moveZ(e.id, 1)));
  btns.appendChild(mk('↓ 下一层', () => moveZ(e.id, -1)));
  btns.appendChild(mk('🗑 删除', () => deleteElement(e.id), 'danger'));
  g4.appendChild(btns);
  host.appendChild(g4);
}

function grp(title) {
  const g = el('div', 'grp');
  g.appendChild(el('h4', '', title));
  return g;
}

function row(label) {
  const r = el('div', 'row');
  r.appendChild(el('label', '', label));
  return r;
}

function numRow(label, val, onSet, min, max, step) {
  const r = row(label);
  const i = el('input');
  i.type = 'number';
  i.value = (Math.round(val * 10) / 10);
  i.min = min; i.max = max; i.step = step || 1;
  i.onchange = () => onSet(parseFloat(i.value) || 0);
  r.appendChild(i);
  return r;
}

function rangeRow(label, val, onSet, min, max) {
  const r = row(label);
  const i = el('input');
  i.type = 'range'; i.min = min; i.max = max; i.value = val;
  const v = el('span', 'val', Math.round(val) + '%');
  i.oninput = () => { v.textContent = Math.round(i.value) + '%'; };
  i.onchange = () => onSet(parseFloat(i.value));
  r.appendChild(i); r.appendChild(v);
  return r;
}

function textRow(label, val, onSet) {
  const r = row(label);
  const i = el('input');
  i.type = 'text'; i.value = val || '';
  i.onchange = () => onSet(i.value);
  r.appendChild(i);
  return r;
}

function areaRow(label, val, onSet) {
  const r = row(label);
  const t = el('textarea');
  t.value = val || '';
  t.onchange = () => onSet(t.value);
  r.appendChild(t);
  return r;
}

function colorRow(label, val, onSet) {
  const r = row(label);
  const i = el('input');
  i.type = 'color'; i.value = val || '#ffffff';
  const t = el('input');
  t.type = 'text'; t.value = val || '#ffffff';
  t.onchange = () => { i.value = t.value; onSet(t.value); };
  i.oninput = () => { t.value = i.value; onSet(i.value); };
  r.appendChild(i); r.appendChild(t);
  return r;
}

function selRow(label, val, opts, onSet) {
  const r = row(label);
  const s = el('select');
  opts.forEach(([v, t]) => {
    const o = el('option', '', t); o.value = v;
    if (v === val) o.selected = true;
    s.appendChild(o);
  });
  s.onchange = () => onSet(s.value);
  r.appendChild(s);
  return r;
}

function chkRow(label, val, onSet) {
  const r = el('div', 'row');
  const l = el('label', 'chk');
  const i = el('input'); i.type = 'checkbox'; i.checked = !!val;
  i.onchange = () => onSet(i.checked);
  l.appendChild(i); l.appendChild(document.createTextNode(label));
  r.appendChild(l);
  return r;
}

async function saveAnd(e, props, redraw) {
  await saveProps(e.id, props);
  renderLayers();
  if (redraw) await drawCanvas();
  else await drawCanvas();
}

function bubbleProps(e) {
  const g = grp('气泡');
  g.appendChild(areaRow('台词', e.text, v => { e.text = v; saveAnd(e, { text: v }); }));
  g.appendChild(textRow('说话人', e.speaker, v => { e.speaker = v; saveAnd(e, { speaker: v }); }));
  g.appendChild(selRow('样式', e.style, [
    ['speech', '对话（圆角）'], ['shout', '喊叫（爆炸）'],
    ['think', '心声（云朵）'], ['narration', '旁白（方框）'],
    ['whisper', '低语（虚线）'],
  ], v => { e.style = v; saveAnd(e, { style: v }); }));
  g.appendChild(numRow('字号', e.font_size, v => {
    e.font_size = Math.round(v); saveAnd(e, { font_size: e.font_size });
  }, 12, 200, 1));
  g.appendChild(selRow('对齐', e.align, [
    ['left', '左'], ['center', '中'], ['right', '右'],
  ], v => { e.align = v; saveAnd(e, { align: v }); }));
  g.appendChild(colorRow('填充', e.fill, v => { e.fill = v; saveAnd(e, { fill: v }); }));
  g.appendChild(colorRow('描边', e.line, v => { e.line = v; saveAnd(e, { line: v }); }));
  g.appendChild(colorRow('文字色', e.text_color, v => { e.text_color = v; saveAnd(e, { text_color: v }); }));
  g.appendChild(rangeRow('圆角', e.radius * 1000, v => {
    e.radius = round(v / 1000); saveAnd(e, { radius: e.radius });
  }, 0, 200));
  g.appendChild(rangeRow('内边距', e.pad * 1000, v => {
    e.pad = round(v / 1000); saveAnd(e, { pad: e.pad });
  }, 0, 100));

  const rr = el('div', 'row');
  const b1 = el('button', '', e.tail ? '去掉箭头' : '加箭头（指向人物）');
  b1.onclick = async () => {
    const t = e.tail
      ? null
      : [round(clamp(e.x + e.w / 2, 0, 1)), round(clamp(e.y + 0.25, 0, 1))];
    e.tail = t;
    await saveProps(e.id, { tail: t });
    await drawCanvas();
  };
  rr.appendChild(b1);
  g.appendChild(rr);
  if (e.tail) {
    g.appendChild(numRow('箭头X', e.tail[0] * 100, v => {
      e.tail = [round(v / 100), e.tail[1]]; saveAnd(e, { tail: e.tail });
    }, -20, 120, 0.1));
    g.appendChild(numRow('箭头Y', e.tail[1] * 100, v => {
      e.tail = [e.tail[0], round(v / 100)]; saveAnd(e, { tail: e.tail });
    }, -20, 120, 0.1));
  }
  return g;
}

function imageProps(e) {
  const g = grp('图片');
  const a = (S.proj.assets || []).find(x => x.id === e.asset_id);
  if (a) {
    g.appendChild(el('div', 'hint', `${a.filename} · ${a.width}×${a.height}`));
  }
  // ── 裁剪入口
  //    crop 是「相对原图四边内缩的比例」，不是包围盒（render.py:132）——
  //    面板里把数值原样写出来，用户不用进画布就知道现在裁掉多少
  const cbtn = el('div', 'btns');
  const cb = el('button', '', e.crop ? '✂ 重新裁剪' : '✂ 裁剪');
  cb.title = '在画布上拖四条边，圈出要保留的部分（Esc 取消）';
  cb.onclick = () => enterCrop(e);
  cbtn.appendChild(cb);
  g.appendChild(cbtn);
  if (e.crop && e.crop.length === 4) {
    g.appendChild(el('div', 'hint pad', '已裁剪：左 ' +
      Math.round(e.crop[0] * 100) + '% · 上 ' + Math.round(e.crop[1] * 100) +
      '% · 右 ' + Math.round(e.crop[2] * 100) + '% · 下 ' +
      Math.round(e.crop[3] * 100) + '%'));
  }
  g.appendChild(selRow('填充方式', e.fit, [
    ['cover', '铺满（裁掉溢出）'], ['contain', '完整显示'],
    ['fill', '拉伸铺满'],
  ], v => { e.fit = v; saveAnd(e, { fit: v }); }));
  g.appendChild(numRow('旋转', e.rotation, v => {
    e.rotation = v; saveAnd(e, { rotation: v });
  }, -180, 180, 1));
  g.appendChild(chkRow('水平翻转', e.flip_h, v => { e.flip_h = v; saveAnd(e, { flip_h: v }); }));
  g.appendChild(chkRow('投影', e.shadow, v => { e.shadow = v; saveAnd(e, { shadow: v }); }));
  g.appendChild(rangeRow('描边', e.border * 1000, v => {
    e.border = round(v / 1000); saveAnd(e, { border: e.border });
  }, 0, 20));
  return g;
}

function textProps(e) {
  const g = grp('文字');
  g.appendChild(areaRow('内容', e.text, v => { e.text = v; saveAnd(e, { text: v }); }));
  g.appendChild(numRow('字号', e.font_size, v => {
    e.font_size = Math.round(v); saveAnd(e, { font_size: e.font_size });
  }, 8, 400, 1));
  g.appendChild(selRow('对齐', e.align, [
    ['left', '左'], ['center', '中'], ['right', '右'],
  ], v => { e.align = v; saveAnd(e, { align: v }); }));
  g.appendChild(colorRow('颜色', e.color, v => { e.color = v; saveAnd(e, { color: v }); }));
  g.appendChild(rangeRow('描边', e.outline * 1000, v => {
    e.outline = round(v / 1000); saveAnd(e, { outline: e.outline });
  }, 0, 20));
  g.appendChild(colorRow('描边色', e.outline_color, v => {
    e.outline_color = v; saveAnd(e, { outline_color: v });
  }));
  g.appendChild(numRow('旋转', e.rotation, v => {
    e.rotation = v; saveAnd(e, { rotation: v });
  }, -180, 180, 1));
  g.appendChild(numRow('行距', e.line_spacing, v => {
    e.line_spacing = v; saveAnd(e, { line_spacing: v });
  }, 0.8, 3, 0.05));
  return g;
}

function shapeProps(e) {
  const g = grp('色块');
  g.appendChild(selRow('形状', e.shape, [['rect', '矩形'], ['ellipse', '椭圆']],
    v => { e.shape = v; saveAnd(e, { shape: v }); }));
  g.appendChild(colorRow('填充', e.fill, v => { e.fill = v; saveAnd(e, { fill: v }); }));
  g.appendChild(colorRow('描边', e.line, v => { e.line = v; saveAnd(e, { line: v }); }));
  g.appendChild(rangeRow('描边粗细', e.line_width * 1000, v => {
    e.line_width = round(v / 1000); saveAnd(e, { line_width: e.line_width });
  }, 0, 30));
  g.appendChild(rangeRow('圆角', e.radius * 1000, v => {
    e.radius = round(v / 1000); saveAnd(e, { radius: e.radius });
  }, 0, 300));
  return g;
}

/* ══════════════════════════════════════════════════
 * 右栏：元素顺序
 * ══════════════════════════════════════════════════ */
function renderLayers() {
  const host = $('#layerList');
  host.innerHTML = '';
  if (!S.page) return;
  const zs = orderedByZ().reverse();       // 上面 = 上层
  $('#layerCount').textContent = String(S.page.elements.length);
  zs.forEach(e => {
    const it = el('div', 'layer-item' + (S.sel === e.id ? ' on' : ''));
    const icon = { image: '▣', bubble: '💬', text: 'T', shape: '▭' }[e.kind] || '?';
    it.appendChild(el('span', 'ki', icon));
    it.appendChild(el('span', 'nm', label(e) + (e.locked ? ' 🔒' : '')));
    const ops = el('div', 'ops');
    const mk = (t, title, fn) => {
      const b = el('button', '', t); b.title = title;
      b.onclick = (ev) => { ev.stopPropagation(); fn(); };
      return b;
    };
    ops.appendChild(mk('↑', '上一层', () => moveZ(e.id, 1)));
    ops.appendChild(mk('↓', '下一层', () => moveZ(e.id, -1)));
    ops.appendChild(mk('⧉', '复制', () => duplicateElement(e.id)));
    ops.appendChild(mk('×', '删除', () => deleteElement(e.id)));
    it.appendChild(ops);
    it.onclick = (ev) => {
      if (ev.shiftKey) {
        const next = new Set(S.sels);
        if (next.has(e.id)) next.delete(e.id); else next.add(e.id);
        setSelection(Array.from(next), e.id);
      } else {
        setSelection([e.id], e.id);
      }
      refreshSel(); renderProps(); renderLayers();
    };
    host.appendChild(it);
  });
  if (!S.page.elements.length) {
    host.innerHTML = '<div class="hint pad">这一页还没有元素</div>';
  }
}

/* ══════════════════════════════════════════════════
 * 元素操作
 * ══════════════════════════════════════════════════ */
async function moveZ(eid, delta) {
  const e = S.page.elements.find(x => x.id === eid);
  if (!e) return;
  const zs = orderedByZ();
  let i = zs.findIndex(x => x.id === eid);
  const j = delta >= 999 ? zs.length - 1
    : delta <= -999 ? 0 : clamp(i + delta, 0, zs.length - 1);
  if (i === j) return;
  zs.splice(j, 0, zs.splice(i, 1)[0]);
  const ids = zs.map(x => x.id);
  try {
    await jpost(`/api/edit/projects/${S.proj.name}/elements/order`,
      { element_ids: ids });
    zs.forEach((x, k) => { x.z = k; });
    await drawCanvas();
  } catch (err) { toast('调整顺序失败：' + err.message, true); }
}

async function deleteElement(eid) {
  const e = S.page.elements.find(x => x.id === eid);
  if (e && e.locked) { toast('已锁定，先解锁'); return; }
  try {
    await api(url(`/api/edit/projects/${S.proj.name}/elements/${eid}`
      + (S.revision != null ? `?revision=${S.revision}` : '')),
      { method: 'DELETE' });
    S.page.elements = S.page.elements.filter(x => x.id !== eid);
    const p = S.proj.pages.find(x => x.id === S.page.id);
    if (p) p.elements = S.page.elements;
    S.sels.delete(eid);
    if (S.sel === eid) S.sel = null;
    await drawCanvas();
    toast('已删除');
  } catch (err) { toast('删除失败：' + err.message, true); }
}

/** 批量删除选中的元素 */
async function deleteSelected() {
  const els = selectedEls();
  if (!els.length) return;
  const unlockable = els.filter(e => e.locked);
  if (unlockable.length) { toast(`有 ${unlockable.length} 个已锁定，先解锁`); return; }
  if (els.length === 1) return deleteElement(els[0].id);
  if (!confirm(`删除选中的 ${els.length} 个元素？`)) return;
  S.busy = true; status('删除中…');
  try {
    for (const e of els) {
      await api(url(`/api/edit/projects/${S.proj.name}/elements/${e.id}`),
        { method: 'DELETE' });
    }
    S.sels.clear(); S.sel = null;
    await openProject(S.proj.name);      // 批量操作后重新拉一次，保证一致
    toast(`已删除 ${els.length} 个`);
  } catch (e) { toast('删除失败：' + e.message, true); }
  finally { S.busy = false; status(''); }
}

async function duplicateElement(eid) {
  try {
    const r = await jpost(
      `/api/edit/projects/${S.proj.name}/elements/${eid}/duplicate`);
    S.page.elements.push(r.element);
    S.sel = r.element.id;
    await drawCanvas();
    toast('已复制');
  } catch (e) { toast('复制失败：' + e.message, true); }
}

/* ══════════════════════════════════════════════════
 * 页面操作
 * ══════════════════════════════════════════════════ */
async function addPage() {
  try {
    const r = await jpost(`/api/edit/projects/${S.proj.name}/pages`, {});
    S.proj.pages.push(r.page);
    await showPage(r.page.id);
    await loadProjects(S.proj.name);
    toast(`已新建 第 ${r.page.number} 页`);
  } catch (e) { toast('新建失败：' + e.message, true); }
}

async function movePage(pid, delta) {
  try {
    await jpost(`/api/edit/projects/${S.proj.name}/pages/${pid}/move?delta=${delta}`);
    const pages = S.proj.pages;
    const ordered = orderedPages();
    const i = ordered.findIndex(p => p.id === pid);
    const j = clamp(i + delta, 0, ordered.length - 1);
    if (i === j) return;
    ordered.splice(j, 0, ordered.splice(i, 1)[0]);
    ordered.forEach((p, k) => { p.order = k; });
    renderPages();
    toast('顺序已调整（页码不变）');
  } catch (e) { toast('调整失败：' + e.message, true); }
}

async function duplicatePage(pid) {
  try {
    const r = await jpost(
      `/api/edit/projects/${S.proj.name}/pages/${pid}/duplicate`);
    S.proj.pages.push(r.page);
    renderPages();
    toast('已复制此页');
  } catch (e) { toast('复制失败：' + e.message, true); }
}

async function deletePage(pid) {
  const p = S.proj.pages.find(x => x.id === pid);
  if (!confirm(`删除 第 ${p.number} 页？（页码不会回收）`)) return;
  try {
    await api(url(`/api/edit/projects/${S.proj.name}/pages/${pid}`),
      { method: 'DELETE' });
    S.proj.pages = S.proj.pages.filter(x => x.id !== pid);
    if (S.page && S.page.id === pid) {
      const left = orderedPages();
      S.page = left[0] || null;
      S.sel = null;
    }
    renderPages(); renderLayers(); renderProps();
    await drawCanvas();
    toast('已删除（页码 ${p.number} 不再使用）');
  } catch (e) { toast('删除失败：' + e.message, true); }
}

/** 每张素材各建一页（批量导入最常用） */
async function pagesFromAssets() {
  const imgs = (S.proj.assets || []);
  if (!imgs.length) { toast('先上传素材'); return; }
  if (!confirm(`为 ${imgs.length} 张素材各建一页（共 ${imgs.length} 页）？`)) return;
  S.busy = true;
  try {
    for (let i = 0; i < imgs.length; i++) {
      status(`第 ${i + 1}/${imgs.length} 页…`);
      const a = imgs[i];
      const r = await jpost(`/api/edit/projects/${S.proj.name}/pages`,
        { title: a.filename.slice(0, 24) });
      S.proj.pages.push(r.page);
      await jpost(
        `/api/edit/projects/${S.proj.name}/pages/${r.page.id}/elements`,
        { kind: 'image', asset_id: a.id, x: 0, y: 0, w: 1 });
    }
    renderPages();
    await showPage(orderedPages()[0].id);
    await loadProjects(S.proj.name);
    toast(`已生成 ${imgs.length} 页`);
  } catch (e) { toast('批量建页失败：' + e.message, true); }
  finally { S.busy = false; status(''); }
}

/* ══════════════════════════════════════════════════
 * 撤销 / 导出
 * ══════════════════════════════════════════════════ */
async function undo() {
  try {
    const r = await jpost(`/api/edit/projects/${S.proj.name}/undo`);
    if (!r.ok) { toast(r.reason || '没有可撤销的操作'); return; }
    S.proj = r.project;
    const pages = orderedPages();
    S.page = pages.find(p => p.id === (S.page && S.page.id)) || pages[0] || null;
    S.sel = null;
    renderAssets(); renderPages();
    await drawCanvas();
    toast(`已撤销（还可撤销 ${r.remaining} 步）`);
  } catch (e) { toast('撤销失败：' + e.message, true); }
}

async function exportAs(fmt) {
  if (!S.proj.pages.length) { toast('项目里还没有页面'); return; }
  S.busy = true; status('导出中…（页数多时需要一会儿）');
  try {
    const r = await jpost(
      `/api/edit/projects/${S.proj.name}/export?fmt=${fmt}&width=1240`, {});
    const f = r.file || (r.files && r.files[0]);
    toast('导出完成，开始下载');
    window.location = url(f);
  } catch (e) { toast('导出失败：' + e.message, true); }
  finally { S.busy = false; status(''); }
}

/* ══════════════════════════════════════════════════
 * 新建项目
 * ══════════════════════════════════════════════════ */
function newProjectDialog() {
  const m = el('div', 'modal');
  const inner = el('div', 'inner');
  inner.appendChild(el('h3', '', '新建项目'));
  const mk = (label, input) => {
    const r = el('div', 'row');
    r.appendChild(el('label', '', label));
    r.appendChild(input);
    inner.appendChild(r);
    return input;
  };
  const nm = el('input'); nm.type = 'text';
  nm.value = '漫画' + new Date().toISOString().slice(5, 10).replace('-', '');
  const ti = el('input'); ti.type = 'text'; ti.placeholder = '可留空';
  const w = el('input'); w.type = 'number'; w.value = 1400;
  const h = el('input'); h.type = 'number'; h.value = 2000;
  mk('项目名', nm); mk('标题', ti);
  mk('画布宽', w); mk('画布高', h);
  inner.appendChild(el('div', 'hint',
    '画布尺寸只是比例基准；导出时可以再指定输出宽度。'));
  const btns = el('div', 'btns');
  btns.style.marginTop = '14px';
  const ok = el('button', 'primary', '创建');
  const cancel = el('button', '', '取消');
  cancel.onclick = () => m.remove();
  ok.onclick = async () => {
    try {
      const r = await jpost('/api/edit/projects', {
        name: nm.value.trim(), title: ti.value.trim(),
        width: parseInt(w.value, 10) || 1400,
        height: parseInt(h.value, 10) || 2000,
      });
      m.remove();
      await loadProjects(r.name);
      await openProject(r.name);
      toast('项目已创建，上传图片开始吧');
    } catch (e) { toast('创建失败：' + e.message, true); }
  };
  btns.appendChild(cancel); btns.appendChild(ok);
  inner.appendChild(btns);
  m.appendChild(inner);
  m.onclick = (ev) => { if (ev.target === m) m.remove(); };
  document.body.appendChild(m);
  nm.focus(); nm.select();
}

/* ══════════════════════════════════════════════════
 * 事件绑定
 * ══════════════════════════════════════════════════ */
function bind() {
  $('#projectSel').onchange = (e) => openProject(e.target.value);
  $('#btnNew').onclick = newProjectDialog;
  $('#btnUndo').onclick = undo;

  $('#btnPrev').onclick = () => {
    const i = pageIndex();
    if (i > 0) showPage(orderedPages()[i - 1].id);
  };
  $('#btnNext').onclick = () => {
    const i = pageIndex();
    const ps = orderedPages();
    if (i >= 0 && i < ps.length - 1) showPage(ps[i + 1].id);
  };

  $('#btnFit').onclick = () => { S.zoom = 1; applyZoom($('.page-holder')); };
  $('#btn100').onclick = () => { S.zoom = 1.0; applyZoom($('.page-holder')); };
  $('#btnZoomIn').onclick = () => {
    S.zoom = Math.min(4, (S.zoom === 1 ? 1.0 : S.zoom) * 1.2);
    applyZoom($('.page-holder'));
  };
  $('#btnZoomOut').onclick = () => {
    S.zoom = Math.max(0.15, (S.zoom === 1 ? 1.0 : S.zoom) / 1.2);
    applyZoom($('.page-holder'));
  };

  $('#btnExportPdf').onclick = () => exportAs('pdf');
  $('#btnExportImg').onclick = () => exportAs('zip');

  // 标签页
  $$('.tab').forEach(t => {
    t.onclick = () => {
      const which = t.dataset.tab;
      const left = which === 'assets' || which === 'pages';
      const scope = left ? $('.panel.left') : $('.panel.right');
      scope.querySelectorAll('.tab').forEach(x =>
        x.classList.toggle('on', x.dataset.tab === which));
      scope.querySelectorAll('.tab-body').forEach(x => {
        x.hidden = x.id !== 'tab-' + which;
      });
    };
  });

  // 上传
  $('#btnPick').onclick = (e) => { e.stopPropagation(); $('#fileInput').click(); };
  $('#btnEmptyUpload').onclick = () => $('#fileInput').click();
  $('#btnEmptyBubble').onclick = () => addElement('bubble');
  $('#fileInput').onchange = (e) => {
    uploadFiles(e.target.files);
    e.target.value = '';
  };
  const dz = $('#dropzone');
  ['dragenter', 'dragover'].forEach(k => {
    dz.addEventListener(k, (e) => { e.preventDefault(); dz.classList.add('over'); });
  });
  ['dragleave', 'drop'].forEach(k => {
    dz.addEventListener(k, () => dz.classList.remove('over'));
  });
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadFiles(e.dataTransfer.files);
  });

  // 画布接收从素材区拖来的图 / 直接拖入文件
  const host = $('#canvasHost');
  host.addEventListener('dragover', (e) => e.preventDefault());
  host.addEventListener('drop', async (e) => {
    e.preventDefault();
    if (!S.page) { toast('先新建一页'); return; }
    const aid = e.dataTransfer.getData('text/asset-id');
    if (aid) {
      const r = e.target.closest('.page-holder');
      let x = 0.15, y = 0.15;
      if (r) {
        const rect = r.getBoundingClientRect();
        x = round(clamp((e.clientX - rect.left) / rect.width, 0, 0.95));
        y = round(clamp((e.clientY - rect.top) / rect.height, 0, 0.95));
      }
      await addElement('image', { asset_id: aid, x, y });
      return;
    }
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      await uploadFiles(e.dataTransfer.files);
      toast('上传完成，点素材卡片即可加到画布');
    }
  });

  $('#btnAddPage').onclick = addPage;
  $('#btnAddPageFromAssets').onclick = pagesFromAssets;

  // 快捷键
  window.addEventListener('keydown', async (ev) => {
    // ★ 裁剪模式下只认 Esc：其它快捷键（Del / 方向键 / Ctrl+A…）会让路，
    //   否则一边调框一边把被裁的图删了/挪了。
    if (cropState) {
      if (ev.key === 'Escape') {
        ev.preventDefault(); exitCrop(); status('已取消裁剪');
      }
      return;
    }
    const tag = (ev.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    const e = curEl();

    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'z') {
      ev.preventDefault(); await undo(); return;
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'd') {
      if (e) { ev.preventDefault(); await duplicateElement(e.id); }
      return;
    }
    if (ev.key === 'Delete' || ev.key === 'Backspace') {
      if (S.sels.size) { ev.preventDefault(); await deleteSelected(); }
      return;
    }
    if (ev.key === 'Escape') {
      setSelection([], null); clearGuides();
      refreshSel(); renderProps(); renderLayers();
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'a') {
      ev.preventDefault();
      if (S.page) {
        setSelection(S.page.elements.map(x => x.id), null);
        refreshSel(); renderProps(); renderLayers();
        toast(`已全选 ${S.sels.size} 个元素`);
      }
      return;
    }
    if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight' ||
        ev.key === 'ArrowUp' || ev.key === 'ArrowDown') {
      const targets = selectedEls();
      if (!targets.length) return;
      ev.preventDefault();
      const step = ev.shiftKey ? 0.02 : 0.004;
      const dx = ev.key === 'ArrowLeft' ? -step
        : ev.key === 'ArrowRight' ? step : 0;
      const dy = ev.key === 'ArrowUp' ? -step
        : ev.key === 'ArrowDown' ? step : 0;
      targets.forEach(x => {
        x.x = round(clamp(x.x + dx, -0.5, 1.5));
        x.y = round(clamp(x.y + dy, -0.5, 1.5));
        const node = document.querySelector(`.ov[data-eid="${x.id}"]`);
        if (node) {
          node.style.left = x.x * 100 + '%';
          node.style.top = x.y * 100 + '%';
        }
      });
      const moves = {};
      targets.forEach(x => { moves[x.id] = { x: x.x, y: x.y }; });
      if (targets.length === 1) await saveProps(targets[0].id, moves[targets[0].id]);
      else await batchMove(moves);
    }
    if (ev.key === 'PageDown' || ev.key === 'PageUp') {
      const i = pageIndex(); const ps = orderedPages();
      const j = clamp(i + (ev.key === 'PageDown' ? 1 : -1), 0, ps.length - 1);
      if (ps[j]) showPage(ps[j].id);
    }
  });

  // Ctrl + 滚轮缩放
  window.addEventListener('wheel', (ev) => {
    if (!(ev.ctrlKey || ev.metaKey)) return;
    if (!ev.target.closest('#canvasHost')) return;
    ev.preventDefault();
    S.zoom = clamp((S.zoom === 1 ? 1.0 : S.zoom) * (ev.deltaY < 0 ? 1.12 : 0.9),
      0.15, 4);
    applyZoom($('.page-holder'));
  }, { passive: false });
}

/* ══════════════════════════════════════════════════
 * 启动
 * ══════════════════════════════════════════════════ */
(async function boot() {
  bind();
  try {
    const q = new URLSearchParams(location.search);
    await loadProjects(q.get('project'));
    const want = q.get('project');
    const list = S.projects;
    if (!list.length) {
      // 一个项目也没有 → 引导新建
      renderEmpty();
      toast('还没有项目，点右上角「新建」开始');
      setTimeout(newProjectDialog, 400);
      return;
    }
    const pick = want && list.some(p => p.name === want) ? want : list[0].name;
    await openProject(pick);
    document.documentElement.setAttribute('data-boot', 'ready');
  } catch (e) {
    document.documentElement.setAttribute('data-boot', 'error');
    const box = document.createElement('pre');
    box.style.cssText = 'position:fixed;left:16px;right:16px;bottom:16px;'
      + 'background:#3a1a1a;color:#ffb4b4;padding:14px;border-radius:8px;'
      + 'z-index:999;white-space:pre-wrap;font-size:12px';
    box.textContent = '启动失败：' + (e && e.message ? e.message : e)
      + '\n' + (e && e.stack ? e.stack : '');
    document.body.appendChild(box);
  }
})();

/* ── 仅用于截图验证：?_demo=guides 时自动全选并显示参考线 ──
   这是临时钩子，不影响正常使用。 */
if (new URLSearchParams(location.search).get('_demo') === 'guides') {
  setTimeout(() => {
    if (!S.page) return;
    setSelection(S.page.elements.map(x => x.id), null);
    S.guides = [
      { axis: 'x', pos: 0.5 },
      { axis: 'y', pos: 0.25 },
    ];
    const h = document.querySelector('.page-holder');
    if (h) drawGuides(h);
    refreshSel(); renderProps(); renderLayers();
    status('截图验证模式：已全选 ' + S.sels.size + ' 个元素并显示参考线');
  }, 2500);
}

/* ── 仅用于截图验证：?_demo=crop[&apply=1] ──
   命令行里没法点鼠标，所以这里走**真实代码路径**：
   选中第一张图 → enterCrop() → 向四条边派发真的 pointer 事件 →
   （apply=1 时再按「完成」，用来验证服务端真的按新 crop 重画了）。
   同时把框位置 / 算出的 crop 写进隐藏的 #cropDbg，方便 --dump-dom 取数核对。
   临时钩子，不影响正常使用。 */
if (new URLSearchParams(location.search).get('_demo') === 'crop') {
  const q = new URLSearchParams(location.search);
  setTimeout(async () => {
    const found = S.page && S.page.elements.find(x => x.kind === 'image');
    if (!found) { toast('演示：这一页没有图片元素', true); return; }
    // flip=1：先打开水平翻转，用来验证「裁剪数据 ↔ 屏幕左右」的换算
    if (q.get('flip') === '1') {
      await saveProps(found.id, { flip_h: true });
      await drawCanvas();
    }
    const img = S.page.elements.find(x => x.id === found.id);
    setSelection([img.id], img.id);
    renderProps(); renderLayers(); refreshSel();
    enterCrop(img);
    const holder = document.querySelector('.page-holder');
    const f = holder && holder.querySelector('.crop-frame');
    if (!cropState || !f) { toast('演示：没能进入裁剪模式', true); return; }
    // 画布的宽度是图片 onload 后 applyZoom 才定的；没宽度的话下面的拖动全是 0 位移
    for (let i = 0; i < 40 && !(f.getBoundingClientRect().width > 10); i++) {
      await new Promise(r => setTimeout(r, 200));
    }

    const drag = (k, dx, dy) => {
      const hd = f.querySelector('.crop-edge.' + k);
      if (!hd) return;
      const r = hd.getBoundingClientRect();
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      const mk = (t, x, y) => new PointerEvent(t,
        { clientX: x, clientY: y, bubbles: true, cancelable: true });
      hd.dispatchEvent(mk('pointerdown', cx, cy));
      window.dispatchEvent(mk('pointermove', cx + dx, cy + dy));
      window.dispatchEvent(mk('pointerup', cx + dx, cy + dy));
    };
    const r0 = f.getBoundingClientRect();
    drag('w', r0.width * 0.20, 0);
    drag('n', 0, r0.height * 0.10);
    drag('e', -r0.width * 0.15, 0);
    drag('s', 0, -r0.height * 0.12);

    const a = (S.proj.assets || []).find(x => x.id === img.asset_id) || {};
    const hr = holder.getBoundingClientRect(), fr = f.getBoundingClientRect();
    const dbg = document.createElement('pre');
    dbg.id = 'cropDbg';
    dbg.hidden = true;
    dbg.textContent = JSON.stringify({
      box: S.boxes[img.id], asset: [a.width, a.height], fit: img.fit,
      page: [S.page.width, S.page.height], ref: cropState.ref,
      flip: !!img.flip_h,
      frame: [cropState.sl, cropState.st, cropState.sr, cropState.sb],
      crop: cropFromFrame(),
      // 屏幕上的实际矩形（给截图核对用）
      holderRect: [hr.left, hr.top, hr.width, hr.height],
      frameRect: [fr.left, fr.top, fr.width, fr.height],
    });
    document.body.appendChild(dbg);
    // 状态栏保持和真实操作一致（截图要能直接当文档图用），数值看工具条读数
    status('裁剪中：拖四条边圈出要保留的部分；Esc 取消');
    if (q.get('apply') === '1') {
      await commitCrop();
      await new Promise(r => setTimeout(r, 1500));
      toast('演示：已应用裁剪');
    }
  }, 2500);
}
