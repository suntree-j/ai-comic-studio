# -*- coding: utf-8 -*-
"""导出：PDF / 长图

优先用 PyMuPDF（体积小、质量好）；没有则退回 Pillow 直接写 PDF。
"""

from __future__ import annotations

import io
import math
import os
from typing import List, Optional, Sequence

from PIL import Image

DPI = 300


# ══════════════════════════════════════════════════════════════════
# PDF
# ══════════════════════════════════════════════════════════════════

def save_pdf(images: Sequence[Image.Image], path: str) -> str:
    """把页面图写成 PDF（每张一页，页面高度自适应）"""
    if not images:
        raise ValueError("没有可导出的页面")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    images[0].save(path, "PDF", save_all=True, append_images=list(images[1:]),
                   resolution=float(DPI))
    return path


def save_pdf_pymupdf(images: Sequence[Image.Image], path: str) -> str:
    """用 PyMuPDF 写 PDF（体积更小，但页高需手动设 mediabox）"""
    try:
        import fitz
    except ImportError:
        return save_pdf(images, path)

    doc = fitz.open()
    for im in images:
        w_pt = im.width * 72.0 / DPI
        h_pt = im.height * 72.0 / DPI
        page = doc.new_page(width=w_pt, height=h_pt)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        page.insert_image(fitz.Rect(0, 0, w_pt, h_pt), stream=buf.getvalue())
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    doc.save(path, deflate=True, garbage=3)
    doc.close()
    return path


# ══════════════════════════════════════════════════════════════════
# 长图（把整本竖着拼成一张）
# ══════════════════════════════════════════════════════════════════

def save_long_image(images: Sequence[Image.Image], path: str,
                    width: int = 1000, gap: int = 12,
                    quality: int = 82) -> str:
    """拼成一张纵向长图（JPEG）

    Args:
        width:   输出宽度（超过会被多数聊天软件二次压缩，1000 较安全）
        quality: JPEG 质量
    """
    if not images:
        raise ValueError("没有可导出的页面")
    scaled: List[Image.Image] = []
    for im in images:
        nh = int(im.height * width / im.width)
        scaled.append(im.convert("RGB").resize((width, nh), Image.LANCZOS))

    total_h = sum(s.height for s in scaled) + gap * (len(scaled) - 1)
    canvas = Image.new("RGB", (width, total_h), (255, 255, 255))
    y = 0
    for s in scaled:
        canvas.paste(s, (0, y))
        y += s.height + gap

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    fmt = "JPEG" if path.lower().endswith((".jpg", ".jpeg")) else "PNG"
    if fmt == "JPEG":
        canvas.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
    else:
        canvas.save(path, "PNG", optimize=True)
    return path


def save_long_images(images: Sequence[Image.Image], outdir: str,
                     count: int = 9, width: int = 1000,
                     ext: str = "jpg", **kw) -> List[str]:
    """把整本切成 count 张长图（便于发送）

    分组尽量均匀；最后一组过薄则并入上一组。
    """
    if not images:
        raise ValueError("没有可导出的页面")
    os.makedirs(outdir, exist_ok=True)
    per = math.ceil(len(images) / count)
    groups: List[tuple] = []
    i = 0
    while i < len(images):
        groups.append((i, min(i + per, len(images))))
        i += per
    if len(groups) >= 2 and (groups[-1][1] - groups[-1][0]) < per * 0.4:
        _, b = groups.pop()
        groups[-1] = (groups[-1][0], b)

    paths: List[str] = []
    for gi, (s, e) in enumerate(groups, 1):
        p = os.path.join(outdir, f"长图_{gi}.{ext}")
        save_long_image(images[s:e], p, width=width, **kw)
        paths.append(p)
    return paths
