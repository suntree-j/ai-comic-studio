# 部署指南

## 在线 Demo（已部署 ✅）

**http://36.151.150.140/comic/**

- 服务器：京东云 4 核 / 16 GB / 100 GB SSD，Ubuntu 24.04
- 部署方式：venv + systemd（监听 `127.0.0.1:8500`）+ Nginx 子路径 `/comic/`
- 生图服务：`mock`（零成本，避免公网被刷额度）
- 一键重新部署：`python scripts/deploy_remote.py`

> ⚠️ **这台服务器同时跑着另一个生产系统**（data-platform：Doris / Airflow /
> MinIO / MySQL 等 11 个容器，占用 80 端口与 `/data/` `/airflow/` `/grafana/` 等子路径）。
> 本应用**只追加**一个 `location /comic/`，不覆盖任何现有配置，
> 且每次改动前都会备份 `data-platform.conf`。

---

三种部署方式，按需选择。

---

## 方式一 · 本地直跑（最快，无需 Docker）

```bash
git clone <repo> && cd ai-comic-studio
pip install -e ".[dev,web]"

# 生成示例项目
python scripts/make_demo_project.py
python scripts/make_showcase_project.py     # 可选：用真实素材

# 起服务
python -m apps.api.server --port 8000
# 打开 http://127.0.0.1:8000
```

**依赖**：Python 3.10+。无 Node、无前端构建。

> **中文字体是硬需求**：缺字体时气泡里的中文会渲染成方块。
> Linux 上先装：`apt-get install -y fonts-wqy-microhei`（约 4 MB）
> 或 `fonts-noto-cjk`。也可用 `COMIC_FONT=/path/to/font.ttf` 显式指定。

---

## 生产实况：子路径部署（/comic/）

服务器 80 端口是唯一对外入口且已被占用，所以本应用挂在子路径下。

### 路径推导链（每一环都不能错）

```
① 浏览器  http://IP/comic/
② 服务端返回 HTML，注入  <base href="/comic/">
③ HTML 里写  "static/app.js"（相对路径）
   → 浏览器按 base 解析成  /comic/static/app.js
④ nginx   location /comic/ { proxy_pass http://127.0.0.1:8500/; }
   —— proxy_pass 结尾的 / 负责**剥掉** /comic 前缀
   → 应用收到  /static/app.js
⑤ 应用  StaticFiles(directory="apps/workbench") 挂在 /static
   → 命中文件  apps/workbench/app.js
```

**关键点**：`--base-path` 只影响注入的 `<base href>`，不影响应用内部路由。
应用本身仍然跑在根路径，由 Nginx 负责剥前缀 —— 这样应用代码不需要知道部署路径。

### 验证

```bash
python scripts/check_subpath.py     # 本地模拟子路径模式
python scripts/deploy_remote.py --verify   # 远程验证 9 项
```

### 部署踩到的三个坑（都已修 + 已加测试）

| 坑 | 现象 | 根因 | 现状 |
|---|---|---|---|
| **缺 numpy** | 服务启动即 `ModuleNotFoundError` | `pyproject.toml` 的 dependencies 漏了 numpy（`bubble.py` 用到）；本地早装好所以没暴露 | 已补；`tests/test_packaging.py` 静态检查「所有第三方 import 都已声明」 |
| **缺中文字体** | 气泡中文全变**方块**，但接口返回 200、图片正常生成 | 服务器只有 DejaVu；旧 `load_font` 找不到中文字体就静默回落 `load_default()` | 装 `fonts-wqy-microhei`；`load_font` 改为**大声警告**；`tests/test_fonts.py` 12 项 |
| **字体探测失效** | 装了字体仍被判为「不含中文」 | `_has_cjk` 用 `mask.tobytes()`，**Pillow 12.3 移除了该 API**，异常被吞 → 所有字体都判 False | 改为查 cmap（fontTools）；`fonttools` 升为必需依赖 |

> 第二个坑最值得记：**静默降级**比报错危险得多 ——
> 接口全绿、日志干净，只有肉眼看图才发现全是方块。
> 现在 `render_page` 找不到中文字体会发 `RuntimeWarning`，
> 部署脚本也会单独验证这一项。

---

## 方式二 · Docker Compose（推荐给服务器）

```bash
cp .env.example .env        # 按需填 key
docker compose up -d
# 打开 http://<服务器IP>:8000
```

### 镜像里有什么

| 项 | 说明 |
|---|---|
| 基础镜像 | `python:3.12-slim` |
| 中文字体 | 装了 `fonts-noto-cjk`（渲染气泡与标题必须） |
| 预生成示例 | 构建时跑了一次 `make_demo_project.py`，打开即有内容 |
| 健康检查 | 每 30 秒打一次 `/api/health` |
| 数据持久化 | volume `comic-projects` → 容器内 `/data/projects` |

### 接真实生图服务

```bash
# .env
COMIC_IMAGE_PROVIDER=seedream
ARK_API_KEY=ark-xxxxxxxx
```

```bash
docker compose up -d --force-recreate
```

支持的值：`mock` / `seedream` / `openai` / `sd-webui` / `generic-http`

> **建议**：公网 Demo 站用 `mock`。真实出图贵且慢，
> 公开接口容易被刷。真实出图放到本地或内网用。

---

## 方式三 · 反向代理（HTTPS）

Nginx 配置：

```nginx
server {
    listen 443 ssl http2;
    server_name comic.example.com;

    ssl_certificate     /etc/letsencrypt/live/comic.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/comic.example.com/privkey.pem;

    client_max_body_size 32m;      # 项目 JSON 可能较大

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;    # 出图/导出可能较慢
    }
}
```

---

## 公网 Demo 站的成本控制

出图 API 是**最贵的部分**（约 90 秒/张）。公开 Demo 必须控制：

| 手段 | 做法 |
|---|---|
| **默认 mock** | `COMIC_IMAGE_PROVIDER=mock` —— 零成本，功能完整可演示 |
| **预置案例** | 构建时生成 demo/showcase 项目，浏览不消耗额度 |
| **只读优先** | 演示站可以只开放浏览 + 工作台编辑，不开放 `/render` |
| **限流** | 在 Nginx 层对 `POST /api/projects/*/render` 加 `limit_req` |
| **出图缓存** | `RenderStudio` 已实现：素材存在就跳过，不会重复出图 |

### 只读演示模式的 Nginx 片段

```nginx
# 禁止公网触发出图
location ~ ^/api/projects/[^/]+/render$ {
    deny all;
    return 403;
}
```

### 限流示例

```nginx
limit_req_zone $binary_remote_addr zone=render:10m rate=1r/m;

location ~ ^/api/projects/[^/]+/render$ {
    limit_req zone=render burst=2 nodelay;
    proxy_pass http://127.0.0.1:8000;
}
```

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `COMIC_PROJECTS` | `./projects` | 项目数据目录 |
| `COMIC_IMAGE_PROVIDER` | `mock` | 生图服务 |
| `ARK_API_KEY` | — | 火山方舟 |
| `OPENAI_API_KEY` | — | OpenAI（生图或 LLM） |
| `DEEPSEEK_API_KEY` | — | DeepSeek LLM |
| `MOONSHOT_API_KEY` | — | Moonshot LLM |
| `DASHSCOPE_API_KEY` | — | 通义千问 LLM |

---

## 数据与备份

```
/data/projects/<项目名>/
├── bible.json        角色/场景/技能/风格
├── storyboard.json   分镜
├── dialogue.json     对白
├── layout.json       页序（冻结）
├── source.txt        原文（IR-007 校验用）
├── assets/panels/    单格素材
└── out/              导出产物（PDF / 长图）
```

**备份只需打包 `<项目名>/` 目录**（不含 assets 也可以 —— 素材可由 IR 重新生成）。

```bash
docker run --rm -v comic-projects:/data -v $PWD:/backup alpine \
    tar czf /backup/projects-$(date +%F).tar.gz -C /data .
```

---

## 未验证项（诚实说明）

| 项 | 状态 |
|---|---|
| **镜像实际构建** | Dockerfile 已通过语法与逻辑检查；**本机 Docker 守护进程未启动，未实测 build** |
| **公网部署** | 未执行（无服务器） |

要验证镜像构建：

```bash
docker compose build          # 首次约 3–5 分钟
docker compose up -d
curl http://localhost:8000/api/health
```

预期输出：

```json
{"ok": true, "provider": "mock", "providers": ["mock","seedream","openai","sd-webui","generic-http"]}
```
