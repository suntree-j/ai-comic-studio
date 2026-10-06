# -*- coding: utf-8 -*-
"""页面合成器

把一页的若干单格图 + 对白气泡 + 技能标签 合成为一张成品页。

渲染是**纯函数**：同 IR + 同素材 → 同输出。
这带来三个好处：可复现、可缓存、可 Golden 测试。

页面规格（A4 300dpi）：
    单格统一宽度 2480 px
    分页目标高度 3450 px
    格间距 26 px
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from ..ir.models import (
    Bible,
    BubbleStyle,
    LayoutPage,
    PageType,
    Panel,
    Utterance,
)
from .bubble import (
    BubbleRequest,
    choose_spot,
    default_tail,
    detect_person_side,
    layout_bubbles,
)

# ══════════════════════════════════════════════════════════════════
# 规格常量
# ══════════════════════════════════════════════════════════════════

PANEL_WIDTH = 2480          # 单格渲染宽度（A4 300dpi 宽）
TARGET_PAGE_HEIGHT = 3450   # 分页目标高度
GAP = 26                    # 格间距
DPI = 300

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",    # 黑体
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]

# 技能属性 → 颜色
ELEMENT_COLORS: Dict[str, Tuple[int, int, int]] = {
    "冰系": (110, 190, 245),
    "绝冰": (165, 228, 252),
    "风系": (128, 216, 205),
    "岩系": (196, 164, 100),
    "超然力": (222, 200, 138),
    "禁界": (240, 246, 255),
    "火系": (232, 108, 56),
    "雷系": (168, 118, 232),
    "冰鸾": (150, 205, 240),
    "冰凤": (180, 215, 235),
}


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:                               # noqa: BLE001
                continue
    return ImageFont.load_default()                         # pragma: no cover


# ══════════════════════════════════════════════════════════════════
# 气泡绘制
# ══════════════════════════════════════════════════════════════════

def wrap_text(text: str, font, max_w: int) -> List[str]:
    """按像素宽度折行（中文逐字，英文按词）"""
    lines: List[str] = []
    for para in (text or "").split("\n"):
        cur = ""
        for ch in para:
            if font.getlength(cur + ch) <= max_w or not cur:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines or [""]


def measure_bubble(utterance: Utterance, panel_w: int, panel_h: int,
                   base_font: int = 46) -> Tuple[List[str], int, int, int]:
    """算出气泡的换行、宽、高、字号"""
    fs = utterance.bubble.font_size or base_font
    font = load_font(fs)
    max_w = int(panel_w * 0.36)
    lines = wrap_text(utterance.text, font, max_w - 56)
    w = int(max(font.getlength(ln) for ln in lines) + 62)
    w = max(w, 260)
    has_name = bool(utterance.who)
    h = int(fs * 1.34) * len(lines) + (int(fs * 1.2) if has_name else 0) + 34
    return lines, w, h, fs


def draw_bubble(
    canvas: Image.Image,
    box: Tuple[float, float, float, float],
    tail: Tuple[float, float],
    lines: List[str],
    fs: int,
    name: str = "",
    style: BubbleStyle = BubbleStyle.SPEECH,
) -> None:
    """在归一化坐标处画一个气泡 + 指向箭头"""
    W, H = canvas.size
    x = box[0] * W
    y = box[1] * H
    w = box[2] * W
    h = box[3] * H
    tx, ty = tail[0] * W, tail[1] * H

    dr = ImageDraw.Draw(canvas, "RGBA")
    font = load_font(fs)
    name_font = load_font(max(20, int(fs * 0.72)))

    # ① 指向箭头（先画，压在气泡下面）
    if style is not BubbleStyle.THINK:
        # 从气泡边缘取出发点
        cx, cy = x + w / 2, y + h / 2
        if abs(tx - cx) > abs(ty - cy):
            sx = x + w if tx > cx else x
            sy = cy
        else:
            sx = cx
            sy = y + h if ty > cy else y
        dr.line([(sx, sy), (tx, ty)], fill=(30, 34, 44, 255), width=5)
        # 箭头尖
        import math
        ang = math.atan2(ty - sy, tx - sx)
        L, SP = 22, 0.42
        dr.polygon([
            (tx, ty),
            (tx - L * math.cos(ang - SP), ty - L * math.sin(ang - SP)),
            (tx - L * math.cos(ang + SP), ty - L * math.sin(ang + SP)),
        ], fill=(30, 34, 44, 255))

    # ② 气泡主体
    radius = min(28, int(min(w, h) * 0.22))
    if style is BubbleStyle.NARRATION:
        dr.rectangle([x, y, x + w, y + h], fill=(252, 251, 246, 246),
                     outline=(60, 62, 70, 255), width=3)
    elif style is BubbleStyle.SHOUT:
        # 爆炸边
        import math as _m
        pts = []
        n = 24
        for i in range(n * 2):
            a = _m.pi * i / n
            rr = (0.5 + (0.10 if i % 2 else 0.0))
            pts.append((cx + _m.cos(a) * w / 2 * (1 + (0.12 if i % 2 else 0)),
                        cy + _m.sin(a) * h / 2 * (1 + (0.14 if i % 2 else 0))))
        dr.polygon(pts, fill=(255, 255, 255, 246), outline=(30, 34, 44, 255))
    else:
        dr.rounded_rectangle([x, y, x + w, y + h], radius=radius,
                             fill=(255, 255, 255, 246),
                             outline=(30, 34, 44, 255), width=4)

    # ③ 文字
    cy = y + 16
    if name:
        try:
            nw = name_font.getlength(name)
        except Exception:                                   # noqa: BLE001
            nw = len(name) * 20
        dr.text((x + (w - nw) / 2, cy), name, font=name_font,
                fill=(70, 120, 90, 255))
        cy += int(fs * 1.2)
    for ln in lines:
        try:
            lw = font.getlength(ln)
        except Exception:                                   # noqa: BLE001
            lw = len(ln) * fs * 0.5
        dr.text((x + (w - lw) / 2, cy), ln, font=font, fill=(22, 24, 30, 255))
        cy += int(fs * 1.34)


# ══════════════════════════════════════════════════════════════════
# 技能标签
# ══════════════════════════════════════════════════════════════════

def draw_skill_label(canvas: Image.Image, name: str, element: str,
                     pos: Tuple[float, float] = (0.06, 0.06),
                     size: int = 62, color: Optional[Tuple[int, int, int]] = None
                     ) -> None:
    """画技能名（只画名字，不加任何描述）"""
    W, H = canvas.size
    col = color or ELEMENT_COLORS.get(element, (180, 200, 220))
    font = load_font(size)
    x, y = pos[0] * W, pos[1] * H
    dr = ImageDraw.Draw(canvas, "RGBA")
    try:
        tw = font.getlength(name)
    except Exception:                                       # noqa: BLE001
        tw = len(name) * size
    pad = int(size * 0.34)
    # 发光底
    dr.rounded_rectangle([x - pad, y - pad // 2, x + tw + pad, y + size * 1.5],
                         radius=int(size * 0.28),
                         fill=(col[0], col[1], col[2], 66))
    # 描边字
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        dr.text((x + dx, y + dy), name, font=font, fill=(255, 255, 255, 235))
    dr.text((x, y), name, font=font, fill=(col[0] // 2, col[1] // 2, col[2] // 2, 255))


# ══════════════════════════════════════════════════════════════════
# 单格渲染
# ══════════════════════════════════════════════════════════════════

@dataclass
class RenderContext:
    """渲染一页所需的一切"""
    bible: Bible
    panels: Dict[str, Panel] = field(default_factory=dict)
    utterances: Dict[str, List[Utterance]] = field(default_factory=dict)
    panel_width: int = PANEL_WIDTH
    base_font: int = 46
    draw_page_number: bool = False
    page_no: int = 1
    total_pages: int = 1


def render_panel(
    ctx: RenderContext,
    panel: Panel,
    image: Image.Image,
) -> Image.Image:
    """把一个单格图渲染成带气泡与技能名的成品格"""
    W0, H0 = image.size
    h = int(H0 * ctx.panel_width / W0)
    canvas = image.convert("RGB").resize((ctx.panel_width, h), Image.LANCZOS)
    W, H = canvas.size

    # ① 技能标签
    if panel.skill and panel.skill in ctx.bible.skills:
        sk = ctx.bible.skills[panel.skill]
        draw_skill_label(canvas, sk.name, sk.element,
                         pos=(0.05, 0.06), size=62, color=tuple(sk.color) if sk.color else None)

    # ② 对白气泡
    utts = ctx.utterances.get(panel.id, [])
    if utts:
        _render_bubbles(ctx, canvas, panel, utts)

    return canvas


def _render_bubbles(ctx: RenderContext, canvas: Image.Image, panel: Panel,
                    utts: List[Utterance]) -> None:
    W, H = canvas.size

    # 预先量算，供布局使用
    measured = []
    for u in utts:
        lines, bw, bh, fs = measure_bubble(u, W, H, ctx.base_font)
        measured.append((u, lines, bw, bh, fs))

    # 需要自动布局的（未锁定、未指定 box）
    auto_reqs: List[BubbleRequest] = []
    auto_idx: List[int] = []
    for i, (u, lines, bw, bh, fs) in enumerate(measured):
        if u.locked and u.bubble.box is not None:
            continue
        speaker_side = None
        if u.who in (c.id for c in panel.cast):
            # 说话人在画面里：用检测取他在哪一侧
            speaker_side = detect_person_side(canvas)
        auto_reqs.append(BubbleRequest(who_side=speaker_side,
                                       w=bw / W, h=bh / H))
        auto_idx.append(i)

    spots = layout_bubbles(canvas, auto_reqs) if auto_reqs else []
    spot_iter = iter(spots)

    for i, (u, lines, bw, bh, fs) in enumerate(measured):
        if u.locked and u.bubble.box is not None:
            bx, by = u.bubble.box.x, u.bubble.box.y
        else:
            bx, by = next(spot_iter, (0.02, 0.03))
            if u.bubble.box is not None:
                bx, by = u.bubble.box.x, u.bubble.box.y

        box = (bx, by, bw / W, bh / H)

        # 箭头
        if u.bubble.tail is not None:
            tail = (u.bubble.tail.x, u.bubble.tail.y)
        else:
            speaker_side = detect_person_side(canvas) if u.who in (
                c.id for c in panel.cast) else None
            tail = default_tail(canvas, box, speaker_side)

        draw_bubble(canvas, box, tail, lines, fs,
                    name=_speaker_name(ctx.bible, u.who),
                    style=u.bubble.style)


def _speaker_name(bible: Bible, cid: str) -> str:
    """名字牌只有在该角色存在于设定集时显示"""
    ch = bible.characters.get(cid)
    return ch.name if ch else ""


# ══════════════════════════════════════════════════════════════════
# 整页渲染
# ══════════════════════════════════════════════════════════════════

def render_title_page(title: str, sub: str = "",
                      width: int = PANEL_WIDTH,
                      height: int = TARGET_PAGE_HEIGHT) -> Image.Image:
    """卷首/封面页"""
    img = Image.new("RGB", (width, height), (26, 32, 48))
    dr = ImageDraw.Draw(img)
    pad = int(width * 0.06)
    dr.rectangle([pad, pad, width - pad, height - pad],
                 outline=(198, 168, 96), width=3)

    f1 = load_font(int(width * 0.088))
    f2 = load_font(int(width * 0.030))
    try:
        w1 = f1.getlength(title)
    except Exception:                                       # noqa: BLE001
        w1 = len(title) * width * 0.09
    dr.text(((width - w1) / 2, height * 0.42), title, font=f1, fill=(238, 240, 245))
    if sub:
        try:
            w2 = f2.getlength(sub)
        except Exception:                                   # noqa: BLE001
            w2 = len(sub) * width * 0.03
        dr.text(((width - w2) / 2, height * 0.42 + int(width * 0.115)),
                sub, font=f2, fill=(160, 172, 190))
    return img


def render_page(
    ctx: RenderContext,
    page: LayoutPage,
    images: Dict[str, Image.Image],
) -> Image.Image:
    """渲染一页（title / panels / empty）"""
    W = ctx.panel_width

    if page.type is PageType.TITLE:
        # 标题页高度按宽度等比，保证与正文页同宽
        return render_title_page(page.title or "", page.sub or "",
                                 width=W, height=int(W * TARGET_PAGE_HEIGHT / PANEL_WIDTH))

    if page.type is PageType.EMPTY:
        # 删除但保留页码的占位页：留白
        return Image.new("RGB", (W, 300), (250, 250, 252))

    cells: List[Image.Image] = []
    for pid in page.panels:
        panel = ctx.panels.get(pid)
        img = images.get(pid)
        if panel is None or img is None:
            continue
        cells.append(render_panel(ctx, panel, img))

    if not cells:
        return Image.new("RGB", (W, 300), (250, 250, 252))

    total_h = sum(c.height for c in cells) + GAP * (len(cells) - 1)
    page_img = Image.new("RGB", (W, total_h), (255, 255, 255))
    y = 0
    for c in cells:
        page_img.paste(c, (0, y))
        y += c.height + GAP
    return page_img


# ══════════════════════════════════════════════════════════════════
# 自动分页
# ══════════════════════════════════════════════════════════════════

def paginate(panel_heights: Sequence[Tuple[str, int]],
             target: int = TARGET_PAGE_HEIGHT) -> List[List[str]]:
    """把镜头按高度切成页

    ★ 页码一经分配即冻结：本函数只负责「一次分配」，不负责后续重排。
    """
    pages: List[List[str]] = []
    buf: List[str] = []
    hh = 0
    for pid, h in panel_heights:
        if buf and hh + h > target:
            pages.append(buf)
            buf, hh = [], 0
        buf.append(pid)
        hh += h + GAP
    if buf:
        pages.append(buf)
    # 最后一页太空就并进上一页
    if len(pages) >= 2:
        last_h = sum(dict(panel_heights)[p] for p in pages[-1]) + GAP * (len(pages[-1]) - 1)
        if last_h < target * 0.4:
            pages[-2] = pages[-2] + pages[-1]
            pages.pop()
    return pages
