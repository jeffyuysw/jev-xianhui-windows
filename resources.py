"""资源定位：兼容开发态与 PyInstaller 打包态的资源路径解析。

开发态：项目根目录下的 assets/
打包态：PyInstaller 会把 datas 解包到 sys._MEIPASS，相对路径保持一致。
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    """返回资源文件的绝对路径。

    relative 形如 "assets/app_icon_256.png"。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        # PyInstaller 打包态：资源被解包到 _MEIPASS
        candidate = Path(base) / relative
        if candidate.exists():
            return candidate
    # 开发态：相对本文件所在目录
    candidate = Path(__file__).resolve().parent / relative
    if candidate.exists():
        return candidate
    # 兜底：相对当前工作目录
    return Path.cwd() / relative


def asset(name: str) -> Path:
    """assets/ 目录下的资源。"""
    return resource_path(f"assets/{name}")
