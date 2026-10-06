# -*- coding: utf-8 -*-
"""工作台 API 测试

用 FastAPI TestClient 直接打接口，不启真实服务器。
每个测试用独立临时项目目录，互不污染。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from packages.ir import (
    Appearance, Bible, Box, BubbleSpec, BubbleStyle, CastMember, Character,
    CharacterState, ComicProject, DamageLevel, DialogueBook, Layout,
    LayoutPage, PageType, Panel, PanelSize, Scene, ShotSize, Skill,
    Storyboard, StyleSpec, Utterance,
)


# ══════════════════════════════════════════════════════════════════
# 夹具：构造一个临时项目目录并指向它
# ══════════════════════════════════════════════════════════════════

def _write_project(root: Path, name: str = "t1") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "assets" / "panels").mkdir(parents=True, exist_ok=True)

    bible = Bible(
        characters={
            "a": Character(id="a", name="甲",
                           appearance=Appearance(text="甲的外观标准描述块，足够长以通过校验。" * 2)),
            "b": Character(id="b", name="乙",
                           appearance=Appearance(text="乙的外观标准描述块，足够长以通过校验。" * 2)),
        },
        scenes={"s": Scene(id="s", name="场地", text="一片雪原。")},
        skills={"k": Skill(id="k", name="技能", element="冰系")},
        style=StyleSpec(shared_block="全彩国漫风格的商业漫画页，柔和通透。" * 3),
    )
    boards = [Storyboard(chapter="1", panels=[
        Panel(id="ch1_P001", seq=1, size=PanelSize.WIDE, shot=ShotSize.WIDE,
              cast=[CastMember(id="a", pos="left"), CastMember(id="b", pos="right")],
              distance="相距约 8 米", action="甲拉弓，乙戒备",
              skill="k", background="s", damage_level=DamageLevel.L0,
              emotion_hint={"a": "冷淡", "b": "倨傲"}, source_span=[1, 1]),
        Panel(id="ch1_P002", seq=2, size=PanelSize.TALL, shot=ShotSize.CLOSE,
              cast=[CastMember(id="a", pos="center")],
              action="甲冷眸直视前方", background="s", damage_level=DamageLevel.L0,
              emotion_hint={"a": "锁定"}, source_span=[2, 2]),
    ])]
    dlg = DialogueBook(items={"ch1_P001": [
        Utterance(id="u1", who="a", text="这是一句原文台词。", source_span=[1, 1],
                  confirmed=True, bubble=BubbleSpec(style=BubbleStyle.SPEECH)),
    ]})
    layout = Layout(total=3, pages=[
        LayoutPage(page=1, type=PageType.TITLE, title="测试"),
        LayoutPage(page=2, type=PageType.PANELS, panels=["ch1_P001"]),
        LayoutPage(page=3, type=PageType.PANELS, panels=["ch1_P002"]),
    ])

    (d / "bible.json").write_text(bible.model_dump_json(indent=1), encoding="utf-8")
    (d / "storyboard.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in boards], ensure_ascii=False),
        encoding="utf-8")
    (d / "dialogue.json").write_text(dlg.model_dump_json(indent=1), encoding="utf-8")
    (d / "layout.json").write_text(layout.model_dump_json(indent=1), encoding="utf-8")
    (d / "source.txt").write_text("这是一句原文台词。\n甲冷眸直视前方。\n", encoding="utf-8")

    # 放一张 Mock 底图，让页面渲染能出图
    from packages.render import MockImageProvider
    from packages.render.providers import ImageRequest
    for pid in ("ch1_P001", "ch1_P002"):
        MockImageProvider().generate_to_file(
            ImageRequest(prompt="x", size="1536x1536"),
            str(d / "assets" / "panels" / f"{pid}.png"))
    return d


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """把 PROJECTS_DIR 指向临时目录，并注入测试项目"""
    root = tmp_path / "projects"
    root.mkdir()
    _write_project(root, "t1")

    import apps.api.server as srv
    monkeypatch.setattr(srv, "PROJECTS_DIR", root)
    # 清掉渲染缓存，避免跨测试污染
    with srv._cache_lock:
        srv._cache.clear()

    from fastapi.testclient import TestClient
    return TestClient(srv.app)


# ══════════════════════════════════════════════════════════════════
# 基础接口
# ══════════════════════════════════════════════════════════════════

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_list_projects(client):
    r = client.get("/api/projects")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()]
    assert "t1" in names


def test_get_project_payload(client):
    r = client.get("/api/projects/t1")
    assert r.status_code == 200
    d = r.json()
    assert d["stats"]["panels"] == 2
    assert d["stats"]["utterances"] == 1
    assert d["stats"]["pages"] == 3
    assert "bible" in d and "layout" in d


def test_get_unknown_project_404(client):
    assert client.get("/api/projects/nope").status_code == 404


def test_path_traversal_blocked(client):
    """项目名必须是安全字符，防止路径穿越"""
    r = client.get("/api/projects/..%2F..%2Fetc")
    assert r.status_code in (400, 404)


def test_rules_endpoint(client):
    r = client.get("/api/rules")
    assert r.status_code == 200
    assert len(r.json()) == 12


def test_validate_endpoint(client):
    r = client.post("/api/projects/t1/validate")
    assert r.status_code == 200
    d = r.json()
    assert "ok" in d and "errors" in d and "rules" in d


# ══════════════════════════════════════════════════════════════════
# 渲染
# ══════════════════════════════════════════════════════════════════

def test_page_image(client):
    r = client.get("/api/projects/t1/pages/2.png?w=400")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert len(r.content) > 2000


def test_page_image_title_page(client):
    r = client.get("/api/projects/t1/pages/1.png?w=400")
    assert r.status_code == 200


def test_page_image_out_of_range(client):
    assert client.get("/api/projects/t1/pages/99.png").status_code == 404


def test_panel_image(client):
    r = client.get("/api/projects/t1/panels/ch1_P001.png?w=300")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_panel_image_missing_404(client):
    assert client.get("/api/projects/t1/panels/nope.png").status_code == 404


def test_prompt_endpoint_exposes_final_prompt(client):
    """提示词透明化 —— 让人能看见到底送了什么给生图模型"""
    r = client.get("/api/projects/t1/prompt/ch1_P001")
    assert r.status_code == 200
    d = r.json()
    assert "全彩国漫风格" in d["prompt"]        # 共享风格块
    assert "甲的外观标准描述块" in d["prompt"]   # 角色标准块
    assert "相距约 8 米" in d["prompt"]          # 本格构图
    assert "绝对不要出现任何文字" in d["prompt"]  # 禁止文字


# ══════════════════════════════════════════════════════════════════
# 编辑闭环
# ══════════════════════════════════════════════════════════════════

def test_patch_bubble_position(client):
    r = client.patch("/api/projects/t1/dialogue/ch1_P001/u1",
                     json={"box": {"x": 0.7, "y": 0.1}})
    assert r.status_code == 200
    assert r.json()["utterance"]["bubble"]["box"] == {"x": 0.7, "y": 0.1}


def test_patch_tail(client):
    r = client.patch("/api/projects/t1/dialogue/ch1_P001/u1",
                     json={"tail": {"x": 0.3, "y": 0.5}})
    assert r.status_code == 200
    assert r.json()["utterance"]["bubble"]["tail"] == {"x": 0.3, "y": 0.5}


def test_patch_speaker_resets_confirmation(client):
    """换说话人必须把 confirmed 置回 false（防张冠李戴）"""
    r = client.patch("/api/projects/t1/dialogue/ch1_P001/u1",
                     json={"who": "b"})
    assert r.status_code == 200
    u = r.json()["utterance"]
    assert u["who"] == "b"
    assert u["confirmed"] is False


def test_patch_speaker_unknown_rejected(client):
    r = client.patch("/api/projects/t1/dialogue/ch1_P001/u1",
                     json={"who": "ghost"})
    assert r.status_code == 400


def test_patch_locked(client):
    r = client.patch("/api/projects/t1/dialogue/ch1_P001/u1",
                     json={"locked": True})
    assert r.status_code == 200
    assert r.json()["utterance"]["locked"] is True


def test_patch_text(client):
    r = client.patch("/api/projects/t1/dialogue/ch1_P001/u1",
                     json={"text": "换了一句台词。"})
    assert r.status_code == 200
    assert r.json()["utterance"]["text"] == "换了一句台词。"


def test_patch_unknown_utterance_404(client):
    assert client.patch("/api/projects/t1/dialogue/ch1_P001/nope",
                        json={"locked": True}).status_code == 404


def test_patch_panel_distance(client):
    r = client.patch("/api/projects/t1/panels/ch1_P001",
                     json={"distance": "相距约 12 米"})
    assert r.status_code == 200
    assert r.json()["panel"]["distance"] == "相距约 12 米"


def test_patch_panel_unknown_404(client):
    assert client.patch("/api/projects/t1/panels/nope",
                        json={"mood": "x"}).status_code == 404


def test_edit_persists_to_disk(client):
    """编辑必须写回 JSON（而不是只改内存）—— 这是「数据与产物分离」的落地"""
    client.patch("/api/projects/t1/dialogue/ch1_P001/u1",
                 json={"text": "落盘测试。"})
    import apps.api.server as srv
    raw = json.loads((srv.PROJECTS_DIR / "t1" / "dialogue.json")
                     .read_text(encoding="utf-8"))
    assert raw["items"]["ch1_P001"][0]["text"] == "落盘测试。"


def test_no_page_mutation_endpoint(client):
    """★ 页码冻结：不应存在任何改页码的接口"""
    paths = {getattr(r, "path", "") for r in client.app.routes}
    assert not any("layout" in p or "page" in p.lower() and "png" not in p
                   for p in paths if p.startswith("/api") and "{" not in p.split("/")[-1])
    # 更直接：试着 PATCH layout，应 405/404
    r = client.patch("/api/projects/t1/layout", json={"total": 1})
    assert r.status_code in (404, 405)


# ══════════════════════════════════════════════════════════════════
# 导出
# ══════════════════════════════════════════════════════════════════

def test_export_pdf_and_download(client):
    r = client.post("/api/projects/t1/export?fmt=pdf")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["file"].endswith("comic.pdf")

    r2 = client.get(d["file"])
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "application/pdf"
    assert len(r2.content) > 3000


def test_export_bad_format(client):
    assert client.post("/api/projects/t1/export?fmt=docx").status_code == 400


def test_download_traversal_blocked(client):
    r = client.get("/api/projects/t1/download/..%2F..%2Fbible.json")
    assert r.status_code in (400, 404)


# ══════════════════════════════════════════════════════════════════
# 任务
# ══════════════════════════════════════════════════════════════════

def test_render_task_lifecycle(client):
    r = client.post("/api/projects/t1/render")
    assert r.status_code == 200
    tid = r.json()["task"]["id"]

    # 轮询直到结束（Mock 生图极快）
    import time
    for _ in range(60):
        t = client.get(f"/api/tasks/{tid}").json()
        if t["status"] != "running":
            break
        time.sleep(0.05)
    assert t["status"] == "done"
    assert t["total"] == 2

    lst = client.get("/api/tasks").json()
    assert any(x["id"] == tid for x in lst)


def test_task_not_found(client):
    assert client.get("/api/tasks/nope").status_code == 404


# ══════════════════════════════════════════════════════════════════
# 静态前端
# ══════════════════════════════════════════════════════════════════

def test_workbench_static(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "AI Comic Studio" in r.text

    for f in ("app.js", "style.css"):
        rr = client.get(f"/static/{f}")
        assert rr.status_code == 200
        assert len(rr.content) > 500
