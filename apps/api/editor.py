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
    ⑤ **渲染结果按「内容指纹」缓存**：key 里带页面内容哈希，
       所以改了元素不会拿到旧图（详见下面「渲染缓存」一节）
"""

from __future__ import annotations

import base64
import hashlib
import io
from collections import OrderedDict
from contextlib import contextmanager
import json
import os
import threading
import time
import zipfile
from typing import Any, Dict, List, NamedTuple, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
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
    """修改前存一份撤销快照

    ★ 快照**落盘**（`.history/`），不再放内存 dict ——
      原来重启服务撤销历史就全没了。
    """
    store.push_history(name)


@contextmanager
def mutate(name: str, revision: Optional[int] = None
           ) -> Iterator[EditProject]:
    """「读 → 改 → 写」全程持锁的上下文

    ★ 为什么需要锁：
      原来是「读整个 JSON → 改 → 整个写回」，没有任何保护。
      实测 6 个并发 PATCH 有 5 个直接报文件占用错误；
      而「先读后写」还会丢改动（后写覆盖先写）。

    ★ 乐观并发控制：
      客户端可以带上它看到的 `revision`。如果服务端的对不上，
      说明别人先改了 —— 直接 409，让前端提示刷新，
      而不是让后写的静默覆盖先写的。

    用法：
        with mutate("我的项目", expect_revision) as p:
            pg = _page(p, page_id)
            pg.elements.append(el)
        # 退出时自动 save
    """
    with store.locked(name):
        p = store.load(name)
        if p is None:
            raise HTTPException(404, f"项目 {name} 不存在")
        if revision is not None and revision != p.revision:
            raise HTTPException(
                409,
                f"项目已被其他人修改（服务端 revision={p.revision}，"
                f"你提交的是 {revision}）。请刷新后重试。")
        snapshot(name)
        yield p
        store.save(p)
    # 写完了：把该项目相关的渲染缓存丢掉。
    # ★ 指纹（revision + 页面内容哈希）已经保证「不会返回旧图」，
    #   这一步纯粹是**及时释放内存** —— 页面内容已经回不去了，
    #   旧图留在缓存里只会白占内存，还要等 LRU 慢慢淘汰。
    #   放在 with 外面：抛异常（409/400/404）时压根没写盘，不用清。
    _render_cache.invalidate_project(name)


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
def patch_project(name: str, req: PatchReq, revision: Optional[int] = None):
    with mutate(name, revision) as p:
        for k, v in req.model_dump(exclude_none=True).items():
            setattr(p, k, v)
    return {"ok": True, "project": p.model_dump(mode="json")}


@router.delete("/projects/{name}")
def delete_project(name: str):
    # store.delete 会一并删掉 .history/ 与内存里的锁
    if not store.delete(name):
        raise HTTPException(404, "项目不存在")
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# 素材（上传图片）
# ══════════════════════════════════════════════════════════════════

@router.post("/projects/{name}/assets")
async def upload_assets(name: str, files: List[UploadFile] = File(...),
                        revision: Optional[int] = None):
    """批量上传图片（支持多选 / 拖入）

    ★ 全程持锁：上传与「改元素」是两类并发写，
      不锁的话上传完成时的 save 会把期间的编辑覆盖掉。
    ★ 大小限制在 store.add_asset 里（应用层也要挡，
      不能只靠 nginx 的 client_max_body_size）。
    """
    added, failed = [], []
    with mutate(name, revision) as p:
        for f in files:
            try:
                data = await f.read()
                if not data:
                    failed.append({"filename": f.filename, "reason": "空文件"})
                    continue
                a = store.add_asset(name, f.filename or "unnamed", data)
                p.assets.append(a)
                added.append(a)
            except Exception as e:                          # noqa: BLE001
                failed.append({"filename": f.filename,
                               "reason": str(e)[:160]})
    return {"ok": True, "added": [a.model_dump(mode="json") for a in added],
            "failed": failed, "total": len(p.assets),
            "revision": p.revision}


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
def delete_asset(name: str, asset_id: str,
                 revision: Optional[int] = None):
    with mutate(name, revision) as p:
        a = p.asset(asset_id)
        if a is None:
            raise HTTPException(404, "素材不存在")
        used = [e.id for pg in p.pages for e in pg.elements
                if isinstance(e, ImageElement) and e.asset_id == asset_id]
        if used:
            raise HTTPException(
                409, f"该素材正在被 {len(used)} 个元素使用，先删元素")
        fp = store.asset_path(name, a.stored_name)
        if os.path.isfile(fp):
            os.remove(fp)
        p.assets = [x for x in p.assets if x.id != asset_id]
    return {"ok": True, "revision": p.revision}


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
def add_page(name: str, req: PageReq, revision: Optional[int] = None):
    with mutate(name, revision) as p:
        n = p.next_number()
        pg = Page(number=n, order=len(p.pages),
                  title=req.title or f"第 {n} 页",
                  width=req.width or p.canvas_width,
                  height=req.height or p.canvas_height,
                  background=req.background or "#ffffff", note=req.note or "")
        p.pages.append(pg)
    return {"ok": True, "page": pg.model_dump(mode="json"),
            "revision": p.revision}


@router.patch("/projects/{name}/pages/{page_id}")
def patch_page(name: str, page_id: str, req: PageReq,
               revision: Optional[int] = None):
    with mutate(name, revision) as p:
        pg = _page(p, page_id)
        for k, v in req.model_dump(exclude_none=True).items():
            setattr(pg, k, v)
    return {"ok": True, "page": pg.model_dump(mode="json"),
            "revision": p.revision}


@router.delete("/projects/{name}/pages/{page_id}")
def delete_page(name: str, page_id: str, revision: Optional[int] = None):
    with mutate(name, revision) as p:
        _page(p, page_id)
        p.pages = [x for x in p.pages if x.id != page_id]
        for i, x in enumerate(p.ordered_pages()):
            x.order = i
    return {"ok": True, "pages": len(p.pages), "revision": p.revision}


@router.post("/projects/{name}/pages/{page_id}/duplicate")
def duplicate_page(name: str, page_id: str, revision: Optional[int] = None):
    with mutate(name, revision) as p:
        src = _page(p, page_id)
        n = p.next_number()
        new = src.model_copy(deep=True)
        new.id = new_id()                                # 新 id
        new.number = n
        new.order = len(p.pages)
        new.title = f"{src.title} 副本"
        for e in new.elements:                           # 元素也要新 id
            e.id = new_id()
        p.pages.append(new)
    return {"ok": True, "page": new.model_dump(mode="json"),
            "revision": p.revision}


class ReorderReq(BaseModel):
    """按给定的 id 顺序重排页面（★ 只改 order，不改 number）"""
    page_ids: List[str]


@router.post("/projects/{name}/pages/reorder")
def reorder_pages(name: str, req: ReorderReq,
                  revision: Optional[int] = None):
    with mutate(name, revision) as p:
        ids = {x.id for x in p.pages}
        if set(req.page_ids) != ids:
            raise HTTPException(400,
                                "页面 id 列表与项目不一致（数量或内容对不上）")
        pos = {pid: i for i, pid in enumerate(req.page_ids)}
        for pg in p.pages:
            pg.order = pos[pg.id]
    return {"ok": True, "order": [x.id for x in p.ordered_pages()],
            "revision": p.revision}


@router.post("/projects/{name}/pages/{page_id}/move")
def move_page(name: str, page_id: str, delta: int = Query(...),
              revision: Optional[int] = None):
    """上移/下移一页（delta = -1 / +1）"""
    with mutate(name, revision) as p:
        ordered = p.ordered_pages()
        idx = next((i for i, x in enumerate(ordered) if x.id == page_id), None)
        if idx is None:
            raise HTTPException(404, "页面不存在")
        j = max(0, min(len(ordered) - 1, idx + delta))
        if j == idx:
            return {"ok": True, "moved": 0, "revision": p.revision}
        ordered.insert(j, ordered.pop(idx))
        for i, x in enumerate(ordered):
            x.order = i
    return {"ok": True, "moved": j - idx, "revision": p.revision}


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
def add_element(name: str, page_id: str, req: AddElementReq,
                revision: Optional[int] = None):
    # 自动找空位要读素材图，放在锁里做（保证与最终落盘的一致）
    with mutate(name, revision) as p:
        pg = _page(p, page_id)
        pg_id = pg.id

        z = (max((e.z for e in pg.elements), default=-1)) + 1
        # ★ 必须显式带上 kind：虽然子类有默认值，但从 dict 构造时
        #   kind 是 Literal 判别字段，缺了会报「未知的元素类型：None」
        base: Dict[str, Any] = {"kind": req.kind, "z": z}

        if req.kind == "image":
            a = p.asset(req.asset_id or "")
            if a is None:
                raise HTTPException(400, "需要有效的 asset_id")
            w = req.w if req.w else 0.6
            base.update(
                asset_id=a.id, w=w,
                h=(req.h if req.h else round(
                    w * pg.width / pg.height * a.height / max(1, a.width), 5)),
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
        except Exception as e:                              # noqa: BLE001
            raise HTTPException(400, f"元素参数不合法：{e}") from e

        pg.elements.append(el)
        pg.normalize_z()
    return {"ok": True, "element": el.model_dump(mode="json"),
            "page_id": pg_id, "revision": p.revision}


class PatchElementReq(BaseModel):
    """只改传进来的字段（其余保持）"""
    props: Dict[str, Any] = Field(default_factory=dict)


@router.patch("/projects/{name}/elements/{element_id}")
def patch_element(name: str, element_id: str, req: PatchElementReq,
                  revision: Optional[int] = None):
    with mutate(name, revision) as p:
        pg = _find_page_of(p, element_id)
        el = next(e for e in pg.elements if e.id == element_id)
        data = el.model_dump(mode="json")
        for k, v in req.props.items():
            if k in ("id", "kind"):
                continue
            data[k] = v
        try:
            new = parse_element(data)
        except Exception as e:                              # noqa: BLE001
            raise HTTPException(400, f"参数不合法：{e}") from e
        pg.elements = [new if e.id == element_id else e for e in pg.elements]
    return {"ok": True, "element": new.model_dump(mode="json"),
            "revision": p.revision}


@router.delete("/projects/{name}/elements/{element_id}")
def delete_element(name: str, element_id: str,
                   revision: Optional[int] = None):
    with mutate(name, revision) as p:
        pg = _find_page_of(p, element_id)
        pg.elements = [e for e in pg.elements if e.id != element_id]
        pg.normalize_z()
    return {"ok": True, "revision": p.revision}


@router.post("/projects/{name}/elements/{element_id}/duplicate")
def duplicate_element(name: str, element_id: str,
                      revision: Optional[int] = None):
    with mutate(name, revision) as p:
        pg = _find_page_of(p, element_id)
        src = next(e for e in pg.elements if e.id == element_id)
        new = src.model_copy(deep=True)
        new.id = new_id()
        new.x = round(min(0.95, new.x + 0.03), 5)
        new.y = round(min(0.95, new.y + 0.03), 5)
        new.z = max((e.z for e in pg.elements), default=-1) + 1
        pg.elements.append(new)
        pg.normalize_z()
    return {"ok": True, "element": new.model_dump(mode="json"),
            "revision": p.revision}


class OrderReq(BaseModel):
    """元素顺序：从下到上（先画的在前）"""
    element_ids: List[str]


@router.post("/projects/{name}/elements/order")
def reorder_elements(name: str, req: OrderReq,
                     revision: Optional[int] = None):
    with mutate(name, revision) as p:
        # 这些 id 必须都属于同一页
        pages = [pg for pg in p.pages
                 if any(e.id in set(req.element_ids) for e in pg.elements)]
        if len(pages) != 1:
            raise HTTPException(400, "元素顺序只能在同一页内调整")
        pg = pages[0]
        if set(req.element_ids) != {e.id for e in pg.elements}:
            raise HTTPException(400, "元素 id 列表与该页不一致")
        pos = {eid: i for i, eid in enumerate(req.element_ids)}
        for e in pg.elements:
            e.z = pos[e.id]
        pg.normalize_z()
    return {"ok": True, "order": [e.id for e in pg.sorted_elements()],
            "revision": p.revision}


class BatchMoveReq(BaseModel):
    """批量改位置（拖动多个 / 对齐）"""
    moves: Dict[str, Dict[str, float]]      # element_id -> {x, y}


@router.post("/projects/{name}/elements/batch-move")
def batch_move(name: str, req: BatchMoveReq,
               revision: Optional[int] = None):
    with mutate(name, revision) as p:
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
    return {"ok": True, "moved": n, "revision": p.revision}


# ══════════════════════════════════════════════════════════════════
# 撤销 / 重做
# ══════════════════════════════════════════════════════════════════

@router.post("/projects/{name}/undo")
def undo(name: str):
    """撤销一步

    ★ 快照来自磁盘上的 `.history/`，所以**重启服务也不会丢**。
    """
    with store.locked(name):
        raw = store.pop_history(name)
        if raw is None:
            return {"ok": False, "reason": "没有可撤销的操作"}
        store.use_history(name, raw)                 # 写回
        p = store.load(name)
    return {"ok": True, "project": p.model_dump(mode="json"),
            "remaining": store.history_depth(name)}


@router.get("/projects/{name}/undo-depth")
def undo_depth(name: str):
    return {"depth": store.history_depth(name)}


# ══════════════════════════════════════════════════════════════════
# 渲染缓存（LRU + 内容指纹）
# ══════════════════════════════════════════════════════════════════

def _env_cache_limit() -> int:
    """缓存张数上限：默认 24

    ★ 为什么要跟上限：一张 1240 宽的页面图在内存里约 6.6 MB
      （1240×1771×3 字节），不设上限的话页数一多就吃光内存。
      24 张 ≈ 160 MB，按需用 COMIC_EDITOR_CACHE 调。
    """
    try:
        n = int(os.environ.get("COMIC_EDITOR_CACHE", "24"))
    except ValueError:
        n = 24
    return n if n > 0 else 24


def _cache_name(name: str) -> str:
    """缓存 key 里的项目名统一成磁盘目录名（store.safe_name）

    ★ 两边必须用同一套归一化：请求 URL 里的名字和 project.json 里的
      `name` 字段可能是同一个名字的不同写法（比如带了个会被过滤掉的字符）。
      归一化不一致的话 invalidate 会清不到东西 —— 不致命（指纹还在兜底），
      但会白占内存，而且看起来「清了却没清」。
    """
    try:
        return store.safe_name(name)
    except ValueError:              # 全是非法字符，safe_name 会抛
        return name or ""


class _CachedRender(NamedTuple):
    """一页的渲染结果：图 + 包围盒

    ★ 图和盒子**必须成对存**，不能各存各的：
      前端的选择框来自盒子、画面来自图，两者只要不是同一次渲染产出的，
      就会出现「框和画对不上」—— 这正是 render.py 里
      `image_box()` / `_draw_image_el()` 共用 `_render_image_piece()` 的原因，
      缓存这一层不能又把它拆开。

    ★ 图是**共享只读**的：render.png 只是编码它、elements-geometry 只读盒子、
      导出（save_pdf / 长图）也只是读它（convert/resize 都返回新对象）。
      以后谁要是往这张图上原地画，就会污染缓存里那一份 —— 别这么干。
    """
    image: Any
    boxes: Dict[str, List[float]]


class _RenderCache:
    """按内容指纹缓存整页渲染结果，容量满了淘汰最久没用的那张

    ★ 为什么 key 里必须有内容指纹，而不是只用 (项目, 页面 id, 宽度)：
      用户改了元素再请求，服务端会把**改之前**那张图原样返回 ——
      HTTP 200、图也是合法 JPEG、响应头也齐全，只是内容是旧的。
      本仓库已经栽过两次这种「静默降级」（缺中文字体 → 渲染成方块但 200、
      缺 python-multipart → 服务起不来但 systemd 显示 active），
      共同点是「一切看起来都正常，只有内容是错的」，最难查。
      所以这里不指望调用方记得清缓存，而是把「内容变没变」直接算进 key。

    ★ 指纹 = revision + 页面序列化 + 本页引用到的素材描述：
      · 页面序列化（含 elements / 画布尺寸 / 背景）→ 任何一处变了都失效
      · revision → 任何一次写操作都会 +1，作用有两个：
        ① **多进程部署的兜底**：进程 A 改了项目，进程 B 的内存缓存
           谁也通知不到，只能靠重新读到的 revision 发现内容变了
           （每个请求都会 store.load 一遍，所以这个兜底是免费的）；
        ② 别的页改了也让这一页失效 —— 宁可少命中也不给旧图。
        撤销会把 revision 写回旧值，但那时页面内容也一起回到了旧状态，
        命中恰好是对的。
      · 素材描述（id/文件名/尺寸）→ 素材记录变了要重画。
        ★ 边界说清楚：素材经 API 上传后不再改写、文件名唯一，所以这里
          只对 project.json 里的素材记录负责。如果有人绕过 API 直接替换
          assets/ 里的同名文件（连尺寸都一样），指纹看不出来 ——
          这种情况本项目不做保证，也不假装能兜住。

    ★ 并发：两个线程同时未命中同一页就各渲染一次（多花一次算力，结果一样），
      不做「单飞」合并 —— 那是另一种复杂度，这里不值当。
      命中/未命中计数只给测试和排查看，不做精确统计。
    """

    def __init__(self, limit: Optional[int] = None):
        self._limit = max(1, int(limit if limit is not None
                                else _env_cache_limit()))
        self._data: "OrderedDict[tuple, _CachedRender]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @property
    def limit(self) -> int:
        return self._limit

    def key(self, project: EditProject, page: Page, width: int) -> tuple:
        used = {e.asset_id for e in page.elements
                if isinstance(e, ImageElement)}
        assets = sorted(
            [a.id, a.stored_name, a.width, a.height]
            for a in project.assets if a.id in used)
        raw = json.dumps(
            {"rev": project.revision,
             "page": page.model_dump(mode="json"),
             "assets": assets},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        fp = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        # ★ 不能只用 hash()：Python 的字符串哈希每次进程启动都不一样，
        #   多 worker 部署下同一页会各算各的 key，缓存等于白做。
        # ★ 宽度和 render_page 用同一套钳制（它也取 max(64, int(w))）：
        #   否则 w=10 和 w=64 会各存一份**完全相同**的图。
        # ★ 带上 store 根目录：不同工作区里的同名项目不能互相命中
        #   （测试里每个用例一个临时工作区，名称又都叫 p1）。
        return (store.root, _cache_name(project.name), page.id,
                max(64, int(width)), fp)

    def get(self, key: tuple) -> Optional[_CachedRender]:
        with self._lock:
            ent = self._data.get(key)
            if ent is None:
                self.misses += 1
                return None
            self._data.move_to_end(key)          # LRU：用完挪到队尾（最新）
            self.hits += 1
            return ent

    def put(self, key: tuple, ent: _CachedRender) -> None:
        with self._lock:
            self._data[key] = ent
            self._data.move_to_end(key)
            while len(self._data) > self._limit:
                self._data.popitem(last=False)   # 淘汰队头（最久没用）

    def invalidate_project(self, name: str) -> None:
        """丢掉某个项目的全部缓存（写操作之后调用）

        指纹已经保证不会返回旧图，这条是省内存用的：让旧图立刻可以被回收。
        """
        prefix = (store.root, _cache_name(name))
        with self._lock:
            for k in [k for k in self._data if k[:2] == prefix]:
                self._data.pop(k, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> Dict[str, Any]:
        """给测试和排查用：一眼看出缓存到底有没有在工作"""
        with self._lock:
            return {"size": len(self._data), "limit": self._limit,
                    "hits": self.hits, "misses": self.misses}


_render_cache = _RenderCache()


def render_cached(project: EditProject, page: Page, width: int
                  ) -> tuple[_CachedRender, str]:
    """渲染一页（带缓存）→ (结果, "hit"|"miss")

    ★ render.png / elements-geometry / 导出 三处**都走这里**：
      共用同一份结果，才能保证「画面」和「选择框」永远出自同一次渲染。

    ★ 一律按 with_boxes=True 渲染：实测同一页多算盒子只要 +2 ms
      （705 → 707 ms，占总量 0.3%），但换来两个接口共用一份结果 ——
      这点开销换「不可能错位」很值。
    """
    key = _render_cache.key(project, page, width)
    ent = _render_cache.get(key)
    if ent is not None:
        return ent, "hit"
    img, boxes = render_page(page, _asset_loader(project),
                             target_width=width, with_boxes=True)
    ent = _CachedRender(image=img, boxes=boxes)
    _render_cache.put(key, ent)
    return ent, "miss"


# ══════════════════════════════════════════════════════════════════
# 渲染（预览 / 导出共用）
# ══════════════════════════════════════════════════════════════════

@router.get("/projects/{name}/pages/{page_id}/render.png")
def render_png(name: str, page_id: str, w: int = 1240,
               boxes: int = 0, t: float = 0):
    """页面预览图

    `boxes=1` 时把元素包围盒放进响应头 `X-Element-Boxes`（base64 JSON）——
    前端据此画选择框，保证与服务端排版一致。

    `X-Cache: hit|miss` 说明这张图是缓存来的还是现画的 ——
    没有这个头，缓存出问题（比如永远不命中、或者命中过期内容）
    从外面完全看不出来，只能靠猜。
    """
    p = _load(name)
    pg = _page(p, page_id)
    ent, state = render_cached(p, pg, w)
    buf = io.BytesIO()
    ent.image.save(buf, "JPEG", quality=90, optimize=True)
    headers = {"Cache-Control": "no-store", "X-Cache": state}
    if boxes:
        payload = base64.b64encode(
            json.dumps(ent.boxes, separators=(",", ":")).encode()).decode()
        headers["X-Element-Boxes"] = payload
    return Response(buf.getvalue(), media_type="image/jpeg", headers=headers)


@router.get("/projects/{name}/elements-geometry")
def elements_geometry(name: str, page_id: str, w: int = 1240):
    """只返回包围盒（拖拽时快速取新位置）

    ★ 名字叫「不算图」，实际还是要整页渲染一遍才知道气泡多高、
      图片缩放到哪（几何就是这么算出来的）。所以它和 render.png
      共用同一份缓存：预览过的页面，拖拽时基本不用再等。
    """
    p = _load(name)
    pg = _page(p, page_id)
    ent, state = render_cached(p, pg, w)
    # jsonable_encoder：保持原来「返回 dict 交给 FastAPI 序列化」时的
    # 类型兜底（万一盒子里混进 numpy 标量也不会 500）
    return JSONResponse(jsonable_encoder({"boxes": ent.boxes}),
                        headers={"X-Cache": state})


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
    # ★ 走同一个缓存：刚才在界面上翻过的页面，导出时不用再重画一遍
    #   （导出是「顺序渲染每一页」，页数多时这一项最费时间）
    pages = [render_cached(p, pg, width)[0].image
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
