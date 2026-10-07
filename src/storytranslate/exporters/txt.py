"""Export translated text as TXT."""
from __future__ import annotations

from pathlib import Path

from ..db import Database


def export_txt(db: Database, project_id: int, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    chapters = db.conn.execute(
        "SELECT * FROM chapters WHERE project_id=? ORDER BY order_index", (project_id,)
    ).fetchall()
    parts: list[str] = []
    for ch in chapters:
        parts.append(ch["title"].strip())
        parts.append("")
        prev_blank = False
        for s in db.segments_for_chapter(ch["id"]):
            text = (s["translated_text"] or s["source_text"]).strip()
            if not text:
                continue
            parts.append(text)
            parts.append("")  # blank line between paragraphs
        parts.append("")  # blank line between chapters
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(parts)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    out_path.write_text(text.strip() + "\n", encoding="utf-8")
    return out_path
