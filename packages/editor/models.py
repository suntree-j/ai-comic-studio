# -*- coding: utf-8 -*-
"""画布编辑器数据模型

## 与 packages/ir 的关系
    **两者正交、互不依赖。**
    · `packages/ir`      —— 由小说自动改编成漫画（AI 生成流程）
    · `packages/editor`  —— 从空白画布手工做漫画（Office 式编辑器）

    编辑器不引用 IR 的任何模型，这样两条产品线可以各自演进。

## 坐标约定
    所有位置与尺寸用**归一化比例**（0–1，相对画布宽/高），不用像素。
    好处：换分辨率、导出不同尺寸时不用换算，前端可以直接乘显示尺寸定位。
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import (BaseModel, ConfigDict, Field, field_validator,
                      model_validator)


# ══════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════

def new_id() -> str:
    """生成一个新的元素/页面 id

    ★ 不要用 `SomeModel().id` 来取「新 id」——
      有些模型有必填字段（比如 Page 的 number），直接实例化会报 ValidationError。
      统一走这个函数。
    """
    return uuid.uuid4().hex[:10]


# ══════════════════════════════════════════════════════════════════
# 枚举
# ══════════════════════════════════════════════════════════════════

class BubbleKind(str, Enum):
    """气泡样式（与 IR 的 BubbleStyle 保持一致，便于将来互通）"""
    SPEECH = "speech"        # 圆角矩形 + 指向尾巴（普通对白）
    SHOUT = "shout"          # 尖角爆炸形（喊叫）
    THINK = "think"          # 云朵形 + 小圆点尾巴（心声）
    NARRATION = "narration"  # 方框（旁白/画外音）
    WHISPER = "whisper"      # 虚线圆角框（低语）


class TextAlign(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class FitMode(str, Enum):
    """图片在格子里的摆放方式"""
    COVER = "cover"    # 铺满并裁掉溢出（默认）
    CONTAIN = "contain"  # 完整显示，可能有留白
    FILL = "fill"      # 拉伸铺满（会变形）


# ══════════════════════════════════════════════════════════════════
# 元素
# ══════════════════════════════════════════════════════════════════

class ElementBase(BaseModel):
    """所有元素的公共字段

    ★ `z` 是**顺序**而不是坐标：数值越大越靠上（后绘制）。
      在右侧「元素」面板里可以上下移动，就是改这个值。
    """
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    kind: Literal["image", "bubble", "text", "shape"]
    name: str = ""                      # 面板里显示的名字
    x: float = Field(0.1, ge=-1.0, le=2.0)   # 左上角 x（比例）
    y: float = Field(0.1, ge=-1.0, le=2.0)   # 左上角 y（比例）
    z: int = 0                          # ★ 顺序
    locked: bool = False                # 锁定后不能被拖动
    opacity: float = Field(1.0, ge=0.0, le=1.0)

    @field_validator("x", "y")
    @classmethod
    def _finite(cls, v: float) -> float:
        if v != v:                      # NaN
            raise ValueError("坐标不能是 NaN")
        return round(v, 5)


class ImageElement(ElementBase):
    """一张图（漫画格 / 整页插画）

    `w` 是宽度比例；`h` 由图片原始宽高比自动算（也可手动覆盖）。
    这样拖动缩放手柄时不会把图拉变形。
    """
    kind: Literal["image"] = "image"
    asset_id: str = ""                  # 指向 Asset
    w: float = Field(0.5, gt=0.0, le=2.0)
    h: Optional[float] = Field(None, gt=0.0, le=2.0)  # 空 = 按原图比例
    fit: FitMode = FitMode.COVER
    rotation: float = 0.0               # 角度（顺时针）
    flip_h: bool = False
    crop: Optional[Tuple[float, float, float, float]] = None  # 相对裁剪 l,t,r,b
    border: float = Field(0.0, ge=0.0)  # 描边粗细（比例）
    shadow: bool = False


class BubbleElement(ElementBase):
    """一个气泡（对白 / 旁白 / 心声）

    `w` 手动调，`h` **由文字自动算** —— 和 Office 的文本框一样：
    改字号或文字，框自动长高。这样不会出现「文字溢出气泡」。
    """
    kind: Literal["bubble"] = "bubble"
    text: str = ""
    speaker: str = ""                   # 说话人（显示在气泡上方的小字）
    w: float = Field(0.34, gt=0.0, le=1.5)
    style: BubbleKind = BubbleKind.SPEECH
    font_size: int = Field(46, ge=12, le=200)
    align: TextAlign = TextAlign.CENTER
    tail: Optional[Tuple[float, float]] = None   # 箭头尖端（比例）；空 = 不画
    tail_w: float = Field(0.35, ge=0.1, le=1.0)  # 尾巴根部宽度（相对气泡宽）
    fill: str = "#ffffff"
    line: str = "#1b1b1b"
    text_color: str = "#111111"
    speaker_color: str = "#4a4a4a"
    line_width: float = Field(0.006, ge=0.0, le=0.05)   # 描边粗细（相对画布宽）
    pad: float = Field(0.014, ge=0.0, le=0.1)           # 内边距（相对画布宽）
    radius: float = Field(0.02, ge=0.0, le=0.2)         # 圆角（相对画布宽）


class TextElement(ElementBase):
    """普通文字（标题、拟声词、说明）"""
    kind: Literal["text"] = "text"
    text: str = ""
    w: float = Field(0.4, gt=0.0, le=2.0)
    font_size: int = Field(48, ge=8, le=400)
    align: TextAlign = TextAlign.CENTER
    color: str = "#111111"
    bold: bool = False
    outline: float = Field(0.0, ge=0.0, le=0.05)   # 描边（相对画布宽），0 = 无
    outline_color: str = "#ffffff"
    rotation: float = 0.0
    line_spacing: float = Field(1.2, ge=0.8, le=3.0)


class ShapeElement(ElementBase):
    """矩形 / 椭圆色块（用来压住原图文字、做分格线、加底色）"""
    kind: Literal["shape"] = "shape"
    shape: Literal["rect", "ellipse"] = "rect"
    w: float = Field(0.3, gt=0.0, le=2.0)
    h: float = Field(0.1, gt=0.0, le=2.0)
    fill: str = "#ffffff"
    line: str = "#1b1b1b"
    line_width: float = Field(0.0, ge=0.0, le=0.05)
    radius: float = Field(0.0, ge=0.0, le=0.3)


Element = ImageElement | BubbleElement | TextElement | ShapeElement

_ELEMENT_TYPES = {
    "image": ImageElement,
    "bubble": BubbleElement,
    "text": TextElement,
    "shape": ShapeElement,
}


def parse_element(data: Dict[str, Any]) -> Element:
    """按 kind 反序列化成具体类型"""
    kind = data.get("kind")
    cls = _ELEMENT_TYPES.get(str(kind))
    if cls is None:
        raise ValueError(f"未知的元素类型：{kind}")
    return cls.model_validate(data)


# ══════════════════════════════════════════════════════════════════
# 素材
# ══════════════════════════════════════════════════════════════════

class Asset(BaseModel):
    """上传的图片（原图存磁盘，这里只记元信息）"""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    filename: str
    stored_name: str                    # 实际文件名（含扩展名）
    width: int = 0
    height: int = 0
    bytes: int = 0
    uploaded_at: float = Field(default_factory=time.time)

    @property
    def aspect(self) -> float:
        return (self.width / self.height) if self.height else 1.0


# ══════════════════════════════════════════════════════════════════
# 页面
# ══════════════════════════════════════════════════════════════════

class Page(BaseModel):
    """一页画布

    ★ `number` 是**身份**，不是位置：新建后不再变。
      调整顺序时改的是 `order`，`number` 保持不动 ——
      这样「第 7 页」永远指同一页（`packages/ir` 里那个页码冻结机制的教训）。
    """
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    number: int                          # 页码（只分配，不重排）
    order: int = 0                       # 显示顺序（可调）
    title: str = ""
    width: int = 1400                    # 画布像素宽
    height: int = 2000                   # 画布像素高
    background: str = "#ffffff"
    elements: List[Element] = Field(default_factory=list)
    note: str = ""

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.7

    def sorted_elements(self) -> List[Element]:
        """按顺序（z 从小到大 = 从下到上）返回"""
        return sorted(self.elements, key=lambda e: (e.z, e.id))

    def normalize_z(self) -> None:
        """把 z 重排成 0,1,2… 保持相对顺序"""
        for i, e in enumerate(self.sorted_elements()):
            e.z = i


# ══════════════════════════════════════════════════════════════════
# 项目
# ══════════════════════════════════════════════════════════════════

class EditProject(BaseModel):
    """一个编辑项目 = 素材库 + 若干页"""
    model_config = ConfigDict(extra="forbid")

    name: str = "untitled"
    title: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    canvas_width: int = 1400            # 新建页面的默认尺寸
    canvas_height: int = 2000
    default_font_size: int = 46
    assets: List[Asset] = Field(default_factory=list)
    pages: List[Page] = Field(default_factory=list)
    #: 已分配过的最大页码 —— **只增不减**，删页也不回收
    max_number: int = 0

    # ── 查询 ──────────────────────────────────────────────
    def asset(self, asset_id: str) -> Optional[Asset]:
        return next((a for a in self.assets if a.id == asset_id), None)

    def page(self, page_id: str) -> Optional[Page]:
        return next((p for p in self.pages if p.id == page_id), None)

    def ordered_pages(self) -> List[Page]:
        return sorted(self.pages, key=lambda p: (p.order, p.number))

    # ── 不变式 ────────────────────────────────────────────
    @model_validator(mode="after")
    def _sync_max_number(self) -> "EditProject":
        """保证 `max_number` 不小于现有任何页码

        ★ 为什么需要：
          `max_number` 是「发到哪儿了」的记账。从磁盘加载旧项目、
          或反序列化时它可能是 0 —— 这时删掉最后一页再新建，
          就会**回收**那个页码。这里在每次校验后对齐一次。

          注意：直接构造对象（不经过 Pydantic 校验）时这个钩子不会跑，
          所以 `next_number()` 里也再对齐一次。
        """
        highest = max((p.number for p in self.pages), default=0)
        if self.max_number < highest:
            self.max_number = highest
        return self

    # ── 修改 ──────────────────────────────────────────────
    def next_number(self) -> int:
        """下一个可用页码 —— **只增不减，删页也不回收**

        ★ 为什么不能用 `max(现有页码) + 1`：
          删掉第 2 页后再新建，会又拿到 2 —— 于是「第 2 页」这个称呼
          一会儿指这页一会儿指那页，跟别人沟通时对不上。
          所以用单调递增的 `max_number` 记账。
          （`packages/ir` 的页码冻结机制是同一个道理。）
        """
        highest = max((p.number for p in self.pages), default=0)
        if self.max_number < highest:
            self.max_number = highest
        n = self.max_number + 1
        self.max_number = n
        return n

    def touch(self) -> None:
        self.updated_at = time.time()

    def stats(self) -> Dict[str, Any]:
        return {
            "assets": len(self.assets),
            "pages": len(self.pages),
            "elements": sum(len(p.elements) for p in self.pages),
            "bubbles": sum(1 for p in self.pages for e in p.elements
                           if e.kind == "bubble"),
        }
