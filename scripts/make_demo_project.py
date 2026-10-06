# -*- coding: utf-8 -*-
"""生成一个可直接在工作台打开的示例项目（projects/demo/）

包含完整的四个 IR 文件 + 用 Mock 生图服务生成的单格素材，
启动工作台后可以直接：浏览 → 拖气泡 → 改说话人 → 一键重渲染 → 导出 PDF

用法：
    python scripts/make_demo_project.py
    python -m apps.api.server --port 8000
    # 打开 http://127.0.0.1:8000
"""
import io, json, os, shutil, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from packages.ir import (
    Appearance, Bible, Box, BubbleSpec, BubbleStyle, CastMember, Character,
    CharacterState, CharacterVariant, ComicProject, DamageLevel, DialogueBook,
    Layout, LayoutPage, PageType, Panel, PanelSize, Scene, ShotSize, Skill,
    Storyboard, StyleSpec, Utterance, Volume,
)
from packages.render import MockImageProvider, RenderStudio

PROJ = os.path.join(ROOT, 'projects', 'demo')

# ══════════════════════════════════════════════════════════════════
# Bible
# ══════════════════════════════════════════════════════════════════

STYLE = StyleSpec(
    shared_block=(
        "全彩国漫风格的商业漫画页，柔和通透的画面。\n"
        "【线条】线条很细，颜色为深蓝紫或深褐（不是纯黑），对比柔和；"
        "外轮廓只比内部线略粗一点；线条干净少交叉。\n"
        "【上色】柔和渐变上色（喷枪式过渡），每块颜色2到3层柔和过渡、阴影边缘不硬；"
        "空气感与柔光；对比偏低、偏亮通透；中等偏高的清亮饱和度。\n"
        "【环境光】冷色调环境光渗进皮肤、衣料与空气。\n"
        "【比例】约七头身；肩线清晰、骨架写实；不拉长腿、不细腰、不纸片人；"
        "脸型偏长、下巴尖而收。\n"
        "【眼睛】眼睛大而长；上睫毛粗黑明显、下睫毛也画出；虹膜上部大片白色高光。\n"
        "【战斗形态】战斗中人物处于战斗形态（翼展开／元素凝聚／能量线缠绕／"
        "衣袍头发被气场掀起）。"
    ),
)

MU_NINGXUE = Character(
    id="mu_ningxue", name="穆宁雪", aliases=["宁雪", "雪雪"],
    appearance=Appearance(
        text=("一位二十出头的年轻女子。冷调雪银白色的长发（明度高、接近雪白，"
              "带极淡冷灰调；绝对不要淡紫色）；额前有整齐的刘海；其余长发盘成"
              "端庄的低髻（在脑后偏下），两侧各留一缕长垂发垂至腰际。长脸形，"
              "下巴尖而收；眼睛大而长、微微上挑；淡冰蓝色瞳孔（单一眸色、虹膜干净，"
              "绝对不要任何纹样）。神情冷淡平静、下巴微抬。服装：纯白色的立领修身"
              "外套——立领、收腰、双排暗扣，下摆及膝并略向外扩、正面开衩；内层白色"
              "立领紧身上衣，下配白色短裙；颈间一条正红色的长围巾，绕颈一圈后尾端"
              "长长地向后飘扬；双手戴及肘的黑色长手套。身形约七头身；肩线清晰。"
              "★ 全篇只有这一套：绝对不要蓝色系服装、不要露出额头。"),
        forbidden=["蓝色系服装", "露出额头", "瞳孔纹样"],
    ),
    variants={"battle": CharacterVariant(
        name="战斗形态",
        override="衣袍与长发被气场掀起，可加冰系能量丝与冰翼；服装同上。")},
    state=CharacterState(emotion="冷淡无波"),
)

MU_FEILUAN = Character(
    id="mu_feiluan", name="穆飞鸾",
    appearance=Appearance(
        text=("一位二十多岁的年轻男子。银白色长发（被气场掀起）；面容英挺、剑眉、"
              "狭长眼型、高鼻梁；浅色瞳孔（单一眸色、无纹样）。"
              "服装：浅蓝色的长毛领大衣（毛领厚实蓬松）＋内搭黑色高领＋黑色长靴。"
              "★ 全篇只有这一套：不要换成长袍、不要换色。"),
        forbidden=["换色", "长袍"],
    ),
    variants={"ice_wing": CharacterVariant(
        name="冰鸾甲翼",
        override=("背后浮现冰鸾甲翼——不是羽毛，而是由暗蓝冰铁锻造的厚甲翼片，"
                  "翼骨如支架、翼面由层层铆合的冰板拼成，精密机械感、坚厚雄壮。"))},
    state=CharacterState(emotion="倨傲"),
)

MU_YINFENG = Character(
    id="mu_yinfeng", name="穆隐凤",
    appearance=Appearance(
        text=("一位三十五六岁的女性。银白色长发（有柔和光泽与块状高光）；面容保养"
              "得极好、眉形细长上扬、眼型狭长；浅紫色瞳孔（单一眸色、无纹样）；"
              "唇色深红、妆容精致；神情常带不屑或冷厉。服装：白色华贵长袍（厚重缎面、"
              "有暗纹），内衬为紫色（领缘与袖口露出紫边），颈肩处有厚实蓬松的狐毛领；"
              "腰间束细金链腰带。"),
        forbidden=["深紫色长袍"],
    ),
    state=CharacterState(emotion="阴狠"),
)

MO_FAN = Character(
    id="mo_fan", name="莫凡",
    appearance=Appearance(
        text=("一位十几岁到二十出头的年轻男子。红色短碎发（发梢凌乱、略显张扬；"
              "必须是红色，绝对不要黑发、不要棕发）；蓝绿色瞳孔（正常单一眸色、"
              "虹膜干净、绝对不要任何纹样）；少年感的脸型、下颌线条清晰；"
              "嘴角常带一点痞气的笑意。身形约七头身、肩宽自然。"
              "服装（唯一一套，全篇不变）：外层是一件深蓝色的连帽卫衣，拉链敞开、"
              "不拉起；内层是一件瓷白色的衬衫——面料挺括、有丝质光泽，胸前扣子"
              "解开好几粒、领口敞开露出颈线；下身黑色长裤＋黑色运动鞋。"
              "★ 要点：卫衣必须敞开、能让白衬衫露出来；"
              "★ 绝对不要画成只穿卫衣没有白衬衫、不要画成只穿白衬衫没有卫衣、不要黑发。"),
        forbidden=["黑发", "棕发", "只穿卫衣没有白衬衫"],
    ),
    variants={"fire_king": CharacterVariant(
        name="火阎王形态",
        override=("头发化为烈焰状金橙、背后浮现有炎姬女王火魂影、气势暴涨；"
                  "服装仍是深蓝卫衣＋瓷白衬衫。"))},
    state=CharacterState(emotion="痞笑"),
)

bible = Bible(
    characters={c.id: c for c in (MU_NINGXUE, MU_FEILUAN, MU_YINFENG, MO_FAN)},
    scenes={
        "ice_corridor": Scene(id="ice_corridor", name="冰玻璃长道",
                              text="雪竹林间的冰玻璃长道，路面如冰面倒映天光，尽头是穆氏城楼。"),
        "cliff": Scene(id="cliff", name="断崖与主楼",
                       text="巨大的断崖壁面布满蛛网状裂痕，崖下是穆氏主楼与成群的穆氏子弟。"),
    },
    skills={
        "ice_bow": Skill(id="ice_bow", name="冰晶刹弓", element="绝冰",
                         color=[165, 228, 252]),
        "ice_phoenix_wing": Skill(id="ice_phoenix_wing", name="冰凤银翅",
                                  element="冰凤", color=[180, 215, 235]),
        "fire_spear": Skill(id="fire_spear", name="溶浆拳河", element="火系",
                            color=[232, 108, 56]),
    },
    style=STYLE,
)

# ══════════════════════════════════════════════════════════════════
# 分镜（3 章 / 8 格，覆盖各种典型场景）
# ══════════════════════════════════════════════════════════════════

def P(pid, seq, size, shot, cast, action, **kw):
    return Panel(id=pid, seq=seq, size=size, shot=shot,
                 cast=[CastMember(**c) for c in cast], action=action, **kw)

sb1 = Storyboard(chapter="2436", panels=[
    P("ch2436_P001", 1, PanelSize.WIDE, ShotSize.WIDE,
      [{"id": "mu_ningxue", "pos": "left", "variant": "battle"},
       {"id": "mu_feiluan", "pos": "far_right", "variant": "ice_wing"}],
      "穆宁雪立于冰玻璃长道拉弓，冰晶刹弓在掌心凝聚成形，箭矢离弦射向画面另一端",
      distance="两人相距约 8 米，中间是大片空旷的冰玻璃长道",
      skill="ice_bow", background="ice_corridor", mood="肃杀，细雪横飞",
      damage_level=DamageLevel.L0,
      emotion_hint={"mu_ningxue": "冷淡无波", "mu_feiluan": "倨傲"},
      source_span=[3, 6]),
    P("ch2436_P002", 2, PanelSize.TALL, ShotSize.CLOSE,
      [{"id": "mu_ningxue", "pos": "center"}],
      "穆宁雪冷眸直视前方，冰晶刹弓被拉成满弧，霜气自弓身四散",
      skill="ice_bow", background="ice_corridor", damage_level=DamageLevel.L0,
      emotion_hint={"mu_ningxue": "锁定目标"}, source_span=[6, 6]),
    P("ch2436_P003", 3, PanelSize.WIDE, ShotSize.EXTREME_WIDE,
      [{"id": "mu_ningxue", "pos": "far_left"}],
      "冰晶刹弓一箭贯穿长空，霜白箭痕横越半座山峦，崖壁炸开蛛网状裂痕",
      background="cliff", mood="天地为之屏息", damage_level=DamageLevel.L0,
      emotion_hint={"mu_ningxue": "决绝"}, source_span=[7, 9]),
])

sb2 = Storyboard(chapter="2437", panels=[
    P("ch2437_P001", 1, PanelSize.WIDE, ShotSize.MEDIUM,
      [{"id": "mu_feiluan", "pos": "left", "variant": "ice_wing"},
       {"id": "mo_fan", "pos": "right"}],
      "穆飞鸾指尖点向莫凡，莫凡双手插兜、神情挑衅",
      distance="两人隔开约两米，一触即发",
      background="ice_corridor", damage_level=DamageLevel.L1,
      emotion_hint={"mu_feiluan": "怒视", "mo_fan": "痞笑"},
      source_span=[20, 24]),
    P("ch2437_P002", 2, PanelSize.WIDE, ShotSize.WIDE,
      [{"id": "mo_fan", "pos": "left", "variant": "fire_king"},
       {"id": "mu_yinfeng", "pos": "right", "variant": "default"}],
      "莫凡周身燃起烈焰，身后浮现炎姬女王火魂影；穆隐凤在画面另一端展翼戒备",
      distance="双方相距约 10 米，中间是融化的雪地",
      skill="fire_spear", background="ice_corridor",
      damage_level=DamageLevel.L2, mood="烈焰与寒气对撞",
      emotion_hint={"mo_fan": "张扬大笑", "mu_yinfeng": "惊疑"},
      source_span=[30, 36]),
    P("ch2437_P003", 3, PanelSize.TALL, ShotSize.CLOSE,
      [{"id": "mu_yinfeng", "pos": "center", "variant": "default"}],
      "穆隐凤背后展开多重冰凤银翅，层叠修长的幽蓝冰晶羽翼边缘泛着银光",
      skill="ice_phoenix_wing", background="cliff", damage_level=DamageLevel.L2,
      emotion_hint={"mu_yinfeng": "杀意凛然"}, source_span=[38, 40]),
])

sb3 = Storyboard(chapter="2438", panels=[
    P("ch2438_P001", 1, PanelSize.WIDE, ShotSize.WIDE,
      [{"id": "mo_fan", "pos": "left"}, {"id": "mu_ningxue", "pos": "right"}],
      "莫凡与穆宁雪背靠背站定，一人火、一人冰，气场各自向外扩开",
      distance="两人背靠背，相隔不到一米，护住彼此的侧后",
      background="cliff", damage_level=DamageLevel.L2,
      emotion_hint={"mo_fan": "咬牙硬撑", "mu_ningxue": "冷静专注"},
      source_span=[50, 53]),
    P("ch2438_P002", 2, PanelSize.WIDE, ShotSize.EXTREME_WIDE,
      [{"id": "mo_fan", "pos": "far_center"}],
      "极远景：火柱自谷底冲上百米高空，云层被烧出一个洞，四周山体尽成焦黑",
      skill="fire_spear", background="cliff",
      damage_level=DamageLevel.L3, mood="毁天灭地",
      emotion_hint={"mo_fan": "力竭嘶吼"}, source_span=[60, 64]),
])

storyboards = [sb1, sb2, sb3]

# ══════════════════════════════════════════════════════════════════
# 对白
# ══════════════════════════════════════════════════════════════════

def U(uid, who, text, span, style=BubbleStyle.SPEECH, confirmed=True):
    return Utterance(id=uid, who=who, text=text, source_span=span,
                     confirmed=confirmed,
                     bubble=BubbleSpec(style=style))

dialogue = DialogueBook(items={
    "ch2436_P001": [
        U("b1", "mu_ningxue", "你喜欢这柄冰晶刹弓，作为多年的朋友，我自然亲手赠你。", [4, 4]),
        U("b2", "mu_feiluan", "解决掉他们。", [7, 7]),
    ],
    "ch2436_P003": [
        U("b3", "mu_ningxue", "今天你休想活着离开穆庞山！", [8, 8], BubbleStyle.SHOUT),
    ],
    "ch2437_P001": [
        U("b4", "mu_feiluan", "你又是什么东西，神圣穆庞山岂是你这种无身份的人能随随便便上来的！", [20, 21]),
        U("b5", "mo_fan", "穆飞鸾，你脑子是有问题吗，来你们穆氏，我当然是打上来的。", [22, 24]),
    ],
    "ch2437_P002": [
        U("b6", "mo_fan", "有老子在的地方，一定得是红色！", [33, 33], BubbleStyle.SHOUT),
    ],
    "ch2438_P001": [
        U("b7", "mu_ningxue", "你为我护航，小心穆隐凤的凤吟。", [51, 51]),
        U("b8", "mo_fan", "我命硬，别分心了，好好蓄下一箭。", [52, 52]),
    ],
    "ch2438_P002": [
        U("b9", "mo_fan", "溶浆拳河！", [61, 61], BubbleStyle.SHOUT),
    ],
})

# ══════════════════════════════════════════════════════════════════
# 页序（★ 冻结）
# ══════════════════════════════════════════════════════════════════

pages = [
    LayoutPage(page=1, type=PageType.TITLE, title="AI Comic Studio",
               sub="示例项目 · 3 章 / 8 格", volume="v1"),
    LayoutPage(page=2, type=PageType.PANELS, panels=["ch2436_P001"]),
    LayoutPage(page=3, type=PageType.PANELS, panels=["ch2436_P002"]),
    LayoutPage(page=4, type=PageType.PANELS, panels=["ch2436_P003"]),
    LayoutPage(page=5, type=PageType.TITLE, title="第2437章 冰凤银翅", volume="v2"),
    LayoutPage(page=6, type=PageType.PANELS, panels=["ch2437_P001"]),
    LayoutPage(page=7, type=PageType.PANELS, panels=["ch2437_P002", "ch2437_P003"]),
    LayoutPage(page=8, type=PageType.PANELS, panels=["ch2438_P001"]),
    LayoutPage(page=9, type=PageType.PANELS, panels=["ch2438_P002"]),
]
layout = Layout(total=len(pages), pages=pages,
                volumes=[Volume(id="v1", title="第一卷", sub="第2436章"),
                         Volume(id="v2", title="第二卷", sub="第2437–2438章")])

project = ComicProject(name="demo", bible=bible,
                       storyboards=storyboards, dialogue=dialogue, layout=layout)

# ══════════════════════════════════════════════════════════════════
# 落盘
# ══════════════════════════════════════════════════════════════════

if os.path.isdir(PROJ):
    shutil.rmtree(PROJ)
os.makedirs(PROJ, exist_ok=True)

(PROJ / "bible.json") if False else open(os.path.join(PROJ, "bible.json"), "w",
                                         encoding="utf-8").write(
    bible.model_dump_json(indent=1))
open(os.path.join(PROJ, "storyboard.json"), "w", encoding="utf-8").write(
    json.dumps([s.model_dump(mode="json") for s in storyboards],
               ensure_ascii=False, indent=1))
open(os.path.join(PROJ, "dialogue.json"), "w", encoding="utf-8").write(
    dialogue.model_dump_json(indent=1))
open(os.path.join(PROJ, "layout.json"), "w", encoding="utf-8").write(
    layout.model_dump_json(indent=1))

# 生成带行号的原文 —— 让 IR-007（对白可追溯）能通过校验
def build_source(proj_dir):
    dlg = json.load(open(os.path.join(proj_dir, "dialogue.json"), encoding="utf-8"))
    items = dlg.get("items", {})
    need = 1
    for utts in items.values():
        for u in utts:
            span = u.get("source_span") or [1, 1]
            need = max(need, span[1])
    lines = [""] * need
    for utts in items.values():
        for u in utts:
            a, b = (u.get("source_span") or [1, 1])
            body = u["text"] if u["text"].startswith("「") else f"「{u['text']}」"
            lines[a - 1] = body
            for i in range(a, b):
                if i < len(lines) and not lines[i]:
                    lines[i] = "（战斗持续。）"
    for i, ln in enumerate(lines):
        if not ln:
            lines[i] = "雪落在冰玻璃长道上。" if i % 3 == 0 else "风把细雪吹得横飞。"
    return "\n".join(lines)


open(os.path.join(PROJ, "source.txt"), "w", encoding="utf-8").write(
    build_source(PROJ))

# 用 Mock 生图产出素材，工作台打开即可见
studio = RenderStudio(MockImageProvider())
rep = studio.generate_panels(
    bible, [p for sb in storyboards for p in sb.panels],
    os.path.join(PROJ, "assets"))

print()
print("  ✅ 示例项目已生成：projects/demo/")
print(f"     角色 {len(bible.characters)} ｜ 场景 {len(bible.scenes)} ｜ "
      f"技能 {len(bible.skills)}")
print(f"     章节 {len(storyboards)} ｜ 镜头 {sum(len(s.panels) for s in storyboards)} ｜ "
      f"对白 {sum(len(v) for v in dialogue.items.values())} ｜ 页数 {layout.total}")
print(f"     素材 {rep.generated} 张（Mock）")
print()
print("  启动工作台：")
print("     python -m apps.api.server --port 8000")
print("     然后打开 http://127.0.0.1:8000")
