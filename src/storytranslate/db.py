"""SQLite persistence layer (WAL). System of record for projects/documents/segments/tm/terminology."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from .models import Chapter, ModelRun, Segment, SegmentState, TMEntry, TerminologyEntry

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  source_lang TEXT DEFAULT 'auto',
  target_lang TEXT DEFAULT 'en',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  path TEXT NOT NULL,
  format TEXT NOT NULL,
  title TEXT
);
CREATE TABLE IF NOT EXISTS chapters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  document_id INTEGER NOT NULL,
  order_index INTEGER NOT NULL,
  title TEXT NOT NULL,
  html TEXT,
  summary TEXT
);
CREATE TABLE IF NOT EXISTS segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  document_id INTEGER NOT NULL,
  chapter_id INTEGER NOT NULL,
  scene_id INTEGER,
  order_index INTEGER NOT NULL,
  source_text TEXT NOT NULL,
  translated_text TEXT,
  state TEXT NOT NULL DEFAULT 'UNPROCESSED',
  model_run_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_segments_project ON segments(project_id, chapter_id, order_index);
CREATE TABLE IF NOT EXISTS terminology (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  source_forms TEXT NOT NULL,         -- json list
  canonical_translation TEXT NOT NULL, -- json dict
  concept TEXT DEFAULT '',
  policy TEXT DEFAULT 'preferred',
  status TEXT DEFAULT 'suggested',
  occurrences INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS translation_memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  source_text TEXT NOT NULL,
  translated_text TEXT NOT NULL,
  context TEXT DEFAULT '',
  entities TEXT DEFAULT '[]',
  terminology TEXT DEFAULT '[]',
  model TEXT DEFAULT '',
  chapter_id INTEGER,
  approved INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS model_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  stage TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT DEFAULT 'v1',
  story_bible_version TEXT DEFAULT '',
  terminology_version TEXT DEFAULT '',
  tm_version TEXT DEFAULT '',
  params TEXT DEFAULT '{}',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS qa_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  segment_id INTEGER,
  severity TEXT NOT NULL,
  type TEXT NOT NULL,
  message TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- projects ----
    def create_project(self, name: str, source_lang: str = "auto", target_lang: str = "en") -> int:
        cur = self.conn.execute(
            "INSERT INTO projects(name, source_lang, target_lang) VALUES (?,?,?)",
            (name, source_lang, target_lang),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_project(self, project_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()

    # ---- documents ----
    def add_document(self, project_id: int, path: str, format: str, title: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO documents(project_id, path, format, title) VALUES (?,?,?,?)",
            (project_id, path, format, title),
        )
        self.conn.commit()
        return cur.lastrowid

    # ---- chapters/segments ----
    def add_chapter(self, ch: Chapter) -> int:
        cur = self.conn.execute(
            "INSERT INTO chapters(project_id, document_id, order_index, title, html, summary) VALUES (?,?,?,?,?,?)",
            (ch.project_id, ch.document_id, ch.order_index, ch.title, ch.html, ch.summary),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_segment(self, seg: Segment) -> int:
        cur = self.conn.execute(
            "INSERT INTO segments(project_id, document_id, chapter_id, scene_id, order_index, source_text, state) VALUES (?,?,?,?,?,?,?)",
            (seg.project_id, seg.document_id, seg.chapter_id, seg.scene_id, seg.order_index, seg.source_text, seg.state.value),
        )
        self.conn.commit()
        return cur.lastrowid

    def segments_for_chapter(self, chapter_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM segments WHERE chapter_id=? ORDER BY order_index", (chapter_id,)
        ).fetchall()

    def open_segments(self, project_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM segments WHERE project_id=? AND state IN ('UNPROCESSED','ANALYZED','NEEDS_REVIEW','STALE') ORDER BY chapter_id, order_index",
            (project_id,),
        ).fetchall()

    def update_segment(self, segment_id: int, translated: Optional[str] = None, state: Optional[SegmentState] = None, model_run_id: Optional[int] = None) -> None:
        sets, vals = [], []
        if translated is not None:
            sets.append("translated_text=?"); vals.append(translated)
        if state is not None:
            sets.append("state=?"); vals.append(state.value)
        if model_run_id is not None:
            sets.append("model_run_id=?"); vals.append(model_run_id)
        if sets:
            vals.append(segment_id)
            self.conn.execute(f"UPDATE segments SET {', '.join(sets)} WHERE id=?", vals)
            self.conn.commit()

    # ---- terminology ----
    def add_terminology(self, t: TerminologyEntry) -> int:
        cur = self.conn.execute(
            "INSERT INTO terminology(project_id, source_forms, canonical_translation, concept, policy, status, occurrences) VALUES (?,?,?,?,?,?,?)",
            (t.project_id, json.dumps(t.source_forms, ensure_ascii=False), json.dumps(t.canonical_translation, ensure_ascii=False), t.concept, t.policy, t.status, t.occurrences),
        )
        self.conn.commit()
        return cur.lastrowid

    def terminology_for_project(self, project_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM terminology WHERE project_id=?", (project_id,)).fetchall()

    # ---- TM ----
    def add_tm(self, t: TMEntry) -> int:
        cur = self.conn.execute(
            "INSERT INTO translation_memory(project_id, source_text, translated_text, context, entities, terminology, model, chapter_id, approved) VALUES (?,?,?,?,?,?,?,?,?)",
            (t.project_id, t.source_text, t.translated_text, t.context, t.entities, t.terminology, t.model, t.chapter_id, t.approved),
        )
        self.conn.commit()
        return cur.lastrowid

    def tm_lookup(self, project_id: int, source_text: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM translation_memory WHERE project_id=? AND source_text=? ORDER BY id DESC LIMIT 1",
            (project_id, source_text),
        ).fetchone()

    # ---- model runs ----
    def add_model_run(self, run: ModelRun) -> int:
        cur = self.conn.execute(
            "INSERT INTO model_runs(project_id, stage, model, prompt_version, story_bible_version, terminology_version, tm_version, params) VALUES (?,?,?,?,?,?,?,?)",
            (run.project_id, run.stage, run.model, run.prompt_version, run.story_bible_version, run.terminology_version, run.tm_version, run.params),
        )
        self.conn.commit()
        return cur.lastrowid

    # ---- QA ----
    def add_qa(self, project_id: int, segment_id: Optional[int], severity: str, type: str, message: str) -> None:
        self.conn.execute(
            "INSERT INTO qa_results(project_id, segment_id, severity, type, message) VALUES (?,?,?,?,?)",
            (project_id, segment_id, severity, type, message),
        )
        self.conn.commit()

    def qa_for_project(self, project_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM qa_results WHERE project_id=?", (project_id,)).fetchall()
