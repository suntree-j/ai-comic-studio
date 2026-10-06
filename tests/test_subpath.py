# -*- coding: utf-8 -*-
"""子路径部署测试

背景：目标服务器 80 端口是唯一对外入口，已被 data-platform 占用
      （/data/ /airflow/ /grafana/ /metrics/ 等子路径）。
      所以本应用必须能挂在 /comic/ 这类子路径下。

本文件验证：
    · <base href> 注入正确
    · 静态资源用相对路径
    · app.js 会按 <base> 解析接口前缀
    · --base-path 参数真的生效（不被 uvicorn 重导入重置）
"""

from __future__ import annotations

import os
import re

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WB = os.path.join(ROOT, "apps", "workbench")
STATIC = os.path.join(WB, "static")


def test_index_injects_base_href(monkeypatch):
    import apps.api.server as srv
    monkeypatch.setattr(srv, "BASE_PATH", "/comic/")
    c = TestClient(srv.app)
    r = c.get("/")
    assert r.status_code == 200
    assert '<base href="/comic/">' in r.text


def test_index_root_base(monkeypatch):
    import apps.api.server as srv
    monkeypatch.setattr(srv, "BASE_PATH", "/")
    c = TestClient(srv.app)
    r = c.get("/")
    assert '<base href="/">' in r.text


def test_base_href_normalises_missing_slash(monkeypatch):
    """--base-path /comic（缺尾斜杠）也要能正确工作"""
    import apps.api.server as srv
    monkeypatch.setattr(srv, "BASE_PATH", "/comic")
    c = TestClient(srv.app)
    assert '<base href="/comic/">' in c.get("/").text


def test_config_endpoint_exposes_base_path(monkeypatch):
    import apps.api.server as srv
    monkeypatch.setattr(srv, "BASE_PATH", "/comic/")
    c = TestClient(srv.app)
    d = c.get("/api/config").json()
    assert d["base_path"] == "/comic/"
    assert "version" in d


def test_health_exposes_base_path(monkeypatch):
    import apps.api.server as srv
    monkeypatch.setattr(srv, "BASE_PATH", "/comic/")
    c = TestClient(srv.app)
    assert c.get("/api/health").json()["base_path"] == "/comic/"


def test_app_js_reads_base_for_api_prefix():
    """app.js 必须从 <base> 推导接口前缀，而不是硬编码根路径"""
    js = open(os.path.join(WB, "app.js"), encoding="utf-8").read()
    assert "querySelector('base')" in js, "app.js 没有读取 <base>"
    assert "function url(" in js, "app.js 缺少 url() 前缀拼接函数"
    # fetch 必须走 url()，不能直接 fetch(path)
    assert "fetch(url(path)" in js, "api() 没有通过 url() 拼前缀"
    # 页面图片同样要走 url()
    assert "img.src = url(" in js, "页面图片没有走 url()"


def test_main_passes_app_object_not_import_string():
    """★ 关键坑：uvicorn.run 传模块字符串会重新导入，把 BASE_PATH 重置回默认值"""
    src = open(os.path.join(ROOT, "apps", "api", "server.py"), encoding="utf-8").read()
    assert 'uvicorn.run(app,' in src, \
        "uvicorn.run 必须传 app 对象（传字符串会重新导入模块，--base-path 失效）"
    assert 'uvicorn.run("apps.api.server:app"' not in src


def test_cli_has_base_path_option():
    src = open(os.path.join(ROOT, "apps", "api", "server.py"), encoding="utf-8").read()
    assert '"--base-path"' in src


def test_static_mounted_at_root_of_app():
    """★ 部署约定：app 内部静态资源挂在 /static。
    配合 nginx 的 `proxy_pass http://127.0.0.1:PORT;`（不带尾斜杠，保留 /comic 前缀），
    浏览器按 <base href="/comic/"> 请求 /comic/static/app.js 才能命中。"""
    import apps.api.server as srv
    paths = [getattr(r, "path", "") for r in srv.app.routes]
    assert "/static" in paths, "静态资源必须挂在 /static"
    assert "/" in paths, "首页必须挂在 /"
