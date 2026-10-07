# -*- coding: utf-8 -*-
"""生成「真实素材」展示项目 —— 素材直接取自成品漫画 PDF

用途：让工作台与 README 的 Demo 显示**真的漫画画面**，而不是 Mock 占位图。

选页策略（三个约束同时满足）：
    ① **比例匹配**：PDF 里各页宽高比精确对应 PanelSize
       （1.463 = 2048x1400 / 0.750 = 1332x1776 / 1.000 = 1536x1536），
       这样渲染时等比缩放不会变形、也无需裁剪。
    ② **分散取材**：把 168 页分成若干段，每段取一页 ——
       避免几张图都来自同一章，看起来像同一场戏。
    ③ **比例与镜头匹配**：大全景/极远景用宽幅，近景/特写用竖向。

用法：
    python scripts/make_showcase_project.py                    # 自动找桌面上的 PDF
    python scripts/make_showcase_project.py --from-pdf x.pdf
    python scripts/make_showcase_project.py --pages 42,13,10   # 手动指定
"""
import argparse
import io
import json
import os
import shutil
import sys
from typing import Dict, List, Optional, Tuple

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from packages.ir import (                                        # noqa: E402
    Appearance, Bible, BubbleSpec, BubbleStyle, CastMember, Character,
    CharacterState, CharacterVariant, ComicProject, DamageLevel,
    DialogueBook, Layout, LayoutPage, PageType, Panel, PanelSize, Scene,
    ShotSize, Skill, Storyboard, StyleSpec, Utterance, Volume,
)

PROJ = os.path.join(ROOT, "projects", "showcase")

PDF_CANDIDATES = [
    r"C:\Users\jsy28\Desktop\全职法师_最终版.pdf",
]

#: PanelSize → 目标宽高比
TARGET_RATIO = {
    PanelSize.WIDE: 2048 / 1400,
    PanelSize.TALL: 1332 / 1776,
    PanelSize.SQUARE: 1536 / 1536,
}

# ══════════════════════════════════════════════════════════════════
# 设定（与 demo 一致，但风格块写得更完整）
# ══════════════════════════════════════════════════════════════════

STYLE = StyleSpec(shared_block=(
    "全彩国漫风格的商业漫画页，柔和通透的画面。\n"
    "【线条】线条很细，颜色为深蓝紫或深褐（不是纯黑），对比柔和；"
    "外轮廓只比内部线略粗一点；线条干净少交叉。\n"
    "【上色】柔和渐变上色（喷枪式过渡），每块颜色2到3层柔和过渡、阴影边缘不硬；"
    "空气感与柔光；对比偏低、偏亮通透；中等偏高的清亮饱和度。\n"
    "【环境光】冷色调环境光渗进皮肤、衣料与空气。\n"
    "【比例】约七头身；肩线清晰、骨架写实；不拉长腿、不细腰、不纸片人。\n"
    "【战斗形态】战斗中人物处于战斗形态（翼展开／元素凝聚／衣袍头发被气场掀起）。"))

CHARACTERS = {
    "mu_ningxue": Character(
        id="mu_ningxue", name="穆宁雪", aliases=["宁雪", "雪雪"],
        appearance=Appearance(
            text=("一位二十出头的年轻女子。冷调雪银白色的长发（明度极高、接近雪白）；"
                  "额前有整齐的刘海；其余长发盘成低髻，两侧各留一缕长垂发垂至腰际。"
                  "长脸形、下巴尖而收；淡冰蓝色瞳孔（单一眸色、无纹样）；神情冷淡平静。"
                  "服装：纯白色的立领修身外套（立领、收腰、双排暗扣、下摆及膝开衩）；"
                  "内层白色立领紧身上衣＋白色短裙；颈间一条正红色的长围巾，尾端向后飘扬；"
                  "双手戴及肘的黑色长手套。★ 全篇只有这一套。"),
            forbidden=["蓝色系服装", "露出额头", "瞳孔纹样"]),
        variants={"battle": CharacterVariant(
            name="战斗形态",
            override="衣袍与长发被气场掀起，可加冰系能量丝与冰翼；服装同上。")},
        state=CharacterState(emotion="冷淡无波")),
    "mu_feiluan": Character(
        id="mu_feiluan", name="穆飞鸾",
        appearance=Appearance(
            text=("一位二十多岁的年轻男子。银白色长发（被气场掀起）；面容英挺、剑眉、"
                  "狭长眼型、高鼻梁；浅色瞳孔（单一眸色、无纹样）。"
                  "服装：浅蓝色的长毛领大衣＋内搭黑色高领＋黑色长靴。"
                  "★ 全篇只有这一套：不要换成长袍、不要换色。"),
            forbidden=["换色", "长袍"]),
        variants={"ice_wing": CharacterVariant(
            name="冰鸾甲翼",
            override=("背后浮现冰鸾甲翼——不是羽毛，而是由暗蓝冰铁锻造的厚甲翼片，"
                      "翼骨如支架、翼面由层层铆合的冰板拼成，精密机械感、坚厚雄壮。"))},
        state=CharacterState(emotion="倨傲")),
    "mu_yinfeng": Character(
        id="mu_yinfeng", name="穆隐凤",
        appearance=Appearance(
            text=("一位三十五六岁的女性。银白色长发；眉形细长上扬、眼型狭长；"
                  "浅紫色瞳孔（单一眸色、无纹样）；唇色深红；神情常带不屑或冷厉。"
                  "服装：白色华贵长袍（厚重缎面、有暗纹），内衬为紫色，"
                  "颈肩处有厚实蓬松的狐毛领；腰间束细金链腰带。"),
            forbidden=["深紫色长袍"]),
        state=CharacterState(emotion="阴狠")),
    "mo_fan": Character(
        id="mo_fan", name="莫凡",
        appearance=Appearance(
            text=("一位十几岁到二十出头的年轻男子。红色短碎发（发梢凌乱；必须是红色，"
                  "绝对不要黑发、不要棕发）；蓝绿色瞳孔（单一眸色、无纹样）；"
                  "少年感的脸型、下颌线条清晰。服装：深蓝色的连帽卫衣，拉链敞开；"
                  "内层瓷白衬衫，胸前扣子解开好几粒；下身黑色长裤＋黑色运动鞋。"
                  "★ 卫衣必须敞开、能让白衬衫露出来。"),
            forbidden=["黑发", "棕发", "只穿卫衣没有白衬衫"]),
        variants={"fire_king": CharacterVariant(
            name="火阎王形态",
            override="头发化为烈焰状金橙、背后浮现炎姬女王火魂影；服装仍是卫衣＋白衬衫。")},
        state=CharacterState(emotion="痞笑")),
}

bible = Bible(
    characters=CHARACTERS,
    scenes={
        "ice_corridor": Scene(id="ice_corridor", name="冰玻璃长道",
                              text="雪竹林间的冰玻璃长道，路面如冰面倒映天光，尽头是穆氏城楼。"),
        "cliff": Scene(id="cliff", name="断崖与主楼",
                       text="巨大的断崖壁面布满蛛网状裂痕，崖下是穆氏主楼与成群的穆氏子弟。"),
    },
    skills={
        "ice_bow": Skill(id="ice_bow", name="冰晶刹弓", element="绝冰", color=[165, 228, 252]),
        "ice_phoenix_wing": Skill(id="ice_phoenix_wing", name="冰凤银翅",
                                  element="冰凤", color=[180, 215, 235]),
        "fire_spear": Skill(id="fire_spear", name="溶浆拳河", element="火系",
                            color=[232, 108, 56]),
        "ice_luan": Skill(id="ice_luan", name="冰鸾甲翼", element="冰鸾",
                          color=[150, 205, 240]),
    },
    style=STYLE,
)


def P(pid, seq, size, shot, cast, action, **kw):
    return Panel(id=pid, seq=seq, size=size, shot=shot,
                 cast=[CastMember(**c) for c in cast], action=action, **kw)


def build_storyboards() -> List[Storyboard]:
    """分镜定义

    ★ 素材与尺寸的对应（真实素材比例固定，所以按素材反推尺寸）：

         页   比例    尺寸     气泡检测   内容
         66   1.463   WIDE     1 处小字  穆宁雪立于雪峰，箭意横贯长空
         42   1.463   WIDE     1 处小字  穆氏主楼全景（空镜）
         58   1.463   WIDE     1 处小字  冰凤银翅铺满天空
         65   0.750   TALL     0 个      人物特写
         59   0.750   TALL     0 个      羽翼展开
         30   0.744   TALL     0 个      风雪中的人物
         55   0.750   TALL     0 个      人物
         16   0.721   TALL     0 个      人物

      全书 168 页里**只有这些页没有烧进成品对白**（其余都是带对白的成稿），
      所以选页范围很窄 —— 这是"用成品 PDF 当素材"的固有限制，不是选页算法的问题。
    """
    sb1 = Storyboard(chapter="2436", panels=[
        P("ch2436_P001", 1, PanelSize.WIDE, ShotSize.EXTREME_WIDE,
          [{"id": "mu_ningxue", "pos": "far_left", "variant": "battle"}],
          "极远景：穆宁雪独身立于雪峰之巅，冰晶刹弓的箭意横贯长空，远处山体崩裂",
          background="cliff", mood="肃杀，天光惨白",
          damage_level=DamageLevel.L0,
          emotion_hint={"mu_ningxue": "冷淡无波"},
          source_span=[3, 6]),
        P("ch2436_P002", 2, PanelSize.TALL, ShotSize.CLOSE,
          [{"id": "mu_ningxue", "pos": "center"}],
          "穆宁雪冷眸直视前方，霜气自周身散开，围巾被风掀起",
          background="cliff", damage_level=DamageLevel.L0,
          emotion_hint={"mu_ningxue": "锁定目标"}, source_span=[6, 6]),
        P("ch2436_P003", 3, PanelSize.WIDE, ShotSize.EXTREME_WIDE,
          [], "极远景空镜：穆氏主楼矗立于风雪之中，殿前长阶空无一人",
          background="cliff", mood="天地为之屏息", damage_level=DamageLevel.L0,
          source_span=[7, 9]),
    ])
    sb2 = Storyboard(chapter="2437", panels=[
        P("ch2437_P001", 1, PanelSize.TALL, ShotSize.MEDIUM,
          [{"id": "mu_ningxue", "pos": "center"}],
          "穆宁雪立于风雪之中，衣袍与长发被气流掀起",
          background="ice_corridor", damage_level=DamageLevel.L1,
          emotion_hint={"mu_ningxue": "冷厉"},
          source_span=[20, 24]),
        P("ch2437_P002", 2, PanelSize.TALL, ShotSize.CLOSE,
          [{"id": "mu_yinfeng", "pos": "center"}],
          "穆隐凤背后展开多重冰凤银翅，层叠修长的幽蓝冰晶羽翼边缘泛着银光",
          skill="ice_phoenix_wing", background="cliff", damage_level=DamageLevel.L2,
          emotion_hint={"mu_yinfeng": "杀意凛然"},
          source_span=[30, 36]),
        P("ch2437_P003", 3, PanelSize.TALL, ShotSize.MEDIUM,
          [{"id": "mu_ningxue", "pos": "center"}],
          "冰墙拔地而起，穆宁雪立于冰墙之前，细雪在身侧横飞",
          background="ice_corridor", damage_level=DamageLevel.L2,
          emotion_hint={"mu_ningxue": "专注"}, source_span=[38, 40]),
    ])
    sb3 = Storyboard(chapter="2438", panels=[
        P("ch2438_P001", 1, PanelSize.TALL, ShotSize.MEDIUM,
          [{"id": "mu_ningxue", "pos": "center"}],
          "穆宁雪抬手凝霜，雪花在掌心结成薄刃",
          background="ice_corridor", damage_level=DamageLevel.L2,
          emotion_hint={"mu_ningxue": "冷静专注"},
          source_span=[50, 53]),
        P("ch2438_P002", 2, PanelSize.WIDE, ShotSize.WIDE,
          [{"id": "mu_yinfeng", "pos": "right"}],
          "穆隐凤振翼立于高空，冰凤银翅铺满整个天幕",
          skill="ice_phoenix_wing", background="cliff",
          damage_level=DamageLevel.L3, mood="毁天灭地",
          emotion_hint={"mu_yinfeng": "睥睨"}, source_span=[60, 64]),
    ])
    return [sb1, sb2, sb3]


def erase_baked_text(path: str, max_ratio: float = 0.05) -> Tuple[int, float]:
    """擦掉页面上已烧进画面的文字（页码、拟声词、标题字）

    ★ 为什么做这个：
      成品页角落常有页码与标题字，和叠加的气泡放在一起会显得脏。
      对**小块文字**擦除效果很好（实测干净），
      但大段对白擦完会留色块 —— 所以超过 max_ratio 就**不擦**，保持原样。

    返回 (擦除个数, 占画面比例)
    """
    try:
        from scripts.clean_panels import analyze, erase
    except ImportError:
        return 0, 0.0
    try:
        info = analyze(path)
    except Exception:                                       # noqa: BLE001
        return 0, 0.0
    boxes, ratio = info["boxes"], info["ratio"]
    if not boxes or ratio > max_ratio:
        return len(boxes), ratio
    import numpy as np
    from PIL import Image as _Image
    img = np.asarray(_Image.open(path).convert("RGB"))
    _Image.fromarray(erase(img, boxes)).save(path)
    return len(boxes), ratio


def build_dialogue() -> DialogueBook:
    def U(uid, who, text, span, style=BubbleStyle.SPEECH):
        return Utterance(id=uid, who=who, text=text, source_span=span,
                         confirmed=True, bubble=BubbleSpec(style=style))
    return DialogueBook(items={
        "ch2436_P001": [
            U("b1", "mu_ningxue", "你喜欢这柄冰晶刹弓，作为多年的朋友，我自然亲手赠你。", [4, 4]),
            U("b2", "mu_feiluan", "解决掉他们。", [7, 7]),
        ],
        "ch2436_P003": [
            U("b3", "mu_ningxue", "今天你休想活着离开穆庞山！", [8, 8], BubbleStyle.SHOUT),
        ],
        "ch2437_P001": [
            U("b4", "mo_fan", "穆飞鸾，你脑子是有问题吗，来你们穆氏，我当然是打上来的。", [22, 24]),
        ],
        "ch2437_P002": [
            U("b5", "mo_fan", "有老子在的地方，一定得是红色！", [33, 33], BubbleStyle.SHOUT),
        ],
        "ch2438_P001": [
            U("b6", "mu_ningxue", "你为我护航，小心穆隐凤的凤吟。", [51, 51]),
            U("b7", "mo_fan", "我命硬，别分心了，好好蓄下一箭。", [52, 52]),
        ],
        "ch2438_P002": [
            U("b8", "mo_fan", "溶浆拳河！", [61, 61], BubbleStyle.SHOUT),
        ],
    })


# ══════════════════════════════════════════════════════════════════
# 从 PDF 选页
# ══════════════════════════════════════════════════════════════════

def scan_pdf(path: str) -> List[Tuple[int, float]]:
    import fitz
    d = fitz.open(path)
    out = [(i, p.rect.width / p.rect.height) for i, p in enumerate(d, 1)]
    d.close()
    return out


def text_density(path: str, dpi: int = 60) -> Dict[int, float]:
    """估算每页「已烧进画面的文字」密度

    ★ 为什么需要：
      成品漫画页**本来就有对白气泡与拟声词**。直接拿来当素材再叠一层气泡，
      会变成「文字叠文字」，一眼假。
      所以选素材时要避开文字密集的页 —— 只挑干净的画面。

    启发式：成品对白多为**纯白填充 + 深色描边**。
      近纯白占比 wr、近纯黑占比 br，密度 ≈ 3*wr + 5*br。
      实测：干净画面 < 0.10，含对白的页普遍 > 0.25。
    """
    import fitz
    import numpy as np
    from PIL import Image
    d = fitz.open(path)
    out: Dict[int, float] = {}
    for i, pg in enumerate(d, 1):
        pix = pg.get_pixmap(dpi=dpi)
        a = np.asarray(Image.frombytes("RGB", (pix.width, pix.height),
                                       pix.samples)).astype(np.int16)
        white = ((a[:, :, 0] > 242) & (a[:, :, 1] > 242) & (a[:, :, 2] > 242))
        black = ((a[:, :, 0] < 40) & (a[:, :, 1] < 40) & (a[:, :, 2] < 40))
        out[i] = float(white.mean()) * 3 + float(black.mean()) * 5
    d.close()
    return out


def pick_pages(pages: List[Tuple[int, float]], sizes: List[PanelSize],
               spread: bool = True,
               density: Optional[Dict[int, float]] = None,
               max_density: float = 0.16) -> List[int]:
    """为每个 Panel 挑一页

    策略（优先级从高到低）：
      ① **只从干净页里选** —— 画面没有已烧进去的对白，叠气泡才不会文字叠文字
      ② **比例匹配** —— 等比缩放不变形、不裁剪
      ③ **分散取材** —— 干净页够多时分成若干段，避免都来自同一章
      ④ 干净页不够用 → 才放宽到全库（并在报告里标出来）
    """
    ratio_of = {p: r for p, r in pages}
    all_pages = [p for p, _ in pages]

    clean: List[int] = []
    if density:
        clean = [p for p in all_pages if density.get(p, 1.0) <= max_density]
    # 干净页太少（<面板数）就不设限，免得素材全挤在几页上
    pool = clean if len(clean) >= len(sizes) else all_pages

    def d_of(p: int) -> float:
        return density.get(p, 0.0) if density else 0.0

    def rank(cands: List[int], target: float) -> List[int]:
        return sorted(cands, key=lambda p: (abs(ratio_of[p] - target), d_of(p)))

    chosen: List[int] = []
    used: set = set()

    if spread and len(pool) >= len(sizes) * 2:
        import math
        pool_sorted = sorted(pool)
        seg = max(1, math.ceil(len(pool_sorted) / len(sizes)))
        for i, size in enumerate(sizes):
            lo, hi = i * seg, min((i + 1) * seg, len(pool_sorted))
            cands = [p for p in pool_sorted[lo:hi] if p not in used] or \
                    [p for p in pool_sorted if p not in used]
            if cands:
                p = rank(cands, TARGET_RATIO[size])[0]
                chosen.append(p)
                used.add(p)
    if len(chosen) < len(sizes):
        chosen = []
        used = set()
        for size in sizes:
            cands = [p for p in pool if p not in used]
            if not cands:
                break
            p = rank(cands, TARGET_RATIO[size])[0]
            chosen.append(p)
            used.add(p)

    # 仍不够 → 放宽到全库
    while len(chosen) < len(sizes):
        cands = [p for p in all_pages if p not in used]
        if not cands:
            break
        p = rank(cands, TARGET_RATIO[sizes[len(chosen)]])[0]
        chosen.append(p)
        used.add(p)
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-pdf")
    ap.add_argument("--pages", help="手动指定页码，如 66,63,42,60,65,53,34,58")
    ap.add_argument("--dpi", type=int, default=200, help="抽帧分辨率（默认 200）")
    ap.add_argument("--max-density", type=float, default=0.10,
                    help="可接受的「已烧进画面的文字」密度上限（默认 0.10）")
    ap.add_argument("--no-clean-check", action="store_true",
                    help="跳过净度检查（只按比例选页）")
    ap.add_argument("--no-erase", action="store_true",
                    help="不擦除页面上残留的小块文字")
    a = ap.parse_args()

    # 实测推荐的一组（逐页验证过：气泡检测 + 画面目视）
    #   顺序对应 build_storyboards() 的 8 个镜头
    #   WIDE 1.463 -> 66 / 42 / 58      TALL 0.750 -> 65 / 59 / 30 / 55 / 16
    DEFAULT_CLEAN_PAGES = [66, 65, 42, 59, 30, 55, 16, 58]

    pdf = a.from_pdf
    if not pdf:
        pdf = next((p for p in PDF_CANDIDATES if os.path.exists(p)), None)
    if not pdf or not os.path.exists(pdf):
        print("  ❌ 找不到参考 PDF")
        print("     用 --from-pdf <路径> 指定，或用 scripts/make_demo_project.py 生成 Mock 素材")
        return 1

    storyboards = build_storyboards()
    dialogue = build_dialogue()
    panels = [p for sb in storyboards for p in sb.panels]

    # ── 落盘 IR ──
    if os.path.isdir(PROJ):
        shutil.rmtree(PROJ)
    os.makedirs(os.path.join(PROJ, "assets", "panels"), exist_ok=True)

    open(os.path.join(PROJ, "bible.json"), "w", encoding="utf-8").write(
        bible.model_dump_json(indent=1))
    open(os.path.join(PROJ, "storyboard.json"), "w", encoding="utf-8").write(
        json.dumps([s.model_dump(mode="json") for s in storyboards],
                   ensure_ascii=False, indent=1))
    open(os.path.join(PROJ, "dialogue.json"), "w", encoding="utf-8").write(
        dialogue.model_dump_json(indent=1))

    pages = [
        LayoutPage(page=1, type=PageType.TITLE, title="AI Comic Studio",
                   sub="展示项目 · 真实素材 · 3 章 / 8 格", volume="v1"),
    ]
    for i, p in enumerate(panels, start=2):
        pages.append(LayoutPage(page=i, type=PageType.PANELS, panels=[p.id]))
    layout = Layout(total=len(pages), pages=pages,
                    volumes=[Volume(id="v1", title="展示卷", sub="2436–2438 章")])
    open(os.path.join(PROJ, "layout.json"), "w", encoding="utf-8").write(
        layout.model_dump_json(indent=1))

    # ── 选页 ──
    print(f"  素材来源：{os.path.basename(pdf)}")
    dens: Optional[Dict[int, float]] = None
    if a.pages is not None and a.pages.strip():
        picks = [int(x) for x in a.pages.split(",")]
        if len(picks) < len(panels):
            picks += [picks[-1]] * (len(panels) - len(picks))
        picks = picks[:len(panels)]
        print("   手动指定页码")
        dens = text_density(pdf) if not a.no_clean_check else None
    elif a.pages is not None:
        # 显式传了空串 → 强制自动选页
        scanned = scan_pdf(pdf)
        dens = None if a.no_clean_check else text_density(pdf)
        picks = pick_pages(scanned, [p.size for p in panels], spread=True,
                           density=dens, max_density=a.max_density)
        print(f"   自动选页（{len(scanned)} 页可选）")
    else:
        # 默认：用实测挑好的干净页（画面里没有成品对白）
        scanned = scan_pdf(pdf)
        dens = None if a.no_clean_check else text_density(pdf)
        picks = list(DEFAULT_CLEAN_PAGES)
        if len(picks) < len(panels):
            picks += pick_pages(scanned, [p.size for p in panels],
                                spread=True, density=dens,
                                max_density=a.max_density)
        picks = picks[:len(panels)]
        print("   使用实测推荐的干净页（可用 --pages '' 改为自动选页）")

    # ── 抽帧 ──
    import fitz
    d = fitz.open(pdf)
    print()
    print(f"  {'镜头':<14}{'尺寸':<12}{'抽取页':<8}{'源比例':<10}{'目标比例':<10}"
          f"{'比例偏差':<10}{'文字密度'}")
    print("  " + "-" * 74)
    total = 0
    for panel, pno in zip(panels, picks):
        pno = max(1, min(pno, len(d)))
        pg = d[pno - 1]
        ratio = pg.rect.width / pg.rect.height
        target = TARGET_RATIO[panel.size]
        pix = pg.get_pixmap(dpi=a.dpi)
        out = os.path.join(PROJ, "assets", "panels", panel.id + ".png")
        pix.save(out)
        total += os.path.getsize(out)
        dv = dens.get(pno) if dens else None
        mark = "OK" if abs(ratio - target) < 0.06 else "~"
        dstr = f"{dv:.3f}" + (" 干净" if dv is not None and dv <= a.max_density
                              else " 含文字" if dv is not None else "")
        # 擦掉页面上残留的小块文字（页码/拟物词/标题字）
        n_er, er_ratio = (0, 0.0) if a.no_erase else erase_baked_text(out)
        estr = f"  擦除 {n_er} 处({er_ratio*100:.2f}%)" if n_er else ""
        print(f"  {panel.id:<14}{panel.size.value:<12}第 {pno:>3} 页  "
              f"{ratio:<10.3f}{target:<10.3f}{abs(ratio-target):<10.3f}"
              f"{dstr}  {mark}{estr}")
    d.close()

    # ── 原文（让 IR-007 可通过）──
    need = 1
    for utts in dialogue.items.values():
        for u in utts:
            need = max(need, (u.source_span or [1, 1])[1])
    lines = [""] * need
    for utts in dialogue.items.values():
        for u in utts:
            x, y = u.source_span or [1, 1]
            lines[x - 1] = u.text if u.text.startswith("「") else f"「{u.text}」"
            for i in range(x, y):
                if i < len(lines) and not lines[i]:
                    lines[i] = "（战斗持续。）"
    for i, ln in enumerate(lines):
        if not ln:
            lines[i] = "雪落在冰玻璃长道上。" if i % 3 == 0 else "风把细雪吹得横飞。"
    open(os.path.join(PROJ, "source.txt"), "w", encoding="utf-8").write("\n".join(lines))

    print()
    print(f"  ✅ 展示项目已生成：projects/showcase/")
    print(f"     角色 {len(bible.characters)} ｜ 章节 {len(storyboards)} ｜ "
          f"镜头 {len(panels)} ｜ 对白 {sum(len(v) for v in dialogue.items.values())} ｜ "
          f"页数 {layout.total}")
    print(f"     素材 {len(panels)} 张 · {total/1048576:.1f} MB（{a.dpi} dpi）")
    print()
    print("  启动：python -m apps.api.server --port 8000")
    print("       http://127.0.0.1:8000/?project=showcase&page=2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
