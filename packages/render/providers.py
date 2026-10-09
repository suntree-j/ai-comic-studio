# -*- coding: utf-8 -*-
"""生图服务 Provider 抽象层

设计原则与 LLM Provider 一致：不绑定任何厂商。
所有具体实现都打成适配器，用户按需选择。

内置适配器：
    MockImageProvider    离线占位图（测试 / Demo，无需任何 key）
    SeedreamProvider     火山方舟 Seedream
    OpenAIImagesProvider OpenAI Images (gpt-image-1 / dall-e-3)
    SDWebUIProvider      本地 Stable Diffusion WebUI (A1111 API)
    GenericHTTPProvider  任意 HTTP 生图接口（用配置描述请求/响应）
"""

from __future__ import annotations

import abc
import base64
import io
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw

# ══════════════════════════════════════════════════════════════════
# 数据
# ══════════════════════════════════════════════════════════════════

SIZE_WIDE = "2048x1400"
SIZE_TALL = "1332x1776"
SIZE_SQUARE = "1536x1536"

KNOWN_SIZES: Dict[str, tuple] = {
    SIZE_WIDE: (2048, 1400),
    SIZE_TALL: (1332, 1776),
    SIZE_SQUARE: (1536, 1536),
}


@dataclass
class ImageRequest:
    prompt: str
    size: str = SIZE_WIDE
    ref_images: List[str] = field(default_factory=list)   # 本地路径或 URL
    negative: str = ""
    seed: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageResult:
    image: Optional[Image.Image]
    model: str = ""
    latency_ms: int = 0
    seed: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.image is not None


class ImageError(RuntimeError):
    pass


class QuotaExceeded(ImageError):
    """额度真的用完了（充钱才行）—— 重试没意义，该停就停"""


class RateLimited(ImageError):
    """**临时**限流（每分钟请求数超了）—— 等一会儿就能过

    ★ 为什么和 QuotaExceeded 分开：
      实测硅基流动返回
        {"code":50604,"message":"... IPM limit reached."}
      这是「你这一分钟发太多了」，不是「你没钱了」。
      一开始两者共用一个异常，于是 9 格里第 8 格撞上限流 →
      整个循环 break → 最后 2 格直接不生成。
      真正该做的是等一下再试。
    """
    """额度耗尽 —— 调用方可据此暂停重试"""


# ══════════════════════════════════════════════════════════════════
# 基类
# ══════════════════════════════════════════════════════════════════

class ImageProvider(abc.ABC):
    name: str = "base"
    #: 是否支持传参考图
    supports_refs: bool = False
    #: 是否支持固定种子（可复现）
    supports_seed: bool = False

    @abc.abstractmethod
    def generate(self, req: ImageRequest) -> ImageResult:
        ...

    # ── 便捷方法 ──────────────────────────────────────────────
    def generate_to_file(self, req: ImageRequest, path: str) -> ImageResult:
        r = self.generate(req)
        if r.ok:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            r.image.save(path, "PNG", optimize=True)
        return r


# ══════════════════════════════════════════════════════════════════
# Mock —— 离线占位图（测试 / 演示）
# ══════════════════════════════════════════════════════════════════

class MockImageProvider(ImageProvider):
    """生成可辨识的占位图，不联网

    用途：
        · 单元测试（渲染流程、气泡布局）
        · Demo 站离线演示
        · 无 API key 时跑通全流程
    占位图内容：渐变底 + 尺寸标注 + 提示词摘要 + 参考图数量
    """

    name = "mock"
    supports_refs = True
    supports_seed = True

    def generate(self, req: ImageRequest) -> ImageResult:
        t0 = time.time()
        w, h = KNOWN_SIZES.get(req.size, (1024, 1024))
        img = Image.new("RGB", (w, h), (232, 236, 242))
        dr = ImageDraw.Draw(img)

        # 对角渐变底（逐层内缩画矩形；步进与上限都要保证不越过中线）
        lim = min(w, h) // 2 - 2
        if lim > 0:
            for i in range(0, lim, max(2, lim // 24)):
                v = 232 - int(28 * i / lim)
                dr.rectangle([i, i, w - i, h - i], outline=(v, v + 4, v + 10))

        # 中央提示块
        dr.rectangle([w // 10, h // 10, w - w // 10, h - h // 10],
                     outline=(150, 160, 175), width=max(2, w // 400))

        try:
            from PIL import ImageFont
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", max(18, w // 46))
            small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", max(14, w // 70))
        except Exception:                                   # pragma: no cover
            font = small = None

        lines = [
            "MOCK PANEL",
            f"{req.size}",
            f"refs: {len(req.ref_images)}",
            "",
        ]
        # 提示词摘要（每 22 字换行）
        p = (req.prompt or "").replace("\n", " ")
        for i in range(0, min(len(p), 220), 22):
            lines.append(p[i:i + 22])

        y = h // 10 + max(20, w // 40)
        for i, ln in enumerate(lines):
            dr.text((w // 10 + 20, y), ln, fill=(90, 100, 115),
                    font=(font if i < 2 else small))
            y += max(26, w // 34)

        return ImageResult(image=img, model="mock", latency_ms=int((time.time() - t0) * 1000),
                           seed=req.seed)


# ══════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════

def to_data_url(path_or_url: str) -> str:
    """本地文件 → data URL；已是 URL 则原样返回"""
    if path_or_url.startswith(("http://", "https://", "data:")):
        return path_or_url
    ext = os.path.splitext(path_or_url)[1].lower().lstrip(".") or "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
            "webp": "webp", "bmp": "bmp"}.get(ext, "png")
    with open(path_or_url, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/{mime};base64,{b64}"


def _img_from_bytes(b: bytes) -> Image.Image:
    return Image.open(io.BytesIO(b)).convert("RGB")


def _check_quota(status: int, text: str) -> None:
    """区分「临时限流」与「额度用尽」

    这是踩出来的：两者混在一起时，一次 IPM 限流会让整批生成停下。
    """
    low = (text or "").lower()
    # 临时限流：429，或报文里明确说是频率
    if status == 429 or "rate limit" in low or "ipm limit" in low \
            or "tpm limit" in low or "too many requests" in low \
            or "限流" in text or "频率" in text:
        raise RateLimited(f"被限流（HTTP {status}）：{text[:300]}")
    # 额度用尽：充钱才行，重试没意义
    if "quota" in low or "insufficient" in low or "balance" in low \
            or "余额" in text or "额度不足" in text:
        raise QuotaExceeded(f"额度不足（HTTP {status}）：{text[:300]}")


# ══════════════════════════════════════════════════════════════════
# 火山方舟 Seedream
# ══════════════════════════════════════════════════════════════════

class SeedreamProvider(ImageProvider):
    """火山方舟 Seedream

    文档里的坑（本项目实战记录）：
        · response_format 必须是 "url"，写 "png" 会 HTTP 400
        · 并行多进程会被限流，务必串行
        · 额度按月重置
    """

    name = "seedream"
    supports_refs = True
    supports_seed = True

    def __init__(self, api_key: Optional[str] = None,
                 model: str = "doubao-seedream-5.0-pro",
                 base_url: str = "https://ark.cn-beijing.volces.com/api/plan/v3",
                 timeout: float = 300.0):
        self.api_key = api_key or os.environ.get("ARK_API_KEY", "")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(self, req: ImageRequest) -> ImageResult:
        if not self.api_key:
            raise ImageError("缺少 ARK_API_KEY")
        try:
            import httpx
        except ImportError as e:                            # pragma: no cover
            raise ImageError("需要 httpx") from e

        body: Dict[str, Any] = {
            "model": self.model,
            "prompt": req.prompt,
            "size": req.size,
            "response_format": "url",        # ★ 不能写 png
            "output_format": "png",
            "watermark": False,
        }
        if req.ref_images:
            body["image"] = [to_data_url(p) for p in req.ref_images]
        if req.seed is not None:
            body["seed"] = req.seed

        t0 = time.time()
        try:
            r = httpx.post(f"{self.base_url}/images/generations",
                           headers={"Authorization": f"Bearer {self.api_key}",
                                    "Content-Type": "application/json"},
                           json=body, timeout=self.timeout)
        except Exception as e:                              # noqa: BLE001
            raise ImageError(f"请求失败：{e}") from e

        _check_quota(r.status_code, r.text)
        if r.status_code >= 400:
            raise ImageError(f"HTTP {r.status_code}：{r.text[:400]}")

        data = r.json()
        items = data.get("data") or []
        if not items:
            raise ImageError(f"响应无图片：{json.dumps(data)[:300]}")
        item = items[0]

        if item.get("b64_json"):
            img = _img_from_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            try:
                rr = httpx.get(item["url"], timeout=self.timeout)
                rr.raise_for_status()
            except Exception as e:                          # noqa: BLE001
                raise ImageError(f"下载图片失败：{e}") from e
            img = _img_from_bytes(rr.content)
        else:
            raise ImageError("响应里既没有 url 也没有 b64_json")

        return ImageResult(image=img, model=data.get("model", self.model),
                           latency_ms=int((time.time() - t0) * 1000),
                           seed=item.get("seed"), raw=data)


# ══════════════════════════════════════════════════════════════════
# 硅基流动 SiliconFlow
# ══════════════════════════════════════════════════════════════════

#: 硅基流动的像素总量上限（实测 2048x1152 = 2359296 会报 400）
SF_MAX_PIXELS = 2_073_600

#: 我们内部的三种尺寸 → 硅基流动支持的 image_size
#: 按比例取最接近的、且不超过像素上限的档位
SF_SIZE_MAP: Dict[str, str] = {
    SIZE_WIDE: "1536x1024",     # 1.46 → 1.50
    SIZE_TALL: "1024x1536",     # 0.75 → 0.67
    SIZE_SQUARE: "1440x1440",   # 1.00 → 1.00
}


class SiliconFlowProvider(ImageProvider):
    """硅基流动（SiliconFlow）

    为什么选它：国内可直连、有免费额度、同时提供 LLM 与生图，
    一个 key 就能把「小说 → 漫画」整条链路跑通。

    文档里的坑（本项目实测记录）：
        · 字段是 `image_size`（不是 OpenAI 的 `size`），
          形如 "1024x1024" 的字符串
        · **像素总量上限 2073600** —— 2048x1152 会返回
          `width * height should not exceed 2073600`
        · 响应里图片在 `images[].url`（**不是** OpenAI 的 `data[].url`），
          同时 `data` 字段也在，但结构与 OpenAI 不同，别按 OpenAI 解析
        · `model` 必须在账号可用列表里，不可用的返回
          `{"code":30003,"message":"Model disabled."}`
        · 试过可用：Qwen/Qwen-Image、Kwai-Kolors/Kolors、
          Tongyi-MAI/Z-Image-Turbo
    """

    name = "siliconflow"
    supports_refs = True        # Qwen-Image-Edit 支持参考图
    supports_seed = True

    def __init__(self, api_key: Optional[str] = None,
                 model: str = "Qwen/Qwen-Image",
                 base_url: str = "https://api.siliconflow.cn/v1",
                 timeout: float = 300.0,
                 steps: int = 30,
                 guidance: float = 4.0):
        self.api_key = (api_key or os.environ.get("SILICONFLOW_API_KEY", "")
                        or os.environ.get("SF_API_KEY", ""))
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.steps = steps
        self.guidance = guidance

    @staticmethod
    def map_size(size: str) -> str:
        """把内部尺寸映射到硅基流动支持的档位

        认不出来就按传入值解析，并确保不超过像素上限
        （超了就等比缩小到刚好达标）。
        """
        if size in SF_SIZE_MAP:
            return SF_SIZE_MAP[size]
        try:
            w, h = (int(x) for x in size.lower().split("x"))
        except Exception:                                   # noqa: BLE001
            return "1024x1024"
        if w * h > SF_MAX_PIXELS:
            k = (SF_MAX_PIXELS / (w * h)) ** 0.5
            w, h = int(w * k) // 16 * 16, int(h * k) // 16 * 16
        return f"{max(256, w)}x{max(256, h)}"

    def generate(self, req: ImageRequest) -> ImageResult:
        if not self.api_key:
            raise ImageError("缺少 SILICONFLOW_API_KEY")
        try:
            import httpx
        except ImportError as e:                            # pragma: no cover
            raise ImageError("需要 httpx") from e

        size = self.map_size(req.size)
        body: Dict[str, Any] = {
            "model": self.model,
            "prompt": req.prompt,
            "image_size": size,          # ★ 不是 "size"
            "batch_size": 1,
            "num_inference_steps": self.steps,
            "guidance_scale": self.guidance,
        }
        # 负面提示词：硅基流动用 negative_prompt（部分模型支持）
        if req.negative:
            body["negative_prompt"] = req.negative
        if req.seed is not None:
            body["seed"] = req.seed
        # 参考图：Qwen-Image-Edit 系列支持
        if req.ref_images:
            body["image"] = to_data_url(req.ref_images[0])

        t0 = time.time()
        try:
            r = httpx.post(f"{self.base_url}/images/generations",
                           headers={"Authorization": f"Bearer {self.api_key}",
                                    "Content-Type": "application/json"},
                           json=body, timeout=self.timeout)
        except Exception as e:                              # noqa: BLE001
            raise ImageError(f"请求失败：{e}") from e

        # 模型不可用是「配置错」而不是额度问题，单独给清晰提示
        if r.status_code == 403:
            try:
                msg = r.json().get("message", "")
            except Exception:                               # noqa: BLE001
                msg = r.text[:200]
            if "disabled" in msg.lower():
                raise ImageError(
                    f"模型 {self.model} 在该账号不可用（Model disabled）。"
                    f"可用模型见 GET /v1/models?type=image")
        _check_quota(r.status_code, r.text)
        if r.status_code >= 400:
            raise ImageError(f"HTTP {r.status_code}（image_size={size}）："
                             f"{r.text[:400]}")

        data = r.json()
        items = data.get("images") or []
        if not items:
            raise ImageError(f"响应无图片：{json.dumps(data)[:300]}")
        url = items[0].get("url")
        if not url:
            raise ImageError(f"响应里没有 url：{json.dumps(items[0])[:300]}")

        try:
            rr = httpx.get(url, timeout=self.timeout, follow_redirects=True)
            rr.raise_for_status()
        except Exception as e:                              # noqa: BLE001
            raise ImageError(f"下载图片失败：{e}") from e

        return ImageResult(image=_img_from_bytes(rr.content),
                           model=self.model,
                           latency_ms=int((time.time() - t0) * 1000),
                           seed=data.get("seed"), raw=data)


# ══════════════════════════════════════════════════════════════════
# OpenAI Images
# ══════════════════════════════════════════════════════════════════

class OpenAIImagesProvider(ImageProvider):
    name = "openai"
    supports_refs = True
    supports_seed = False

    def __init__(self, api_key: Optional[str] = None,
                 model: str = "gpt-image-1",
                 base_url: str = "https://api.openai.com/v1",
                 timeout: float = 300.0):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(self, req: ImageRequest) -> ImageResult:
        if not self.api_key:
            raise ImageError("缺少 OPENAI_API_KEY")
        try:
            import httpx
        except ImportError as e:                            # pragma: no cover
            raise ImageError("需要 httpx") from e

        # OpenAI 的尺寸词表与我们的不同，做一次映射
        w, h = KNOWN_SIZES.get(req.size, (1024, 1024))
        if w > h * 1.2:
            oai_size = "1536x1024"
        elif h > w * 1.2:
            oai_size = "1024x1536"
        else:
            oai_size = "1024x1024"

        body: Dict[str, Any] = {
            "model": self.model,
            "prompt": req.prompt,
            "size": oai_size,
            "n": 1,
        }

        t0 = time.time()
        try:
            r = httpx.post(f"{self.base_url}/images/generations",
                           headers={"Authorization": f"Bearer {self.api_key}"},
                           json=body, timeout=self.timeout)
        except Exception as e:                              # noqa: BLE001
            raise ImageError(f"请求失败：{e}") from e

        _check_quota(r.status_code, r.text)
        if r.status_code >= 400:
            raise ImageError(f"HTTP {r.status_code}：{r.text[:400]}")

        data = r.json()
        item = (data.get("data") or [{}])[0]
        if item.get("b64_json"):
            img = _img_from_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            rr = httpx.get(item["url"], timeout=self.timeout)
            rr.raise_for_status()
            img = _img_from_bytes(rr.content)
        else:
            raise ImageError("响应无图片")

        return ImageResult(image=img, model=self.model,
                           latency_ms=int((time.time() - t0) * 1000), raw=data)


# ══════════════════════════════════════════════════════════════════
# 本地 Stable Diffusion WebUI (A1111)
# ══════════════════════════════════════════════════════════════════

class SDWebUIProvider(ImageProvider):
    name = "sd-webui"
    supports_refs = False        # 需装 ControlNet 才能用参考图
    supports_seed = True

    def __init__(self, base_url: str = "http://127.0.0.1:7860",
                 steps: int = 28, cfg_scale: float = 6.0,
                 sampler: str = "DPM++ 2M Karras", timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.steps = steps
        self.cfg_scale = cfg_scale
        self.sampler = sampler
        self.timeout = timeout

    def generate(self, req: ImageRequest) -> ImageResult:
        try:
            import httpx
        except ImportError as e:                            # pragma: no cover
            raise ImageError("需要 httpx") from e

        w, h = KNOWN_SIZES.get(req.size, (1024, 1024))
        payload = {
            "prompt": req.prompt,
            "negative_prompt": req.negative or "文字, 水印, 低质量, 多手, 多指",
            "width": w, "height": h,
            "steps": self.steps, "cfg_scale": self.cfg_scale,
            "sampler_name": self.sampler,
            "seed": req.seed if req.seed is not None else -1,
        }
        t0 = time.time()
        try:
            r = httpx.post(f"{self.base_url}/sdapi/v1/txt2img",
                           json=payload, timeout=self.timeout)
        except Exception as e:                              # noqa: BLE001
            raise ImageError(f"连接 SD WebUI 失败（{self.base_url}）：{e}") from e

        _check_quota(r.status_code, r.text)
        if r.status_code >= 400:
            raise ImageError(f"HTTP {r.status_code}：{r.text[:300]}")

        data = r.json()
        imgs = data.get("images") or []
        if not imgs:
            raise ImageError("SD 未返回图片")
        b64 = imgs[0].split(",", 1)[-1]
        img = _img_from_bytes(base64.b64decode(b64))

        seed = None
        try:
            info = json.loads(data.get("info") or "{}")
            seed = info.get("seed")
        except Exception:                                   # noqa: BLE001
            pass

        return ImageResult(image=img, model="sd-webui",
                           latency_ms=int((time.time() - t0) * 1000),
                           seed=seed, raw={"info": data.get("info", "")[:200]})


# ══════════════════════════════════════════════════════════════════
# 通用 HTTP（用户自己描述请求/响应）
# ══════════════════════════════════════════════════════════════════

class GenericHTTPProvider(ImageProvider):
    """用配置描述任意生图接口

    例（自建服务）：
        GenericHTTPProvider(
            url="https://my-api/generate",
            headers={"X-Token": "..."},
            body_template={"prompt": "{prompt}", "w": 1024, "h": 768},
            image_path="data.0.url",      # 支持点号路径，列表用数字
        )
    """

    name = "generic-http"
    supports_refs = True
    supports_seed = True

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None,
                 body_template: Optional[Dict[str, Any]] = None,
                 image_path: str = "data.0.url",
                 timeout: float = 300.0):
        self.url = url
        self.headers = headers or {}
        self.body_template = body_template or {"prompt": "{prompt}",
                                               "size": "{size}"}
        self.image_path = image_path
        self.timeout = timeout

    @staticmethod
    def _dig(obj: Any, path: str) -> Any:
        cur = obj
        for part in path.split("."):
            if isinstance(cur, list):
                cur = cur[int(part)]
            else:
                cur = cur[part]
        return cur

    def _fill(self, tpl: Any, req: ImageRequest, w: int, h: int) -> Any:
        if isinstance(tpl, dict):
            return {k: self._fill(v, req, w, h) for k, v in tpl.items()}
        if isinstance(tpl, list):
            return [self._fill(v, req, w, h) for v in tpl]
        if isinstance(tpl, str):
            return (tpl.replace("{prompt}", req.prompt)
                       .replace("{size}", req.size)
                       .replace("{width}", str(w))
                       .replace("{height}", str(h))
                       .replace("{negative}", req.negative))
        return tpl

    def generate(self, req: ImageRequest) -> ImageResult:
        try:
            import httpx
        except ImportError as e:                            # pragma: no cover
            raise ImageError("需要 httpx") from e

        w, h = KNOWN_SIZES.get(req.size, (1024, 1024))
        body = self._fill(self.body_template, req, w, h)
        if req.ref_images:
            body["ref_images"] = [to_data_url(p) for p in req.ref_images]

        t0 = time.time()
        try:
            r = httpx.post(self.url, headers=self.headers, json=body,
                           timeout=self.timeout)
        except Exception as e:                              # noqa: BLE001
            raise ImageError(f"请求失败：{e}") from e

        _check_quota(r.status_code, r.text)
        if r.status_code >= 400:
            raise ImageError(f"HTTP {r.status_code}：{r.text[:300]}")

        data = r.json()
        val = self._dig(data, self.image_path)
        if isinstance(val, str) and val.startswith("data:"):
            img = _img_from_bytes(base64.b64decode(val.split(",", 1)[-1]))
        elif isinstance(val, str) and val.startswith("http"):
            rr = httpx.get(val, timeout=self.timeout)
            rr.raise_for_status()
            img = _img_from_bytes(rr.content)
        elif isinstance(val, str):
            img = _img_from_bytes(base64.b64decode(val))
        else:
            raise ImageError(f"无法从 {self.image_path} 取得图片")

        return ImageResult(image=img, model="generic-http",
                           latency_ms=int((time.time() - t0) * 1000), raw=data)


# ══════════════════════════════════════════════════════════════════
# 工厂
# ══════════════════════════════════════════════════════════════════

IMAGE_PROVIDER_NAMES = ["mock", "siliconflow", "seedream", "openai",
                        "sd-webui", "generic-http"]


def get_image_provider(name: str = "mock", **kwargs) -> ImageProvider:
    """按名字取生图 Provider

    >>> get_image_provider("mock")
    >>> get_image_provider("siliconflow", api_key="sk-...")
    >>> get_image_provider("seedream", api_key="...")
    >>> get_image_provider("sd-webui", base_url="http://127.0.0.1:7860")
    """
    table = {
        "mock": MockImageProvider,
        "siliconflow": SiliconFlowProvider,
        "seedream": SeedreamProvider,
        "openai": OpenAIImagesProvider,
        "sd-webui": SDWebUIProvider,
        "generic-http": GenericHTTPProvider,
    }
    cls = table.get(name)
    if cls is None:
        raise ImageError(f"未知生图 provider {name!r}；可用：{IMAGE_PROVIDER_NAMES}")
    return cls(**kwargs)                                    # type: ignore[arg-type]
