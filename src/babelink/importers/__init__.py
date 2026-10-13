"""Importers: EPUB / TXT / HTML / pasted text / comic page images."""
from __future__ import annotations

from pathlib import Path

from .epub import import_epub  # noqa: F401
from .html import import_html  # noqa: F401
from .paste import import_paste  # noqa: F401
from .txt import import_txt  # noqa: F401

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}   # comic pages -> babelink.images (needs the [images] extra)
ARCHIVE_EXT = {".cbz", ".zip"}


def import_any(db, project_id: int, path: str | Path) -> int:
    """Dispatch on extension."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT | ARCHIVE_EXT:
        from ..images import import_images
        return import_images(db, project_id, path)
    if ext == ".epub":
        return import_epub(db, project_id, path)
    if ext in (".html", ".htm", ".xhtml"):
        return import_html(db, project_id, path)
    return import_txt(db, project_id, path)
