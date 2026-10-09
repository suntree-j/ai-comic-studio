# -*- coding: utf-8 -*-
"""AI 改漫 Agent 的 5 个技能

    Skill 1  split_chapters      章节切分
    Skill 2  generate_storyboard 分镜生成
    Skill 3  extract_dialogue    对白抽取 + 说话人判定
    Skill 4  track_state         角色状态追踪（跨格一致性）
    Skill 5  repair_until_valid  校验 + 自修复循环  ★ 核心

Skill 5 是本项目的技术亮点：
    不让 LLM「一次做对」，而是让它「错了能改」。
    校验失败 → 把结构化错误回喂 → 重产 → 再校验，最多 N 轮。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from pydantic import BaseModel

from ..ir import (
    Bible,
    ComicProject,
    DialogueBook,
    Storyboard,
    ValidationReport,
    validate,
)
from ..ir.models import Injury
from . import prompts
from .providers import LLMError, LLMProvider, Message, parse_as


# ══════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════

def number_lines(text: str, start: int = 1) -> str:
    """给原文加行号 —— 让模型能填准 source_span"""
    lines = text.splitlines()
    width = len(str(start + len(lines)))
    return "\n".join(f"{i:>{width}}| {ln}" for i, ln in enumerate(lines, start))


def split_lines(text: str) -> List[str]:
    return text.splitlines()


# ══════════════════════════════════════════════════════════════════
# Skill 5 · 自修复循环（核心）
# ══════════════════════════════════════════════════════════════════

@dataclass
class RepairResult:
    """自修复的结果"""
    ok: bool
    value: Optional[BaseModel]
    attempts: int
    final_report: Optional[ValidationReport] = None
    history: List[str] = field(default_factory=list)
    # 仍未通过时，携带最后一次的产物供人工处理
    pending: Optional[BaseModel] = None

    @property
    def status(self) -> str:
        if self.ok:
            return "ok" if self.attempts == 1 else f"repaired_in_{self.attempts}"
        return "pending_human"


def repair_until_valid(
    provider: LLMProvider,
    model_cls,
    system: str,
    user: str,
    validator: Callable[[BaseModel], ValidationReport],
    *,
    max_rounds: int = 3,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    verbose: bool = False,
) -> RepairResult:
    """生成 → 校验 → 失败则回喂错误重产，直到通过或超轮次

    Args:
        provider:  LLM
        model_cls: 期望的 Pydantic 模型
        system:    系统提示词
        user:      用户提示词
        validator: 接收产物返回 ValidationReport
        max_rounds: 最多尝试几轮（含首次）
    """
    history: List[str] = []
    messages = [Message(role="system", content=system),
                Message(role="user", content=user)]

    last_value: Optional[BaseModel] = None
    last_report: Optional[ValidationReport] = None

    for attempt in range(1, max_rounds + 1):
        try:
            value = provider.structured(
                messages, model_cls,
                temperature=temperature, max_tokens=max_tokens,
                retries=0,          # ★ 重试权归修复循环，避免两层重试互相吃掉轮次
            )
        except LLMError as e:
            history.append(f"第 {attempt} 轮：结构化输出失败 —— {e}")
            if verbose:
                print(f"   [repair] 第 {attempt} 轮解析失败：{e}")
            messages = messages + [Message(
                role="user",
                content=f"上一次的输出无法解析为合法 JSON：{e}\n请只输出合法 JSON。")]
            continue

        last_value = value
        report = validator(value)
        last_report = report

        if report.ok:
            history.append(f"第 {attempt} 轮：通过（{len(report.errors)} 条提示）")
            if verbose:
                print(f"   [repair] 第 {attempt} 轮通过")
            return RepairResult(ok=True, value=value, attempts=attempt,
                                final_report=report, history=history)

        errs = len([e for e in report.errors if e.severity.value == "error"])
        history.append(f"第 {attempt} 轮：{errs} 个错误（{', '.join(report.rules_hit())}）")
        if verbose:
            print(f"   [repair] 第 {attempt} 轮失败：{errs} 个错误 "
                  f"{report.rules_hit()}")

        if attempt >= max_rounds:
            break

        # 回喂结构化错误
        messages = [
            Message(role="system", content=prompts.REPAIR_SYSTEM),
            Message(role="user",
                    content=prompts.repair_user(
                        previous_json=json.dumps(
                            value.model_dump(mode="json"),
                            ensure_ascii=False, indent=1)[:12000],
                        feedback=report.to_feedback(),
                    )),
        ]

    return RepairResult(
        ok=False, value=None, attempts=max_rounds,
        final_report=last_report, history=history, pending=last_value,
    )


# ══════════════════════════════════════════════════════════════════
# Skill 1 · 章节切分
# ══════════════════════════════════════════════════════════════════

class ChapterPlan(BaseModel):
    class Chapter(BaseModel):
        id: str
        title: str
        start_line: int
        end_line: int
        summary: str = ""
        dramatic_beat: str = ""

    chapters: List[Chapter]


def split_chapters(provider: LLMProvider, text: str,
                   target: int = 1, start_id: int = 1,
                   verbose: bool = False) -> ChapterPlan:
    system = prompts.CHAPTER_SPLIT_SYSTEM
    user = prompts.chapter_split_user(
        number_lines(text), target_chapters=target, start_id=start_id)
    if verbose:
        print(f"   [split] 切分 {len(text)} 字 → 约 {target} 章")
    plan = provider.structured([Message(role="system", content=system),
                                Message(role="user", content=user)],
                               ChapterPlan, max_tokens=4096)
    return plan


# ══════════════════════════════════════════════════════════════════
# Skill 2 · 分镜生成
# ══════════════════════════════════════════════════════════════════

def generate_storyboard(
    provider: LLMProvider,
    bible: Bible,
    chapter_title: str,
    chapter_text: str,
    *,
    start_line: int = 1,
    panel_count_hint: int = 10,
    verbose: bool = False,
) -> Storyboard:
    """生成分镜（单轮，不带自修复；自修复由 pipeline 统一编排）"""
    system = prompts.STORYBOARD_SYSTEM
    user = prompts.storyboard_user(
        chapter_title=chapter_title,
        text=number_lines(chapter_text, start_line),
        character_table=prompts.render_character_table(bible),
        scene_table=prompts.render_scene_table(bible),
        skill_table=prompts.render_skill_table(bible),
        start_line=start_line,
        panel_count_hint=panel_count_hint,
    )
    if verbose:
        print(f"   [storyboard] 生成《{chapter_title}》约 {panel_count_hint} 格")
    return provider.structured(
        [Message(role="system", content=system),
         Message(role="user", content=user)],
        Storyboard, temperature=0.5, max_tokens=16384)


# ══════════════════════════════════════════════════════════════════
# Skill 3 · 对白抽取
# ══════════════════════════════════════════════════════════════════

def extract_dialogue(
    provider: LLMProvider,
    bible: Bible,
    storyboard: Storyboard,
    chapter_text: str,
    *,
    start_line: int = 1,
    verbose: bool = False,
) -> DialogueBook:
    system = prompts.DIALOGUE_SYSTEM
    user = prompts.dialogue_user(
        text=number_lines(chapter_text, start_line),
        panel_list=prompts.render_panel_list(storyboard),
        character_table=prompts.render_character_table(bible),
        start_line=start_line,
    )
    if verbose:
        print("   [dialogue] 抽取对白")
    return provider.structured(
        [Message(role="system", content=system),
         Message(role="user", content=user)],
        DialogueBook, temperature=0.2, max_tokens=16384)


# ══════════════════════════════════════════════════════════════════
# Skill 4 · 角色状态追踪
# ══════════════════════════════════════════════════════════════════

class StateDelta(BaseModel):
    class Wound(BaseModel):
        character: str
        part: str
        kind: str
        desc: str
        panel: str

    class Heal(BaseModel):
        character: str
        part: str
        panel: str

    new_wounds: List[Wound] = []
    healed: List[Heal] = []


STATE_SYSTEM = """你负责追踪角色在战斗中的**伤情变化**，用于保证跨格画面的一致性。

{json_only}
{no_fabrication}

只做两件事：
1. 本章里谁受了什么新伤（new_wounds）
2. 本章里谁的旧伤愈合了（healed）—— 只包括原文明确写了「治愈/圣药/恢复」的情况

判据：
· 只要原文写了「被击中／贯穿／灼伤／撕裂」且角色没有立刻恢复，就要记一条 new_wound
· 伤情描述要写成**可画的画面语言**（部位+外观+状态），不要写抽象感受
  例："左肩延伸到右腰的霜白伤口，边缘结霜向体内蔓延，一滴血不出"
· 持续的伤不要重复记录；只记**新发生**的

输出 JSON：
{{
  "new_wounds": [
    {{"character": "mu_ningxue", "part": "左肩→右腰", "kind": "霜白伤口",
      "desc": "边缘结霜向体内蔓延，一滴血不出", "panel": "ch2429_P012"}}
  ],
  "healed": [
    {{"character": "mu_ningxue", "part": "左肩", "panel": "ch2430_P005"}}
  ]
}}""".format(
    json_only=prompts.JSON_ONLY, no_fabrication=prompts.NO_FABRICATION)


def track_state(
    provider: LLMProvider,
    bible: Bible,
    storyboard: Storyboard,
    chapter_text: str,
    *,
    verbose: bool = False,
) -> StateDelta:
    """抽取本章的伤情变化"""
    known = []
    for cid, c in bible.characters.items():
        for inj in c.state.active_injuries():
            known.append(f"- {cid}：{inj.part} {inj.kind}（自 {inj.since} 起）")
    known_txt = "\n".join(known) or "（无已知旧伤）"

    user = (f"═══════════ 已知的伤（不要重复记录） ═══════════\n{known_txt}\n\n"
            f"═══════════ 本章镜头 ═══════════\n"
            f"{prompts.render_panel_list(storyboard)}\n\n"
            f"═══════════ 本章原文 ═══════════\n{number_lines(chapter_text)}")
    if verbose:
        print("   [state] 追踪伤情变化")
    return provider.structured(
        [Message(role="system", content=STATE_SYSTEM),
         Message(role="user", content=user)],
        StateDelta, temperature=0.2, max_tokens=4096)


def apply_state_delta(bible: Bible, delta: StateDelta) -> Bible:
    """把伤情变化写回 bible（返回新对象，不原地改）"""
    b = bible.model_copy(deep=True)
    for w in delta.new_wounds:
        ch = b.characters.get(w.character)
        if ch is None:
            continue
        exists = any(i.part == w.part and not i.healed for i in ch.state.injuries)
        if exists:
            continue
        ch.state.injuries.append(Injury(
            part=w.part, kind=w.kind, desc=w.desc,
            since=w.panel, healed=False,
        ))
    for h in delta.healed:
        ch = b.characters.get(h.character)
        if ch is None:
            continue
        for inj in ch.state.injuries:
            if inj.part == h.part and not inj.healed:
                inj.healed = True
                inj.healed_at = h.panel
    return b


# ══════════════════════════════════════════════════════════════════
# 校验器工厂（把项目级校验降为「只校验本章」）
# ══════════════════════════════════════════════════════════════════

def make_panel_validator(bible: Bible):
    """返回一个只校验分镜的 validator（用于 Skill 2 的自修复）"""
    from ..ir.validator import (
        _rule_cast_refs,
        _rule_damage_monotonic,
        _rule_distance_required,
        _rule_emotion_variety,
        _rule_shot_variety,
        ValidationReport,
    )

    def _v(sb: Storyboard) -> ValidationReport:
        r = ValidationReport()
        boards = [sb]
        _rule_cast_refs(bible, boards, r)
        _rule_distance_required(boards, r)
        _rule_emotion_variety(boards, r)
        _rule_damage_monotonic(boards, r)
        _rule_shot_variety(boards, r)
        return r

    return _v


def make_dialogue_validator(bible: Bible, storyboard: Storyboard,
                            source_lines: Optional[Sequence[str]] = None):
    """返回一个只校验对白的 validator（用于 Skill 3 的自修复）"""
    from ..ir.validator import (
        _rule_cast_emotion_consistency,
        _rule_dialogue_refs,
        _rule_dialogue_traceable,
        ValidationReport,
    )

    known = {p.id for p in storyboard.panels}

    def _v(dlg: DialogueBook) -> ValidationReport:
        r = ValidationReport()
        _rule_dialogue_refs(bible, dlg, known, r)
        _rule_dialogue_traceable(dlg, source_lines, r)
        _rule_cast_emotion_consistency([storyboard], dlg, r)
        return r

    return _v


# ══════════════════════════════════════════════════════════════════
# Skill 3.5 · 回原文核对（★ 不让 LLM 自己数行号）
# ══════════════════════════════════════════════════════════════════

def _norm_for_match(s: str) -> str:
    """去掉引号与空白，只留正文字符，用于比对"""
    return "".join(ch for ch in (s or "")
                   if ch not in "「」『』“”\"' \t\u3000\n\r")


def locate_in_source(text: str, source_lines: Sequence[str],
                     start_line: int = 1) -> Optional[List[int]]:
    """在一章原文里找到这句话，返回行号区间 [起, 止]（1-based）

    先按行找；找不到就把整章的正文拼起来再找（应对原文里换行的长句）。
    """
    want = _norm_for_match(text)
    if not want:
        return None
    # ① 逐行找（最常见）
    for i, ln in enumerate(source_lines):
        if want in _norm_for_match(ln):
            n = start_line + i
            return [n, n]
    # ② 跨行找：把所有行拼成一个字符串，记录每行的起止偏移
    joined, spans = [], []
    for i, ln in enumerate(source_lines):
        piece = _norm_for_match(ln)
        spans.append((len("".join(joined)), len("".join(joined)) + len(piece)))
        joined.append(piece)
    whole = "".join(joined)
    pos = whole.find(want)
    if pos < 0:
        return None
    end = pos + len(want) - 1
    first = next((i for i, (a, b) in enumerate(spans) if a <= pos < b), 0)
    last = next((i for i, (a, b) in enumerate(spans) if a <= end < b), first)
    return [start_line + first, start_line + last]


def verify_dialogue_against_source(
    dlg: DialogueBook,
    source_lines: Sequence[str],
    start_line: int = 1,
    bible: Optional[Bible] = None,
) -> tuple:
    """★ 把 source_span / confirmed 从「问 LLM」改成「系统自己算」

    为什么必须这样（真实事故）：
        DeepSeek-V3.2 对一章 7 条对白**全部**填 `confirmed=false` +
        `source_span=null`。而 IR-006 要求说话人已确认、IR-007 要求可追溯，
        于是自修复循环跑满 3 轮仍全被拒 → `dlg_res.value=None` →
        pipeline 拿到空对白 → **成品漫画上一个字都没有**。

    但这两个字段本来就**不该问 LLM**：
      · LLM 数不准行号（没人能可靠地对 2616 字原文数行），
        所以它保守地填 null —— 这是合理行为，不是模型的错，是设计的问题。
      · 而这两件事系统完全可以自己确定性地算出来。

    这里：
      · 能在原文里找到 → 回填真实行号，并置 confirmed=true
      · 找不到         → 说明 LLM 编了台词（IR-007 防的就是这个），**丢弃该条**
      · 说话人不在角色表 → 丢弃（不能渲染一个不存在的人）

    返回 (处理后的 DialogueBook, 报告 dict)
    """
    kept: Dict[str, List[Utterance]] = {}
    dropped_not_found, dropped_unknown, spans = [], [], 0
    # characters 是 {角色id: Character} 的字典
    known = set(bible.characters) if bible else None

    for pid, utts in (dlg.items or {}).items():
        for u in utts:
            if known is not None and u.who and u.who not in known:
                dropped_unknown.append((pid, u.who, u.text[:24]))
                continue
            span = locate_in_source(u.text, source_lines, start_line)
            if span is None:
                dropped_not_found.append((pid, u.text[:32]))
                continue
            u.source_span = span
            u.confirmed = True          # 原文核对通过 = 说话人可确认
            spans += 1
            kept.setdefault(pid, []).append(u)

    report = {
        "kept": spans,
        "dropped_not_in_source": dropped_not_found,
        "dropped_unknown_speaker": dropped_unknown,
    }
    return DialogueBook(items=kept), report
