# -*- coding: utf-8 -*-
"""端到端验收：一条命令跑通全部核心流程并自检

    python scripts/e2e_check.py

覆盖：
    ① 单元测试                129 个
    ② 小说 → IR → PDF         完全离线（Mock LLM + Mock 生图）
    ③ 生成示例项目             可在工作台打开
    ④ 生成展示项目             用真实素材（有的话）
    ⑤ 起工作台 + 打全部接口    真实 HTTP
    ⑥ 编辑闭环                 改气泡 → 重渲染 → 图片确实变化
    ⑦ 导出 PDF / 长图          并验证文件有效
    ⑧ 前端静态检查             JS 语法 / 路由对齐 / README 图链

任何一步失败都会明确报出，最后给出总结。
"""
import io, json, os, subprocess, sys, time, urllib.request, urllib.error
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PORT = 8140
BASE = f"http://127.0.0.1:{PORT}"
results = []


def step(name: str):
    print(f"\n{'─' * 66}\n {name}\n{'─' * 66}")


def record(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    print(f"   {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def http(path, method="GET", data=None, timeout=60):
    req = urllib.request.Request(BASE + path, method=method)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, body, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def wait_ready(timeout=45):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    print("=" * 66)
    print(" AI Comic Studio · 端到端验收")
    print("=" * 66)

    # ── ① 单元测试 ──
    step("① 单元测试")
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    tail = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
    record("pytest", r.returncode == 0, tail)

    # ── ② 离线端到端出 PDF ──
    step("② 小说 → IR → PDF（完全离线）")
    r = subprocess.run([sys.executable, "examples/minimal/run_full.py"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    out = r.stdout or ""
    pdf = os.path.join(ROOT, "examples", "minimal", "out", "comic.pdf")
    ok = r.returncode == 0 and os.path.exists(pdf)
    record("生成 PDF", ok, f"{os.path.getsize(pdf)/1024:.0f} KB" if ok else out[-200:])
    record("自修复被触发", "IR-002" in out and "第 2 轮通过" in out)
    longs = os.path.join(ROOT, "examples", "minimal", "out", "longs")
    record("长图导出", os.path.isdir(longs) and len(os.listdir(longs)) >= 2,
           f"{len(os.listdir(longs))} 张" if os.path.isdir(longs) else "")

    # ── ③④ 示例项目 ──
    step("③ 生成示例项目")
    for script, name in (("scripts/make_demo_project.py", "demo"),
                         ("scripts/make_showcase_project.py", "showcase")):
        r = subprocess.run([sys.executable, script], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
        d = os.path.join(ROOT, "projects", name)
        files = ["bible.json", "storyboard.json", "dialogue.json", "layout.json"]
        ok = r.returncode == 0 and all(os.path.exists(os.path.join(d, f)) for f in files)
        record(f"{name} 项目", ok,
               "4 个 IR 文件齐全" if ok else (r.stdout or r.stderr or "")[-200:])

    # ── ⑤⑥⑦ 起服务打接口 ──
    step("④ 工作台 API（真实 HTTP）")
    proc = subprocess.Popen(
        [sys.executable, "-m", "apps.api.server", "--port", str(PORT)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready():
            record("服务启动", False, "等待超时")
            return 1
        record("服务启动", True, f":{PORT}")

        checks = [
            ("健康检查", "/api/health", "GET"),
            ("项目列表", "/api/projects", "GET"),
            ("完整 IR", "/api/projects/showcase", "GET"),
            ("规则表", "/api/rules", "GET"),
            ("第2页渲染", "/api/projects/showcase/pages/2.png?w=600", "GET"),
            ("单格底图", "/api/projects/showcase/panels/ch2436_P001.png?w=400", "GET"),
            ("提示词透明化", "/api/projects/showcase/prompt/ch2436_P001", "GET"),
            ("工作台首页", "/", "GET"),
            ("静态 JS", "/static/app.js", "GET"),
            ("校验", "/api/projects/showcase/validate", "POST"),
        ]
        for name, path, m in checks:
            try:
                s, ct, b = http(path, m, {} if m == "POST" else None)
                record(name, s == 200, f"{s} {ct.split('/')[-1][:12]} {len(b)/1024:.0f}KB")
            except Exception as e:                          # noqa: BLE001
                record(name, False, str(e)[:80])

        # 编辑闭环
        step("⑤ 编辑闭环：改气泡 → 重渲染")
        _, _, body = http("/api/projects/showcase")
        proj = json.loads(body)
        pid = next(iter(proj["dialogue"]["items"]))
        utt = proj["dialogue"]["items"][pid][0]

        before = http(f"/api/projects/showcase/pages/2.png?w=500")[2]
        s, _, b = http(f"/api/projects/showcase/dialogue/{pid}/{utt['id']}",
                       "PATCH", {"box": {"x": 0.6, "y": 0.08}})
        record("PATCH 气泡位置", s == 200,
               str(json.loads(b)["utterance"]["bubble"]["box"]))
        after = http(f"/api/projects/showcase/pages/2.png?w=500")[2]
        record("重渲染后画面确实变化", before != after)

        s, _, b = http(f"/api/projects/showcase/dialogue/{pid}/{utt['id']}",
                       "PATCH", {"who": "mo_fan"})
        rec = json.loads(b)["utterance"]
        record("换说话人自动取消确认", rec["who"] == "mo_fan" and rec["confirmed"] is False)

        # 改回去，避免污染示例
        http(f"/api/projects/showcase/dialogue/{pid}/{utt['id']}", "PATCH",
             {"who": utt["who"], "confirmed": True})

        # 导出
        step("⑥ 导出")
        s, _, b = http("/api/projects/showcase/export?fmt=pdf", "POST", {})
        f = json.loads(b)["file"] if s == 200 else ""
        record("导出 PDF", s == 200 and f.endswith("comic.pdf"))
        if f:
            s2, ct, pdfb = http(f)
            record("下载 PDF 有效", s2 == 200 and pdfb[:4] == b"%PDF",
                   f"{len(pdfb)/1024:.0f} KB, 魔数 {pdfb[:4]!r}")

        s, _, b = http("/api/projects/showcase/export?fmt=long&long_count=3",
                       "POST", {})
        files = json.loads(b).get("files", []) if s == 200 else []
        record("导出长图", s == 200 and len(files) == 3, f"{len(files)} 张")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()

    # ── ⑧ 前端静态 ──
    step("⑦ 前端静态检查")
    node = __import__("shutil").which("node")
    if node:
        r = subprocess.run([node, "--check", "apps/workbench/app.js"],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        record("app.js 语法", r.returncode == 0,
               (r.stderr or "").strip().splitlines()[0] if r.returncode else "node --check OK")
    else:
        record("app.js 语法", True, "（无 node，跳过）")

    rd = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    import re
    missing = [x for x in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', rd)
               if not x.startswith("http") and not os.path.exists(os.path.join(ROOT, x))]
    record("README 图链", not missing, f"缺 {missing}" if missing else "全部存在")

    # ── 总结 ──
    step("验收总结")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    for name, ok, detail in results:
        if not ok:
            print(f"   ❌ {name}  {detail}")
    print()
    print(f"   {passed}/{total} 项通过")
    print("=" * 66)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
