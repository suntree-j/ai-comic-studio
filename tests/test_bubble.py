# -*- coding: utf-8 -*-
"""气泡布局算法测试

用**合成图**做可验证断言（不依赖真实素材）：
    · 左半边有人（深色块）→ 人物检测应判为 left，气泡应落右侧
    · 右半边有人 → 反之
    · 同格 6 个气泡 → 竖排且互不重叠
    · 同格 2 个不同说话人 → 分居两侧
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from packages.render.bubble import (
    BubbleRequest,
    choose_spot,
    detect_person_side,
    energy_map,
    layout_bubbles,
)


# ══════════════════════════════════════════════════════════════════
# 合成测试图
# ══════════════════════════════════════════════════════════════════

def snow_bg(w: int = 400, h: int = 280) -> Image.Image:
    """雪白背景（模拟冰系场景）"""
    img = Image.new("RGB", (w, h), (238, 242, 248))
    dr = ImageDraw.Draw(img)
    for i in range(0, min(w, h), 6):
        dr.line([(0, i), (w, i + 4)], fill=(226, 232, 240))
    return img


def person(img: Image.Image, side: str, color=(40, 45, 60)) -> Image.Image:
    """在左/右半边画一个「人物」（深色块 + 肤色头 + 红围巾）"""
    w, h = img.size
    dr = ImageDraw.Draw(img)
    cx = int(w * (0.25 if side == "left" else 0.75))
    # 躯干
    dr.rectangle([cx - 26, h // 3, cx + 26, h - 10], fill=color)
    # 头（肤色）
    dr.ellipse([cx - 18, h // 3 - 40, cx + 18, h // 3], fill=(232, 196, 174))
    # 红围巾
    dr.rectangle([cx - 22, h // 3, cx + 22, h // 3 + 14], fill=(198, 42, 48))
    return img


# ══════════════════════════════════════════════════════════════════
# 人物左右检测
# ══════════════════════════════════════════════════════════════════

def test_detect_person_left():
    img = person(snow_bg(), "left")
    assert detect_person_side(img) == "left"


def test_detect_person_right():
    img = person(snow_bg(), "right")
    assert detect_person_side(img) == "right"


def test_detect_empty_returns_none():
    """纯雪景没有人 → 判不出来，返回 None（调用方自行决定）"""
    assert detect_person_side(snow_bg()) is None


def test_detect_two_people_returns_a_side():
    """两人分居两侧时不该崩，返回任一侧即可"""
    img = person(person(snow_bg(), "left"), "right")
    assert detect_person_side(img) in ("left", "right", None)


# ══════════════════════════════════════════════════════════════════
# 能量图
# ══════════════════════════════════════════════════════════════════

def test_energy_map_shape_and_range():
    e = energy_map(snow_bg())
    assert e.ndim == 2
    assert e.min() >= 0.0
    assert e.max() <= 1.0 + 1e-6


def test_energy_higher_on_person():
    """人物区域的能量应明显高于纯背景区"""
    img = person(snow_bg(), "left")
    e = energy_map(img, tw=120)
    h, w = e.shape
    left = float(e[:, :w // 2].mean())
    right = float(e[:, w // 2:].mean())
    assert left > right


# ══════════════════════════════════════════════════════════════════
# 单选位置
# ══════════════════════════════════════════════════════════════════

def test_choose_spot_avoids_person():
    """人在左边 → 气泡应落在右半区"""
    img = person(snow_bg(), "left")
    x, y = choose_spot(img, bw=0.36, bh=0.22, prefer_opposite_of="left")
    assert x + 0.36 / 2 > 0.5, f"气泡中心应在右半区，实际 x={x:.3f}"


def test_choose_spot_other_side():
    img = person(snow_bg(), "right")
    x, y = choose_spot(img, bw=0.36, bh=0.22, prefer_opposite_of="right")
    assert x + 0.36 / 2 < 0.5, f"气泡中心应在左半区，实际 x={x:.3f}"


def test_choose_spot_respects_must_side():
    img = person(snow_bg(), "left")
    x, _ = choose_spot(img, 0.30, 0.20, must_side="right")
    assert x >= 0.5 - 1e-6
    x2, _ = choose_spot(img, 0.30, 0.20, must_side="left")
    assert x2 + 0.30 <= 0.5 + 1e-6


def test_choose_spot_in_bounds():
    img = snow_bg()
    for bw, bh in ((0.30, 0.20), (0.50, 0.40), (0.20, 0.30)):
        x, y = choose_spot(img, bw, bh)
        assert 0.0 <= x <= 1.0 - bw + 1e-6
        assert 0.0 <= y <= 1.0 - bh + 1e-6


def test_choose_spot_is_deterministic():
    """同输入必同输出（渲染是纯函数）"""
    img = person(snow_bg(), "left")
    a = choose_spot(img, 0.35, 0.22, prefer_opposite_of="left")
    b = choose_spot(img, 0.35, 0.22, prefer_opposite_of="left")
    assert a == b


# ══════════════════════════════════════════════════════════════════
# 多气泡整体布局
# ══════════════════════════════════════════════════════════════════

def _overlap(a, b) -> float:
    """a, b 均为 ((x, y), (w, h))"""
    (ax, ay), (aw, ah) = a
    (bx, by), (bw, bh) = b
    ox = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    oy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    return ox * oy


def _box(pos, w, h):
    return (pos, (w, h))


def test_single_bubble():
    img = person(snow_bg(), "left")
    out = layout_bubbles(img, [BubbleRequest(who_side="left", w=0.36, h=0.22)])
    assert len(out) == 1
    x, y = out[0]
    assert 0 <= x <= 1 and 0 <= y <= 1


def test_two_speakers_split_sides():
    """两个不同说话人 → 分居左右"""
    img = person(person(snow_bg(), "left"), "right")
    items = [
        BubbleRequest(who_side="left", w=0.34, h=0.20),
        BubbleRequest(who_side="right", w=0.34, h=0.20),
    ]
    out = layout_bubbles(img, items)
    assert len(out) == 2
    (x1, _), (x2, _) = out
    c1 = x1 + 0.34 / 2
    c2 = x2 + 0.34 / 2
    assert abs(c1 - c2) > 0.3, f"两个气泡应分居两侧，中心距仅 {abs(c1 - c2):.3f}"


def test_two_bubbles_do_not_overlap():
    img = snow_bg()
    items = [
        BubbleRequest(who_side="left", w=0.34, h=0.20),
        BubbleRequest(who_side="right", w=0.34, h=0.20),
    ]
    out = layout_bubbles(img, items)
    (x1, y1), (x2, y2) = out
    ov = _overlap(_box((x1, y1), 0.34, 0.20), _box((x2, y2), 0.34, 0.20))
    assert ov < 0.004, f"气泡重叠过多：{ov:.5f}"


def test_six_bubbles_stack_without_overlap():
    """≥5 个气泡 → 固定竖排，两两不重叠"""
    img = snow_bg()
    W, H = 0.34, 0.11
    items = [BubbleRequest(who_side="left", w=W, h=H) for _ in range(6)]
    out = layout_bubbles(img, items)
    assert len(out) == 6
    for i in range(len(out)):
        for j in range(i + 1, len(out)):
            ov = _overlap(_box(out[i], W, H), _box(out[j], W, H))
            assert ov < 0.004, f"第{i}与第{j}个气泡重叠 {ov:.5f}"


def test_layout_empty():
    assert layout_bubbles(snow_bg(), []) == []


def test_all_bubbles_in_bounds():
    img = snow_bg()
    items = [BubbleRequest(who_side="left", w=0.3, h=0.14) for _ in range(6)]
    for (x, y) in layout_bubbles(img, items):
        assert 0.0 <= x <= 1.0 - 0.3 + 1e-6
        assert 0.0 <= y <= 1.0 - 0.14 + 1e-6
