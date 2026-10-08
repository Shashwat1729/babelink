import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from lorebridge import web

H = {"X-Requested-With": "lorebridge"}
ZH = "林凡走进青云宗。\n\n大师兄说：“欢迎。”\n\n林凡点了点头。"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREBRIDGE_HOME", str(tmp_path))
    web.JOBS.clear()
    return TestClient(web.app)


def make_project(c, name="Demo"):
    r = c.post("/api/projects", json={"name": name, "source_lang": "zh", "target_lang": "en"}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def wait(c, job_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_index_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and "Lorebridge" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_guards(client):
    assert client.post("/api/projects", json={"name": "x"}).status_code == 403  # no CSRF header
    assert client.get("/api/projects", headers={"Host": "evil.example"}).status_code == 403  # DNS rebinding
    assert client.get("/api/projects").status_code == 200


def test_upload_rules(client):
    pid = make_project(client)
    bad = client.post(f"/api/projects/{pid}/import", files={"file": ("x.exe", b"MZ")}, headers=H)
    assert bad.status_code == 400
    ok = client.post(f"/api/projects/{pid}/import", files={"file": ("../../evil.txt", ZH.encode())}, headers=H)
    assert ok.status_code == 200 and ok.json()["chapters_added"] >= 1
    assert not (web.workspace().parent / "evil.txt").exists()
    assert client.get("/api/projects/999").status_code == 404


def test_paste_translate_continues_project(client):
    pid = make_project(client)
    r = client.post(f"/api/projects/{pid}/paste", json={"text": ZH, "title": "第一章", "translate": True, "provider": "mock"}, headers=H)
    assert r.status_code == 200 and len(r.json()["chapter_ids"]) == 1
    assert wait(client, r.json()["job"]["id"])["status"] == "done"
    r2 = client.post(f"/api/projects/{pid}/paste", json={"text": "林凡离开了。", "title": "第二章"}, headers=H)
    assert len(client.get(f"/api/projects/{pid}/chapters").json()) == 2
    cid2 = r2.json()["chapter_ids"][0]
    segs = client.get(f"/api/projects/{pid}/chapters/{cid2}/segments").json()["segments"]
    ctx = client.get(f"/api/projects/{pid}/segments/{segs[0]['id']}/context").json()
    assert "第一章" in ctx["context"] or "林凡" in ctx["context"]  # chapter 2 sees chapter 1


def test_job_edit_approve_retranslate_export(client, tmp_path):
    pid = make_project(client)
    cid = client.post(f"/api/projects/{pid}/paste", json={"text": ZH, "title": "Ch"}, headers=H).json()["chapter_ids"][0]
    job = client.post(f"/api/projects/{pid}/jobs", json={"kind": "translate", "provider": "mock"}, headers=H).json()
    assert wait(client, job["id"])["status"] == "done"
    segs = client.get(f"/api/projects/{pid}/chapters/{cid}/segments").json()["segments"]
    assert all(s["translation"] for s in segs)

    sid = segs[0]["id"]
    assert client.put(f"/api/projects/{pid}/segments/{sid}", json={"text": "Lin Fan entered.", "scope": "project"}, headers=H).json()["state"] == "HUMAN_APPROVED"
    assert client.put(f"/api/projects/{pid}/segments/{sid}", json={"text": "x", "scope": "bogus"}, headers=H).status_code == 400
    assert client.post(f"/api/projects/{pid}/segments/{segs[1]['id']}/approve", headers=H).json()["state"] == "HUMAN_APPROVED"

    rt = client.post(f"/api/projects/{pid}/segments/{segs[2]['id']}/retranslate", json={"kind": "translate", "provider": "mock"}, headers=H)
    assert rt.status_code == 200 and wait(client, rt.json()["id"])["status"] == "done"

    job = client.post(f"/api/projects/{pid}/jobs", json={"kind": "qa", "provider": "mock"}, headers=H).json()
    assert wait(client, job["id"])["status"] == "done"
    assert client.get(f"/api/projects/{pid}/validate").status_code == 200

    txt = client.get(f"/api/projects/{pid}/export?format=txt")
    assert txt.status_code == 200 and "Lin Fan entered." in txt.text
    epub = client.get(f"/api/projects/{pid}/export?format=epub&bilingual=true")
    assert epub.status_code == 200 and zipfile.ZipFile(io.BytesIO(epub.content)).testzip() is None
    assert client.get(f"/api/projects/{pid}/export?format=docx").status_code == 400


def test_one_job_per_project_and_errors(client):
    pid = make_project(client)
    client.post(f"/api/projects/{pid}/paste", json={"text": ZH}, headers=H)
    assert client.post(f"/api/projects/{pid}/jobs", json={"kind": "nope"}, headers=H).status_code == 400
    # unreachable Ollama: job ends in error with a message, nothing crashes
    job = client.post(f"/api/projects/{pid}/jobs", json={"kind": "translate", "provider": "ollama", "base_url": "http://127.0.0.1:9"}, headers=H).json()
    done = wait(client, job["id"], 60)
    assert done["status"] == "error" and done["error"]


def test_terms_and_bible(client):
    pid = make_project(client)
    client.post(f"/api/projects/{pid}/paste", json={"text": (ZH + "\n\n") * 4}, headers=H)
    terms = client.get(f"/api/projects/{pid}/terms").json()
    assert terms
    t = terms[0]
    r = client.patch(f"/api/projects/{pid}/terms/{t['id']}", json={"translation": "Lin Fan", "status": "confirmed"}, headers=H)
    assert r.status_code == 200 and r.json()["translation"] == "Lin Fan"
    assert client.patch(f"/api/projects/{pid}/terms/{t['id']}", json={"status": "weird"}, headers=H).status_code == 400
    e = client.put(f"/api/projects/{pid}/bible/characters", json={"source": "林凡", "gender": "male", "notes": "protagonist"}, headers=H)
    assert e.status_code == 200
    bible = client.get(f"/api/projects/{pid}/bible").json()
    assert any(x.get("source") == "林凡" and x.get("gender") == "male" for x in bible["characters"])
    assert client.put(f"/api/projects/{pid}/bible/terminology", json={"source": "x"}, headers=H).status_code == 400


def test_model_picker_endpoints(client):
    assert client.get("/api/models?provider=mock").json()["reachable"] is True
    down = client.get("/api/models?provider=ollama&base_url=http://127.0.0.1:9").json()
    assert down["reachable"] is False and down["error"] and down["installed"] == []
    assert {"ram_gb", "vram_gb"} <= set(down["hardware"])
    assert client.get("/api/models?provider=ollama&base_url=file:///etc/passwd").status_code == 400
    assert client.get("/api/models?provider=nope").status_code == 400
    ok = client.post("/api/models/test", json={"provider": "mock"}, headers=H).json()
    assert ok["ok"] and ok["text"]
    bad = client.post("/api/models/test", json={"provider": "ollama", "model": "x", "base_url": "http://127.0.0.1:9"}, headers=H).json()
    assert bad["ok"] is False and bad["error"]
    assert client.post("/api/models/test", json={"provider": "mock"}).status_code == 403  # CSRF header still required


def test_rename_delete_and_accept_all_names(client, tmp_path):
    pid = make_project(client, "Old name")
    client.post(f"/api/projects/{pid}/paste", json={"text": (ZH + "\n\n") * 4}, headers=H)
    assert client.patch(f"/api/projects/{pid}", json={"name": "Dream"}, headers=H).json()["name"] == "Dream"
    terms = client.get(f"/api/projects/{pid}/terms").json()
    client.patch(f"/api/projects/{pid}/terms/{terms[0]['id']}", json={"translation": "Lin Fan"}, headers=H)  # confirmed
    assert client.post(f"/api/projects/{pid}/terms/accept-all", headers=H).json() == {"accepted": 0}  # nothing suggested has a rendering yet
    client.patch(f"/api/projects/{pid}/terms/{terms[1]['id']}", json={"translation": "Senior Brother"}, headers=H)
    client.patch(f"/api/projects/{pid}/terms/{terms[1]['id']}", json={"status": "suggested"}, headers=H)  # a suggestion that has a rendering
    assert client.post(f"/api/projects/{pid}/terms/accept-all", headers=H).json() == {"accepted": 1}
    assert next(t for t in client.get(f"/api/projects/{pid}/terms").json() if t["id"] == terms[1]["id"])["status"] == "confirmed"

    assert (tmp_path / "projects" / str(pid)).exists()
    job = client.post(f"/api/projects/{pid}/jobs", json={"kind": "translate", "provider": "mock"}, headers=H).json()
    wait(client, job["id"])
    assert client.delete(f"/api/projects/{pid}", headers=H).json() == {"deleted": pid}
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.get("/api/projects").json() == []
    assert not (tmp_path / "projects" / str(pid)).exists()
    assert client.delete(f"/api/projects/{pid}", headers=H).status_code == 404
    other = make_project(client, "Other")  # ids are not reused and other works are untouched
    assert other != pid and client.get(f"/api/projects/{other}").status_code == 200
