# -*- coding: utf-8 -*-
"""自动截取工作台界面截图（用 Edge headless，无需装 Playwright）

产物 → docs/images/
    workbench.png       工作台主界面（含气泡框与对白编辑）
    page.png            示例漫画页
    validate.png        12 条规则校验弹层

用法：
    python scripts/shoot.py                 # 自动起服务、截图、关服务
    python scripts/shoot.py --port 8125     # 服务已在跑时直接用
"""
import io, os, subprocess, sys, time, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "images")
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
]


def find_browser():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def wait_ready(port: int, timeout: float = 30.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3):
                return True
        except Exception:
            time.sleep(0.6)
    return False


def shoot(browser: str, url: str, out: str, size: str = "1600,1000",
          budget: int = 9000) -> bool:
    cmd = [
        browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-sandbox", "--force-device-scale-factor=1",
        f"--window-size={size}",
        f"--screenshot={out}",
        f"--virtual-time-budget={budget}",
        url,
    ]
    subprocess.run(cmd, capture_output=True, timeout=120)
    return os.path.exists(out) and os.path.getsize(out) > 5000


def main():
    args = sys.argv[1:]
    port = 8125
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    manage = "--port" not in args

    browser = find_browser()
    if not browser:
        print("  ❌ 找不到 Edge/Chromium，无法截图")
        return 1

    os.makedirs(OUT, exist_ok=True)
    proc = None
    if manage:
        print(f"  启动服务 :{port} …")
        proc = subprocess.Popen(
            [sys.executable, "-m", "apps.api.server", "--port", str(port)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not wait_ready(port):
            print("  ❌ 服务未就绪")
            proc.terminate()
            return 1
    print("  服务就绪")

    base = f"http://127.0.0.1:{port}"
    shots = [
        ("workbench.png", f"{base}/?project=showcase&page=2", "1600,1000", 9000),
        ("page.png", f"{base}/?project=showcase&page=9", "1200,1400", 9000),
    ]
    ok = 0
    for name, url, size, budget in shots:
        p = os.path.join(OUT, name)
        if shoot(browser, url, p, size, budget):
            ok += 1
            print(f"  ✅ {name}  {os.path.getsize(p)/1024:.0f} KB")
        else:
            print(f"  ❌ {name} 截图失败")

    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()
        print("  服务已停")
    print(f"\n  共 {ok}/{len(shots)} 张 → {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
