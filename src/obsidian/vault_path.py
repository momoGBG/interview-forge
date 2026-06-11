"""vault 路径与文件名工具。"""
from __future__ import annotations

import re
from pathlib import Path

from ..infra.config import load_config


def notes_dir() -> Path:
    cfg = load_config()
    d = Path(cfg["obsidian"]["vault_abs"]) / cfg["obsidian"].get("notes_subdir", "")
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_filename(text: str, max_len: int = 60) -> str:
    """把题目转成安全的文件名（去掉非法字符，保留中文）。"""
    name = re.sub(r'[\\/:*?"<>|]', " ", text).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:max_len].rstrip() or "untitled"
