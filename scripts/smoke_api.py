# -*- coding: utf-8 -*-
"""端到端验证工作台 API（用 urllib，避免 PowerShell 的编码问题）"""
import io, json, sys, urllib.request, urllib.error
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = "http://127.0.0.1:8123"


def get(path, method="GET", data=None, ctype="application/json"):
    req = urllib.request.Request(BASE + path, method=method)
    if data is not None:
        body = json.dumps(data).encode() if ctype == "application/json" else data
        req.add_header("Content-Type", ctype)
    else:
        body = None
    with urllib.request.urlopen(req, body, timeout=60) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def show(name, fn):
    try:
        print(f"   OK  {name}  {fn()}")
    except Exception as e:
        print(f"   --  {name}  {e}")


print("  ══════════ 工作台 API 端到端验证 ══════════")
show("首页", lambda: (lambda s, c, b: f"{s}  {len(b)/1024:.1f} KB")(*get("/")))
show("app.js", lambda: (lambda s, c, b: f"{s}  {len(b)/1024:.1f} KB")(*get("/static/app.js")))
show("style.css", lambda: (lambda s, c, b: f"{s}  {len(b)/1024:.1f} KB")(*get("/static/style.css")))
show("第2页渲染图", lambda: (lambda s, c, b: f"{s} {c} {len(b)/1024:.1f} KB")(*get("/api/projects/demo/pages/2.png?w=600")))
show("第7页渲染图", lambda: (lambda s, c, b: f"{s} {c} {len(b)/1024:.1f} KB")(*get("/api/projects/demo/pages/7.png?w=600")))
show("单格底图", lambda: (lambda s, c, b: f"{s} {c} {len(b)/1024:.1f} KB")(*get("/api/projects/demo/panels/ch2437_P002.png?w=500")))
show("规则表", lambda: f"{len(json.loads(get('/api/rules')[2]))} 条")

# ── 编辑闭环：改气泡位置 → 重渲染 → 确认变化 ──
print()
print("  ══════════ 编辑闭环测试 ══════════")
before = get("/api/projects/demo/pages/2.png?w=500")[2]
_, _, proj = get("/api/projects/demo")
p = json.loads(proj)
utt = p["dialogue"]["items"]["ch2436_P001"][0]
print(f"   目标对白：{utt['id']}  who={utt['who']}  text={utt['text'][:18]}…")

st, _, body = get(f"/api/projects/demo/dialogue/ch2436_P001/{utt['id']}",
                  "PATCH", {"box": {"x": 0.62, "y": 0.05}})
print(f"   PATCH box → {st}  {json.loads(body)['utterance']['bubble']['box']}")

after = get("/api/projects/demo/pages/2.png?w=500")[2]
print(f"   重渲染后图片变化：{'是' if before != after else '否（可能未生效）'}")

# 改说话人 → 必须自动取消确认
st, _, body = get(f"/api/projects/demo/dialogue/ch2436_P001/{utt['id']}",
                  "PATCH", {"who": "mo_fan"})
res = json.loads(body)["utterance"]
print(f"   PATCH who → {st}  who={res['who']}  confirmed={res['confirmed']}（应为 False）")

# 改回
get(f"/api/projects/demo/dialogue/ch2436_P001/{utt['id']}", "PATCH",
    {"who": utt["who"], "confirmed": True})

# ── 导出 ──
print()
print("  ══════════ 导出测试 ══════════")
show("导出 PDF", lambda: (lambda s, c, b: f"{s}  {json.loads(b)['file']}")(*get("/api/projects/demo/export?fmt=pdf", "POST", {})))
show("下载 PDF", lambda: (lambda s, c, b: f"{s} {c} {len(b)/1024:.1f} KB")(*get("/api/projects/demo/download/comic.pdf")))
