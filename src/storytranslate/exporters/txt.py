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
        parts.append(ch["title"])
        parts.append("")
        for s in db.segments_for_chapter(ch["id"]):
            parts.append(s["translated_text"] or s["source_text"])
            parts.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path
