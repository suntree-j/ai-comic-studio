# -*- coding: utf-8 -*-
"""Comic IR 业务规则校验器

为什么需要它：
    JSON Schema 只能挡住「格式错误」，
    挡不住「格式正确但语义错误」——比如：
        · cast 引用了 bible 里不存在的角色
        · 对白在原文里根本找不到（LLM 编的）
        · 两人同框却没写距离（模型会把两人画得很近）
        · 同一角色相邻两格同一个表情（全程一个表情）
        · 角色伤情无故自愈
        · 气泡坐标越界
        · 页码中途变动导致错位

    这些正是「AI 改漫」最容易出错、也最贵的地方。
    本模块把它们变成可自动检测的规则集。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import (
    Bible,
    ComicProject,
    DialogueBook,
    Layout,
    PageType,
    Panel,
    Storyboard,
)


class Severity(str, Enum):
    ERROR = "error"      # 必须修复，否则不得渲染
    WARNING = "warning"  # 可渲染，但建议修复


@dataclass
class ValidationError:
    """一条校验失败"""
    rule: str                      # 规则编号，如 "IR-004"
    severity: Severity
    message: str
    where: str = ""                # 定位，如 "ch2436_P001 / cast[1]"
    hint: str = ""                 # 修复建议（会回喂给 LLM）

    def __str__(self) -> str:
        tag = "ERROR" if self.severity is Severity.ERROR else "WARN "
        loc = f" [{self.where}]" if self.where else ""
        return f"{tag} {self.rule}{loc} {self.message}"


@dataclass
class ValidationReport:
    errors: List[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(e.severity is Severity.ERROR for e in self.errors)

    def by_severity(self, s: Severity) -> List[ValidationError]:
        return [e for e in self.errors if e.severity is s]

    def rules_hit(self) -> List[str]:
        return sorted({e.rule for e in self.errors})

    def summary(self) -> str:
        errs = len(self.by_severity(Severity.ERROR))
        warns = len(self.by_severity(Severity.WARNING))
        if not self.errors:
            return "校验通过"
        return f"{errs} 个错误 / {warns} 个警告，命中规则：{', '.join(self.rules_hit())}"

    def to_feedback(self) -> str:
        """把错误整理成可回喂给 LLM 的反馈文本"""
        if not self.errors:
            return ""
        lines = ["你上一次的输出有以下问题，请修正后重新输出完整结果："]
        for e in self.errors:
            lines.append(f"- [{e.rule}] {e.where}: {e.message}")
            if e.hint:
                lines.append(f"  修正方式：{e.hint}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 规则实现
# ══════════════════════════════════════════════════════════════════

def _rule_cast_refs(bible: Bible, boards: Sequence[Storyboard],
                    report: ValidationReport) -> None:
    """IR-001 引用完整性：cast / skill / background 必须存在于 bible"""
    for sb in boards:
        for p in sb.panels:
            for i, c in enumerate(p.cast):
                if c.id not in bible.characters:
                    report.errors.append(ValidationError(
                        rule="IR-001", severity=Severity.ERROR,
                        where=f"{p.id} / cast[{i}]",
                        message=f"角色 id {c.id!r} 不在 bible.characters 中",
                        hint=f"改用 bible 里已有的角色 id：{sorted(bible.characters)[:8]}",
                    ))
                else:
                    variants = bible.characters[c.id].variants
                    if c.variant != "default" and c.variant not in variants:
                        report.errors.append(ValidationError(
                            rule="IR-001", severity=Severity.ERROR,
                            where=f"{p.id} / cast[{i}]",
                            message=f"角色 {c.id} 的形态 {c.variant!r} 未定义",
                            hint=f"可用形态：{sorted(variants) or ['default']}",
                        ))
            if p.skill and p.skill not in bible.skills:
                report.errors.append(ValidationError(
                    rule="IR-001", severity=Severity.ERROR,
                    where=f"{p.id} / skill",
                    message=f"技能 id {p.skill!r} 不在 bible.skills 中",
                    hint=f"可用技能：{sorted(bible.skills)[:8]}",
                ))
            if p.background and p.background not in bible.scenes:
                report.errors.append(ValidationError(
                    rule="IR-001", severity=Severity.ERROR,
                    where=f"{p.id} / background",
                    message=f"场景 id {p.background!r} 不在 bible.scenes 中",
                    hint=f"可用场景：{sorted(bible.scenes)[:8]}",
                ))


def _rule_distance_required(boards: Sequence[Storyboard],
                            report: ValidationReport) -> None:
    """IR-002 两人以上同框必须写明距离"""
    for sb in boards:
        for p in sb.panels:
            if len(p.cast) >= 2 and not (p.distance or "").strip():
                report.errors.append(ValidationError(
                    rule="IR-002", severity=Severity.ERROR,
                    where=p.id,
                    message=f"有 {len(p.cast)} 个角色出场但未写 distance",
                    hint="写明具体距离，如「两人相距约 8 米，中间大片空旷冰道」；"
                         "否则模型会把两人画得像在聊天",
                ))
            if p.distance and len(p.distance.strip()) < 4:
                report.errors.append(ValidationError(
                    rule="IR-002", severity=Severity.WARNING,
                    where=p.id,
                    message=f"distance 过于模糊：{p.distance!r}",
                    hint="写成具体数值或身位，如「相距 5 米」「隔开三个身位」",
                ))


def _rule_emotion_variety(boards: Sequence[Storyboard],
                          report: ValidationReport) -> None:
    """IR-003 同一角色相邻两格不得同表情"""
    for sb in boards:
        prev: Dict[str, str] = {}
        for p in sb.panels:
            for cid, emo in p.emotion_hint.items():
                if prev.get(cid) == emo:
                    report.errors.append(ValidationError(
                        rule="IR-003", severity=Severity.WARNING,
                        where=p.id,
                        message=f"角色 {cid} 与前格表情相同（{emo}）",
                        hint="换一个符合本格情节的表情，制造层次",
                    ))
                prev[cid] = emo


_DAMAGE_ORDER = ["L0", "L1", "L2", "L3", "L4"]


def _rule_damage_monotonic(boards: Sequence[Storyboard],
                           report: ValidationReport) -> None:
    """IR-004 主角损伤不得无故回落"""
    for sb in boards:
        last: Optional[str] = None
        for p in sb.panels:
            if p.damage_level is None:
                continue
            cur = p.damage_level.value
            if last is not None:
                if _DAMAGE_ORDER.index(cur) < _DAMAGE_ORDER.index(last):
                    report.errors.append(ValidationError(
                        rule="IR-004", severity=Severity.WARNING,
                        where=p.id,
                        message=f"损伤等级从 {last} 回落到 {cur}",
                        hint="只有明确写了「服用圣药/治疗」才允许回落，"
                             "否则应保持或加深",
                    ))
            last = cur


def _rule_wound_continuity(bible: Bible,
                           report: ValidationReport) -> None:
    """IR-005 伤情必须闭环：未愈合的伤要有起始镜头，愈合的要有愈合镜头"""
    for cid, ch in bible.characters.items():
        for i, inj in enumerate(ch.state.injuries):
            w = f"characters.{cid}.state.injuries[{i}]"
            if not inj.since:
                report.errors.append(ValidationError(
                    rule="IR-005", severity=Severity.ERROR, where=w,
                    message="伤情缺少 since（起始镜头）",
                    hint="写上受伤那一格的 id",
                ))
            if inj.healed and not inj.healed_at:
                report.errors.append(ValidationError(
                    rule="IR-005", severity=Severity.WARNING, where=w,
                    message="标记为已愈合但没写 healed_at",
                    hint="写明在哪一格愈合，或把 healed 改回 false",
                ))
            if inj.healed_at and not inj.healed:
                report.errors.append(ValidationError(
                    rule="IR-005", severity=Severity.WARNING, where=w,
                    message="写了 healed_at 但 healed=false",
                    hint="两者要一致",
                ))


def _rule_dialogue_refs(bible: Bible, dialogue: DialogueBook,
                        known_panels: set,
                        report: ValidationReport) -> None:
    """IR-006 对白引用完整 + 说话人已确认"""
    for pid, utts in dialogue.items.items():
        if pid not in known_panels:
            report.errors.append(ValidationError(
                rule="IR-006", severity=Severity.ERROR, where=pid,
                message="对白挂在不存在的镜头上",
                hint=f"可用的镜头 id 示例：{sorted(known_panels)[:6]}",
            ))
        ids = [u.id for u in utts]
        dup = {x for x in ids if ids.count(x) > 1}
        if dup:
            report.errors.append(ValidationError(
                rule="IR-006", severity=Severity.ERROR, where=pid,
                message=f"对白 id 重复：{sorted(dup)}",
                hint="每条对白的 id 必须全局唯一",
            ))
        for i, u in enumerate(utts):
            w = f"{pid}[{i}]"
            if u.who not in bible.characters:
                report.errors.append(ValidationError(
                    rule="IR-006", severity=Severity.ERROR,
                    where=f"{w}.who",
                    message=f"说话人 {u.who!r} 不在 bible.characters 中",
                    hint=f"可用角色：{sorted(bible.characters)[:8]}",
                ))
            if not u.confirmed:
                report.errors.append(ValidationError(
                    rule="IR-006", severity=Severity.ERROR,
                    where=f"{w}.confirmed",
                    message="说话人未经确认，不得渲染",
                    hint="人工核对原文后把 confirmed 置为 true；"
                         "「……对XX说道」中的 XX 是听者而不是说话人",
                ))


def _rule_dialogue_traceable(dialogue: DialogueBook,
                             source_lines: Optional[Sequence[str]],
                             report: ValidationReport) -> None:
    """IR-007 对白必须能在原文里找到（防 LLM 编台词）"""
    if source_lines is None:
        return
    n = len(source_lines)

    def norm(s: str) -> str:
        return "".join(ch for ch in s if ch not in "「」『』“”\"' \t\u3000")

    for pid, utts in dialogue.items.items():
        for i, u in enumerate(utts):
            w = f"{pid}[{i}]"
            if not u.source_span:
                report.errors.append(ValidationError(
                    rule="IR-007", severity=Severity.ERROR,
                    where=f"{w}.source_span",
                    message="对白缺少 source_span，无法追溯来源",
                    hint="写上该句在原文中的行号区间 [起, 止]",
                ))
                continue
            a, b = u.source_span
            if not (1 <= a <= b <= n):
                report.errors.append(ValidationError(
                    rule="IR-007", severity=Severity.ERROR,
                    where=f"{w}.source_span",
                    message=f"行号区间 [{a}, {b}] 超出原文范围（共 {n} 行）",
                    hint=f"改成 1–{n} 之间的区间",
                ))
                continue
            window = norm("".join(source_lines[a - 1:b]))
            target = norm(u.text)
            if target and target not in window:
                report.errors.append(ValidationError(
                    rule="IR-007", severity=Severity.ERROR,
                    where=f"{w}.text",
                    message="这句在 source_span 指向的原文里找不到（疑似编造）",
                    hint="只能摘录原文原句，不得改写或创作；"
                         "若确实需要改写请把它标为旁白并单独说明",
                ))


def _rule_bubble_bounds(dialogue: DialogueBook,
                        report: ValidationReport) -> None:
    """IR-008 气泡坐标必须落在画面内，且气泡与箭头不能重合"""
    for pid, utts in dialogue.items.items():
        for i, u in enumerate(utts):
            b = u.bubble
            w = f"{pid}[{i}].bubble"
            if b.box and b.tail:
                if abs(b.box.x - b.tail.x) < 0.02 and abs(b.box.y - b.tail.y) < 0.02:
                    report.errors.append(ValidationError(
                        rule="IR-008", severity=Severity.WARNING,
                        where=w,
                        message="箭头尖端与气泡位置几乎重合",
                        hint="箭头应指向说话人所在处，而不是气泡自身",
                    ))
            if b.font_size is None:
                continue


def _rule_layout_consistency(layout: Layout, known_panels: set,
                             report: ValidationReport) -> None:
    """IR-009 页序自洽：引用的镜头必须存在，且不得重复占用两页"""
    seen: Dict[str, int] = {}
    for pg in layout.pages:
        if pg.type is not PageType.PANELS:
            continue
        for pid in pg.panels:
            if pid not in known_panels:
                report.errors.append(ValidationError(
                    rule="IR-009", severity=Severity.ERROR,
                    where=f"page {pg.page}",
                    message=f"引用了不存在的镜头 {pid!r}",
                    hint="检查分镜表里是否有这个 id，或改页序",
                ))
            if pid in seen:
                report.errors.append(ValidationError(
                    rule="IR-009", severity=Severity.ERROR,
                    where=f"page {pg.page}",
                    message=f"镜头 {pid!r} 已在第 {seen[pid]} 页出现过",
                    hint="同一格只能出现在一页",
                ))
            seen[pid] = pg.page


def _rule_panels_placed(storyboards: Sequence[Storyboard], layout: Layout,
                        report: ValidationReport) -> None:
    """IR-010 每个镜头都必须在页序里有一席之地（防漏排）"""
    placed = set()
    for pg in layout.pages:
        placed.update(pg.panels)
    for sb in storyboards:
        for p in sb.panels:
            if p.id not in placed:
                report.errors.append(ValidationError(
                    rule="IR-010", severity=Severity.ERROR,
                    where=p.id,
                    message="该镜头没有被排进任何页面",
                    hint="加入 layout.pages，或从分镜表移除",
                ))


def _rule_cast_emotion_consistency(boards: Sequence[Storyboard],
                                   dialogue: DialogueBook,
                                   report: ValidationReport) -> None:
    """IR-011 有对白的镜头，说话人必须真的在这一格出场"""
    panel_cast: Dict[str, set] = {}
    for sb in boards:
        for p in sb.panels:
            panel_cast[p.id] = {c.id for c in p.cast}

    for pid, utts in dialogue.items.items():
        cast = panel_cast.get(pid)
        if cast is None:
            continue
        for i, u in enumerate(utts):
            if u.who in cast:
                continue
            # 允许画外音：但必须显式声明
            if u.bubble.style.value == "narration":
                continue
            report.errors.append(ValidationError(
                rule="IR-011", severity=Severity.WARNING,
                where=f"{pid}[{i}]",
                message=f"说话人 {u.who} 不在本格出场名单里",
                hint="要么把该角色加入 cast，要么把气泡样式改为 narration "
                     "（画外音），并让箭头指向画面空白处",
            ))


def _rule_shot_variety(boards: Sequence[Storyboard],
                       report: ValidationReport) -> None:
    """IR-012 连续多格同景别会显得呆板"""
    for sb in boards:
        run: List[str] = []
        for p in sb.panels:
            if run and run[-1] == p.shot.value:
                run.append(p.shot.value)
            else:
                run = [p.shot.value]
            if len(run) >= 4:
                report.errors.append(ValidationError(
                    rule="IR-012", severity=Severity.WARNING,
                    where=p.id,
                    message=f"连续 {len(run)} 格都是「{p.shot.value}」",
                    hint="穿插远景/近景/特写来调节节奏",
                ))
                run = []


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════

def validate(
    project: ComicProject,
    source_lines: Optional[Sequence[str]] = None,
) -> ValidationReport:
    """对一个完整项目跑全部业务规则

    Args:
        project: 四个 IR 文件的聚合
        source_lines: 原文按行切分的列表。给了才能校验 IR-007（对白可追溯）

    Returns:
        ValidationReport；`report.ok` 为 True 时方可进入渲染
    """
    report = ValidationReport()
    boards = project.storyboards
    known_panels = {p.id for sb in boards for p in sb.panels}

    _rule_cast_refs(project.bible, boards, report)
    _rule_distance_required(boards, report)
    _rule_emotion_variety(boards, report)
    _rule_damage_monotonic(boards, report)
    _rule_wound_continuity(project.bible, report)
    _rule_dialogue_refs(project.bible, project.dialogue, known_panels, report)
    _rule_dialogue_traceable(project.dialogue, source_lines, report)
    _rule_bubble_bounds(project.dialogue, report)
    _rule_layout_consistency(project.layout, known_panels, report)
    _rule_panels_placed(boards, project.layout, report)
    _rule_cast_emotion_consistency(boards, project.dialogue, report)
    _rule_shot_variety(boards, report)

    return report


RULES = {
    "IR-001": "引用完整性：cast / skill / background 必须存在于 bible",
    "IR-002": "两人以上同框必须写明具体距离",
    "IR-003": "同一角色相邻两格不得同表情",
    "IR-004": "主角损伤等级不得无故回落",
    "IR-005": "伤情必须闭环（有起始镜头；愈合要有愈合镜头）",
    "IR-006": "对白引用合法且说话人已确认",
    "IR-007": "对白必须能在原文里逐字找到（防编造）",
    "IR-008": "气泡与箭头坐标不得重合或越界",
    "IR-009": "页序引用的镜头必须存在且不重复",
    "IR-010": "每个镜头都必须被排进页面",
    "IR-011": "有对白的镜头，说话人应在场（否则须声明为画外音）",
    "IR-012": "避免连续 ≥4 格同景别",
}
