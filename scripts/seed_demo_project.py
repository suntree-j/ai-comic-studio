# -*- coding: utf-8 -*-
"""在服务器上生成一个「示例漫画」编辑器项目

目的：访客点开 `/comic/` 时**立刻看到成品效果**，而不是一个空列表 + 新建弹窗。
同时保留「新建」按钮，用户仍可从零开始。

项目名固定为 `示例漫画`（创建时已存在就跳过，不覆盖用户数据）。

用法：
    python scripts/seed_demo_project.py              # 部署脚本会自动调用
    python scripts/seed_demo_project.py --force      # 覆盖
"""
from __future__ import annotations

import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

NAME = "示例漫画"


def make_panel(w: int, h: int, label: str, base: tuple) -> bytes:
    """造一张「像漫画格」的占位图（纯程序生成，不依赖任何素材）"""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), base)
    d = ImageDraw.Draw(im)
    # 斜线背景
    for i in range(0, w + h, 90):
        d.line([(i, 0), (i - h, h)],
               fill=(base[0] + 45, base[1] + 55, base[2] + 75), width=4)
    # 人物剪影
    cx, cy = w * 0.5, h * 0.56
    d.ellipse([cx - w * 0.16, cy - h * 0.26, cx + w * 0.16, cy + h * 0.26],
              fill=(236, 242, 252))
    d.ellipse([cx - w * 0.075, cy - h * 0.12, cx - w * 0.028, cy - h * 0.06],
              fill=(56, 66, 90))
    d.ellipse([cx + w * 0.028, cy - h * 0.12, cx + w * 0.075, cy - h * 0.06],
              fill=(56, 66, 90))
    d.arc([cx - w * 0.05, cy + h * 0.02, cx + w * 0.05, cy + h * 0.12],
          0, 180, fill=(96, 106, 128), width=5)
    # 围巾
    d.polygon([(cx - w * 0.10, cy - h * 0.13), (cx + w * 0.10, cy - h * 0.13),
               (cx + w * 0.05, cy - h * 0.06), (cx - w * 0.05, cy - h * 0.06)],
              fill=(196, 60, 60))
    d.text((w * 0.04, h * 0.04), label, fill=(255, 255, 255))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="已存在也重建")
    ap.add_argument("--workspace", default=None)
    a = ap.parse_args()

    ws = a.workspace or os.environ.get("COMIC_WORKSPACE") or os.path.join(ROOT, "works")
    os.environ["COMIC_WORKSPACE"] = ws

    from fastapi.testclient import TestClient
    from apps.api.server import app

    c = TestClient(app)
    P = "/api/edit"

    existing = {p["name"] for p in c.get(f"{P}/projects").json()}
    if NAME in existing and not a.force:
        print(f"   示例项目「{NAME}」已存在，跳过")
        return 0
    if NAME in existing:
        c.delete(f"{P}/projects/{NAME}")

    print(f"   创建示例项目「{NAME}」…")
    r = c.post(f"{P}/projects", json={
        "name": NAME, "title": "示例漫画（可直接编辑）",
        "width": 1400, "height": 2000})
    if r.status_code != 200:
        print(f"   ❌ 建项目失败：{r.status_code} {r.text[:200]}")
        return 1
    pid = r.json()["pages"][0]["id"]

    # 三张占位素材
    panels = [
        ("格一.png", make_panel(1600, 1100, "panel 1", (26, 36, 54))),
        ("格二.png", make_panel(1600, 1100, "panel 2", (34, 30, 58))),
        ("格三.png", make_panel(1600, 1100, "panel 3", (24, 44, 60))),
    ]
    d = c.post(f"{P}/projects/{NAME}/assets",
               files=[("files", (n, b, "image/png")) for n, b in panels]).json()
    if not d["added"]:
        print("   ❌ 上传素材失败")
        return 1
    aid = d["added"][0]["id"]
    print(f"   素材 {len(d['added'])} 张")

    # 首页：底图铺满 + 两个气泡 + 标题
    c.post(f"{P}/projects/{NAME}/pages/{pid}/elements",
           json={"kind": "image", "asset_id": aid, "x": 0, "y": 0, "w": 1, "h": 1,
                 "props": {"fit": "cover"}})

    e1 = c.post(f"{P}/projects/{NAME}/pages/{pid}/elements",
                json={"kind": "bubble", "speaker": "穆宁雪",
                      "text": "这是示例气泡。拖动它、改文字、调样式都可以 ——"
                              "高度会跟着文字自动变。",
                      "x": 0.04, "y": 0.05, "w": 0.44}).json()["element"]["id"]
    c.patch(f"{P}/projects/{NAME}/elements/{e1}",
            json={"props": {"tail": [0.5, 0.35]}})

    e2 = c.post(f"{P}/projects/{NAME}/pages/{pid}/elements",
                json={"kind": "bubble", "speaker": "穆飞鸾",
                      "text": "右键这里看属性面板。", "x": 0.58, "y": 0.70,
                      "w": 0.32}).json()["element"]["id"]
    c.patch(f"{P}/projects/{NAME}/elements/{e2}",
            json={"props": {"style": "shout", "tail": [0.36, 0.52]}})

    c.post(f"{P}/projects/{NAME}/pages/{pid}/elements",
           json={"kind": "text", "text": "示例漫画", "x": 0.08, "y": 0.30, "w": 0.7,
                 "props": {"font_size": 120, "outline": 0.008}})

    # 再加两页，说明「页面顺序可调、页码不变」
    for t in ("第 2 页（可拖图进来）", "第 3 页"):
        c.post(f"{P}/projects/{NAME}/pages", json={"title": t})

    st = c.get(f"{P}/projects/{NAME}").json()["stats"]
    print(f"   ✅ {st}")
    print(f"   访客打开 http://<服务器>/comic/?project={NAME} 即可看到效果")
    return 0


if __name__ == "__main__":
    sys.exit(main())
