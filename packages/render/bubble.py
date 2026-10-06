# -*- coding: utf-8 -*-
"""气泡布局算法

难点：
    ① 气泡不能压住人物 —— 需要知道画面哪里「空」
    ② 气泡应放在说话人的**相反侧**，箭头才指得过去
    ③ 同格多气泡不能重叠 —— 说话人不同就强制分侧，数量多就竖排
    ④ 人工调过的位置不能被自动逻辑覆盖（locked）

核心思想：**内容能量最小化**
    画面越「平静」（色彩均匀、边缘少）的地方能量越低，
    气泡放在那里最不打扰画面。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

# ══════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════

@dataclass
class Spot:
    """一个候选放置位"""
    name: str            # tl / tr / bl / br / tm / bm / ml / mr
    x: float             # 归一化左上角
    y: float
    score: float = 0.0
    side: str = "left"   # 位于画面哪一侧


@dataclass
class Placed:
    """已放置的气泡（用于避让）"""
    x: float
    y: float
    w: float
    h: float


LEFT_SPOTS = ("tl", "bl", "ml")
RIGHT_SPOTS = ("tr", "br", "mr")
TOP_SPOTS = ("tl", "tr", "tm")

GAP = 0.012          # 气泡之间的最小间隙（归一化）


# ══════════════════════════════════════════════════════════════════
# 内容能量图
# ══════════════════════════════════════════════════════════════════

def energy_map(img: Image.Image, tw: int = 160) -> np.ndarray:
    """内容能量图：值越大表示该区域越「满」（有内容/对比强/边缘多）

    能量 = 0.6 × 与画面中位色的差异 + 0.4 × 边缘梯度
    """
    w0, h0 = img.size
    th = max(48, int(tw * h0 / max(1, w0)))
    small = np.asarray(img.convert("RGB").resize((tw, th))).astype("float32")

    med = np.median(small.reshape(-1, 3), axis=0)
    diff = np.abs(small - med).sum(axis=2)
    diff = diff / (diff.max() + 1e-6)

    g = small.mean(axis=2)
    gy, gx = np.gradient(g)
    edge = np.sqrt(gx * gx + gy * gy)
    edge = edge / (edge.max() + 1e-6)

    return 0.6 * diff + 0.4 * edge


def detect_person_side(img: Image.Image, top_only: float = 0.78) -> Optional[str]:
    """检测人物主要位于画面左/右

    依据：肤色 + 深色（发/衣）+ 红色（围巾/血），并**排除雪白背景**
    （否则整幅画在雪景里全是「亮」的，判不出来）

    返回 'left' / 'right'；差异不明显时返回 None（调用方自行决定）
    """
    w0, h0 = img.size
    small = img.convert("RGB").resize((120, max(64, int(120 * h0 / max(1, w0)))))
    a = np.asarray(small).astype("int16")
    a = a[:int(a.shape[0] * top_only)]

    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]

    skin = (r > 150) & (g > 105) & (b > 85) & (r > b + 18) & (np.abs(r - g) < 75)
    dark = (r + g + b) < 350
    red = (r > 105) & (r > g + 40) & (r > b + 40)
    snow = (r > 205) & (g > 212) & (b > 218)

    person = (skin | dark | red) & (~snow)

    half = a.shape[1] // 2
    left = float(person[:, :half].mean())
    right = float(person[:, half:].mean())
    if abs(left - right) < 0.006:
        return None
    return "left" if left > right else "right"


# ══════════════════════════════════════════════════════════════════
# 候选位
# ══════════════════════════════════════════════════════════════════

def candidate_spots(bw: float, bh: float,
                    must_side: Optional[str] = None) -> List[Spot]:
    """生成候选放置位

    Args:
        bw, bh: 气泡尺寸（归一化，相对画面宽高）
        must_side: 'left'/'right' 时只返回该半区的候选
    """
    m = 0.022
    spots = [
        Spot("tl", m, m, side="left"),
        Spot("tr", 1 - m - bw, m, side="right"),
        Spot("bl", m, 1 - m - bh, side="left"),
        Spot("br", 1 - m - bw, 1 - m - bh, side="right"),
        Spot("tm", 0.5 - bw / 2, m, side="left"),
        Spot("bm", 0.5 - bw / 2, 1 - m - bh, side="left"),
        Spot("ml", m, 0.5 - bh / 2, side="left"),
        Spot("mr", 1 - m - bw, 0.5 - bh / 2, side="right"),
    ]
    if must_side == "left":
        spots = [s for s in spots if s.side == "left" and s.x + bw <= 0.52]
    elif must_side == "right":
        spots = [s for s in spots if s.side == "right" and s.x >= 0.48]
    return spots


# ══════════════════════════════════════════════════════════════════
# 主算法
# ══════════════════════════════════════════════════════════════════

def choose_spot(
    img: Image.Image,
    bw: float,
    bh: float,
    *,
    prefer_opposite_of: Optional[str] = None,
    must_side: Optional[str] = None,
    placed: Optional[Sequence[Placed]] = None,
    top_bias: float = 0.02,
) -> Tuple[float, float]:
    """为气泡选一个最佳位置

    Args:
        img:               单格图
        bw, bh:            气泡尺寸（归一化）
        prefer_opposite_of: 说话人所在侧（'left'/'right'）—— 气泡会倾向放另一侧
        must_side:         强制只在该半区（同格多气泡分侧时用）
        placed:            已放置的气泡，用于避让
        top_bias:          轻微偏好上半部（漫画阅读习惯）

    Returns:
        (x, y) 归一化左上角坐标
    """
    placed = list(placed or [])
    e = energy_map(img)
    th, tw = e.shape

    cw = max(2, min(int(round(bw * tw)), tw - 1))
    ch = max(2, min(int(round(bh * th)), th - 1))

    spots = candidate_spots(bw, bh, must_side=must_side)
    if not spots:
        spots = candidate_spots(bw, bh)

    best: Optional[Tuple[float, float, float]] = None   # (score, x, y)

    for s in spots:
        # 该方向上的纵向避让候选：沿同列向下 / 向上平移
        ys = [s.y]
        step = ch / th + GAP
        for k in range(1, 5):
            if s.y + k * step + ch / th <= 1 - 0.01:
                ys.append(s.y + k * step)
        for k in range(1, 4):
            if s.y - k * step >= 0.01:
                ys.append(s.y - k * step)

        for y in ys:
            x0 = int(round(np.clip(s.x, 0, 1 - cw / tw) * tw))
            y0 = int(round(np.clip(y, 0, 1 - ch / th) * th))
            patch = e[y0:y0 + ch, x0:x0 + cw]
            if patch.size == 0:
                continue

            # 硬约束：分侧时不得越界（显式裁剪后再判一次）
            if must_side == "left" and s.x + bw > 0.52:
                continue
            if must_side == "right" and s.x < 0.48:
                continue

            score = float(patch.mean())

            # ① 避让已放置的气泡（重叠重罚）
            for p in placed:
                ox = max(0.0, min(s.x + bw, p.x + p.w) - max(s.x, p.x))
                oy = max(0.0, min(y + bh, p.y + p.h) - max(y, p.y))
                overlap = (ox * oy) / max(1e-6, bw * bh)
                score += 4.0 * overlap
                # 同侧再放也要付代价（避免全挤在一角）
                if abs((s.x + bw / 2) - (p.x + p.w / 2)) < 0.4:
                    score += 0.55

            # ② 放说话人的相反侧（弱偏置，不硬性）
            if prefer_opposite_of == "left" and s.side == "right":
                score -= 0.05
            elif prefer_opposite_of == "right" and s.side == "left":
                score -= 0.05

            # ③ 轻微偏好上方
            if s.name in TOP_SPOTS:
                score -= top_bias

            if best is None or score < best[0]:
                best = (score, float(np.clip(s.x, 0, max(0.0, 1 - bw))),
                        float(np.clip(y, 0, max(0.0, 1 - bh))))

    if best is None:                                        # pragma: no cover
        return 0.02 if (must_side or "left") == "left" else max(0.02, 0.98 - bw), 0.03
    return best[1], best[2]


# ══════════════════════════════════════════════════════════════════
# 一格多气泡的整体布局
# ══════════════════════════════════════════════════════════════════

@dataclass
class BubbleRequest:
    """待布局的一个气泡"""
    who_side: Optional[str]        # 说话人所在侧
    w: float                       # 归一化宽
    h: float                       # 归一化高


def layout_bubbles(
    img: Image.Image,
    items: Sequence[BubbleRequest],
) -> List[Tuple[float, float]]:
    """给同一格的多个气泡统一布局

    策略：
        · 1 个         → 放最空处，倾向说话人反侧
        · 2–4 个不同侧 → 强制左右分侧，避免挤在一起
        · ≥5 个        → 固定竖排（可预期、不重叠）
    """
    n = len(items)
    if n == 0:
        return []

    # ① 数量多 → 固定竖排
    if n >= 5:
        out: List[Tuple[float, float]] = []
        y = 0.015
        for i, it in enumerate(items):
            x = 0.02
            out.append((x, min(y, max(0.0, 1 - it.h))))
            y += it.h + GAP
        return out

    # ② 判定是否需要分侧
    sides = [it.who_side for it in items]
    known = [s for s in sides if s in ("left", "right")]
    need_split = n >= 2 and len(set(known)) >= 2

    placed: List[Placed] = []
    out = []
    for i, it in enumerate(items):
        if need_split and known:
            # 交替分侧：气泡放说话人的反侧
            speaker = it.who_side or known[i % len(known)]
            must = "right" if speaker == "left" else "left"
        else:
            must = None
        x, y = choose_spot(
            img, it.w, it.h,
            prefer_opposite_of=it.who_side,
            must_side=must,
            placed=placed,
        )
        placed.append(Placed(x, y, it.w, it.h))
        out.append((x, y))
    return out


# ══════════════════════════════════════════════════════════════════
# 箭头
# ══════════════════════════════════════════════════════════════════

def default_tail(img: Image.Image, box: Tuple[float, float, float, float],
                 speaker_side: Optional[str]) -> Tuple[float, float]:
    """给气泡算一个默认的箭头尖端

    规则：从气泡指向说话人那一侧的画面中部；若说话人不在画面里，
    指向画面中部的空白处（表示画外音）。
    """
    bx, by, bw, bh = box
    side = speaker_side or detect_person_side(img)

    # 气泡中心
    cx, cy = bx + bw / 2, by + bh / 2

    if side is None:
        tx, ty = 0.5, 0.42          # 画外音：指向画面中部空白
    elif side == "left":
        tx, ty = 0.24, 0.50
    else:
        tx, ty = 0.76, 0.50

    # 避免箭头戳在气泡自己身上
    if abs(cx - tx) < 0.06 and abs(cy - ty) < 0.06:
        ty = 0.62 if cy < 0.5 else 0.30
    return tx, ty
