"""Optional YAML configuration (spec §86).

`<workspace>/lorebridge.yaml`, or `--config FILE`. Precedence: command-line flag > file > built-in default.
Unknown keys are an error, so a typo does not silently do nothing.

    provider: ollama            # mock | ollama | openai-compatible
    model: huihui_ai/qwen3-abliterated:4b
    base_url: ""                # for openai-compatible servers
    models:                     # model per task (CLI command); falls back to `model`
      translate: qwen2.5:14b
      refine: qwen2.5:14b
      review: qwen2.5:7b
      analyze: qwen2.5:7b
    escalate_model: qwen2.5:14b # stronger model for risky segments
    source_lang: zh
    target_lang: en
    style: faithful-natural     # translation mode / style preset
    summaries: true             # model summary after each finished chapter
    n_prev: 3                   # previous translated passages shown to the model
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from . import service

DEFAULTS = {"provider": "mock", "model": "qwen2.5:7b", "base_url": "", "source_lang": "auto", "target_lang": "en",
            "style": "faithful-natural", "summaries": False, "escalate_model": None, "n_prev": 3}
TASKS = ("analyze", "translate", "refine", "review", "paste", "retranslate", "terminology")


def load(path: Optional[str | Path], required: bool = False) -> dict:
    p = Path(path) if path else None
    if not p or not p.is_file():
        if required:
            raise SystemExit(f"config file not found: {p}")
        return {}
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError as e:
        raise SystemExit(f"{p}: invalid YAML: {e}")
    if not isinstance(cfg, dict):
        raise SystemExit(f"{p}: expected a mapping at top level")
    unknown = set(cfg) - set(DEFAULTS) - {"models"}
    if unknown:
        raise SystemExit(f"{p}: unknown key(s) {sorted(unknown)}; allowed: {sorted(set(DEFAULTS) | {'models'})}")
    models = cfg.get("models") or {}
    if not isinstance(models, dict) or set(models) - set(TASKS):
        raise SystemExit(f"{p}: `models` must map task names ({', '.join(TASKS)}) to model names")
    if cfg.get("provider", "mock") not in service.PROVIDERS:
        raise SystemExit(f"{p}: provider must be one of {list(service.PROVIDERS)}")
    if cfg.get("style", "faithful-natural") not in service.STYLES:
        raise SystemExit(f"{p}: style must be one of {list(service.STYLES)}")
    if not isinstance(cfg.get("n_prev", 3), int) or cfg.get("n_prev", 3) < 0:
        raise SystemExit(f"{p}: n_prev must be a non-negative integer")
    return cfg


def apply(args, cfg: dict) -> None:
    """Fill every option the user left unset (None) from the file, then from DEFAULTS."""
    if getattr(args, "model", None) is None:
        task_model = (cfg.get("models") or {}).get(args.command)
        if task_model:
            args.model = task_model
    for key, default in DEFAULTS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, cfg.get(key, default))
