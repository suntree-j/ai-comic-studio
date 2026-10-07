# -*- coding: utf-8 -*-
"""演示手册与实现一致性测试

★ 为什么需要这个：
    演示手册最容易出的问题不是写得不清楚，而是**写的交互现场根本点不出来**、
    或者接口数量/文件路径对不上。
    照着念却点不出效果，比没有手册更尴尬。

    这里把 `docs/demo-script.md` 里的每条说法都拿去和真实代码核对。
"""

from __future__ import annotations

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WB = os.path.join(ROOT, "apps", "workbench")


@pytest.fixture(scope="module")
def doc() -> str:
    p = os.path.join(ROOT, "docs", "demo-script.md")
    if not os.path.exists(p):
        pytest.fail("演示手册不存在：docs/demo-script.md")
    return open(p, encoding="utf-8").read()


@pytest.fixture(scope="module")
def js() -> str:
    return open(os.path.join(WB, "app.js"), encoding="utf-8").read()


@pytest.fixture(scope="module")
def srv() -> str:
    return open(os.path.join(ROOT, "apps", "api", "server.py"),
                encoding="utf-8").read()


def _norm(p: str) -> str:
    """把路径占位符统一成 X（兼容 {page_no} 与 {rest:path}）"""
    p = re.sub(r"\{[^}]*\}", "X", p)
    p = p.split("?")[0].rstrip("/").replace("/...", "")
    return p.rstrip("/")


# ══════════════════════════════════════════════════════════════════
# 接口清单
# ══════════════════════════════════════════════════════════════════

def test_api_count_matches_doc(doc):
    from apps.api.server import app
    n = sum(len((getattr(r, "methods", None) or set()) - {"HEAD", "OPTIONS"})
            for r in app.routes if getattr(r, "path", "").startswith("/api"))
    m = re.search(r"一共\s*(\d+)\s*个", doc)
    assert m, "手册里没写接口总数"
    assert n == int(m.group(1)), f"手册说 {m.group(1)} 个，实际 {n} 个"


def test_api_paths_in_doc_exist(doc):
    from apps.api.server import app
    paths = {_norm(getattr(r, "path", "")) for r in app.routes
             if getattr(r, "path", "").startswith("/api")}
    bad = []
    for meth, path in re.findall(r"\|\s*(GET|POST|PATCH)\s*\|\s*`([^`]+)`", doc):
        n = _norm(path)
        if n not in paths and not any(p.startswith(n) for p in paths):
            bad.append(f"{meth} {path}")
    assert not bad, f"手册写了不存在的接口：{bad}"


# ══════════════════════════════════════════════════════════════════
# 演示步骤必须真的能点出来
# ══════════════════════════════════════════════════════════════════

def test_demo_action_drag_bubble(js):
    """演示动作①「拖气泡」必须真的存在"""
    assert "startDrag(e, 'box'" in js
    assert "kind === 'box' ? { box:" in js


def test_demo_action_drag_arrow(js):
    """演示动作②的箭头尾端拖拽"""
    assert "startDrag(e, 'tail'" in js
    assert ": { tail: { x, y } }" in js


def test_demo_action_speaker_resets_confirm(js):
    """演示动作③「换说话人 → 徽章变待确认」"""
    assert "u.confirmed = false" in js
    assert "'已确认' : '待确认'" in js


def test_demo_validate_button_works(js):
    """演示第 5 分钟点「校验 IR」按钮"""
    assert "function showValidation" in js
    assert "$('#btnValidate').onclick" in js


def test_demo_export_button_works(js):
    assert "$('#btnExport').onclick" in js


def test_demo_url_params_supported(js, doc):
    """手册教的 URL 定位参数必须真的支持"""
    if "?project=" in doc:
        assert "bootParams" in js and "want.project" in js
    if "?validate=1" in doc:
        assert "get('validate')" in js


# ══════════════════════════════════════════════════════════════════
# 手册里讲的设计决策必须能在代码里验证
# ══════════════════════════════════════════════════════════════════

def test_page_number_freezing_is_real(srv, doc):
    """手册说「API 里根本没有改页码的接口」—— 必须是真的"""
    assert "页码冻结" in doc or "页码只读" in doc
    for pat in ('patch("/api/projects/{name}/layout"',
                'patch("/api/projects/{name}/pages',
                'post("/api/projects/{name}/pages'):
        assert pat not in srv, f"出现了改页码的接口：{pat}"


def test_edit_endpoints_only_write_json(srv):
    """手册说「编辑只改 JSON，不碰图片」"""
    assert "save_project(project)" in srv
    assert "invalidate(name)" in srv


def test_frontend_does_not_reimplement_layout(js):
    """手册说「前端只发坐标，不实现排版」"""
    assert "function refreshPageImage" in js      # 重新拉服务端渲染好的图
    # 不应在前端画气泡本身（只画编辑框）
    assert "drawImage" not in js
    assert "canvas.getContext" not in js


# ══════════════════════════════════════════════════════════════════
# 手册引用的实物都要存在
# ══════════════════════════════════════════════════════════════════

def test_doc_referenced_files_exist(doc):
    for rel in set(re.findall(
            r"`((?:docs|scripts|examples|apps|packages)/[\w./-]+)`", doc)):
        if rel.endswith("/"):
            continue
        assert os.path.exists(os.path.join(ROOT, rel.replace("/", os.sep))), \
            f"演示手册引用了不存在的文件：{rel}"


def test_workbench_files_exist():
    for f in ("app.js", "index.html", "style.css"):
        assert os.path.exists(os.path.join(WB, f))


def test_doc_numbers_are_accurate(doc):
    """手册里的数字必须对得上"""
    # 前端行数
    total = sum(len(open(os.path.join(WB, f), encoding="utf-8").read().splitlines())
                for f in ("app.js", "index.html", "style.css"))
    m = re.search(r"(\d+)\s*行（`app\.js`", doc)
    if m:
        assert int(m.group(1)) == total, \
            f"手册说前端 {m.group(1)} 行，实际 {total} 行"


def test_readme_mentions_demo_script():
    rd = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert "demo-script" in rd, "README 没有指向演示手册"
