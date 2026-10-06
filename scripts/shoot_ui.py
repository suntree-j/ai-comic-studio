# -*- coding: utf-8 -*-
"""用 Edge headless 截工作台界面（确保数据加载完成再截）

坑：`--virtual-time-budget` 只推进虚拟时钟，异步 fetch 未必回来，
    结果截到「请选择一个项目」的空状态。
解法：截图后按文件大小判断是否加载完成（有渲染图的页面必然 > 100 KB），
    不达标就加大预算重试。

用法：
    python scripts/shoot_ui.py
"""
import io, os, subprocess, sys, time, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "images")
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/chromium", "/usr/bin/google-chrome",
]
MIN_OK = 90_000          # 小于这个字节数说明页面没加载完


def find_browser():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def wait_ready(port, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def warmup(port):
    """先打一遍接口，避免首次请求慢导致页面来不及渲染"""
    for path in ("/api/projects", "/api/projects/showcase",
                 "/api/projects/showcase/pages/2.png?w=760"):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30).read()
        except Exception as e:
            print(f"   ⚠️ 预热 {path} 失败：{e}")


def shoot(browser, url, out, size, budget):
    cmd = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--no-sandbox", "--force-device-scale-factor=1",
           f"--window-size={size}", f"--screenshot={out}",
           f"--virtual-time-budget={budget}", url]
    subprocess.run(cmd, capture_output=True, timeout=240)
    return os.path.getsize(out) if os.path.exists(out) else 0


def main():
    browser = find_browser()
    if not browser:
        print("  ❌ 找不到 Edge/Chromium")
        return 1
    os.makedirs(OUT, exist_ok=True)

    port = 8130
    proc = subprocess.Popen(
        [sys.executable, "-m", "apps.api.server", "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready(port):
            print("  ❌ 服务未就绪")
            return 1
        print("  服务就绪，预热接口 …")
        warmup(port)

        targets = [
            ("workbench.png", f"http://127.0.0.1:{port}/?project=showcase&page=2",
             "1600,1000"),
            ("validate.png",
             f"http://127.0.0.1:{port}/?project=showcase&page=2&validate=1",
             "1400,900"),
        ]
        ok = 0
        for name, url, size in targets:
            p = os.path.join(OUT, name)
            n = 0
            for budget in (15000, 30000, 60000):
                n = shoot(browser, url, p, size, budget)
                if n >= MIN_OK:
                    break
                print(f"     {name} 只截到 {n/1024:.0f} KB，加大预算重试 …")
            if n >= MIN_OK:
                ok += 1
                print(f"  ✅ {name}  {n/1024:.0f} KB")
            else:
                print(f"  ❌ {name} 仍然过小（{n/1024:.0f} KB）")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()
        print("  服务已停")
    print(f"\n  {ok}/{len(targets)} 张截图成功")
    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    sys.exit(main())
