# -*- coding: utf-8 -*-
"""本地验证子路径部署模式

模拟 nginx 把 /comic/* 剥掉前缀转发到应用的根路径：
    浏览器请求  /comic/static/app.js
      → nginx proxy_pass http://127.0.0.1:8152/   （带尾斜杠 = 去前缀）
      → 应用收到  /static/app.js

同时应用在 HTML 里注入 <base href="/comic/">，
让前端的相对路径与 API 前缀都能算对。
"""
import io, os, re, subprocess, sys, time, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 8152
BP = "/comic/"

PROC = None


def get(path, timeout=10):
    """直接打应用（模拟 nginx 去前缀之后的效果）"""
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def main():
    global PROC
    PROC = subprocess.Popen(
        [sys.executable, "-m", "apps.api.server", "--port", str(PORT),
         "--base-path", BP],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    try:
        ready = False
        for _ in range(40):
            try:
                get("/api/health", 2); ready = True; break
            except Exception:
                time.sleep(0.5)
        if not ready:
            out = PROC.stdout.read() if PROC.stdout else ""
            print("  ❌ 服务未起来：\n", out[-1500:])
            return 1

        print("  服务就绪（--base-path /comic/）\n")

        s, _, b = get("/api/health")
        print("  /api/health          →", s, b.decode()[:118])
        assert s == 200 and '"/comic/"' in b.decode(), "base_path 未生效"

        s, _, b = get("/api/config")
        print("  /api/config          →", s, b.decode()[:70])

        s, _, b = get("/")
        html = b.decode("utf-8", "replace")
        m = re.search(r'<base href="([^"]+)"', html)
        print("  注入 <base href>      →", m.group(1) if m else "（未注入）")
        assert m and m.group(1) == "/comic/", "base href 不对"

        refs = re.findall(r'(?:src|href)="(static/[^"]+)"', html)
        print("  HTML 相对资源引用      →", refs)
        assert refs and all(not r.startswith("/") for r in refs), "资源必须用相对路径"

        # 浏览器会把 static/app.js 解析成 /comic/static/app.js，
        # nginx 去前缀后应用收到 /static/app.js
        for ref in refs:
            s, ct, b = get("/" + ref)
            ok = s == 200 and len(b) > 500
            print(f"  nginx 去前缀后 /{ref:<18} → {s}  {len(b)} 字节  {'OK' if ok else 'FAIL'}")
            assert ok, f"{ref} 取不到"

        js = get("/static/app.js")[2].decode("utf-8", "replace")
        print("  含 url() 前缀函数      →", "function url(" in js)
        print("  含 <base> 解析         →", "querySelector('base')" in js)
        print("  页面图片走 url()       →", "img.src = url(" in js)

        s, _, b = get("/api/projects")
        print("  /api/projects        →", s, b.decode()[:70])

        print("\n  ✅ 子路径部署模式验证通过")
        print("     nginx: location /comic/ { proxy_pass http://127.0.0.1:8500/; }")
        print("            （proxy_pass 结尾的 / 负责剥掉 /comic 前缀）")
        return 0
    finally:
        PROC.terminate()
        try:
            PROC.wait(timeout=6)
        except Exception:
            PROC.kill()


if __name__ == "__main__":
    sys.exit(main())
