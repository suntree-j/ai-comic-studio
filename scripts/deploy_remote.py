# -*- coding: utf-8 -*-
"""把 AI Comic Studio 部署到云服务器（子路径 /comic/）

前提（已勘察）：
    · 服务器已有生产环境 data-platform，占用 80 端口与 /data/ /airflow/ 等子路径
    · 8000/8100/8085 已被占用 → 本应用用 8500
    · 不覆盖现有 nginx 站点配置，只**追加**一个 /comic/ location

做法：
    ① 打包最小运行集（packages / apps / pyproject）
    ② scp 上传到 /opt/ai-comic-studio
    ③ 服务器上建 venv 装依赖
    ④ 生成示例项目（本地生成好的 IR + 假漫画页一起传上去）
    ⑤ 装 systemd 服务（监听 127.0.0.1:8500）
    ⑥ 追加 nginx location /comic/
    ⑦ 验证

用法：
    python scripts/deploy_remote.py            # 全流程
    python scripts/deploy_remote.py --upload   # 只上传
    python scripts/deploy_remote.py --verify   # 只验证
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HOST = os.environ.get("COMIC_DEPLOY_HOST", "36.151.150.140")
USER = os.environ.get("COMIC_DEPLOY_USER", "root")
KEY = os.environ.get("COMIC_DEPLOY_KEY", r"C:\Users\jsy28\Downloads\suntree.pem")
REMOTE_DIR = "/opt/ai-comic-studio"
PORT = 8500
BASE_PATH = "/comic/"
PUBLIC = f"http://{HOST}{BASE_PATH}"

SSH_OPTS = [
    "-i", KEY,
    "-o", "IdentitiesOnly=yes",
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=20",
    "-o", "ServerAliveInterval=15",
]


def sh(cmd: str, check=True) -> subprocess.CompletedProcess:
    """在服务器上执行命令"""
    r = subprocess.run(["ssh", *SSH_OPTS, f"{USER}@{HOST}", cmd],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    if check and r.returncode != 0:
        print(f"  ❌ 远程命令失败（{r.returncode}）：{cmd[:90]}")
        print((r.stdout or "")[-1200:])
        print((r.stderr or "")[-1200:])
        raise SystemExit(1)
    return r


def upload(local: str, remote: str) -> None:
    r = subprocess.run(
        ["scp", *SSH_OPTS, "-r", local, f"{USER}@{HOST}:{remote}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=1800)
    if r.returncode != 0:
        print(f"  ❌ 上传失败：{local}\n{r.stderr[-800:]}")
        raise SystemExit(1)


def step(t: str) -> None:
    print(f"\n{'─' * 66}\n {t}\n{'─' * 66}")


# ══════════════════════════════════════════════════════════════════
# ① 打包
# ══════════════════════════════════════════════════════════════════

def make_tarball() -> str:
    """只打包运行必需的文件（不含 docs/images、tests、.git）"""
    out = os.path.join(tempfile.gettempdir(), "aicomic-deploy.tar.gz")
    # scripts 也要传：服务器上要用它生成示例内容
    include = ["packages", "apps", "scripts", "pyproject.toml",
               "README.md", "LICENSE"]

    def flt(ti: tarfile.TarInfo):
        base = os.path.basename(ti.name)
        if base in ("__pycache__", ".pytest_cache") or base.endswith(".pyc"):
            return None
        return ti

    with tarfile.open(out, "w:gz") as tf:
        for item in include:
            p = os.path.join(ROOT, item)
            if os.path.exists(p):
                tf.add(p, arcname=item, filter=flt)
    return out


def make_demo_tarball() -> str:
    """打包示例项目（漫画 IR 项目 + 面板编辑器的 works 项目）

    ★ 不带 assets/ 里的缓存与 out/ 产物（可由数据重建，省流量）
    """
    out = os.path.join(tempfile.gettempdir(), "aicomic-projects.tar.gz")
    groups = [(os.path.join(ROOT, "projects"), "projects"),
              (os.path.join(ROOT, "works"), "works")]
    if not any(os.path.isdir(d) for d, _ in groups):
        return ""

    def flt(ti: tarfile.TarInfo):
        name = ti.name.replace("\\", "/")
        for skip in ("/out/", "/.cache/"):
            if skip in name:
                return None
        return ti

    with tarfile.open(out, "w:gz") as tf:
        for root, arc in groups:
            if not os.path.isdir(root):
                continue
            for name in os.listdir(root):
                d = os.path.join(root, name)
                if os.path.isdir(d):
                    tf.add(d, arcname=f"{arc}/{name}", filter=flt)
    return out


# ══════════════════════════════════════════════════════════════════
# ② 服务器准备
# ══════════════════════════════════════════════════════════════════

SERVICE = f"""[Unit]
Description=AI Comic Studio (FastAPI workbench + render engine)
Documentation=https://github.com/
After=network.target

[Service]
Type=simple
WorkingDirectory={REMOTE_DIR}
Environment=COMIC_PROJECTS={REMOTE_DIR}/projects
Environment=COMIC_IMAGE_PROVIDER=mock
Environment=COMIC_BASE_PATH={BASE_PATH}
ExecStart={REMOTE_DIR}/venv/bin/python -m apps.api.server \\
    --host 127.0.0.1 --port {PORT} --base-path {BASE_PATH} --provider mock
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

NGINX_SNIPPET = f"""
    # --------------------------------------------------------
    # AI Comic Studio（漫画工作台 + 渲染引擎）
    # 追加于 {time.strftime('%Y-%m-%d')}，不改动上方 data-platform 配置
    #
    # proxy_pass 结尾的 **/** 负责剥掉 /comic 前缀：
    #   浏览器 /comic/static/app.js → 应用收到 /static/app.js
    # 首页 /comic/ 会先走 index 再注入 <base href="/comic/">，
    # 让前端相对路径与接口前缀都能算对。
    # --------------------------------------------------------
    location = /comic {{
        return 301 /comic/;
    }}

    location /comic/ {{
        proxy_pass http://127.0.0.1:{PORT}/;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 页面渲染 / 导出可能较慢
        proxy_connect_timeout 10s;
        proxy_read_timeout   300s;
        proxy_send_timeout   300s;

        # 演示站：不允许公网触发出图（会消耗生图额度）
        location = /comic/api/projects/demo/render {{
            return 403;
        }}
    }}
"""


def prepare() -> None:
    step("① 服务器准备（目录 / venv / 依赖 / 中文字体）")
    sh(f"mkdir -p {REMOTE_DIR}")

    # ★ 中文字体：缺了它气泡里的中文会渲染成方块（接口仍返回 200，极难发现）
    r = sh("fc-list :lang=zh 2>/dev/null | head -1 || "
           "ls /usr/share/fonts/truetype/wqy/*.ttc "
           "/usr/share/fonts/**/NotoSansCJK*.ttc 2>/dev/null | head -1 || true",
           check=False)
    if (r.stdout or "").strip():
        print(f"   中文字体已存在：{(r.stdout or '').strip()[:70]}")
    else:
        print("   安装中文字体 fonts-wqy-microhei（约 4 MB）…")
        sh("apt-get update -qq && DEBIAN_FRONTEND=noninteractive "
           "apt-get install -y -qq fonts-wqy-microhei", check=False)
        r2 = sh("ls /usr/share/fonts/truetype/wqy/*.ttc 2>/dev/null | head -1", check=False)
        print(f"   字体：{(r2.stdout or '（仍缺失，将回退到其它候选）').strip()}")

    r = sh(f"test -d {REMOTE_DIR}/venv && echo exists || echo missing", check=False)
    if "exists" in (r.stdout or ""):
        print("   venv 已存在，跳过创建")
    else:
        print("   创建 venv 并安装依赖（约 1–3 分钟）…")
        sh(f"python3 -m venv {REMOTE_DIR}/venv")
    sh(f"{REMOTE_DIR}/venv/bin/pip install -q --upgrade pip")
    # 依赖必须与 pyproject.toml 的 dependencies + web extra 完全一致。
    # ★ 这里漏过两次：
    #     numpy            —— packages/render/bubble.py 用到
    #     python-multipart —— apps/api/editor.py 的 File/UploadFile 用到
    #   两次都只在服务器上炸，因为本地早就装好了。
    #   tests/test_packaging.py 会静态检查「所有第三方 import 都已声明」。
    sh(f"{REMOTE_DIR}/venv/bin/pip install -q "
       "'fastapi>=0.110' 'uvicorn[standard]>=0.27' 'python-multipart>=0.0.9' "
       "'pydantic>=2.6' 'Pillow>=10.0' 'numpy>=1.24' 'fonttools>=4.40' "
       "'pymupdf>=1.24' 'httpx>=0.27'")
    r = sh(f"{REMOTE_DIR}/venv/bin/python -c \""
           f"import fastapi,uvicorn,PIL,pydantic,numpy,fontTools,multipart,httpx;"
           f"print('fastapi',fastapi.__version__,'pydantic',pydantic.VERSION,"
           f"'PIL',PIL.__version__,'numpy',numpy.__version__,"
           f"'fontTools',fontTools.version)\"")
    print("   " + (r.stdout or "").strip())


def upload_code() -> None:
    step("② 上传代码")
    tb = make_tarball()
    print(f"   代码包 {os.path.getsize(tb)/1024:.0f} KB")
    upload(tb, f"{REMOTE_DIR}/code.tar.gz")
    sh(f"cd {REMOTE_DIR} && tar xzf code.tar.gz && rm -f code.tar.gz")
    r = sh(f"cd {REMOTE_DIR} && ls -1")
    print("   " + " ".join((r.stdout or "").split()))

    pt = make_demo_tarball()
    if pt:
        print(f"   示例项目包 {os.path.getsize(pt)/1024:.0f} KB")
        upload(pt, f"{REMOTE_DIR}/projects.tar.gz")
        sh(f"cd {REMOTE_DIR} && tar xzf projects.tar.gz && rm -f projects.tar.gz")
        r = sh(f"ls -1 {REMOTE_DIR}/projects 2>/dev/null | head")
        print("   项目：" + " ".join((r.stdout or "").split()))


def install_service() -> None:
    step("③ 安装 systemd 服务")
    with tempfile.NamedTemporaryFile("w", suffix=".service", delete=False,
                                     encoding="utf-8") as f:
        f.write(SERVICE)
        tmp = f.name
    upload(tmp, "/etc/systemd/system/ai-comic-studio.service")
    os.unlink(tmp)

    sh(f"chmod -R a+rX {REMOTE_DIR}")
    sh("systemctl daemon-reload")
    sh("systemctl enable ai-comic-studio")
    # ★ 必须 stop + start，不能只 restart：
    #   实测 `systemctl restart` 后立刻 `is-active` 会返回 active，
    #   但新进程其实还没起来 —— 于是验证跑在**旧代码**上，
    #   表现为「接口 404 但服务显示 active」，极难排查。
    #   这里改成显式停+启，然后**轮询等接口真的通**。
    sh("systemctl stop ai-comic-studio", check=False)
    time.sleep(1)
    sh("systemctl start ai-comic-studio")

    probe = f"http://127.0.0.1:{PORT}/api/health"
    ready = False
    for i in range(30):
        time.sleep(1)
        r = sh(f"curl -s -o /dev/null -w '%{{http_code}}' {probe}", check=False)
        if (r.stdout or "").strip() == "200":
            print(f"   服务就绪（{i + 1} 秒）")
            ready = True
            break
    if not ready:
        r = sh("journalctl -u ai-comic-studio -n 40 --no-pager", check=False)
        print("   ❌ 服务未就绪，日志：")
        print((r.stdout or "")[-2000:])
        raise SystemExit(1)

    # 再确认编辑器的新路由真的在（防止旧代码还在跑）
    r = sh(f"curl -s -o /dev/null -w '%{{http_code}}' "
           f"http://127.0.0.1:{PORT}/api/edit/projects", check=False)
    code = (r.stdout or "").strip()
    if code != "200":
        print(f"   ❌ 编辑器接口返回 {code}（新代码没生效？）")
        r = sh("journalctl -u ai-comic-studio -n 30 --no-pager", check=False)
        print((r.stdout or "")[-1500:])
        raise SystemExit(1)
    print("   新路由已生效（/api/edit/projects → 200）")


def install_nginx() -> None:
    step("④ 追加 nginx 配置（不覆盖现有站点）")
    conf = "/etc/nginx/sites-enabled/data-platform.conf"
    marker = "AI Comic Studio"

    r = sh(f"grep -c '{marker}' {conf} || true", check=False)
    if (r.stdout or "0").strip() not in ("0", ""):
        print("   已存在 /comic/ 配置，跳过")
        return

    # 备份 → 在最后一个 } 前插入 → 语法检查 → reload
    sh(f"cp {conf} /root/{os.path.basename(conf)}.bak-$(date +%Y%m%d-%H%M%S)")
    print("   已备份原配置")

    with tempfile.NamedTemporaryFile("w", suffix=".snippet", delete=False,
                                     encoding="utf-8") as f:
        f.write(NGINX_SNIPPET)
        snip = f.name
    upload(snip, "/tmp/comic.snippet")
    os.unlink(snip)

    # 用 python 在最后一个 } 之前插入（保持原文件其余内容一字不动）
    sh("python3 - <<'PYEOF'\n"
       "conf = '/etc/nginx/sites-enabled/data-platform.conf'\n"
       "src = open(conf, encoding='utf-8').read()\n"
       "snippet = open('/tmp/comic.snippet', encoding='utf-8').read()\n"
       "idx = src.rstrip().rfind('}')\n"
       "assert idx > 0, '找不到 server 块的结束大括号'\n"
       "new = src[:idx] + snippet + src[idx:]\n"
       "open(conf, 'w', encoding='utf-8').write(new)\n"
       "print('   已插入 /comic/ location')\n"
       "PYEOF")

    r = sh("nginx -t", check=False)
    out = (r.stdout or "") + (r.stderr or "")
    print("   nginx -t：" + ("OK" if "successful" in out else out[-400:]))
    if "successful" not in out:
        sh(f"cp $(ls -t /root/data-platform.conf.bak-* | head -1) {conf}")
        print("   ❌ 语法检查失败，已回滚")
        raise SystemExit(1)
    sh("systemctl reload nginx")
    print("   已 reload nginx")


def generate_assets() -> None:
    step("⑤ 生成示例内容")
    r = sh("ls -1 {0}/projects 2>/dev/null | wc -l".format(REMOTE_DIR), check=False)
    n = (r.stdout or "0").strip()
    if n not in ("0", ""):
        print(f"   漫画 IR 项目已有 {n} 个，跳过生成")
    else:
        sh(f"cd {REMOTE_DIR} && mkdir -p projects && "
           f"COMIC_PROJECTS={REMOTE_DIR}/projects "
           f"{REMOTE_DIR}/venv/bin/python scripts/make_demo_project.py", check=False)
        r = sh(f"ls -1 {REMOTE_DIR}/projects 2>/dev/null", check=False)
        print("   IR 项目：" + " ".join((r.stdout or "").split()))

    # 面板编辑器的示例项目 —— 让访客点开就看到效果，而不是空列表
    r = sh(f"cd {REMOTE_DIR} && COMIC_WORKSPACE={REMOTE_DIR}/works "
           f"{REMOTE_DIR}/venv/bin/python scripts/seed_demo_project.py",
           check=False)
    out = (r.stdout or "").strip()
    print("   " + (out.splitlines()[-1] if out else "（编辑器示例跳过）"))


# ══════════════════════════════════════════════════════════════════
# 验证
# ══════════════════════════════════════════════════════════════════

def fetch(url: str, timeout=90, method="GET"):
    req = urllib.request.Request(url, method=method)
    if method == "POST":
        req.add_header("Content-Type", "application/json")
    body = b"{}" if method == "POST" else None
    with urllib.request.urlopen(req, body, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def verify() -> int:
    step("⑥ 验证")
    checks = []

    def chk(name, fn):
        try:
            detail = fn()
            checks.append((name, True, detail))
            print(f"   ✅ {name}  {detail}")
        except Exception as e:                              # noqa: BLE001
            checks.append((name, False, str(e)[:90]))
            print(f"   ❌ {name}  {str(e)[:90]}")

    chk("首页 /comic/", lambda: (
        lambda s, c, b: f"{s}  {len(b)} 字节  base={'/comic/' in b.decode('utf-8','replace')}"
    )(*fetch(PUBLIC)))
    chk("首页是面板编辑器", lambda: (
        lambda s, c, b: f"{s}  {'编辑器' if '面板编辑器' in b.decode('utf-8','replace') else '???'}"
    )(*fetch(PUBLIC)))
    chk("编辑器 JS", lambda: (
        lambda s, c, b: f"{s}  {len(b)} 字节"
    )(*fetch(PUBLIC + "static/editor.js")))
    chk("编辑器 CSS", lambda: (
        lambda s, c, b: f"{s}  {len(b)} 字节"
    )(*fetch(PUBLIC + "static/editor.css")))
    chk("编辑器接口", lambda: fetch(PUBLIC + "api/edit/projects")[2].decode()[:80])
    chk("旧工作台 /workbench", lambda: (
        lambda s, c, b: f"{s}  {'漫画工作台' in b.decode('utf-8','replace')}"
    )(*fetch(PUBLIC + "workbench")))
    chk("静态 app.js", lambda: (
        lambda s, c, b: f"{s}  {len(b)} 字节"
    )(*fetch(PUBLIC + "static/app.js")))
    chk("静态 style.css", lambda: (
        lambda s, c, b: f"{s}  {len(b)} 字节"
    )(*fetch(PUBLIC + "static/style.css")))
    chk("健康检查", lambda: fetch(PUBLIC + "api/health")[2].decode()[:90])
    chk("项目列表", lambda: fetch(PUBLIC + "api/projects")[2].decode()[:120])
    chk("规则表", lambda: f"{len(__import__('json').loads(fetch(PUBLIC + 'api/rules')[2]))} 条")
    chk("页面渲染图", lambda: (
        lambda s, c, b: f"{s} {c} {len(b)/1024:.0f} KB"
    )(*fetch(PUBLIC + "api/projects/demo/pages/2.png?w=600")))
    chk("IR 校验", lambda: fetch(PUBLIC + "api/projects/demo/validate", method="POST")[2]
        .decode()[:80])
    chk("中文字体可用", lambda: _check_font())

    ok = sum(1 for _, o, _ in checks if o)
    print(f"\n   {ok}/{len(checks)} 项通过")
    print(f"\n   👉 打开 {PUBLIC}")
    return 0 if ok == len(checks) else 1


def _check_font() -> str:
    """★ 在服务器上直接问「有没有中文字体」

    缺字体时页面接口照样 200、图片照样生成，只有肉眼能看出是方块，
    所以必须单独验证这一项。
    """
    code = (
        "import sys; sys.path.insert(0,'.');"
        "from packages.render.page import find_cjk_font;"
        "p=find_cjk_font();"
        "print(p or 'NONE')"
    )
    r = sh(f"cd {REMOTE_DIR} && {REMOTE_DIR}/venv/bin/python -c \"{code}\"", check=False)
    out = (r.stdout or "").strip().splitlines()
    p = out[-1] if out else "NONE"
    assert p and p != "NONE", \
        "服务器上找不到中文字体 —— 气泡中文会变方块（apt install fonts-wqy-microhei）"
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true", help="只上传代码")
    ap.add_argument("--verify", action="store_true", help="只验证")
    a = ap.parse_args()

    print("=" * 66)
    print(f" 部署 AI Comic Studio → {USER}@{HOST}:{REMOTE_DIR}")
    print(f" 对外地址：{PUBLIC}")
    print("=" * 66)

    if a.verify:
        return verify()

    prepare()
    upload_code()
    install_service()
    install_nginx()
    generate_assets()

    if a.upload:
        print("\n   （--upload 模式，跳过验证）")
        return 0
    return verify()


if __name__ == "__main__":
    sys.exit(main())
