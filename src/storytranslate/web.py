"""Minimal web UI: FastAPI backend + single-page UI."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from .db import Database
from .exporters import export_epub, export_txt
from .importers import import_epub, import_txt
from .pipeline import analyze_project, translate_project
from .providers.base import MockProvider
from .providers.ollama import OllamaProvider
from .refinement import run_refinement

app = FastAPI(title="Story Translate")
WEB_DIR = Path("web_project")

HTML = """<!doctype html><html><head><title>Story Translate</title>
<style>body{font-family:system-ui;max-width:900px;margin:2rem auto}textarea,input{width:100%;margin:.25rem 0}table{width:100%;border-collapse:collapse}td,th{border:1px solid #ccc;padding:.4rem;vertical-align:top}</style></head>
<body><h1>Story Translate</h1>
<h2>1. Upload</h2>
<input type=file id=f accept=".epub,.txt"><button onclick=upload()>Upload & Analyze</button>
<h2>2. Pipeline</h2>
<label>Model: <input id=m value="huihui_ai/qwen3-abliterated:4b"></label>
<button onclick="call('/translate')">Translate</button>
<button onclick="call('/refine')">Refine</button>
<button onclick="call('/qa')">QA</button>
<a href="/export"><button>Download translation</button></a>
<h2>Segments</h2><table id=t><tr><th>#</th><th>Source</th><th>Translation</th><th>State</th></tr></table>
<h2>QA</h2><pre id=q></pre><script>
async function upload(){let d=new FormData();d.append('file',document.getElementById('f').files[0]);let r=await fetch('/upload',{method:'POST',body:d});alert(await r.text());load()}
async function call(u){let r=await fetch(u+'?model='+encodeURIComponent(document.getElementById('m').value),{method:'POST'});alert(await r.text());load()}
async function load(){let r=await fetch('/segments');let rows=await r.json();let h='<tr><th>#</th><th>Source</th><th>Translation</th><th>State</th></tr>';for(const s of rows){h+=`<tr><td>${s.order_index}</td><td>${s.source_text}</td><td>${s.translated_text||''}</td><td>${s.state}</td></tr>`}document.getElementById('t').innerHTML=h;let q=await fetch('/qa');document.getElementById('q').textContent=JSON.stringify(await q.json(),null,2)}load();
</script></body></html>"""


def _db() -> Database:
    WEB_DIR.mkdir(exist_ok=True)
    return Database(WEB_DIR / "project.db")


def _project_id(db: Database) -> int:
    row = db.conn.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()
    if row:
        return row["id"]
    return db.create_project("web", target_lang="en")


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


@app.post("/upload")
def upload(file: UploadFile = File(...)):
    db = _db()
    pid = _project_id(db)
    WEB_DIR.mkdir(exist_ok=True)
    dest = WEB_DIR / (uuid.uuid4().hex[:8] + "_" + file.filename)
    dest.write_bytes(file.file.read())
    if file.filename.lower().endswith(".epub"):
        import_epub(db, pid, dest)
    else:
        import_txt(db, pid, dest)
    info = analyze_project(db, pid, project_dir=WEB_DIR)
    db.close()
    return f"Imported + analyzed: {info}"


@app.post("/translate")
def translate(model: str = "huihui_ai/qwen3-abliterated:4b"):
    db = _db()
    pid = _project_id(db)
    n = translate_project(db, pid, OllamaProvider(model=model), project_dir=WEB_DIR)
    db.close()
    return f"Translated {n} segments"


@app.post("/refine")
def refine(model: str = "huihui_ai/qwen3-abliterated:4b"):
    db = _db()
    pid = _project_id(db)
    n = run_refinement(db, pid, OllamaProvider(model=model), str(WEB_DIR))
    db.close()
    return f"Refined {n} segments"


@app.get("/segments")
def segments():
    db = _db()
    pid = _project_id(db)
    rows = db.conn.execute(
        "SELECT order_index, source_text, translated_text, state FROM segments WHERE project_id=? ORDER BY chapter_id, order_index LIMIT 200",
        (pid,),
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


@app.get("/qa")
def qa():
    db = _db()
    pid = _project_id(db)
    rows = db.qa_for_project(pid)
    db.close()
    return [dict(r) for r in rows]


@app.get("/export")
def export():
    db = _db()
    pid = _project_id(db)
    doc = db.conn.execute("SELECT * FROM documents WHERE project_id=? ORDER BY id LIMIT 1", (pid,)).fetchone()
    out = WEB_DIR / "translation.txt"
    export_txt(db, pid, out)
    db.close()
    return FileResponse(out, filename="translation.txt")
