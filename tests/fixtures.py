# -*- coding: utf-8 -*-
"""测试夹具：一个「全部规则都通过」的最小项目

每个测试从这里 deepcopy 一份，然后故意破坏一处，
断言对应规则被触发。
"""

from __future__ import annotations

import copy
from typing import List

from packages.ir import (
    Appearance,
    Bible,
    Box,
    BubbleSpec,
    BubbleStyle,
    CastMember,
    Character,
    CharacterState,
    ComicProject,
    DamageLevel,
    DialogueBook,
    Injury,
    Layout,
    LayoutPage,
    PageType,
    Panel,
    PanelSize,
    Scene,
    ShotSize,
    Skill,
    Storyboard,
    StyleSpec,
    Utterance,
    Volume,
)

# ── 原文（IR-007 会拿它核对对白） ──
SOURCE_LINES: List[str] = [
    "第2436章 冰晶刹弓",                                     # 1
    "",                                                     # 2
    "穆宁雪站在冰玻璃长道的一端。",                              # 3
    "「你喜欢这柄冰晶刹弓，作为多年的朋友，我自然亲手赠你。」",       # 4
    "南荣倪被一箭钉在了断崖上，动弹不得。",                       # 5
    "穆飞鸾与穆隐凤并肩从长道另一端走来。",                       # 6
    "「解决掉他们。」穆飞鸾说。",                                # 7
    "莫凡笑了笑：「我们也很久没有联手了。」",                     # 8
]


def make_bible() -> Bible:
    return Bible(
        characters={
            "mu_ningxue": Character(
                id="mu_ningxue",
                name="穆宁雪",
                aliases=["宁雪", "雪雪"],
                appearance=Appearance(
                    text="一位二十出头的年轻女子。冷调雪银白色的长发，额前有整齐的刘海，"
                         "淡冰蓝色瞳孔，神情冷淡平静。纯白色立领修身外套，正红色长围巾。",
                    forbidden=["蓝色系服装", "露出额头"],
                ),
                state=CharacterState(
                    outfit="default",
                    injuries=[
                        Injury(part="左肩→右腰", kind="霜白伤口",
                               desc="边缘结霜向体内蔓延，一滴血不出",
                               since="ch2436_P001", healed=False),
                    ],
                ),
            ),
            "mo_fan": Character(
                id="mo_fan",
                name="莫凡",
                appearance=Appearance(
                    text="一位十几岁到二十出头的年轻男子。红色短碎发，蓝绿色瞳孔，"
                         "深蓝色连帽卫衣敞开露出瓷白衬衫，黑色长裤。",
                    forbidden=["黑发", "只穿卫衣没有白衬衫"],
                ),
            ),
            "mu_feiluan": Character(
                id="mu_feiluan",
                name="穆飞鸾",
                appearance=Appearance(
                    text="一位二十多岁的年轻男子。银白色长发，浅色瞳孔。"
                         "浅蓝色的长毛领大衣，内搭黑色高领。",
                ),
            ),
        },
        scenes={
            "ice_corridor": Scene(
                id="ice_corridor", name="冰玻璃长道",
                text="雪竹林间的冰玻璃长道，尽头是穆氏城楼。",
            ),
        },
        skills={
            "ice_bow": Skill(id="ice_bow", name="冰晶刹弓", element="绝冰",
                             color=[165, 228, 252]),
        },
        style=StyleSpec(
            shared_block="全彩国漫风格的商业漫画页，柔和通透的画面。【线条】细线，深蓝紫。",
        ),
    )


def make_storyboard() -> Storyboard:
    return Storyboard(
        chapter="2436",
        panels=[
            Panel(
                id="ch2436_P001", seq=1,
                size=PanelSize.WIDE, shot=ShotSize.WIDE,
                cast=[CastMember(id="mu_ningxue", pos="left", variant="default"),
                      CastMember(id="mu_feiluan", pos="far_right")],
                distance="两人相距约 8 米，中间是大片空旷冰道",
                action="穆宁雪立于冰玻璃长道拉弓，箭矢离弦射向画面另一端",
                skill="ice_bow", background="ice_corridor",
                mood="肃杀，细雪横飞",
                damage_level=DamageLevel.L0,
                emotion_hint={"mu_ningxue": "冷淡无波", "mu_feiluan": "倨傲"},
                source_span=[3, 4],
            ),
            Panel(
                id="ch2436_P002", seq=2,
                size=PanelSize.TALL, shot=ShotSize.CLOSE,
                cast=[CastMember(id="mu_ningxue", pos="center")],
                action="穆宁雪冷眸直视前方，冰晶刹弓拉成满弧",
                background="ice_corridor",
                damage_level=DamageLevel.L0,
                emotion_hint={"mu_ningxue": "锁定目标"},
                source_span=[4, 4],
            ),
        ],
    )


def make_dialogue() -> DialogueBook:
    return DialogueBook(items={
        "ch2436_P001": [
            Utterance(
                id="ch2436_P001_b1",
                who="mu_ningxue",
                text="你喜欢这柄冰晶刹弓，作为多年的朋友，我自然亲手赠你。",
                source_span=[4, 4],
                confirmed=True,
                bubble=BubbleSpec(
                    style=BubbleStyle.SPEECH,
                    box=Box(x=0.60, y=0.03),
                    tail=Box(x=0.30, y=0.45),
                ),
                locked=True,
            ),
            Utterance(
                id="ch2436_P001_b2",
                who="mu_feiluan",
                text="解决掉他们。",
                source_span=[7, 7],
                confirmed=True,
                bubble=BubbleSpec(
                    style=BubbleStyle.SPEECH,
                    box=Box(x=0.02, y=0.60),
                    tail=Box(x=0.70, y=0.50),
                ),
                locked=True,
            ),
        ],
    })


def make_layout() -> Layout:
    return Layout(
        total=3,
        volumes=[Volume(id="v1", title="测试卷", sub="第2436章")],
        pages=[
            LayoutPage(page=1, type=PageType.TITLE, title="测试标题",
                       volume="v1"),
            LayoutPage(page=2, type=PageType.PANELS, panels=["ch2436_P001"]),
            LayoutPage(page=3, type=PageType.PANELS, panels=["ch2436_P002"]),
        ],
    )


def make_project() -> ComicProject:
    return ComicProject(
        name="测试项目",
        bible=make_bible(),
        storyboards=[make_storyboard()],
        dialogue=make_dialogue(),
        layout=make_layout(),
    )


def clone(p: ComicProject) -> ComicProject:
    return copy.deepcopy(p)
