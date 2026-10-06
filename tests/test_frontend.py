# -*- coding: utf-8 -*-
"""前端静态资源测试

⚠️ 为什么需要这个文件：
    曾经因为 app.js 少了一个括号导致**整个脚本不执行**，
    页面显示「请选择一个项目」的空状态 —— 而所有 Python 测试仍然全绿。
    浏览器不报错到服务端，这类问题只能靠静态检查抓。

本文件用 `node --check` 做语法校验，并检查前后端接口对齐。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WB = os.path.join(ROOT, "apps", "workbench")
STATIC = os.path.join(WB, "static")

NODE = shutil.which("node")

# ══════════════════════════════════════════════════════════════════
# 语法
# ══════════════════════════════════════════════════════════════════

@pytest.mark.skipif(NODE is None, reason="需要 node 才能做 JS 语法检查")
def test_app_js_syntax():
    """app.js 必须语法合法 —— 否则整个工作台是白板"""
    try:
        r = subprocess.run([NODE, "--check", os.path.join(WB, "app.js")],
                           capture_output=True, text=True, timeout=60)
    except Exception as e:                                  # noqa: BLE001
        pytest.fail(f"node --check 执行失败：{e}")
    assert r.returncode == 0, (
        "app.js 语法错误（浏览器不会把这类错误报给服务端，"
        f"只会导致整个工作台白板）：\n{(r.stderr or '')[:1200]}"
    )


def test_app_js_is_not_empty():
    p = os.path.join(WB, "app.js")
    src = open(p, encoding="utf-8").read()
    assert len(src) > 3000
    # 关键功能必须存在
    for kw in ("drawBoxes", "showValidation", "showPage", "openProject",
               "startDrag", "renderUtts"):
        assert kw in src, f"app.js 缺少 {kw}"


def test_html_references_existing_assets():
    """静态资源引用必须用相对路径，并能在部署链路上解析到真实文件

    ★ 完整路径推导（也是 docs/deploy.md 里的部署约定）：
        ① 浏览器请求  http://IP/comic/
        ② 服务端返回 HTML，注入 <base href="/comic/">
        ③ HTML 里写 "static/app.js"（相对路径）
           → 浏览器按 base 解析成  /comic/static/app.js
        ④ nginx  `location /comic/ { proxy_pass http://127.0.0.1:8500/; }`
           —— proxy_pass 结尾的 **/** 负责剥掉 /comic 前缀
           → 应用收到  /static/app.js
        ⑤ 应用  StaticFiles(directory="apps/workbench") 挂在 /static
           → 命中文件  apps/workbench/app.js
    """
    html = open(os.path.join(WB, "index.html"), encoding="utf-8").read()
    refs = re.findall(r'(?:src|href)="(static/[^"]+)"', html)
    assert refs, "index.html 没有引用任何静态资源"

    for rel in refs:
        # 应用侧收到 /static/xxx；StaticFiles 根是 apps/workbench
        actual = os.path.join(WB, *rel.split("/")[1:])
        assert os.path.exists(actual), \
            f"引用了不存在的资源：{rel}（按部署映射应落在 {actual}）"

    assert not re.search(r'(?:src|href)="/static/', html), \
        "静态资源不应使用 /static 绝对路径（挂子路径时会 404），请用相对路径"


def test_html_has_required_mount_points():
    """app.js 用 getElementById 找的元素必须在 HTML 里存在"""
    html = open(os.path.join(WB, "index.html"), encoding="utf-8").read()
    js = open(os.path.join(WB, "app.js"), encoding="utf-8").read()
    ids = set(re.findall(r"\$\('#([A-Za-z0-9_]+)'\)", js))
    missing = [i for i in ids if f'id="{i}"' not in html]
    assert not missing, f"app.js 引用了 HTML 里不存在的 id：{missing}"


# ══════════════════════════════════════════════════════════════════
# 前后端接口对齐
# ══════════════════════════════════════════════════════════════════

def test_js_api_paths_match_backend():
    """app.js 里调的接口路径，后端必须真的注册了"""
    from apps.api.server import app

    js = open(os.path.join(WB, "app.js"), encoding="utf-8").read()
    called = set()
    for m in re.finditer(r"""api\(\s*[`'"]([^`'"]+)""", js):
        p = m.group(1)
        p = p.split("?")[0].split("${")[0].rstrip("/")
        if p.startswith("/api"):
            called.add(p)

    routes = set()
    for r in app.routes:
        path = getattr(r, "path", "")
        if path.startswith("/api"):
            routes.add(re.sub(r"\{[^}]+\}", "X", path.rstrip("/")))

    def norm(p: str) -> str:
        # 把具体的项目名/页码换成占位符再比
        p = re.sub(r"/api/projects/[^/]+", "/api/projects/X", p)
        p = re.sub(r"/api/tasks/[^/]+", "/api/tasks/X", p)
        return p

    miss = [p for p in called if norm(p) not in routes]
    assert not miss, f"app.js 调了后端没有的接口：{miss}\n后端路由：{sorted(routes)}"


def test_backend_has_no_page_mutation_route():
    """★ 页码冻结：后端不得提供任何修改页码的接口"""
    from apps.api.server import app
    for r in app.routes:
        path = getattr(r, "path", "")
        methods = getattr(r, "methods", set()) or set()
        if not path.startswith("/api"):
            continue
        assert "layout" not in path.lower(), f"出现了 layout 接口：{path}"
        if path.endswith("/pages") or path.endswith("/page"):
            assert methods <= {"GET", "HEAD", "OPTIONS"}, \
                f"页码接口只允许读：{methods} {path}"


# ══════════════════════════════════════════════════════════════════
# 文档物料
# ══════════════════════════════════════════════════════════════════

def test_readme_images_exist():
    """README 里引用的图片必须真的存在（否则 GitHub 上是裂图）"""
    rd = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    refs = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', rd)
    assert refs, "README 里没有任何图片"
    for rel in refs:
        if rel.startswith(("http://", "https://")):
            continue
        p = os.path.join(ROOT, rel)
        assert os.path.exists(p), f"README 引用了不存在的图片：{rel}"


def test_docs_referenced_in_readme_exist():
    rd = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    for rel in re.findall(r'\]\((docs/[^)]+\.md)\)', rd):
        assert os.path.exists(os.path.join(ROOT, rel)), f"README 链接失效：{rel}"
