# -*- coding: utf-8 -*-
"""渲染层测试

覆盖：
    · 提示词组装（三段式完整性 + 硬规则注入）
    · 生图 Provider（Mock 离线）
    · 页面合成（气泡 + 技能名）
    · 导出（PDF / 长图）
    · 端到端：IR → PDF
"""

from __future__ import annotations

import json
import os

import pytest
from PIL import Image

from packages.render import (
    MockImageProvider,
    RenderStudio,
    build_negative,
    build_prompt,
    get_image_provider,
    paginate,
    render_title_page,
    save_long_image,
    save_long_images,
    save_pdf,
)
from packages.render.providers import ImageError, ImageRequest
from tests.fixtures import clone, make_project


# ══════════════════════════════════════════════════════════════════
# 提示词组装
# ══════════════════════════════════════════════════════════════════

def test_prompt_has_three_parts():
    """共享风格块 + 角色标准块 + 本格构图 + 禁止文字"""
    p = make_project()
    panel = p.storyboards[0].panels[0]
    prompt = build_prompt(p.bible, panel)

    # ① 共享风格块
    assert "全彩国漫风格" in prompt
    # ② 角色标准块（逐字）
    assert "冷调雪银白色的长发" in prompt
    assert "浅蓝色的长毛领大衣" in prompt     # 穆飞鸾
    # ③ 本格构图
    assert "穆宁雪" in prompt
    assert "冰晶刹弓" in prompt
    assert "距离" in prompt
    # ④ 禁止文字
    assert "绝对不要出现任何文字" in prompt


def test_prompt_injects_distance_rule_when_two_cast():
    p = make_project()
    panel = p.storyboards[0].panels[0]
    assert len(panel.cast) >= 2
    prompt = build_prompt(p.bible, panel)
    assert "两人之间必须留出明确的空间间隔" in prompt
    assert "两人相距约 8 米" in prompt


def test_prompt_injects_battle_form_rule_when_skill():
    p = make_project()
    panel = p.storyboards[0].panels[0]
    assert panel.skill
    prompt = build_prompt(p.bible, panel)
    assert "战斗形态" in prompt


def test_prompt_is_deterministic():
    p = make_project()
    panel = p.storyboards[0].panels[0]
    assert build_prompt(p.bible, panel) == build_prompt(p.bible, panel)


def test_prompt_includes_wound_state():
    """角色当前伤情必须进入提示词（跨格一致性）"""
    p = make_project()
    panel = p.storyboards[0].panels[0]
    prompt = build_prompt(p.bible, panel)
    assert "当前带伤" in prompt
    assert "霜白伤口" in prompt


def test_negative_mentions_no_text():
    p = make_project()
    neg = build_negative(p.storyboards[0].panels[0])
    assert "文字" in neg
    assert "两人贴近" in neg          # 两人同框时的额外负面词


# ══════════════════════════════════════════════════════════════════
# 生图 Provider
# ══════════════════════════════════════════════════════════════════

def test_mock_provider_produces_correct_size():
    prov = MockImageProvider()
    for size, expect in (("2048x1400", (2048, 1400)),
                         ("1332x1776", (1332, 1776)),
                         ("1536x1536", (1536, 1536))):
        r = prov.generate(ImageRequest(prompt="测试", size=size))
        assert r.ok
        assert r.image.size == expect


def test_mock_provider_is_deterministic():
    prov = MockImageProvider()
    req = ImageRequest(prompt="同一个提示词", size="1536x1536")
    a = prov.generate(req).image
    b = prov.generate(req).image
    assert a.tobytes() == b.tobytes()


def test_mock_provider_to_file(tmp_path):
    prov = MockImageProvider()
    out = str(tmp_path / "sub" / "p001.png")
    r = prov.generate_to_file(ImageRequest(prompt="x", size="1536x1536"), out)
    assert r.ok and os.path.exists(out)


def test_get_image_provider_names():
    assert get_image_provider("mock").name == "mock"
    assert get_image_provider("sd-webui").name == "sd-webui"
    with pytest.raises(ImageError):
        get_image_provider("not_a_vendor")


def test_seedream_requires_key(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    prov = get_image_provider("seedream", api_key="")
    with pytest.raises(ImageError):
        prov.generate(ImageRequest(prompt="x"))


# ══════════════════════════════════════════════════════════════════
# 分页
# ══════════════════════════════════════════════════════════════════

def test_paginate_packs_by_height():
    heights = [("a", 1000), ("b", 1000), ("c", 1000), ("d", 1000)]
    pages = paginate(heights, target=2500)
    # 每页装得下两张（1000+26+1000=2026 < 2500，三张=3052 > 2500）
    assert len(pages) == 2
    assert pages[0] == ["a", "b"]
    assert pages[1] == ["c", "d"]


def test_paginate_merges_thin_last_page():
    heights = [("a", 3000), ("b", 3000), ("c", 200)]
    pages = paginate(heights, target=3450)
    assert len(pages) == 2
    assert pages[1] == ["b", "c"]


# ══════════════════════════════════════════════════════════════════
# 页面合成
# ══════════════════════════════════════════════════════════════════

def test_render_title_page():
    img = render_title_page("测试标题", "副标题")
    assert img.width > 1000 and img.height > 1000
    # 深色底
    assert sum(img.getpixel((10, 10))) < 200


def test_compose_pages_from_mock_assets(tmp_path):
    """出图 → 合成，页面尺寸应为统一宽度"""
    project = make_project()
    prov = MockImageProvider()
    studio = RenderStudio(prov, panel_width=800)

    with_progress = []
    rep = studio.generate_panels(
        project.bible,
        [p for sb in project.storyboards for p in sb.panels],
        str(tmp_path),
        on_progress=lambda i, t, m: with_progress.append(m),
    )
    assert rep.generated == 2
    assert not rep.errors

    pages = studio.compose_pages(project, str(tmp_path))
    assert len(pages) == project.layout.total
    for pg in pages:
        assert pg.width == 800


def test_generate_panels_uses_cache(tmp_path):
    project = make_project()
    studio = RenderStudio(MockImageProvider(), panel_width=600)
    panels = [p for sb in project.storyboards for p in sb.panels]

    r1 = studio.generate_panels(project.bible, panels, str(tmp_path))
    assert r1.generated == 2 and r1.cached == 0

    r2 = studio.generate_panels(project.bible, panels, str(tmp_path))
    assert r2.generated == 0 and r2.cached == 2

    r3 = studio.generate_panels(project.bible, panels, str(tmp_path), force=True)
    assert r3.generated == 2


def test_bubbles_are_drawn(tmp_path):
    """有对白的格，合成后画面应出现白色气泡"""
    project = make_project()
    studio = RenderStudio(MockImageProvider(), panel_width=800)
    panels = [p for sb in project.storyboards for p in sb.panels]
    studio.generate_panels(project.bible, panels, str(tmp_path))

    pages = studio.compose_pages(project, str(tmp_path))
    # 第 2 页是 ch2436_P001（有两条对白）
    page = pages[1]
    import numpy as np
    a = np.asarray(page)
    # 气泡底色接近纯白
    whites = ((a[:, :, 0] > 244) & (a[:, :, 1] > 244) & (a[:, :, 2] > 244)).mean()
    assert whites > 0.01, f"没检测到气泡区域（白色占比 {whites:.4f}）"


# ══════════════════════════════════════════════════════════════════
# 导出
# ══════════════════════════════════════════════════════════════════

def test_save_pdf(tmp_path):
    pages = [render_title_page("A"), render_title_page("B")]
    out = str(tmp_path / "comic.pdf")
    save_pdf(pages, out)
    assert os.path.exists(out)
    assert os.path.getsize(out) > 1000
    try:
        import fitz
        assert len(fitz.open(out)) == 2
    except ImportError:
        pass


def test_save_pdf_empty_raises():
    with pytest.raises(ValueError):
        save_pdf([], "x.pdf")


def test_save_long_image(tmp_path):
    pages = [render_title_page("A"), render_title_page("B")]
    out = str(tmp_path / "long.jpg")
    save_long_image(pages, out, width=400)
    assert os.path.exists(out)
    im = Image.open(out)
    assert im.width == 400
    assert im.height > 400 * 1.5


def test_save_long_images_splits(tmp_path):
    pages = [render_title_page(f"P{i}") for i in range(18)]
    outdir = str(tmp_path / "longs")
    paths = save_long_images(pages, outdir, count=3, width=300)
    assert len(paths) == 3
    for p in paths:
        assert os.path.exists(p)


# ══════════════════════════════════════════════════════════════════
# 端到端：IR → PDF
# ══════════════════════════════════════════════════════════════════

def test_end_to_end_ir_to_pdf(tmp_path):
    """完整渲染：出图 → 合成 → 导出 PDF"""
    project = make_project()
    studio = RenderStudio(MockImageProvider(), panel_width=700)

    out_pdf = str(tmp_path / "out" / "comic.pdf")
    events = []
    rep = studio.render(
        project, str(tmp_path / "assets"), out_pdf,
        on_progress=lambda stage, i, t, m: events.append((stage, m)),
        long_images=2,
        long_outdir=str(tmp_path / "longs"),
    )

    assert rep.generated == 2
    assert rep.pages == project.layout.total
    assert os.path.exists(out_pdf)
    assert os.path.getsize(out_pdf) > 5000
    # 两个阶段都有进度回调
    assert {s for s, _ in events} == {"生成", "合成"}
    # 长图也生成了
    longs = os.listdir(str(tmp_path / "longs"))
    assert len(longs) == 2


def test_end_to_end_is_reproducible(tmp_path):
    """同 IR + 同 provider → 同输出（渲染是纯函数）"""
    import numpy as np

    project = make_project()

    def build(tag):
        s = RenderStudio(MockImageProvider(), panel_width=600)
        d = str(tmp_path / tag)
        s.generate_panels(project.bible,
                          [p for sb in project.storyboards for p in sb.panels], d)
        return s.compose_pages(project, d)

    a = build("a")
    b = build("b")
    assert len(a) == len(b)
    for pa, pb in zip(a, b):
        assert pa.size == pb.size
        assert np.array_equal(np.asarray(pa), np.asarray(pb))
