/* AI Comic Studio · 工作台前端
 *
 * 设计取舍：
 *   ① 渲染在服务端 —— 前端只发坐标，保证与 CLI 产出一致
 *   ② 气泡可拖拽 + 可锁定 —— 自动布局必然有偏差，人工调完要锁住
 *   ③ 说话人必须人选 —— 不让程序猜
 *   ④ 页码只读 —— 冻结机制在 UI 上体现为不可编辑
 */
'use strict';

const API = '';
const S = {
  project: null,      // 项目数据
  page: 1,            // 当前页
  sel: null,          // 选中的对白 id
  boxes: true,        // 显示气泡框
  drag: null,
};

const $ = (s) => document.querySelector(s);
const el = (t, c, h) => {
  const n = document.createElement(t);
  if (c) n.className = c;
  if (h !== undefined) n.innerHTML = h;
  return n;
};

async function api(path, opt) {
  const r = await fetch(API + path, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text();
}

function toast(msg, ms = 2200) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('on'), ms);
}

/* ── 项目 ───────────────────────────────────────────── */
async function loadProjects() {
  const list = await api('/api/projects');
  const sel = $('#projectSel');
  sel.innerHTML = '';
  if (!list.length) {
    sel.appendChild(el('option', '', '（还没有项目）'));
    $('#canvasHost').innerHTML = '<div class="empty">还没有项目。'
      + '用 <code>python -m packages.cli adapt</code> 生成一个，'
      + '或运行 <code>python scripts/make_demo_project.py</code></div>';
    return;
  }
  list.forEach(p => {
    const o = el('option', '', `${p.name}　${p.pages}页`);
    o.value = p.name;
    sel.appendChild(o);
  });
  await openProject(list[0].name);
}

async function openProject(name) {
  S.project = await api(`/api/projects/${encodeURIComponent(name)}`);
  S.page = 1;
  S.sel = null;
  renderStats();
  renderCharList();
  renderPageList();
  await showPage(1);
}

/* ── 左栏 ───────────────────────────────────────────── */
function renderStats() {
  const st = S.project.stats;
  const host = $('#stats');
  host.innerHTML = '';
  const items = [
    ['页数', st.pages], ['镜头', st.panels],
    ['对白', st.utterances], ['章节', st.chapters],
  ];
  items.forEach(([k, v]) => {
    const d = el('div', 'stat');
    d.innerHTML = `<b>${v}</b><span>${k}</span>`;
    host.appendChild(d);
  });
}

function renderCharList() {
  const host = $('#charList');
  host.innerHTML = '';
  const chars = S.project.bible.characters || {};
  const ids = new Set();
  S.project.storyboards.forEach(sb => sb.panels.forEach(p =>
    (p.cast || []).forEach(c => ids.add(c.id))));
  Object.values(chars).forEach(c => {
    const used = ids.has(c.id);
    host.appendChild(el('span', 'chip' + (used ? '' : ' missing'), c.name));
  });
}

function renderPageList() {
  const host = $('#pageList');
  host.innerHTML = '';
  S.project.layout.pages.forEach(p => {
    let cls = 'pg';
    if (p.type === 'title') cls += ' title';
    if (p.type === 'empty') cls += ' empty';
    if (p.page === S.page) cls += ' active';
    const n = el('div', cls, p.page);
    n.title = p.type === 'title' ? (p.title || '标题页')
      : p.type === 'empty' ? ('空位：' + (p.note || ''))
        : (p.panels || []).join('\n');
    n.onclick = () => showPage(p.page);
    host.appendChild(n);
  });
}

/* ── 画布 ───────────────────────────────────────────── */
function currentPage() {
  return S.project.layout.pages.find(p => p.page === S.page);
}

async function showPage(no) {
  S.page = no;
  S.sel = null;
  const lp = currentPage();
  $('#pageLabel').textContent = `第 ${no} / ${S.project.layout.total} 页`
    + (lp && lp.type === 'title' ? '（标题页）' : '');

  const host = $('#canvasHost');
  host.innerHTML = '<div class="empty">渲染中…</div>';

  const wrap = el('div', 'page-rel');
  const img = el('img');
  img.src = `/api/projects/${encodeURIComponent(S.project.name)}/pages/${no}.png?w=760&t=${Date.now()}`;
  img.onload = () => { host.innerHTML = ''; host.appendChild(wrap); drawBoxes(wrap, img); };
  img.onerror = () => { host.innerHTML = '<div class="empty">这一页渲染失败</div>'; };
  wrap.appendChild(img);

  renderUtts();
  renderPanels();
  renderPageList();
}

/** 在页面图上叠加「气泡框」与「箭头尾端」，可拖动 */
function drawBoxes(wrap, img) {
  wrap.querySelectorAll('.box,.tail-dot').forEach(n => n.remove());
  if (!S.boxes) return;

  const lp = currentPage();
  if (!lp || !lp.panels) return;

  const dispW = img.clientWidth, dispH = img.clientHeight;
  const scaleX = dispW, scaleY = dispH;   // 归一化坐标 × 显示尺寸

  lp.panels.forEach(pid => {
    const utts = (S.project.dialogue.items || {})[pid] || [];
    utts.forEach(u => {
      const b = u.bubble || {};
      const est = estimateBox(u);          // 服务端不返回尺寸，按文字估一个
      const x = (b.box ? b.box.x : est.x) * scaleX;
      const y = (b.box ? b.box.y : est.y) * scaleY;
      const w = est.w * scaleX, h = est.h * scaleY;

      const box = el('div', 'box' + (u.locked ? ' locked' : '')
        + (S.sel === u.id ? ' sel' : ''));
      box.style.cssText += `left:${x}px;top:${y}px;width:${w}px;height:${h}px`;
      box.appendChild(el('div', 'lbl',
        `${who(u.who)}${u.locked ? ' 🔒' : ''}`));
      box.onpointerdown = (e) => startDrag(e, 'box', u, box, scaleX, scaleY);
      wrap.appendChild(box);

      // 箭头尾端
      if (b.tail) {
        const d = el('div', 'tail-dot');
        d.style.cssText += `left:${b.tail.x * scaleX}px;top:${b.tail.y * scaleY}px`;
        d.title = '拖动可改箭头指向';
        d.onpointerdown = (e) => startDrag(e, 'tail', u, d, scaleX, scaleY);
        wrap.appendChild(d);
      }
    });
  });
}

/** 服务端算出的尺寸未回传，这里按文字长度粗估一个框（仅用于可视化编辑） */
function estimateBox(u) {
  const len = (u.text || '').length;
  const perLine = 11;
  const lines = Math.max(1, Math.ceil(len / perLine));
  return { x: 0.05, y: 0.04, w: 0.34, h: 0.055 * lines + 0.05 };
}

function who(id) {
  const c = (S.project.bible.characters || {})[id];
  return c ? c.name : id;
}

function startDrag(e, kind, utt, node, sx, sy) {
  e.preventDefault();
  e.stopPropagation();
  S.sel = utt.id;
  renderUtts();
  const wrap = node.parentElement;
  const rect = wrap.getBoundingClientRect();
  S.drag = { kind, utt, node, rect, sx, sy };
  node.setPointerCapture?.(e.pointerId);

  const move = (ev) => {
    const x = Math.min(Math.max((ev.clientX - rect.left) / sx, 0), 0.98);
    const y = Math.min(Math.max((ev.clientY - rect.top) / sy, 0), 0.98);
    if (kind === 'box') {
      node.style.left = (x * sx) + 'px';
      node.style.top = (y * sy) + 'px';
    } else {
      node.style.left = (x * sx) + 'px';
      node.style.top = (y * sy) + 'px';
    }
    S.drag.last = { x, y };
  };
  const up = async () => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    if (!S.drag || !S.drag.last) return;
    const { x, y } = S.drag.last;
    const panelId = findPanelOf(utt.id);
    const body = kind === 'box' ? { box: { x, y } }
      : { tail: { x, y } };
    try {
      await api(`/api/projects/${encodeURIComponent(S.project.name)}`
        + `/dialogue/${panelId}/${utt.id}`,
        { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body) });
      // 本地同步，避免整页重载
      const items = S.project.dialogue.items[panelId];
      const t = items.find(a => a.id === utt.id);
      if (kind === 'box') t.bubble.box = { x, y }; else t.bubble.tail = { x, y };
      toast('已保存气泡位置');
      refreshPageImage();
    } catch (err) { toast('保存失败：' + err.message); }
    S.drag = null;
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up, { once: true });
}

function findPanelOf(uttId) {
  const items = S.project.dialogue.items || {};
  for (const pid in items) if (items[pid].some(u => u.id === uttId)) return pid;
  return null;
}

function refreshPageImage() {
  const img = document.querySelector('#canvasHost img');
  if (!img) return;
  const base = img.src.split('?')[0];
  const fresh = el('img');
  fresh.src = `${base}?w=760&t=${Date.now()}`;
  fresh.onload = () => {
    const wrap = el('div', 'page-rel');
    wrap.appendChild(fresh);
    const host = $('#canvasHost');
    host.innerHTML = '';
    host.appendChild(wrap);
    drawBoxes(wrap, fresh);
  };
}

/* ── 右栏：对白 ─────────────────────────────────────── */
function renderUtts() {
  const host = $('#uttList');
  host.innerHTML = '';
  const lp = currentPage();
  if (!lp || !lp.panels) { host.innerHTML = '<div class="hint">本页无对白</div>'; return; }

  let count = 0;
  const chars = Object.values(S.project.bible.characters || {});
  lp.panels.forEach(pid => {
    const utts = (S.project.dialogue.items || {})[pid] || [];
    utts.forEach(u => {
      count++;
      const card = el('div', 'utt' + (S.sel === u.id ? ' sel' : ''));

      const row = el('div', 'row');
      const sel = el('select');
      chars.forEach(c => {
        const o = el('option', '', c.name);
        o.value = c.id;
        if (c.id === u.who) o.selected = true;
        sel.appendChild(o);
      });
      sel.onchange = async () => {
        try {
          await api(`/api/projects/${encodeURIComponent(S.project.name)}`
            + `/dialogue/${pid}/${u.id}`,
            { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ who: sel.value }) });
          u.who = sel.value;
          u.confirmed = false;      // 换人必须重新确认
          toast('说话人已改，需重新确认');
          renderUtts(); refreshPageImage();
        } catch (e) { toast('失败：' + e.message); }
      };
      row.appendChild(sel);

      const badge = el('span', 'badge ' + (u.confirmed ? 'ok' : 'warn'),
        u.confirmed ? '已确认' : '待确认');
      badge.title = '未经确认的说话人不会进入渲染';
      badge.style.cursor = 'pointer';
      badge.onclick = async () => {
        try {
          await api(`/api/projects/${encodeURIComponent(S.project.name)}`
            + `/dialogue/${pid}/${u.id}`,
            { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ confirmed: !u.confirmed }) });
          u.confirmed = !u.confirmed;
          renderUtts();
        } catch (e) { toast('失败：' + e.message); }
      };
      row.appendChild(badge);
      card.appendChild(row);

      const ta = el('textarea');
      ta.value = u.text;
      ta.onchange = async () => {
        try {
          await api(`/api/projects/${encodeURIComponent(S.project.name)}`
            + `/dialogue/${pid}/${u.id}`,
            { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ text: ta.value }) });
          u.text = ta.value;
          toast('台词已保存');
          refreshPageImage();
        } catch (e) { toast('失败：' + e.message); }
      };
      card.appendChild(ta);

      const meta = el('div', 'meta');
      const lock = el('label');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = !!u.locked;
      cb.onchange = async () => {
        try {
          await api(`/api/projects/${encodeURIComponent(S.project.name)}`
            + `/dialogue/${pid}/${u.id}`,
            { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ locked: cb.checked }) });
          u.locked = cb.checked;
          toast(cb.checked ? '已锁定（自动布局不再改动）' : '已解锁');
          renderUtts(); refreshPageImage();
        } catch (e) { toast('失败：' + e.message); }
      };
      lock.appendChild(cb);
      lock.appendChild(document.createTextNode('锁定'));
      meta.appendChild(lock);
      meta.appendChild(el('span', '', pid));
      card.appendChild(meta);

      host.appendChild(card);
    });
  });
  if (!count) host.innerHTML = '<div class="hint">本页无对白</div>';
}

/* ── 右栏：镜头 ─────────────────────────────────────── */
function renderPanels() {
  const host = $('#panelList');
  host.innerHTML = '';
  const lp = currentPage();
  if (!lp || !lp.panels) { host.innerHTML = '<div class="hint">—</div>'; return; }

  const all = {};
  S.project.storyboards.forEach(sb => sb.panels.forEach(p => all[p.id] = p));

  lp.panels.forEach(pid => {
    const p = all[pid];
    if (!p) { host.appendChild(el('div', 'pcard', pid + '（缺失）')); return; }
    const cast = (p.cast || []).map(c => who(c.id)).join('、') || '空镜';
    const card = el('div', 'pcard');
    card.innerHTML = `<b>${p.id}</b>　<span class="sh">${p.shot}·${p.size}</span><br>`
      + `<span class="sh">${cast}</span><br>${p.action || ''}`
      + (p.distance ? `<br><span class="sh">距离：${p.distance}</span>` : '')
      + (p.skill ? `<br><span class="sh">技能：${p.skill}</span>` : '');
    host.appendChild(card);
  });
}

/* ── 顶栏动作 ───────────────────────────────────────── */
$('#projectSel').onchange = (e) => openProject(e.target.value);
$('#prevPage').onclick = () => showPage(Math.max(1, S.page - 1));
$('#nextPage').onclick = () => showPage(Math.min(S.project.layout.total, S.page + 1));
$('#showBoxes').onchange = (e) => {
  S.boxes = e.target.checked;
  const img = document.querySelector('#canvasHost img');
  if (img) drawBoxes(img.parentElement, img);
};

$('#btnValidate').onclick = async () => {
  if (!S.project) return;
  const r = await api(`/api/projects/${encodeURIComponent(S.project.name)}/validate`,
    { method: 'POST' });
  const m = el('div', 'modal on');
  const inner = el('div', 'inner');
  inner.appendChild(el('h2', '', r.ok ? '✅ 校验通过' : '⚠️ 发现 ' + r.errors.length + ' 个问题'));
  inner.appendChild(el('p', 'hint', r.summary));
  r.errors.forEach(e => {
    const row = el('div', 'err-row' + (e.severity === 'warning' ? ' warn' : ''));
    row.innerHTML = `<span class="r">${e.rule}</span> ${e.where}<br>${e.message}`
      + (e.hint ? `<div class="h">修正：${e.hint}</div>` : '');
    inner.appendChild(row);
  });
  const close = el('button', 'primary', '关闭');
  close.style.marginTop = '12px';
  close.onclick = () => m.remove();
  inner.appendChild(close);
  m.onclick = (e) => { if (e.target === m) m.remove(); };
  m.appendChild(inner);
  document.body.appendChild(m);
};

$('#btnRender').onclick = async () => {
  if (!S.project) return;
  const r = await api(`/api/projects/${encodeURIComponent(S.project.name)}/render`,
    { method: 'POST' });
  const tid = r.task.id;
  toast('已开始出图…');
  const poll = setInterval(async () => {
    const t = await api(`/api/tasks/${tid}`);
    if (t.status !== 'running') {
      clearInterval(poll);
      toast(`出图${t.status === 'done' ? '完成' : '结束'}：${t.message}`, 4200);
      showPage(S.page);
    } else {
      $('#reloadHint').textContent = `${t.done}/${t.total} ${t.message}`;
    }
  }, 1200);
};

$('#btnExport').onclick = async () => {
  if (!S.project) return;
  toast('正在导出…');
  const r = await api(`/api/projects/${encodeURIComponent(S.project.name)}`
    + `/export?fmt=pdf`, { method: 'POST' });
  toast('导出完成，开始下载');
  window.location = r.file;
};

/* ── 启动 ───────────────────────────────────────────── */
loadProjects().catch(e => toast('加载失败：' + e.message));
