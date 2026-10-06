# -*- coding: utf-8 -*-
"""字体与中文渲染测试

★ 为什么需要这个：
    部署到服务器后发现气泡里的中文全变**方块**。
    原因：服务器只有 DejaVu 字体，而旧实现找不到中文字体时会
    静默回落到 `ImageFont.load_default()`（不含 CJK 字形）。
    接口照样返回 200、图片照样生成 —— 只有肉眼看图才发现。

    这类「静默降级」必须靠测试挡住。
"""

from __future__ import annotations

import os

import pytest
from PIL import Image, ImageDraw

from packages.render.page import (
    FONT_CANDIDATES, FONT_GLOBS, _CJK_PROBE, _has_cjk, find_cjk_font,
    load_font, render_title_page,
)


# ══════════════════════════════════════════════════════════════════
# 字体查找
# ══════════════════════════════════════════════════════════════════

def test_finds_a_cjk_font():
    """运行环境必须能找到一个含中文字形的字体，否则渲染出来是方块"""
    p = find_cjk_font()
    assert p is not None, (
        "找不到含中文字形的字体 —— 气泡里的中文会变成方块。\n"
        "  Linux: apt-get install -y fonts-noto-cjk（或 fonts-wqy-microhei）\n"
        "  或设置环境变量 COMIC_FONT=/path/to/font.ttf"
    )
    assert os.path.exists(p)


def test_found_font_really_has_cjk_glyphs():
    """找到的字体必须真的能画出中文字（不能是豆腐块）"""
    p = find_cjk_font()
    assert p and _has_cjk(p)


def test_env_var_override(monkeypatch):
    p = find_cjk_font()
    if not p:
        pytest.skip("本机没有中文字体")
    monkeypatch.setenv("COMIC_FONT", p)
    assert find_cjk_font() == p


def test_env_var_ignored_when_missing(monkeypatch):
    monkeypatch.setenv("COMIC_FONT", "/nonexistent/font.ttf")
    # 不应抛异常，应回落到自动查找
    assert find_cjk_font() is not None


def test_font_candidates_cover_common_distros():
    """候选路径要覆盖主流发行版，避免又要手工排障"""
    joined = " ".join(FONT_CANDIDATES) + " ".join(FONT_GLOBS)
    for hint in ("msyh", "NotoSansCJK", "wqy", "uming"):
        assert hint in joined, f"候选里缺少 {hint}"


def test_has_cjk_rejects_latin_only_font():
    """★ 纯拉丁字体必须被判为「不含中文」

    这是踩坑后的回归测试：曾经用 `getbbox()` 判断，
    而实测发现 arial/consola/tahoma 对缺失字形**也返回非空 bbox**，
    于是它们全被判为「含中文」→ 线上用 arial 画中文 → 一排方块。
    """
    candidates = [
        (r"C:\Windows\Fonts\arial.ttf", "arial"),
        (r"C:\Windows\Fonts\consola.ttf", "consola"),
        (r"C:\Windows\Fonts\tahoma.ttf", "tahoma"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "DejaVuSerif"),
    ]
    tested = 0
    for path, name in candidates:
        if not os.path.exists(path):
            continue
        tested += 1
        assert not _has_cjk(path), f"{name} 是纯拉丁字体，不该被判为含中文"
    if not tested:
        pytest.skip("本机没有纯拉丁字体可测")


def test_has_cjk_accepts_real_cjk_fonts():
    """真实中文字体必须被判为「含中文」"""
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    tested = 0
    for path in candidates:
        if not os.path.exists(path):
            continue
        tested += 1
        assert _has_cjk(path), f"{path} 是中文，不该被判为不含中文"
    if not tested:
        pytest.skip("本机没有中文字体可测")


def test_has_cjk_does_not_crash_on_non_font(tmp_path):
    """给个不是字体的文件也不能崩"""
    p = tmp_path / "notafont.ttf"
    p.write_bytes(b"this is not a font")
    assert _has_cjk(str(p)) is False


def test_has_cjk_never_swallows_pillow_api_changes(monkeypatch):
    """★ 回归：曾因 `mask.tobytes()` 在 Pillow 12.3 被移除，
    异常被吞 → 所有字体都被判为不含中文 → 线上全用方块字体。

    现在判定只依赖 fontTools 的 cmap；即使字体加载相关 API 全废，
    也不该「把所有字体都判为 False」而静默降级。
    """
    p = find_cjk_font()
    if not p:
        pytest.skip("本机没有中文字体")
    # 屏蔽 Pillow 的字体加载，模拟 API 变更
    import PIL.ImageFont as IF
    monkeypatch.setattr(IF, "truetype",
                        lambda *a, **k: (_ for _ in ()).throw(AttributeError("broke")))
    # 判定仍应正确（因为走的是 fontTools cmap）
    assert _has_cjk(p) is True


# ══════════════════════════════════════════════════════════════════
# 渲染结果里中文真的画出来了
# ══════════════════════════════════════════════════════════════════

def _ink_ratio(img: Image.Image) -> float:
    """画面里非背景像素的比例"""
    import numpy as np
    a = np.asarray(img.convert("L"))
    return float((a < 200).mean())


def test_load_font_renders_cjk_with_ink():
    """用 load_font 画中文，必须有真实笔画（不是空白也不是实心块）"""
    f = load_font(64)
    img = Image.new("L", (200, 100), 255)
    ImageDraw.Draw(img).text((10, 10), "雪花穆宁冰", font=f, fill=0)
    import numpy as np
    a = np.asarray(img)
    ink = (a < 128).mean()
    assert ink > 0.01, "中文没画出来（全白）"
    assert ink < 0.6, "画成了实心块（多半是豆腐块）"


def test_title_page_has_chinese_ink():
    """标题页含中文，渲染后必须有笔画"""
    img = render_title_page("测试标题", "副标题")
    assert _ink_ratio(img) > 0.01


def test_load_font_is_cached():
    """同字号应复用同一个字体对象（避免每格重复加载）"""
    a = load_font(40)
    b = load_font(40)
    assert a is b


def test_warns_loudly_when_no_cjk_font(monkeypatch):
    """★ 找不到中文字体时必须发出警告，不能静默出方块"""
    import packages.render.page as pg
    monkeypatch.setattr(pg, "find_cjk_font", lambda: None)
    monkeypatch.setattr(pg, "_font_warned", [False])
    pg._FONT_CACHE.clear()
    with pytest.warns(RuntimeWarning, match="方块"):
        pg.load_font(32)
