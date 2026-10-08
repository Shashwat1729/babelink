"""Output validation (spec §84): is the export complete, readable and actually translated?"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from ..db import Database

_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")


def validate_project(db: Database, project_id: int) -> dict:
    """Database-level completeness checks. `ok` is False when the export would ship unfinished text."""
    c = db.conn
    total = c.execute("SELECT COUNT(*) n FROM segments WHERE project_id=?", (project_id,)).fetchone()["n"]
    untranslated = c.execute("SELECT COUNT(*) n FROM segments WHERE project_id=? AND (translated_text IS NULL OR translated_text='')", (project_id,)).fetchone()["n"]
    review = c.execute("SELECT COUNT(*) n FROM segments WHERE project_id=? AND state='NEEDS_REVIEW'", (project_id,)).fetchone()["n"]
    stale = c.execute("SELECT COUNT(*) n FROM segments WHERE project_id=? AND state='STALE'", (project_id,)).fetchone()["n"]
    errors = c.execute("SELECT COUNT(*) n FROM qa_results WHERE project_id=? AND severity='error'", (project_id,)).fetchone()["n"]
    return {"segments": total, "untranslated": untranslated, "needs_review": review, "stale": stale, "qa_errors": errors,
            "ok": total > 0 and untranslated == 0 and stale == 0 and errors == 0}


def validate_epub(path: str | Path, target_lang: str = "en") -> dict:
    """File-level checks on the produced EPUB: zip integrity, content documents, leftover source script."""
    path = Path(path)
    problems: list[str] = []
    docs = cjk_docs = 0
    try:
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                problems.append("corrupt zip member")
            names = z.namelist()
            if "mimetype" not in names:
                problems.append("missing mimetype")
            for n in names:
                if n.endswith((".xhtml", ".html")) and "nav" not in n.lower():
                    text = re.sub(r"<[^>]+>", "", z.read(n).decode("utf-8", errors="replace"))
                    docs += 1
                    if target_lang not in ("zh", "ja", "ko") and len(_CJK.findall(text)) > 0.2 * max(len(text), 1):
                        cjk_docs += 1
            if docs == 0:
                problems.append("no content documents")
    except zipfile.BadZipFile:
        problems.append("not a zip/EPUB")
    except OSError as e:
        problems.append(f"cannot read file: {e.strerror or e}")
    if cjk_docs:
        problems.append(f"{cjk_docs} document(s) still mostly source-language text")
    return {"documents": docs, "problems": problems, "ok": not problems}
