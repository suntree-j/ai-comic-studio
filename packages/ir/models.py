# -*- coding: utf-8 -*-
"""Comic IR 数据模型（Pydantic v2）

设计要点：
    ① 枚举而非自由文本  —— 把「算错」变成「填不出来」
    ② 引用而非内联      —— cast 只写 character_id，外观从 bible 取
    ③ 可追溯            —— 对白/分镜都带 source_span 指回原文
    ④ 状态外置          —— 角色伤情/服装在 bible 里跨格追踪
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ══════════════════════════════════════════════════════════════════
# 枚举
# ══════════════════════════════════════════════════════════════════

class ShotSize(str, Enum):
    """景别 —— 受控词表"""
    EXTREME_WIDE = "极远景"      # 人物极小，主体是法术与天地
    WIDE = "大全景"
    FULL = "全景"
    MEDIUM = "中景"
    CLOSE = "近景"
    EXTREME_CLOSE = "特写"


class PanelSize(str, Enum):
    """单格画幅 —— 决定排版"""
    WIDE = "2048x1400"           # 横
    TALL = "1332x1776"           # 竖
    SQUARE = "1536x1536"         # 方


class BubbleStyle(str, Enum):
    SPEECH = "speech"            # 普通对话
    SHOUT = "shout"              # 喊叫（尖角）
    THINK = "think"              # 心声（云朵）
    NARRATION = "narration"      # 旁白（方框）
    WHISPER = "whisper"          # 低语（虚线）


class DamageLevel(str, Enum):
    """主角战斗损伤分级 —— 保证「战斗有代价」"""
    L0 = "L0"   # 整洁：刚登场
    L1 = "L1"   # 初战：衣摆翻飞、脸颊擦伤
    L2 = "L2"   # 硬扛：肩部撕裂、前襟破洞
    L3 = "L3"   # 重伤：上衣大片撕碎、半张脸电黑
    L4 = "L4"   # 战后：伤口愈合中、神情疲惫


class PageType(str, Enum):
    TITLE = "title"        # 卷首/全书卷首
    PANELS = "panels"      # 正常漫画页
    EMPTY = "empty"        # ★ 被删除但保留页码的占位页


# ══════════════════════════════════════════════════════════════════
# Bible —— 一致性的唯一来源
# ══════════════════════════════════════════════════════════════════

class Injury(BaseModel):
    """角色伤情 —— 跨格追踪，直到 healed 才消失"""
    model_config = ConfigDict(extra="forbid")

    part: str = Field(..., description="部位，如「左肩→右腰」")
    kind: str = Field(..., description="伤型，如「霜白伤口」")
    desc: str = Field(..., description="画法描述，会逐字进提示词")
    since: str = Field(..., description="起始镜头 id，如 ch2429_P001")
    healed: bool = False
    healed_at: Optional[str] = Field(None, description="愈合的镜头 id")


class CharacterState(BaseModel):
    """角色当前状态 —— 渲染时叠加到提示词"""
    model_config = ConfigDict(extra="forbid")

    outfit: str = "default"
    injuries: List[Injury] = Field(default_factory=list)
    emotion: Optional[str] = None

    def active_injuries(self) -> List[Injury]:
        return [i for i in self.injuries if not i.healed]


class CharacterVariant(BaseModel):
    """形态变体 —— 只允许改发型/服装/气势，禁止改脸型瞳色"""
    model_config = ConfigDict(extra="forbid")

    name: str
    override: str = Field(..., description="追加到标准块后的段落")
    # ★ 硬约束：任何变体都不得改动脸型与瞳色
    preserve_face: bool = True
    preserve_eye_color: bool = True

    @field_validator("preserve_face", "preserve_eye_color")
    @classmethod
    def _must_preserve(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "变体不得改动脸型或瞳色（这是跨格辨识度的基准）"
            )
        return v


class Appearance(BaseModel):
    """角色外观标准块"""
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=20,
                      description="逐字拼进提示词的标准块，不得改写")
    forbidden: List[str] = Field(default_factory=list,
                                 description="该角色绝对不要出现的元素")


class Character(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    aliases: List[str] = Field(default_factory=list)
    appearance: Appearance
    variants: Dict[str, CharacterVariant] = Field(default_factory=dict)
    ref_images: List[str] = Field(default_factory=list)
    state: CharacterState = Field(default_factory=CharacterState)


class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    text: str
    ref_images: List[str] = Field(default_factory=list)


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(..., description="技能名（标签只显示这个名字）")
    element: str = Field(..., description="属性，对应配色表")
    color: Optional[List[int]] = Field(None, min_length=3, max_length=3)
    ref_images: List[str] = Field(default_factory=list)

    @field_validator("color")
    @classmethod
    def _rgb(cls, v):
        if v is not None and any(not (0 <= c <= 255) for c in v):
            raise ValueError("color 必须是 0–255 的 RGB")
        return v


class StyleSpec(BaseModel):
    """全篇共享风格 —— 逐字不变"""
    model_config = ConfigDict(extra="forbid")

    shared_block: str = Field(..., min_length=20)
    no_text_rule: str = Field(
        default=("画面中绝对不要出现任何文字、台词、对白框、气泡、字幕、"
                 "水印、logo、任何中文/英文/日文字符。"),
        description="★ 防止对白被画进图里的关键约束",
    )


class Bible(BaseModel):
    model_config = ConfigDict(extra="forbid")

    characters: Dict[str, Character] = Field(default_factory=dict)
    scenes: Dict[str, Scene] = Field(default_factory=dict)
    skills: Dict[str, Skill] = Field(default_factory=dict)
    style: StyleSpec

    @model_validator(mode="after")
    def _keys_match_ids(self):
        for k, c in self.characters.items():
            if k != c.id:
                raise ValueError(f"characters 的键 {k!r} 与 id {c.id!r} 不一致")
        for k, s in self.scenes.items():
            if k != s.id:
                raise ValueError(f"scenes 的键 {k!r} 与 id {s.id!r} 不一致")
        for k, s in self.skills.items():
            if k != s.id:
                raise ValueError(f"skills 的键 {k!r} 与 id {s.id!r} 不一致")
        return self


# ══════════════════════════════════════════════════════════════════
# Storyboard —— 每格画什么
# ══════════════════════════════════════════════════════════════════

class CastMember(BaseModel):
    """出场角色 —— ★ 只引用 id，绝不内联外观"""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="bible.characters 的键")
    pos: str = Field(..., description="画面位置，如 left / right / center / far_right")
    variant: str = "default"
    note: Optional[str] = None


class Panel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z0-9]+_P\d{3}$",
                    description="镜头 id，如 ch2436_P001")
    seq: int = Field(..., ge=1)
    size: PanelSize
    shot: ShotSize
    cast: List[CastMember] = Field(default_factory=list)
    # ★ 两人以上同框时必填（validator 强制）
    distance: Optional[str] = Field(
        None, description="角色间距离，必须具体（如「相距约 8 米」）")
    action: str = Field(..., min_length=4)
    skill: Optional[str] = None
    background: Optional[str] = None
    mood: Optional[str] = None
    damage_level: Optional[DamageLevel] = None
    emotion_hint: Dict[str, str] = Field(
        default_factory=dict, description="角色 id → 该格表情")
    source_span: Optional[List[int]] = Field(
        None, min_length=2, max_length=2,
        description="对应原文的行号区间 [起, 止]，用于追溯")

    @field_validator("source_span")
    @classmethod
    def _span_order(cls, v):
        if v and v[0] > v[1]:
            raise ValueError(f"source_span 起止颠倒：{v}")
        return v


class Storyboard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter: str
    panels: List[Panel]

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [p.id for p in self.panels]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"镜头 id 重复：{sorted(dup)}")
        return self


# ══════════════════════════════════════════════════════════════════
# Dialogue —— 与画面分离
# ══════════════════════════════════════════════════════════════════

class Box(BaseModel):
    """归一化坐标 [0,1]"""
    model_config = ConfigDict(extra="forbid")

    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)


class BubbleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: BubbleStyle = BubbleStyle.SPEECH
    box: Optional[Box] = None
    tail: Optional[Box] = Field(None, description="箭头尖端指向")
    font_size: Optional[int] = Field(None, ge=12, le=200)


class Utterance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    who: str = Field(..., description="bible.characters 的键")
    text: str = Field(..., min_length=1)
    source_span: Optional[List[int]] = Field(None, min_length=2, max_length=2)
    # ★ 未经人工确认的说话人不得进入渲染
    confirmed: bool = False
    bubble: BubbleSpec = Field(default_factory=BubbleSpec)
    # ★ 锁定后自动布局逻辑不得改动
    locked: bool = False


class DialogueBook(BaseModel):
    """镜头 id → 该镜的对白列表"""
    model_config = ConfigDict(extra="forbid")

    items: Dict[str, List[Utterance]] = Field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════
# Layout —— 页码冻结
# ══════════════════════════════════════════════════════════════════

class Volume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    sub: Optional[str] = None


class LayoutPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(..., ge=1)
    type: PageType
    # type == panels 时必填
    panels: List[str] = Field(default_factory=list)
    # type == title 时必填
    title: Optional[str] = None
    sub: Optional[str] = None
    volume: Optional[str] = None
    # type == empty 时的说明（★ 删除但保留页码）
    note: Optional[str] = None

    @model_validator(mode="after")
    def _type_consistency(self):
        if self.type == PageType.PANELS and not self.panels:
            raise ValueError(f"第 {self.page} 页 type=panels 但 panels 为空")
        if self.type == PageType.TITLE and not self.title:
            raise ValueError(f"第 {self.page} 页 type=title 但 title 为空")
        if self.type == PageType.EMPTY and not self.note:
            raise ValueError(
                f"第 {self.page} 页 type=empty 必须写 note 说明删了什么")
        return self


class Layout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(..., ge=1)
    volumes: List[Volume] = Field(default_factory=list)
    pages: List[LayoutPage]

    @model_validator(mode="after")
    def _pages_consistent(self):
        nums = [p.page for p in self.pages]
        if len(nums) != self.total:
            raise ValueError(
                f"total={self.total} 但 pages 有 {len(nums)} 条")
        if nums != sorted(nums):
            raise ValueError("pages 未按 page 升序排列")
        if nums != list(range(1, self.total + 1)):
            missing = set(range(1, self.total + 1)) - set(nums)
            dup = {n for n in nums if nums.count(n) > 1}
            raise ValueError(
                f"页码不连续。缺 {sorted(missing)[:10]}；重复 {sorted(dup)[:10]}")
        return self


# ══════════════════════════════════════════════════════════════════
# 聚合
# ══════════════════════════════════════════════════════════════════

class ComicProject(BaseModel):
    """一个完整项目 = 四个 IR 文件"""
    model_config = ConfigDict(extra="forbid")

    name: str
    bible: Bible
    storyboards: List[Storyboard] = Field(default_factory=list)
    dialogue: DialogueBook
    layout: Layout
