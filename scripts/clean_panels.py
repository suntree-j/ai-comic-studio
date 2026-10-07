# -*- coding: utf-8 -*-
"""从成品漫画页里提取「干净画面」—— 自动检测并擦除已有的对白气泡

## 为什么需要
    成品页**本来就有对白气泡**。直接拿来当演示素材再叠一层自己的气泡，
    会变成「文字叠文字」，一眼假。

## 方法
    ① 成品对白气泡的特征是：**近纯白的填充 + 深色描边**，且成块状
    ② 用 scipy 做连通域分析找出这些块
    ③ 逐个判定是不是气泡（面积、圆度、描边占比）
    ④ 擦掉：用膨胀环的中位色填充（取周边画面颜色，不是纯白），
       再羽化边缘 —— 这样残留痕迹比直接盖白块小得多

## 局限（诚实说明）
    · 拟声词、美术字标题、不是白色填充的旁白框**检测不到**
    · 大面积气泡擦除后会留下明显色块，所以擦除超阈值就**放弃这一页**
    所以脚本会对每页给出「可用 / 需裁剪 / 放弃」的判定，而不是硬来。
"""
from __future__ import annotations

import io
import os
import sys
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageFilter


def find_bubbles(img: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """找出疑似对白气泡的矩形区域 (x, y, w, h)"""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    # ① 近纯白区域（气泡填充通常是纯白）
    white = (gray > 235).astype(np.uint8) * 255

    # ② 闭运算连成块，同时把气泡内部的文字也并进来
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    closed = cv2.morphologyEx(white, cv2.MORPH_CLOSE, k, iterations=2)

    # ③ 深色描边掩码
    dark = (gray < 110).astype(np.uint8)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    out: List[Tuple[int, int, int, int]] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw < w * 0.03 or bh < h * 0.02:        # 太小
            continue
        if bw > w * 0.92 and bh > h * 0.92:       # 整页背景，不是气泡
            continue
        if area < 400:
            continue
        fill = area / float(bw * bh)               # 填充率：气泡接近 1
        if fill < 0.45:
            continue
        # ④ 边界上应有深色描边（沿外接矩形一圈取像素）
        pad = 3
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
        ring = np.zeros_like(dark)
        ring[y0:y1, x0:x1] = 1
        ring[y + 1:y + bh - 1, x + 1:x + bw - 1] = 0
        ring_px = ring.astype(bool)
        if ring_px.sum() == 0:
            continue
        dark_ratio = dark[ring_px].mean()
        if dark_ratio < 0.04:
            continue
        out.append((int(x), int(y), int(bw), int(bh)))
    return out


def erase(img: np.ndarray, boxes: List[Tuple[int, int, int, int]],
          feather: int = 6) -> np.ndarray:
    """用周边画面的中位色填充气泡区域（比盖白块自然）"""
    out = img.copy()
    h, w = img.shape[:2]
    for (x, y, bw, bh) in boxes:
        g = 12                                   # 向外扩一点，确保盖住描边
        x0, y0 = max(0, x - g), max(0, y - g)
        x1, y1 = min(w, x + bw + g), min(h, y + bh + g)
        ring = 26                                # 取样环宽
        ry0, rx0 = max(0, y0 - ring), max(0, x0 - ring)
        ry1, rx1 = min(h, y1 + ring), min(w, x1 + ring)
        surround = img[ry0:ry1, rx0:rx1].reshape(-1, 3)
        mask = np.ones(len(surround), bool)
        # 排除属于气泡自身的像素
        sub = np.zeros((ry1 - ry0, rx1 - rx0), bool)
        sub[y0 - ry0:y1 - ry0, x0 - rx0:x1 - rx0] = True
        mask &= ~sub.reshape(-1)
        if mask.sum() < 50:
            color = np.array([235, 238, 242], np.uint8)
        else:
            color = np.median(surround[mask], axis=0).astype(np.uint8)

        bh_px, bw_px = y1 - y0, x1 - x0
        block = np.tile(color, (bh_px, bw_px, 1))
        # 混一点周围画面的模糊色，让填充不是死平色（★ 形状必须一致）
        if bh_px > 4 and bw_px > 4:
            blur = cv2.GaussianBlur(img[ry0:ry1, rx0:rx1],
                                    (0, 0), sigmaX=max(4, ring / 2))
            patch = blur[y0 - ry0:y0 - ry0 + bh_px, x0 - rx0:x0 - rx0 + bw_px]
            if patch.shape[:2] == (bh_px, bw_px):
                block = (block.astype(np.float32) * 0.55 +
                         patch.astype(np.float32) * 0.45).astype(np.uint8)
        out[y0:y1, x0:x1] = block

    if feather > 0 and boxes:
        pil = Image.fromarray(out).filter(ImageFilter.GaussianBlur(feather / 3))
        base = Image.fromarray(out)
        # 只在被擦区域做羽化混合
        m = Image.new("L", (w, h), 0)
        from PIL import ImageDraw
        dr = ImageDraw.Draw(m)
        for (x, y, bw, bh) in boxes:
            g = 12
            dr.rectangle([max(0, x - g), max(0, y - g),
                          min(w, x + bw + g), min(h, y + bh + g)], fill=180)
        m = m.filter(ImageFilter.GaussianBlur(8))
        out = np.asarray(Image.composite(pil, base, m))
    return out


def analyze(path: str) -> Dict:
    img = np.asarray(Image.open(path).convert("RGB"))
    h, w = img.shape[:2]
    boxes = find_bubbles(img)
    area = sum(bw * bh for _, _, bw, bh in boxes) / float(w * h)
    return {"boxes": boxes, "ratio": area, "size": (w, h)}


def verdict(ratio: float) -> str:
    if ratio < 0.006:
        return "干净"          # 几乎没有气泡
    if ratio < 0.16:
        return "可擦除"        # 擦掉即可
    return "放弃"             # 气泡太多，擦了会糊


if __name__ == "__main__":
    # ★ stdout 包装必须放在 __main__ 下：
    #   放模块级的话，被别的脚本 import 时也会执行，
    #   调用方的 print 会因原 stdout 被替换/关闭而报 "I/O operation on closed file"
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    for p in sys.argv[1:]:
        r = analyze(p)
        print(f"  {os.path.basename(p):<22} 气泡 {len(r['boxes'])} 个  "
              f"占面积 {r['ratio']*100:5.2f}%  {verdict(r['ratio'])}")
