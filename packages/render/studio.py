# -*- coding: utf-8 -*-
"""渲染编排：Comic IR + 素材 → 成品页 → PDF

    RenderStudio(provider).render(project, assets_dir, out_pdf)

职责：
    ① 按 IR 生成每格的提示词
    ② 调生图 provider 出图（带缓存，已存在就跳过）
    ③ 按 layout.json 逐页合成（气泡 + 技能名）
    ④ 导出 PDF
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from PIL import Image

from ..ir.models import (
    Bible,
    ComicProject,
    LayoutPage,
    PageType,
    Panel,
    Utterance,
)
from .export import save_pdf, save_long_images
from .page import (
    RenderContext,
    render_page,
)
from .prompt import build_negative, build_prompt, describe_panel
from .providers import (
    ImageError,
    ImageProvider,
    ImageRequest,
    QuotaExceeded,
)


@dataclass
class RenderReport:
    generated: int = 0
    cached: int = 0
    failed: int = 0
    pages: int = 0
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"出图 {self.generated} 张（缓存 {self.cached}，失败 {self.failed}）"
                f"｜合成 {self.pages} 页")


class RenderStudio:
    """渲染编排器"""

    def __init__(self, provider: ImageProvider,
                 panel_width: int = 2480,
                 base_font: int = 46):
        self.provider = provider
        self.panel_width = panel_width
        self.base_font = base_font

    # ── 素材 ──────────────────────────────────────────────────
    @staticmethod
    def panel_path(assets_dir: str, panel_id: str) -> str:
        d = os.path.join(assets_dir, "panels")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{panel_id}.png")

    # ── 出图 ──────────────────────────────────────────────────
    def generate_panels(
        self,
        bible: Bible,
        panels: Sequence[Panel],
        assets_dir: str,
        *,
        force: bool = False,
        refs_of: Optional[Callable[[Panel], List[str]]] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        stop_on_quota: bool = True,
    ) -> RenderReport:
        """逐格出图（串行 —— 并行会被多数服务限流）"""
        rep = RenderReport()
        total = len(panels)
        for i, p in enumerate(panels, 1):
            out = self.panel_path(assets_dir, p.id)
            if os.path.exists(out) and not force:
                rep.cached += 1
                if on_progress:
                    on_progress(i, total, f"{p.id} 已存在")
                continue
            prompt = build_prompt(bible, p)
            req = ImageRequest(
                prompt=prompt,
                size=p.size.value,
                ref_images=(refs_of(p) if refs_of else []),
                negative=build_negative(p),
            )
            try:
                res = self.provider.generate_to_file(req, out)
                if res.ok:
                    rep.generated += 1
                    if on_progress:
                        on_progress(i, total, f"{p.id} 已生成 {res.latency_ms}ms")
                else:                                       # pragma: no cover
                    rep.failed += 1
                    rep.errors.append(f"{p.id}: provider 返回空图")
            except QuotaExceeded as e:
                rep.failed += 1
                rep.errors.append(f"{p.id}: 额度超限 —— {e}")
                if stop_on_quota:
                    if on_progress:
                        on_progress(i, total, "额度超限，停止")
                    break
            except ImageError as e:
                rep.failed += 1
                rep.errors.append(f"{p.id}: {e}")
                if on_progress:
                    on_progress(i, total, f"{p.id} 失败：{e}")
        return rep

    # ── 合成页面 ──────────────────────────────────────────────
    def compose_pages(
        self,
        project: ComicProject,
        assets_dir: str,
        *,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[Image.Image]:
        bible = project.bible
        panels: Dict[str, Panel] = {}
        for sb in project.storyboards:
            for p in sb.panels:
                panels[p.id] = p

        ctx = RenderContext(
            bible=bible,
            panels=panels,
            utterances=project.dialogue.items,
            panel_width=self.panel_width,
            base_font=self.base_font,
        )

        pages: List[Image.Image] = []
        total = len(project.layout.pages)
        for i, lp in enumerate(project.layout.pages, 1):
            # 载入该页需要的单格图
            images: Dict[str, Image.Image] = {}
            for pid in lp.panels:
                fp = self.panel_path(assets_dir, pid)
                if os.path.exists(fp):
                    images[pid] = Image.open(fp).convert("RGB")
            pg = render_page(ctx, lp, images)
            pages.append(pg)
            if on_progress:
                on_progress(i, total, f"第 {lp.page} 页 {lp.type.value}")
        return pages

    # ── 一把梭 ────────────────────────────────────────────────
    def render(
        self,
        project: ComicProject,
        assets_dir: str,
        out_pdf: str,
        *,
        force: bool = False,
        refs_of: Optional[Callable[[Panel], List[str]]] = None,
        on_progress: Optional[Callable[[str, int, int, str], None]] = None,
        long_images: int = 0,
        long_outdir: Optional[str] = None,
    ) -> RenderReport:
        """完整渲染：出图 → 合成 → 导出"""
        def _p(stage: str):
            def _f(i, t, msg):
                if on_progress:
                    on_progress(stage, i, t, msg)
            return _f

        all_panels: List[Panel] = [p for sb in project.storyboards for p in sb.panels]

        rep = self.generate_panels(project.bible, all_panels, assets_dir,
                                   force=force, refs_of=refs_of,
                                   on_progress=_p("生成"))
        pages = self.compose_pages(project, assets_dir, on_progress=_p("合成"))
        rep.pages = len(pages)

        os.makedirs(os.path.dirname(os.path.abspath(out_pdf)) or ".", exist_ok=True)
        save_pdf(pages, out_pdf)

        if long_images and long_outdir:
            save_long_images(pages, long_outdir, count=long_images)

        return rep
