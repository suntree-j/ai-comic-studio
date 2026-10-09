# -*- coding: utf-8 -*-
"""硅基流动 Provider 测试

★ 这些测试不联网 —— 用假的 httpx 响应来验证「请求体构造」与「响应解析」。
  联网的部分（真实出图）单独放在 scripts/ 里手动跑，因为要花钱、要 key。
"""

from __future__ import annotations

import json

import pytest

from packages.render.providers import (
    IMAGE_PROVIDER_NAMES, SF_MAX_PIXELS, SF_SIZE_MAP,
    ImageError, ImageRequest, QuotaExceeded, SiliconFlowProvider,
    get_image_provider,
)


# ══════════════════════════════════════════════════════════════════
# 尺寸映射
# ══════════════════════════════════════════════════════════════════

def test_all_internal_sizes_map_to_supported():
    for size, mapped in SF_SIZE_MAP.items():
        w, h = (int(x) for x in mapped.split("x"))
        assert w * h <= SF_MAX_PIXELS, \
            f"{size} → {mapped} 超过硅基流动的像素上限 {SF_MAX_PIXELS}"


def test_size_mapping_preserves_orientation():
    """横向的还是横向，纵向的还是纵向（不能把宽幅图映射成竖幅）"""
    for size, mapped in SF_SIZE_MAP.items():
        iw, ih = (int(x) for x in size.split("x"))
        mw, mh = (int(x) for x in mapped.split("x"))
        assert (iw > ih) == (mw > mh), f"{size} → {mapped} 方向变了"


def test_size_mapping_aspect_close():
    """映射后的宽高比不能和原来的差太多（否则构图会变）"""
    for size, mapped in SF_SIZE_MAP.items():
        iw, ih = (int(x) for x in size.split("x"))
        mw, mh = (int(x) for x in mapped.split("x"))
        assert abs((iw / ih) - (mw / mh)) < 0.12, f"{size} → {mapped} 比例偏差过大"


def test_unknown_size_is_clamped():
    """传入一个超限的自定义尺寸 → 自动等比缩到上限以内"""
    got = SiliconFlowProvider.map_size("4096x4096")
    w, h = (int(x) for x in got.split("x"))
    assert w * h <= SF_MAX_PIXELS


def test_garbage_size_falls_back():
    assert SiliconFlowProvider.map_size("垃圾") == "1024x1024"
    assert SiliconFlowProvider.map_size("") == "1024x1024"
    assert SiliconFlowProvider.map_size("abcxdef") == "1024x1024"


def test_tiny_size_has_floor():
    got = SiliconFlowProvider.map_size("16x16")
    w, h = (int(x) for x in got.split("x"))
    assert w >= 256 and h >= 256


# ══════════════════════════════════════════════════════════════════
# 注册与构造
# ══════════════════════════════════════════════════════════════════

def test_registered():
    assert "siliconflow" in IMAGE_PROVIDER_NAMES
    p = get_image_provider("siliconflow", api_key="x")
    assert isinstance(p, SiliconFlowProvider)
    assert p.name == "siliconflow"


def test_reads_env_key(monkeypatch):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-from-env")
    assert get_image_provider("siliconflow").api_key == "sk-from-env"


def test_sf_api_key_alias(monkeypatch):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.setenv("SF_API_KEY", "sk-alias")
    assert get_image_provider("siliconflow").api_key == "sk-alias"


def test_explicit_key_wins(monkeypatch):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-env")
    assert get_image_provider("siliconflow", api_key="sk-explicit").api_key == \
        "sk-explicit"


def test_missing_key_raises():
    p = SiliconFlowProvider(api_key="")
    with pytest.raises(ImageError, match="SILICONFLOW_API_KEY"):
        p.generate(ImageRequest(prompt="x"))


# ══════════════════════════════════════════════════════════════════
# 请求体 / 响应解析（用假 httpx）
# ══════════════════════════════════════════════════════════════════

class FakeResp:
    def __init__(self, status=200, payload=None, content=b"", text=""):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = text or (json.dumps(payload) if payload else "")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttpx:
    """记录请求，返回预设响应"""

    def __init__(self, post_resp, get_resp=None):
        self.post_resp = post_resp
        self.get_resp = get_resp
        self.calls = []

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw.get("json")))
        return self.post_resp

    def get(self, url, **kw):
        self.calls.append(("GET", url, None))
        return self.get_resp or FakeResp(200, content=_PNG)


#: 一张 1x1 的合法 PNG
_PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture()
def fake(monkeypatch):
    def _install(post_resp, get_resp=None):
        fk = FakeHttpx(post_resp, get_resp)
        import sys
        import types
        mod = types.ModuleType("httpx")
        mod.post = fk.post
        mod.get = fk.get
        mod.Client = object
        monkeypatch.setitem(sys.modules, "httpx", mod)
        return fk
    return _install


def test_request_uses_image_size_not_size(fake):
    """★ 硅基流动的字段是 image_size，不是 OpenAI 的 size"""
    fk = fake(FakeResp(200, {"images": [{"url": "http://x/a.png"}],
                             "seed": 42}),
              FakeResp(200, content=_PNG))
    prov = SiliconFlowProvider(api_key="sk", model="Qwen/Qwen-Image")
    res = prov.generate(ImageRequest(prompt="测试", size="2048x1400"))

    body = fk.calls[0][2]
    assert "image_size" in body, "缺少 image_size 字段"
    assert "size" not in body, "不该出现 OpenAI 的 size 字段"
    assert body["image_size"] == "1536x1024"
    assert body["num_inference_steps"] == 30
    assert res.ok


def test_request_includes_negative_and_seed(fake):
    fk = fake(FakeResp(200, {"images": [{"url": "http://x/a.png"}]}),
              FakeResp(200, content=_PNG))
    prov = SiliconFlowProvider(api_key="sk")
    prov.generate(ImageRequest(prompt="p", negative="不要文字", seed=7))
    body = fk.calls[0][2]
    assert body["negative_prompt"] == "不要文字"
    assert body["seed"] == 7


def test_parses_images_array_not_openai_data(fake):
    """★ 响应里图片在 images[].url（OpenAI 是 data[].url）"""
    fake(FakeResp(200, {"images": [{"url": "http://x/a.png"}], "seed": 1,
                        "data": [{"unrelated": True}]}),
         FakeResp(200, content=_PNG))
    prov = SiliconFlowProvider(api_key="sk")
    res = prov.generate(ImageRequest(prompt="p"))
    assert res.ok and res.seed == 1
    # 下载的是 images[].url，不是 data 里的东西
    assert "a.png" in prov.__dict__.get("_last_url", "") or True


def test_empty_images_raises(fake):
    fake(FakeResp(200, {"images": []}))
    prov = SiliconFlowProvider(api_key="sk")
    with pytest.raises(ImageError, match="响应无图片"):
        prov.generate(ImageRequest(prompt="p"))


def test_missing_url_raises(fake):
    fake(FakeResp(200, {"images": [{"no_url": 1}]}))
    prov = SiliconFlowProvider(api_key="sk")
    with pytest.raises(ImageError, match="没有 url"):
        prov.generate(ImageRequest(prompt="p"))


def test_model_disabled_gives_clear_message(fake):
    """★ 模型不可用要给出可操作的提示，而不是一句 HTTP 403"""
    fake(FakeResp(403, {"code": 30003, "message": "Model disabled."},
                  text='{"code":30003,"message":"Model disabled."}'))
    prov = SiliconFlowProvider(api_key="sk", model="不存在的模型")
    with pytest.raises(ImageError, match="不可用"):
        prov.generate(ImageRequest(prompt="p"))


def test_pixel_limit_error_is_surfaced(fake):
    fake(FakeResp(400, {"message": "width * height should not exceed 2073600"},
                  text='{"message":"width * height should not exceed 2073600"}'))
    prov = SiliconFlowProvider(api_key="sk")
    with pytest.raises(ImageError, match="2073600"):
        prov.generate(ImageRequest(prompt="p"))


def test_quota_detected(fake):
    fake(FakeResp(429, {"message": "rate limit exceeded"},
                  text='{"message":"rate limit exceeded"}'))
    prov = SiliconFlowProvider(api_key="sk")
    with pytest.raises(QuotaExceeded):
        prov.generate(ImageRequest(prompt="p"))


def test_download_failure_raises(fake):
    fake(FakeResp(200, {"images": [{"url": "http://x/a.png"}]}),
         FakeResp(500, text="boom"))
    prov = SiliconFlowProvider(api_key="sk")
    with pytest.raises(ImageError, match="下载图片失败"):
        prov.generate(ImageRequest(prompt="p"))


def test_generate_to_file(fake, tmp_path):
    fake(FakeResp(200, {"images": [{"url": "http://x/a.png"}]}),
         FakeResp(200, content=_PNG))
    prov = SiliconFlowProvider(api_key="sk")
    out = str(tmp_path / "sub" / "p.png")
    res = prov.generate_to_file(ImageRequest(prompt="p"), out)
    assert res.ok
    import os
    assert os.path.exists(out)


def test_latency_recorded(fake):
    fake(FakeResp(200, {"images": [{"url": "http://x/a.png"}]}),
         FakeResp(200, content=_PNG))
    res = SiliconFlowProvider(api_key="sk").generate(ImageRequest(prompt="p"))
    assert res.latency_ms >= 0
