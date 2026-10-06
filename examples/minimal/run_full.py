# -*- coding: utf-8 -*-
"""端到端演示：小说 → Comic IR → 漫画 PDF

全程离线（MockProvider + MockImageProvider），不需要任何 API key。

运行：
    python examples/minimal/run_full.py
输出：
    examples/minimal/out/comic.pdf
    examples/minimal/out/长图_1.jpg …
"""
import io, json, os, shutil, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from packages.agent import ComicPipeline, MockProvider, PipelineOptions
from packages.ir import Bible, validate
from packages.render import MockImageProvider, RenderStudio

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')

bible = Bible.model_validate_json(
    open(os.path.join(HERE, 'bible.json'), encoding='utf-8').read())
text = open(os.path.join(HERE, 'source.txt'), encoding='utf-8').read()

# ── Mock LLM 脚本 ────────────────────────────────────────────
CHAPTER = json.dumps({"chapters": [{
    "id": "2436", "title": "冰晶刹弓", "start_line": 1, "end_line": 12,
    "summary": "穆宁雪一箭钉住南荣倪", "dramatic_beat": "对决开场"}]},
    ensure_ascii=False)

STORYBOARD_BAD = json.dumps({"chapter": "2436", "panels": [{
    "id": "ch2436_P001", "seq": 1, "size": "2048x1400", "shot": "大全景",
    "cast": [{"id": "mu_ningxue", "pos": "left"},
             {"id": "mu_feiluan", "pos": "right"}],
    "action": "穆宁雪立于冰玻璃长道拉弓",          # ← 故意不写 distance（触发 IR-002）
    "source_span": [3, 6]}]}, ensure_ascii=False)

STORYBOARD_GOOD = json.dumps({"chapter": "2436", "panels": [
    {
        "id": "ch2436_P001", "seq": 1, "size": "2048x1400", "shot": "大全景",
        "cast": [{"id": "mu_ningxue", "pos": "left", "variant": "battle"},
                 {"id": "mu_feiluan", "pos": "far_right"}],
        "distance": "两人相距约 8 米，中间是大片空旷的冰玻璃长道",
        "action": "穆宁雪立于冰玻璃长道拉弓，冰晶刹弓在掌心凝聚成形，箭矢离弦",
        "skill": "ice_bow", "background": "ice_corridor", "mood": "肃杀，细雪横飞",
        "damage_level": "L0",
        "emotion_hint": {"mu_ningxue": "冷淡无波", "mu_feiluan": "倨傲"},
        "source_span": [3, 6],
    },
    {
        "id": "ch2436_P002", "seq": 2, "size": "1332x1776", "shot": "近景",
        "cast": [{"id": "mu_ningxue", "pos": "center"}],
        "action": "穆宁雪冷眸直视前方，冰晶刹弓被拉成满弧，霜气自弓身四散",
        "skill": "ice_bow", "background": "ice_corridor", "damage_level": "L0",
        "emotion_hint": {"mu_ningxue": "锁定目标"},
        "source_span": [6, 6],
    },
]}, ensure_ascii=False)

DIALOGUE = json.dumps({"items": {"ch2436_P001": [
    {"id": "ch2436_P001_b1", "who": "mu_ningxue",
     "text": "你喜欢这柄冰晶刹弓，作为多年的朋友，我自然亲手赠你。",
     "source_span": [6, 6], "confirmed": True,
     "bubble": {"style": "speech"}, "locked": False},
    {"id": "ch2436_P001_b2", "who": "mu_feiluan",
     "text": "解决掉他们。",
     "source_span": [12, 12], "confirmed": True,
     "bubble": {"style": "speech"}, "locked": False},
]}}, ensure_ascii=False)

STATE = json.dumps({"new_wounds": [], "healed": []}, ensure_ascii=False)

# ── 跑 ───────────────────────────────────────────────────────
print("=" * 64)
print(" AI Comic Studio · 端到端演示（完全离线）")
print("=" * 64)

if os.path.isdir(OUT):
    shutil.rmtree(OUT)
os.makedirs(OUT, exist_ok=True)

print("\n【阶段 1】小说 → Comic IR")
print("-" * 64)
provider = MockProvider([CHAPTER, STORYBOARD_BAD, STORYBOARD_GOOD, DIALOGUE, STATE])
pipe = ComicPipeline(provider, bible, PipelineOptions(
    panel_count_hint=2, max_repair_rounds=3, verbose=True))
project = pipe.run(text, project_name="离线演示", chapters=1)

for r in getattr(project, '_chapter_results', []):
    print(f"   章节状态：{r.status}（{r.attempts} 轮）")

print("\n【阶段 2】IR 校验")
print("-" * 64)
report = validate(project, text.splitlines())
print("   " + report.summary())

print("\n【阶段 3】渲染 → PDF")
print("-" * 64)
studio = RenderStudio(MockImageProvider(), panel_width=900)
rep = studio.render(
    project,
    os.path.join(OUT, 'assets'),
    os.path.join(OUT, 'comic.pdf'),
    on_progress=lambda stage, i, t, m: print(f"   [{stage}] {i}/{t} {m}"),
    long_images=2,
    long_outdir=os.path.join(OUT, 'longs'),
)

print()
print("=" * 64)
print(" 结果")
print("=" * 64)
print("  " + rep.summary())
for f in sorted(os.listdir(OUT)):
    p = os.path.join(OUT, f)
    if os.path.isfile(p):
        print(f"  📄 {f}  {os.path.getsize(p) / 1024:.0f} KB")
longs = os.path.join(OUT, 'longs')
if os.path.isdir(longs):
    for f in sorted(os.listdir(longs)):
        p = os.path.join(longs, f)
        print(f"  🖼 longs/{f}  {os.path.getsize(p) / 1024:.0f} KB")
print()
print("  ✅ 全流程离线跑通：小说 → IR → 校验 → 渲染 → PDF")
