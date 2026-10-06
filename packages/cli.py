# -*- coding: utf-8 -*-
"""AI Comic Studio 命令行

用法：
    # 校验一个项目
    python -m packages.cli validate project/

    # 小说 → Comic IR
    python -m packages.cli adapt 原文/2436.txt --bible project/bible.json \
        --provider deepseek --out project/ --chapters 1

    # 用 Mock 离线跑通流程（演示 / 测试）
    python -m packages.cli adapt 原文/2436.txt --bible project/bible.json \
        --provider mock --out /tmp/demo/

    # 打印可用 Provider
    python -m packages.cli providers
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .ir import Bible, ComicProject, Layout, Storyboard, DialogueBook, validate
from .ir.validator import RULES, Severity


# ══════════════════════════════════════════════════════════════════
# 读写
# ══════════════════════════════════════════════════════════════════

def load_bible(path: str) -> Bible:
    return Bible.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_project(project: ComicProject, outdir: str) -> None:
    d = Path(outdir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "bible.json").write_text(
        project.bible.model_dump_json(indent=1), encoding="utf-8")
    (d / "storyboard.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in project.storyboards],
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "dialogue.json").write_text(
        project.dialogue.model_dump_json(indent=1), encoding="utf-8")
    (d / "layout.json").write_text(
        project.layout.model_dump_json(indent=1), encoding="utf-8")


def load_project(path: str) -> ComicProject:
    d = Path(path)
    bible = Bible.model_validate_json((d / "bible.json").read_text(encoding="utf-8"))
    boards_raw = json.loads((d / "storyboard.json").read_text(encoding="utf-8"))
    boards = [Storyboard.model_validate(x) for x in boards_raw]
    dialogue = DialogueBook.model_validate_json(
        (d / "dialogue.json").read_text(encoding="utf-8"))
    layout = Layout.model_validate_json(
        (d / "layout.json").read_text(encoding="utf-8"))
    return ComicProject(name=d.name, bible=bible, storyboards=boards,
                        dialogue=dialogue, layout=layout)


# ══════════════════════════════════════════════════════════════════
# 子命令
# ══════════════════════════════════════════════════════════════════

def cmd_providers(_args) -> int:
    from .agent import PROVIDER_NAMES
    print("可用 LLM Provider：")
    for n in PROVIDER_NAMES:
        print(f"  - {n}")
    print("\n用环境变量提供 key，例如 DEEPSEEK_API_KEY / OPENAI_API_KEY")
    return 0


def cmd_rules(_args) -> int:
    print(f"Comic IR 业务规则（共 {len(RULES)} 条）：\n")
    for k in sorted(RULES):
        print(f"  {k}  {RULES[k]}")
    return 0


def cmd_validate(args) -> int:
    project = load_project(args.project)
    src = None
    if args.source:
        src = Path(args.source).read_text(encoding="utf-8").splitlines()
    report = validate(project, src)
    print(f"项目：{project.name}")
    print(f"分镜：{len(project.storyboards)} 章 / "
          f"{sum(len(s.panels) for s in project.storyboards)} 格")
    print(f"对白：{sum(len(v) for v in project.dialogue.items.values())} 条")
    print(f"页数：{project.layout.total}")
    print()
    if not report.errors:
        print("✅ 校验通过")
        return 0
    for sev in (Severity.ERROR, Severity.WARNING):
        items = report.by_severity(sev)
        if not items:
            continue
        print(f"── {sev.value.upper()}（{len(items)}）──")
        for e in items:
            print(f"  {e}")
    print()
    print(report.summary())
    return 0 if report.ok else 1


def cmd_adapt(args) -> int:
    from .agent import ComicPipeline, PipelineOptions, get_provider

    text = Path(args.text).read_text(encoding="utf-8")
    bible = load_bible(args.bible)
    provider = get_provider(args.provider, model=args.model,
                            api_key=args.api_key, base_url=args.base_url)

    pipe = ComicPipeline(provider, bible, PipelineOptions(
        panel_count_hint=args.panels,
        max_repair_rounds=args.repair_rounds,
        verbose=True,
    ))
    project = pipe.run(text, project_name=args.name,
                       chapters=args.chapters, start_id=args.start_id)

    save_project(project, args.out)
    print(f"\n✅ 已写出 {args.out}/  (bible / storyboard / dialogue / layout)")

    results = getattr(project, "_chapter_results", [])
    if results:
        print("\n各章状态：")
        for r in results:
            icon = {"ok": "✅", "repaired": "🔧",
                    "pending_human": "⚠️", "failed": "❌"}.get(r.status, "?")
            print(f"  {icon} 第{r.chapter_id}章《{r.title}》 "
                  f"{r.status}（{r.attempts} 轮）")
    return 0


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="comic", description="AI Comic Studio · 小说 → 漫画 IR")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("providers", help="列出可用 LLM Provider").set_defaults(
        func=cmd_providers)
    sub.add_parser("rules", help="列出业务校验规则").set_defaults(func=cmd_rules)

    v = sub.add_parser("validate", help="校验一个项目目录")
    v.add_argument("project", help="含 bible/storyboard/dialogue/layout 的目录")
    v.add_argument("--source", help="原文文件（给了才能校验对白可追溯 IR-007）")
    v.set_defaults(func=cmd_validate)

    a = sub.add_parser("adapt", help="小说文本 → Comic IR")
    a.add_argument("text", help="原文 txt")
    a.add_argument("--bible", required=True, help="bible.json")
    a.add_argument("--out", required=True, help="输出目录")
    a.add_argument("--provider", default="deepseek")
    a.add_argument("--model", default=None)
    a.add_argument("--api-key", default=None)
    a.add_argument("--base-url", default=None)
    a.add_argument("--name", default="untitled")
    a.add_argument("--chapters", type=int, default=1)
    a.add_argument("--start-id", type=int, default=2436)
    a.add_argument("--panels", type=int, default=10, help="每章预计格数")
    a.add_argument("--repair-rounds", type=int, default=3)
    a.set_defaults(func=cmd_adapt)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
