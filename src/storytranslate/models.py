"""Core domain models."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class SegmentState(str, enum.Enum):
    UNPROCESSED = "UNPROCESSED"
    ANALYZED = "ANALYZED"
    TRANSLATED = "TRANSLATED"
    REFINED = "REFINED"
    QA_PASSED = "QA_PASSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    STALE = "STALE"


@dataclass
class Segment:
    id: Optional[int]
    project_id: int
    document_id: int
    chapter_id: int
    scene_id: Optional[int]
    order_index: int
    source_text: str
    translated_text: Optional[str] = None
    state: SegmentState = SegmentState.UNPROCESSED
    model_run_id: Optional[int] = None


@dataclass
class Chapter:
    id: Optional[int]
    project_id: int
    document_id: int
    order_index: int
    title: str
    html: Optional[str] = None  # original html for epub round-trip
    summary: Optional[str] = None


@dataclass
class TerminologyEntry:
    id: Optional[int]
    project_id: int
    source_forms: list[str]
    canonical_translation: dict[str, str]  # lang -> translation
    concept: str = ""
    policy: str = "preferred"  # fixed|preferred|flexible|contextual|do-not-translate|transliterate|preserve-original|user-defined
    status: str = "suggested"  # inferred|uncertain|suggested|confirmed|user-confirmed
    occurrences: int = 0


@dataclass
class TMEntry:
    id: Optional[int]
    project_id: int
    source_text: str
    translated_text: str
    context: str = ""
    entities: str = ""  # json list
    terminology: str = ""  # json list
    model: str = ""
    chapter_id: Optional[int] = None
    approved: int = 0


@dataclass
class ModelRun:
    id: Optional[int]
    project_id: int
    stage: str  # analysis|translation|refinement|qa
    model: str
    prompt_version: str = "v1"
    story_bible_version: str = ""
    terminology_version: str = ""
    tm_version: str = ""
    params: str = "{}"
    created_at: str = ""
