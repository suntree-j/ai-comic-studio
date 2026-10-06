# -*- coding: utf-8 -*-
"""AI Comic Studio · Comic IR

Comic IR 是「小说 → 漫画」流程中的受约束中间表示。
LLM 只负责产出 IR，渲染引擎只读 IR —— 双方通过它解耦。

四个文件：
    bible.json       角色/场景/技能/风格的持久设定（一致性的唯一来源）
    storyboard.json  每格画什么
    dialogue.json    谁说哪句、气泡放哪
    layout.json      页序与分卷（★ 冻结，永不重排）
"""

from .models import (
    Bible,
    Character,
    CharacterState,
    Injury,
    CharacterVariant,
    Appearance,
    Scene,
    Skill,
    StyleSpec,
    Storyboard,
    Panel,
    CastMember,
    DialogueBook,
    Utterance,
    BubbleSpec,
    Box,
    Layout,
    LayoutPage,
    Volume,
    ComicProject,
    ShotSize,
    PanelSize,
    BubbleStyle,
    DamageLevel,
    PageType,
)
from .validator import validate, ValidationError, ValidationReport, Severity, RULES

__all__ = [
    "Bible", "Character", "CharacterState", "Injury", "CharacterVariant",
    "Appearance", "Scene", "Skill", "StyleSpec",
    "Storyboard", "Panel", "CastMember",
    "DialogueBook", "Utterance", "BubbleSpec", "Box",
    "Layout", "LayoutPage", "Volume",
    "ComicProject",
    "ShotSize", "PanelSize", "BubbleStyle", "DamageLevel", "PageType",
    "validate", "ValidationError", "ValidationReport", "Severity", "RULES",
]

__version__ = "0.1.0"
