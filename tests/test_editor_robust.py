# -*- coding: utf-8 -*-
"""并发 / 撤销持久化 / 上传限制 / EXIF —— 三类修复的回归测试

★ 这些都是「实跑才发现」的问题：
  · 6 个并发 PATCH 有 5 个报文件占用错误，且「后写覆盖先写」
  · 撤销栈只在内存里，重启服务就全丢
  · 上传没有应用层大小限制（只靠 nginx 的 client_max_body_size）
  · 手机竖拍照片的 EXIF 方向没处理，加进来是横的
"""

from __future__ import annotations

import io
import os
import threading
import time

import pytest
from PIL import Image, ImageDraw

from packages.editor import EditStore
from packages.editor.store import HISTORY_LIMIT

P = "/api/edit"


def _png(w=400, h=300, color=(30, 40, 60)):
    im = Image.new("RGB", (w, h), color)
    ImageDraw.Draw(im).ellipse([10, 10, w - 10, h - 10], fill=(230, 235, 245))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import apps.api.editor as E
    monkeypatch.setattr(E, "WORKSPACE", str(tmp_path / "works"))
    monkeypatch.setattr(E, "store", EditStore(str(tmp_path / "works")))
    from fastapi.testclient import TestClient
    from apps.api.server import app
    return TestClient(app)


def _project(client, name="p"):
    client.post(f"{P}/projects", json={"name": name})
    return client.get(f"{P}/projects/{name}").json()["pages"][0]["id"]


# ══════════════════════════════════════════════════════════════════
# ① 并发：不丢改动、不报文件占用
# ══════════════════════════════════════════════════════════════════

def test_concurrent_patches_all_succeed(client):
    """★ 回归：并发改多个元素，全部要成功

    修复前：6 个并发 PATCH 有 5 个报
    `PermissionError: 另一个程序正在使用此文件`，只有 1 个成功。
    """
    pid = _project(client, "race")
    ids = [client.post(f"{P}/projects/race/pages/{pid}/elements",
                       json={"kind": "shape", "x": 0.1 * i}).json()["element"]["id"]
           for i in range(6)]

    errs = []

    def move(i):
        try:
            r = client.patch(f"{P}/projects/race/elements/{ids[i]}",
                             json={"props": {"x": 0.9}})
            if r.status_code != 200:
                errs.append((i, r.status_code, r.text[:80]))
        except Exception as e:                              # noqa: BLE001
            errs.append((i, type(e).__name__, str(e)[:80]))

    ts = [threading.Thread(target=move, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert not errs, f"并发请求出错：{errs}"
    final = client.get(f"{P}/projects/race").json()["pages"][0]["elements"]
    moved = sum(1 for e in final if abs(e["x"] - 0.9) < 1e-6)
    assert moved == 6, f"只有 {moved}/6 个改动生效（有覆盖丢失）"


def test_concurrent_edits_do_not_lose_each_other(client):
    """★ 两个「读-改-写」序列交错时不能互相覆盖

    修复前：A 改 x、B 改 y，B 后写 → A 的 x 改动消失。
    """
    pid = _project(client, "lost")
    eid = client.post(f"{P}/projects/lost/pages/{pid}/elements",
                      json={"kind": "shape", "x": 0.1, "y": 0.1}
                      ).json()["element"]["id"]

    def set_x():
        client.patch(f"{P}/projects/lost/elements/{eid}",
                     json={"props": {"x": 0.3}})

    def set_y():
        client.patch(f"{P}/projects/lost/elements/{eid}",
                     json={"props": {"y": 0.7}})

    ts = [threading.Thread(target=set_x), threading.Thread(target=set_y)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    e = client.get(f"{P}/projects/lost").json()["pages"][0]["elements"][0]
    assert abs(e["x"] - 0.3) < 1e-6, "x 的改动被覆盖了"
    assert abs(e["y"] - 0.7) < 1e-6, "y 的改动被覆盖了"


def test_store_locked_serialises(tmp_path):
    """store.locked 是真的互斥（同项目）"""
    st = EditStore(str(tmp_path))
    st.create("a")
    order = []
    gate = threading.Event()

    def slow():
        with st.locked("a"):
            order.append("slow-in")
            gate.set()
            time.sleep(0.15)
            order.append("slow-out")

    def fast():
        gate.wait(1.0)
        with st.locked("a"):
            order.append("fast-in")

    t1 = threading.Thread(target=slow)
    t2 = threading.Thread(target=fast)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert order == ["slow-in", "slow-out", "fast-in"], \
        f"锁没生效：{order}"


# ══════════════════════════════════════════════════════════════════
# ② 乐观并发控制：revision
# ══════════════════════════════════════════════════════════════════

def test_revision_increments(client):
    pid = _project(client, "rev")
    r0 = client.get(f"{P}/projects/rev").json()["revision"]
    client.post(f"{P}/projects/rev/pages/{pid}/elements", json={"kind": "shape"})
    r1 = client.get(f"{P}/projects/rev").json()["revision"]
    assert r1 > r0, "save 之后 revision 应该自增"


def test_stale_revision_gets_409(client):
    """★ 带上过期的 revision → 409，而不是静默覆盖"""
    pid = _project(client, "s")
    rev = client.get(f"{P}/projects/s").json()["revision"]

    # 别人先改了一次
    client.post(f"{P}/projects/s/pages/{pid}/elements", json={"kind": "shape"})

    # 我拿着旧 revision 提交
    r = client.patch(f"{P}/projects/s/pages/{pid}",
                     json={"title": "我的改动"}, params={"revision": rev})
    assert r.status_code == 409, f"应该 409，实际 {r.status_code}"
    assert "已被其他人修改" in r.json()["detail"]
    # 别人的改动没被覆盖
    assert client.get(f"{P}/projects/s").json()["pages"][0]["title"] != "我的改动"


def test_current_revision_accepted(client):
    pid = _project(client, "ok")
    rev = client.get(f"{P}/projects/ok").json()["revision"]
    r = client.patch(f"{P}/projects/ok/pages/{pid}",
                     json={"title": "新标题"}, params={"revision": rev})
    assert r.status_code == 200
    assert r.json()["revision"] > rev


def test_no_revision_means_no_check(client):
    """不传 revision 就按老行为（单人使用不受影响）"""
    pid = _project(client, "n")
    client.post(f"{P}/projects/n/pages/{pid}/elements", json={"kind": "shape"})
    r = client.patch(f"{P}/projects/n/pages/{pid}", json={"title": "x"})
    assert r.status_code == 200


def test_write_endpoints_return_revision(client):
    """写接口都要回传新 revision，前端才能接着用"""
    pid = _project(client, "ret")
    r1 = client.post(f"{P}/projects/ret/pages/{pid}/elements",
                     json={"kind": "shape"})
    assert "revision" in r1.json()
    eid = r1.json()["element"]["id"]
    assert "revision" in client.patch(
        f"{P}/projects/ret/elements/{eid}", json={"props": {"x": 0.2}}).json()
    assert "revision" in client.patch(
        f"{P}/projects/ret/pages/{pid}", json={"title": "t"}).json()


# ══════════════════════════════════════════════════════════════════
# ③ 撤销：落盘、重启不丢
# ══════════════════════════════════════════════════════════════════

def test_undo_survives_restart(client):
    """★ 回归：撤销历史写在磁盘上，服务重启不会丢

    修复前 `_undo` 是模块级内存 dict，restart 一次就空了。
    """
    import apps.api.editor as E
    pid = _project(client, "u")
    client.post(f"{P}/projects/u/pages/{pid}/elements",
                json={"kind": "bubble", "text": "第一条"})
    assert client.get(f"{P}/projects/u").json()["stats"]["elements"] == 1

    # 模拟「服务重启」：把内存里的 store 换成新的（磁盘不变）
    E.store = EditStore(E.WORKSPACE)

    assert client.get(f"{P}/projects/u/undo-depth").json()["depth"] >= 1
    assert client.post(f"{P}/projects/u/undo").json()["ok"] is True


def test_history_stored_on_disk(client):
    import apps.api.editor as E
    pid = _project(client, "d")
    client.post(f"{P}/projects/d/pages/{pid}/elements", json={"kind": "shape"})
    hd = E.store.history_dir("d")
    assert os.path.isdir(hd)
    assert [f for f in os.listdir(hd) if f.endswith(".json")]


def test_history_is_ring_buffer(client):
    """快照数量有上限，不会无限涨"""
    import apps.api.editor as E
    pid = _project(client, "ring")
    for i in range(HISTORY_LIMIT + 8):
        client.post(f"{P}/projects/ring/pages/{pid}/elements",
                    json={"kind": "shape", "x": 0.001 * i})
    assert E.store.history_depth("ring") <= HISTORY_LIMIT


def test_undo_multiple_steps(client):
    pid = _project(client, "m")
    for i in range(3):
        client.post(f"{P}/projects/m/pages/{pid}/elements",
                    json={"kind": "shape"})
    assert client.get(f"{P}/projects/m").json()["stats"]["elements"] == 3
    client.post(f"{P}/projects/m/undo")
    assert client.get(f"{P}/projects/m").json()["stats"]["elements"] == 2
    client.post(f"{P}/projects/m/undo")
    assert client.get(f"{P}/projects/m").json()["stats"]["elements"] == 1


def test_history_removed_with_project(client):
    import apps.api.editor as E
    pid = _project(client, "gone")
    client.post(f"{P}/projects/gone/pages/{pid}/elements", json={"kind": "shape"})
    assert os.path.isdir(E.store.history_dir("gone"))
    client.delete(f"{P}/projects/gone")
    assert not os.path.isdir(E.store.dir_of("gone"))


# ══════════════════════════════════════════════════════════════════
# ④ 上传：大小限制 + 图片校验
# ══════════════════════════════════════════════════════════════════

def _noisy_png(w=600, h=600):
    """随机噪声图 —— PNG 压不动，用来测大小限制"""
    import numpy as np
    arr = np.random.randint(0, 256, (h, w, 3), dtype=np.uint8)
    b = io.BytesIO()
    Image.fromarray(arr).save(b, "PNG")
    return b.getvalue()


def test_upload_size_limit_enforced(client, monkeypatch):
    """★ 应用层也要挡大文件（不能只靠 nginx）"""
    import apps.api.editor as E
    monkeypatch.setattr(E.store, "MAX_ASSET_BYTES", 5000)
    _project(client, "big")
    data = _noisy_png(400, 400)
    assert len(data) > 5000, "测试图本身要大于限制，否则测不到"
    r = client.post(f"{P}/projects/big/assets",
                    files=[("files", ("big.png", data, "image/png"))])
    d = r.json()
    assert len(d["added"]) == 0, f"超限的图不该被接受：{d}"
    assert len(d["failed"]) == 1
    assert "太大" in d["failed"][0]["reason"]


def test_upload_limit_boundary(client, monkeypatch):
    """刚好等于上限应该通过（边界不能误伤）"""
    import apps.api.editor as E
    _project(client, "edge")
    data = _png(200, 200)
    monkeypatch.setattr(E.store, "MAX_ASSET_BYTES", len(data))
    r = client.post(f"{P}/projects/edge/assets",
                    files=[("files", ("a.png", data, "image/png"))])
    assert len(r.json()["added"]) == 1, "刚好等于上限应通过"


def test_upload_normal_size_ok(client):
    _project(client, "ok")
    r = client.post(f"{P}/projects/ok/assets",
                    files=[("files", ("a.png", _png(500, 400), "image/png"))])
    assert len(r.json()["added"]) == 1


def test_upload_empty_file_reported(client):
    _project(client, "e")
    r = client.post(f"{P}/projects/e/assets",
                    files=[("files", ("a.png", b"", "image/png"))])
    d = r.json()
    assert not d["added"] and d["failed"][0]["reason"] == "空文件"


def test_upload_non_image_reported(client):
    _project(client, "n")
    r = client.post(f"{P}/projects/n/assets",
                    files=[("files", ("a.png", b"not an image", "image/png"))])
    d = r.json()
    assert not d["added"] and len(d["failed"]) == 1


def test_upload_returns_revision(client):
    _project(client, "ur")
    r = client.post(f"{P}/projects/ur/assets",
                    files=[("files", ("a.png", _png(), "image/png"))])
    assert "revision" in r.json()


# ══════════════════════════════════════════════════════════════════
# ⑤ EXIF 方向
# ══════════════════════════════════════════════════════════════════

def _jpeg_with_orientation(size=(120, 60), orientation=6):
    """造一张带 EXIF Orientation=6 的 JPEG（表示需要顺时针转 90° 观看）"""
    im = Image.new("RGB", size, (200, 60, 60))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, size[0] // 2, size[1]], fill=(60, 120, 200))
    exif = im.getexif()
    exif[274] = orientation            # 274 = Orientation
    b = io.BytesIO()
    im.save(b, "JPEG", exif=exif)
    return b.getvalue(), size


def test_exif_orientation_is_applied(tmp_path):
    """★ 回归：手机竖拍的照片要自动转正

    不处理的话，一张 120x60 的横图带 Orientation=6，
    会被当成 120x60 存下来，实际显示时是躺倒的。
    """
    st = EditStore(str(tmp_path))
    st.create("p")
    data, (w, h) = _jpeg_with_orientation()
    a = st.add_asset("p", "phone.jpg", data)
    assert (a.width, a.height) == (h, w), \
        f"应该转成 {h}x{w}，实际 {a.width}x{a.height}"


def test_exif_normal_image_unchanged(tmp_path):
    """没有 EXIF 的图不能被改尺寸"""
    st = EditStore(str(tmp_path))
    st.create("p")
    a = st.add_asset("p", "plain.png", _png(300, 200))
    assert (a.width, a.height) == (300, 200)


def test_load_asset_applies_orientation(tmp_path):
    st = EditStore(str(tmp_path))
    st.create("p")
    data, (w, h) = _jpeg_with_orientation()
    a = st.add_asset("p", "phone.jpg", data)
    im = st.load_asset_image("p", a.stored_name)
    assert im is not None
    assert im.size == (h, w)


def test_rgba_png_saved_as_jpeg_ext_does_not_crash(tmp_path):
    """扩展名是 jpg 但内容是带 alpha 的 PNG —— 不该崩"""
    st = EditStore(str(tmp_path))
    st.create("p")
    im = Image.new("RGBA", (80, 80), (255, 0, 0, 128))
    b = io.BytesIO()
    im.save(b, "PNG")
    a = st.add_asset("p", "weird.jpg", b.getvalue())
    assert a.width == 80
    loaded = st.load_asset_image("p", a.stored_name)
    assert loaded is not None and loaded.mode == "RGB"


# ══════════════════════════════════════════════════════════════════
# ⑥ 包围盒必须真的框住画出来的内容
# ══════════════════════════════════════════════════════════════════

def _measure_painted(img, bg_sum=150):
    """量出图上「画了东西」的像素范围，归一化返回 (x, y, w, h)"""
    import numpy as np
    a = np.asarray(img).astype(int)
    mask = a.sum(axis=2) > bg_sum
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    W, H = img.size
    return (xs.min() / W, ys.min() / H,
            (xs.max() - xs.min() + 1) / W, (ys.max() - ys.min() + 1) / H)


#: 深色底 + 纯亮图 → 亮像素的并集就是元素的外接矩形
_BRIGHT = None


def _bright_image():
    global _BRIGHT
    if _BRIGHT is None:
        _BRIGHT = Image.new("RGB", (1600, 900), (250, 250, 250))
    return _BRIGHT


@pytest.mark.parametrize("name,kw", [
    ("rot0", {"w": 0.5, "rotation": 0, "fit": "fill"}),
    ("rot15", {"w": 0.5, "rotation": 15, "fit": "fill"}),
    ("rot30", {"w": 0.5, "rotation": 30, "fit": "fill"}),
    ("rot45", {"w": 0.5, "rotation": 45, "fit": "fill"}),
    ("rot60", {"w": 0.5, "rotation": 60, "fit": "fill"}),
    ("rot90", {"w": 0.5, "rotation": 90, "fit": "fill"}),
    ("rot135", {"w": 0.5, "rotation": 135, "fit": "fill"}),
    ("rot180", {"w": 0.5, "rotation": 180, "fit": "fill"}),
    ("fit_fill", {"w": 0.5, "h": 0.3, "fit": "fill"}),
    ("fit_cover", {"w": 0.5, "h": 0.3, "fit": "cover"}),
    ("fit_contain", {"w": 0.5, "h": 0.3, "fit": "contain"}),
    ("crop", {"w": 0.5, "h": 0.3, "fit": "fill",
              "crop": (0.2, 0.1, 0.2, 0.1)}),
    ("crop_rot30", {"w": 0.5, "h": 0.3, "rotation": 30, "fit": "fill",
                    "crop": (0.2, 0.1, 0.2, 0.1)}),
    ("crop_flip", {"w": 0.5, "h": 0.3, "fit": "fill", "flip_h": True,
                   "crop": (0.1, 0.1, 0.1, 0.1)}),
])
def test_image_box_matches_painted_pixels(name, kw):
    """★ 回归：接口返回的包围盒必须真的框住画出来的内容

    为什么单独立一条：
        「画」和「量」曾经是两套算法（`_draw_image_el` 一套、
        `_image_box_h` 一套），于是旋转/裁剪/cover 任一情况下选择框都可能
        和画面对不上 —— 旋转 90° 时盒子比实际小 28%，
        而接口照样返回 200，只有肉眼看图才发现。

        现在两者共用 `_render_image_piece`，这条测试就是那个不变式的守门员：
        谁要是又把它拆成两套，这里会红。
    """
    from packages.editor import ImageElement, Page, render_page

    pg = Page(number=1, width=1400, height=2000, background="#0a0a0a")
    pg.elements = [ImageElement(id="e", asset_id="a", x=0.15, y=0.2, **kw)]
    img, boxes = render_page(pg, lambda i: _bright_image(), 1000,
                             with_boxes=True)
    bx = boxes.get("e")
    assert bx is not None, f"{name}: 没有返回包围盒"

    painted = _measure_painted(img)
    assert painted is not None, f"{name}: 什么都没画出来"

    err = max(abs(painted[2] - bx[2]), abs(painted[3] - bx[3]))
    assert err < 0.012, (
        f"{name}: 盒子 {bx[2]:.3f}x{bx[3]:.3f} 但画出来的是 "
        f"{painted[2]:.3f}x{painted[3]:.3f}（差 {err:.4f}）")


def test_image_box_uses_same_source_as_drawing():
    """结构保证：画和量必须来自同一个函数，不能各写一套"""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "packages", "editor", "render.py"),
        encoding="utf-8").read()
    assert "_render_image_piece" in src
    # _draw_image_el 里不该再出现自己那一套 resize/rotate
    i = src.index("def _draw_image_el(")
    j = src.index("\ndef ", i + 10)
    body = src[i:j]
    assert "_render_image_piece" in body, "画的时候必须用 _render_image_piece"
    assert ".rotate(" not in body, "旋转逻辑只能有一处"
    assert ".resize(" not in body, "缩放逻辑只能有一处"


def test_image_box_rotation_is_not_guessed():
    """★ 回归：旋转后的包围盒不能再用拍脑袋的系数估

    曾经写成 `base * 1.4`（那个分支注释还写着「给个保守估计」）。
    实际数学是外接矩形：
        W' = |w·cosθ| + |h·sinθ|      H' = |w·sinθ| + |h·cosθ|

    ★ 这里有个容易栽跟头的归一化细节：
      宽度按 page_w 归一、高度按 page_h 归一，两者**尺度不同**。
      所以「转 90° 后新高度 == 旧宽度那个数」是**错的**。
      实测：0° 时 700x394 → 盒子 (0.500, 0.197)；
            90° 时 394x700 → 盒子 (0.281, 0.350)。
      0.350 ≠ 0.500 —— 要换成像素再比才对。
    """
    from packages.editor import ImageElement
    from packages.editor.render import image_box

    img = Image.new("RGB", (1600, 900), (250, 250, 250))
    page_wh = (1400, 2000)
    pw, ph = page_wh
    flat = image_box(ImageElement(id="e", asset_id="a", w=0.5, rotation=0),
                     img, page_wh)
    # 横图：宽 0.5，高 = 0.5 * (1400/2000) * (900/1600) = 0.1969
    assert abs(flat[2] - 0.5) < 1e-6
    assert abs(flat[3] - 0.196875) < 0.002

    turned = image_box(ImageElement(id="e", asset_id="a", w=0.5, rotation=90),
                       img, page_wh)
    # 转 90° → 宽高互换（换成像素再比）
    assert abs(turned[2] * pw - flat[3] * ph) < 2.0, \
        f"转 90° 后宽度(px)应≈原高(px)：{turned[2] * pw:.1f} vs {flat[3] * ph:.1f}"
    assert abs(turned[3] * ph - flat[2] * pw) < 2.0, \
        f"转 90° 后高度(px)应≈原宽(px)：{turned[3] * ph:.1f} vs {flat[2] * pw:.1f}"

    # 更一般地：任何角度都该满足外接矩形公式（不手算期望值，避免又写错）
    import math
    for deg in (15, 30, 45, 60, 135):
        b = image_box(ImageElement(id="e", asset_id="a", w=0.5, rotation=deg),
                      img, page_wh)
        rad = math.radians(deg)
        w0, h0 = flat[2] * pw, flat[3] * ph          # 未旋转时的像素尺寸
        want_w = abs(w0 * math.cos(rad)) + abs(h0 * math.sin(rad))
        want_h = abs(w0 * math.sin(rad)) + abs(h0 * math.cos(rad))
        assert abs(b[2] * pw - want_w) < 3.0, \
            f"{deg}° 宽 {b[2] * pw:.1f} vs 公式 {want_w:.1f}"
        assert abs(b[3] * ph - want_h) < 3.0, \
            f"{deg}° 高 {b[3] * ph:.1f} vs 公式 {want_h:.1f}"

    # 45° 时外接矩形最大，必须比 0° 和 90° 都高（旧公式做不到这点）
    d45 = image_box(ImageElement(id="e", asset_id="a", w=0.5, rotation=45),
                    img, page_wh)
    assert d45[3] > flat[3] and d45[3] > turned[3], \
        "45° 的外接矩形应比 0° 和 90° 都高"

    # 绝不能是「原高度 × 某个常数」那种估法：
    # 真值 0.35，旧公式 0.1969*1.4 = 0.2757
    assert abs(turned[3] - 0.196875 * 1.4) > 0.05, \
        "看着还像老的 *1.4 估法"
