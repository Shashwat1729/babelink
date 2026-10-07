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

HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Story Translate</title>
<style>
:root{--bg:#0f1220;--card:#1a1f33;--accent:#6ea8ff;--accent2:#8b5cf6;--text:#e6e9f2;--muted:#9aa3b2}
*{box-sizing:border-box}body{font-family:'Segoe UI',system-ui,sans-serif;background:linear-gradient(135deg,#0f1220,#141a2e);color:var(--text);margin:0;padding:2rem}
.wrap{max-width:1000px;margin:0 auto}h1{margin:0 0 .25rem;font-size:2rem}h1 span{color:var(--accent)}
.sub{color:var(--muted);margin-bottom:1.5rem}
.card{background:var(--card);border:1px solid #2a3150;border-radius:12px;padding:1.25rem;margin-bottom:1.25rem;box-shadow:0 8px 30px rgba(0,0,0,.35)}
.card h2{margin-top:0;font-size:1.1rem;color:var(--accent)}
input[type=file]{padding:.5rem;background:#0e1322;border:1px dashed #3a4366;border-radius:8px;color:var(--text)}
button{background:linear-gradient(135deg,var(--accent),var(--accent2));border:0;color:white;padding:.55rem 1.1rem;border-radius:8px;cursor:pointer;font-weight:600}
button:hover{filter:brightness(1.15)}
input[type=text]{background:#0e1322;border:1px solid #3a4366;border-radius:8px;color:var(--text);padding:.5rem .75rem;width:100%}
table{width:100%;border-collapse:collapse;margin-top:.5rem}th,td{border-bottom:1px solid #2a3150;padding:.6rem;text-align:left;vertical-align:top;font-size:.9rem}
th{color:var(--muted);font-weight:600}
td.src{width:42%}td.trn{width:42%}
.badge{display:inline-block;padding:.15rem .5rem;border-radius:999px;font-size:.72rem;font-weight:700}
.badge.UNPROCESSED{background:#3a4366;color:#cfd6e6}.badge.ANALYZED{background:#155e75;color:#a5f3fc}
.badge.TRANSLATED{background:#065f46;color:#a7f3d0}.badge.REFINED{background:#5b21b6;color:#ddd6fe}
.badge.NEEDS_REVIEW{background:#7c2d12;color:#fdba74}.badge.QA_PASSED{background:#166534;color:#bbf7d0}
pre{background:#0e1322;border:1px solid #2a3150;border-radius:8px;padding:1rem;overflow:auto}
.row{display:flex;gap:1rem;align-items:center;flex-wrap:wrap}.row>*{flex:1}
a button{margin-top:.5rem}
</style></head>
<body><div class="wrap">
<h1>Story <span>Translate</span></h1>
<div class="sub">Context-aware long-form fiction translation — local-first.</div>

<div class="card"><h2>1. Import</h2>
<div class="row"><input type=file id=f accept=".epub,.txt"><button onclick=upload()>Upload &amp; Analyze</button></div></div>

<div class="card"><h2>2. Run pipeline</h2>
<div class="row"><input type=text id=m value="huihui_ai/qwen3-abliterated:4b" placeholder="Model"></div>
<div class="row" style="margin-top:.75rem"><button onclick="call('/translate')">Translate</button><button onclick="call('/refine')">Refine</button><button onclick="call('/qa')">QA checks</button><a href="/export"><button>Download .txt</button></a></div></div>

<div class="card"><h2>Segments</h2><table id=t><tr><th>#</th><th>Source</th><th>Translation</th><th>State</th></tr></table></div>

<div class="card"><h2>QA findings</h2><pre id=q>[]</pre></div>
</div>
<script>
async function upload(){let d=new FormData();d.append('file',document.getElementById('f').files[0]);let r=await fetch('/upload',{method:'POST',body:d});alert(await r.text());load()}
async function call(u){let r=await fetch(u+'?model='+encodeURIComponent(document.getElementById('m').value),{method:'POST'});alert(await r.text());load()}
async function load(){let r=await fetch('/segments');let rows=await r.json();let h='<tr><th>#</th><th>Source</th><th>Translation</th><th>State</th></tr>';for(const s of rows){h+=`<tr><td>${s.order_index}</td><td class=src>${s.source_text}</td><td class=trn>${s.translated_text||''}</td><td><span class="badge ${s.state}">${s.state}</span></td></tr>`}document.getElementById('t').innerHTML=h;let q=await fetch('/qa');document.getElementById('q').textContent=JSON.stringify(await q.json(),null,2)}load();
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
