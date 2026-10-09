# -*- coding: utf-8 -*-
"""AI 改漫 Agent 的提示词模板

设计原则：
    ① 【规则前置】把硬约束写在最前面，模型才不容易忘
    ② 【引用而非内联】让模型输出 bible 的 id，而不是重新描述外观
    ③ 【反例】明确写出「不要做什么」，比只写「要做什么」有效
    ④ 【可追溯】要求输出 source_span，便于校验器回原文核对
"""

from __future__ import annotations

from typing import Sequence

# ══════════════════════════════════════════════════════════════════
# 通用片段
# ══════════════════════════════════════════════════════════════════

JSON_ONLY = (
    "只输出一个合法 JSON 对象。不要任何解释文字，不要 markdown 代码块围栏。"
)

NO_FABRICATION = (
    "【最高原则】只能使用用户提供的内容。\n"
    "绝对不要编造原文里没有的台词、人物、场景或设定。\n"
    "对白必须逐字摘录原文，一个字都不能改。"
)


# ══════════════════════════════════════════════════════════════════
# Skill 1 · 章节切分
# ══════════════════════════════════════════════════════════════════

CHAPTER_SPLIT_SYSTEM = """你是一位漫画编剧，负责把长篇小说切分成适合改编成漫画的章节单元。

{json_only}

切分依据（按优先级）：
1. 场景转换 —— 地点或时间的明显跳跃
2. 戏剧单元完整性 —— 每一章应有一个完整的小冲突或情绪弧
3. 篇幅均衡 —— 每章大致相当（可以 ±30%）
4. 原文已有的章节标题/分隔符（若有则必须尊重）

输出 JSON：
{{
  "chapters": [
    {{
      "id": "2436",
      "title": "冰晶刹弓",
      "start_line": 1,
      "end_line": 120,
      "summary": "一句话概括本章发生了什么",
      "dramatic_beat": "本章的戏剧核心（如「对决开场」「身份揭露」）"
    }}
  ]
}}""".format(json_only=JSON_ONLY)


def chapter_split_user(text: str, target_chapters: int = 1,
                       start_id: int = 1) -> str:
    return (f"请把下面的小说文本切分成约 {target_chapters} 章。\n"
            f"章节 id 从 {start_id} 开始编号。\n"
            f"行号从 1 开始计。\n\n"
            f"═══ 原文 ═══\n{text}")


# ══════════════════════════════════════════════════════════════════
# Skill 2 · 分镜生成（核心）
# ══════════════════════════════════════════════════════════════════

STORYBOARD_SYSTEM = """你是一位资深漫画分镜师。你要把小说章节拆成**一格一格的漫画画面**。

{json_only}
{no_fabrication}

═══════════ 硬性规则（违反任意一条都会导致你的输出被自动拒绝） ═══════════

【规则 1 · 引用而非描述】
cast 里只写角色的 id（来自下方的「角色表」），**绝对不要**在分镜里重新描述角色长相或服装。
外观由系统从角色表自动拼接。

【规则 2 · 两人以上同框必须写距离】
只要 cast 里有 2 个及以上角色，distance 字段**必须填写具体距离**。
写得越具体越好，例如：
  ✅ "两人相距约 8 米，中间是大片空旷的冰道"
  ✅ "隔开三个身位，背靠背站立"
  ❌ "不远" / "靠近" / 留空
原因：如果不写，绘图模型会把两人画得像在聊天，完全破坏对峙感。

【规则 3 · 大场面必须用极远景】
原著里凡是「半公里冰霜」「百米高空」「连绵数公里」这类规模，
必须用 极远景（shot="极远景"），action 里写明：
  "极远景全景，人物极小（只占画面高度约十分之一或更小），画面主体是法术与群山"
人物越小越对，要敢留大片天地给法术与地形。

【规则 4 · 战斗格必须是战斗形态】
战场上的人物**不能是普通站姿**。action 里要包含：
翼已展开／脚下有星图星轨／手掌有元素凝聚／重心下沉／衣袍与长发被气场掀起。
（例外：纯对话、密议的格子可以常服站姿。）

【规则 5 · 照搬原文时保留原文的语感】
action 要写成「镜头描述」而不是「小说叙述」。
  ❌ "穆宁雪很生气，决定出手"
  ✅ "穆宁雪冷眸直视前方，右手抬起，冰晶刹弓在掌心凝聚成形的瞬间"

【规则 6 · 表情要随情节变化】
emotion_hint 里为每个角色写本格的表情。
**同一角色在相邻两格不得使用完全相同的表情**（系统会自动检查并报错）。
每章至少要有 1 格「极端表情」（暴怒／剧痛／崩溃）。

【规则 7 · 主角损伤要有代价】
如果本章有战斗，用 damage_level 标记主角的损伤等级：
  L0 整洁（刚登场）  L1 初战（衣摆翻飞、擦伤）
  L2 硬扛（肩部撕裂、前襟破洞）  L3 重伤（上衣大片撕碎、半张脸被电黑）
  L4 战后（伤口愈合中、神情疲惫）
损伤等级**只能加深或保持，不能无故回落**（系统会检查）。

【规则 8 · 景别要有节奏】
不要连续 4 格以上用同一个景别。穿插远景/全景/近景/特写。
主角大特写每章不超过 2–3 格，只用在情绪爆点。

【规则 9 · 可追溯】
每一格都要填 source_span = [起始行, 结束行]，指向原文对应位置。

【规则 10 · 画面里不能有文字】
不要在任何字段里要求画面出现文字、台词、标题。
对白是单独的一层，由系统叠加。

═══════════ 可用枚举 ═══════════
shot（景别）：
  "极远景"（人物极小，主体是法术与天地）
  "大全景" | "全景" | "中景" | "近景" | "特写"

size（画幅）：
  "2048x1400"（横，适合大场面与双人对话）
  "1332x1776"（竖，适合单人特写与纵向构图）
  建议：交错使用，横竖比例约 6:4

damage_level: "L0" | "L1" | "L2" | "L3" | "L4"

═══════════ 输出格式 ═══════════
{{
  "chapter": "2436",
  "panels": [
    {{
      "id": "ch2436_P001",
      "seq": 1,
      "size": "2048x1400",
      "shot": "大全景",
      "cast": [
        {{"id": "mu_ningxue", "pos": "left", "variant": "default"}},
        {{"id": "mu_feiluan", "pos": "right", "variant": "default"}}
      ],
      "distance": "两人相距约 8 米，中间是大片空旷冰道",
      "action": "镜头描述，写清楚人物姿态、动作、法术形态与背景",
      "skill": "ice_bow",
      "background": "ice_corridor",
      "mood": "肃杀，细雪横飞",
      "damage_level": "L0",
      "emotion_hint": {{"mu_ningxue": "冷淡无波", "mu_feiluan": "倨傲"}},
      "source_span": [3, 4]
    }}
  ]
}}""".format(json_only=JSON_ONLY, no_fabrication=NO_FABRICATION)


def storyboard_user(chapter_title: str, text: str,
                    character_table: str,
                    scene_table: str,
                    skill_table: str,
                    start_line: int = 1,
                    panel_count_hint: int = 10) -> str:
    return (f"═══════════ 角色表（cast 里只能写这些 id） ═══════════\n"
            f"{character_table}\n\n"
            f"═══════════ 场景表（background 只能写这些 id） ═══════════\n"
            f"{scene_table}\n\n"
            f"═══════════ 技能表（skill 只能写这些 id，没有就留空） ═══════════\n"
            f"{skill_table}\n\n"
            f"═══════════ 本章 ═══════════\n"
            f"标题：{chapter_title}\n"
            f"预计格数：约 {panel_count_hint} 格（可 ±3 格）\n"
            f"原文行号从 {start_line} 开始计。\n\n"
            f"═══════════ 原文 ═══════════\n{text}")


def render_character_table(bible) -> str:
    """把 bible 的角色渲染成给模型看的简表（不含完整外观块，省 token）"""
    lines = []
    for cid, c in bible.characters.items():
        alias = f"（别名：{'、'.join(c.aliases)}）" if c.aliases else ""
        variants = [k for k in c.variants if k != "default"]
        vtxt = f"  可用形态：{'、'.join(variants)}" if variants else "  可用形态：无"
        lines.append(f"- id={cid}  姓名={c.name}{alias}\n{vtxt}")
    return "\n".join(lines) or "（无）"


def render_scene_table(bible) -> str:
    lines = [f"- id={s.id}  {s.name}：{s.text[:60]}" for s in bible.scenes.values()]
    return "\n".join(lines) or "（无）"


def render_skill_table(bible) -> str:
    lines = [f"- id={s.id}  {s.name}（{s.element}）" for s in bible.skills.values()]
    return "\n".join(lines) or "（无）"


# ══════════════════════════════════════════════════════════════════
# Skill 3 · 对白抽取
# ══════════════════════════════════════════════════════════════════

DIALOGUE_SYSTEM = """你负责从小说原文里**逐字摘录**对白，并判定每句话是谁说的。

{json_only}
{no_fabrication}

═══════════ 说话人判定规则（必须严格遵守） ═══════════

【规则 1】引号紧邻的人名 + 说话动词 → 那个人是说话人
  ✅ 「解决掉他们。」穆飞鸾说。         → 穆飞鸾
  ✅ 莫凡笑了笑：「我们也很久没有联手了。」 → 莫凡

【规则 2】「……对XX说道」→ XX 是**听者**，不是说话人
  ❌ 「你来了。」穆宁雪对侯泽说道。  → 说话人是穆宁雪，不是侯泽
  这是最容易出错的地方，务必仔细。

【规则 3】没有明确说话人时，看上下文
  · 上一句是谁的动作，通常还是谁在说
  · 对话轮替：A 说 → B 说 → A 说
  · 若实在无法确定，confirmed 填 false（系统会交人工确认）

【规则 4】对白必须逐字摘录
  一个字都不能改。不要补标点，不要改错别字，不要合并两句。
  「……」这类省略号也要原样保留。

【规则 5】同一人连续说的一段话 → 只产出一条
  如果原文是「他说：『第一句。第二句。』」，应产出**一条**对白，
  text 里保留换行。不要拆成两条。

【规则 6】不用管 source_span 和 confirmed
  系统会拿你给的 text 回原文核对，自动定位行号并确认说话人。
  你只要保证 text 是**原文原句**就行 —— 编的句子一核就露馅，会被丢掉。

═══════════ 输出格式 ═══════════
{{
  "items": {{
    "ch2436_P001": [
      {{
        "id": "ch2436_P001_b1",
        "who": "mu_ningxue",
        "text": "你喜欢这柄冰晶刹弓，作为多年的朋友，我自然亲手赠你。",
        "bubble": {{"style": "speech"}}
      }}
    ]
  }}
}}

bubble.style 可选：
  "speech"（普通对话）| "shout"（喊叫）| "think"（心声）
  "narration"（旁白/画外音）| "whisper"（低语）

把对白挂到对应的镜头 id 上（见下方镜头列表）。
若某句对白找不到合适的镜头，挂到最接近的那一格。""".format(
    json_only=JSON_ONLY, no_fabrication=NO_FABRICATION)


def dialogue_user(text: str, panel_list: str, character_table: str,
                  start_line: int = 1) -> str:
    return (f"═══════════ 可用角色（who 只能写这些 id） ═══════════\n"
            f"{character_table}\n\n"
            f"═══════════ 本章镜头列表 ═══════════\n{panel_list}\n\n"
            f"═══════════ 原文（行号从 {start_line} 开始） ═══════════\n{text}")


def render_panel_list(storyboard) -> str:
    lines = []
    for p in storyboard.panels:
        cast = "、".join(c.id for c in p.cast) or "无角色"
        lines.append(f"- {p.id}（第{p.seq}格，{p.shot.value}）：{p.action[:50]}｜出场：{cast}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# Skill 5 · 自修复
# ══════════════════════════════════════════════════════════════════

REPAIR_SYSTEM = """你上一次输出的 JSON 未通过校验。请根据错误列表修正后，重新输出**完整**的 JSON。

{json_only}

修正要求：
1. 只修改错误列表指出的地方，**其他部分保持原样**
2. 重新输出**完整**对象，不是补丁
3. 不要解释，不要道歉，直接给修正后的 JSON
4. 若错误提示「不能在原文里找到」，说明你编造了内容 —— 必须改用原文原句，
   或删除该条""".format(json_only=JSON_ONLY)


def repair_user(previous_json: str, feedback: str) -> str:
    return (f"═══════════ 你上一次的输出 ═══════════\n{previous_json}\n\n"
            f"═══════════ 校验错误 ═══════════\n{feedback}\n\n"
            f"请输出修正后的完整 JSON。")
