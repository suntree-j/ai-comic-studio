# -*- coding: utf-8 -*-
"""前端交互逻辑测试（在 node 里跑 editor.js 的真实函数）

★ 为什么需要：
    对齐吸附是纯数学，错了会让人以为「拖不准」，但接口测试全绿。
    这类逻辑值得单独验。
    做法：把 editor.js 里的函数**抠出来**在 node 里跑 ——
    测的是真实代码，不是复制品。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "apps", "workbench", "editor.js")

NODE = shutil.which("node")


def _grab(name: str, src: str) -> str:
    """从 editor.js 里抠出一个函数（按大括号配平）"""
    i = src.index(f"function {name}(")
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise ValueError(f"找不到函数 {name}")


def _run(js_body: str) -> subprocess.CompletedProcess:
    if not NODE:
        pytest.skip("需要 node")
    src = open(JS, encoding="utf-8").read()
    prelude = """
const SNAP = 0.008;
const SNAP_X = [0, 0.5, 1];
const SNAP_Y = [0, 0.5, 1];
const S = { sels: new Set(), boxes: {}, page: null };
const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));
const round = (v,p=4)=>Math.round(v*10**p)/10**p;
"""
    helpers = "\n".join(_grab(f, src) for f in
                        ("selectedEls", "selectionBounds", "snapMove"))
    tmp = os.path.join(tempfile.gettempdir(), f"_snap_{abs(hash(js_body)) % 10**8}.mjs")
    open(tmp, "w", encoding="utf-8").write(prelude + helpers + js_body)
    try:
        return subprocess.run([NODE, tmp], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════
# snapMove
# ══════════════════════════════════════════════════════════════════

def test_snap_to_canvas_left_edge():
    r = _run("""
const b = { x0: 0.003, y0: 0.2, x1: 0.303, y1: 0.3 };
const s = snapMove(b, []);
console.log(JSON.stringify({ dx: s.dx, guides: s.guides.length }));
""")
    assert r.returncode == 0, r.stderr[:400]
    import json
    d = json.loads(r.stdout.strip())
    assert abs(d["dx"] + 0.003) < 1e-6, "应把左边拉到 0"
    assert d["guides"] > 0, "应该给出参考线"


def test_snap_to_canvas_center():
    r = _run("""
// 中心 0.504，距中线 0.004 < 阈值 0.008 → 吸回 0.5
const b = { x0: 0.344, y0: 0.2, x1: 0.664, y1: 0.3 };
const s = snapMove(b, []);
console.log(s.dx);
""")
    assert r.returncode == 0, r.stderr[:400]
    dx = float(r.stdout.strip())
    assert abs(dx - (-0.004)) < 1e-5, f"中心偏 0.004 应吸回，得到 dx={dx}"


def test_no_snap_beyond_threshold():
    r = _run("""
// 三条边分别 0.30 / 0.45 / 0.60，都离 0/0.5/1 超过 0.008
const b = { x0: 0.30, y0: 0.2, x1: 0.60, y1: 0.3 };
const s = snapMove(b, []);
console.log(s.dx);
""")
    assert r.returncode == 0, r.stderr[:400]
    assert abs(float(r.stdout.strip())) < 1e-9, "超出阈值不该吸附"


def test_snap_to_other_element():
    """右边缘贴到另一个元素的左边缘"""
    r = _run("""
const other = [0.4, 0.1, 0.2, 0.2];
const b = { x0: 0.202, y0: 0.6, x1: 0.402, y1: 0.7 };
const s = snapMove(b, [other]);
console.log(b.x1 + s.dx);
""")
    assert r.returncode == 0, r.stderr[:400]
    assert abs(float(r.stdout.strip()) - 0.4) < 1e-6, "应吸到另一个元素的左边"


def test_vertical_snap():
    r = _run("""
const b = { x0: 0.2, y0: 0.496, x1: 0.4, y1: 0.596 };  // 上边离 0.5 差 0.004
const s = snapMove(b, []);
console.log(s.dy);
""")
    assert r.returncode == 0, r.stderr[:400]
    assert abs(float(r.stdout.strip()) - 0.004) < 1e-5, "应吸到水平中线"


def test_guides_axis_is_correct():
    """水平吸附给 x 轴参考线，垂直吸附给 y 轴参考线"""
    r = _run("""
const bx = { x0: 0.003, y0: 0.2, x1: 0.203, y1: 0.3 };
const gx = snapMove(bx, []).guides;
const by = { x0: 0.2, y0: 0.003, x1: 0.4, y1: 0.103 };
const gy = snapMove(by, []).guides;
console.log(JSON.stringify({
  x: gx.filter(g => g.axis === 'x').length,
  y: gy.filter(g => g.axis === 'y').length,
}));
""")
    assert r.returncode == 0, r.stderr[:400]
    import json
    d = json.loads(r.stdout.strip())
    assert d["x"] > 0 and d["y"] > 0


# ══════════════════════════════════════════════════════════════════
# selectionBounds（多选包围盒）
# ══════════════════════════════════════════════════════════════════

def test_selection_bounds_covers_all():
    r = _run("""
S.page = { elements: [
  { id: 'a' }, { id: 'b' }, { id: 'c' },
] };
S.boxes = {
  a: [0.1, 0.1, 0.2, 0.2],
  b: [0.5, 0.4, 0.1, 0.1],
  c: [0.9, 0.9, 0.05, 0.05],   // 没选中
};
S.sels = new Set(['a', 'b']);
const b = selectionBounds();
console.log(JSON.stringify(b));
""")
    assert r.returncode == 0, r.stderr[:400]
    import json
    b = json.loads(r.stdout.strip())
    assert b["n"] == 2, "只算选中的"
    assert abs(b["x0"] - 0.1) < 1e-9 and abs(b["y0"] - 0.1) < 1e-9
    assert abs(b["x1"] - 0.6) < 1e-9, f"右边界应为 max(0.3,0.6)={b['x1']}"
    assert abs(b["y1"] - 0.5) < 1e-9


def test_selection_bounds_empty():
    r = _run("""
S.page = { elements: [] };
S.sels = new Set();
console.log(selectionBounds() === null);
""")
    assert r.returncode == 0, r.stderr[:400]
    assert r.stdout.strip() == "true"


def test_selection_bounds_ignores_missing_boxes():
    """素材丢了没有盒子 → 不参与包围盒计算，不能算出 NaN"""
    r = _run("""
S.page = { elements: [{ id: 'a' }, { id: 'b' }] };
S.boxes = { a: [0.2, 0.2, 0.1, 0.1] };      // b 没有盒子
S.sels = new Set(['a', 'b']);
const b = selectionBounds();
console.log(JSON.stringify(b));
""")
    assert r.returncode == 0, r.stderr[:400]
    import json
    b = json.loads(r.stdout.strip())
    assert b["n"] == 1
    assert b["x0"] == b["x0"] and b["x0"] != float("inf"), "不能是 NaN/inf"


# ══════════════════════════════════════════════════════════════════
# 源码层面的功能存在性
# ══════════════════════════════════════════════════════════════════

def test_multi_select_features_present():
    src = open(JS, encoding="utf-8").read()
    for kw, why in [
        ("S.sels", "多选集合"),
        ("function setSelection", "设置选中集合"),
        ("function startMarquee", "框选"),
        ("function batchMove", "批量移动（一条请求）"),
        ("function deleteSelected", "批量删除"),
        ("ev.shiftKey", "Shift 加减选"),
        ("function snapMove", "对齐吸附"),
        ("function drawGuides", "画参考线"),
        ("function clearGuides", "清除参考线"),
        ("revision", "乐观并发（带版本号）"),
    ]:
        assert kw in src, f"缺少：{why}（{kw}）"


def test_revision_sent_with_writes():
    """所有写请求都要带上 revision"""
    src = open(JS, encoding="utf-8").read()
    assert "?revision=${S.revision}" in src or "revision=${S.revision}" in src
    assert "409" in src or "已被其他人修改" in src, "要处理版本冲突"


def test_snap_disabled_while_shift_selecting():
    """Shift 点选时不该触发拖动（否则加选会顺手挪动元素）"""
    src = open(JS, encoding="utf-8").read()
    i = src.index("function startDrag(")
    head = src[i:i + 700]
    assert "ev.shiftKey" in head and "return" in head


# ══════════════════════════════════════════════════════════════════
# 页面模板：分格几何
# ══════════════════════════════════════════════════════════════════

def _tpl_run(body: str) -> subprocess.CompletedProcess:
    """在 node 里跑真实的 TEMPLATES / tplSpans / tplCells

    ★ 从 editor.js 里**抠出真代码**来跑，不是复制一份 ——
      复制品测不出「改了实现忘了改测试」。
    """
    if not NODE:
        pytest.skip("需要 node")
    src = open(JS, encoding="utf-8").read()
    prelude = """
const TPL_GAP = 0.008;
const round = (v, p = 4) => Math.round(v * 10 ** p) / 10 ** p;
"""
    # TEMPLATES 是 const 数组字面量，用括号配平抠出来
    i = src.index("const TEMPLATES = [")
    j = src.index("];", i) + 2
    templates = src[i:j]
    fns = "\n".join(_grab(f, src) for f in ("tplSpans", "tplCells"))
    tmp = os.path.join(tempfile.gettempdir(),
                       f"_tpl_{abs(hash(body)) % 10 ** 8}.mjs")
    open(tmp, "w", encoding="utf-8").write(
        prelude + templates + "\n" + fns + "\n" + body)
    try:
        return subprocess.run([NODE, tmp], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _tpl_json(body: str):
    import json
    r = _tpl_run(body)
    assert r.returncode == 0, (r.stderr or "")[:500]
    return json.loads(r.stdout.strip())


def test_template_count_and_ids():
    d = _tpl_json("""
console.log(JSON.stringify(TEMPLATES.map(t => [t.id, t.name, t.rows.length,
  t.rows.reduce((a, r) => a + r.length, 0)])));
""")
    ids = [x[0] for x in d]
    assert len(ids) == len(set(ids)), "模板 id 有重复"
    assert len(ids) >= 5, f"至少要 5 种模板，只有 {len(ids)}"
    for tid, name, _rows, cells in d:
        assert name, f"{tid} 没名字"
        assert cells >= 1


def test_all_templates_stay_inside_canvas():
    """每一格都必须在 0..1 里 —— 越界会渲染到画布外，用户看不见还删不掉"""
    d = _tpl_json("""
const out = {};
TEMPLATES.forEach(t => {
  out[t.id] = tplCells(t).map(c => [c.x, c.y, c.w, c.h]);
});
console.log(JSON.stringify(out));
""")
    for tid, cells in d.items():
        for k, (x, y, w, h) in enumerate(cells):
            assert -1e-9 <= x and x + w <= 1 + 1e-9, \
                f"{tid} 第{k+1}格 横向越界：x={x} w={w}"
            assert -1e-9 <= y and y + h <= 1 + 1e-9, \
                f"{tid} 第{k+1}格 纵向越界：y={y} h={h}"
            assert w > 0 and h > 0, f"{tid} 第{k+1}格 尺寸非正"


def test_all_templates_have_no_overlap():
    """格子之间不能重叠 —— 重叠的格子会互相盖住，用户摆图时会莫名其妙"""
    d = _tpl_json("""
const EPS = 1e-9;
const out = {};
TEMPLATES.forEach(t => {
  const cs = tplCells(t);
  let bad = null;
  for (let i = 0; i < cs.length && !bad; i++) {
    for (let j = i + 1; j < cs.length; j++) {
      const a = cs[i], b = cs[j];
      const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      if (ox > EPS && oy > EPS) {
        bad = { i, j, ox, oy };
        break;
      }
    }
  }
  out[t.id] = bad;
});
console.log(JSON.stringify(out));
""")
    for tid, bad in d.items():
        assert bad is None, f"{tid} 的格子重叠了：{bad}"


def test_multi_cell_templates_have_gap():
    """多格之间要留缝（漫画分格的白边），且缝宽等于 TPL_GAP"""
    d = _tpl_json("""
const out = {};
TEMPLATES.forEach(t => {
  const cs = tplCells(t);
  if (cs.length < 2) { out[t.id] = null; return; }
  // 相邻两格之间的最小缝隙
  let best = 1;
  for (let i = 0; i < cs.length; i++) {
    for (let j = i + 1; j < cs.length; j++) {
      const a = cs[i], b = cs[j];
      const dx = Math.max(b.x - (a.x + a.w), a.x - (b.x + b.w));
      const dy = Math.max(b.y - (a.y + a.h), a.y - (b.y + b.h));
      const gap = Math.max(dx, dy, 0);
      if (gap > 1e-9) best = Math.min(best, gap);
    }
  }
  out[t.id] = best;
});
console.log(JSON.stringify(out));
""")
    for tid, gap in d.items():
        if gap is None:
            continue
        assert abs(gap - 0.008) < 0.002, \
            f"{tid} 的格间缝隙是 {gap:.4f}，应接近 0.008"


def test_single_cell_template_fills_canvas():
    """满版必须正好铺满，不能平白缩水"""
    d = _tpl_json("""
const t = TEMPLATES.find(x => x.id === 'full');
console.log(JSON.stringify(tplCells(t)));
""")
    assert len(d) == 1
    c = d[0]
    assert abs(c["x"]) < 1e-9 and abs(c["y"]) < 1e-9
    assert abs(c["w"] - 1) < 1e-9 and abs(c["h"] - 1) < 1e-9


def test_template_cell_counts():
    """每个模板的格数要和名字里写的一致（名字骗人是低级错）"""
    d = _tpl_json("""
const out = {};
TEMPLATES.forEach(t => { out[t.id] = tplCells(t).length; });
console.log(JSON.stringify(out));
""")
    expect = {"full": 1, "rows2": 2, "cols2": 2, "grid4": 4,
              "topWide": 3, "botWide": 3, "strip3": 3, "grid6": 6}
    for tid, n in expect.items():
        if tid in d:
            assert d[tid] == n, f"{tid} 应该有 {n} 格，实际 {d[tid]}"


def test_template_source_uses_one_table():
    """结构保证：一个坐标表 + 一个生成器，不要 N 段重复代码"""
    src = open(JS, encoding="utf-8").read()
    assert "const TEMPLATES = [" in src
    assert "function tplCells(" in src and "function tplSpans(" in src
    # 缩略图必须用同一份坐标，否则预览和结果会不一致
    i = src.index("function tplMini(")
    j = src.index("\nfunction ", i + 10)
    assert "tplCells(" in src[i:j], "缩略图要用 tplCells 的真实坐标"
