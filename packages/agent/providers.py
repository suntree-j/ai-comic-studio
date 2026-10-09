# -*- coding: utf-8 -*-
"""LLM Provider 抽象层

设计原则：
    ① 不绑定任何厂商 —— 所有外部服务都走适配器
    ② 结构化输出优先 —— 优先用原生 JSON Schema 模式，退化到「提示词约束 + 解析」
    ③ 可 Mock —— 测试与 Demo 可完全离线运行
"""

from __future__ import annotations

import abc
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


# ══════════════════════════════════════════════════════════════════
# 消息与响应
# ══════════════════════════════════════════════════════════════════

@dataclass
class Message:
    role: str            # system / user / assistant
    content: str

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def cost_hint(self) -> str:
        return f"{self.input_tokens}+{self.output_tokens} tok / {self.latency_ms}ms"


class LLMError(RuntimeError):
    pass


class JSONParseError(LLMError):
    """模型没吐出合法 JSON"""


# ══════════════════════════════════════════════════════════════════
# JSON 提取（各家模型都可能把 JSON 包在 markdown 里）
# ══════════════════════════════════════════════════════════════════

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """从模型输出里稳健地取出 JSON

    依次尝试：
        ① 整段就是 JSON
        ② ```json ... ``` 代码块
        ③ 第一个 { 到最后一个 } 之间的内容
    """
    text = (text or "").strip()
    if not text:
        raise JSONParseError("模型返回空内容")

    # ①
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # ②
    m = _FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # ③
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise JSONParseError(f"JSON 解析失败：{e}") from e

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise JSONParseError(f"JSON 解析失败：{e}") from e

    raise JSONParseError("输出里找不到 JSON")


def parse_as(text: str, model_cls: Type[T]) -> T:
    """把模型输出解析成 Pydantic 模型"""
    data = extract_json(text)
    if not isinstance(data, dict):
        raise JSONParseError(f"期望 JSON 对象，得到 {type(data).__name__}")
    return model_cls.model_validate(data)


# ══════════════════════════════════════════════════════════════════
# Provider 基类
# ══════════════════════════════════════════════════════════════════

class LLMProvider(abc.ABC):
    """所有 LLM 适配器的基类"""

    name: str = "base"
    supports_json_schema: bool = False

    @abc.abstractmethod
    def chat(
        self,
        messages: List[Message],
        *,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        json_schema: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        """发一轮对话，返回文本"""

    def structured(
        self,
        messages: List[Message],
        model_cls: Type[T],
        *,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        retries: int = 2,
    ) -> T:
        """要求模型输出符合 model_cls 的 JSON

        若 provider 支持原生 json_schema 就直接用；
        否则在提示词末尾追加 Schema 说明，并重试解析。
        """
        schema = model_cls.model_json_schema()
        msgs = list(messages)

        if not self.supports_json_schema:
            msgs = msgs + [Message(
                role="user",
                content=("只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。\n"
                         "必须符合以下 JSON Schema：\n"
                         + json.dumps(schema, ensure_ascii=False, indent=1)),
            )]

        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            resp = self.chat(
                msgs,
                temperature=temperature,
                max_tokens=max_tokens,
                json_schema=schema if self.supports_json_schema else None,
            )
            try:
                return parse_as(resp.text, model_cls)
            except Exception as e:                       # noqa: BLE001
                last_err = e
                if attempt >= retries:
                    break
                msgs = msgs + [
                    Message(role="assistant", content=resp.text[:2000]),
                    Message(role="user",
                            content=f"上面的输出不是合法 JSON：{e}\n请只输出合法 JSON。"),
                ]
        raise LLMError(f"{model_cls.__name__} 结构化输出失败：{last_err}")


# ══════════════════════════════════════════════════════════════════
# OpenAI 兼容 Provider（覆盖 OpenAI / DeepSeek / Moonshot / 本地 vLLM…）
# ══════════════════════════════════════════════════════════════════

class OpenAICompatProvider(LLMProvider):
    """任何兼容 /v1/chat/completions 的服务都能用

    例：
        OpenAICompatProvider(model="gpt-4o-mini",
                             base_url="https://api.openai.com/v1",
                             api_key=os.environ["OPENAI_API_KEY"])

        OpenAICompatProvider(model="deepseek-chat",
                             base_url="https://api.deepseek.com/v1",
                             api_key=os.environ["DEEPSEEK_API_KEY"])

        OpenAICompatProvider(model="qwen2.5:14b",
                             base_url="http://localhost:11434/v1",
                             api_key="ollama")
    """

    supports_json_schema = True

    def __init__(self, model: str, base_url: str, api_key: str,
                 timeout: float = 300.0, name: str = "openai-compat",
                 net_retries: int = 2):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        #: ★ 默认 300s 而不是 120s：
        #:   实测 DeepSeek-V3.2 处理 2600 字原文的提示词要 60-90s，
        #:   峰值会超过 120s，于是 httpx 读超时 →
        #:   而 repair_until_valid 里 provider 的 retries=0（重试权归修复循环），
        #:   一次超时就白吃一轮，两轮就把整章判成 pending_human。
        self.timeout = timeout
        #: ★ 网络层重试（超时 / 连不上）。
        #:   注意这**不是**「让模型重答」—— 那由 repair 循环负责。
        #:   两者分开，才不会「两层重试互相吃掉轮次」。
        #:   4xx 不重试（那是请求本身的问题，重试也是白搭）。
        self.net_retries = net_retries
        self.name = name

    def chat(self, messages, *, temperature=0.3, max_tokens=8192,
             json_schema=None) -> LLMResponse:
        try:
            import httpx
        except ImportError as e:                          # pragma: no cover
            raise LLMError("需要 httpx：pip install httpx") from e

        body: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema,
                                "strict": False},
            }

        t0 = time.time()
        # ★ 只对**网络层**失败重试（读超时 / 连接被断），不重试 4xx。
        #   这是「请求没发成功」，不是「模型答得不好」——
        #   后者由 repair 循环处理。把两件事分开，轮次才不会被互相吃掉。
        last_exc: Optional[Exception] = None
        r = None
        for net_try in range(self.net_retries + 1):
            try:
                r = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json=body, timeout=self.timeout,
                )
                break
            except Exception as e:                        # noqa: BLE001
                last_exc = e
                if net_try >= self.net_retries:
                    raise LLMError(
                        f"请求 {self.base_url} 失败（试了 "
                        f"{self.net_retries + 1} 次，每次超时 {self.timeout:.0f}s）："
                        f"{e}") from e
                time.sleep(2.0 * (net_try + 1))           # 退避一下再试
        if r is None:                                      # pragma: no cover
            raise LLMError(f"请求失败：{last_exc}")

        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}：{r.text[:400]}")

        data = r.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"响应结构异常：{json.dumps(data)[:400]}") from e

        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=data.get("model", self.model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=int((time.time() - t0) * 1000),
            raw=data,
        )


# ══════════════════════════════════════════════════════════════════
# Mock Provider（测试 / 离线 Demo）
# ══════════════════════════════════════════════════════════════════

class MockProvider(LLMProvider):
    """按脚本返回预置内容

    用途：
        · 单元测试自修复循环（可编排「先错后对」）
        · Demo 站离线演示
    """

    name = "mock"
    supports_json_schema = False

    def __init__(self, responses: List[str]):
        self._responses = list(responses)
        self.calls: List[List[Message]] = []

    def chat(self, messages, *, temperature=0.3, max_tokens=8192,
             json_schema=None) -> LLMResponse:
        self.calls.append(list(messages))
        if not self._responses:
            raise LLMError("MockProvider 的脚本已用尽")
        text = self._responses.pop(0)
        return LLMResponse(text=text, model="mock", latency_ms=1)


# ══════════════════════════════════════════════════════════════════
# 工厂
# ══════════════════════════════════════════════════════════════════

_PRESETS = {
    "openai":   ("https://api.openai.com/v1",      "OPENAI_API_KEY",    "gpt-4o-mini"),
    "deepseek": ("https://api.deepseek.com/v1",    "DEEPSEEK_API_KEY",  "deepseek-chat"),
    "moonshot": ("https://api.moonshot.cn/v1",     "MOONSHOT_API_KEY",  "moonshot-v1-32k"),
    "dashscope": ("https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "DASHSCOPE_API_KEY", "qwen-plus"),
    # 硅基流动：国内可直连，**同一个 key 同时提供 LLM 与生图**，
    # 所以「小说 → 漫画」整条链路可以只配一个 key 就跑通。
    "siliconflow": ("https://api.siliconflow.cn/v1",
                    "SILICONFLOW_API_KEY", "Qwen/Qwen2.5-72B-Instruct"),
    "ollama":   ("http://localhost:11434/v1",      None,                "qwen2.5:14b"),
}


def get_provider(name: str = "deepseek", model: Optional[str] = None,
                 api_key: Optional[str] = None,
                 base_url: Optional[str] = None) -> LLMProvider:
    """按名字取 Provider

    name 可为预设名（openai/deepseek/moonshot/dashscope/ollama）或 "mock"
    """
    if name == "mock":
        return MockProvider([])

    preset = _PRESETS.get(name)
    if preset is None and not base_url:
        raise LLMError(f"未知 provider {name!r}；可用：{list(_PRESETS)} 或 mock")

    p_base, env_key, p_model = preset or ("", None, "")
    base_url = base_url or p_base
    model = model or p_model
    if api_key is None and env_key:
        api_key = os.environ.get(env_key)
    if not api_key and "localhost" not in base_url:
        raise LLMError(f"缺少 API key（环境变量 {env_key}）")

    return OpenAICompatProvider(model=model, base_url=base_url,
                                api_key=api_key or "none", name=name)


PROVIDER_NAMES = list(_PRESETS) + ["mock"]
