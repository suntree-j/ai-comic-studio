# -*- coding: utf-8 -*-
"""检查依赖声明是否与实际 import 一致

★ 为什么需要这个：
    部署到服务器时才发现 `packages/render/bubble.py` 用了 numpy，
    但 pyproject.toml 的 dependencies 里没写 ——
    本地因为 numpy 早就装好了，所以一直没暴露。
    这类问题只在干净环境部署时才炸，必须靠静态检查提前发现。
"""
from __future__ import annotations

import ast
import os
import sys
import tomllib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: import 名 → 发行包名（不一致的需要映射）
IMPORT_TO_DIST = {
    "PIL": "Pillow",
    "fitz": "pymupdf",
    "pymupdf": "pymupdf",
    "yaml": "PyYAML",
    "sklearn": "scikit-learn",
}

#: 标准库 / 本项目内部模块，不需要声明
INTERNAL = {"packages", "apps", "tests", "scripts", "examples"}
STDLIB = set(sys.stdlib_module_names)


def project_deps() -> set[str]:
    data = tomllib.load(open(os.path.join(ROOT, "pyproject.toml"), "rb"))
    deps = set()
    for spec in data["project"]["dependencies"]:
        deps.add(_dist_name(spec))
    for group in data["project"].get("optional-dependencies", {}).values():
        for spec in group:
            deps.add(_dist_name(spec))
    return deps


def _dist_name(spec: str) -> str:
    name = spec.split(";")[0].split("[")[0]
    for sep in (">=", "==", "<=", "~=", ">", "<", "!="):
        name = name.split(sep)[0]
    return name.strip()


def all_imports() -> dict[str, set[str]]:
    """返回 {顶层模块: {出现在哪些文件}}"""
    found: dict[str, set[str]] = {}
    for sub in ("packages", "apps"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, sub)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, ROOT).replace("\\", "/")
                try:
                    tree = ast.parse(open(fp, encoding="utf-8").read())
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for a in node.names:
                            found.setdefault(a.name.split(".")[0], set()).add(rel)
                    elif isinstance(node, ast.ImportFrom):
                        if node.level == 0 and node.module:
                            found.setdefault(node.module.split(".")[0], set()).add(rel)
    return found


def test_third_party_imports_are_declared():
    deps_lower = {d.lower() for d in project_deps()}
    missing = []
    for mod, files in sorted(all_imports().items()):
        if mod in STDLIB or mod in INTERNAL:
            continue
        dist = IMPORT_TO_DIST.get(mod, mod).lower()
        if dist not in deps_lower:
            missing.append(f"{mod}（用在 {', '.join(sorted(files))}）")
    assert not missing, (
        "以下第三方依赖在 pyproject.toml 里没有声明，干净环境安装后会 ImportError：\n  "
        + "\n  ".join(missing)
    )


def test_optional_extras_cover_their_modules():
    """web 相关的 import 必须能靠 extras 装上"""
    data = tomllib.load(open(os.path.join(ROOT, "pyproject.toml"), "rb"))
    extras = data["project"].get("optional-dependencies", {})
    assert "web" in extras, "缺少 web extra（fastapi/uvicorn）"
    web = " ".join(extras["web"]).lower()
    for need in ("fastapi", "uvicorn"):
        assert need in web, f"web extra 缺少 {need}"
    assert "pdf" in extras and "pymupdf" in " ".join(extras["pdf"]).lower()


def test_package_discovery_includes_render():
    """packages.render 必须能被发现 —— 手写列表曾经漏掉它"""
    data = tomllib.load(open(os.path.join(ROOT, "pyproject.toml"), "rb"))
    st = data.get("tool", {}).get("setuptools", {})
    pkgs = st.get("packages")

    if isinstance(pkgs, list):
        # 手写列表：必须把每个子包都列全
        listed = set(pkgs)
        for sub in ("packages", "packages.ir", "packages.agent",
                    "packages.render"):
            assert sub in listed, f"手写 packages 列表缺少 {sub}（建议改用 find）"
    else:
        # 自动发现：确认 include 覆盖 packages 与 apps
        assert isinstance(pkgs, dict) and "find" in pkgs, \
            "既没有 packages 列表也没有 packages.find"
        inc = pkgs["find"]["include"]
        assert any("packages" in p for p in inc), f"find.include 未覆盖 packages：{inc}"
        assert any("apps" in p for p in inc), f"find.include 未覆盖 apps：{inc}"

    # 无论哪种方式，packages/render 都得真的存在且是可导入包
    assert os.path.exists(os.path.join(ROOT, "packages", "render", "__init__.py"))
    assert os.path.exists(os.path.join(ROOT, "apps", "api", "__init__.py"))
