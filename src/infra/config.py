"""配置加载：读取项目根的 config.yaml，解析 vault 等相对路径为绝对路径。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

# 项目根 = 本文件的 src/infra/../../ -> interview-forge/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@lru_cache(maxsize=1)
def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # vault 相对路径解析为绝对路径
    vault = cfg.get("obsidian", {}).get("vault_path", "./vault")
    cfg["obsidian"]["vault_abs"] = str((PROJECT_ROOT / vault).resolve())
    return cfg


def profile_cfg(cfg: dict | None = None) -> dict:
    """领域人设：把所有 prompt 里的「岗位方向 / 面试官头衔」抽成可配置项。

    改 config.yaml 的 `profile.field` / `profile.role_title` 一处，整套 prompt
    （答案教练 / 面试官 / 招聘官 / 简历抽取）就换成对应岗位——后端、前端、产品、
    数据、算法皆可，不必改代码。默认值保持 AI 算法味，向后兼容原定制。
    """
    cfg = cfg or load_config()
    p = dict(cfg.get("profile") or {})
    field = p.get("field", "AI 算法")
    role_title = p.get("role_title", "AI 算法工程师")
    return {
        "field": field,
        "role_title": role_title,
        "coach_title": p.get("coach_title", f"资深「{field}」面试教练"),
        "interviewer_title": p.get("interviewer_title", f"资深「{field}」面试官"),
        "recruiter_title": p.get("recruiter_title", f"资深「{field}」岗位招聘官"),
        "topics": p.get("topics") or ["general"],
    }
