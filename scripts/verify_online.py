# -*- coding: utf-8 -*-
"""线上状态一键自检：GitHub 仓库 + 部署的服务器

用法：
    python scripts/verify_online.py            # 两项都查
    python scripts/verify_online.py --github   # 只查 GitHub
    python scripts/verify_online.py --server   # 只查服务器

GitHub 部分用 `gh auth token` 取凭据（**绝不打印 token**）；
服务器部分只用公开 HTTP，不需要凭据。

改完代码上了线，跑这个确认「外面看起来是不是好的」。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 换成自己的仓库 / 地址，或用环境变量覆盖
GH_FULL = os.environ.get("COMIC_GH_REPO", "suntree-j/ai-comic-studio")
SERVER = os.environ.get("COMIC_SERVER", "http://36.151.150.140/comic/")

results: list = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"   {'OK ' if ok else '-- '} {name}" + (f"  {detail}" if detail else ""))


def section(t: str) -> None:
    print(f"\n{'-' * 64}\n {t}\n{'-' * 64}")


# ══════════════════════════════════════════════════════════════════
# GitHub
# ══════════════════════════════════════════════════════════════════

def gh_api(method: str, path: str, token: str, body=None):
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        data=json.dumps(body).encode() if body else None)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "ai-comic-studio")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:                                  # noqa: BLE001
        return None, str(e)[:120]


def head(url: str, timeout=30):
    """不带凭据访问，模拟匿名访客（这才是招聘方看到的）"""
    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Length", "")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:                                  # noqa: BLE001
        return None, str(e)[:50]


def check_github() -> None:
    section("① GitHub 仓库")
    r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    token = r.stdout.strip() if r.returncode == 0 else ""
    if not token:
        rec("gh 凭据", False, "取不到 token，先 `gh auth login`")
        return
    rec("gh 凭据", True, "已登录")

    st, repo = gh_api("GET", f"/repos/{GH_FULL}", token)
    if st != 200:
        rec("读取仓库", False, f"HTTP {st} {repo}")
        return
    rec("读取仓库", True, repo["html_url"])
    rec("可见性", not repo["private"],
        "公开（陌生人可访问）" if not repo["private"] else "私有")
    rec("默认分支", bool(repo["default_branch"]), repo["default_branch"])
    if repo.get("topics"):
        rec("topics", True, ", ".join(repo["topics"][:6]) + " ...")

    # ★ 提交是否关联到账号 —— 关系到 contribution graph 与头像
    st_c, commits = gh_api("GET", f"/repos/{GH_FULL}/commits?per_page=1", token)
    if st_c == 200 and commits:
        c = commits[0]
        linked = bool(c.get("author"))
        rec("提交关联账号", linked,
            c["author"]["login"] if linked
            else "未关联（GitHub 会显示为无主头像）")

    br = repo["default_branch"]
    st, tree = gh_api("GET", f"/repos/{GH_FULL}/git/trees/{br}?recursive=1", token)
    if st == 200:
        files = [t["path"] for t in tree["tree"] if t["type"] == "blob"]
        rec("文件数", len(files) > 50, f"{len(files)} 个")
        for f in ("README.md", "LICENSE", "pyproject.toml",
                  "docs/lessons.md", "docs/modifying.md", "docs/deploy.md",
                  "packages/render/page.py", "apps/workbench/app.js"):
            rec(f"含 {f}", f in files)
    else:
        rec("读取文件树", False, f"HTTP {st}")

    # README 图片（访客视角）
    raw = f"https://raw.githubusercontent.com/{GH_FULL}/{br}/"
    rd = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    imgs = [x for x in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", rd)
            if not x.startswith("http")]
    good = 0
    for img in imgs:
        s, _ = head(raw + img)
        if s == 200:
            good += 1
        else:
            rec(f"图片 {img}", False, f"HTTP {s}")
    rec("README 图片访客可访问", bool(imgs) and good == len(imgs),
        f"{good}/{len(imgs)} 张")

    if st_c == 200 and commits:
        rec("最近提交", True,
            commits[0]["commit"]["message"].splitlines()[0][:52])
    print(f"\n   >> {repo['html_url']}")


# ══════════════════════════════════════════════════════════════════
# 服务器
# ══════════════════════════════════════════════════════════════════

def fetch(path: str, method="GET", timeout=90):
    req = urllib.request.Request(SERVER + path, method=method)
    if method == "POST":
        req.add_header("Content-Type", "application/json")
    body = b"{}" if method == "POST" else None
    t0 = time.time()
    with urllib.request.urlopen(req, body, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read(), time.time() - t0


def check_server() -> None:
    section("② 部署的服务器")
    print(f"   {SERVER}\n")
    checks = [
        ("工作台首页", "", "GET"),
        ("前端 app.js", "static/app.js", "GET"),
        ("前端 style.css", "static/style.css", "GET"),
        ("健康检查", "api/health", "GET"),
        ("项目列表", "api/projects", "GET"),
        ("规则表", "api/rules", "GET"),
        ("第 2 页渲染图", "api/projects/demo/pages/2.png?w=600", "GET"),
        ("第 7 页渲染图", "api/projects/demo/pages/7.png?w=600", "GET"),
        ("IR 校验", "api/projects/demo/validate", "POST"),
        ("提示词透明化", "api/projects/demo/prompt/ch2436_P001", "GET"),
    ]
    for name, path, m in checks:
        try:
            s, c, b, dt = fetch(path, m)
            if "image" in c:
                info = f"{len(b) / 1024:.0f} KB"
            elif "json" in c:
                j = json.loads(b)
                info = (f"ok={j['ok']}" if isinstance(j, dict) and "ok" in j
                        else f"{len(j)} 条" if isinstance(j, (list, dict)) else "")
            else:
                info = f"{len(b)} 字节"
            rec(name, s == 200, f"{s}  {info}  {dt:.2f}s")
        except Exception as e:                              # noqa: BLE001
            rec(name, False, str(e)[:70])

    # 页面图是真的 JPEG（顺带确认能出图 —— 缺字体会静默降级成方块）
    try:
        s, c, b, _ = fetch("api/projects/demo/pages/2.png?w=900")
        rec("页面图可下载", s == 200 and b[:2] == b"\xff\xd8", "JPEG")
    except Exception as e:                                  # noqa: BLE001
        rec("页面图可下载", False, str(e)[:60])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--github", action="store_true")
    ap.add_argument("--server", action="store_true")
    a = ap.parse_args()

    print("=" * 64)
    print(" 线上状态验证")
    print("=" * 64)

    only = a.github or a.server
    if a.github or not only:
        check_github()
    if a.server or not only:
        check_server()

    ok = sum(1 for _, o, _ in results if o)
    section("总结")
    for n, o, d in results:
        if not o:
            print(f"   -- {n}  {d}")
    print(f"\n   {ok}/{len(results)} 项通过")
    print("=" * 64)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
