# -*- coding: utf-8 -*-
"""Agent 层测试

重点验证 **自修复循环** —— 这是「AI 改漫」区别于「一次性生成」的关键能力。

用 MockProvider 编排脚本：
    第 1 次返回有缺陷的 JSON → 校验失败 → 回喂错误
    第 2 次返回修正后的 JSON → 校验通过
并断言：最终结果正确、尝试轮次为 2、反馈文本确实被送进了模型。
"""

from __future__ import annotations

import json

import pytest

from packages.agent import (
    MockProvider,
    extract_json,
    generate_storyboard,
    make_dialogue_validator,
    make_panel_validator,
    number_lines,
    repair_until_valid,
    apply_state_delta,
    StateDelta,
)
from packages.agent.providers import JSONParseError, LLMError, parse_as
from packages.ir import Bible, Storyboard, DialogueBook
from packages.ir.validator import Severity
from tests.fixtures import SOURCE_LINES, make_bible, make_storyboard


# ══════════════════════════════════════════════════════════════════
# JSON 提取
# ══════════════════════════════════════════════════════════════════

def test_extract_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_from_markdown_fence():
    text = '这是结果：\n```json\n{"a": 1}\n```\n完成'
    assert extract_json(text) == {"a": 1}


def test_extract_from_prose():
    text = '好的，我生成了如下内容 {"a": 1, "b": [2, 3]} 希望有帮助'
    assert extract_json(text) == {"a": 1, "b": [2, 3]}


def test_extract_empty_raises():
    with pytest.raises(JSONParseError):
        extract_json("")


def test_extract_no_json_raises():
    with pytest.raises(JSONParseError):
        extract_json("这句话里完全没有 JSON")


def test_number_lines():
    out = number_lines("第一行\n第二行", start=1)
    assert "1| 第一行" in out
    assert "2| 第二行" in out


# ══════════════════════════════════════════════════════════════════
# ★ 自修复循环
# ══════════════════════════════════════════════════════════════════

def _bad_storyboard_json() -> str:
    """一份有缺陷的分镜：两人同框但没写距离 + 引用了不存在的角色"""
    return json.dumps({
        "chapter": "2436",
        "panels": [{
            "id": "ch2436_P001", "seq": 1,
            "size": "2048x1400", "shot": "大全景",
            "cast": [{"id": "mu_ningxue", "pos": "left"},
                     {"id": "ghost_person", "pos": "right"}],
            "distance": None,
            "action": "穆宁雪站在冰道上",
            "source_span": [3, 4],
        }],
    }, ensure_ascii=False)


def _good_storyboard_json() -> str:
    return json.dumps({
        "chapter": "2436",
        "panels": [{
            "id": "ch2436_P001", "seq": 1,
            "size": "2048x1400", "shot": "大全景",
            "cast": [{"id": "mu_ningxue", "pos": "left"},
                     {"id": "mu_feiluan", "pos": "right"}],
            "distance": "两人相距约 8 米，中间是大片空旷冰道",
            "action": "穆宁雪立于冰玻璃长道拉弓，箭矢离弦射向画面另一端",
            "emotion_hint": {"mu_ningxue": "冷淡无波"},
            "source_span": [3, 4],
        }],
    }, ensure_ascii=False)


def test_repair_loop_fixes_on_second_try():
    """第 1 轮有缺陷 → 第 2 轮修正 → 整体成功"""
    bible = make_bible()
    provider = MockProvider([_bad_storyboard_json(), _good_storyboard_json()])

    result = repair_until_valid(
        provider, Storyboard,
        system="你是分镜师",
        user="生成分镜",
        validator=make_panel_validator(bible),
        max_rounds=3,
    )

    assert result.ok
    assert result.attempts == 2
    assert result.status == "repaired_in_2"
    assert result.value is not None
    assert result.value.panels[0].distance


def test_repair_loop_feeds_errors_back_to_model():
    """断言错误信息确实被送回了模型（而不是盲目重试）"""
    bible = make_bible()
    provider = MockProvider([_bad_storyboard_json(), _good_storyboard_json()])

    repair_until_valid(
        provider, Storyboard, system="s", user="u",
        validator=make_panel_validator(bible),
    )

    assert len(provider.calls) == 2
    second_round = " ".join(m.content for m in provider.calls[1])
    # 第二轮应当包含规则编号与修正建议
    assert "IR-002" in second_round or "IR-001" in second_round
    assert "修正方式" in second_round
    # 并且应当带上上一次的错误输出，便于模型对比
    assert "ghost_person" in second_round


def test_repair_loop_gives_up_and_returns_pending():
    """一直修不好 → 返回 pending_human，而不是抛异常或硬渲染"""
    bible = make_bible()
    provider = MockProvider([_bad_storyboard_json()] * 3)

    result = repair_until_valid(
        provider, Storyboard, system="s", user="u",
        validator=make_panel_validator(bible), max_rounds=3,
    )

    assert not result.ok
    assert result.status == "pending_human"
    assert result.value is None
    assert result.pending is not None          # 保留最后一次产物供人工改
    assert result.final_report is not None
    assert result.final_report.rules_hit()


def test_repair_loop_handles_unparseable_output():
    """模型吐出的不是 JSON → 重试而不是崩溃"""
    bible = make_bible()
    provider = MockProvider(["这不是 JSON", _good_storyboard_json()])

    result = repair_until_valid(
        provider, Storyboard, system="s", user="u",
        validator=make_panel_validator(bible), max_rounds=3,
    )

    assert result.ok
    assert result.attempts == 2
    assert "解析" in result.history[0] or "失败" in result.history[0]


def test_repair_loop_passes_first_time():
    bible = make_bible()
    provider = MockProvider([_good_storyboard_json()])
    result = repair_until_valid(
        provider, Storyboard, system="s", user="u",
        validator=make_panel_validator(bible),
    )
    assert result.ok
    assert result.attempts == 1
    assert result.status == "ok"
    assert len(provider.calls) == 1


# ══════════════════════════════════════════════════════════════════
# Skill 2 · 分镜生成（走 provider.structured）
# ══════════════════════════════════════════════════════════════════

def test_generate_storyboard_via_mock():
    bible = make_bible()
    provider = MockProvider([_good_storyboard_json()])
    sb = generate_storyboard(provider, bible, "冰晶刹弓",
                             "穆宁雪站在冰玻璃长道的一端。", panel_count_hint=1)
    assert isinstance(sb, Storyboard)
    assert sb.chapter == "2436"
    # 提示词里应当包含角色表与硬规则
    sent = " ".join(m.content for m in provider.calls[0])
    assert "mu_ningxue" in sent
    assert "规则 2" in sent or "距离" in sent


# ══════════════════════════════════════════════════════════════════
# Skill 3 · 对白校验
# ══════════════════════════════════════════════════════════════════

def test_dialogue_validator_catches_fabrication():
    bible = make_bible()
    sb = make_storyboard()
    dlg = DialogueBook(items={
        "ch2436_P001": [{
            "id": "b1", "who": "mu_ningxue",
            "text": "原文里根本没有这句话。",
            "source_span": [4, 4], "confirmed": True,
        }]
    })
    report = make_dialogue_validator(bible, sb, SOURCE_LINES)(dlg)
    assert "IR-007" in report.rules_hit()


def test_dialogue_validator_accepts_faithful_quote():
    bible = make_bible()
    sb = make_storyboard()
    dlg = DialogueBook(items={
        "ch2436_P001": [{
            "id": "b1", "who": "mu_ningxue",
            "text": "你喜欢这柄冰晶刹弓，作为多年的朋友，我自然亲手赠你。",
            "source_span": [4, 4], "confirmed": True,
        }]
    })
    report = make_dialogue_validator(bible, sb, SOURCE_LINES)(dlg)
    assert report.ok


# ══════════════════════════════════════════════════════════════════
# Skill 4 · 状态追踪
# ══════════════════════════════════════════════════════════════════

def test_apply_state_delta_adds_wound():
    bible = make_bible()
    delta = StateDelta(
        new_wounds=[StateDelta.Wound(
            character="mo_fan", part="左肩", kind="撕裂伤",
            desc="卫衣左肩被撕开一道口子，露出里面的白衬衫",
            panel="ch2437_P003")],
    )
    b2 = apply_state_delta(bible, delta)
    wounds = b2.characters["mo_fan"].state.active_injuries()
    assert len(wounds) == 1
    assert wounds[0].part == "左肩"
    # 原对象不被修改（纯函数）
    assert bible.characters["mo_fan"].state.active_injuries() == []


def test_apply_state_delta_no_duplicate():
    bible = make_bible()
    delta = StateDelta(new_wounds=[
        StateDelta.Wound(character="mu_ningxue", part="左肩→右腰",
                         kind="霜白伤口", desc="重复记录", panel="ch2436_P005"),
    ])
    b2 = apply_state_delta(bible, delta)
    # 已有的同部位伤不应被重复添加
    assert len(b2.characters["mu_ningxue"].state.injuries) == 1


def test_apply_state_delta_heals():
    bible = make_bible()
    delta = StateDelta(healed=[
        StateDelta.Heal(character="mu_ningxue", part="左肩→右腰",
                        panel="ch2436_P009"),
    ])
    b2 = apply_state_delta(bible, delta)
    inj = b2.characters["mu_ningxue"].state.injuries[0]
    assert inj.healed is True
    assert inj.healed_at == "ch2436_P009"
    assert b2.characters["mu_ningxue"].state.active_injuries() == []


# ══════════════════════════════════════════════════════════════════
# Provider 工厂
# ══════════════════════════════════════════════════════════════════

def test_get_provider_unknown_raises():
    from packages.agent import get_provider
    with pytest.raises(LLMError):
        get_provider("nonexistent_vendor")


def test_get_provider_mock():
    from packages.agent import get_provider
    p = get_provider("mock")
    assert p.name == "mock"


def test_mock_provider_exhausted():
    p = MockProvider([])
    with pytest.raises(LLMError):
        p.chat([])
