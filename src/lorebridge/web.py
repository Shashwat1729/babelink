"""Web UI: FastAPI JSON API + static single-page app (spec §73). Local-first: binds to 127.0.0.1 by default.

Safety model (no accounts, one local user):
- every mutating /api call needs the `X-Requested-With: lorebridge` header, so another website cannot
  drive it from the user's browser (a cross-site form or fetch cannot set that header without a CORS preflight)
- the Host header must be a loopback name (blocks DNS rebinding); LOREBRIDGE_HOSTS adds more, comma-separated
- uploads: extension whitelist, size cap, server-chosen file name
- the frontend writes data with textContent only
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import hardware, images, service
from .context import build_context
from .db import Database
from .exporters import export_epub, export_txt
from .exporters.validate import validate_project
from .importers import ARCHIVE_EXT, IMAGE_EXT
from .models import SegmentState
from .pipeline import analyze_project, count_words, translate_project
from .providers import ollama, openai_compatible
from .providers.ollama import list_models
from .qa import ai_qa, run_qa
from .refinement import run_refinement
from .registry import recommend
from .review import approve_segment, edit_segment
from .story_bible import SECTIONS, load_section, project_root, save_section
from .terminology import build_glossary, set_term, suggest_term_translations, sync_bible

STATIC = Path(__file__).parent / "static"
CSRF_HEADER = ("x-requested-with", "lorebridge")
MAX_UPLOAD = 64 * 1024 * 1024
TEXT_EXT = {".epub", ".txt", ".html", ".htm", ".xhtml", ".md"}
PAGE_EXT = IMAGE_EXT | ARCHIVE_EXT  # comic pages: OCR'd in a background job
UPLOAD_EXT = TEXT_EXT | PAGE_EXT
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}

app = FastAPI(title="Lorebridge", docs_url=None, redoc_url=None)


def workspace() -> Path:
    """Read per request so tests and `lorebridge web --project-dir` can point it elsewhere."""
    return Path(os.environ.get("LOREBRIDGE_HOME", "web_project"))


@app.middleware("http")
async def guard(request: Request, call_next):
    host = urlsplit("//" + request.headers.get("host", "")).hostname
    extra = {h.strip() for h in os.environ.get("LOREBRIDGE_HOSTS", "").split(",") if h.strip()}
    if host not in LOCAL_HOSTS | extra:
        return JSONResponse({"detail": "host not allowed"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api"):
        if request.headers.get(CSRF_HEADER[0]) != CSRF_HEADER[1]:
            return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


def get_db():
    db = service.open_workspace(workspace())
    try:
        yield db
    finally:
        db.close()


def project_or_404(db: Database, pid: int):
    row = db.get_project(pid)
    if row is None:
        raise HTTPException(404, "project not found")
    return row


def segment_or_404(db: Database, pid: int, sid: int):
    seg = db.get_segment(sid)
    if seg is None or seg["project_id"] != pid:
        raise HTTPException(404, "segment not found")
    return seg


def _terms(db: Database, pid: int) -> list[dict]:
    target = db.get_project(pid)["target_lang"]
    out = []
    for t in db.terminology_for_project(pid):
        canon = json.loads(t["canonical_translation"])
        out.append({"id": t["id"], "forms": json.loads(t["source_forms"]), "translation": canon.get(target, ""),
                    "concept": t["concept"], "status": t["status"], "occurrences": t["occurrences"]})
    return out


# ---- jobs ------------------------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    project_id: int
    kind: str
    status: str = "running"  # running | done | error | cancelled
    done: int = 0
    total: int = 0
    message: str = ""
    error: str = ""
    result: dict = field(default_factory=dict)
    stop: threading.Event = field(default_factory=threading.Event)
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    words: int = 0  # source words translated so far (translate jobs)
    live: dict = field(default_factory=dict)  # the paragraph being written right now, text so far (replaced, never mutated)
    recent: list = field(default_factory=list)  # the last few finished paragraphs, oldest first

    def public(self) -> dict:
        return {"id": self.id, "project_id": self.project_id, "kind": self.kind, "status": self.status, "done": self.done,
                "total": self.total, "message": self.message, "error": self.error, "result": self.result,
                "words": self.words, "live": self.live, "recent": self.recent, "stopping": self.status == "running" and self.stop.is_set(),
                "seconds": round((self.finished or time.time()) - self.started, 1)}


JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()  # guards JOBS and the one-running-job-per-project rule


def _busy(pid: int) -> bool:
    """A job is running for this project. Call with _LOCK held when the answer decides what happens next."""
    return any(j.project_id == pid and j.status == "running" for j in JOBS.values())


# ---- idempotency -----------------------------------------------------------------------------------
_IDEM: "OrderedDict[tuple, dict]" = OrderedDict()
_IDEM_LOCK = threading.Lock()
_IDEM_KEEP = 256


def idempotent(request: Request, scope: str, work):
    """Run `work()` once per `Idempotency-Key` header and hand every repeat (double click, retry after a timeout, second tab) the
    first answer instead of doing the work again. Without the header it just runs. A failed `work` is not remembered, so a retry runs it
    again. Concurrent repeats wait for the first. State is in memory: it protects a live session, not a server restart.
    """
    key = (request.headers.get("Idempotency-Key") or "")[:64]
    if not key:
        return work()
    with _IDEM_LOCK:
        entry = _IDEM.get((scope, key))
        if entry is None:
            entry = _IDEM[(scope, key)] = {"lock": threading.Lock(), "ready": False, "result": None}
            while len(_IDEM) > _IDEM_KEEP:
                _IDEM.popitem(last=False)
    with entry["lock"]:
        if not entry["ready"]:
            entry["result"] = work()
            entry["ready"] = True
        return entry["result"]


JOB_KINDS = ("analyze", "suggest_terms", "translate", "refine", "qa", "import_images")


class JobRequest(BaseModel):
    kind: str
    chapter_id: Optional[int] = None
    segment_ids: list[int] = Field(default_factory=list)
    limit: Optional[int] = Field(default=None, ge=1)
    max_words: Optional[int] = Field(default=None, ge=1)  # translate: stop after the paragraph that reaches this many source words
    provider: str = "ollama"
    model: str = "huihui_ai/qwen3-abliterated:4b"
    base_url: str = ""
    refine_model: str = ""  # optional second model for refine / AI review; empty = same as `model`
    summarize: bool = True
    ai_review: bool = False
    uploads: list[str] = Field(default_factory=list)  # import_images: file names already saved under uploads/<project>/


def _work(job: Job, ws: Path, p: JobRequest) -> None:
    db = service.open_workspace(ws)
    try:
        pid, root = job.project_id, project_root(ws, job.project_id)
        provider = service.make_provider(p.provider, p.model, p.base_url)
        editor = service.make_provider(p.provider, p.refine_model, p.base_url) if p.refine_model else provider

        def progress(done: int, total: int, msg: str = "") -> None:
            job.done, job.total, job.message = done, total, msg

        if job.kind == "analyze":
            job.result = analyze_project(db, pid, root, provider=provider if p.provider != "mock" else None)
        elif job.kind == "suggest_terms":
            job.result = {"translated": suggest_term_translations(db, pid, provider, limit=p.limit or 60)}
            sync_bible(db, pid, root)
        elif job.kind == "translate":
            chapters: dict[int, object] = {}

            def on_text(s, text: str, final: bool) -> None:
                if final or s["chapter_id"] not in chapters:  # one lookup per paragraph, not per token
                    chapters[s["chapter_id"]] = db.conn.execute("SELECT title, title_translated, order_index FROM chapters WHERE id=?",
                                                                (s["chapter_id"],)).fetchone()
                ch = chapters[s["chapter_id"]]
                item = {"segment_id": s["id"], "chapter_id": s["chapter_id"], "chapter": ch["order_index"] + 1,
                        "chapter_title": ch["title_translated"] or ch["title"], "index": s["order_index"] + 1,
                        "source": s["source_text"][:400], "text": text[-1500:], "final": final}
                job.live = item
                if final:
                    job.words += count_words(s["source_text"])
                    job.recent = (job.recent + [item])[-4:]

            n = translate_project(
                db, pid, provider, limit=p.limit, project_dir=root, chapter_id=p.chapter_id, on_progress=progress,
                should_stop=job.stop.is_set, summarize=p.summarize, only_ids=set(p.segment_ids) or None,
                word_limit=p.max_words, on_text=on_text)
            job.result = {"translated": n, "words": job.words}
        elif job.kind == "refine":
            job.result = {"refined": run_refinement(db, pid, editor, root, limit=p.limit, chapter_id=p.chapter_id,
                                                    on_progress=progress, should_stop=job.stop.is_set)}
        elif job.kind == "import_images":
            from .images import import_images
            if any(Path(n).name != n for n in p.uploads):  # file names come from the client on /jobs: bare names only
                raise ValueError("invalid upload name")
            pages = sum(import_images(db, pid, ws / "uploads" / str(pid) / name, on_progress=progress, should_stop=job.stop.is_set,
                                      title=Path(name.split("_", 1)[-1]).stem)  # drop the server's `<id>_` prefix
                        for name in p.uploads)
            job.result = {"pages_added": pages, "analysis": analyze_project(db, pid, root)}
        elif job.kind == "qa":
            job.result = run_qa(db, pid, build_glossary(db, pid), chapter_id=p.chapter_id)
            if p.ai_review:
                job.result["ai_findings"] = ai_qa(db, pid, editor, limit=p.limit or 50, chapter_id=p.chapter_id)
        job.live = {}  # before the status flips: a poller that sees "done" must not also see a half-written paragraph
        job.status = "cancelled" if job.stop.is_set() else "done"
    except Exception as e:  # provider offline, bad model name ... shown in the UI; progress already saved
        job.live = {}
        job.status, job.error = "error", f"{type(e).__name__}: {e}"
    finally:
        db.close()
        job.finished = time.time()


def start_job(pid: int, req: JobRequest) -> Job:
    if req.kind not in JOB_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(JOB_KINDS)}")
    if req.provider not in service.PROVIDERS:
        raise HTTPException(400, f"provider must be one of {', '.join(service.PROVIDERS)}")
    with _LOCK:
        if _busy(pid):
            raise HTTPException(409, "another job is already running for this project")
        job = Job(uuid.uuid4().hex[:10], pid, req.kind)
        JOBS[job.id] = job
        for old in sorted((j for j in JOBS.values() if j.finished), key=lambda j: j.finished)[:-30]:
            JOBS.pop(old.id, None)
    threading.Thread(target=_work, args=(job, workspace(), req), daemon=True).start()
    return job


# ---- status / projects -----------------------------------------------------------------------------
@app.get("/api/status")
def status(base_url: str = "http://localhost:11434"):
    models = list_models(base_url)
    return {"ollama": bool(models), "models": models, "providers": service.PROVIDERS, "styles": service.STYLES}


def _check_url(url: str) -> None:
    if url and urlsplit(url).scheme not in ("http", "https"):
        raise HTTPException(400, "server address must start with http:// or https://")


@lru_cache(maxsize=1)
def _hardware() -> dict:
    return hardware.detect()


@app.get("/api/models")
def models(provider: str = "ollama", base_url: str = "", target: str = "en"):
    """What the model picker shows: reachability, installed models, and registry models that fit this machine."""
    if provider not in service.PROVIDERS:
        raise HTTPException(400, f"provider must be one of {', '.join(service.PROVIDERS)}")
    _check_url(base_url)
    hw = _hardware()
    out = {"provider": provider, "reachable": True, "error": "", "installed": [], "recommended": [], "hardware": hw, "remote": False, "url": ""}
    if provider == "mock":
        return out
    url = base_url or ("http://localhost:11434" if provider == "ollama" else "http://localhost:8080/v1")
    out["url"], out["remote"] = url, urlsplit(url).hostname not in LOCAL_HOSTS
    try:
        out["installed"] = (ollama.installed if provider == "ollama" else openai_compatible.installed)(url)
    except Exception as e:  # server down, wrong address, no /models route
        out["reachable"], out["error"] = False, f"{type(e).__name__}: {e}"[:300]
    if provider == "ollama":
        have = {m["name"] for m in out["installed"]}
        out["recommended"] = [{"name": m.name, "ram_gb": m.ram_gb, "vram_gb": m.vram_gb, "tasks": m.recommended_tasks,
                               "long_context": bool(m.capabilities.get("long_context")), "installed": m.name in have}
                              for m in recommend(target, hw["vram_gb"], hw["ram_gb"]) if m.provider == "ollama"]
    return out


class ModelTest(BaseModel):
    provider: str = "ollama"
    model: str = ""
    base_url: str = ""


@app.post("/api/models/test")
def test_model(body: ModelTest):
    """Translate one short line so the user finds out now, not after queuing a chapter, that the model works."""
    if body.provider not in service.PROVIDERS:
        raise HTTPException(400, f"provider must be one of {', '.join(service.PROVIDERS)}")
    _check_url(body.base_url)
    t0 = time.time()
    try:
        text = service.make_provider(body.provider, body.model, body.base_url).translate("林凡走进青云宗，大师兄对他点了点头。", {"林凡": "Lin Fan"}, max_tokens=80)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:400], "seconds": round(time.time() - t0, 1)}
    return {"ok": True, "text": text, "seconds": round(time.time() - t0, 1)}


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_lang: str = "auto"
    target_lang: str = "en"
    style: str = "faithful-natural"


@app.get("/api/projects")
def projects(db: Database = Depends(get_db)):
    return [service.overview(db, p["id"]) for p in db.projects()]


@app.post("/api/projects")
def create_project(body: ProjectIn, request: Request, db: Database = Depends(get_db)):
    if body.style not in service.STYLES:
        raise HTTPException(400, "unknown style")
    return idempotent(request, "create", lambda: service.overview(
        db, service.create_project(db, body.name.strip(), body.source_lang, body.target_lang, body.style)))


class ProjectPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    style: Optional[str] = None
    source_lang: Optional[str] = None
    target_lang: Optional[str] = None


@app.patch("/api/projects/{pid}")
def patch_project(pid: int, body: ProjectPatch, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    if body.style is not None and body.style not in service.STYLES:
        raise HTTPException(400, "unknown style")
    db.set_project(pid, **body.model_dump(exclude_none=True))
    return service.overview(db, pid)


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    with _LOCK:
        if _busy(pid):
            raise HTTPException(409, "stop the running job first")
        for jid in [j.id for j in JOBS.values() if j.project_id == pid]:
            JOBS.pop(jid, None)
        db.delete_project(pid)  # inside the lock: no job can start on a project that is going away
    for folder in (project_root(workspace(), pid), workspace() / "uploads" / str(pid)):
        shutil.rmtree(folder, ignore_errors=True)
    return {"deleted": pid}


@app.get("/api/projects/{pid}")
def project(pid: int, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    return service.overview(db, pid)


async def _read_upload(file: UploadFile, allowed: set[str] = UPLOAD_EXT) -> tuple[str, bytes]:
    name = re.sub(r"[^\w.\- ]", "_", Path(file.filename or "").name).strip() or "upload"
    if Path(name).suffix.lower() not in allowed:
        raise HTTPException(400, f"unsupported file type; use {', '.join(sorted(allowed))}")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"file larger than {MAX_UPLOAD // 2**20} MB")
    return name, data


@app.post("/api/projects/{pid}/import")
async def import_upload(pid: int, file: UploadFile = File(...), db: Database = Depends(get_db)):
    project_or_404(db, pid)
    name, data = await _read_upload(file, TEXT_EXT)
    return await run_in_threadpool(_store_and_import, db, pid, name, data)  # reading a book takes a while: keep the server answering meanwhile


_IMPORT_LOCK = threading.Lock()  # ponytail: one import at a time server-wide; per-project locks if imports ever queue up


def _store_and_import(db: Database, pid: int, name: str, data: bytes) -> dict:
    """Keep the upload under a name that starts with its content hash, so the same file added twice (double click, two tabs,
    dropping it again) is recognised and refused instead of duplicating every chapter. A failed import removes its file so a retry works."""
    folder = workspace() / "uploads" / str(pid)
    dest = folder / f"{hashlib.sha256(data).hexdigest()[:16]}_{name}"
    with _IMPORT_LOCK:
        if any(folder.glob(dest.name.split("_", 1)[0] + "_*")):
            raise HTTPException(409, "This file was already added to this work.")
        folder.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        try:
            added = service.import_file(db, pid, dest)
            info = analyze_project(db, pid, project_root(workspace(), pid))
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"could not read {name}: {e}")
    return {"chapters_added": added, "analysis": info, "project": service.overview(db, pid)}


@app.post("/api/projects/{pid}/pages")
async def import_pages(pid: int, files: list[UploadFile] = File(...), db: Database = Depends(get_db)):
    """Comic/manhua page images or a CBZ/ZIP of them. OCR runs as a job so the request returns at once."""
    project_or_404(db, pid)
    if importlib.util.find_spec("rapidocr_onnxruntime") is None:
        raise HTTPException(501, "image support is not installed: pip install 'lorebridge[images]'")
    if not files or len(files) > 200:
        raise HTTPException(400, "send between 1 and 200 files")
    with _LOCK:
        if _busy(pid):  # say so before storing the upload
            raise HTTPException(409, "another job is already running for this project")
    saved = []
    try:
        for f in files:
            name, data = await _read_upload(f, PAGE_EXT)
            dest = workspace() / "uploads" / str(pid) / f"{uuid.uuid4().hex[:8]}_{name}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            saved.append(dest.name)
        return {"job": start_job(pid, JobRequest(kind="import_images", uploads=saved)).public()}
    except BaseException:  # refused, too large, or the project's job slot was taken meanwhile: leave no orphans
        for n in saved:
            (workspace() / "uploads" / str(pid) / n).unlink(missing_ok=True)
        raise


class PasteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)
    title: str = Field(default="Pasted", max_length=200)
    translate: bool = False
    provider: str = "ollama"
    model: str = "huihui_ai/qwen3-abliterated:4b"
    base_url: str = ""


@app.post("/api/projects/{pid}/paste")
def paste(pid: int, body: PasteIn, request: Request, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    if not body.text.strip():
        raise HTTPException(400, "nothing to add")

    def work() -> dict:
        if body.translate:
            with _LOCK:  # refuse before adding anything: a 409 must not leave a chapter behind
                if _busy(pid):
                    raise HTTPException(409, "another job is already running for this project. Wait for it, or use Add only.")
        ids = service.add_pasted_chapter(db, workspace(), pid, body.text, body.title.strip() or "Pasted")
        job, job_error = None, ""
        if body.translate and ids:
            try:
                job = start_job(pid, JobRequest(kind="translate", chapter_id=None, provider=body.provider, model=body.model,
                                                base_url=body.base_url)).public()
            except HTTPException as e:  # another request took the job slot after the check: the text is saved, say that it was not started
                job_error = str(e.detail)
        return {"chapter_ids": ids, "job": job, "job_error": job_error, "project": service.overview(db, pid)}

    return idempotent(request, f"paste:{pid}", work)


# ---- chapters / segments ---------------------------------------------------------------------------
@app.get("/api/projects/{pid}/chapters")
def chapters(pid: int, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    counts: dict[int, dict[str, int]] = {}
    for r in db.conn.execute("SELECT chapter_id, state, COUNT(*) n FROM segments WHERE project_id=? GROUP BY chapter_id, state", (pid,)):
        counts.setdefault(r["chapter_id"], {})[r["state"]] = r["n"]
    pages = {r["id"] for r in db.conn.execute("SELECT c.id FROM chapters c JOIN documents d ON d.id=c.document_id "
                                              "WHERE c.project_id=? AND d.format='image'", (pid,))}
    return [{"id": c["id"], "index": c["order_index"], "title": c["title"], "title_translated": c["title_translated"] or "",
             "has_summary": bool(c["summary"]), "states": counts.get(c["id"], {}), "segments": sum(counts.get(c["id"], {}).values()),
             "page": c["id"] in pages}
            for c in db.chapters(pid)]


@app.get("/api/projects/{pid}/chapters/{cid}/segments")
def chapter_segments(pid: int, cid: int, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    ch = db.conn.execute("SELECT * FROM chapters WHERE id=? AND project_id=?", (cid, pid)).fetchone()
    if ch is None:
        raise HTTPException(404, "chapter not found")
    glossary = build_glossary(db, pid)
    findings: dict[int, list[dict]] = {}
    for q in db.qa_for_project(pid):
        findings.setdefault(q["segment_id"], []).append({"severity": q["severity"], "type": q["type"], "message": q["message"]})
    segs = db.segments_for_chapter(cid)
    return {"chapter": {"id": ch["id"], "title": ch["title"], "title_translated": ch["title_translated"] or "", "summary": ch["summary"] or "",
                        "page": images.is_page(db, cid)},
            "segments": [{"id": s["id"], "index": s["order_index"] + 1, "source": s["source_text"], "translation": s["translated_text"] or "",
                          "state": s["state"], "findings": findings.get(s["id"], []),
                          "terms": [{"source": k, "target": v} for k, v in glossary.items() if k in s["source_text"]]} for s in segs]}


@app.get("/api/projects/{pid}/chapters/{cid}/page")
def page_image(pid: int, cid: int, version: str = "translated", db: Database = Depends(get_db)):
    """The comic page as PNG: `original`, or `translated` (text erased and re-typeset from the current translations)."""
    project_or_404(db, pid)
    if db.conn.execute("SELECT 1 FROM chapters WHERE id=? AND project_id=?", (cid, pid)).fetchone() is None:
        raise HTTPException(404, "chapter not found")
    try:
        png = images.render_page(db, cid, translated=version != "original")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    return Response(png, media_type="image/png")


class SourceIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.put("/api/projects/{pid}/segments/{sid}/source")
def fix_source(pid: int, sid: int, body: SourceIn, db: Database = Depends(get_db)):
    """Correct what the OCR read. The old translation is dropped and the block returns to the translate queue."""
    project_or_404(db, pid)
    seg = segment_or_404(db, pid, sid)
    if db.conn.execute("SELECT 1 FROM regions WHERE segment_id=?", (seg["id"],)).fetchone() is None:
        raise HTTPException(400, "only text read from an image page can be corrected here")
    db.conn.execute("UPDATE segments SET source_text=?, translated_text=NULL, state=? WHERE id=?",
                    (body.text.strip(), SegmentState.ANALYZED.value, sid))
    db.conn.commit()
    return {"id": sid, "source": body.text.strip()}


class EditIn(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)
    scope: str = "local"


@app.put("/api/projects/{pid}/segments/{sid}")
def edit(pid: int, sid: int, body: EditIn, db: Database = Depends(get_db)):
    segment_or_404(db, pid, sid)
    if body.scope not in ("local", "project"):
        raise HTTPException(400, "scope must be local or project")
    edit_segment(db, pid, sid, body.text, body.scope)
    return {"state": db.get_segment(sid)["state"]}


@app.post("/api/projects/{pid}/segments/{sid}/approve")
def approve(pid: int, sid: int, db: Database = Depends(get_db)):
    seg = segment_or_404(db, pid, sid)
    if not seg["translated_text"]:
        raise HTTPException(400, "nothing to approve yet")
    approve_segment(db, pid, sid)
    return {"state": db.get_segment(sid)["state"]}


@app.post("/api/projects/{pid}/segments/{sid}/retranslate")
def retranslate(pid: int, sid: int, req: JobRequest, db: Database = Depends(get_db)):
    segment_or_404(db, pid, sid)
    with _LOCK:  # check before touching state, so a 409 leaves the segment alone
        if _busy(pid):
            raise HTTPException(409, "another job is already running for this project")
    before = db.get_segment(sid)["state"]
    db.update_segment(sid, state=SegmentState.STALE)
    db.clear_qa(sid)
    req.kind, req.segment_ids = "translate", [sid]
    try:
        return start_job(pid, req).public()
    except HTTPException:  # lost the job slot to another request between the check and here: put the paragraph back as it was
        db.update_segment(sid, state=SegmentState(before))
        raise


@app.get("/api/projects/{pid}/segments/{sid}/context")
def segment_context(pid: int, sid: int, db: Database = Depends(get_db)):
    """What the model sees for this paragraph: the same builders the pipeline uses."""
    seg = segment_or_404(db, pid, sid)
    glossary = {k: v for k, v in build_glossary(db, pid).items() if k in seg["source_text"]}
    tm = [{"similarity": round(sim, 2), "source": r["source_text"], "translation": r["translated_text"]}
          for sim, r in db.tm_similar(pid, seg["source_text"])]
    return {"glossary": glossary, "tm_hints": tm, "context": build_context(db, pid, seg, project_root(workspace(), pid))}


# ---- jobs endpoints --------------------------------------------------------------------------------
@app.post("/api/projects/{pid}/jobs")
def create_job(pid: int, req: JobRequest, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    return start_job(pid, req).public()


@app.get("/api/projects/{pid}/job")
def latest_job(pid: int):
    jobs = [j for j in JOBS.values() if j.project_id == pid]
    return max(jobs, key=lambda j: j.started).public() if jobs else None


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job.public()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    job.stop.set()
    return job.public()


# ---- terminology / Bible / QA ----------------------------------------------------------------------
@app.get("/api/projects/{pid}/terms")
def terms(pid: int, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    return _terms(db, pid)


class TermPatch(BaseModel):
    translation: Optional[str] = Field(default=None, max_length=200)
    status: Optional[str] = None


@app.patch("/api/projects/{pid}/terms/{tid}")
def patch_term(pid: int, tid: int, body: TermPatch, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    if body.status not in (None, "suggested", "confirmed", "ignored"):
        raise HTTPException(400, "status must be suggested, confirmed or ignored")
    try:
        set_term(db, pid, tid, translation=body.translation, status=body.status)
    except KeyError:
        raise HTTPException(404, "term not found")
    sync_bible(db, pid, project_root(workspace(), pid))
    return next(t for t in _terms(db, pid) if t["id"] == tid)


@app.post("/api/projects/{pid}/terms/accept-all")
def accept_all_terms(pid: int, db: Database = Depends(get_db)):
    """Lock every suggested name that already has a rendering (the CLI's `terminology --accept-all`)."""
    project_or_404(db, pid)
    accepted = 0
    for t in _terms(db, pid):
        if t["status"] == "suggested" and t["translation"]:
            set_term(db, pid, t["id"], status="confirmed")
            accepted += 1
    sync_bible(db, pid, project_root(workspace(), pid))
    return {"accepted": accepted}


ENTITY_FIELDS = ("translation", "type", "gender", "pronouns", "notes", "relationship", "description")


@app.get("/api/projects/{pid}/bible")
def bible(pid: int, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    root = project_root(workspace(), pid)
    return {s: load_section(root, s) for s in SECTIONS if s not in ("chapter_summaries", "arc_summaries")}


class EntryIn(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    translation: Optional[str] = Field(default=None, max_length=200)
    type: Optional[str] = Field(default=None, max_length=60)
    gender: Optional[str] = Field(default=None, max_length=30)
    pronouns: Optional[str] = Field(default=None, max_length=60)
    notes: Optional[str] = Field(default=None, max_length=500)
    relationship: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=500)


@app.put("/api/projects/{pid}/bible/{section}")
def bible_entry(pid: int, section: str, body: EntryIn, db: Database = Depends(get_db)):
    """Create or update one entity by its source form. Notes survive later automatic syncs (spec §11)."""
    project_or_404(db, pid)
    if section not in ("characters", "locations", "organizations", "items", "abilities", "factions"):
        raise HTTPException(400, "not an entity section")
    root = project_root(workspace(), pid)
    items = [e for e in load_section(root, section) if isinstance(e, dict)]
    entry = next((e for e in items if e.get("source") == body.source), None)
    if entry is None:
        entry = {"origin": "user", "source": body.source}
        items.append(entry)
    entry.update({k: v for k, v in body.model_dump().items() if k in ENTITY_FIELDS and v is not None})
    save_section(root, section, items)
    return entry


@app.get("/api/projects/{pid}/qa")
def qa(pid: int, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    rows = db.conn.execute(
        "SELECT q.severity, q.type, q.message, s.id segment_id, s.order_index idx, c.id chapter_id, c.order_index chapter_idx "
        "FROM qa_results q JOIN segments s ON s.id=q.segment_id JOIN chapters c ON c.id=s.chapter_id WHERE q.project_id=? "
        "ORDER BY (q.severity='error') DESC, c.order_index, s.order_index", (pid,)).fetchall()
    return [{**dict(r), "idx": r["idx"] + 1} for r in rows]


# ---- export / validation ---------------------------------------------------------------------------
@app.get("/api/projects/{pid}/validate")
def validate(pid: int, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    return validate_project(db, pid)


@app.get("/api/projects/{pid}/export")
def export(pid: int, format: str = "epub", bilingual: bool = False, db: Database = Depends(get_db)):
    project_or_404(db, pid)
    if format not in ("epub", "txt", "cbz"):
        raise HTTPException(400, "format must be epub, txt or cbz")
    tmp = Path(tempfile.mkdtemp(prefix="st_export_"))
    out = tmp / f"translation.{format}"
    try:
        if format == "cbz":
            images.export_pages(db, pid, out)
        elif format == "epub":
            source = next((d["path"] for d in db.conn.execute("SELECT path FROM documents WHERE project_id=? AND format='epub' ORDER BY id", (pid,))
                           if Path(d["path"]).is_file()), None)
            export_epub(db, pid, source, out, bilingual=bilingual)
        else:
            export_txt(db, pid, out, bilingual=bilingual)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(500, f"export failed: {e}")
    name = re.sub(r"[^\w.\- ]", "_", db.get_project(pid)["name"]).strip() or "translation"
    return FileResponse(out, filename=f"{name}.{format}", background=BackgroundTask(shutil.rmtree, tmp, True))


# ---- frontend --------------------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
