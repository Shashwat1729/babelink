"""Human-in-the-loop (spec §38): edit/approve a translation and optionally promote it to project memory."""
from __future__ import annotations

from .db import Database
from .models import SegmentState, TMEntry


def edit_segment(db: Database, project_id: int, segment_id: int, new_text: str, scope: str = "local") -> None:
    """Replace a translation with the human's version and lock it (HUMAN_APPROVED).

    scope="local"   only this paragraph changes.
    scope="project" the approved text also enters the TM, so identical source lines reuse it
                    and similar lines get it as a prompt hint from now on.
    """
    if scope not in ("local", "project"):
        raise ValueError("scope must be 'local' or 'project'")
    seg = db.get_segment(segment_id)
    if seg is None or seg["project_id"] != project_id:
        raise KeyError(f"segment {segment_id}")
    new_text = new_text.strip()
    db.add_human_edit(project_id, segment_id, seg["translated_text"], new_text, scope)
    db.update_segment(segment_id, translated=new_text, state=SegmentState.HUMAN_APPROVED)
    db.clear_qa(segment_id)
    if scope == "project":
        db.add_tm(TMEntry(None, project_id, seg["source_text"], new_text, model="human", chapter_id=seg["chapter_id"], approved=1))


def approve_segment(db: Database, project_id: int, segment_id: int) -> None:
    """Accept the current translation as-is."""
    seg = db.get_segment(segment_id)
    if seg is None or seg["project_id"] != project_id or not seg["translated_text"]:
        raise KeyError(f"segment {segment_id} has nothing to approve")
    edit_segment(db, project_id, segment_id, seg["translated_text"], scope="project")
