# -*- coding: utf-8 -*-
"""渲染引擎

    providers.py  生图服务适配器（可插拔）
    prompt.py     三段式提示词组装
    bubble.py     气泡布局算法（内容能量最小化）
    page.py       页面合成
    export.py     PDF / 长图导出
    studio.py     把上面串起来的渲染编排
"""

from .providers import (
    ImageProvider,
    ImageRequest,
    ImageResult,
    ImageError,
    QuotaExceeded,
    MockImageProvider,
    SeedreamProvider,
    OpenAIImagesProvider,
    SDWebUIProvider,
    GenericHTTPProvider,
    get_image_provider,
    IMAGE_PROVIDER_NAMES,
    KNOWN_SIZES,
    SIZE_WIDE,
    SIZE_TALL,
    SIZE_SQUARE,
    to_data_url,
)
from .prompt import (
    build_prompt,
    build_negative,
    describe_panel,
    render_character_block,
    render_composition,
)
from .bubble import (
    BubbleRequest,
    Placed,
    Spot,
    choose_spot,
    detect_person_side,
    default_tail,
    energy_map,
    layout_bubbles,
)
from .page import (
    RenderContext,
    PANEL_WIDTH,
    TARGET_PAGE_HEIGHT,
    GAP,
    ELEMENT_COLORS,
    load_font,
    wrap_text,
    measure_bubble,
    draw_bubble,
    draw_skill_label,
    render_panel,
    render_title_page,
    render_page,
    paginate,
)
from .export import save_pdf, save_pdf_pymupdf, save_long_image, save_long_images
from .studio import RenderStudio, RenderReport

__all__ = [
    "ImageProvider", "ImageRequest", "ImageResult", "ImageError", "QuotaExceeded",
    "MockImageProvider", "SeedreamProvider", "OpenAIImagesProvider",
    "SDWebUIProvider", "GenericHTTPProvider",
    "get_image_provider", "IMAGE_PROVIDER_NAMES", "KNOWN_SIZES",
    "SIZE_WIDE", "SIZE_TALL", "SIZE_SQUARE", "to_data_url",
    "build_prompt", "build_negative", "describe_panel",
    "render_character_block", "render_composition",
    "BubbleRequest", "Placed", "Spot", "choose_spot", "detect_person_side",
    "default_tail", "energy_map", "layout_bubbles",
    "RenderContext", "PANEL_WIDTH", "TARGET_PAGE_HEIGHT", "GAP",
    "ELEMENT_COLORS", "load_font", "wrap_text", "measure_bubble",
    "draw_bubble", "draw_skill_label", "render_panel", "render_title_page",
    "render_page", "paginate",
    "save_pdf", "save_pdf_pymupdf", "save_long_image", "save_long_images",
    "RenderStudio", "RenderReport",
]
