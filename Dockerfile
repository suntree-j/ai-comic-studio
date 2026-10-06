# AI Comic Studio · 一体化镜像（API + 工作台）
# 多阶段构建：前端是纯静态，无需 Node

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    COMIC_PROJECTS=/data/projects

WORKDIR /app

# 中文字体（渲染气泡与标题必须）
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# ── 依赖层（单独一层，改代码不必重装依赖）
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir \
        "pydantic>=2.6" "httpx>=0.27" "Pillow>=10.0" \
        "pymupdf>=1.24" "fastapi>=0.110" "uvicorn[standard]>=0.27"

# ── 源码
COPY packages/ ./packages/
COPY apps/ ./apps/
COPY scripts/ ./scripts/
COPY examples/ ./examples/
COPY docs/ ./docs/

# 预生成示例项目（打开即有内容可看）
RUN python scripts/make_demo_project.py || true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4).status==200 else 1)"

CMD ["python", "-m", "apps.api.server", "--host", "0.0.0.0", "--port", "8000"]
