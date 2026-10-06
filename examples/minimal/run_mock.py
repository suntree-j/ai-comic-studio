# -*- coding: utf-8 -*-
"""用 MockProvider 离线跑通全流程（不需要 API key）

演示：
    ① 章节切分 → ② 分镜 → ③ 对白 → ④ 状态追踪
    并展示自修复循环：第 1 轮故意给缺陷输出，第 2 轮修正

运行：
    python examples/minimal/run_mock.py
"""
import io, json, os, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from packages.agent import ComicPipeline, MockProvider, PipelineOptions
from packages.ir import Bible, validate

HERE = os.path.dirname(os.path.abspath(__file__))
bible = Bible.model_validate_json(
    open(os.path.join(HERE, 'bible.json'), encoding='utf-8').read())
text = open(os.path.join(HERE, 'source.txt'), encoding='utf-8').read()

# ── Mock 脚本 ────────────────────────────────────────────────
CHAPTER = json.dumps({
    "chapters": [{
        "id": "2436", "title": "冰晶刹弓",
        "start_line": 1, "end_line": 12,
        "summary": "穆宁雪一箭钉住南荣倪，穆飞鸾与穆隐凤登场",
        "dramatic_beat": "对决开场",
    }]
}, ensure_ascii=False)

# ★ 第 1 版故意有缺陷：两人同框却没写距离（触发 IR-002）
STORYBOARD_BAD = json.dumps({
    "chapter": "2436",
    "panels": [{
        "id": "ch2436_P001", "seq": 1,
        "size": "2048x1400", "shot": "大全景",
        "cast": [{"id": "mu_ningxue", "pos": "left"},
                 {"id": "mu_feiluan", "pos": "right"}],
        "action": "穆宁雪立于冰玻璃长道拉弓",
        "source_span": [3, 6],
    }],
}, ensure_ascii=False)

STORYBOARD_GOOD = json.dumps({
    "chapter": "2436",
    "panels": [
        {
            "id": "ch2436_P001", "seq": 1,
            "size": "2048x1400", "shot": "大全景",
            "cast": [{"id": "mu_ningxue", "pos": "left", "variant": "battle"},
                     {"id": "mu_feiluan", "pos": "far_right"}],
            "distance": "两人相距约 8 米，中间是大片空旷的冰玻璃长道",
            "action": "穆宁雪立于冰玻璃长道拉弓，冰晶刹弓在掌心凝聚成形，"
                      "箭矢离弦射向画面另一端的南荣倪",
            "skill": "ice_bow",
            "background": "ice_corridor",
            "mood": "肃杀，细雪横飞",
            "damage_level": "L0",
            "emotion_hint": {"mu_ningxue": "冷淡无波", "mu_feiluan": "倨傲"},
            "source_span": [3, 6],
        },
        {
            "id": "ch2436_P002", "seq": 2,
            "size": "1332x1776", "shot": "近景",
            "cast": [{"id": "mu_ningxue", "pos": "center"}],
            "action": "穆宁雪冷眸直视前方，冰晶刹弓被拉成满弧，霜气自弓身四散",
            "skill": "ice_bow",
            "background": "ice_corridor",
            "damage_level": "L0",
            "emotion_hint": {"mu_ningxue": "锁定目标"},
            "source_span": [6, 6],
        },
    ],
}, ensure_ascii=False)

DIALOGUE = json.dumps({
    "items": {
        "ch2436_P001": [
            {
                "id": "ch2436_P001_b1", "who": "mu_ningxue",
                "text": "你喜欢这柄冰晶刹弓，作为多年的朋友，我自然亲手赠你。",
                "source_span": [6, 6], "confirmed": True,
                "bubble": {"style": "speech",
                           "box": {"x": 0.60, "y": 0.03},
                           "tail": {"x": 0.30, "y": 0.45}},
                "locked": True,
            },
            {
                # 穆飞鸾在同一格出场，所以挂这里（若挂到 P002 会触发 IR-011）
                "id": "ch2436_P001_b2", "who": "mu_feiluan",
                "text": "解决掉他们。",
                "source_span": [12, 12], "confirmed": True,
                "bubble": {"style": "speech",
                           "box": {"x": 0.02, "y": 0.62},
                           "tail": {"x": 0.72, "y": 0.50}},
                "locked": True,
            },
        ],
    }
}, ensure_ascii=False)

STATE = json.dumps({"new_wounds": [], "healed": []}, ensure_ascii=False)

provider = MockProvider([CHAPTER, STORYBOARD_BAD, STORYBOARD_GOOD,
                         DIALOGUE, STATE])

print("=" * 62)
print(" AI Comic Studio · 离线演示（MockProvider，无需 API key）")
print("=" * 62)

pipe = ComicPipeline(provider, bible, PipelineOptions(
    panel_count_hint=2, max_repair_rounds=3, verbose=True))

project = pipe.run(text, project_name="离线演示", chapters=1)

print()
print("─" * 62)
print(" 结果")
print("─" * 62)
print(f" 分镜：{len(project.storyboards)} 章 / "
      f"{sum(len(s.panels) for s in project.storyboards)} 格")
print(f" 对白：{sum(len(v) for v in project.dialogue.items.values())} 条")
print(f" 页数：{project.layout.total}")
print()
print(" 各章状态：")
for r in getattr(project, '_chapter_results', []):
    icon = {"ok": "OK ", "repaired": "FIX", "pending_human": "WAIT"}.get(r.status, "?")
    print(f"   [{icon}] 第{r.chapter_id}章《{r.title}》  {r.status}  "
          f"{r.attempts} 轮")
    for h in r.history:
        print(f"           · {h}")

# 项目级校验
report = validate(project, text.splitlines())
print()
print("─" * 62)
print(" 项目级校验")
print("─" * 62)
print(" " + report.summary())
if not report.ok:
    for e in report.errors[:5]:
        print(f"   {e}")
