"""Chapter-wise paste import: the pasted text joins an existing project, so the Story Bible,
glossary, TM and summaries built from earlier chapters keep applying (spec §38/§70)."""
from __future__ import annotations

from ..db import Database
from ..segment import chapters_from_text
from .base import store_chapters


def import_paste(db: Database, project_id: int, text: str, title: str = "Pasted") -> int:
    """`title` names the chapter when the text has no heading of its own."""
    doc_id = db.add_document(project_id, f"paste://{title}", "paste", title)
    store_chapters(db, project_id, doc_id, chapters_from_text(text, default_title=title))
    return doc_id
