# -*- coding: utf-8 -*-
"""编辑项目的磁盘读写

目录结构：
    <root>/<项目名>/
    ├── project.json      项目数据（页面与元素全在这里）
    ├── assets/           上传的原图（不改动，保留原始文件）
    └── out/              导出产物（PDF / 长图）

★ 与 IR 项目分开存放（默认 `works/`），避免两套东西混在一起。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from typing import List, Optional

from PIL import Image

from .models import Asset, EditProject, Page


class EditStore:
    """一个目录管所有编辑项目"""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    # ── 路径 ──────────────────────────────────────────────
    @staticmethod
    def safe_name(name: str) -> str:
        """项目名只允许安全字符，防路径穿越"""
        s = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]", "_", name or "").strip("_")
        if not s:
            raise ValueError("非法的项目名")
        return s[:60]

    def dir_of(self, name: str) -> str:
        return os.path.join(self.root, self.safe_name(name))

    def assets_dir(self, name: str) -> str:
        d = os.path.join(self.dir_of(name), "assets")
        os.makedirs(d, exist_ok=True)
        return d

    def out_dir(self, name: str) -> str:
        d = os.path.join(self.dir_of(name), "out")
        os.makedirs(d, exist_ok=True)
        return d

    def _json_path(self, name: str) -> str:
        return os.path.join(self.dir_of(name), "project.json")

    def exists(self, name: str) -> bool:
        return os.path.exists(self._json_path(name))

    def asset_path(self, name: str, stored_name: str) -> str:
        p = os.path.join(self.assets_dir(name), os.path.basename(stored_name))
        return p

    # ── 读写 ──────────────────────────────────────────────
    def list(self) -> List[dict]:
        out = []
        for d in sorted(os.listdir(self.root)):
            fp = os.path.join(self.root, d, "project.json")
            if not os.path.isfile(fp):
                continue
            try:
                p = EditProject.model_validate_json(
                    open(fp, encoding="utf-8").read())
                out.append({"name": p.name, "title": p.title or p.name,
                            "pages": len(p.pages), "assets": len(p.assets),
                            "updated_at": p.updated_at})
            except Exception:                               # noqa: BLE001
                out.append({"name": d, "title": d, "pages": 0,
                            "assets": 0, "updated_at": 0, "broken": True})
        return sorted(out, key=lambda x: -x.get("updated_at", 0))

    def load(self, name: str) -> Optional[EditProject]:
        fp = self._json_path(name)
        if not os.path.isfile(fp):
            return None
        return EditProject.model_validate_json(open(fp, encoding="utf-8").read())

    def save(self, project: EditProject) -> None:
        project.touch()
        os.makedirs(self.dir_of(project.name), exist_ok=True)
        tmp = self._json_path(project.name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(project.model_dump_json(indent=1))
        os.replace(tmp, self._json_path(project.name))      # 原子写，防中断损坏

    def create(self, name: str, title: str = "",
               canvas: tuple[int, int] = (1400, 2000),
               with_first_page: bool = True) -> EditProject:
        name = self.safe_name(name)
        if self.exists(name):
            raise FileExistsError(f"项目 {name} 已存在")
        p = EditProject(name=name, title=title or name,
                        canvas_width=canvas[0], canvas_height=canvas[1])
        if with_first_page:
            p.pages.append(Page(number=1, order=0, title="第 1 页"))
        self.save(p)
        return p

    def delete(self, name: str) -> bool:
        d = self.dir_of(name)
        if not os.path.isdir(d):
            return False
        shutil.rmtree(d)
        return True

    # ── 素材 ──────────────────────────────────────────────
    def add_asset(self, name: str, filename: str, data: bytes) -> Asset:
        """保存上传的图片，读出真实尺寸"""
        ad = self.assets_dir(name)
        ext = os.path.splitext(filename)[1].lower() or ".png"
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
            ext = ".png"
        stored = f"{int(time.time()*1000)}_{os.urandom(3).hex()}{ext}"
        fp = os.path.join(ad, stored)

        # 顺带校验是不是真图片（避免存进垃圾文件）
        from io import BytesIO
        im = Image.open(BytesIO(data))
        im.load()
        w, h = im.size
        with open(fp, "wb") as f:
            f.write(data)
        return Asset(filename=filename, stored_name=stored,
                     width=w, height=h, bytes=len(data))

    def load_asset_image(self, name: str, stored_name: str):
        fp = self.asset_path(name, stored_name)
        if not os.path.isfile(fp):
            return None
        try:
            return Image.open(fp).convert("RGB")
        except Exception:                                   # noqa: BLE001
            return None
