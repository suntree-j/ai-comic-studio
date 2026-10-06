# -*- coding: utf-8 -*-
"""工作台后端 API（FastAPI）

一个进程同时提供：
    /api/*         REST 接口
    /             静态工作台前端（apps/workbench）

设计要点：
    ① **IR 是唯一数据源** —— 所有编辑都写回 JSON，渲染从 IR 重新计算
    ② **渲染在服务端** —— 前端只发坐标，不重复实现排版逻辑，
       保证「所见即所得」与 CLI 产出一致
    ③ **单页重渲染** —— 改一个气泡只重算那一页（数百毫秒级）
    ④ **页码只读** —— 冻结机制体现在 API 上：不提供改页码的接口
"""

from __future__ import annotations

import io
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from packages.ir import (
    Bible,
    Box,
    BubbleSpec,
    BubbleStyle,
    ComicProject,
    DialogueBook,
    Layout,
    Storyboard,
    Utterance,
    validate,
)
from packages.ir.validator import RULES, Severity
from packages.render import (
    MockImageProvider,
    RenderStudio,
    get_image_provider,
    IMAGE_PROVIDER_NAMES,
    build_prompt,
    render_page,
    RenderContext,
)

HERE = Path(__file__).resolve().parent
WORKBENCH_DIR = HERE.parent / "workbench"
PROJECTS_DIR = Path(os.environ.get("COMIC_PROJECTS", HERE.parent.parent / "projects"))
#: 挂载在反向代理的子路径下时（如 /comic/），静态资源要用相对路径引用
BASE_PATH = os.environ.get("COMIC_BASE_PATH", "/")

app = FastAPI(title="AI Comic Studio API", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════
# 项目存取
# ══════════════════════════════════════════════════════════════════

def project_path(name: str) -> Path:
    """防止路径穿越"""
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    if not safe or safe != name:
        raise HTTPException(400, "非法的项目名")
    return PROJECTS_DIR / safe


def load_project(name: str) -> ComicProject:
    d = project_path(name)
    if not (d / "bible.json").exists():
        raise HTTPException(404, f"项目 {name} 不存在")
    bible = Bible.model_validate_json((d / "bible.json").read_text(encoding="utf-8"))
    boards_raw = json.loads((d / "storyboard.json").read_text(encoding="utf-8"))
    boards = [Storyboard.model_validate(x) for x in boards_raw]
    dialogue = DialogueBook.model_validate_json(
        (d / "dialogue.json").read_text(encoding="utf-8"))
    layout = Layout.model_validate_json((d / "layout.json").read_text(encoding="utf-8"))
    return ComicProject(name=name, bible=bible, storyboards=boards,
                        dialogue=dialogue, layout=layout)


def save_project(project: ComicProject) -> None:
    d = project_path(project.name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "bible.json").write_text(project.bible.model_dump_json(indent=1), encoding="utf-8")
    (d / "storyboard.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in project.storyboards],
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "dialogue.json").write_text(
        project.dialogue.model_dump_json(indent=1), encoding="utf-8")
    (d / "layout.json").write_text(
        project.layout.model_dump_json(indent=1), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
# 页面渲染缓存
# ══════════════════════════════════════════════════════════════════

_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()
_provider_name = os.environ.get("COMIC_IMAGE_PROVIDER", "mock")
_provider_kwargs: Dict[str, Any] = {}


def get_provider():
    try:
        return get_image_provider(_provider_name, **_provider_kwargs)
    except Exception:                                       # noqa: BLE001
        return MockImageProvider()


def assets_dir(name: str) -> Path:
    d = project_path(name) / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_one_page(name: str, page_no: int, project: Optional[ComicProject] = None):
    """渲染指定页，返回 PIL Image（带内存缓存，改动后失效）"""
    key = f"{name}:{page_no}"
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit["image"]

    project = project or load_project(name)
    studio = RenderStudio(get_provider(), panel_width=900)
    ctx = RenderContext(
        bible=project.bible,
        panels={p.id: p for sb in project.storyboards for p in sb.panels},
        utterances=project.dialogue.items,
        panel_width=900,
        base_font=18,
    )
    lp = next((p for p in project.layout.pages if p.page == page_no), None)
    if lp is None:
        raise HTTPException(404, f"第 {page_no} 页不存在")

    images = {}
    for pid in lp.panels:
        fp = studio.panel_path(str(assets_dir(name)), pid)
        if os.path.exists(fp):
            from PIL import Image
            images[pid] = Image.open(fp).convert("RGB")

    img = render_page(ctx, lp, images)
    with _cache_lock:
        _cache[key] = {"image": img, "t": time.time()}
    return img


def invalidate(name: str, page_no: Optional[int] = None) -> None:
    with _cache_lock:
        if page_no is None:
            for k in [k for k in _cache if k.startswith(name + ":")]:
                _cache.pop(k, None)
        else:
            _cache.pop(f"{name}:{page_no}", None)


# ══════════════════════════════════════════════════════════════════
# 请求模型
# ══════════════════════════════════════════════════════════════════

class BubblePatch(BaseModel):
    """气泡编辑（★ 只允许改这些字段；页码/镜头 id 不可改）"""
    who: Optional[str] = None
    text: Optional[str] = None
    box: Optional[Dict[str, float]] = None      # {x, y}
    tail: Optional[Dict[str, float]] = None     # {x, y}
    style: Optional[str] = None
    font_size: Optional[int] = None
    locked: Optional[bool] = None
    confirmed: Optional[bool] = None


class PanelPatch(BaseModel):
    distance: Optional[str] = None
    action: Optional[str] = None
    mood: Optional[str] = None
    damage_level: Optional[str] = None
    emotion_hint: Optional[Dict[str, str]] = None


# ══════════════════════════════════════════════════════════════════
# API · 项目
# ══════════════════════════════════════════════════════════════════

@app.get("/api/health")
def health():
    return {"ok": True, "provider": _provider_name,
            "providers": IMAGE_PROVIDER_NAMES,
            "base_path": BASE_PATH}


@app.get("/api/config")
def get_config():
    """给前端读的运行时配置（子路径部署时前端据此拼接口地址）"""
    return {"base_path": BASE_PATH, "version": app.version}


@app.get("/api/projects")
def list_projects():
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not (d / "bible.json").exists():
            continue
        try:
            b = Bible.model_validate_json((d / "bible.json").read_text(encoding="utf-8"))
            lay = Layout.model_validate_json((d / "layout.json").read_text(encoding="utf-8"))
            out.append({"name": d.name, "characters": len(b.characters),
                        "pages": lay.total})
        except Exception:                                   # noqa: BLE001
            out.append({"name": d.name, "characters": 0, "pages": 0})
    return out


@app.get("/api/projects/{name}")
def get_project(name: str):
    p = load_project(name)
    boards = [s.model_dump(mode="json") for s in p.storyboards]
    n_panels = sum(len(s["panels"]) for s in boards)
    n_utt = sum(len(v) for v in p.dialogue.items.values())
    placed = {pid for pg in p.layout.pages for pid in pg.panels}
    return {
        "name": p.name,
        "bible": json.loads(p.bible.model_dump_json()),
        "storyboards": boards,
        "dialogue": json.loads(p.dialogue.model_dump_json()),
        "layout": json.loads(p.layout.model_dump_json()),
        "stats": {
            "chapters": len(boards),
            "panels": n_panels,
            "utterances": n_utt,
            "pages": p.layout.total,
            "unplaced": n_panels - len(placed),
        },
    }


@app.post("/api/projects/{name}/validate")
def validate_project(name: str):
    p = load_project(name)
    src_file = project_path(name) / "source.txt"
    src = src_file.read_text(encoding="utf-8").splitlines() if src_file.exists() else None
    rep = validate(p, src)
    return {
        "ok": rep.ok,
        "summary": rep.summary(),
        "errors": [{"rule": e.rule, "severity": e.severity.value,
                    "where": e.where, "message": e.message, "hint": e.hint}
                   for e in rep.errors],
        "rules": RULES,
    }


@app.get("/api/rules")
def get_rules():
    return RULES


# ══════════════════════════════════════════════════════════════════
# API · 页面与预览
# ══════════════════════════════════════════════════════════════════

@app.get("/api/projects/{name}/pages/{page_no}.png")
def page_image(name: str, page_no: int, w: int = 900):
    img = render_one_page(name, page_no)
    if img.width != w:
        img = img.resize((w, int(img.height * w / img.width)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=86, optimize=True)
    return Response(buf.getvalue(), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/projects/{name}/panels/{panel_id}.png")
def panel_image(name: str, panel_id: str, w: int = 700):
    """单格预览（未合成气泡的底图 or 已合成）"""
    from PIL import Image
    fp = assets_dir(name) / "panels" / f"{panel_id}.png"
    if not fp.exists():
        raise HTTPException(404, "该格还没有出图")
    img = Image.open(fp).convert("RGB")
    if img.width != w:
        img = img.resize((w, int(img.height * w / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86, optimize=True)
    return Response(buf.getvalue(), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/projects/{name}/prompt/{panel_id}")
def panel_prompt(name: str, panel_id: str):
    """查看某格最终送给生图模型的提示词（透明化，便于调试）"""
    p = load_project(name)
    panel = next((x for sb in p.storyboards for x in sb.panels
                  if x.id == panel_id), None)
    if panel is None:
        raise HTTPException(404, "镜头不存在")
    return {"panel_id": panel_id, "prompt": build_prompt(p.bible, panel),
            "panel": panel.model_dump(mode="json")}


# ══════════════════════════════════════════════════════════════════
# API · 编辑（★ 所有编辑只改 IR，然后重渲染该页）
# ══════════════════════════════════════════════════════════════════

def _find_page_of(project: ComicProject, panel_id: str) -> Optional[int]:
    for pg in project.layout.pages:
        if panel_id in pg.panels:
            return pg.page
    return None


@app.patch("/api/projects/{name}/dialogue/{panel_id}/{utt_id}")
def patch_utterance(name: str, panel_id: str, utt_id: str, patch: BubblePatch):
    """改一条对白（说话人/文字/气泡位置/箭头/样式）"""
    project = load_project(name)
    items = project.dialogue.items
    if panel_id not in items:
        raise HTTPException(404, "该镜头没有对白")
    target = next((u for u in items[panel_id] if u.id == utt_id), None)
    if target is None:
        raise HTTPException(404, "对白不存在")

    data = target.model_dump(mode="json")
    if patch.who is not None:
        if patch.who not in project.bible.characters:
            raise HTTPException(400, f"角色 {patch.who} 不在设定集中")
        data["who"] = patch.who
        # 换了说话人 → 必须重新人工确认
        data["confirmed"] = False
    if patch.text is not None:
        data["text"] = patch.text
    if patch.style is not None:
        data.setdefault("bubble", {})["style"] = patch.style
    if patch.font_size is not None:
        data.setdefault("bubble", {})["font_size"] = patch.font_size
    if patch.box is not None:
        data.setdefault("bubble", {})["box"] = patch.box
    if patch.tail is not None:
        data.setdefault("bubble", {})["tail"] = patch.tail
    if patch.locked is not None:
        data["locked"] = patch.locked
    if patch.confirmed is not None:
        data["confirmed"] = patch.confirmed

    new = Utterance.model_validate(data)
    items[panel_id] = [new if u.id == utt_id else u for u in items[panel_id]]
    save_project(project)
    invalidate(name)
    return {"ok": True, "utterance": new.model_dump(mode="json")}


@app.patch("/api/projects/{name}/panels/{panel_id}")
def patch_panel(name: str, panel_id: str, patch: PanelPatch):
    """改一格的分镜属性（距离/动作/氛围/损伤/表情）"""
    project = load_project(name)
    for sb in project.storyboards:
        for p in sb.panels:
            if p.id == panel_id:
                if patch.distance is not None:
                    p.distance = patch.distance
                if patch.action is not None:
                    p.action = patch.action
                if patch.mood is not None:
                    p.mood = patch.mood
                if patch.damage_level is not None:
                    p.damage_level = patch.damage_level        # type: ignore[assignment]
                if patch.emotion_hint is not None:
                    p.emotion_hint = patch.emotion_hint
                save_project(project)
                invalidate(name)
                return {"ok": True, "panel": p.model_dump(mode="json")}
    raise HTTPException(404, "镜头不存在")


# ══════════════════════════════════════════════════════════════════
# API · 渲染任务
# ══════════════════════════════════════════════════════════════════

@dataclass
class Task:
    id: str
    name: str
    kind: str
    status: str = "running"          # running / done / failed / quota
    total: int = 0
    done: int = 0
    message: str = ""
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None


_tasks: Dict[str, Task] = {}
_tasks_lock = threading.Lock()
_seq = [0]


def _new_task(name: str, kind: str, total: int = 0) -> Task:
    with _tasks_lock:
        _seq[0] += 1
        t = Task(id=f"t{_seq[0]}", name=name, kind=kind, total=total)
        _tasks[t.id] = t
    return t


@app.get("/api/tasks")
def list_tasks():
    with _tasks_lock:
        return [asdict(t) for t in sorted(_tasks.values(),
                                          key=lambda x: -x.started)[:30]]


@app.get("/api/tasks/{tid}")
def get_task(tid: str):
    with _tasks_lock:
        t = _tasks.get(tid)
    if t is None:
        raise HTTPException(404, "任务不存在")
    return asdict(t)


@app.post("/api/projects/{name}/render")
def render_project(name: str, force: bool = False):
    """后台渲染全部单格（串行，避免限流）"""
    project = load_project(name)
    panels = [p for sb in project.storyboards for p in sb.panels]
    t = _new_task(name, "render", total=len(panels))

    def work():
        studio = RenderStudio(get_provider())
        try:
            rep = studio.generate_panels(
                project.bible, panels, str(assets_dir(name)), force=force,
                on_progress=lambda i, tot, m: setattr(t, "done", i) or
                setattr(t, "message", m),
            )
            t.status = "done"
            t.message = rep.summary()
            if rep.errors:
                t.status = "quota" if any("额度" in e for e in rep.errors) else "failed"
                t.message += "｜" + rep.errors[0]
        except Exception as e:                              # noqa: BLE001
            t.status, t.message = "failed", str(e)
        finally:
            t.finished = time.time()
            invalidate(name)

    threading.Thread(target=work, daemon=True).start()
    return {"task": asdict(t)}


@app.post("/api/projects/{name}/export")
def export_project(name: str, fmt: str = "pdf", long_count: int = 0):
    """导出 PDF / 长图"""
    from packages.render import save_long_images, save_pdf
    project = load_project(name)
    out = project_path(name) / "out"
    out.mkdir(parents=True, exist_ok=True)

    pages = []
    studio = RenderStudio(get_provider(), panel_width=1240)
    for lp in project.layout.pages:
        pages.append(render_one_page(name, lp.page, project))

    if fmt == "pdf":
        p = out / "comic.pdf"
        save_pdf(pages, str(p))
        return {"ok": True, "file": f"/api/projects/{name}/download/comic.pdf"}
    if fmt == "long":
        paths = save_long_images(pages, str(out / "longs"),
                                 count=long_count or 9, width=1000)
        return {"ok": True, "files": [f"/api/projects/{name}/download/longs/{Path(x).name}"
                                      for x in paths]}
    raise HTTPException(400, "fmt 只能是 pdf 或 long")


@app.get("/api/projects/{name}/download/{rest:path}")
def download(name: str, rest: str):
    d = project_path(name) / "out"
    fp = (d / rest).resolve()
    if not str(fp).startswith(str(d.resolve())) or not fp.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(fp), filename=fp.name)


# ══════════════════════════════════════════════════════════════════
# 静态前端
# ══════════════════════════════════════════════════════════════════

if WORKBENCH_DIR.exists():
    @app.get("/")
    def index():
        # 把 BASE_PATH 注入 <base>，让前端在任何子路径下都能正确加载资源
        html = (WORKBENCH_DIR / "index.html").read_text(encoding="utf-8")
        bp = BASE_PATH if BASE_PATH.endswith("/") else BASE_PATH + "/"
        if "<head>" in html:
            html = html.replace("<head>", f'<head>\n<base href="{bp}">', 1)
        return Response(html, media_type="text/html; charset=utf-8")

    app.mount("/static", StaticFiles(directory=str(WORKBENCH_DIR)), name="static")
else:                                                       # pragma: no cover
    @app.get("/")
    def index_missing():
        return JSONResponse({"error": "workbench 未找到"}, status_code=404)


def main(argv=None) -> int:                                 # pragma: no cover
    global _provider_name, BASE_PATH

    import argparse
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--provider", default=_provider_name)
    ap.add_argument("--base-path", default=BASE_PATH)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args(argv)

    _provider_name = a.provider
    BASE_PATH = a.base_path
    os.environ["COMIC_BASE_PATH"] = BASE_PATH
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  AI Comic Studio 工作台 → http://{a.host}:{a.port}{BASE_PATH}")
    print(f"  生图 provider: {a.provider}｜项目目录: {PROJECTS_DIR}")
    # ★ 传 app 对象而不是 "模块:app" 字符串 —— 后者会让 uvicorn 重新导入模块，
    #   把上面设好的全局变量（BASE_PATH / _provider_name）重置回默认值。
    uvicorn.run(app, host=a.host, port=a.port, reload=a.reload)
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())
