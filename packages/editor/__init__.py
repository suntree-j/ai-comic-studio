# -*- coding: utf-8 -*-
"""画布编辑器（Office 式）

    models.py   数据模型：项目 / 页面 / 元素 / 素材
    render.py   服务端渲染：把 JSON 画成图，并返回元素包围盒
    store.py    磁盘读写

与 `packages.ir` 正交：那个管「小说自动改编」，这个管「手工从空白页做」。
"""

from .models import (
    Asset,
    BubbleElement,
    BubbleKind,
    EditProject,
    Element,
    ImageElement,
    Page,
    ShapeElement,
    TextAlign,
    TextElement,
    FitMode,
    new_id,
    parse_element,
)
from .render import (
    bubble_geometry,
    hex_to_rgb,
    render_page,
    auto_place_bubble,
)
from .store import EditStore

__all__ = [
    "Asset", "BubbleElement", "BubbleKind", "EditProject", "Element",
    "ImageElement", "Page", "ShapeElement", "TextAlign", "TextElement",
    "FitMode", "new_id", "parse_element",
    "bubble_geometry", "hex_to_rgb", "render_page", "auto_place_bubble",
    "EditStore",
]
