# -*- coding: utf-8 -*-
"""Comic IR 校验器测试

策略：
    ① 先证明「正确样例能通过」——否则校验器可能只是乱报错
    ② 再对 12 条规则逐一破坏，断言对应规则被触发
"""

from __future__ import annotations

import pytest

from packages.ir import (
    Box,
    BubbleSpec,
    BubbleStyle,
    CastMember,
    Character,
    Appearance,
    DamageLevel,
    Injury,
    LayoutPage,
    PageType,
    Panel,
    PanelSize,
    ShotSize,
    Storyboard,
    Utterance,
    validate,
)
from tests.fixtures import SOURCE_LINES, clone, make_project


# ══════════════════════════════════════════════════════════════════
# 基线
# ══════════════════════════════════════════════════════════════════

def test_valid_project_passes():
    """正确样例必须零错误"""
    report = validate(make_project(), source_lines=SOURCE_LINES)
    assert report.ok, "正确样例被误判：\n" + "\n".join(str(e) for e in report.errors)


def test_report_summary_and_feedback():
    report = validate(make_project(), source_lines=SOURCE_LINES)
    assert isinstance(report.summary(), str)
    # 没有错误时 feedback 为空
    assert report.to_feedback() == ""


# ══════════════════════════════════════════════════════════════════
# IR-001 引用完整性
# ══════════════════════════════════════════════════════════════════

def test_ir001_unknown_character():
    p = clone(make_project())
    p.storyboards[0].panels[0].cast.append(CastMember(id="no_such_person", pos="right"))
    r = validate(p, SOURCE_LINES)
    assert not r.ok
    assert "IR-001" in r.rules_hit()


def test_ir001_unknown_skill():
    p = clone(make_project())
    p.storyboards[0].panels[0].skill = "no_such_skill"
    r = validate(p, SOURCE_LINES)
    assert "IR-001" in r.rules_hit()


def test_ir001_unknown_scene():
    p = clone(make_project())
    p.storyboards[0].panels[0].background = "no_such_scene"
    r = validate(p, SOURCE_LINES)
    assert "IR-001" in r.rules_hit()


def test_ir001_undefined_variant():
    p = clone(make_project())
    p.storyboards[0].panels[0].cast[0].variant = "fire_form"
    r = validate(p, SOURCE_LINES)
    assert "IR-001" in r.rules_hit()


# ══════════════════════════════════════════════════════════════════
# IR-002 距离必填
# ══════════════════════════════════════════════════════════════════

def test_ir002_missing_distance():
    p = clone(make_project())
    p.storyboards[0].panels[0].distance = None
    r = validate(p, SOURCE_LINES)
    assert "IR-002" in r.rules_hit()


def test_ir002_vague_distance_warns():
    p = clone(make_project())
    p.storyboards[0].panels[0].distance = "近"
    r = validate(p, SOURCE_LINES)
    assert "IR-002" in r.rules_hit()


# ══════════════════════════════════════════════════════════════════
# IR-003 表情重复
# ══════════════════════════════════════════════════════════════════

def test_ir003_same_emotion_twice():
    p = clone(make_project())
    p.storyboards[0].panels[1].emotion_hint = {"mu_ningxue": "冷淡无波"}  # 与 P001 相同
    r = validate(p, SOURCE_LINES)
    assert "IR-003" in r.rules_hit()


# ══════════════════════════════════════════════════════════════════
# IR-004 损伤回落
# ══════════════════════════════════════════════════════════════════

def test_ir004_damage_regression():
    p = clone(make_project())
    p.storyboards[0].panels[0].damage_level = DamageLevel.L3
    p.storyboards[0].panels[1].damage_level = DamageLevel.L1   # 无故回落
    r = validate(p, SOURCE_LINES)
    assert "IR-004" in r.rules_hit()


# ══════════════════════════════════════════════════════════════════
# IR-005 伤情闭环
# ══════════════════════════════════════════════════════════════════

def test_ir005_injury_without_since():
    p = clone(make_project())
    p.bible.characters["mu_ningxue"].state.injuries[0].since = ""
    r = validate(p, SOURCE_LINES)
    assert "IR-005" in r.rules_hit()


def test_ir005_healed_without_at():
    p = clone(make_project())
    inj = p.bible.characters["mu_ningxue"].state.injuries[0]
    inj.healed = True
    inj.healed_at = None
    r = validate(p, SOURCE_LINES)
    assert "IR-005" in r.rules_hit()


# ══════════════════════════════════════════════════════════════════
# IR-006 对白引用与确认
# ══════════════════════════════════════════════════════════════════

def test_ir006_unconfirmed_speaker_blocks_render():
    p = clone(make_project())
    p.dialogue.items["ch2436_P001"][0].confirmed = False
    r = validate(p, SOURCE_LINES)
    assert not r.ok
    assert "IR-006" in r.rules_hit()


def test_ir006_speaker_not_in_bible():
    p = clone(make_project())
    p.dialogue.items["ch2436_P001"][0].who = "ghost"
    r = validate(p, SOURCE_LINES)
    assert "IR-006" in r.rules_hit()


def test_ir006_dialogue_on_unknown_panel():
    p = clone(make_project())
    p.dialogue.items["ch9999_P999"] = [
        Utterance(id="x1", who="mu_ningxue", text="测试", confirmed=True)
    ]
    r = validate(p, SOURCE_LINES)
    assert "IR-006" in r.rules_hit()


# ══════════════════════════════════════════════════════════════════
# IR-007 对白可追溯（防编造）
# ══════════════════════════════════════════════════════════════════

def test_ir007_fabricated_line_is_caught():
    p = clone(make_project())
    p.dialogue.items["ch2436_P001"][0].text = "这句话原文里根本没有。"
    r = validate(p, SOURCE_LINES)
    assert not r.ok
    assert "IR-007" in r.rules_hit()


def test_ir007_missing_span():
    p = clone(make_project())
    p.dialogue.items["ch2436_P001"][0].source_span = None
    r = validate(p, SOURCE_LINES)
    assert "IR-007" in r.rules_hit()


def test_ir007_span_out_of_range():
    p = clone(make_project())
    p.dialogue.items["ch2436_P001"][0].source_span = [900, 999]
    r = validate(p, SOURCE_LINES)
    assert "IR-007" in r.rules_hit()


# ══════════════════════════════════════════════════════════════════
# IR-008 气泡坐标
# ══════════════════════════════════════════════════════════════════

def test_ir008_tail_overlaps_box():
    p = clone(make_project())
    u = p.dialogue.items["ch2436_P001"][0]
    u.bubble.box = Box(x=0.50, y=0.50)
    u.bubble.tail = Box(x=0.50, y=0.50)
    r = validate(p, SOURCE_LINES)
    assert "IR-008" in r.rules_hit()


# ══════════════════════════════════════════════════════════════════
# IR-009 / IR-010 页序
# ══════════════════════════════════════════════════════════════════

def test_ir009_panel_referenced_twice():
    p = clone(make_project())
    p.layout.pages[2] = LayoutPage(
        page=3, type=PageType.PANELS, panels=["ch2436_P001"])
    r = validate(p, SOURCE_LINES)
    assert "IR-009" in r.rules_hit()


def test_ir009_unknown_panel_in_layout():
    p = clone(make_project())
    p.layout.pages[1] = LayoutPage(
        page=2, type=PageType.PANELS, panels=["ch9999_P001"])
    r = validate(p, SOURCE_LINES)
    assert "IR-009" in r.rules_hit()


def test_ir010_panel_not_placed():
    p = clone(make_project())
    p.storyboards[0].panels.append(Panel(
        id="ch2436_P003", seq=3,
        size=PanelSize.WIDE, shot=ShotSize.FULL,
        cast=[CastMember(id="mu_ningxue", pos="center")],
        action="穆宁雪转身离开",
    ))
    r = validate(p, SOURCE_LINES)
    assert "IR-010" in r.rules_hit()


# ══════════════════════════════════════════════════════════════════
# IR-011 说话人是否在场
# ══════════════════════════════════════════════════════════════════

def test_ir011_speaker_not_in_cast():
    p = clone(make_project())
    # P002 的 cast 只有穆宁雪，却让穆飞鸾说话
    p.dialogue.items["ch2436_P002"] = [
        Utterance(id="ch2436_P002_b1", who="mu_feiluan", text="解决掉他们。",
                  source_span=[7, 7], confirmed=True)
    ]
    r = validate(p, SOURCE_LINES)
    assert "IR-011" in r.rules_hit()


def test_ir011_narration_is_allowed():
    """画外音（narration）不算违规"""
    p = clone(make_project())
    p.dialogue.items["ch2436_P002"] = [
        Utterance(id="ch2436_P002_b1", who="mu_feiluan", text="解决掉他们。",
                  source_span=[7, 7], confirmed=True,
                  bubble=BubbleSpec(style=BubbleStyle.NARRATION))
    ]
    r = validate(p, SOURCE_LINES)
    assert "IR-011" not in r.rules_hit()


# ══════════════════════════════════════════════════════════════════
# IR-012 景别单调
# ══════════════════════════════════════════════════════════════════

def test_ir012_four_same_shots_in_a_row():
    p = clone(make_project())
    sb = Storyboard(chapter="2437", panels=[
        Panel(id=f"ch2437_P{i:03d}", seq=i, size=PanelSize.WIDE,
              shot=ShotSize.CLOSE,
              cast=[CastMember(id="mu_ningxue", pos="center")],
              action=f"动作 {i}")
        for i in range(1, 5)
    ])
    p.storyboards.append(sb)
    for i in range(1, 5):
        p.layout.pages.append(LayoutPage(
            page=p.layout.total + i, type=PageType.PANELS,
            panels=[f"ch2437_P{i:03d}"]))
    p.layout.total += 4
    r = validate(p, SOURCE_LINES)
    assert "IR-012" in r.rules_hit()


# ══════════════════════════════════════════════════════════════════
# 反馈文本（供 Agent 自修复使用）
# ══════════════════════════════════════════════════════════════════

def test_feedback_text_is_actionable():
    p = clone(make_project())
    p.storyboards[0].panels[0].distance = None
    p.dialogue.items["ch2436_P001"][0].confirmed = False
    r = validate(p, SOURCE_LINES)
    fb = r.to_feedback()
    assert "IR-002" in fb and "IR-006" in fb
    assert "修正方式" in fb
