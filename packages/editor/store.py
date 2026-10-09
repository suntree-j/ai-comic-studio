# -*- coding: utf-8 -*-
"""编辑项目的磁盘读写

目录结构：
    <root>/<项目名>/
    ├── project.json      项目数据（页面与元素全在这里）
    ├── .history/         撤销快照（环形保留，重启不丢）
    ├── assets/           上传的原图（不改动，保留原始文件）
    └── out/              导出产物（PDF / 长图）

★ 两个关键设计（都是踩坑后补的）：

  ① **每个项目一把写锁** —— 原来是「读整个 JSON → 改 → 整个写回」，
     没有任何保护。实测 6 个并发 PATCH 有 5 个直接报文件占用错误，
     而且「先读后写」会丢改动（后写覆盖先写）。现在：
       · 进程内：每个项目一把 `threading.RLock`
       · 跨进程：`project.json.lock` 文件锁（Windows / Linux 都支持）

  ② **撤销快照落盘** —— 原来只存在服务端内存的 dict 里，
     重启服务就全丢。现在写进 `.history/`，环形保留。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from PIL import Image, ImageOps

from .models import Asset, EditProject, Page

#: 撤销历史保留的份数
HISTORY_LIMIT = 40


# ══════════════════════════════════════════════════════════════════
# 跨进程文件锁
# ══════════════════════════════════════════════════════════════════

if os.name == "nt":                                         # pragma: no cover
    import msvcrt

    def _lock_file(f) -> None:
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(f) -> None:
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:                                                       # pragma: no cover
    import fcntl

    def _lock_file(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock_file(f) -> None:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


class EditStore:
    """一个目录管所有编辑项目"""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        #: 每个项目一把进程内锁（跨线程）；跨进程靠 .lock 文件
        self._locks: Dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def lock_for(self, name: str) -> threading.RLock:
        with self._locks_guard:
            if name not in self._locks:
                self._locks[name] = threading.RLock()
            return self._locks[name]

    @contextmanager
    def locked(self, name: str) -> Iterator[None]:
        """整个「读 → 改 → 写」期间持有锁

        用法：
            with store.locked("我的项目"):
                p = store.load("我的项目")
                ... 改 ...
                store.save(p)
        """
        safe = self.safe_name(name)
        with self.lock_for(safe):
            d = self.dir_of(safe)
            os.makedirs(d, exist_ok=True)
            lock_path = os.path.join(d, "project.json.lock")
            f = open(lock_path, "a+b")
            try:
                _lock_file(f)
                yield
            finally:
                _unlock_file(f)
                f.close()

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

    def history_dir(self, name: str) -> str:
        d = os.path.join(self.dir_of(name), ".history")
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
                            "revision": p.revision,
                            "updated_at": p.updated_at})
            except Exception:                               # noqa: BLE001
                out.append({"name": d, "title": d, "pages": 0,
                            "assets": 0, "updated_at": 0, "broken": True})
        return sorted(out, key=lambda x: -x.get("updated_at", 0))

    def load(self, name: str) -> Optional[EditProject]:
        fp = self._json_path(name)
        if not os.path.isfile(fp):
            return None
        for attempt in range(5):
            try:
                raw = open(fp, encoding="utf-8").read()
                if not raw.strip():
                    time.sleep(0.02)
                    continue
                return EditProject.model_validate_json(raw)
            except Exception:                               # noqa: BLE001
                # 正在被替换（Windows 下 os.replace 与 open 可能撞），重试
                time.sleep(0.03 * (attempt + 1))
        return EditProject.model_validate_json(
            open(fp, encoding="utf-8").read())

    def save(self, project: EditProject) -> None:
        """写入项目（自动 +1 revision）

        ★ 原子写 + revision 自增：
          revision 是给「乐观并发控制」用的 —— 前端带上它发请求，
          服务端发现对不上就拒绝（说明别人先改了）。
        """
        project.touch()
        project.revision += 1
        d = self.dir_of(project.name)
        os.makedirs(d, exist_ok=True)
        fp = self._json_path(project.name)
        tmp = f"{fp}.{os.getpid()}.{threading.get_ident()}.tmp"
        payload = project.model_dump_json(indent=1)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp, fp)                         # 原子替换
                return
            except PermissionError:                         # Windows
                time.sleep(0.05 * (attempt + 1))
        os.replace(tmp, fp)

    # ── 撤销历史（落盘）──────────────────────────────────
    def push_history(self, name: str) -> None:
        """把当前 project.json 存一份快照（在修改**之前**调用）"""
        fp = self._json_path(name)
        if not os.path.isfile(fp):
            return
        hd = self.history_dir(name)
        with self.lock_for(self.safe_name(name)):
            stamp = f"{time.time():.6f}".replace(".", "")
            dst = os.path.join(hd, f"{stamp}.json")
            try:
                shutil.copy2(fp, dst)
            except OSError:
                return
            snapshots = sorted(f for f in os.listdir(hd) if f.endswith(".json"))
            for old in snapshots[:-HISTORY_LIMIT]:
                try:
                    os.remove(os.path.join(hd, old))
                except OSError:
                    pass

    def pop_history(self, name: str) -> Optional[str]:
        """取出最近一份快照的内容（并删除它）"""
        hd = self.history_dir(name)
        with self.lock_for(self.safe_name(name)):
            snaps = sorted(f for f in os.listdir(hd) if f.endswith(".json"))
            if not snaps:
                return None
            fp = os.path.join(hd, snaps[-1])
            try:
                raw = open(fp, encoding="utf-8").read()
            except OSError:
                return None
            try:
                os.remove(fp)
            except OSError:
                pass
            return raw

    def use_history(self, name: str, raw: str) -> None:
        """把快照内容写回 project.json（撤销）"""
        fp = self._json_path(name)
        tmp = f"{fp}.undo.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, fp)

    def history_depth(self, name: str) -> int:
        hd = self.history_dir(name)
        return len([f for f in os.listdir(hd) if f.endswith(".json")])

    def create(self, name: str, title: str = "",
               canvas: tuple = (1400, 2000),
               with_first_page: bool = True) -> EditProject:
        name = self.safe_name(name)
        with self.locked(name):
            if self.exists(name):
                raise FileExistsError(f"项目 {name} 已存在")
            p = EditProject(name=name, title=title or name,
                            canvas_width=canvas[0], canvas_height=canvas[1])
            if with_first_page:
                p.pages.append(Page(number=1, order=0, title="第 1 页"))
            self.save(p)
            return p

    def delete(self, name: str) -> bool:
        safe = self.safe_name(name)
        d = self.dir_of(safe)
        if not os.path.isdir(d):
            return False
        with self.lock_for(safe):
            shutil.rmtree(d, ignore_errors=True)
            self._locks.pop(safe, None)
        return True

    # ── 素材 ──────────────────────────────────────────────
    #: 单个文件上限（默认 25 MB）
    MAX_ASSET_BYTES = 25 * 1024 * 1024

    def add_asset(self, name: str, filename: str, data: bytes) -> Asset:
        """保存上传的图片

        ★ 两处校验：
          · 大小上限（应用层也要挡，不能只靠 nginx 的 client_max_body_size）
          · 真的是图片（否则会存进垃圾文件）
        ★ EXIF 方向：手机竖拍的照片带 Orientation 标记，
          PIL 默认不转 —— 不处理的话加进来会是横的。
        """
        if not data:
            raise ValueError("空文件")
        if len(data) > self.MAX_ASSET_BYTES:
            raise ValueError(
                f"文件太大（{len(data) / 1048576:.1f} MB，"
                f"上限 {self.MAX_ASSET_BYTES // 1048576} MB）")

        ad = self.assets_dir(name)
        ext = os.path.splitext(filename)[1].lower() or ".png"
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
            ext = ".png"
        stored = f"{int(time.time()*1000)}_{os.urandom(3).hex()}{ext}"
        fp = os.path.join(ad, stored)

        from io import BytesIO
        im = Image.open(BytesIO(data))
        im.load()
        w, h = im.size
        # 处理 EXIF 方向（transpose 会消耗掉 orientation 标记）
        fixed = ImageOps.exif_transpose(im)
        if fixed is not None and fixed.size != im.size:
            w, h = fixed.size
            buf = BytesIO()
            fmt = "JPEG" if ext in (".jpg", ".jpeg") else "PNG"
            if fmt == "JPEG" and fixed.mode in ("RGBA", "P", "LA"):
                fixed = fixed.convert("RGB")
            fixed.save(buf, fmt, quality=92 if fmt == "JPEG" else None)
            data = buf.getvalue()
        elif ext in (".jpg", ".jpeg") and im.mode in ("RGBA", "P", "LA"):
            # 存成 JPEG 但带 alpha → 会报错，先转 RGB
            buf = BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=92)
            data = buf.getvalue()

        with open(fp, "wb") as f:
            f.write(data)
        return Asset(filename=filename, stored_name=stored,
                     width=w, height=h, bytes=len(data))

    def load_asset_image(self, name: str, stored_name: str):
        fp = self.asset_path(name, stored_name)
        if not os.path.isfile(fp):
            return None
        try:
            im = Image.open(fp)
            im.load()
            return ImageOps.exif_transpose(im).convert("RGB")
        except Exception:                                   # noqa: BLE001
            return None
