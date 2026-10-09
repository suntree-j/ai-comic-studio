# -*- coding: utf-8 -*-
"""面板编辑器测试

覆盖三层：
    · 模型层   packages/editor/models.py —— 坐标校验、页码是身份、顺序是 order
    · 渲染层   packages/editor/render.py —— 包围盒、气泡高度、★ 箭头几何
    · 接口层   apps/api/editor.py        —— 上传/建页/加元素/排序/撤销/导出/防护

★ 其中「箭头几何」与「图片包围盒」那几条是踩坑后的回归测试：
  · 尾巴基点最初用「圆心 + 半径×方向」近似，箭头拖远时基点被推到画面外，
    渲染出一个巨大白三角把整页挡住；
  · 图片包围盒高度最初把「像素宽」当成「比例高」返回，
    前端画出的选择框比实际图片高出一大截。
  这两类错误接口都返回 200，只有肉眼看图或量几何才发现。
"""

from __future__ import annotations

import base64
import io
import json
import os
import re

import numpy as np
import pytest
from PIL import Image, ImageDraw

from packages.editor import (
    Asset, BubbleElement, BubbleKind, EditProject, EditStore, ImageElement,
    Page, ShapeElement, TextElement, bubble_geometry, hex_to_rgb, new_id,
    parse_element, render_page,
)
from packages.editor.render import _edge_point, _image_box_h

P = "/api/edit"


# ══════════════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════════════

def _img(w=1600, h=1100, color=(30, 40, 60)):
    im = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(im)
    d.ellipse([w * 0.3, h * 0.3, w * 0.7, h * 0.8], fill=(230, 235, 245))
    return im


def _png(w=1600, h=1100):
    b = io.BytesIO()
    _img(w, h).save(b, "PNG")
    return b.getvalue()


@pytest.fixture()
def project():
    p = EditProject(name="t", title="测试")
    a = Asset(filename="a.png", stored_name="a.png", width=1600, height=1100)
    p.assets.append(a)
    pg = Page(number=1, order=0, width=1400, height=2000)
    p.pages.append(pg)
    return p, a, pg


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import apps.api.editor as E
    monkeypatch.setattr(E, "WORKSPACE", str(tmp_path / "works"))
    monkeypatch.setattr(E, "store", EditStore(str(tmp_path / "works")))
    # 撤销快照现在落在项目的 .history/ 下，随临时目录一起隔离，无需手动清
    from fastapi.testclient import TestClient
    from apps.api.server import app
    return TestClient(app)


def _new_project(client, name="p1"):
    r = client.post(f"{P}/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _upload(client, name="p1", w=1600, h=1100):
    r = client.post(f"{P}/projects/{name}/assets",
                    files=[("files", ("a.png", _png(w, h), "image/png"))])
    return r.json()["added"][0]


def _add_el(client, name, pid, **body):
    """加一个元素，返回**整个元素对象**"""
    body.setdefault("kind", "bubble")
    r = client.post(f"{P}/projects/{name}/pages/{pid}/elements", json=body)
    assert r.status_code == 200, r.text
    return r.json()["element"]


def _add(client, name, pid, **body):
    """加一个元素，返回**元素 id**（大多数用例只关心 id）"""
    return _add_el(client, name, pid, **body)["id"]


# ══════════════════════════════════════════════════════════════════
# 模型层
# ══════════════════════════════════════════════════════════════════

def test_parse_element_by_kind():
    for kind, cls in (("image", ImageElement), ("bubble", BubbleElement),
                      ("text", TextElement), ("shape", ShapeElement)):
        assert isinstance(parse_element({"kind": kind}), cls)


def test_parse_element_unknown_kind():
    with pytest.raises(ValueError, match="未知的元素类型"):
        parse_element({"kind": "wat"})


def test_parse_element_needs_kind():
    """★ 从 dict 构造时必须显式带 kind（Literal 判别字段）"""
    with pytest.raises(ValueError):
        parse_element({})


def test_coordinates_reject_nan():
    with pytest.raises(ValueError):
        BubbleElement(x=float("nan"))


def test_new_id_is_unique_and_not_model_instance():
    """★ 不要用 Model().id 取新 id —— 有必填字段的模型会抛异常"""
    assert new_id() != new_id()
    c = Page                                 # 有必填 number，直接实例化会报错
    with pytest.raises(Exception):
        c()
    assert len(new_id()) == 10


def test_page_number_is_identity_order_is_position():
    """★ 页码是身份：调顺序改 order，number 不动"""
    p = EditProject(name="x")
    p.pages = [Page(number=7, order=0), Page(number=8, order=1),
               Page(number=9, order=2)]
    p.pages[0].order = 2
    p.pages[2].order = 0
    assert [x.number for x in p.ordered_pages()] == [9, 8, 7]
    assert sorted(x.number for x in p.pages) == [7, 8, 9]


def test_next_number_never_recycles():
    """★ 页码只增不回收

    删掉最后一页后新页拿更大的号，不复用刚删掉的那个 ——
    否则「第 3 页」会因为删页而一会儿指这页一会儿指那页。
    """
    # 走 Pydantic 校验路径构造，让不变式生效
    p = EditProject.model_validate({
        "name": "x",
        "pages": [{"number": 1}, {"number": 2}, {"number": 3}],
    })
    assert p.max_number == 3, "构造时就该把已用过的最大页码记下来"
    p.pages.pop()
    assert p.next_number() == 4, "删掉第 3 页后，新页不该再拿到 3"


def test_sorted_elements_by_z(project):
    _, _, pg = project
    pg.elements = [ShapeElement(z=2, id="a"), ShapeElement(z=0, id="b"),
                   ShapeElement(z=1, id="c")]
    assert [e.id for e in pg.sorted_elements()] == ["b", "c", "a"]


def test_normalize_z(project):
    _, _, pg = project
    pg.elements = [ShapeElement(z=50, id="a"), ShapeElement(z=10, id="b")]
    pg.normalize_z()
    assert [e.id for e in pg.sorted_elements()] == ["b", "a"]
    assert sorted(e.z for e in pg.elements) == [0, 1]


def test_hex_to_rgb():
    assert hex_to_rgb("#ff8000") == (255, 128, 0)
    assert hex_to_rgb("f80") == (255, 136, 0)
    assert hex_to_rgb("") == (0, 0, 0)
    assert hex_to_rgb("垃圾") == (0, 0, 0)          # 非法值不抛异常
    assert hex_to_rgb("zzzzzz", (1, 2, 3)) == (1, 2, 3)


# ══════════════════════════════════════════════════════════════════
# 渲染层
# ══════════════════════════════════════════════════════════════════

def test_render_empty_page(project):
    _, _, pg = project
    img, boxes = render_page(pg, lambda a: None, target_width=600,
                             with_boxes=True)
    assert img.size == (600, 857)
    assert boxes == {}


def test_render_scales_by_width(project):
    _, _, pg = project
    a = render_page(pg, lambda x: None, 700)[0]
    b = render_page(pg, lambda x: None, 1400)[0]
    assert a.width == 700 and b.width == 1400
    assert abs(a.height / a.width - b.height / b.width) < 0.002


def test_render_is_deterministic(project):
    """★ 纯函数：同数据同素材 → 逐像素一致"""
    _, a, pg = project
    pg.elements.append(BubbleElement(text="测试台词", speaker="甲", x=0.1, y=0.1))
    pg.elements.append(ShapeElement(x=0.5, y=0.5, w=0.2, h=0.1, fill="#aaddff"))
    x1, _ = render_page(pg, lambda i: _img(), 600, with_boxes=True)
    x2, _ = render_page(pg, lambda i: _img(), 600, with_boxes=True)
    assert x1.tobytes() == x2.tobytes()


def test_bubble_height_grows_with_text():
    short = BubbleElement(text="短", w=0.34)
    long = BubbleElement(text="很长的一段台词。" * 6, w=0.34)
    assert bubble_geometry(long, 1000, 1400)[3] > \
        bubble_geometry(short, 1000, 1400)[3] * 1.8


def test_narrower_bubble_is_taller():
    """同样文字，气泡越窄行数越多 → 越高"""
    el = BubbleElement(text="一二三四五六七八九十" * 3)
    el.w = 0.5
    h_wide = bubble_geometry(el, 1000, 1400)[3]
    el.w = 0.2
    h_narrow = bubble_geometry(el, 1000, 1400)[3]
    assert h_narrow > h_wide


def test_bubble_box_position():
    el = BubbleElement(text="测试", x=0.1, y=0.2, w=0.3)
    x, y, w, h = bubble_geometry(el, 1000, 1400)
    assert x == pytest.approx(100)
    assert y == pytest.approx(280)
    assert w == pytest.approx(300)
    assert h > 0


def test_speaker_adds_height():
    a = BubbleElement(text="一样的话")
    b = BubbleElement(text="一样的话", speaker="穆宁雪")
    assert bubble_geometry(b, 1000, 1400)[3] > bubble_geometry(a, 1000, 1400)[3]


def test_image_box_height_matches_aspect(project):
    """★ 回归：图片包围盒高度按原图比例算

    最初把「像素宽」当成「比例高」返回，前端选择框比实际图片高一大截。
    """
    _, a, pg = project
    el = ImageElement(asset_id=a.id, x=0, y=0, w=1.0)
    img = _img(1600, 1100)
    h = _image_box_h(el, img, (pg.width, pg.height))
    expect = 1.0 * (1400 / 2000) * (1100 / 1600)
    assert abs(h - expect) < 1e-6, f"得到 {h:.4f}，期望 {expect:.4f}"

    # 与渲染结果一致
    pg.elements.append(el)
    _, boxes = render_page(pg, lambda i: img, 800, with_boxes=True)
    assert abs(boxes[el.id][3] - expect) < 0.005


def test_image_box_height_respects_explicit_h(project):
    _, a, pg = project
    el = ImageElement(asset_id=a.id, w=0.5, h=0.25)
    assert _image_box_h(el, _img(), (pg.width, pg.height)) == 0.25


def test_missing_asset_is_skipped_entirely(project):
    """素材丢了既不画、也不给包围盒（否则前端会画一个点不到的空框）"""
    _, _, pg = project
    pg.elements.append(ImageElement(asset_id="不存在", x=0, y=0, w=0.5))
    pg.elements.append(BubbleElement(text="还在", x=0.1, y=0.1))
    img, boxes = render_page(pg, lambda i: None, 400, with_boxes=True)
    assert img.size[0] == 400
    assert len(boxes) == 1
    kinds = [next(e.kind for e in pg.elements if e.id == k) for k in boxes]
    assert kinds == ["bubble"]


def test_all_bubble_styles_render(project):
    _, _, pg = project
    for st in BubbleKind:
        pg.elements.append(BubbleElement(text="样式测试", style=st,
                                         x=0.05, y=0.05, w=0.4))
    img, boxes = render_page(pg, lambda i: None, 700, with_boxes=True)
    assert img.size[0] == 700
    assert len(boxes) == len(list(BubbleKind))


def test_opacity_is_applied(project):
    _, _, pg = project
    pg.elements.append(ShapeElement(x=0, y=0, w=1, h=1, fill="#000000",
                                    opacity=0.5))
    img, _ = render_page(pg, lambda i: None, 200)
    px = img.getpixel((100, 100))
    assert 100 < px[0] < 200, f"不透明度没生效：{px}"


def test_shapes_and_text_render(project):
    _, _, pg = project
    pg.elements.append(ShapeElement(x=0.1, y=0.1, w=0.3, h=0.2,
                                    fill="#ff0000", shape="ellipse"))
    pg.elements.append(TextElement(text="标题", x=0.1, y=0.5, w=0.6))
    img, boxes = render_page(pg, lambda i: None, 500, with_boxes=True)
    assert len(boxes) == 2
    # 椭圆中心应该是红的
    px = img.getpixel((int(500 * 0.25), int(714 * 0.2)))
    assert px[0] > 200 and px[1] < 80


# ── ★ 箭头几何：踩坑回归 ────────────────────────────────────────

def test_edge_point_lies_on_rect_border():
    """基点必须落在矩形边界上（这是巨大白三角 bug 的根因）"""
    box = [100.0, 100.0, 300.0, 200.0]
    for tip in ((900, 150), (200, -800), (-500, 150), (200, 900),
                (901, 899), (-500, -500)):
        x, y = _edge_point(box, tip)
        inside = (100 - 1e-6 <= x <= 300 + 1e-6
                  and 100 - 1e-6 <= y <= 200 + 1e-6)
        on_v = abs(x - 100) < 1e-6 or abs(x - 300) < 1e-6
        on_h = abs(y - 100) < 1e-6 or abs(y - 200) < 1e-6
        assert inside, f"基点跑到矩形外：{tip} → ({x}, {y})"
        assert on_v or on_h, f"基点不在边界上：{tip} → ({x}, {y})"


def test_edge_point_centre_returns_centre():
    assert _edge_point([0.0, 0.0, 100.0, 100.0], (50.0, 50.0)) == (50.0, 50.0)


def _white_ratio(arr) -> float:
    return float(((arr[:, :, 0] > 240) & (arr[:, :, 1] > 240)
                  & (arr[:, :, 2] > 240)).mean())


def test_tail_does_not_paint_huge_area(project):
    """★ 回归：箭头拖远时不能糊掉整页"""
    _, _, pg = project
    pg.background = "#808080"           # 灰底：白色像素 = 画出来的东西
    pg.elements.append(BubbleElement(text="一句话", x=0.6, y=0.05, w=0.3,
                                     tail=(0.05, 0.95)))
    img, _ = render_page(pg, lambda i: None, 800)
    a = np.asarray(img)
    h, w = a.shape[:2]
    patch = a[int(h * 0.78):, :int(w * 0.25)]
    r = _white_ratio(patch)
    assert r < 0.25, f"箭头把底部糊住了（白色占比 {r:.2f}）"


def test_tail_points_toward_tip(project):
    """尾巴方向要指向尖端那一侧

    ★ 尾巴有长度上限（画布短边的 38%），所以不能断言「一定碰到尖端」，
      只能断言「尾迹朝尖端方向延伸，且不超过上限」。
    """
    _, _, pg = project
    pg.background = "#808080"
    pg.elements.append(BubbleElement(text="A", x=0.02, y=0.02, w=0.22,
                                     tail=(0.8, 0.9)))
    img, _ = render_page(pg, lambda i: None, 800)
    a = np.asarray(img)
    h, w = a.shape[:2]
    mask = (a[:, :, 0] > 240) & (a[:, :, 1] > 240) & (a[:, :, 2] > 240)
    ys, xs = np.where(mask)
    assert len(xs) > 0, "什么都没画出来"
    bubble_r = 0.24 * w            # 气泡右边界
    bubble_b = 0.20 * h            # 气泡下边界
    assert xs.max() > bubble_r, "尾巴没往右延伸"
    assert ys.max() > bubble_b, "尾巴没往下延伸"
    # 受长度上限约束（画布短边 38%），尖端 (0.8, 0.9) 是够不到的
    assert xs.max() < 0.8 * w, "尾巴长得离谱，超出了长度上限"


def test_no_tail_means_no_ink_outside_bubble(project):
    _, _, pg = project
    pg.background = "#808080"
    pg.elements.append(BubbleElement(text="无箭头", x=0.1, y=0.1, w=0.3,
                                     tail=None))
    img, _ = render_page(pg, lambda i: None, 400)
    a = np.asarray(img)
    h, w = a.shape[:2]
    # 气泡占 x 0.1–0.4 / y 0.1–0.18；更远的右下角应干净
    q = a[int(h * 0.35):, int(w * 0.55):]
    assert _white_ratio(q) < 0.01, "没设箭头却画出了东西"


def test_tail_base_width_bounded(project):
    """尾巴根部不能比气泡还宽"""
    _, _, pg = project
    pg.background = "#808080"
    pg.elements.append(BubbleElement(text="短", x=0.4, y=0.4, w=0.12,
                                     tail=(0.46, 0.9), tail_w=1.0))
    img, _ = render_page(pg, lambda i: None, 600)
    a = np.asarray(img)
    h, w = a.shape[:2]
    # 气泡正下方紧邻的一行，白色宽度不应超过气泡宽度太多
    row = a[int(h * 0.44), :]
    cols = np.where((row[:, 0] > 240) & (row[:, 1] > 240) & (row[:, 2] > 240))[0]
    if len(cols):
        tail_w_px = cols.max() - cols.min()
        bubble_w_px = 0.12 * w
        assert tail_w_px < bubble_w_px * 0.9, \
            f"尾巴根部 {tail_w_px}px 比气泡 {bubble_w_px:.0f}px 还宽"


# ══════════════════════════════════════════════════════════════════
# Store
# ══════════════════════════════════════════════════════════════════

def test_store_safe_name_blocks_traversal():
    assert EditStore.safe_name("../../evil") == "evil"
    assert "/" not in EditStore.safe_name("a/b")
    assert EditStore.safe_name("中文名") == "中文名"
    with pytest.raises(ValueError):
        EditStore.safe_name("///")


def test_store_create_load_save(tmp_path):
    st = EditStore(str(tmp_path))
    st.create("proj", "标题", (1000, 1400))
    got = st.load("proj")
    assert got.title == "标题" and len(got.pages) == 1
    assert got.pages[0].number == 1
    got.title = "改过"
    st.save(got)
    assert st.load("proj").title == "改过"


def test_store_rejects_duplicate(tmp_path):
    st = EditStore(str(tmp_path))
    st.create("p1")
    with pytest.raises(FileExistsError):
        st.create("p1")


def test_store_add_asset_reads_real_size(tmp_path):
    st = EditStore(str(tmp_path))
    st.create("p1")
    a = st.add_asset("p1", "x.png", _png(800, 500))
    assert (a.width, a.height) == (800, 500)
    assert os.path.isfile(st.asset_path("p1", a.stored_name))


def test_store_add_asset_rejects_non_image(tmp_path):
    st = EditStore(str(tmp_path))
    st.create("p1")
    with pytest.raises(Exception):
        st.add_asset("p1", "x.png", b"this is not an image")


def test_save_is_atomic_no_tmp_left(tmp_path):
    st = EditStore(str(tmp_path))
    st.create("p1")
    p = st.load("p1")
    st.save(p)
    leftovers = [f for f in os.listdir(st.dir_of("p1")) if f.endswith(".tmp")]
    assert not leftovers, "原子写没清理临时文件"


# ══════════════════════════════════════════════════════════════════
# 接口层
# ══════════════════════════════════════════════════════════════════

def test_editor_endpoints_registered():
    """编辑器接口都真的注册上了（FastAPI 0.142 的 include_router 是延迟展开）"""
    from apps.api.server import _editor_route_paths
    paths = _editor_route_paths()
    for need in ("/api/edit/projects",
                 "/api/edit/projects/{name}/assets",
                 "/api/edit/projects/{name}/pages",
                 "/api/edit/projects/{name}/elements/order",
                 "/api/edit/projects/{name}/undo",
                 "/api/edit/projects/{name}/pages/{page_id}/render.png",
                 "/api/edit/projects/{name}/export"):
        assert need in paths, f"缺少接口 {need}"


def test_home_is_editor_workbench_kept():
    """首页是编辑器（空白画布），旧工作台保留在 /workbench"""
    from fastapi.testclient import TestClient
    from apps.api.server import app
    c = TestClient(app)
    r = c.get("/")
    assert r.status_code == 200 and "面板编辑器" in r.text and "<base" in r.text
    r2 = c.get("/workbench")
    assert r2.status_code == 200 and "漫画工作台" in r2.text


def test_create_and_get_project(client):
    _new_project(client)
    d = client.get(f"{P}/projects/p1").json()
    assert d["stats"]["pages"] == 1 and d["stats"]["elements"] == 0
    assert d["stats"]["assets"] == 0


def test_create_duplicate_409(client):
    _new_project(client)
    assert client.post(f"{P}/projects", json={"name": "p1"}).status_code == 409


def test_upload_assets(client):
    _new_project(client)
    files = [("files", (f"a{i}.png", _png(), "image/png")) for i in range(3)]
    d = client.post(f"{P}/projects/p1/assets", files=files).json()
    assert len(d["added"]) == 3 and not d["failed"]
    assert d["added"][0]["width"] == 1600


def test_upload_rejects_non_image(client):
    _new_project(client)
    d = client.post(f"{P}/projects/p1/assets",
                    files=[("files", ("x.png", b"nope", "image/png"))]).json()
    assert len(d["added"]) == 0 and len(d["failed"]) == 1


def test_asset_raw_served_scaled(client):
    _new_project(client)
    aid = _upload(client)["id"]
    r = client.get(f"{P}/projects/p1/assets/{aid}/raw?w=200")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.content)).width == 200


def test_add_image_element_uses_asset_aspect(client):
    _new_project(client)
    aid = _upload(client)["id"]
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    e = _add_el(client, "p1", pid, kind="image", asset_id=aid, x=0, y=0, w=1)
    assert abs(e["h"] - (1.0 * 1400 / 2000 * 1100 / 1600)) < 0.01


def test_add_element_requires_valid_asset(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    r = client.post(f"{P}/projects/p1/pages/{pid}/elements",
                    json={"kind": "image", "asset_id": "不存在"})
    assert r.status_code == 400


def test_add_bubble_auto_place(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    e = _add_el(client, "p1", pid, kind="bubble", text="测试", auto_place=True)
    assert 0 <= e["x"] <= 1 and 0 <= e["y"] <= 1


def test_add_text_and_shape(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    assert _add_el(client, "p1", pid, kind="text", text="标题")["kind"] == "text"
    assert _add_el(client, "p1", pid, kind="shape")["kind"] == "shape"


def test_add_unknown_kind_400(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    r = client.post(f"{P}/projects/p1/pages/{pid}/elements",
                    json={"kind": "wat"})
    assert r.status_code == 400


def test_patch_element(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    eid = _add(client, "p1", pid, text="a")
    r = client.patch(f"{P}/projects/p1/elements/{eid}",
                     json={"props": {"text": "改过了", "tail": [0.5, 0.5],
                                     "style": "shout"}})
    e = r.json()["element"]
    assert e["text"] == "改过了" and e["style"] == "shout"
    assert e["tail"] == [0.5, 0.5]


def test_patch_rejects_out_of_range(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    eid = _add(client, "p1", pid)
    r = client.patch(f"{P}/projects/p1/elements/{eid}",
                     json={"props": {"font_size": 99999}})
    assert r.status_code == 400


def test_patch_cannot_change_id_or_kind(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    eid = _add(client, "p1", pid)
    e = client.patch(f"{P}/projects/p1/elements/{eid}",
                     json={"props": {"id": "hack", "kind": "shape"}}
                     ).json()["element"]
    assert e["id"] == eid and e["kind"] == "bubble"


def test_delete_element(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    eid = _add(client, "p1", pid, kind="shape")
    assert client.delete(f"{P}/projects/p1/elements/{eid}").status_code == 200
    assert client.get(f"{P}/projects/p1").json()["stats"]["elements"] == 0


def test_duplicate_element_offsets(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    eid = _add(client, "p1", pid, text="原件", x=0.2, y=0.2)
    e = client.post(f"{P}/projects/p1/elements/{eid}/duplicate").json()["element"]
    assert e["id"] != eid and e["text"] == "原件" and e["x"] > 0.2


def test_reorder_elements(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    ids = [_add(client, "p1", pid, kind="shape") for _ in range(3)]
    r = client.post(f"{P}/projects/p1/elements/order",
                    json={"element_ids": list(reversed(ids))})
    assert r.status_code == 200 and r.json()["order"] == list(reversed(ids))


def test_reorder_elements_rejects_mismatch(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    _add(client, "p1", pid, kind="shape")
    r = client.post(f"{P}/projects/p1/elements/order",
                    json={"element_ids": ["不存在"]})
    assert r.status_code == 400


def test_batch_move(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    eid = _add(client, "p1", pid, kind="shape")
    r = client.post(f"{P}/projects/p1/elements/batch-move",
                    json={"moves": {eid: {"x": 0.42, "y": 0.43}}})
    assert r.json()["moved"] == 1
    e = client.get(f"{P}/projects/p1").json()["pages"][0]["elements"][0]
    assert abs(e["x"] - 0.42) < 1e-6


# ── 页面顺序：页码不变 ──────────────────────────────────────────

def test_page_reorder_keeps_numbers(client):
    """★ 核心约定：调顺序改 order，number 永远不变"""
    _new_project(client)
    for _ in range(2):
        client.post(f"{P}/projects/p1/pages", json={})
    before = client.get(f"{P}/projects/p1").json()["pages"]
    nums = sorted(p["number"] for p in before)
    first = sorted(before, key=lambda x: x["order"])[0]["id"]

    client.post(f"{P}/projects/p1/pages/{first}/move?delta=1")
    after = client.get(f"{P}/projects/p1").json()["pages"]
    assert sorted(p["number"] for p in after) == nums
    assert sorted(after, key=lambda x: x["order"])[0]["id"] != first


def test_page_reorder_by_list(client):
    _new_project(client)
    for _ in range(2):
        client.post(f"{P}/projects/p1/pages", json={})
    pages = client.get(f"{P}/projects/p1").json()["pages"]
    ids = [p["id"] for p in sorted(pages, key=lambda x: x["order"])]
    new = ids[1:] + ids[:1]
    r = client.post(f"{P}/projects/p1/pages/reorder", json={"page_ids": new})
    assert r.status_code == 200 and r.json()["order"] == new


def test_page_reorder_rejects_mismatch(client):
    _new_project(client)
    assert client.post(f"{P}/projects/p1/pages/reorder",
                       json={"page_ids": ["x"]}).status_code == 400
    ids = [p["id"] for p in client.get(f"{P}/projects/p1").json()["pages"]]
    assert client.post(f"{P}/projects/p1/pages/reorder",
                       json={"page_ids": ids + ["ghost"]}).status_code == 400


def test_delete_page_number_not_recycled(client):
    _new_project(client)
    p2 = client.post(f"{P}/projects/p1/pages", json={}).json()["page"]
    client.delete(f"{P}/projects/p1/pages/{p2['id']}")
    r = client.post(f"{P}/projects/p1/pages", json={})
    assert r.json()["page"]["number"] == 3, "删掉的页码不该被复用"


def test_page_move_clamps_at_edges(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    r = client.post(f"{P}/projects/p1/pages/{pid}/move?delta=-5")
    assert r.status_code == 200 and r.json()["moved"] == 0


def test_duplicate_page_gives_new_element_ids(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    eid = _add(client, "p1", pid, text="原页")
    new = client.post(f"{P}/projects/p1/pages/{pid}/duplicate").json()["page"]
    assert new["id"] != pid
    assert len(new["elements"]) == 1
    assert new["elements"][0]["id"] != eid


def test_patch_page(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    r = client.patch(f"{P}/projects/p1/pages/{pid}",
                     json={"title": "封面", "width": 1600, "height": 2400})
    p = r.json()["page"]
    assert p["title"] == "封面" and p["width"] == 1600


# ── 渲染与导出 ──────────────────────────────────────────────────

def test_render_png_with_boxes(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    eid = _add(client, "p1", pid, text="测试")
    r = client.get(f"{P}/projects/p1/pages/{pid}/render.png?w=500&boxes=1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    boxes = json.loads(base64.b64decode(r.headers["X-Element-Boxes"]))
    x, y, w, h = boxes[eid]
    assert 0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1.5 and 0 < h <= 1.5


def test_render_without_boxes_header(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    r = client.get(f"{P}/projects/p1/pages/{pid}/render.png?w=300")
    assert r.status_code == 200 and "X-Element-Boxes" not in r.headers


def test_render_unknown_page_404(client):
    _new_project(client)
    assert client.get(f"{P}/projects/p1/pages/nope/render.png").status_code == 404


def test_elements_geometry_endpoint(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    _add(client, "p1", pid, kind="shape", w=0.2, h=0.1)
    r = client.get(f"{P}/projects/p1/elements-geometry?page_id={pid}&w=600")
    assert len(r.json()["boxes"]) == 1


def test_export_pdf(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    _add(client, "p1", pid, text="导出测试")
    r = client.post(f"{P}/projects/p1/export?fmt=pdf&width=600", json={})
    assert r.status_code == 200
    rr = client.get(r.json()["file"])
    assert rr.status_code == 200 and rr.content[:4] == b"%PDF"
    assert r.json()["pages"] == 1


def test_export_zip(client):
    _new_project(client)
    r = client.post(f"{P}/projects/p1/export?fmt=zip&width=400", json={})
    assert client.get(r.json()["file"]).content[:2] == b"PK"


def test_export_long(client):
    _new_project(client)
    r = client.post(f"{P}/projects/p1/export?fmt=long&width=400&long_count=1",
                    json={})
    assert r.status_code == 200 and len(r.json()["files"]) == 1


def test_export_bad_format(client):
    _new_project(client)
    assert client.post(f"{P}/projects/p1/export?fmt=docx",
                       json={}).status_code == 400


def test_export_empty_project_400(client):
    client.post(f"{P}/projects", json={"name": "p1", "first_page": False})
    assert client.post(f"{P}/projects/p1/export?fmt=pdf",
                       json={}).status_code == 400


# ── 撤销 ────────────────────────────────────────────────────────

def test_undo_restores_previous_state(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    _add(client, "p1", pid, text="第一条")
    _add(client, "p1", pid, text="第二条")
    assert client.get(f"{P}/projects/p1").json()["stats"]["elements"] == 2
    assert client.post(f"{P}/projects/p1/undo").json()["ok"] is True
    assert client.get(f"{P}/projects/p1").json()["stats"]["elements"] == 1


def test_undo_when_empty(client):
    _new_project(client)
    r = client.post(f"{P}/projects/p1/undo").json()
    assert r["ok"] is False and "reason" in r


def test_undo_restores_deleted_element(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    eid = _add(client, "p1", pid, text="要保住的")
    assert client.delete(f"{P}/projects/p1/elements/{eid}").status_code == 200
    assert client.get(f"{P}/projects/p1").json()["stats"]["elements"] == 0
    client.post(f"{P}/projects/p1/undo")
    els = client.get(f"{P}/projects/p1").json()["pages"][0]["elements"]
    assert len(els) == 1 and els[0]["text"] == "要保住的"


def test_undo_depth_reported(client):
    _new_project(client)
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    for _ in range(3):
        _add(client, "p1", pid, kind="shape")
    assert client.get(f"{P}/projects/p1/undo-depth").json()["depth"] >= 3


# ── 安全 ────────────────────────────────────────────────────────

def test_download_blocks_traversal(client):
    _new_project(client)
    r = client.get(f"{P}/projects/p1/download/..%2F..%2Fproject.json")
    assert r.status_code == 404


def test_project_name_sanitised(client):
    r = client.post(f"{P}/projects", json={"name": "../../evil"})
    assert r.status_code == 200 and r.json()["name"] == "evil"


def test_unknown_project_404(client):
    assert client.get(f"{P}/projects/nope").status_code == 404


def test_asset_delete_blocked_when_in_use(client):
    _new_project(client)
    aid = _upload(client)["id"]
    pid = client.get(f"{P}/projects/p1").json()["pages"][0]["id"]
    _add(client, "p1", pid, kind="image", asset_id=aid, w=0.5)
    assert client.delete(f"{P}/projects/p1/assets/{aid}").status_code == 409


def test_asset_delete_when_unused(client):
    _new_project(client)
    aid = _upload(client)["id"]
    assert client.delete(f"{P}/projects/p1/assets/{aid}").status_code == 200
    assert client.get(f"{P}/projects/p1").json()["stats"]["assets"] == 0


def test_delete_project(client):
    _new_project(client)
    assert client.delete(f"{P}/projects/p1").status_code == 200
    assert client.get(f"{P}/projects/p1").status_code == 404


def test_project_list_shows_stats(client):
    _new_project(client)
    d = client.get(f"{P}/projects").json()
    assert len(d) == 1 and d[0]["name"] == "p1" and d[0]["pages"] == 1


# ══════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════
# ★ 静态资源引用：白屏 bug 的回归测试
# ══════════════════════════════════════════════════════════════════

WB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "apps", "workbench")


def test_editor_html_references_resolve_to_real_files():
    """★ 回归：editor.html 引用的资源必须真能取到

    曾经写的是 href="editor.css"，而文件在 apps/workbench/editor.css、
    应用把它挂在 /static 下 -> 请求落到 /editor.css -> 404。
    结果是：CSS 没生效（布局塌成一列）+ JS 没执行（画布空白），
    但页面本身返回 200 —— 只看接口测试完全发现不了。
    """
    html = open(os.path.join(WB, "editor.html"), encoding="utf-8").read()
    refs = re.findall(r'(?:src|href)="(static/[^"]+)"', html)
    assert refs, "editor.html 没有引用任何静态资源（或用错了路径）"
    for rel in refs:
        # 应用把 /static 映射到 apps/workbench，所以 static/x -> apps/workbench/x
        actual = os.path.join(WB, *rel.split("/")[1:])
        assert os.path.exists(actual), (
            f"引用了不存在的资源：{rel}（按部署映射应落在 {actual}）")


def test_editor_html_has_no_root_relative_assets():
    """静态资源不能用根路径引用（挂子路径时会 404）"""
    html = open(os.path.join(WB, "editor.html"), encoding="utf-8").read()
    assert not re.search(r'(?:src|href)="/static/', html), "不要用 /static 绝对路径"
    # 形如 href="editor.css" 这种（不以 static/ 开头）也不允许
    bad = re.findall(r'(?:src|href)="(?!static/|/|https?:|data:)'
                     r'[^"]+\.(?:js|css)"', html)
    assert not bad, f"静态资源必须写成 static/xxx 形式，发现：{bad}"


def test_editor_assets_actually_served():
    """端到端确认这两个资源能取到（而不只是文件存在）"""
    from fastapi.testclient import TestClient
    from apps.api.server import app
    c = TestClient(app)
    for f in ("static/editor.js", "static/editor.css"):
        r = c.get("/" + f)
        assert r.status_code == 200, f"{f} -> {r.status_code}"
        assert len(r.content) > 1000


def test_editor_js_syntax():
    """editor.js 必须语法合法（否则整个界面白屏）"""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 node")
    r = subprocess.run([node, "--check", os.path.join(WB, "editor.js")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    assert r.returncode == 0, f"editor.js 语法错误：\n{(r.stderr or '')[:900]}"


def test_editor_js_calls_only_existing_apis():
    """editor.js 调的 /api/edit/* 后端必须真的注册了"""
    from apps.api.server import _editor_route_paths

    def norm(x: str) -> str:
        x = re.sub(r"\$\{[^}]*\}", "X", x)
        x = re.sub(r"\{[^}]*\}", "X", x)
        return x.rstrip("/")

    paths = {norm(x) for x in _editor_route_paths()}
    js = open(os.path.join(WB, "editor.js"), encoding="utf-8").read()
    called = {norm(m.group(0)) for m in
              re.finditer(r"/api/edit/[^`'\"?\s)]*", js)}
    miss = []
    for c in sorted(called):
        if c in paths:
            continue
        # 允许前缀拼接（如 /api/edit/projects/X 与 /api/edit/projects/{name}）
        if any(x.startswith(c) or c.startswith(x.split("X")[0].rstrip("/"))
               for x in paths if x):
            continue
        miss.append(c)
    assert not miss, f"editor.js 调了后端没有的接口：{miss}"


def test_editor_js_has_required_features():
    """用户要求的功能必须在代码里能找到"""
    js = open(os.path.join(WB, "editor.js"), encoding="utf-8").read()
    for kw, why in [
        ("uploadFiles", "上传图片"),
        ("addElement", "添加元素"),
        ("startDrag", "拖动"),
        ("startResize", "缩放"),
        ("startTailDrag", "拖箭头"),
        ("moveZ", "调元素顺序"),
        ("movePage", "调页面顺序"),
        ("duplicateElement", "复制元素"),
        ("deleteElement", "删除元素"),
        ("undo", "撤销"),
        ("exportAs", "导出"),
        ("renderProps", "属性面板"),
    ]:
        assert kw in js, f"缺少功能：{why}（{kw}）"
