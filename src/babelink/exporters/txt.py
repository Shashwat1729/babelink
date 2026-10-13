"""Export translated text as TXT."""
from __future__ import annotations

from pathlib import Path

from ..db import Database


def export_txt(db: Database, project_id: int, out_path: str | Path, *, bilingual: bool = False) -> Path:
    out_path = Path(out_path)
    parts: list[str] = []
    for ch in db.chapters(project_id):
        parts += [(ch["title_translated"] or ch["title"]).strip(), ""]
        for s in db.segments_for_chapter(ch["id"]):
            text = (s["translated_text"] or s["source_text"]).strip()
            if not text:
                continue
            parts += [text, ""]  # blank line between paragraphs
            if bilingual:
                parts += [f"    [{s['source_text'].strip()}]", ""]
            if s["translator_note"]:
                parts += [f"    [Translator's note: {s['translator_note'].strip()}]", ""]
        parts.append("")  # blank line between chapters
    text = "\n".join(parts)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text.strip() + "\n", encoding="utf-8")
    return out_path
