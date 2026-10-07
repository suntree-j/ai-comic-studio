# -*- coding: utf-8 -*-
"""生成 README 用的演示物料

产物 → docs/images/
    workbench.png    工作台主界面
    page.png         示例页
    edit_demo.png    「改一句话 → 重渲染」的前后对比（核心价值演示）
    rules.png        12 条规则校验结果

用法：
    python scripts/make_graphics.py
"""
import io, os, shutil, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(ROOT, "docs", "images")
PROJ = os.path.join(ROOT, "projects", "showcase")

FONT_PATHS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]


def font(sz):
    for p in FONT_PATHS:
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()


def load_project():
    from packages.ir import Bible, ComicProject, DialogueBook, Layout, Storyboard
    import json
    bible = Bible.model_validate_json(
        open(os.path.join(PROJ, "bible.json"), encoding="utf-8").read())
    boards = [Storyboard.model_validate(x) for x in json.load(
        open(os.path.join(PROJ, "storyboard.json"), encoding="utf-8"))]
    dlg = DialogueBook.model_validate_json(
        open(os.path.join(PROJ, "dialogue.json"), encoding="utf-8").read())
    lay = Layout.model_validate_json(
        open(os.path.join(PROJ, "layout.json"), encoding="utf-8").read())
    return ComicProject(name="showcase", bible=bible, storyboards=boards,
                        dialogue=dlg, layout=lay)


def render_page_image(project, page_no, transcript=None, panel_w=760,
                      force_pos=None):
    """渲染一页；可临时替换某条对白的文字与位置"""
    import copy
    from packages.render import RenderContext, render_page
    p = copy.deepcopy(project)
    if transcript:
        for pid, new in transcript.items():
            for u in p.dialogue.items.get(pid, []):
                if u.id in new:
                    if "text" in new[u.id]:
                        u.text = new[u.id]["text"]
                    if "box" in new[u.id]:
                        from packages.ir import Box
                        u.bubble.box = Box(**new[u.id]["box"])
                    if "tail" in new[u.id]:
                        from packages.ir import Box
                        u.bubble.tail = Box(**new[u.id]["tail"])

    ctx = RenderContext(
        bible=p.bible,
        panels={x.id: x for sb in p.storyboards for x in sb.panels},
        utterances=p.dialogue.items, panel_width=panel_w,
        base_font=max(14, int(panel_w * 0.026)))
    lp = next(x for x in p.layout.pages if x.page == page_no)
    imgs = {}
    for pid in lp.panels:
        fp = os.path.join(PROJ, "assets", "panels", pid + ".png")
        if os.path.exists(fp):
            imgs[pid] = Image.open(fp).convert("RGB")
    return render_page(ctx, lp, imgs)


def labelled(im: Image.Image, title: str, sub: str = "", pad=44) -> Image.Image:
    """加一个标题条"""
    W, H = im.size
    cv = Image.new("RGB", (W, H + pad + 46), (22, 26, 34))
    dr = ImageDraw.Draw(cv)
    cv.paste(im, (0, pad + 46))
    dr.text((16, 12), title, font=font(24), fill=(232, 236, 244))
    if sub:
        dr.text((16, pad + 16), sub, font=font(17), fill=(150, 162, 180))
    dr.rectangle([0, pad + 44, W, pad + 46], fill=(74, 158, 255))
    return cv


def arrow_between(cv, x0, y0, x1, y1, color=(245, 166, 35)):
    dr = ImageDraw.Draw(cv)
    dr.line([(x0, y0), (x1, y1)], fill=color, width=5)
    import math
    a = math.atan2(y1 - y0, x1 - x0)
    L, S = 20, 0.42
    dr.polygon([(x1, y1),
                (x1 - L * math.cos(a - S), y1 - L * math.sin(a - S)),
                (x1 - L * math.cos(a + S), y1 - L * math.sin(a + S))], fill=color)


def main():
    os.makedirs(OUT, exist_ok=True)
    project = load_project()

    # ── 核心演示：改一句话 + 挪气泡 → 重渲染 ──
    print("  渲染 before / after …")
    before = render_page_image(project, 2, panel_w=700)
    after = render_page_image(
        project, 2,
        transcript={"ch2436_P001": {
            "b1": {"text": "你喜欢这柄冰晶刹弓，作为多年的朋友，我自然亲手赠你。",
                   "box": {"x": 0.55, "y": 0.62}, "tail": {"x": 0.30, "y": 0.52}},
            "b2": {"text": "解决掉他们。",
                   "box": {"x": 0.03, "y": 0.72}, "tail": {"x": 0.62, "y": 0.55}},
        }},
        panel_w=700)
    print("  渲染 before / after … 完成")

    H = max(before.height, after.height)
    pad = 26
    gap = 130
    W = before.width + after.width + pad * 3 + gap
    cv = Image.new("RGB", (W, H + pad * 2 + 90), (22, 26, 34))
    dr = ImageDraw.Draw(cv)
    dr.text((pad, 18), "改一句话 + 挪气泡 → 重渲染（约 3 秒）",
            font=font(30), fill=(232, 236, 244))
    dr.text((pad, 58),
            "IR 与画面分离：对白只存在 JSON 里，改完重渲染单页即可，不需要重新出图",
            font=font(18), fill=(150, 162, 180))

    y = 90 + pad
    cv.paste(before, (pad, y))
    cv.paste(after, (pad * 2 + before.width + gap, y))

    dr.text((pad + 10, y - 30), "改 前", font=font(22), fill=(150, 162, 180))
    dr.text((pad * 2 + before.width + gap + 10, y - 30), "改 后",
            font=font(22), fill=(74, 158, 255))

    # 中间箭头
    ax0 = pad + before.width + 18
    ax1 = pad * 2 + before.width + gap - 18
    arrow_between(cv, ax0, y + H // 2, ax1, y + H // 2)
    label = "PATCH /dialogue/.../b1"
    tw = dr.textlength(label, font=font(18))
    dr.text(((ax0 + ax1) / 2 - tw / 2, y + H // 2 - 46), label,
            font=font(18), fill=(245, 166, 35))

    p = os.path.join(OUT, "edit_demo.png")
    cv.save(p, "PNG", optimize=True)
    print(f"  ✅ edit_demo.png  {cv.size[0]}x{cv.size[1]}  {os.path.getsize(p)/1024:.0f} KB")

    # ── 页面单图 ──
    p2 = os.path.join(OUT, "page.png")
    after.save(p2, "PNG", optimize=True)
    print(f"  ✅ page.png  {after.size[0]}x{after.size[1]}  {os.path.getsize(p2)/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
