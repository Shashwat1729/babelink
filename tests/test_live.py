"""Live visibility: streamed text from providers, word-limit batches, and the job's live fields."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from lorebridge import service, web
from lorebridge.pipeline import count_words, take_words, translate_project
from lorebridge.providers.base import MockProvider
from lorebridge.providers.ollama import OllamaProvider
from lorebridge.providers.openai_compatible import OpenAICompatibleProvider
from lorebridge.story_bible import project_root

H = {"X-Requested-With": "lorebridge"}
PARAS = ["林凡走进青云宗。", "大师兄说：“欢迎。”", "林凡点了点头。", "他们一起走了。"]  # 7, 6, 6, 6 CJK characters (punctuation is not a word)


def project(tmp_path, paras=PARAS):
    db = service.open_workspace(tmp_path)
    pid = service.create_project(db, "novel", "zh", "en")
    service.add_pasted_chapter(db, tmp_path, pid, "\n\n".join(paras), "Ch")
    return db, pid, project_root(tmp_path, pid)


def test_count_words_counts_cjk_by_character_and_latin_by_word():
    assert count_words("林凡走进青云宗。") == 7
    assert count_words("Lin Fan didn't leave 3 times.") == 6
    assert count_words("") == 0


def test_take_words_never_cuts_a_paragraph_and_never_returns_nothing():
    rows = [{"source_text": t} for t in PARAS]
    assert take_words(rows, None) == rows
    assert len(take_words(rows, 1)) == 1  # one paragraph is already over the limit
    assert len(take_words(rows, 10)) == 2  # 7 words is under 10, 7 + 6 reaches it
    assert take_words(rows, 10_000) == rows


def test_translate_project_stops_at_the_word_limit_and_reports_every_paragraph(tmp_path):
    db, pid, root = project(tmp_path)
    events, seen_total = [], []
    n = translate_project(db, pid, MockProvider(), project_dir=root, word_limit=10,
                          on_text=lambda s, text, final: events.append((s["order_index"], text, final)),
                          on_progress=lambda d, t, m: seen_total.append(t))
    assert n == 2 and set(seen_total) == {2}  # progress total reflects the batch, not the whole chapter
    assert [e for e in events if e[2]] == [(0, "林凡走进青云宗。", True), (1, "大师兄说：“欢迎。”", True)]
    assert events[0] == (0, "", False)  # the paragraph is announced before the model answers
    assert any(e[0] == 0 and e[1] and not e[2] for e in events)  # and streamed text arrives before the final event
    rows = db.open_segments(pid)
    assert len(rows) == 2  # the rest waits for the next batch
    assert translate_project(db, pid, MockProvider(), project_dir=root, word_limit=10) == 2  # next batch resumes
    assert db.open_segments(pid) == []


def test_refusal_still_finishes_the_live_event(tmp_path):
    db, pid, root = project(tmp_path, PARAS[:1])
    events = []
    translate_project(db, pid, MockProvider(refuse=("林凡",)), project_dir=root, on_text=lambda s, text, final: events.append((text, final)))
    assert events[-1] == ("", True)


class FakeStream:
    """Stands in for httpx.stream(...): a context manager with the response API the providers use."""

    def __init__(self, lines, status=200, text=""):
        self.lines, self.status_code, self.text = lines, status, text
        self.request = httpx.Request("POST", "http://x")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=self.request, response=httpx.Response(self.status_code, request=self.request))

    def iter_lines(self):
        return iter(self.lines)


def test_ollama_streams_pieces_and_counts_tokens(monkeypatch):
    bodies = []
    lines = [json.dumps({"message": {"content": "Lin "}, "done": False}), "",
             json.dumps({"message": {"content": "Fan came."}, "done": False}),
             json.dumps({"message": {"content": ""}, "done": True, "prompt_eval_count": 11, "eval_count": 4})]

    def fake_stream(method, url, json, timeout):
        bodies.append(dict(json))
        return FakeStream(lines)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    seen = []
    p = OllamaProvider("qwen3:4b")
    assert p._chat_once("s", "u", 0.2, 50, seen.append) == "Lin Fan came."
    assert seen == ["Lin ", "Lin Fan came."]  # each call carries the text so far
    assert bodies[0]["stream"] is True and bodies[0]["think"] is False
    assert p.usage == {"calls": 0, "prompt_tokens": 11, "completion_tokens": 4, "refusals": 0}


def test_ollama_stream_retries_without_think_on_old_servers(monkeypatch):
    bodies = []

    def fake_stream(method, url, json, timeout):
        bodies.append(dict(json))
        if "think" in json:
            return FakeStream([], status=400, text='{"error":"model does not support think"}')
        return FakeStream([json_line("ok", done=True)])

    def json_line(content, done):
        return json.dumps({"message": {"content": content}, "done": done})

    monkeypatch.setattr(httpx, "stream", fake_stream)
    assert OllamaProvider("old")._chat_once("s", "u", 0.2, 50, lambda t: None) == "ok"
    assert "think" in bodies[0] and "think" not in bodies[1]


def test_ollama_stream_error_line_is_raised_so_the_job_can_retry(monkeypatch):
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: FakeStream([json.dumps({"error": "model not found"})]))
    with pytest.raises(httpx.TransportError, match="model not found"):
        OllamaProvider("x")._chat_once("s", "u", 0.2, 50, lambda t: None)


def test_openai_compatible_streams_sse(monkeypatch):
    sent = []

    def sse(piece=None, usage=None):
        d = {"choices": [{"delta": {"content": piece}}]} if piece is not None else {"choices": [], "usage": usage}
        return "data: " + json.dumps(d)

    def fake_stream(method, url, json, headers, timeout):
        sent.append(json)
        return FakeStream([": keep-alive", sse("Lin "), "", sse("Fan"), sse(usage={"prompt_tokens": 5, "completion_tokens": 2}), "data: [DONE]"])

    monkeypatch.setattr(httpx, "stream", fake_stream)
    seen = []
    p = OpenAICompatibleProvider(model="m")
    assert p._chat_once("s", "u", 0.2, 50, seen.append) == "Lin Fan"
    assert seen == ["Lin ", "Lin Fan"] and sent[0]["stream"] is True
    assert (p.usage["prompt_tokens"], p.usage["completion_tokens"]) == (5, 2)


def test_chat_provider_only_streams_when_asked(monkeypatch):
    """A backend written before streaming existed (no on_delta parameter) must keep working."""
    class Old(OllamaProvider):
        def _chat_once(self, system, user, temperature, max_tokens):
            return "plain"

    assert Old("m").translate("林凡来了。") == "plain"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREBRIDGE_HOME", str(tmp_path))
    web.JOBS.clear()
    return TestClient(web.app)


def wait_done(c, job_id, timeout=20):
    import time

    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_job_reports_words_recent_paragraphs_and_stops_at_max_words(client):
    pid = client.post("/api/projects", json={"name": "Demo", "source_lang": "zh", "target_lang": "en"}, headers=H).json()["id"]
    client.post(f"/api/projects/{pid}/paste", json={"text": "\n\n".join(PARAS), "title": "第一章"}, headers=H)
    job = client.post(f"/api/projects/{pid}/jobs", json={"kind": "translate", "provider": "mock", "max_words": 10}, headers=H).json()
    done = wait_done(client, job["id"])
    assert done["status"] == "done" and done["result"] == {"translated": 2, "words": 13}
    assert done["words"] == 13 and done["total"] == 2
    assert [r["index"] for r in done["recent"]] == [1, 2] and all(r["final"] and r["text"] for r in done["recent"])
    assert done["recent"][0]["chapter"] == 1 and done["recent"][0]["chapter_title"]
    assert done["live"] == {}  # nothing is "being written" once the job is over
    chapters = client.get(f"/api/projects/{pid}/chapters").json()
    assert chapters[0]["states"] == {"TRANSLATED": 2, "ANALYZED": 2}  # only the batch was written

    again = client.post(f"/api/projects/{pid}/jobs", json={"kind": "translate", "provider": "mock", "max_words": 10}, headers=H).json()
    assert wait_done(client, again["id"])["result"]["translated"] == 2  # the next batch picks up where the first stopped


def test_job_rejects_a_zero_word_limit(client):
    pid = client.post("/api/projects", json={"name": "Demo"}, headers=H).json()["id"]
    assert client.post(f"/api/projects/{pid}/jobs", json={"kind": "translate", "provider": "mock", "max_words": 0}, headers=H).status_code == 422


# ---- double submit: a repeated click must not add or translate twice ------------------------------------
def _paste(c, pid, key=None, **body):
    headers = {**H, **({"Idempotency-Key": key} if key else {})}
    return c.post(f"/api/projects/{pid}/paste", json={"text": "\n\n".join(PARAS), "title": "第一章", "provider": "mock", **body}, headers=headers)


def _project(c):
    return c.post("/api/projects", json={"name": "Demo", "source_lang": "zh", "target_lang": "en"}, headers=H).json()["id"]


def test_paste_with_the_same_key_adds_one_chapter_and_one_job(client):
    pid = _project(client)
    first, again = _paste(client, pid, "k1", translate=True), _paste(client, pid, "k1", translate=True)
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert len(client.get(f"/api/projects/{pid}/chapters").json()) == 1
    assert len([j for j in web.JOBS.values() if j.project_id == pid]) == 1
    wait_done(client, first.json()["job"]["id"])
    assert len(client.get(f"/api/projects/{pid}/chapters").json()) == 1


def test_paste_with_different_keys_is_a_new_chapter(client):
    pid = _project(client)
    assert _paste(client, pid, "a").status_code == _paste(client, pid, "b").status_code == 200
    assert len(client.get(f"/api/projects/{pid}/chapters").json()) == 2


def test_concurrent_double_click_adds_once(client):
    """Two requests with the same key in flight at the same moment, as a fast double click sends them."""
    from concurrent.futures import ThreadPoolExecutor

    pid = _project(client)
    with ThreadPoolExecutor(2) as pool:
        out = list(pool.map(lambda _: _paste(client, pid, "same", translate=True), range(2)))
    assert [r.status_code for r in out] == [200, 200] and out[0].json() == out[1].json()
    assert len(client.get(f"/api/projects/{pid}/chapters").json()) == 1
    assert len([j for j in web.JOBS.values() if j.project_id == pid]) == 1


def test_failed_request_is_not_remembered(client):
    pid = _project(client)
    assert client.post(f"/api/projects/{pid}/paste", json={"text": "  "}, headers={**H, "Idempotency-Key": "k"}).status_code in (400, 422)
    assert _paste(client, pid, "k").status_code == 200  # the retry with the same key still works


def test_paste_and_translate_while_busy_is_refused_before_anything_is_added(client):
    pid = _project(client)
    web.JOBS["busy"] = web.Job("busy", pid, "translate")  # status "running"
    r = _paste(client, pid, translate=True)
    assert r.status_code == 409 and "already running" in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}/chapters").json() == []  # no chapter left behind
    assert _paste(client, pid, translate=False).status_code == 200  # adding without translating is still fine while busy


def test_losing_the_job_slot_after_adding_reports_it(client, monkeypatch):
    pid = _project(client)

    def lose(*a, **k):
        raise web.HTTPException(409, "another job is already running for this project")

    monkeypatch.setattr(web, "start_job", lose)
    r = _paste(client, pid, translate=True)
    assert r.status_code == 200 and r.json()["job"] is None and "already running" in r.json()["job_error"]
    assert len(r.json()["chapter_ids"]) == 1


def test_creating_a_work_twice_with_the_same_key_makes_one(client):
    body = {"name": "Once", "source_lang": "zh", "target_lang": "en"}
    a = client.post("/api/projects", json=body, headers={**H, "Idempotency-Key": "w"})
    b = client.post("/api/projects", json=body, headers={**H, "Idempotency-Key": "w"})
    assert a.json()["id"] == b.json()["id"] and len(client.get("/api/projects").json()) == 1


def test_idempotency_memory_is_bounded(client, monkeypatch):
    monkeypatch.setattr(web, "_IDEM_KEEP", 3)
    web._IDEM.clear()
    pid = _project(client)
    for i in range(10):
        _paste(client, pid, f"k{i}")
    assert len(web._IDEM) <= 3


def test_page_upload_while_busy_is_refused_and_leaves_no_files(client):
    pytest.importorskip("rapidocr_onnxruntime")
    pid = _project(client)
    web.JOBS["busy"] = web.Job("busy", pid, "translate")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    r = client.post(f"/api/projects/{pid}/pages", files=[("files", ("p.png", png, "image/png"))], headers=H)
    assert r.status_code == 409
    assert not list((web.workspace() / "uploads" / str(pid)).glob("*")) if (web.workspace() / "uploads" / str(pid)).exists() else True


# ---- a running job must not clobber what a person did meanwhile ------------------------------------------
class Interfering(MockProvider):
    """Calls `act()` while the model is 'writing' the paragraph whose source is `when`, like a person editing in the browser."""

    def __init__(self, when, act, hook="translate"):
        super().__init__()
        self.when, self.act, self.hook = when, act, hook

    def translate(self, source, *a, **kw):
        out = super().translate(source, *a, **kw)
        if self.hook == "translate" and source == self.when:
            self.act()
        return out

    def refine(self, source, draft, *a, **kw):
        if self.hook == "refine" and source == self.when:
            self.act()
        return draft + " (refined)"


def test_translate_never_overwrites_a_paragraph_a_person_edited_meanwhile(tmp_path):
    from lorebridge.review import edit_segment

    db, pid, root = project(tmp_path)
    ids = [s["id"] for s in db.open_segments(pid)]

    def person():  # edits the paragraph being written AND one the batch has not reached yet
        edit_segment(db, pid, ids[0], "Human one")
        edit_segment(db, pid, ids[2], "Human three")

    n = translate_project(db, pid, Interfering(PARAS[0], person), project_dir=root)
    got = {s["id"]: (s["translated_text"], s["state"]) for s in db.segments_for_chapter(db.chapters(pid)[0]["id"])}
    assert got[ids[0]] == ("Human one", "HUMAN_APPROVED") and got[ids[2]] == ("Human three", "HUMAN_APPROVED")
    assert got[ids[1]][1] == "TRANSLATED" and got[ids[3]][1] == "TRANSLATED"
    assert n == 2  # only the paragraphs the model really wrote count
    assert db.conn.execute("SELECT COUNT(*) n FROM qa_results WHERE segment_id=?", (ids[0],)).fetchone()["n"] == 0  # no stale findings on the human's text


def test_refine_never_overwrites_a_paragraph_a_person_edited_meanwhile(tmp_path):
    from lorebridge.refinement import run_refinement
    from lorebridge.review import edit_segment

    db, pid, root = project(tmp_path)
    translate_project(db, pid, MockProvider(), project_dir=root)
    ids = [s["id"] for s in db.segments_for_chapter(db.chapters(pid)[0]["id"])]
    run_refinement(db, pid, Interfering(PARAS[0], lambda: edit_segment(db, pid, ids[0], "Human one"), hook="refine"), root)
    first, second = db.get_segment(ids[0]), db.get_segment(ids[1])
    assert (first["translated_text"], first["state"]) == ("Human one", "HUMAN_APPROVED")
    assert second["state"] in ("REFINED", "NEEDS_REVIEW")  # the paragraphs nobody touched still went through refinement


def test_stop_ends_the_paragraph_being_written_not_just_the_next_one(tmp_path):
    import threading

    db, pid, root = project(tmp_path)
    flag = threading.Event()

    class Slow(MockProvider):
        def translate(self, source, *a, on_delta=None, **kw):
            on_delta("Lin")
            flag.set()  # Stop pressed while the model is mid-sentence
            on_delta("Lin Fan")  # the next piece of the stream must end the paragraph
            raise AssertionError("the stream should have been abandoned")

    n = translate_project(db, pid, Slow(), project_dir=root, should_stop=flag.is_set, on_text=lambda *a: None)
    assert n == 0 and len(db.open_segments(pid)) == len(PARAS)  # nothing half-written was saved


def test_job_reports_that_it_is_stopping(client):
    import threading

    job = web.Job("j", 1, "translate")
    assert job.public()["stopping"] is False
    job.stop.set()
    assert job.public()["stopping"] is True
    job.status = "cancelled"
    assert job.public()["stopping"] is False  # once it has stopped there is nothing left to wait for


def test_retranslate_puts_the_paragraph_back_when_the_job_slot_is_lost(client, monkeypatch):
    pid = _project(client)
    cid = _paste(client, pid).json()["chapter_ids"][0]
    wait_done(client, client.post(f"/api/projects/{pid}/jobs", json={"kind": "translate", "provider": "mock"}, headers=H).json()["id"])
    sid = client.get(f"/api/projects/{pid}/chapters/{cid}/segments").json()["segments"][0]["id"]

    def lose(*a, **k):
        raise web.HTTPException(409, "another job is already running for this project")

    monkeypatch.setattr(web, "start_job", lose)
    r = client.post(f"/api/projects/{pid}/segments/{sid}/retranslate", json={"kind": "translate", "provider": "mock"}, headers=H)
    assert r.status_code == 409
    seg = client.get(f"/api/projects/{pid}/chapters/{cid}/segments").json()["segments"][0]
    assert seg["state"] == "TRANSLATED" and seg["translation"]  # not stranded as "Outdated"


# ---- the same file twice ------------------------------------------------------------------------------
def test_same_file_added_twice_is_refused_and_does_not_duplicate_chapters(client):
    pid = _project(client)
    text = "第一章\n\n" + "\n\n".join(PARAS)
    up = lambda name, data: client.post(f"/api/projects/{pid}/import", files={"file": (name, data)}, headers=H)  # noqa: E731
    assert up("book.txt", text.encode()).status_code == 200
    again = up("book.txt", text.encode())
    renamed = up("copy of book.txt", text.encode())
    assert again.status_code == renamed.status_code == 409 and "already added" in again.json()["detail"]
    assert len(client.get(f"/api/projects/{pid}/chapters").json()) == 1
    assert up("book2.txt", (text + "\n\n新的一段。").encode()).status_code == 200  # a different file is fine


def test_unreadable_file_can_be_retried(client):
    pid = _project(client)
    up = lambda: client.post(f"/api/projects/{pid}/import", files={"file": ("bad.epub", b"not a zip")}, headers=H)  # noqa: E731
    assert up().status_code == 400
    assert up().status_code == 400  # still "could not read", never "already added": the failed upload left nothing behind


def test_import_does_not_block_the_server(client, monkeypatch):
    """A long import runs in a worker thread, so the event loop (and the job polling in the browser) keeps answering."""
    import threading

    seen = []
    real = web._store_and_import

    def spy(*a):
        seen.append(threading.current_thread() is threading.main_thread())
        return real(*a)

    monkeypatch.setattr(web, "_store_and_import", spy)
    pid = _project(client)
    client.post(f"/api/projects/{pid}/import", files={"file": ("b.txt", "\n\n".join(PARAS).encode())}, headers=H)
    assert seen == [False]
