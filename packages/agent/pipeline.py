# -*- coding: utf-8 -*-
"""端到端编排：小说文本 → Comic IR

    text
     ├─ Skill 1  split_chapters       → 章节
     ├─ Skill 2  generate_storyboard  → storyboard  （带自修复）
     ├─ Skill 3  extract_dialogue     → dialogue    （带自修复）
     ├─ Skill 4  track_state          → 更新 bible
     └─ 产出一个可直接送进渲染器的 ComicProject
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..ir import (
    Bible,
    ComicProject,
    DialogueBook,
    Layout,
    LayoutPage,
    PageType,
    Storyboard,
    ValidationReport,
    validate,
)
from .providers import LLMProvider
from . import skills as S


@dataclass
class ChapterResult:
    chapter_id: str
    title: str
    storyboard: Optional[Storyboard]
    dialogue: Optional[DialogueBook]
    status: str                     # ok / repaired / pending_human / failed
    attempts: int = 0
    history: List[str] = field(default_factory=list)
    report: Optional[ValidationReport] = None


@dataclass
class PipelineOptions:
    panel_count_hint: int = 10
    max_repair_rounds: int = 3
    temperature: float = 0.5
    verbose: bool = True


class ComicPipeline:
    """把「一段小说」变成「一页页漫画的 IR」

    用法：
        pipe = ComicPipeline(provider, bible)
        project = pipe.run("第2436章 冰晶刹弓\\n\\n穆宁雪站在...")
    """

    def __init__(self, provider: LLMProvider, bible: Bible,
                 options: Optional[PipelineOptions] = None):
        self.provider = provider
        self.bible = bible
        self.opt = options or PipelineOptions()

    # ── 单章 ──────────────────────────────────────────────────
    def run_chapter(self, chapter_id: str, title: str, text: str,
                    start_line: int = 1) -> ChapterResult:
        o = self.opt
        log: List[str] = []
        if o.verbose:
            print(f"\n▶ 处理第 {chapter_id} 章《{title}》（{len(text)} 字）")

        # ① 分镜（带自修复）
        sb_res = S.repair_until_valid(
            self.provider, Storyboard,
            system=S.prompts.STORYBOARD_SYSTEM,
            user=S.prompts.storyboard_user(
                chapter_title=title,
                text=S.number_lines(text, start_line),
                character_table=S.prompts.render_character_table(self.bible),
                scene_table=S.prompts.render_scene_table(self.bible),
                skill_table=S.prompts.render_skill_table(self.bible),
                start_line=start_line,
                panel_count_hint=o.panel_count_hint,
            ),
            validator=S.make_panel_validator(self.bible),
            max_rounds=o.max_repair_rounds,
            temperature=o.temperature,
            verbose=o.verbose,
        )
        log += ["分镜：" + h for h in sb_res.history]

        if not sb_res.ok or sb_res.value is None:
            return ChapterResult(chapter_id, title, None, None,
                                 "pending_human", sb_res.attempts, log,
                                 sb_res.final_report)

        storyboard: Storyboard = sb_res.value

        # ② 对白（带自修复）
        dlg_res = S.repair_until_valid(
            self.provider, DialogueBook,
            system=S.prompts.DIALOGUE_SYSTEM,
            user=S.prompts.dialogue_user(
                text=S.number_lines(text, start_line),
                panel_list=S.prompts.render_panel_list(storyboard),
                character_table=S.prompts.render_character_table(self.bible),
                start_line=start_line,
            ),
            validator=S.make_dialogue_validator(
                self.bible, storyboard, S.split_lines(text)),
            max_rounds=o.max_repair_rounds,
            temperature=0.2,
            verbose=o.verbose,
        )
        log += ["对白：" + h for h in dlg_res.history]

        # ★ 取 repair 的最后产物来核对，而不是直接认输
        #   为什么：`source_span` 与 `confirmed` 本来就**不该问 LLM** ——
        #   LLM 数不准 2616 字原文的行号，所以它一律填 null / false，
        #   于是自修复循环跑满 3 轮仍全被 IR-006 / IR-007 拒掉，
        #   最后 pipeline 拿到空对白，成品漫画上一个字都没有。
        #   这两件事系统能确定性算出来：回原文找这句话，找到就回填行号。
        candidate = dlg_res.value if dlg_res.ok else dlg_res.pending
        dialogue: Optional[DialogueBook] = None
        if candidate is not None:
            dialogue, vrep = S.verify_dialogue_against_source(
                candidate, S.split_lines(text), start_line, self.bible)
            n = sum(len(v) for v in dialogue.items.values())
            log.append(f"对白核对：保留 {n} 条，"
                       f"原文找不到 {len(vrep['dropped_not_in_source'])} 条，"
                       f"说话人不存在 {len(vrep['dropped_unknown_speaker'])} 条")
            if o.verbose:
                print(f"   [对白] 回原文核对 → 保留 {n} 条"
                      f"（丢弃 {len(vrep['dropped_not_in_source'])} 条查无实据、"
                      f"{len(vrep['dropped_unknown_speaker'])} 条说话人不存在）")
                for pid, txt in vrep["dropped_not_in_source"][:3]:
                    print(f"          ✗ {pid} 原文里没有：「{txt}」")
            if not dialogue.items:
                dialogue = None

        if dialogue is None:
            return ChapterResult(chapter_id, title, storyboard, None,
                                 "pending_human",
                                 sb_res.attempts + dlg_res.attempts,
                                 log, dlg_res.final_report)

        # ③ 状态追踪（尽力而为，失败不阻塞）
        try:
            delta = S.track_state(self.provider, self.bible, storyboard, text,
                                  verbose=o.verbose)
            self.bible = S.apply_state_delta(self.bible, delta)
            log.append(f"状态：新增伤情 {len(delta.new_wounds)}，愈合 {len(delta.healed)}")
        except Exception as e:                              # noqa: BLE001
            log.append(f"状态：跳过（{e}）")

        status = "ok" if (sb_res.attempts == 1 and dlg_res.attempts == 1) else "repaired"
        return ChapterResult(chapter_id, title, storyboard, dialogue, status,
                             sb_res.attempts + dlg_res.attempts, log)

    # ── 整本 ──────────────────────────────────────────────────
    def run(self, text: str, project_name: str = "untitled",
            chapters: int = 1, start_id: int = 2436) -> ComicProject:
        o = self.opt
        plan = S.split_chapters(self.provider, text, target=chapters,
                                start_id=start_id, verbose=o.verbose)

        lines = S.split_lines(text)
        boards: List[Storyboard] = []
        items: dict = {}
        results: List[ChapterResult] = []

        for ch in plan.chapters:
            seg = "\n".join(lines[ch.start_line - 1:ch.end_line])
            r = self.run_chapter(ch.id, ch.title, seg,
                                 start_line=ch.start_line)
            results.append(r)
            if r.storyboard:
                boards.append(r.storyboard)
            if r.dialogue:
                items.update(r.dialogue.items)

        layout = self._auto_layout(boards, plan)
        project = ComicProject(
            name=project_name,
            bible=self.bible,
            storyboards=boards,
            dialogue=DialogueBook(items=items),
            layout=layout,
        )
        project._chapter_results = results            # type: ignore[attr-defined]
        return project

    # ── 自动排版（★ 页码一经分配即冻结） ────────────────────
    @staticmethod
    def _auto_layout(boards: List[Storyboard], plan) -> Layout:
        pages: List[LayoutPage] = []
        n = 1

        # 全书卷首
        pages.append(LayoutPage(page=n, type=PageType.TITLE,
                                title="（封面待填）")); n += 1
        # 每章一个卷首 + 逐格成页
        for sb in boards:
            pages.append(LayoutPage(page=n, type=PageType.TITLE,
                                    title=f"第{sb.chapter}章")); n += 1
            for p in sb.panels:
                pages.append(LayoutPage(page=n, type=PageType.PANELS,
                                        panels=[p.id])); n += 1
        return Layout(total=len(pages), pages=pages)


# ══════════════════════════════════════════════════════════════════
# 便捷入口
# ══════════════════════════════════════════════════════════════════

def adapt_novel(
    text: str,
    bible: Bible,
    provider: LLMProvider,
    *,
    project_name: str = "untitled",
    chapters: int = 1,
    start_id: int = 2436,
    options: Optional[PipelineOptions] = None,
) -> ComicProject:
    """一行把小说变成 Comic IR"""
    pipe = ComicPipeline(provider, bible, options)
    return pipe.run(text, project_name=project_name,
                    chapters=chapters, start_id=start_id)
