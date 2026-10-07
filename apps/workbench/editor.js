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
  sel: null,           // 选中元素 id
  boxes: {},           // element_id -> [x,y,w,h] 归一化（服务端给的）
  drag: null,
  zoom: 1,             // 1 = 适应窗口
  busy: false,
};

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
    S.sel = null;
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
  S.sel = null;
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

  // ④ 点空白取消选择
  holder.onpointerdown = (ev) => {
    if (ev.target === holder || ev.target === im) {
      S.sel = null; renderProps(); renderLayers(); refreshSel();
    }
  };

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
  const n = el('div', 'ov' + (S.sel === e.id ? ' sel' : '') + (e.locked ? ' locked' : ''));
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
  $$('.ov').forEach(n => n.classList.toggle('sel', n.dataset.eid === S.sel));
}

/* ── 拖动 ─────────────────────────────────────── */
function startDrag(ev, e) {
  if (e.locked) { toast('该元素已锁定'); return; }
  ev.preventDefault(); ev.stopPropagation();
  S.sel = e.id; refreshSel(); renderProps(); renderLayers();

  const holder = $('.page-holder');
  const rect = holder.getBoundingClientRect();
  const node = holder.querySelector(`.ov[data-eid="${e.id}"]`);
  const start = { mx: ev.clientX, my: ev.clientY, x: e.x, y: e.y };
  let moved = false;

  const onMove = (ev2) => {
    const dx = (ev2.clientX - start.mx) / rect.width;
    const dy = (ev2.clientY - start.my) / rect.height;
    if (Math.abs(dx) > 0.002 || Math.abs(dy) > 0.002) moved = true;
    e.x = round(clamp(start.x + dx, -0.5, 1.5));
    e.y = round(clamp(start.y + dy, -0.5, 1.5));
    node.style.left = e.x * 100 + '%';
    node.style.top = e.y * 100 + '%';
    syncTail(e);
    status(`x ${(e.x * 100).toFixed(1)}%  y ${(e.y * 100).toFixed(1)}%`);
  };
  const onUp = async () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    if (!moved) { status(''); return; }
    await saveProps(e.id, { x: e.x, y: e.y });
    status('');
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

/** 保存元素属性（本地也同步，避免整页重载） */
async function saveProps(eid, props) {
  try {
    const r = await jpatch(
      `/api/edit/projects/${S.proj.name}/elements/${eid}`, { props });
    const i = S.page.elements.findIndex(x => x.id === eid);
    if (i >= 0) {
      const merged = Object.assign({}, S.page.elements[i], r.element);
      S.page.elements[i] = merged;
    }
    syncProjectCache(eid, r.element);
    return true;
  } catch (e) { toast('保存失败：' + e.message, true); return false; }
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
    it.onclick = () => {
      S.sel = e.id; refreshSel(); renderProps(); renderLayers();
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
    await api(url(`/api/edit/projects/${S.proj.name}/elements/${eid}`),
      { method: 'DELETE' });
    S.page.elements = S.page.elements.filter(x => x.id !== eid);
    const p = S.proj.pages.find(x => x.id === S.page.id);
    if (p) p.elements = S.page.elements;
    if (S.sel === eid) S.sel = null;
    await drawCanvas();
    toast('已删除');
  } catch (err) { toast('删除失败：' + err.message, true); }
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
      if (e) { ev.preventDefault(); await deleteElement(e.id); }
      return;
    }
    if (ev.key === 'Escape') { S.sel = null; refreshSel(); renderProps(); renderLayers(); return; }
    if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight' ||
        ev.key === 'ArrowUp' || ev.key === 'ArrowDown') {
      if (!e) return;
      ev.preventDefault();
      const step = ev.shiftKey ? 0.02 : 0.004;
      if (ev.key === 'ArrowLeft') e.x = round(clamp(e.x - step, -0.5, 1.5));
      if (ev.key === 'ArrowRight') e.x = round(clamp(e.x + step, -0.5, 1.5));
      if (ev.key === 'ArrowUp') e.y = round(clamp(e.y - step, -0.5, 1.5));
      if (ev.key === 'ArrowDown') e.y = round(clamp(e.y + step, -0.5, 1.5));
      const node = document.querySelector(`.ov[data-eid="${e.id}"]`);
      if (node) { node.style.left = e.x * 100 + '%'; node.style.top = e.y * 100 + '%'; }
      await saveProps(e.id, { x: e.x, y: e.y });
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
