"""本地 ASR（faster-whisper）：口语答案转写。

- 设备自适应：优先 CUDA(本机 RTX 4090)，失败回退 CPU(int8)。
- 模型首次使用经代理从 HuggingFace 下载，存到项目 models/ 下。
- 解码用 PyAV，浏览器 MediaRecorder 的 webm/opus 也能直接吃。
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import PROJECT_ROOT, load_config

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

_MODEL = None
_MODEL_KEY = None


def _load_model(cfg: dict):
    global _MODEL, _MODEL_KEY
    a = cfg.get("asr", {})
    key = (a.get("model", "small"), a.get("device", "auto"))
    if _MODEL is not None and _MODEL_KEY == key:
        return _MODEL

    from faster_whisper import WhisperModel

    # 模型下载经代理
    proxy = cfg.get("proxy")
    if proxy:
        os.environ.setdefault("HTTP_PROXY", proxy)
        os.environ.setdefault("HTTPS_PROXY", proxy)
    download_root = str(PROJECT_ROOT / "models")
    Path(download_root).mkdir(exist_ok=True)

    size = a.get("model", "small")
    want = a.get("device", "auto")
    attempts = ([("cuda", "float16"), ("cpu", "int8")] if want == "auto"
                else [("cuda", "float16")] if want == "cuda" else [("cpu", "int8")])
    last_err = None
    for device, ctype in attempts:
        try:
            _MODEL = WhisperModel(size, device=device, compute_type=ctype,
                                  download_root=download_root)
            _MODEL_KEY = key
            _MODEL.device_used = device  # type: ignore[attr-defined]
            return _MODEL
        except Exception as e:  # noqa: BLE001 — CUDA 缺 cuDNN 等 → 回退 CPU
            last_err = e
    raise RuntimeError(f"ASR 模型加载失败：{last_err}")


def transcribe(audio_path: str, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    model = _load_model(cfg)
    segments, info = model.transcribe(
        audio_path, language=cfg.get("asr", {}).get("language", "zh"),
        vad_filter=True, beam_size=5)
    segs = list(segments)
    text = "".join(s.text for s in segs).strip()
    return {"text": text, "language": info.language,
            "duration": round(info.duration, 1),
            "device": getattr(model, "device_used", "?")}
