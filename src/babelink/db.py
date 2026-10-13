"""SQLite persistence layer (WAL). System of record for projects/documents/segments/tm/terminology."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from .models import Chapter, ModelRun, Segment, SegmentState, TMEntry, TerminologyEntry

# States a paragraph is in while it still waits for a translation (what the next run picks up).
OPEN_STATES = ("UNPROCESSED", "ANALYZED", "STALE")

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
CREATE TABLE IF NOT EXISTS regions (          -- pixel box of an OCR'd text block on an image page (one per segment)
  segment_id INTEGER PRIMARY KEY,
  x0 INTEGER, y0 INTEGER, x1 INTEGER, y1 INTEGER,
  polys TEXT NOT NULL,                        -- json: one quad per OCR line, used to erase the original lettering
  page_w INTEGER, page_h INTEGER
);
CREATE TABLE IF NOT EXISTS human_edits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  segment_id INTEGER NOT NULL,
  old_text TEXT,
  new_text TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'local',  -- local | project
  created_at TEXT DEFAULT (datetime('now'))
);
"""

# columns added after the first release: (table, column, declaration)
MIGRATIONS = [
    ("chapters", "title_translated", "TEXT"),
    ("projects", "style", "TEXT DEFAULT 'faithful-natural'"),
    ("projects", "genre", "TEXT DEFAULT ''"),
    ("segments", "translator_note", "TEXT DEFAULT ''"),
]


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # one connection per request/job, used by one task at a time; the web framework may hop threads between calls
        self.conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        for table, col, decl in MIGRATIONS:
            if col not in {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
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

    def projects(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM projects ORDER BY id").fetchall()

    def set_project(self, project_id: int, **fields) -> None:
        sets = {k: v for k, v in fields.items() if k in {"name", "source_lang", "target_lang", "style", "genre"}}
        if sets:
            self.conn.execute(f"UPDATE projects SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), project_id])
            self.conn.commit()

    def delete_project(self, project_id: int) -> None:
        """Remove a project and everything that belongs to it, in one transaction."""
        with self.conn:
            self.conn.execute("DELETE FROM regions WHERE segment_id IN (SELECT id FROM segments WHERE project_id=?)", (project_id,))
            for table in ("human_edits", "qa_results", "model_runs", "translation_memory", "terminology", "segments", "chapters", "documents"):
                self.conn.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))
            self.conn.execute("DELETE FROM projects WHERE id=?", (project_id,))

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

    def bulk_store_chapters(self, project_id: int, doc_id: int, chapters: list) -> list[int]:
        """Import many chapters + segments in ONE transaction. Row-at-a-time
        commits make whole-book imports (1000+ chapters) take tens of minutes;
        this does it in seconds. Crash-safety during translate is unaffected
        (translate still commits per segment)."""
        base = self.conn.execute(
            "SELECT COALESCE(MAX(order_index), -1) + 1 AS b FROM chapters WHERE project_id=?", (project_id,)
        ).fetchone()["b"]
        ids: list[int] = []
        with self.conn:
            for i, ch in enumerate(chapters):
                cur = self.conn.execute(
                    "INSERT INTO chapters(project_id, document_id, order_index, title, html, summary) VALUES (?,?,?,?,?,?)",
                    (project_id, doc_id, base + i, ch.title, ch.html, None),
                )
                ch_id = cur.lastrowid
                self.conn.executemany(
                    "INSERT INTO segments(project_id, document_id, chapter_id, scene_id, order_index, source_text, state) VALUES (?,?,?,?,?,?,?)",
                    [(project_id, doc_id, ch_id, None, j, para, "UNPROCESSED") for j, para in enumerate(ch.paragraphs)],
                )
                ids.append(ch_id)
        return ids

    def chapters(self, project_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM chapters WHERE project_id=? ORDER BY order_index", (project_id,)).fetchall()

    def set_chapter_title_translated(self, chapter_id: int, title: str) -> None:
        self.conn.execute("UPDATE chapters SET title_translated=? WHERE id=?", (title, chapter_id))
        self.conn.commit()

    def chapter_progress(self, project_id: int) -> list[dict]:
        """Per-chapter segment counts by state, in reading order."""
        out = []
        for ch in self.chapters(project_id):
            counts: dict[str, int] = {}
            for r in self.conn.execute("SELECT state, COUNT(*) n FROM segments WHERE chapter_id=? GROUP BY state", (ch["id"],)):
                counts[r["state"]] = r["n"]
            out.append({"id": ch["id"], "order": ch["order_index"], "title": ch["title"],
                        "title_translated": ch["title_translated"], "total": sum(counts.values()), "states": counts})
        return out

    def get_segment(self, segment_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()

    def previous_translated(self, project_id: int, chapter_order: int, seg_order: int, n: int = 6) -> list[sqlite3.Row]:
        """The n translated segments immediately before this position in reading order.
        Crosses chapter boundaries, so a pasted chapter continues from the previous one."""
        rows = self.conn.execute(
            "SELECT s.* FROM segments s JOIN chapters c ON c.id=s.chapter_id "
            "WHERE s.project_id=? AND s.translated_text IS NOT NULL AND (c.order_index < ? OR (c.order_index=? AND s.order_index < ?)) "
            "ORDER BY c.order_index DESC, s.order_index DESC LIMIT ?",
            (project_id, chapter_order, chapter_order, seg_order, n),
        ).fetchall()
        return list(reversed(rows))

    def segments_for_chapter(self, chapter_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM segments WHERE chapter_id=? ORDER BY order_index", (chapter_id,)
        ).fetchall()

    def segments_for_project(self, project_id: int, translated_only: bool = False) -> list[sqlite3.Row]:
        """Every segment of a project (reconcile votes, audits)."""
        q = "SELECT s.* FROM segments s WHERE s.project_id=? "
        if translated_only:
            q += "AND s.translated_text IS NOT NULL "
        return self.conn.execute(q + "ORDER BY s.id", (project_id,)).fetchall()

    def open_segments(self, project_id: int, chapter_id: Optional[int] = None) -> list[sqlite3.Row]:
        """Segments still waiting for a translation, in reading order. NEEDS_REVIEW is excluded:
        those are waiting for a human (or an explicit re-translate), not for the next run."""
        q = ("SELECT s.* FROM segments s JOIN chapters c ON c.id=s.chapter_id WHERE s.project_id=? "
             f"AND s.state IN {OPEN_STATES}")
        args: list = [project_id]
        if chapter_id is not None:
            q += " AND s.chapter_id=?"
            args.append(chapter_id)
        return self.conn.execute(q + " ORDER BY c.order_index, s.order_index", args).fetchall()

    def segment_is_open(self, segment_id: int) -> bool:
        """Still waiting for a translation (the states `open_segments` lists). False once a person edited or approved it."""
        row = self.conn.execute("SELECT state FROM segments WHERE id=?", (segment_id,)).fetchone()
        return row is not None and row["state"] in OPEN_STATES

    def update_segment(self, segment_id: int, translated: Optional[str] = None, state: Optional[SegmentState] = None, model_run_id: Optional[int] = None,
                       note: Optional[str] = None, only_if_state: Optional[tuple[str, ...]] = None) -> bool:
        """Write the given fields. With `only_if_state` the write happens only while the segment is in one of those states, decided inside
        the UPDATE itself, so a model that finishes after a person edited the paragraph cannot overwrite it. Returns whether a row changed."""
        sets, vals = [], []
        if translated is not None:
            sets.append("translated_text=?"); vals.append(translated)
        if state is not None:
            sets.append("state=?"); vals.append(state.value)
        if model_run_id is not None:
            sets.append("model_run_id=?"); vals.append(model_run_id)
        if note is not None:
            sets.append("translator_note=?"); vals.append(note)
        if not sets:
            return True
        where = "id=?"
        vals.append(segment_id)
        if only_if_state:
            where += f" AND state IN ({','.join('?' * len(only_if_state))})"
            vals.extend(only_if_state)
        changed = self.conn.execute(f"UPDATE segments SET {', '.join(sets)} WHERE {where}", vals).rowcount > 0
        self.conn.commit()
        return changed

    # ---- terminology ----
    def add_terminology(self, t: TerminologyEntry) -> int:
        cur = self.conn.execute(
            "INSERT INTO terminology(project_id, source_forms, canonical_translation, concept, policy, status, occurrences) VALUES (?,?,?,?,?,?,?)",
            (t.project_id, json.dumps(t.source_forms, ensure_ascii=False), json.dumps(t.canonical_translation, ensure_ascii=False), t.concept, t.policy, t.status, t.occurrences),
        )
        self.conn.commit()
        return cur.lastrowid

    def terminology_for_project(self, project_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM terminology WHERE project_id=? ORDER BY occurrences DESC, id", (project_id,)).fetchall()

    def update_terminology(self, term_id: int, **fields) -> None:
        """canonical_translation (dict), concept, policy, status, occurrences."""
        if "canonical_translation" in fields:
            fields["canonical_translation"] = json.dumps(fields["canonical_translation"], ensure_ascii=False)
        sets = {k: v for k, v in fields.items() if k in {"canonical_translation", "concept", "policy", "status", "occurrences"}}
        if sets:
            self.conn.execute(f"UPDATE terminology SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), term_id])
            self.conn.commit()

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
            "SELECT * FROM translation_memory WHERE project_id=? AND source_text=? ORDER BY approved DESC, id DESC LIMIT 1",
            (project_id, source_text),
        ).fetchone()

    def tm_similar(self, project_id: int, source_text: str, k: int = 2, min_sim: float = 0.5) -> list[tuple[float, sqlite3.Row]]:
        """Fuzzy TM hints (char-bigram Dice): a reference for the prompt, never blind substitution (spec §21)."""
        def grams(t: str) -> set[str]:
            return {t[i:i + 2] for i in range(len(t) - 1)} or {t}

        g = grams(source_text)
        scored = []
        for r in self.conn.execute("SELECT * FROM translation_memory WHERE project_id=? AND source_text<>?", (project_id, source_text)):
            h = grams(r["source_text"])
            sim = 2 * len(g & h) / (len(g) + len(h))
            if sim >= min_sim:
                scored.append((sim, r))
        return sorted(scored, key=lambda x: (-x[0], -x[1]["approved"]))[:k]

    # ---- model runs ----
    def add_model_run(self, run: ModelRun) -> int:
        cur = self.conn.execute(
            "INSERT INTO model_runs(project_id, stage, model, prompt_version, story_bible_version, terminology_version, tm_version, params) VALUES (?,?,?,?,?,?,?,?)",
            (run.project_id, run.stage, run.model, run.prompt_version, run.story_bible_version, run.terminology_version, run.tm_version, run.params),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_model_run(self, run_id: int, params: dict) -> None:
        self.conn.execute("UPDATE model_runs SET params=? WHERE id=?", (json.dumps(params), run_id))
        self.conn.commit()

    # ---- QA ----
    def add_qa(self, project_id: int, segment_id: Optional[int], severity: str, type: str, message: str) -> None:
        self.conn.execute(
            "INSERT INTO qa_results(project_id, segment_id, severity, type, message) VALUES (?,?,?,?,?)",
            (project_id, segment_id, severity, type, message),
        )
        self.conn.commit()

    def qa_for_project(self, project_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM qa_results WHERE project_id=?", (project_id,)).fetchall()

    def clear_qa(self, segment_id: int, types: Optional[tuple[str, ...]] = None) -> None:
        """Drop earlier findings for a segment before re-checking (no duplicate rows)."""
        if types:
            self.conn.execute(f"DELETE FROM qa_results WHERE segment_id=? AND type IN ({','.join('?' * len(types))})", (segment_id, *types))
        else:
            self.conn.execute("DELETE FROM qa_results WHERE segment_id=?", (segment_id,))
        self.conn.commit()

    def qa_types(self, segment_id: int) -> list[str]:
        return [r["type"] for r in self.conn.execute("SELECT type FROM qa_results WHERE segment_id=?", (segment_id,))]

    # ---- human edits ----
    def add_human_edit(self, project_id: int, segment_id: int, old: Optional[str], new: str, scope: str) -> None:
        self.conn.execute(
            "INSERT INTO human_edits(project_id, segment_id, old_text, new_text, scope) VALUES (?,?,?,?,?)",
            (project_id, segment_id, old, new, scope),
        )
        self.conn.commit()

    def edits_for_segment(self, segment_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM human_edits WHERE segment_id=? ORDER BY id DESC", (segment_id,)).fetchall()
