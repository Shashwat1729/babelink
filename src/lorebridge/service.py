"""Orchestration shared by the CLI and the web UI, so both behave identically."""
from __future__ import annotations

import json
from pathlib import Path

from .db import Database
from .hymt import DEFAULT_MODEL as HYMT_DEFAULT_MODEL
from .importers import import_any, import_paste
from .pipeline import analyze_project
from .providers.gemini import GeminiProvider
from .providers.base import MockProvider
from .providers.cat_ollama import CatOllamaProvider, DEFAULT_MODEL as CAT_DEFAULT_MODEL
from .providers.hymt_ollama import HymtOllamaProvider, is_mt_name
from .providers.hymt_local import HymtLocalProvider
from .providers.ollama import OllamaProvider
from .providers.openai_compatible import OpenAICompatibleProvider
from .story_bible import project_root

STYLES = ("literal", "faithful-natural", "literary", "highly-literary")
PROVIDERS = ("mock", "ollama", "hymt", "hymt-local", "cat", "openai-compatible", "gemini", "auto")

# Default translation engine: Tencent Hy-MT2-1.8B Q4_K_M via Ollama (~1.1GB,
# CPU-runnable, stronger zh/ja->en fiction translation than general 4B LLMs).
# `ollama pull hf.co/tencent/Hy-MT2-1.8B-GGUF:Q4_K_M`
DEFAULT_TRANSLATE_MODEL = HYMT_DEFAULT_MODEL
# Helper for non-translation tasks (term classification, refinement, review,
# summaries): Hy-MT2 is translation-only, so these need a general LLM.
DEFAULT_HELPER_MODEL = "huihui_ai/qwen3-abliterated:4b"


def default_engine(source_lang: str = "", target_lang: str = "en") -> tuple[str, str]:
    """Evidence-based engine per direction (E04 bake-off, E07 gold-glossary):
    ja->en is CAT-Translate-1.4B (bare chrF wins 3/4 vs Hy-MT2, ≈12B-class);
    everything else translation is Hy-MT2 (zh->en classical + ja->zh proven).
    An explicit user choice always wins; this only answers 'auto'."""
    if source_lang == "ja" and target_lang == "en":
        return "cat", CAT_DEFAULT_MODEL
    return "hymt", DEFAULT_TRANSLATE_MODEL


def make_provider(kind: str = "mock", model: str = DEFAULT_TRANSLATE_MODEL, base_url: str = ""):
    if kind == "mock":
        return MockProvider()
    if kind == "hymt":
        return HymtOllamaProvider(model=model or DEFAULT_TRANSLATE_MODEL, **({"base_url": base_url} if base_url else {}))
    if kind == "hymt-local":
        return HymtLocalProvider(model=model or "tencent/Hy-MT2-1.8B")
    if kind == "cat":
        return CatOllamaProvider(model=model or CAT_DEFAULT_MODEL, **({"base_url": base_url} if base_url else {}))
    if kind == "ollama":
        if is_mt_name(model or ""):  # `ollama --model hf.co/tencent/...` just works
            return HymtOllamaProvider(model=model, **({"base_url": base_url} if base_url else {}))
        return OllamaProvider(model=model, **({"base_url": base_url} if base_url else {}))
    if kind == "openai-compatible":
        return OpenAICompatibleProvider(base_url=base_url or "http://localhost:8080/v1", model=model)
    if kind == "gemini":
        return GeminiProvider(model=model or "gemini-2.0-flash")
    raise ValueError(f"unknown provider: {kind}")


def is_mt_only(provider) -> bool:
    """True when the provider is a translation-only model (Hy-MT2): it must not
    do term classification, refinement, review or summaries."""
    return bool(getattr(provider, "translation_only", False))


def helper_for(provider, kind: str = "ollama", base_url: str = "", helper_model: str = ""):
    """Provider for non-translation tasks. Returns `provider` itself for general
    LLMs; a general-LLM helper for translation-only models."""
    if not is_mt_only(provider):
        return provider
    model = helper_model or DEFAULT_HELPER_MODEL
    if kind == "mock":
        return MockProvider()
    if kind == "gemini":
        return GeminiProvider(model=model or "gemini-2.0-flash")
    if kind == "openai-compatible":
        return OpenAICompatibleProvider(base_url=base_url or "http://localhost:8080/v1", model=model)
    # Helper tasks (terms/review/summaries) share the provider; context stays
    # large because chapter summaries need it — speed comes from fewer, not
    # smaller, helper calls (see the mining-side filters in terminology).
    # ponytail: helper runs at 4k ctx, not 8k — a 4B helper's 8k KV cache
    # (~6GB) OOMs Ollama on a 16GB CPU-only box; helper prompts are small.
    return OllamaProvider(model=model, num_ctx=4096, **({"base_url": base_url} if base_url else {}))


def open_workspace(workspace: str | Path) -> Database:
    return Database(Path(workspace) / "project.db")


def create_project(db: Database, name: str, source_lang: str = "auto", target_lang: str = "en", style: str = "faithful-natural") -> int:
    pid = db.create_project(name, source_lang=source_lang, target_lang=target_lang)
    db.set_project(pid, style=style)
    return pid


def import_file(db: Database, pid: int, path: str | Path) -> int:
    n_before = len(db.chapters(pid))
    import_any(db, pid, path)
    return len(db.chapters(pid)) - n_before


def retarget(db: Database, pid: int, target_lang: str, name: str) -> int:
    """Start a new target language from the same source: documents, chapters
    and segment sources are copied; the story graph (term forms, concepts,
    occurrences) carries over WITHOUT renderings, so the new language gets
    its own classification run while names/types stay aligned across languages.
    Translations, TM, QA and summaries are not copied (they belong to the old
    target language)."""
    from .models import SegmentState, TerminologyEntry

    src = db.get_project(pid)
    npid = create_project(db, name, source_lang=src["source_lang"], target_lang=target_lang, style=src["style"])
    doc_map: dict[int, int] = {}
    for d in db.conn.execute("SELECT * FROM documents WHERE project_id=?", (pid,)).fetchall():
        doc_map[d["id"]] = db.add_document(npid, d["path"], d["format"], d["title"])
    ch_map: dict[int, int] = {}
    for c in db.chapters(pid):
        cur = db.conn.execute(
            "INSERT INTO chapters(project_id, document_id, order_index, title, html, summary) VALUES (?,?,?,?,?,?)",
            (npid, doc_map[c["document_id"]], c["order_index"], c["title"], c["html"], None))
        ch_map[c["id"]] = cur.lastrowid
    db.conn.executemany(
        "INSERT INTO segments(project_id, document_id, chapter_id, scene_id, order_index, source_text, state) VALUES (?,?,?,?,?,?,?)",
        [(npid, doc_map[s["document_id"]], ch_map[s["chapter_id"]], s["scene_id"], s["order_index"], s["source_text"],
          SegmentState.UNPROCESSED.value)
         for s in db.conn.execute("SELECT * FROM segments WHERE project_id=?", (pid,)).fetchall()])
    seen: dict[tuple, dict] = {}
    for t in db.terminology_for_project(pid):
        if t["status"] == "ignored":
            continue
        # ponytail: same form can exist twice (suggested + confirmed row);
        # carry one entry per form set, preferring a real concept.
        key = tuple(sorted(json.loads(t["source_forms"])))
        prev = seen.get(key)
        if prev is None or (not prev["concept"] and t["concept"]):
            seen[key] = {"forms": json.loads(t["source_forms"]), "concept": t["concept"],
                         "occurrences": t["occurrences"]}
        else:
            seen[key]["occurrences"] = max(seen[key]["occurrences"], t["occurrences"])
    for v in seen.values():
        db.add_terminology(TerminologyEntry(None, npid, v["forms"], {}, v["concept"],
                                           "preferred", "suggested", v["occurrences"]))
    db.conn.commit()
    return npid


def add_pasted_chapter(db: Database, workspace: str | Path, pid: int, text: str, title: str = "Pasted", provider=None) -> list[int]:
    """Append pasted text as new chapter(s) and analyze incrementally: earlier chapters' glossary, TM,
    summaries and Bible entries stay; only new terms are mined and new chapters summarized."""
    before = {c["id"] for c in db.chapters(pid)}
    import_paste(db, pid, text, title)
    analyze_project(db, pid, project_root(workspace, pid), provider=provider)
    return [c["id"] for c in db.chapters(pid) if c["id"] not in before]


def overview(db: Database, pid: int) -> dict:
    """Numbers for the project header / `projects` listing."""
    p = db.get_project(pid)
    states = {r["state"]: r["n"] for r in db.conn.execute("SELECT state, COUNT(*) n FROM segments WHERE project_id=? GROUP BY state", (pid,))}
    total = sum(states.values())
    translated = total - sum(states.get(k, 0) for k in ("UNPROCESSED", "ANALYZED"))
    terms = db.terminology_for_project(pid)
    target = p["target_lang"]
    return {
        "id": pid, "name": p["name"], "source_lang": p["source_lang"], "target_lang": target,
        "style": p["style"], "chapters": len(db.chapters(pid)), "segments": total,
        "pages": db.conn.execute("SELECT COUNT(*) n FROM chapters c JOIN documents d ON d.id=c.document_id WHERE c.project_id=? AND d.format='image'", (pid,)).fetchone()["n"], "translated": translated,
        "states": states,
        "terms": sum(1 for t in terms if t["status"] != "ignored"),
        "terms_with_translation": sum(1 for t in terms if t["status"] != "ignored" and target in json.loads(t["canonical_translation"])),
        "tm_entries": db.conn.execute("SELECT COUNT(*) n FROM translation_memory WHERE project_id=?", (pid,)).fetchone()["n"],
        "summaries": db.conn.execute("SELECT COUNT(*) n FROM chapters WHERE project_id=? AND summary<>''", (pid,)).fetchone()["n"],
        "qa_warnings": db.conn.execute("SELECT COUNT(*) n FROM qa_results WHERE project_id=? AND severity='warning'", (pid,)).fetchone()["n"],
        "qa_errors": db.conn.execute("SELECT COUNT(*) n FROM qa_results WHERE project_id=? AND severity='error'", (pid,)).fetchone()["n"],
    }
