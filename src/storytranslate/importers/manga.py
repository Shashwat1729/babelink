"""Manga/manhua adapter (Phase 6) — interface + dependency guard.

Full pipeline (spec §69): OCR -> text-region detection -> panel ordering ->
speaker/context -> Story Bible -> translation -> inpainting -> typesetting.

This module is intentionally a thin boundary: real OCR/inpainting need heavyweight
deps (opencv, easyocr/pytesseract, lama-inpainting). Install them separately;
the core translation/Bible/TM machinery is reused unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PanelText:
    page: int
    bbox: tuple[int, int, int, int]
    raw_text: str
    speaker: str = ""


@dataclass
class ComicSession:
    project_id: int
    panels: list[PanelText] = field(default_factory=list)


def ocr_page(image_path: str) -> list[PanelText]:
    """Stub: wire easyocr/pytesseract here. Returns panel texts in reading order."""
    raise NotImplementedError(
        "OCR backend not installed. Add easyocr/pytesseract + opencv to enable Phase 6."
    )
