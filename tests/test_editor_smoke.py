# -*- coding: utf-8 -*-
"""在 Node 里真跑一遍 editor.js 的顶层代码（DOM 打桩）

★ 为什么需要这条测试（真实事故）：
  我把 `const DEMO_MODE` 写在文件末尾，却在文件中间就用了它。
  `const` 有暂时性死区（TDZ）—— 顶层第一行 `if (DEMO_MODE === 'guides')`
  当场抛 `ReferenceError`，**顶层脚本就此中断**。

  而当时：
    · `node --check` 通过（它只查语法，不查 TDZ）
    · pytest 365 条全绿（没有一条真跑过这段代码）
    · 界面照常启动（`data-boot=ready`），所以**没人会发现**

  只有真开一次浏览器才抓得到。这条测试就是那个「真开一次」，
  只是用 Node + DOM 打桩代替浏览器，跑得快还能进 CI。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "apps", "workbench", "editor.js")
NODE = shutil.which("node")

#: 用 defineProperty 打桩 —— Node 24 里 `navigator` / `location` 是只读 getter，
#: 直接赋值会抛 TypeError，测试自己就挂了（这坑踩过一次）。
DEF = r"""
const def = (name, value) => {
  try {
    Object.defineProperty(globalThis, name, {
      value, writable: true, configurable: true, enumerable: false,
    });
  } catch (e) { /* 只读且不可配置就跳过，被测代码多半也不依赖它 */ }
};
"""

#: DOM 打桩。够让 editor.js 的顶层代码跑完即可，不需要真渲染。
STUBS = DEF + r"""
// ── 最小 DOM 打桩 ──────────────────────────────────────────
const noop = () => {};
const mkEl = (tag = 'div') => {
  const e = {
    tagName: String(tag).toUpperCase(),
    style: {}, dataset: {}, classList: {
      add: noop, remove: noop, toggle: noop, contains: () => false,
    },
    children: [], childNodes: [], _text: '',
    set textContent(v) { this._text = String(v); },
    get textContent() { return this._text; },
    set innerHTML(v) { this._html = String(v); },
    get innerHTML() { return this._html || ''; },
    setAttribute: noop, getAttribute: () => null,
    removeAttribute: noop, hasAttribute: () => false,
    appendChild(c) { this.children.push(c); return c; },
    insertBefore(c) { this.children.push(c); return c; },
    removeChild: noop, remove: noop, replaceChildren: noop,
    addEventListener: noop, removeEventListener: noop,
    querySelector: () => null, querySelectorAll: () => [],
    getBoundingClientRect: () => ({ x: 0, y: 0, width: 800, height: 600,
      top: 0, left: 0, right: 800, bottom: 600 }),
    focus: noop, blur: noop, click: noop, scrollIntoView: noop,
    getContext: () => null,
    get firstChild() { return this.children[0] || null; },
  };
  return e;
};
def('document', {
  body: mkEl('body'),
  documentElement: mkEl('html'),
  head: mkEl('head'),
  title: '',
  createElement: mkEl,
  createTextNode: (t) => ({ textContent: String(t) }),
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: noop,
  removeEventListener: noop,
  readyState: 'complete',
  hidden: false,
  execCommand: noop,
});
try { globalThis.window = globalThis; } catch (e) {}
def('location', {
  href: 'http://127.0.0.1:8210/', search: '__SEARCH__',
  hostname: '127.0.0.1', protocol: 'http:', origin: 'http://127.0.0.1:8210',
  reload: noop, assign: noop,
});
def('navigator', { userAgent: 'node', clipboard: { writeText: noop } });
def('localStorage', {
  _d: {}, getItem(k) { return this._d[k] ?? null; },
  setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; },
  clear() { this._d = {}; },
});
def('alert', noop);
def('confirm', () => true);
def('prompt', () => null);
def('requestAnimationFrame', (f) => setTimeout(f, 0));
def('cancelAnimationFrame', noop);
if (!globalThis.URL) def('URL', class { constructor(u) { this.href = u; } });
try { globalThis.URL.createObjectURL = () => 'blob:x'; } catch (e) {}
if (!globalThis.Blob) def('Blob', class { constructor() {} });
if (!globalThis.FormData) def('FormData', class {
  constructor() { this._d = []; } append(k, v) { this._d.push([k, v]); }
});
if (!globalThis.FileReader) def('FileReader', class {
  readAsDataURL() { this.onload && this.onload({ target: { result: 'x' } }); }
});
if (!globalThis.Image) def('Image', class {
  constructor() { this.width = 100; this.height = 100; }
  set src(v) { this.onload && this.onload(); }
});
def('getComputedStyle', () => ({ getPropertyValue: () => '' }));
// fetch：所有请求都返回空数据，别让顶层初始化挂住
def('fetch', async () => ({
  ok: true, status: 200,
  headers: { get: () => null },
  json: async () => ([]),
  text: async () => '',
  blob: async () => ({}),
  arrayBuffer: async () => new ArrayBuffer(0),
}));
"""


def _run_top_level(search: str = "") -> subprocess.CompletedProcess:
    """在 Node 里跑 editor.js 的顶层代码"""
    if not NODE:
        pytest.skip("需要 node")
    src = open(JS, encoding="utf-8").read()
    stub = STUBS.replace("__SEARCH__", json.dumps(search))
    tmp = os.path.join(tempfile.gettempdir(),
                       f"_smoke_{abs(hash(search)) % 10 ** 8}.mjs")
    body = (
        stub
        + "\n// ── 被测代码 ──────────────────────────────────────────\n"
        + src
        + "\nconsole.log('__TOP_LEVEL_OK__');\n"
    )
    open(tmp, "w", encoding="utf-8").write(body)
    try:
        return subprocess.run([NODE, tmp], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=90)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════

def test_editor_js_top_level_runs():
    """★ 回归：editor.js 的顶层代码必须能跑到底

    抓的就是 TDZ 那类错：`const X` 写在用了 X 的代码**后面**。
    `node --check` 查不出（它只看语法），pytest 的静态测试也查不出，
    但真跑一次顶层就会 ReferenceError，而且**顶层脚本当场中断** ——
    后面的钩子、初始化全都不执行，界面还照常显示，静默失效。
    """
    r = _run_top_level()
    assert "__TOP_LEVEL_OK__" in (r.stdout or ""), (
        "editor.js 顶层没跑完\n"
        f"--- stdout ---\n{(r.stdout or '')[-1500:]}\n"
        f"--- stderr ---\n{(r.stderr or '')[-2500:]}")
    assert "ReferenceError" not in (r.stderr or ""), r.stderr[-2000:]


def test_demo_hooks_actually_run():
    """★ 回归：截图验证钩子必须真的执行

    TDZ 事故的直接症状就是这个：`?_demo=guides` 时状态栏没字。
    钩子失效不影响界面，所以只有主动去查才会发现。
    """
    r = _run_top_level("?_demo=guides")
    assert "__TOP_LEVEL_OK__" in (r.stdout or ""), (
        f"带 ?_demo=guides 时顶层没跑完：\n{(r.stderr or '')[-2000:]}")


def test_demo_hooks_gated_to_localhost():
    """★ 安全闸：非 localhost 时钩子不能生效

    这几个钩子会**真的改项目数据**（crop&apply=1 提交裁剪、
    template&force=1 清空页面重排）。公开 Demo 上必须失效。
    """
    src = open(JS, encoding="utf-8").read()
    assert "DEMO_MODE" in src
    # 闸门判断必须同时包含这三个
    i = src.index("const DEMO_MODE")
    seg = src[i:i + 300]
    for host in ("localhost", "127.0.0.1", "::1"):
        assert host in seg, f"安全闸没包含 {host}"
    # 不许有裸读 _demo 的地方（绕过闸门）
    bare = src.count("get('_demo')")
    assert bare == 1, f"有 {bare} 处裸读 _demo，应只有 DEMO_MODE 那一处"


def _strip_comments(src: str) -> str:
    """把 // 与 /* */ 注释换成等长空白（保住行号）

    ★ 必须先剥注释再查「先用后声明」——
      否则注释里提到 `DEMO_MODE === 'x'` 就会被当成使用点，第一版就误报了。
    """
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(c if c == "\n" else " " for c in src[i:j]))
            i = j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def test_demo_mode_declared_before_use():
    """★ 结构保证：DEMO_MODE 的声明必须在第一次使用**之前**

    这条是 TDZ 事故的根因检查 —— 写成断言比靠人记靠谱。
    """
    src = _strip_comments(open(JS, encoding="utf-8").read())
    decl = src.index("const DEMO_MODE")
    uses = [m for m in range(len(src)) if src.startswith("DEMO_MODE ===", m)]
    assert uses, "没找到 DEMO_MODE 的使用点"
    first_use = uses[0]
    assert decl < first_use, (
        f"DEMO_MODE 声明在第 {src[:decl].count(chr(10)) + 1} 行，"
        f"但第 {src[:first_use].count(chr(10)) + 1} 行就用了它 "
        f"—— const 有暂时性死区，会抛 ReferenceError 并中断顶层脚本")


def test_no_top_level_const_used_before_declaration():
    """★ 同类问题的通用检查：顶层 const/let 不许先用后声明

    不指望覆盖所有情况，但能挡住这个坑的常见形态。
    """
    import re
    src = _strip_comments(open(JS, encoding="utf-8").read())
    lines = src.splitlines()

    decls = {}
    for i, line in enumerate(lines):
        m = re.match(r"^(?:const|let)\s+([A-Za-z_$][\w$]*)", line)
        if m:
            decls.setdefault(m.group(1), i)

    bad = []
    for name, dline in decls.items():
        pat = re.compile(
            rf"(?<![\w$.]){re.escape(name)}(?![\w$])\s*(?:===|!==|==|\.|\?|\))")
        for i, line in enumerate(lines[:dline]):
            if pat.search(line):
                bad.append((name, i + 1, dline + 1, line.strip()[:70]))
                break
    assert not bad, (
        "顶层变量先用后声明（const/let 有 TDZ，会中断脚本）：\n"
        + "\n".join(f"  {n}: 第 {u} 行用了，第 {d} 行才声明 —— {t}"
                    for n, u, d, t in bad))
