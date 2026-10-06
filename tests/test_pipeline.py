# -*- coding: utf-8 -*-
"""测试端到端 Pipeline（用 MockProvider 离线跑通全流程）"""

from __future__ import annotations

import json

from packages.agent import ComicPipeline, MockProvider, PipelineOptions, adapt_novel
from packages.ir import Layout, PageType, validate
from tests.fixtures import SOURCE_LINES, make_bible


def _chapter_json():
    return json.dumps({
        "chapters": [{
            "id": "2436", "title": "冰晶刹弓",
            "start_line": 1, "end_line": len(SOURCE_LINES),
            "summary": "穆宁雪一箭钉住南荣倪",
            "dramatic_beat": "对决开场",
        }]
    }, ensure_ascii=False)


def _storyboard_json():
    return json.dumps({
        "chapter": "2436",
        "panels": [
            {
                "id": "ch2436_P001", "seq": 1,
                "size": "2048x1400", "shot": "大全景",
                "cast": [{"id": "mu_ningxue", "pos": "left"},
                         {"id": "mu_feiluan", "pos": "right"}],
                "distance": "两人相距约 8 米，中间是大片空旷冰道",
                "action": "穆宁雪立于冰玻璃长道拉弓，箭矢离弦",
                "emotion_hint": {"mu_ningxue": "冷淡无波", "mu_feiluan": "倨傲"},
                "source_span": [3, 4],
            },
            {
                "id": "ch2436_P002", "seq": 2,
                "size": "1332x1776", "shot": "近景",
                "cast": [{"id": "mu_ningxue", "pos": "center"}],
                "action": "穆宁雪冷眸直视前方，冰晶刹弓拉成满弧",
                "emotion_hint": {"mu_ningxue": "锁定目标"},
                "source_span": [4, 4],
            },
        ],
    }, ensure_ascii=False)


def _dialogue_json():
    return json.dumps({
        "items": {
            "ch2436_P001": [{
                "id": "ch2436_P001_b1", "who": "mu_ningxue",
                "text": "你喜欢这柄冰晶刹弓，作为多年的朋友，我自然亲手赠你。",
                "source_span": [4, 4], "confirmed": True,
                "bubble": {"style": "speech"},
            }]
        }
    }, ensure_ascii=False)


def _state_json():
    return json.dumps({"new_wounds": [], "healed": []}, ensure_ascii=False)


def test_pipeline_end_to_end_with_mock():
    """章节切分 → 分镜 → 对白 → 状态，全流程离线跑通"""
    bible = make_bible()
    text = "\n".join(SOURCE_LINES)
    provider = MockProvider([
        _chapter_json(),      # Skill 1
        _storyboard_json(),   # Skill 2
        _dialogue_json(),     # Skill 3
        _state_json(),        # Skill 4
    ])

    project = adapt_novel(
        text, bible, provider,
        project_name="测试项目", chapters=1,
        options=PipelineOptions(panel_count_hint=2, verbose=False),
    )

    assert project.name == "测试项目"
    assert len(project.storyboards) == 1
    assert len(project.storyboards[0].panels) == 2
    assert "ch2436_P001" in project.dialogue.items
    assert isinstance(project.layout, Layout)


def test_pipeline_layout_freezes_page_numbers():
    """自动排版：页码连续、卷首页正确、每格都有归属"""
    bible = make_bible()
    text = "\n".join(SOURCE_LINES)
    provider = MockProvider([
        _chapter_json(), _storyboard_json(), _dialogue_json(), _state_json(),
    ])
    project = adapt_novel(text, bible, provider,
                          options=PipelineOptions(verbose=False))

    pages = project.layout.pages
    assert [p.page for p in pages] == list(range(1, len(pages) + 1))
    assert pages[0].type is PageType.TITLE
    placed = {pid for p in pages for pid in p.panels}
    for sb in project.storyboards:
        for p in sb.panels:
            assert p.id in placed


def test_pipeline_records_chapter_results():
    bible = make_bible()
    text = "\n".join(SOURCE_LINES)
    provider = MockProvider([
        _chapter_json(), _storyboard_json(), _dialogue_json(), _state_json(),
    ])
    project = adapt_novel(text, bible, provider,
                          options=PipelineOptions(verbose=False))
    results = project._chapter_results           # type: ignore[attr-defined]
    assert len(results) == 1
    assert results[0].status in ("ok", "repaired")
    assert results[0].storyboard is not None


def test_pipeline_marks_pending_when_unrepairable():
    """分镜三轮都修不好 → 该章标记 pending_human，不阻塞其他部分"""
    bible = make_bible()
    text = "\n".join(SOURCE_LINES)
    bad = json.dumps({
        "chapter": "2436",
        "panels": [{
            "id": "ch2436_P001", "seq": 1,
            "size": "2048x1400", "shot": "大全景",
            "cast": [{"id": "mu_ningxue", "pos": "left"},
                     {"id": "mu_feiluan", "pos": "right"}],
            "action": "测试",
            # 故意不给 distance → IR-002 一直报错
        }],
    }, ensure_ascii=False)
    provider = MockProvider([_chapter_json(), bad, bad, bad])

    project = adapt_novel(text, bible, provider,
                          options=PipelineOptions(max_repair_rounds=3,
                                                  verbose=False))
    results = project._chapter_results           # type: ignore[attr-defined]
    assert results[0].status == "pending_human"
    assert results[0].storyboard is None


def test_pipeline_uses_bible_for_consistency():
    """生成的提示词里必须包含角色 id 与硬规则（保证一致性靠 bible 而非模型记忆）"""
    bible = make_bible()
    text = "\n".join(SOURCE_LINES)
    provider = MockProvider([
        _chapter_json(), _storyboard_json(), _dialogue_json(), _state_json(),
    ])
    adapt_novel(text, bible, provider,
                options=PipelineOptions(verbose=False))

    # 第 3 次调用是分镜（第1次章节切分，第2次分镜…）
    sent = " ".join(m.content for m in provider.calls[1])
    assert "mu_ningxue" in sent          # 角色表
    assert "ice_corridor" in sent        # 场景表
    assert "距离" in sent                 # 硬规则
