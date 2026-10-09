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
