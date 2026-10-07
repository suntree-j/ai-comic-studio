# -*- coding: utf-8 -*-
"""编辑器渲染器

## 设计要点
    ① **纯函数**：同一份 JSON + 同一批素材 → 逐像素一致（可缓存、可测试）
    ② **归一化坐标**：元素位置用 0–1 比例，换导出尺寸不用重算
    ③ **服务端算高度**：气泡高度由文字排版决定，前端拿服务端返回的
       `boxes` 来画选择框 —— 前后端不会各算一套而错位
    ④ 复用 `packages.render.page` 的字体加载（含中文字体探测）

## 谁负责什么
    服务端：排版、绘制、算几何
    前端：只发坐标、只画选择框（不实现任何排版）
"""

from __future__ import annotations

import math
import os
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

from ..render.page import load_font, wrap_text
from .models import (
    BubbleElement, BubbleKind, Element, FitMode, ImageElement, Page,
    ShapeElement, TextElement,
)

DPI = 300
AssetLoader = Callable[[str], Optional[Image.Image]]


# ══════════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════════

def hex_to_rgb(s: str, default: Tuple[int, int, int] = (0, 0, 0)
               ) -> Tuple[int, int, int]:
    """#rrggbb → (r,g,b)；非法值返回默认色（不抛异常，避免一处配色错误毁掉整页）"""
    if not s:
        return default
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return default
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return default


def _wrap_cjk(text: str, font, max_w: float) -> List[str]:
    """按像素宽折行（中文逐字、英文按词，保留手动换行）"""
    lines: List[str] = []
    for para in (text or "").split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            if font.getlength(cur + ch) <= max_w or not cur:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines or [""]


def _image_box_h(el: ImageElement, img: Image.Image,
                 page_wh: Tuple[int, int]) -> float:
    """图片元素的包围盒高度（归一化，相对画布高）

    ★ 换算关系（容易写错，这里写清楚）：
        el.w 是相对**画布宽**的比例 → 实际宽 = el.w * page_w
        实际高 = 实际宽 * 原图高宽比
        归一化高 = 实际高 / page_h = el.w * (page_w / page_h) * (img.h / img.w)

      最初写成把 `el.w * W`（像素宽）直接当成比例高返回，
      结果前端画出的选择框比实际图片高出一大截。
    """
    if el.h:
        return el.h
    pw, ph = page_wh
    if not ph:
        return el.w
    if el.rotation:
        # 旋转后外接矩形会变大，给个保守估计
        return min(2.0, el.w * (pw / ph) * 1.4)
    return el.w * (pw / ph) * (img.height / max(1, img.width))


def bubble_geometry(el: BubbleElement, W: int, H: int
                    ) -> Tuple[float, float, float, float]:
    """算气泡的包围盒（像素）：(x, y, w, h)

    ★ 高度由文字决定，不手动指定 —— 和 Office 文本框一样自动长高。
      前端也拿这个结果画选择框，所以前后端不会各算一套。
    """
    x = el.x * W
    y = el.y * H
    w = max(40.0, el.w * W)
    fs = max(8, int(el.font_size * W / 1400)) or 8
    pad = max(4.0, el.pad * W)
    font = load_font(fs)
    sp_fs = max(8, int(fs * 0.72))

    # 说话人那一行
    head = 0.0
    if el.speaker:
        head = load_font(sp_fs).getbbox("口")[3] * 1.25

    tw = max(20.0, w - pad * 2)
    lines = _wrap_cjk(el.text, font, tw)
    line_h = fs * 1.32
    th = line_h * len(lines)
    h = head + th + pad * 2
    return x, y, w, h


def _draw_image_el(canvas: Image.Image, el: ImageElement,
                   img: Optional[Image.Image], W: int, H: int) -> None:
    if img is None:
        return
    x, y = el.x * W, el.y * H
    w = el.w * W
    h = (el.h * H) if el.h else (w * img.height / max(1, img.width))
    src = img.convert("RGB")

    if el.crop:
        l, t, r, b = el.crop
        sw, sh = src.size
        box = (int(l * sw), int(t * sh), int((1 - r) * sw), int((1 - b) * sh))
        if box[2] > box[0] and box[3] > box[1]:
            src = src.crop(box)

    tw, th = max(1, int(round(w))), max(1, int(round(h)))
    if el.fit is FitMode.FILL:
        piece = src.resize((tw, th), Image.LANCZOS)
    else:
        sw, sh = src.size
        scale = (max(tw / sw, th / sh) if el.fit is FitMode.COVER
                 else min(tw / sw, th / sh))
        nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
        scaled = src.resize((nw, nh), Image.LANCZOS)
        piece = Image.new("RGB", (tw, th), (255, 255, 255))
        piece.paste(scaled, ((tw - nw) // 2, (th - nh) // 2))

    if el.flip_h:
        piece = piece.transpose(Image.FLIP_LEFT_RIGHT)
    if el.opacity < 1.0:
        piece = Image.blend(Image.new("RGB", piece.size, (255, 255, 255)),
                            piece, el.opacity)
    if el.rotation:
        piece = piece.rotate(-el.rotation, expand=True, resample=Image.BICUBIC,
                             fillcolor=(255, 255, 255))

    px, py = int(round(W * el.x)), int(round(H * el.y))
    if el.shadow:
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(sh)
        d.rectangle([px + 8, py + 10, px + piece.width + 8,
                     py + piece.height + 10], fill=(0, 0, 0, 90))
        sh = sh.filter(ImageFilter.GaussianBlur(10))
        canvas.paste(Image.alpha_composite(
            canvas.convert("RGBA"), sh).convert("RGB"), (0, 0))

    canvas.paste(piece, (px, py))
    if el.border > 0:
        lw = max(1, int(el.border * W))
        ImageDraw.Draw(canvas).rectangle(
            [px, py, px + piece.width, py + piece.height],
            outline=(20, 20, 20), width=lw)


def _draw_bubble_el(canvas: Image.Image, el: BubbleElement,
                    W: int, H: int) -> Tuple[float, float, float, float]:
    """画一个气泡，返回它的包围盒（给前端画选择框用）"""
    x, y, w, h = bubble_geometry(el, W, H)
    fs = max(8, int(el.font_size * W / 1400)) or 8
    pad = max(4.0, el.pad * W)
    radius = max(0.0, el.radius * W)
    lw = max(1, int(el.line_width * W)) if el.line_width > 0 else 0
    fill = hex_to_rgb(el.fill, (255, 255, 255))
    line = hex_to_rgb(el.line, (27, 27, 27))
    dr = ImageDraw.Draw(canvas)
    box = [x, y, x + w, y + h]

    # ① 尾巴（先画，再被气泡本体压住根部）
    #
    # ★ 踩坑记录：最初把基点算在 `圆心 + 半径*0.92*方向`，当箭头尖端离气泡较远时
    #   这个偏移量会把基点推到画面外，画出一个巨大的白色三角，把整页挡住。
    #   正确做法：基点必须落在**气泡自身的边界**上（按方向取矩形/椭圆的交点），
    #   尾巴根部宽度也要限制在气泡尺寸的量级内。
    if el.tail:
        tx, ty = el.tail[0] * W, el.tail[1] * H
        if el.style is BubbleKind.THINK:
            _draw_think_tail(dr, box, (tx, ty), fill, line, lw, (W, H))
        else:
            _draw_tail(dr, box, (tx, ty), el.tail_w, fill, line, lw, (W, H))

    # ② 气泡本体（按样式）
    if el.style is BubbleKind.NARRATION:
        dr.rectangle(box, fill=fill, outline=line if lw else None, width=lw)
    elif el.style is BubbleKind.SHOUT:
        _draw_spiky(dr, box, fill, line, lw)
    elif el.style is BubbleKind.THINK:
        dr.rounded_rectangle(box, radius=radius * 1.6, fill=fill,
                             outline=line if lw else None, width=lw)
    else:
        dr.rounded_rectangle(box, radius=radius, fill=fill,
                             outline=line if lw else None, width=lw)
        if el.style is BubbleKind.WHISPER:
            # 低语：再套一圈虚线感（点划线）
            step = max(6, int(W * 0.008))
            for i in range(0, int(w), step * 2):
                dr.line([(x + i, y + h), (min(x + i + step, x + w), y + h)],
                        fill=line, width=max(1, lw))

    # ③ 文字
    cx = x + w / 2
    cy = y + pad
    if el.speaker:
        sp_fs = max(8, int(fs * 0.72))
        sp_font = load_font(sp_fs)
        sp_w = sp_font.getlength(el.speaker)
        sp_x = {"left": x + pad, "center": cx - sp_w / 2,
                "right": x + w - pad - sp_w}[el.align.value]
        dr.text((sp_x, cy), el.speaker, font=sp_font,
                fill=hex_to_rgb(el.speaker_color, (74, 74, 74)))
        cy += sp_font.getbbox("口")[3] * 1.25

    font = load_font(fs)
    for ln in _wrap_cjk(el.text, font, w - pad * 2):
        lw_px = font.getlength(ln)
        tx = {"left": x + pad, "center": cx - lw_px / 2,
              "right": x + w - pad - lw_px}[el.align.value]
        dr.text((tx, cy), ln, font=font, fill=hex_to_rgb(el.text_color, (17, 17, 17)))
        cy += fs * 1.32

    return x, y, w, h


def _edge_point(box: List[float], p: Tuple[float, float]) -> Tuple[float, float]:
    """从矩形中心朝 p 方向，与矩形边界的交点

    ★ 关键：基点必须落在气泡边界上。用「中心 + 半径×方向」的近似写法，
      在方向接近水平/垂直时会把基点推到很远处（甚至画面外），
      画出来就是一个巨大的三角 —— 这是实际踩过的坑。
    """
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = p[0] - cx, p[1] - cy
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return cx, cy
    hw, hh = (x1 - x0) / 2, (y1 - y0) / 2
    # 取「中心到边界」需要的缩放系数（取较小者 = 先碰到的边）
    sx = hw / abs(dx) if abs(dx) > 1e-6 else float("inf")
    sy = hh / abs(dy) if abs(dy) > 1e-6 else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def _draw_tail(dr: ImageDraw.ImageDraw, box: List[float],
               tip: Tuple[float, float], tail_w: float,
               fill: Tuple[int, int, int], line: Tuple[int, int, int],
               lw: int, canvas_wh: Tuple[int, int] = (1400, 2000)) -> None:
    """画指向尾巴（对话/喊叫气泡）

    ★ 两轮踩坑后的结论：
      ① 基点必须落在**气泡边界**上（用「中心+半径×方向」的近似写法，
         方向接近水平/垂直时基点会被推到画面外，画出巨大白三角）
      ② 长度上限要按**画布尺寸**算，不能按气泡尺寸 ——
         否则小气泡会拖出一根又长又细的针
      ③ 尖端保留 25% 根部宽度，看起来像漫画的锥形尾巴，而不是一根刺
    """
    x0, y0, x1, y1 = box
    bw_px, bh_px = x1 - x0, y1 - y0
    cw, ch = canvas_wh
    bx, by = _edge_point(box, tip)

    # 根部宽度：气泡短边的 10%–26%
    short = max(6.0, min(bw_px, bh_px))
    k = max(0.15, min(1.0, tail_w))
    bw = max(9.0, short * (0.10 + 0.16 * k))
    # 长度上限按画布：最多画布短边的 38%
    # （太小会让箭头够不到画面另一侧的人物；太大又会在拖远时变成一根长刺）
    max_len = min(cw, ch) * 0.38
    dx, dy = tip[0] - bx, tip[1] - by
    d = math.hypot(dx, dy)
    if d > max_len and d > 0:
        kk = max_len / d
        tx, ty = bx + dx * kk, by + dy * kk
    else:
        tx, ty = tip

    ang = math.atan2(ty - by, tx - bx)
    px, py = -math.sin(ang), math.cos(ang)
    tipw = bw * 0.25 / 2                      # 尖端保留 25% 宽度
    pts = [(bx + px * bw / 2, by + py * bw / 2),
           (tx + px * tipw, ty + py * tipw),
           (tx - px * tipw, ty - py * tipw),
           (bx - px * bw / 2, by - py * bw / 2)]
    dr.polygon(pts, fill=fill, outline=line if lw else None)
    if lw:
        dr.line(pts + [pts[0]], fill=line, width=lw, joint="curve")


def _draw_think_tail(dr: ImageDraw.ImageDraw, box: List[float],
                     tip: Tuple[float, float],
                     fill: Tuple[int, int, int], line: Tuple[int, int, int],
                     lw: int, canvas_wh: Tuple[int, int] = (1400, 2000)) -> None:
    """心声气泡的尾巴：一串由大到小的圆点"""
    x0, y0, x1, y1 = box
    cw, ch = canvas_wh
    bx, by = _edge_point(box, tip)
    short = max(8.0, min(x1 - x0, y1 - y0))
    dx, dy = tip[0] - bx, tip[1] - by
    d = math.hypot(dx, dy)
    if d < 4:
        return
    # 圆点沿方向排布，半径递减；总长上限与对话尾巴一致
    d = min(d, min(cw, ch) * 0.38)
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    radii = [short * 0.10, short * 0.068, short * 0.045]
    for i, r in enumerate(radii):
        t = (i + 1) / (len(radii) + 0.4)
        px_, py_ = bx + ux * d * t, by + uy * d * t
        rr = max(2.5, r)
        dr.ellipse([px_ - rr, py_ - rr, px_ + rr, py_ + rr],
                   fill=fill, outline=line if lw else None,
                   width=max(1, lw // 2))


def _draw_spiky(dr: ImageDraw.ImageDraw, box: List[float],
                fill: Tuple[int, int, int], line: Tuple[int, int, int],
                lw: int) -> None:
    """爆炸形（喊叫气泡）"""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    n = 22
    pts = []
    for i in range(n * 2):
        a = math.pi * 2 * i / (n * 2) - math.pi / 2
        k = 1.0 if i % 2 == 0 else 0.82
        pts.append((cx + math.cos(a) * rx * k, cy + math.sin(a) * ry * k))
    dr.polygon(pts, fill=fill, outline=line if lw else None)
    if lw:
        dr.line(pts + [pts[0]], fill=line, width=lw, joint="curve")


def _draw_text_el(canvas: Image.Image, el: TextElement,
                  W: int, H: int) -> Tuple[float, float, float, float]:
    fs = max(8, int(el.font_size * W / 1400)) or 8
    font = load_font(fs)
    w = el.w * W
    lines = _wrap_cjk(el.text, font, w)
    lh = fs * el.line_spacing
    h = lh * len(lines)
    x, y = el.x * W, el.y * H
    layer = Image.new("RGBA", (max(1, int(w) + 20), max(1, int(h) + 20)),
                      (0, 0, 0, 0))
    dr = ImageDraw.Draw(layer)
    color = hex_to_rgb(el.color, (17, 17, 17))
    oc = hex_to_rgb(el.outline_color, (255, 255, 255))
    ow = int(el.outline * W)
    for i, ln in enumerate(lines):
        lw_px = font.getlength(ln)
        tx = {"left": 10, "center": 10 + w / 2 - lw_px / 2,
              "right": 10 + w - lw_px}[el.align.value]
        ty = 10 + i * lh
        if ow > 0:
            for dx in range(-ow, ow + 1):
                for dy in range(-ow, ow + 1):
                    if dx * dx + dy * dy <= ow * ow:
                        dr.text((tx + dx, ty + dy), ln, font=font, fill=oc)
        dr.text((tx, ty), ln, font=font, fill=color)
    if el.rotation:
        layer = layer.rotate(-el.rotation, expand=True, resample=Image.BICUBIC)
    canvas.paste(layer, (int(x) - 10, int(y) - 10), layer)
    return x, y, w, h


def _draw_shape_el(canvas: Image.Image, el: ShapeElement,
                   W: int, H: int) -> Tuple[float, float, float, float]:
    x, y = el.x * W, el.y * H
    w, h = el.w * W, el.h * H
    dr = ImageDraw.Draw(canvas)
    fill = hex_to_rgb(el.fill, (255, 255, 255))
    lw = max(1, int(el.line_width * W)) if el.line_width > 0 else 0
    box = [x, y, x + w, y + h]
    if el.shape == "ellipse":
        dr.ellipse(box, fill=fill,
                   outline=hex_to_rgb(el.line) if lw else None, width=lw)
    elif el.radius > 0:
        dr.rounded_rectangle(box, radius=el.radius * W, fill=fill,
                             outline=hex_to_rgb(el.line) if lw else None,
                             width=lw)
    else:
        dr.rectangle(box, fill=fill,
                     outline=hex_to_rgb(el.line) if lw else None, width=lw)
    return x, y, w, h


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def render_page(page: Page, load_asset: AssetLoader,
                target_width: int = 1240,
                with_boxes: bool = False
                ) -> Tuple[Image.Image, Dict[str, List[float]]]:
    """渲染一页

    Args:
        page:          页面数据
        load_asset:    asset_id → PIL.Image（取不到返回 None）
        target_width:  输出宽度（高度按画布比例算）
        with_boxes:    是否返回每个元素的包围盒（前端画选择框用）

    Returns:
        (图片, {element_id: [x, y, w, h]}（归一化，相对画布）)
    """
    target_width = max(64, int(target_width))
    H = max(1, int(round(target_width * page.height / max(1, page.width))))
    canvas = Image.new("RGB", (target_width, H),
                       hex_to_rgb(page.background, (255, 255, 255)))
    W = target_width

    boxes: Dict[str, List[float]] = {}
    for el in page.sorted_elements():
        alpha = None
        if 0 < el.opacity < 1.0:
            alpha = canvas.copy()
        if isinstance(el, ImageElement):
            img = load_asset(el.asset_id)
            # ★ 素材取不到就跳过，**不要**记进 boxes ——
            #   否则前端会画出一个空的选择框，点上去什么都没有
            if img is None:
                continue
            _draw_image_el(canvas, el, img, W, H)
            boxes[el.id] = [el.x, el.y, el.w,
                            _image_box_h(el, img, (page.width, page.height))]
        elif isinstance(el, BubbleElement):
            x, y, w, h = _draw_bubble_el(canvas, el, W, H)
            boxes[el.id] = [el.x, el.y, w / W, h / H]
        elif isinstance(el, TextElement):
            x, y, w, h = _draw_text_el(canvas, el, W, H)
            boxes[el.id] = [el.x, el.y, w / W, h / H]
        elif isinstance(el, ShapeElement):
            x, y, w, h = _draw_shape_el(canvas, el, W, H)
            boxes[el.id] = [el.x, el.y, el.w, el.h]
        if alpha is not None:
            canvas = Image.blend(alpha, canvas, el.opacity)

    if not with_boxes:
        boxes = {}
    return canvas, boxes


def auto_place_bubble(page: Page, load_asset: AssetLoader,
                      W: int = 1240) -> Tuple[float, float]:
    """给新气泡找个不压人的位置

    复用 `packages.render.bubble` 的内容能量最小化 —— 它已经解决了
    「雪景里判不出人物在哪」这类问题，没必要重写。
    """
    from ..render.bubble import choose_spot

    base, _ = render_page(page, load_asset, target_width=W)
    bw = 0.34 * W
    bh = 0.18 * (W * page.height / page.width)
    try:
        x, y = choose_spot(base, bw, bh)
    except Exception:                                       # noqa: BLE001
        x, y = 0.06, 0.06
    return round(max(0.02, min(0.95, x / W)), 4), round(max(0.02, min(0.95, y)), 4)
