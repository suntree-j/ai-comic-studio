# -*- coding: utf-8 -*-
"""★ 真实完整闭环测试：小说原文 → LLM 改编 → Comic IR → 真实生图 → 成品页

这是整个项目唯一没跑通过的一环：
    「5 个生图适配器都写好了，但线上用的是 mock」

本脚本用硅基流动同时做 LLM 与生图，一个 key 跑通全链路：
    ① 切出小说章节原文
    ② LLM 改编 → storyboard + dialogue（含 12 条规则自修复）
    ③ 校验 IR
    ④ 用 Comic IR 的提示词**真实生图**
    ⑤ 叠气泡 → 成品漫画页
    ⑥ 存 PDF

用法：
    set SILICONFLOW_API_KEY=sk-...
    python scripts/run_real_pipeline.py --chapter 2436
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEFAULT_NOVEL = r"C:\Users\jsy28\Desktop\新建 文本文档.txt"


def cut_chapter(path: str, chapter: int) -> tuple:
    """从整本小说里切出一章（按「第N章」标记）"""
    txt = open(path, encoding="utf-8", errors="replace").read()
    lines = txt.splitlines()
    starts = {}
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*第(\d+)章", ln)
        if m:
            starts[int(m.group(1))] = i
    if chapter not in starts:
        raise SystemExit(f"找不到第 {chapter} 章。有：{sorted(starts)[:5]}…")
    begin = starts[chapter]
    after = [n for n in sorted(starts) if n > chapter]
    end = starts[after[0]] if after else len(lines)
    body = "\n".join(lines[begin:end]).strip()
    title = lines[begin].strip()
    return title, body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", type=int, default=2436)
    ap.add_argument("--novel", default=DEFAULT_NOVEL)
    ap.add_argument("--llm", default="deepseek-ai/DeepSeek-V3.2")
    ap.add_argument("--image", default="Kwai-Kolors/Kolors")
    ap.add_argument("--panels", type=int, default=4)
    # ★ 硅基流动按 IPM（每分钟请求数）限流，连打 8-9 格很容易撞上。
    #   实测第 8 格起返回 429「IPM limit reached」。
    #   默认留 6 秒间隔：既不打满限流，也不至于太慢。
    ap.add_argument("--delay", type=float, default=6.0,
                    help="每格之间的间隔秒数（防 IPM 限流）")
    ap.add_argument("--retries", type=int, default=3,
                    help="被限流时重试几次")
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "real_pipeline"))
    ap.add_argument("--api-key", default=os.environ.get("SILICONFLOW_API_KEY", ""))
    a = ap.parse_args()

    if not a.api_key:
        print("  ❌ 需要 SILICONFLOW_API_KEY")
        return 1
    os.environ["SILICONFLOW_API_KEY"] = a.api_key
    os.makedirs(a.out, exist_ok=True)

    title, body = cut_chapter(a.novel, a.chapter)
    print("=" * 68)
    print(f" 真实闭环 · {title}")
    print("=" * 68)
    print(f"   LLM    ：{a.llm}")
    print(f"   生图   ：{a.image}")
    print(f"   原文   ：{len(body)} 字")
    print(f"   输出   ：{a.out}")

    # ── ② LLM 改编 ────────────────────────────────────────
    from packages.agent import ComicPipeline, PipelineOptions, get_provider
    from packages.cli import load_bible, save_project

    bible_path = os.path.join(ROOT, "projects", "showcase", "bible.json")
    bible = load_bible(bible_path)
    print(f"\n② LLM 改编（bible：{len(bible.characters)} 个角色）")
    t0 = time.time()
    prov = get_provider("siliconflow", model=a.llm, api_key=a.api_key)
    pipe = ComicPipeline(prov, bible, PipelineOptions(
        panel_count_hint=a.panels, max_repair_rounds=2, verbose=True))
    project = pipe.run(body, project_name=f"real_{a.chapter}",
                       chapters=1, start_id=a.chapter)
    print(f"   ✅ 改编完成 {time.time() - t0:.1f}s")

    results = getattr(project, "_chapter_results", [])
    for r in results:
        icon = {"ok": "✅", "repaired": "🔧",
                "pending_human": "⚠️", "failed": "❌"}.get(r.status, "?")
        print(f"   {icon} 第{r.chapter_id}章 {r.status}（{r.attempts} 轮）")

    # ── ③ 校验 ────────────────────────────────────────────
    from packages.ir import validate
    rep = validate(project)
    print(f"\n③ IR 校验：{rep.summary}")
    for e in rep.errors[:6]:
        print(f"   ⚠️  {e.rule} {e.message[:90]}")

    boards = project.storyboards
    panels = [p for sb in boards for p in sb.panels]
    utts = project.dialogue.items          # {panel_id: [Utterance]}
    print(f"   分镜 {len(panels)} 格，台词共 "
          f"{sum(len(v) for v in utts.values())} 条")
    for p in panels:
        dlg = utts.get(p.id, [])
        print(f"     {p.id}  {p.size.value}  {p.shot.value}  "
              f"台词 {len(dlg)} 条")
        for d in dlg[:2]:
            print(f"        「{d.text[:44]}」")

    # ── ④ 真实生图 ────────────────────────────────────────
    from packages.render.prompt import build_prompt, build_negative
    from packages.render.providers import (
        ImageRequest, QuotaExceeded, RateLimited, get_image_provider)

    iprov = get_image_provider("siliconflow", api_key=a.api_key, model=a.image)
    print(f"\n④ 真实生图（{len(panels)} 格，串行，间隔 {a.delay:.0f}s）")
    panel_dir = os.path.join(a.out, "panels")
    os.makedirs(panel_dir, exist_ok=True)
    images, failed = [], []
    for i, p in enumerate(panels, 1):
        prompt = build_prompt(bible, p)
        fp = os.path.join(panel_dir, f"{p.id}.png")
        # ★ 撞上限流就等一下重试，而不是丢掉这一格。
        #   硅基流动按 IPM（每分钟请求数）限流，8-9 格连着打很容易撞上。
        res, rate_err = None, None
        for attempt in range(a.retries + 1):
            t1 = time.time()
            try:
                res = iprov.generate(ImageRequest(
                    prompt=prompt, size=p.size.value,
                    negative=build_negative(p)))
                break
            except RateLimited as e:
                rate_err = e
                if attempt >= a.retries:
                    break
                wait = a.delay * 2 * (attempt + 1)
                print(f"   ⏳ [{i}/{len(panels)}] {p.id} 被限流，"
                      f"等 {wait:.0f}s 再试（第 {attempt + 2} 次）")
                time.sleep(wait)
            except QuotaExceeded as e:
                print(f"   ❌ [{i}/{len(panels)}] {p.id} 额度不足：{str(e)[:110]}")
                print("      ⛔ 额度问题重试没意义，停止。")
                failed.append(p.id)
                break
            except Exception as e:                          # noqa: BLE001
                print(f"   ❌ [{i}/{len(panels)}] {p.id} "
                      f"{type(e).__name__}: {str(e)[:130]}")
                failed.append(p.id)
                break

        if rate_err is not None and res is None:
            print(f"   ❌ [{i}/{len(panels)}] {p.id} 重试 {a.retries} 次"
                  f"仍被限流")
            failed.append(p.id)
        elif res is not None and res.ok:
            res.image.save(fp)
            images.append((p, res.image))
            print(f"   ✅ [{i}/{len(panels)}] {p.id}  {res.image.size}  "
                  f"{time.time() - t1:.1f}s  seed={res.seed}")
        elif res is not None:
            print(f"   ❌ [{i}/{len(panels)}] {p.id} 返回空图")
            failed.append(p.id)

        # 主动限速：别把 IPM 打满
        if a.delay and i < len(panels):
            time.sleep(a.delay)

    print(f"   出图 {len(images)}/{len(panels)}"
          + (f"，失败 {failed}" if failed else ""))
    if not images:
        print("\n   ❌ 一张都没出成")
        return 1

    # ── ⑤ 合成页面 ────────────────────────────────────────
    from packages.render.page import RenderContext, PANEL_WIDTH, render_page

    print(f"\n⑤ 合成页面（叠气泡 + 技能名）")
    pages_dir = os.path.join(a.out, "pages")
    os.makedirs(pages_dir, exist_ok=True)

    all_panels = {p.id: p for p in panels}
    ctx = RenderContext(bible=bible, panels=all_panels,
                        utterances=project.dialogue.items,
                        panel_width=PANEL_WIDTH, base_font=46)
    img_map = {p.id: im for p, im in images}

    page_imgs = []
    for lp in project.layout.pages:
        pg = render_page(ctx, lp, img_map)
        page_imgs.append(pg)
        print(f"   第 {lp.page} 页 {lp.type.value}  {pg.size}")
    if not page_imgs:
        # 没有 layout 就逐格输出
        for p, im in images:
            page_imgs.append(im)
        print(f"   ⚠️ layout 为空，退化为逐格输出 {len(page_imgs)} 张")

    made = []
    for i, im in enumerate(page_imgs, 1):
        fp = os.path.join(pages_dir, f"page_{i:02d}.png")
        im.save(fp)
        made.append(fp)
    print(f"   ✅ {len(made)} 页 → {pages_dir}")

    # ── ⑥ 存 IR + PDF ─────────────────────────────────────
    save_project(project, os.path.join(a.out, "ir"))
    print(f"   ✅ IR 已写 → {a.out}/ir")

    from packages.render.export import save_pdf

    pdf = os.path.join(a.out, f"chapter_{a.chapter}.pdf")
    save_pdf(page_imgs, pdf)
    print(f"   ✅ PDF → {pdf}  {os.path.getsize(pdf) // 1024} KB")

    # 拼一张总览图，方便一眼看效果
    try:
        from PIL import Image as _I
        H = 1300
        thumbs = [im.resize((int(im.width * H / im.height), H), _I.LANCZOS)
                  for im in page_imgs]
        gap = 16
        total = sum(t.width for t in thumbs) + gap * (len(thumbs) + 1)
        cv = _I.new("RGB", (total, H + 2 * gap), (18, 22, 28))
        x = gap
        for t in thumbs:
            cv.paste(t, (x, gap))
            x += t.width + gap
        ov = os.path.join(a.out, "_overview.png")
        cv.save(ov)
        print(f"   ✅ 总览图 → {ov}  {cv.size}")
    except Exception as e:                                  # noqa: BLE001
        print(f"   ⚠️ 总览图失败：{e}")

    print()
    print("=" * 68)
    print(f" ✅ 闭环跑通：{len(images)}/{len(panels)} 格真实出图，"
          f"{len(page_imgs)} 页")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
