# -*- coding: utf-8 -*-
"""提示词组装器：把 Comic IR 的一格变成可送进生图模型的提示词

三段式结构（顺序不能变）：
    ① 共享风格块   —— 全篇逐字相同，保证画风统一
    ② 角色标准块   —— 从 bible 逐字取，绝不改写（保证跨格不换装不换发色）
    ③ 本格构图     —— 景别/位置/距离/动作/背景/氛围 + 硬规则
    ④ 禁止文字     —— ★ 防止对白被画进图里

另有硬规则注入：
    · 两人以上同框 → 强制重申距离
    · 极远景 → 强制写「人物极小，主体是法术与群山」
    · 战斗格 → 强制写战斗形态要素
    · 变身形态 → 强制写「仍是他本人，脸型瞳色不变」
"""

from __future__ import annotations

from typing import List, Optional

from ..ir.models import (
    Bible,
    CastMember,
    Character,
    DamageLevel,
    Panel,
    ShotSize,
)

# ══════════════════════════════════════════════════════════════════
# 硬规则片段
# ══════════════════════════════════════════════════════════════════

RULE_EXTREME_WIDE = (
    "【极远景要求】人物在画面中极小（只占画面高度约十分之一或更小），"
    "画面主体是法术奇观与群山地形。近景（被摧毁的地面）→中景（法术主体）→"
    "远景（山体与天幕），至少三层纵深。天空占比超过一半。"
    "要敢留大片天地给法术与地形。"
)

RULE_BATTLE_FORM = (
    "【战斗形态要求】人物处于战斗形态，不能是普通站姿："
    "翼已展开或脚下有星图星轨或手掌有元素凝聚；重心下沉或前倾、双腿分开；"
    "衣袍与长发被自身气场掀起；脚边积雪被气场推开、地面有裂纹或霜环。"
)

RULE_DISTANCE = (
    "【距离要求】两人之间必须留出明确的空间间隔（见上方具体距离），"
    "分置画面左右两端，中间是空旷地带。绝对不要把两人画成并肩或贴近。"
)

RULE_PRESERVE_IDENTITY = (
    "【保持同一个人】仍然是他本人：脸型、五官比例与瞳色与常态完全一致，"
    "不得成年化、不得更改脸型与瞳色；只有发型、服装、体型气势随形态变化。"
)

RULE_NO_TEXT = (
    "【绝对禁止文字】画面中绝对不要出现任何文字、台词、对白框、气泡、字幕、"
    "水印、logo、任何中文/英文/日文字符。对白由后期单独叠加。"
)

RULE_DAMAGE = {
    DamageLevel.L0: "【状态】衣物完好无伤，头发略凌乱即可。",
    DamageLevel.L1: "【状态】卫衣被气流掀起、下摆翻飞；白衬衫从腰间松脱露出一角、"
                    "袖子卷到手肘；头发被吹乱；脸颊有一两道擦伤、衣袖有焦痕；"
                    "膝盖与手肘沾雪与灰。",
    DamageLevel.L2: "【状态】上衣左肩被撕开一道口子、露出里面的内层；前襟被烧出焦黑破洞、"
                    "下摆撕破；小臂与锁骨处有雷电焦痕（细密黑紫纹路）；"
                    "额头有血痕、嘴角破皮；裤腿有破口；头发被电得炸起。",
    DamageLevel.L3: "【状态】上衣几乎只剩挂在身上、大片撕碎（但仍能看出原来的款式）；"
                    "胸口与背部有大片雷电焦痕、手臂有伤口淌血；半张脸被电黑；"
                    "站姿摇晃但仍支撑。克制写实，不要血肉模糊的猎奇画面。",
    DamageLevel.L4: "【状态】身上多处伤、衣物破碎；伤口开始愈合（可画愈合中的微光）；"
                    "神情从倔强转为疲惫而放松。",
}

DAMAGE_HINT = (
    "【损伤画法】破洞与血痕要顺着战斗方向：火系→焦黑烧痕；雷系→细密黑紫电痕；"
    "冰系→霜白割裂。以「狼狈但硬气」为调性。"
)


# ══════════════════════════════════════════════════════════════════
# 角色块
# ══════════════════════════════════════════════════════════════════

def render_character_block(bible: Bible, cast: CastMember,
                           emotion: Optional[str] = None) -> str:
    """把一个出场角色渲染成提示词段落（逐字使用 bible 的标准块）"""
    ch: Optional[Character] = bible.characters.get(cast.id)
    if ch is None:
        return f"【角色 {cast.id}】未在设定集中定义"

    parts: List[str] = []

    # ① 标准块（逐字，不改写）
    parts.append(f"【{ch.name}的外观（唯一标准，逐字照此绘制，不得改写）】\n{ch.appearance.text}")

    # ② 形态变体
    if cast.variant and cast.variant != "default":
        v = ch.variants.get(cast.variant)
        if v:
            parts.append(f"【{ch.name}·{v.name}形态】{v.override}")
            parts.append(RULE_PRESERVE_IDENTITY)

    # ③ 该角色当前伤情（跨格追踪）
    active = ch.state.active_injuries()
    if active:
        txt = "；".join(f"{i.part}的{i.kind}——{i.desc}" for i in active)
        parts.append(f"【{ch.name}当前带伤】{txt}。这些伤必须画出来，不得省略。")

    # ④ 本格表情
    emo = emotion or ch.state.emotion
    if emo:
        parts.append(f"【{ch.name}本格表情】{emo}")

    # ⑤ 禁忌
    if ch.appearance.forbidden:
        parts.append(f"【{ch.name}绝对不要】{'、'.join(ch.appearance.forbidden)}")

    return "\n".join(parts)


def render_composition(bible: Bible, panel: Panel) -> str:
    """本格构图描述"""
    parts: List[str] = []

    head = f"【景别】{panel.shot.value}"
    if panel.size:
        head += f"　【画幅】{'横构图' if panel.size.value.startswith('2048') else '竖构图'}"
    parts.append(head)

    # 角色站位
    if panel.cast:
        pos_txt = "；".join(
            f"{bible.characters[c.id].name if c.id in bible.characters else c.id}"
            f"在画面{c.pos}" + (f"（{c.note}）" if c.note else "")
            for c in panel.cast
        )
        parts.append(f"【站位】{pos_txt}")

    # 环境 / 动作 / 氛围
    if panel.background and panel.background in bible.scenes:
        parts.append(f"【环境】{bible.scenes[panel.background].text}")
    parts.append(f"【动作】{panel.action}")
    if panel.mood:
        parts.append(f"【氛围】{panel.mood}")

    # 技能
    if panel.skill and panel.skill in bible.skills:
        sk = bible.skills[panel.skill]
        parts.append(f"【法术】{sk.name}（{sk.element}属性），"
                     f"用发光的浅色线条与飘带表现，带细密光点与粒子，不要实体色块。")

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def build_prompt(bible: Bible, panel: Panel) -> str:
    """把一格组装成完整提示词"""
    blocks: List[str] = []

    # ① 共享风格块（全篇逐字相同）
    blocks.append(bible.style.shared_block)

    # ② 角色标准块（每个出场角色一段）
    for c in panel.cast:
        blocks.append(render_character_block(
            bible, c, panel.emotion_hint.get(c.id)))

    # ③ 本格构图
    comp = render_composition(bible, panel)

    # ④ 硬规则
    rules: List[str] = []

    if panel.shot is ShotSize.EXTREME_WIDE:
        rules.append(RULE_EXTREME_WIDE)

    if len(panel.cast) >= 2:
        if panel.distance:
            comp += f"\n【距离】{panel.distance}"
        rules.append(RULE_DISTANCE)

    if panel.damage_level is not None:
        rules.append(RULE_DAMAGE[panel.damage_level])
        rules.append(DAMAGE_HINT)

    if panel.skill:
        rules.append(RULE_BATTLE_FORM)

    blocks.append(comp + ("\n" + "\n".join(rules) if rules else ""))

    # ⑤ 禁止文字（永远放最后，模型对结尾更敏感）
    blocks.append(
        (bible.style.no_text_rule or RULE_NO_TEXT)
        if "文字" in (bible.style.no_text_rule or "") else RULE_NO_TEXT
    )

    return "\n\n".join(b for b in blocks if b.strip())


def build_negative(panel: Panel) -> str:
    """负面提示词（给支持 negative 的后端用）"""
    base = (
        "文字, 汉字, 台词, 对白框, 气泡, 字幕, 水印, logo, 签名, 边框, 分格线, "
        "低质量, 模糊, 畸形, 多手, 多指, 断肢, 比例失调, 过长腿, 过细腰"
    )
    extra: List[str] = []
    if len(panel.cast) >= 2:
        extra.append("两人贴近, 并肩, 搂抱")
    if panel.shot is ShotSize.EXTREME_WIDE:
        extra.append("人物占满画面, 大特写")
    return ", ".join([base] + extra)


def describe_panel(bible: Bible, panel: Panel) -> str:
    """一行摘要，用于日志/调试"""
    cast = "、".join(
        bible.characters[c.id].name if c.id in bible.characters else c.id
        for c in panel.cast) or "空镜"
    return f"{panel.id}　{panel.shot.value}　{cast}　{panel.action[:34]}"
