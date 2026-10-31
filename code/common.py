#!/usr/bin/env python3
"""common.py — 路径与配置解析（H3 版代码包）

优先级：环境变量 > twopass_spec.json 的 env 段 > 默认值。

环境变量：
  AISV_PROJECT_ROOT  项目根（默认取 spec.env.project_root，再退当前目录）
  AISV_H3_ROOT       ComfyUI H3 根目录
  AISV_SCENE         子项目目录名（默认 magic）
  AISV_SPEC          spec 文件路径（默认本目录 twopass_spec.json）
  AISV_UPSCALE_MODEL 超分模型路径（默认取 spec.upscale_model）
"""
from __future__ import annotations

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEC_PATH = os.environ.get("AISV_SPEC") or os.path.join(SCRIPT_DIR, "twopass_spec.json")


def load_spec() -> dict:
    with open(SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


def _env() -> dict:
    try:
        return load_spec().get("env", {})
    except Exception:
        return {}


_ENV = _env()

AISV_ROOT = (
    os.environ.get("AISV_PROJECT_ROOT")
    or _ENV.get("project_root")
    or os.getcwd()
)
H3_ROOT = (
    os.environ.get("AISV_H3_ROOT")
    or _ENV.get("h3_root")
    or r"C:\AI\ComfyUI_H3"
)
SCENE = os.environ.get("AISV_SCENE") or _ENV.get("scene_dir") or "magic"
SCENE_DIR = os.path.join(AISV_ROOT, SCENE)

UPSCALE_MODEL = (
    os.environ.get("AISV_UPSCALE_MODEL")
    or (load_spec().get("upscale_model") if os.path.exists(SPEC_PATH) else None)
    or ""
)
