# -*- coding: utf-8 -*-
"""文档与代码一致性测试

★ 为什么需要这个：
    文档最容易骗人的地方是**示例代码里的函数名/类名/字段名和真实代码对不上**。
    照着 `docs/modifying.md` 抄一段代码却报 AttributeError，
    比没有文档更糟 —— 会让人以为项目坏了。

    这里把指南里引用的每个符号都实际 import 一遍核对。
"""

from __future__ import annotations

import inspect
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")


def read_doc(name: str) -> str:
    p = os.path.join(DOCS, name)
    if not os.path.exists(p):
        pytest.fail(f"文档不存在：docs/{name}")
    return open(p, encoding="utf-8").read()


# ══════════════════════════════════════════════════════════════════
# modifying.md 引用的符号必须真实存在
# ══════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def guide() -> str:
    return read_doc("modifying.md")


def test_guide_validator_symbols(guide):
    """指南教的「加一条校验规则」必须能照着写"""
    from packages.ir import validator as V
    assert isinstance(V.RULES, dict) and len(V.RULES) >= 12
    assert callable(V.validate)
    for field in ("rule", "severity", "where", "message", "hint"):
        assert f"{field}:" in open(
            os.path.join(ROOT, "packages", "ir", "validator.py"),
            encoding="utf-8").read(), f"ValidationError 缺字段 {field}"
    assert "IR-013" not in V.RULES, "IR-013 已被占用，指南里的示例编号需要改"


def test_guide_provider_symbols(guide):
    """指南教的「加一个生图服务」必须能照着写"""
    from packages.render import providers as P
    for name in ("ImageProvider", "ImageRequest", "ImageResult",
                 "ImageError", "QuotaExceeded", "IMAGE_PROVIDER_NAMES",
                 "get_image_provider", "_check_quota"):
        assert hasattr(P, name), f"providers.py 缺少 {name}（指南里引用了它）"
    assert hasattr(P.ImageProvider, "generate")
    assert "mock" in P.IMAGE_PROVIDER_NAMES


def test_guide_prompt_symbols(guide):
    from packages.render import prompt as PR
    assert callable(PR.build_prompt)
    assert hasattr(PR, "RULE_NO_TEXT")
    sig = inspect.signature(PR.build_prompt)
    assert list(sig.parameters)[:2] == ["bible", "panel"], \
        f"build_prompt 签名变了：{sig}（指南写的是 build_prompt(bible, panel)）"


def test_guide_render_symbols(guide):
    from packages.render import page as PG
    for name in ("RenderContext", "render_page", "render_title_page",
                 "load_font", "find_cjk_font"):
        assert hasattr(PG, name), f"page.py 缺少 {name}"


def test_guide_api_symbols(guide):
    from apps.api import server as S
    for name in ("load_project", "save_project", "invalidate",
                 "render_one_page", "project_path", "assets_dir"):
        assert hasattr(S, name), f"server.py 缺少 {name}"
    params = list(inspect.signature(S.invalidate).parameters)
    assert "page_no" in params, "invalidate 签名变了（指南写的是 invalidate(name, page_no)）"


def test_guide_referenced_scripts_exist(guide):
    for rel in sorted(set(re.findall(r"python (scripts/[\w_]+\.py)", guide))):
        assert os.path.exists(os.path.join(ROOT, rel.replace("/", os.sep))), \
            f"指南引用了不存在的脚本：{rel}"


def test_guide_referenced_paths_exist(guide):
    paths = set(re.findall(r"`((?:packages|apps|tests|projects)/[\w./]+)`", guide))
    for rel in sorted(paths):
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        # 允许通配（如 projects/<名字>/dialogue.json）
        if "<" in rel or rel.endswith("/"):
            continue
        assert os.path.exists(p), f"指南引用了不存在的路径：{rel}"


def test_guide_referenced_docs_exist(guide):
    for rel in sorted(set(re.findall(r"[`(](docs/[\w_]+\.md)", guide))):
        assert os.path.exists(os.path.join(ROOT, rel.replace("/", os.sep))), \
            f"指南引用了不存在的文档：{rel}"


# ══════════════════════════════════════════════════════════════════
# 红线必须在文档里写清楚
# ══════════════════════════════════════════════════════════════════

def test_guide_documents_the_red_lines(guide):
    """五条红线必须都在指南里 —— 它们是项目的核心约束"""
    red_lines = [
        ("页码", "不要重排页码"),
        ("对白", "不要把对白写进生图提示词"),
        ("说话人", "不要让程序猜说话人"),
        ("外观", "不要在提示词里改写角色外观"),
        ("字体", "不要让中文字体静默降级"),
    ]
    for kw, phrase in red_lines:
        assert phrase in guide, f"改动指南缺少红线：{phrase}"


def test_readme_links_the_guide():
    rd = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert "docs/modifying.md" in rd or "modifying.md" in rd, \
        "README 没有指向改动指南"


# ══════════════════════════════════════════════════════════════════
# 所有文档提到的脚本/文档互链都有效
# ══════════════════════════════════════════════════════════════════

def test_all_docs_have_no_broken_internal_links():
    broken = []
    for fn in os.listdir(DOCS):
        if not fn.endswith(".md"):
            continue
        src = open(os.path.join(DOCS, fn), encoding="utf-8").read()
        for rel in re.findall(r"\]\(([^)#]+\.md)\)", src):
            if rel.startswith(("http://", "https://")):
                continue
            target = os.path.normpath(os.path.join(DOCS, rel))
            if not os.path.exists(target):
                broken.append(f"{fn} → {rel}")
    assert not broken, "文档内部链接失效：\n  " + "\n  ".join(broken)


def test_docs_referenced_scripts_run_from_repo_root():
    """文档里 `python scripts/x.py` 的写法必须真能从仓库根目录跑起来

    只检查脚本存在 + 有 __main__ 入口（不实际执行，避免副作用）。
    """
    for fn in os.listdir(DOCS):
        if not fn.endswith(".md"):
            continue
        src = open(os.path.join(DOCS, fn), encoding="utf-8").read()
        for rel in sorted(set(re.findall(r"python (scripts/[\w_]+\.py)", src))):
            p = os.path.join(ROOT, rel.replace("/", os.sep))
            assert os.path.exists(p), f"{fn} 引用不存在的脚本 {rel}"
            body = open(p, encoding="utf-8").read()
            assert "__main__" in body, f"{rel} 没有 __main__ 入口，文档里跑的写法无效"
