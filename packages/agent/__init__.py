# -*- coding: utf-8 -*-
"""AI 改漫 Agent

    providers.py  LLM 适配器（可插拔）
    prompts.py    提示词模板（规则前置、引用式、可追溯）
    skills.py     5 个技能（含自修复循环）
    pipeline.py   端到端编排
"""

from .providers import (
    LLMProvider,
    LLMError,
    JSONParseError,
    LLMResponse,
    Message,
    MockProvider,
    OpenAICompatProvider,
    get_provider,
    extract_json,
    parse_as,
    PROVIDER_NAMES,
)
from .skills import (
    RepairResult,
    repair_until_valid,
    split_chapters,
    generate_storyboard,
    extract_dialogue,
    track_state,
    apply_state_delta,
    make_panel_validator,
    make_dialogue_validator,
    ChapterPlan,
    StateDelta,
    number_lines,
    split_lines,
)
from .pipeline import (
    ComicPipeline,
    PipelineOptions,
    ChapterResult,
    adapt_novel,
)

__all__ = [
    "LLMProvider", "LLMError", "JSONParseError", "LLMResponse", "Message",
    "MockProvider", "OpenAICompatProvider", "get_provider",
    "extract_json", "parse_as", "PROVIDER_NAMES",
    "RepairResult", "repair_until_valid",
    "split_chapters", "generate_storyboard", "extract_dialogue",
    "track_state", "apply_state_delta",
    "make_panel_validator", "make_dialogue_validator",
    "ChapterPlan", "StateDelta", "number_lines", "split_lines",
    "ComicPipeline", "PipelineOptions", "ChapterResult", "adapt_novel",
]
