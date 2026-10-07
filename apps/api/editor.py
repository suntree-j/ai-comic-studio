# -*- coding: utf-8 -*-
"""画布编辑器 API（Office 式）

## 与 apps/api/server.py 的关系
    两者挂在同一个 FastAPI 应用下，但**互不依赖**：
      ·  `/api/*`        —— 漫画 IR（小说自动改编）
      ·  `/api/edit/*`   —— 画布编辑器（手工从空白页做）

## 设计要点
    ① **服务端渲染**：前端只发坐标，排版与绘制都在服务端 ——
       保证「所见 = 导出」，前端不需要实现一套排版
    ② **服务端返回包围盒**：气泡高度由文字决定，前端拿服务端返回的
       boxes 画选择框，前后端不会各算一套而错位
    ③ **页码是身份，order 是顺序**：调顺序改 order，number 永不变
    ④ **撤销栈**：直接存整个 project.json 快照（文件很小，实现简单可靠）
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import zipfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from packages.editor import (
    BubbleElement, EditProject, EditStore, ImageElement, Page, ShapeElement,
    TextElement, new_id, parse_element, render_page,
)

router = APIRouter(prefix="/api/edit", tags=["editor"])

WORKSPACE = os.environ.get(
    "COMIC_WORKSPACE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "works"))

store = EditStore(WORKSPACE)

#: 撤销栈：项目名 → [project.json 快照]
_undo: Dict[str, List[str]] = {}
_MAX_UNDO = 40


# ══════════════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════════════

def _load(name: str) -> EditProject:
    p = store.load(name)
    if p is None:
        raise HTTPException(404, f"项目 {name} 不存在")
    return p


def _page(project: EditProject, page_id: str) -> Page:
    pg = project.page(page_id)
    if pg is None:
        raise HTTPException(404, "页面不存在")
    return pg


def _asset_loader(project: EditProject):
    """交给渲染器的取图函数：asset_id → PIL.Image"""
    by_id = {a.id: a for a in project.assets}

    def load(asset_id: str):
        a = by_id.get(asset_id)
        if a is None:
            return None
        return store.load_asset_image(project.name, a.stored_name)
    return load


def snapshot(name: str) -> None:
    """把当前 project.json 压进撤销栈（在修改前调用）"""
    fp = os.path.join(store.dir_of(name), "project.json")
    if not os.path.isfile(fp):
        return
    raw = open(fp, encoding="utf-8").read()
    stack = _undo.setdefault(name, [])
    stack.append(raw)
    del stack[:-_MAX_UNDO]


def _find_page_of(project: EditProject, element_id: str) -> Page:
    for pg in project.pages:
        if any(e.id == element_id for e in pg.elements):
            return pg
    raise HTTPException(404, "元素不存在")


# ══════════════════════════════════════════════════════════════════
# 项目
# ══════════════════════════════════════════════════════════════════

@router.get("/projects")
def list_projects():
    return store.list()


class CreateReq(BaseModel):
    name: str
    title: str = ""
    width: int = 1400
    height: int = 2000
    first_page: bool = True


@router.post("/projects")
def create_project(req: CreateReq):
    try:
        p = store.create(req.name, req.title, (req.width, req.height),
                         req.first_page)
    except FileExistsError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return p.model_dump(mode="json")


@router.get("/projects/{name}")
def get_project(name: str):
    p = _load(name)
    return {**p.model_dump(mode="json"), "stats": p.stats()}


class PatchReq(BaseModel):
    title: Optional[str] = None
    canvas_width: Optional[int] = None
    canvas_height: Optional[int] = None
    default_font_size: Optional[int] = None


@router.patch("/projects/{name}")
def patch_project(name: str, req: PatchReq):
    p = _load(name)
    snapshot(name)
    for k, v in req.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    store.save(p)
    return {"ok": True, "project": p.model_dump(mode="json")}


@router.delete("/projects/{name}")
def delete_project(name: str):
    if not store.delete(name):
        raise HTTPException(404, "项目不存在")
    _undo.pop(name, None)
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# 素材（上传图片）
# ══════════════════════════════════════════════════════════════════

@router.post("/projects/{name}/assets")
async def upload_assets(name: str, files: List[UploadFile] = File(...)):
    """批量上传图片（支持多选 / 拖入）"""
    p = _load(name)
    added, failed = [], []
    for f in files:
        try:
            data = await f.read()
            if not data:
                failed.append({"filename": f.filename, "error": "空文件"})
                continue
            a = store.add_asset(name, f.filename or "unnamed", data)
            p.assets.append(a)
            added.append(a)
        except Exception as e:                              # noqa: BLE001
            failed.append({"filename": f.filename, "reason": str(e)[:120]})
    store.save(p)
    return {"ok": True, "added": [a.model_dump(mode="json") for a in added],
            "failed": failed, "total": len(p.assets)}


@router.get("/projects/{name}/assets/{asset_id}/raw")
def asset_raw(name: str, asset_id: str, w: int = 0):
    """原图（缩略图用 ?w=320）"""
    p = _load(name)
    a = p.asset(asset_id)
    if a is None:
        raise HTTPException(404, "素材不存在")
    img = store.load_asset_image(name, a.stored_name)
    if img is None:
        raise HTTPException(404, "素材文件丢失")
    if w and img.width > w:
        img = img.resize((w, max(1, int(img.height * w / img.width))))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    return Response(buf.getvalue(), media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.delete("/projects/{name}/assets/{asset_id}")
def delete_asset(name: str, asset_id: str):
    p = _load(name)
    a = p.asset(asset_id)
    if a is None:
        raise HTTPException(404, "素材不存在")
    used = [e.id for pg in p.pages for e in pg.elements
            if isinstance(e, ImageElement) and e.asset_id == asset_id]
    if used:
        raise HTTPException(409, f"该素材正在被 {len(used)} 个元素使用，先删元素")
    snapshot(name)
    fp = store.asset_path(name, a.stored_name)
    if os.path.isfile(fp):
        os.remove(fp)
    p.assets = [x for x in p.assets if x.id != asset_id]
    store.save(p)
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# 页面
# ══════════════════════════════════════════════════════════════════

class PageReq(BaseModel):
    title: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    background: Optional[str] = None
    note: Optional[str] = None


@router.post("/projects/{name}/pages")
def add_page(name: str, req: PageReq):
    p = _load(name)
    snapshot(name)
    n = p.next_number()
    pg = Page(number=n, order=len(p.pages), title=req.title or f"第 {n} 页",
              width=req.width or p.canvas_width,
              height=req.height or p.canvas_height,
              background=req.background or "#ffffff", note=req.note or "")
    p.pages.append(pg)
    store.save(p)
    return {"ok": True, "page": pg.model_dump(mode="json")}


@router.patch("/projects/{name}/pages/{page_id}")
def patch_page(name: str, page_id: str, req: PageReq):
    p = _load(name)
    pg = _page(p, page_id)
    snapshot(name)
    for k, v in req.model_dump(exclude_none=True).items():
        setattr(pg, k, v)
    store.save(p)
    return {"ok": True, "page": pg.model_dump(mode="json")}


@router.delete("/projects/{name}/pages/{page_id}")
def delete_page(name: str, page_id: str):
    p = _load(name)
    _page(p, page_id)
    snapshot(name)
    p.pages = [x for x in p.pages if x.id != page_id]
    for i, x in enumerate(p.ordered_pages()):
        x.order = i
    store.save(p)
    return {"ok": True, "pages": len(p.pages)}


@router.post("/projects/{name}/pages/{page_id}/duplicate")
def duplicate_page(name: str, page_id: str):
    p = _load(name)
    src = _page(p, page_id)
    snapshot(name)
    n = p.next_number()
    new = src.model_copy(deep=True)
    new.id = new_id()                                    # 新 id
    new.number = n
    new.order = len(p.pages)
    new.title = f"{src.title} 副本"
    for e in new.elements:                               # 元素也要新 id
        e.id = e.__class__().id
    p.pages.append(new)
    store.save(p)
    return {"ok": True, "page": new.model_dump(mode="json")}


class ReorderReq(BaseModel):
    """按给定的 id 顺序重排页面（★ 只改 order，不改 number）"""
    page_ids: List[str]


@router.post("/projects/{name}/pages/reorder")
def reorder_pages(name: str, req: ReorderReq):
    p = _load(name)
    ids = {x.id for x in p.pages}
    if set(req.page_ids) != ids:
        raise HTTPException(400, "页面 id 列表与项目不一致（数量或内容对不上）")
    snapshot(name)
    pos = {pid: i for i, pid in enumerate(req.page_ids)}
    for pg in p.pages:
        pg.order = pos[pg.id]
    store.save(p)
    return {"ok": True, "order": [x.id for x in p.ordered_pages()]}


@router.post("/projects/{name}/pages/{page_id}/move")
def move_page(name: str, page_id: str, delta: int = Query(...)):
    """上移/下移一页（delta = -1 / +1）"""
    p = _load(name)
    ordered = p.ordered_pages()
    idx = next((i for i, x in enumerate(ordered) if x.id == page_id), None)
    if idx is None:
        raise HTTPException(404, "页面不存在")
    j = max(0, min(len(ordered) - 1, idx + delta))
    if j == idx:
        return {"ok": True, "moved": 0}
    snapshot(name)
    ordered.insert(j, ordered.pop(idx))
    for i, x in enumerate(ordered):
        x.order = i
    store.save(p)
    return {"ok": True, "moved": j - idx}


# ══════════════════════════════════════════════════════════════════
# 元素
# ══════════════════════════════════════════════════════════════════

class AddElementReq(BaseModel):
    kind: str
    asset_id: Optional[str] = None
    text: Optional[str] = None
    speaker: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    w: Optional[float] = None
    h: Optional[float] = None
    auto_place: bool = False        # 让服务端找空位（复用气泡避让算法）
    props: Dict[str, Any] = Field(default_factory=dict)


@router.post("/projects/{name}/pages/{page_id}/elements")
def add_element(name: str, page_id: str, req: AddElementReq):
    p = _load(name)
    pg = _page(p, page_id)
    snapshot(name)

    z = (max((e.z for e in pg.elements), default=-1)) + 1
    # ★ 必须显式带上 kind：虽然子类有默认值，但从 dict 构造时
    #   kind 是 Literal 判别字段，缺了会报「未知的元素类型：None」
    base: Dict[str, Any] = {"kind": req.kind, "z": z}

    if req.kind == "image":
        a = p.asset(req.asset_id or "")
        if a is None:
            raise HTTPException(400, "需要有效的 asset_id")
        w = req.w if req.w else 0.6
        base.update(asset_id=a.id, w=w,
                    h=(req.h if req.h else round(w * pg.width / pg.height
                                                 * a.height / max(1, a.width), 5)),
                    x=(req.x if req.x is not None else 0.06),
                    y=(req.y if req.y is not None else 0.06),
                    name=a.filename[:28])
    elif req.kind == "bubble":
        x, y = req.x, req.y
        if req.auto_place or x is None or y is None:
            from packages.editor import auto_place_bubble
            ax, ay = auto_place_bubble(pg, _asset_loader(p))
            x = x if x is not None else ax
            y = y if y is not None else ay
        base.update(text=req.text or "在这里输入台词",
                    speaker=req.speaker or "",
                    x=x or 0.06, y=y or 0.06,
                    w=req.w or 0.34,
                    font_size=p.default_font_size)
    elif req.kind == "text":
        base.update(text=req.text or "标题",
                    x=(req.x if req.x is not None else 0.1),
                    y=(req.y if req.y is not None else 0.1),
                    w=req.w or 0.5)
    elif req.kind == "shape":
        base.update(x=(req.x if req.x is not None else 0.1),
                    y=(req.y if req.y is not None else 0.1),
                    w=req.w or 0.3, h=req.h or 0.08)
    else:
        raise HTTPException(400, f"未知元素类型：{req.kind}")

    base.update({k: v for k, v in req.props.items() if v is not None})
    try:
        el = parse_element(base)
    except Exception as e:                                  # noqa: BLE001
        raise HTTPException(400, f"元素参数不合法：{e}") from e

    pg.elements.append(el)
    pg.normalize_z()
    store.save(p)
    return {"ok": True, "element": el.model_dump(mode="json"),
            "page_id": pg.id}


class PatchElementReq(BaseModel):
    """只改传进来的字段（其余保持）"""
    props: Dict[str, Any] = Field(default_factory=dict)


@router.patch("/projects/{name}/elements/{element_id}")
def patch_element(name: str, element_id: str, req: PatchElementReq):
    p = _load(name)
    pg = _find_page_of(p, element_id)
    el = next(e for e in pg.elements if e.id == element_id)

    data = el.model_dump(mode="json")
    for k, v in req.props.items():
        if k in ("id", "kind"):
            continue
        data[k] = v
    try:
        new = parse_element(data)
    except Exception as e:                                  # noqa: BLE001
        raise HTTPException(400, f"参数不合法：{e}") from e

    snapshot(name)
    pg.elements = [new if e.id == element_id else e for e in pg.elements]
    store.save(p)
    return {"ok": True, "element": new.model_dump(mode="json")}


@router.delete("/projects/{name}/elements/{element_id}")
def delete_element(name: str, element_id: str):
    p = _load(name)
    pg = _find_page_of(p, element_id)
    snapshot(name)
    pg.elements = [e for e in pg.elements if e.id != element_id]
    pg.normalize_z()
    store.save(p)
    return {"ok": True}


@router.post("/projects/{name}/elements/{element_id}/duplicate")
def duplicate_element(name: str, element_id: str):
    p = _load(name)
    pg = _find_page_of(p, element_id)
    src = next(e for e in pg.elements if e.id == element_id)
    snapshot(name)
    new = src.model_copy(deep=True)
    new.id = new_id()
    new.x = round(min(0.95, new.x + 0.03), 5)
    new.y = round(min(0.95, new.y + 0.03), 5)
    new.z = max((e.z for e in pg.elements), default=-1) + 1
    pg.elements.append(new)
    pg.normalize_z()
    store.save(p)
    return {"ok": True, "element": new.model_dump(mode="json")}


class OrderReq(BaseModel):
    """元素顺序：从下到上（先画的在前）"""
    element_ids: List[str]


@router.post("/projects/{name}/elements/order")
def reorder_elements(name: str, req: OrderReq):
    p = _load(name)
    # 这些 id 必须都属于同一页
    pages = [pg for pg in p.pages
             if any(e.id in set(req.element_ids) for e in pg.elements)]
    if len(pages) != 1:
        raise HTTPException(400, "元素顺序只能在同一页内调整")
    pg = pages[0]
    if set(req.element_ids) != {e.id for e in pg.elements}:
        raise HTTPException(400, "元素 id 列表与该页不一致")
    snapshot(name)
    pos = {eid: i for i, eid in enumerate(req.element_ids)}
    for e in pg.elements:
        e.z = pos[e.id]
    pg.normalize_z()
    store.save(p)
    return {"ok": True, "order": [e.id for e in pg.sorted_elements()]}


class BatchMoveReq(BaseModel):
    """批量改位置（拖动多个 / 对齐）"""
    moves: Dict[str, Dict[str, float]]      # element_id -> {x, y}


@router.post("/projects/{name}/elements/batch-move")
def batch_move(name: str, req: BatchMoveReq):
    p = _load(name)
    snapshot(name)
    n = 0
    for eid, xy in req.moves.items():
        pg = _find_page_of(p, eid)
        for e in pg.elements:
            if e.id == eid:
                if "x" in xy:
                    e.x = round(max(-0.5, min(1.5, xy["x"])), 5)
                if "y" in xy:
                    e.y = round(max(-0.5, min(1.5, xy["y"])), 5)
                n += 1
    store.save(p)
    return {"ok": True, "moved": n}


# ══════════════════════════════════════════════════════════════════
# 撤销 / 重做
# ══════════════════════════════════════════════════════════════════

@router.post("/projects/{name}/undo")
def undo(name: str):
    stack = _undo.get(name) or []
    if not stack:
        return {"ok": False, "reason": "没有可撤销的操作"}
    raw = stack.pop()
    with open(os.path.join(store.dir_of(name), "project.json"), "w",
              encoding="utf-8") as f:
        f.write(raw)
    p = _load(name)
    return {"ok": True, "project": p.model_dump(mode="json"),
            "remaining": len(stack)}


@router.get("/projects/{name}/undo-depth")
def undo_depth(name: str):
    return {"depth": len(_undo.get(name) or [])}


# ══════════════════════════════════════════════════════════════════
# 渲染（预览 / 导出共用）
# ══════════════════════════════════════════════════════════════════

@router.get("/projects/{name}/pages/{page_id}/render.png")
def render_png(name: str, page_id: str, w: int = 1240,
               boxes: int = 0, t: float = 0):
    """页面预览图

    `boxes=1` 时把元素包围盒放进响应头 `X-Element-Boxes`（base64 JSON）——
    前端据此画选择框，保证与服务端排版一致。
    """
    p = _load(name)
    pg = _page(p, page_id)
    img, boxmap = render_page(pg, _asset_loader(p), target_width=w,
                              with_boxes=bool(boxes))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, optimize=True)
    headers = {"Cache-Control": "no-store"}
    if boxes:
        payload = base64.b64encode(
            json.dumps(boxmap, separators=(",", ":")).encode()).decode()
        headers["X-Element-Boxes"] = payload
    return Response(buf.getvalue(), media_type="image/jpeg", headers=headers)


@router.get("/projects/{name}/elements-geometry")
def elements_geometry(name: str, page_id: str, w: int = 1240):
    """只算几何不算图（拖拽时快速取新位置）"""
    p = _load(name)
    pg = _page(p, page_id)
    _, boxmap = render_page(pg, _asset_loader(p), target_width=w,
                            with_boxes=True)
    return {"boxes": boxmap}


# ══════════════════════════════════════════════════════════════════
# 导出
# ══════════════════════════════════════════════════════════════════

@router.post("/projects/{name}/export")
def export(name: str, fmt: str = "pdf", width: int = 1240,
           long_count: int = 0):
    from packages.render.export import save_long_images, save_pdf

    p = _load(name)
    if not p.pages:
        raise HTTPException(400, "项目里没有页面")
    out = store.out_dir(name)
    pages = [render_page(pg, _asset_loader(p), target_width=width)[0]
             for pg in p.ordered_pages()]

    if fmt == "pdf":
        fp = os.path.join(out, "comic.pdf")
        save_pdf(pages, fp)
        return {"ok": True, "file": f"/api/edit/projects/{name}/download/comic.pdf",
                "pages": len(pages)}
    if fmt == "long":
        paths = save_long_images(pages, os.path.join(out, "longs"),
                                 count=long_count or 9, width=min(width, 1000))
        return {"ok": True,
                "files": [f"/api/edit/projects/{name}/download/longs/"
                          f"{os.path.basename(x)}" for x in paths]}
    if fmt == "zip":
        fp = os.path.join(out, "pages.zip")
        with zipfile.ZipFile(fp, "w", zipfile.ZIP_DEFLATED) as z:
            for i, img in enumerate(pages, 1):
                b = io.BytesIO()
                img.save(b, "PNG")
                z.writestr(f"{p.name}_{i:03d}.png", b.getvalue())
        return {"ok": True,
                "file": f"/api/edit/projects/{name}/download/pages.zip"}
    raise HTTPException(400, "fmt 只能是 pdf / long / zip")


@router.get("/projects/{name}/download/{rest:path}")
def download(name: str, rest: str):
    d = store.out_dir(name)
    fp = os.path.abspath(os.path.join(d, rest))
    if not fp.startswith(os.path.abspath(d)) or not os.path.isfile(fp):
        raise HTTPException(404, "文件不存在")
    return FileResponse(fp, filename=os.path.basename(fp))
