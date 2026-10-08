"""Comic/manhua pages: block grouping, erase + typeset, page import/export, and the web endpoints.
Pixel tests use a fake detector so they run without OCR models; one end-to-end test runs the real OCR if installed."""
import io
import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cv2")
pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from lorebridge import images, service, web  # noqa: E402
from lorebridge.db import Database  # noqa: E402
from lorebridge.pipeline import translate_project  # noqa: E402
from lorebridge.providers.base import MockProvider  # noqa: E402

H = {"X-Requested-With": "lorebridge"}


def quad(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_group_blocks_merges_bubble_lines_and_orders_reading():
    lines = [(quad(300, 40, 420, 70), "你回来了", 0.9), (quad(310, 75, 410, 105), "吗？", 0.9),   # one bubble, two stacked lines
             (quad(20, 400, 140, 430), "快撤！", 0.9),                                              # separate bubble lower down
             (quad(20, 5, 130, 35), "砰！", 0.9)]                                                  # top left, read first
    blocks = images.group_blocks(lines)
    assert [b.text for b in blocks] == ["砰！", "你回来了吗？", "快撤！"]   # CJK lines join without a space
    assert blocks[1].box == (300, 40, 420, 105)
    assert images.group_blocks([(quad(0, 0, 90, 20), "Get out", 0.9), (quad(0, 24, 80, 44), "of here!", 0.9)])[0].text == "Get out of here!"


def page(tmp_path, name="p1.png", textured=False):
    """White speech bubble with a dark scribble standing in for lettering; `textured` = lettering straight on the art."""
    img = Image.new("RGB", (400, 300), (90, 120, 170))
    d = ImageDraw.Draw(img)
    if textured:
        for y in range(300):
            d.line([(0, y), (400, y)], fill=(40 + (y * 7) % 180, 90, 200 - (y * 5) % 120))
    if not textured:
        d.ellipse((40, 40, 360, 200), fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    for k in range(2):
        d.rectangle((120, 90 + k * 40, 280, 110 + k * 40), fill=(0, 0, 0))
    p = tmp_path / name
    img.save(p)
    return p


def fake_detect(img):
    return [images.Block((120, 90, 280, 150), [quad(120, 90, 280, 110), quad(120, 130, 280, 150)], "你回来了")]


def test_erase_removes_lettering_from_flat_bubble_and_inpaints_texture(tmp_path):
    for textured in (False, True):
        img = images.read_image(page(tmp_path, textured=textured))
        block = fake_detect(img)[0]
        before = (img[90:150, 120:280].sum(axis=2) < 60).sum()
        clean, fills = images.erase_text(img, [block])
        after = (clean[90:150, 120:280].sum(axis=2) < 60).sum()
        assert before > 5000 and after < before * 0.02
        assert (fills[0] is not None) == (not textured)  # flat bubble: filled with its own colour; textured: inpainted


def test_import_translate_render_export(tmp_path):
    db = Database(tmp_path / "ws" / "project.db")
    pid = service.create_project(db, "comic", "zh", "en")
    assert images.import_images(db, pid, page(tmp_path), detect=fake_detect) == 1
    ch = db.chapters(pid)[0]
    assert images.is_page(db, ch["id"])
    seg = db.segments_for_chapter(ch["id"])[0]
    assert seg["source_text"] == "你回来了"
    region = db.conn.execute("SELECT * FROM regions WHERE segment_id=?", (seg["id"],)).fetchone()
    assert (region["x0"], region["y1"], region["page_w"], region["page_h"]) == (120, 150, 400, 300)

    translate_project(db, pid, MockProvider(), project_dir=str(tmp_path / "root"))
    db.update_segment(seg["id"], translated="You're back!")
    png = images.render_page(db, ch["id"])
    out = Image.open(io.BytesIO(png))
    assert out.size == (400, 300)
    assert images.render_page(db, ch["id"], translated=False) != png          # original differs from the edited page
    cbz = images.export_pages(db, pid, tmp_path / "book.cbz")
    with zipfile.ZipFile(cbz) as z:
        assert z.namelist() == ["0001.png"]
    db.delete_project(pid)
    assert db.conn.execute("SELECT COUNT(*) n FROM regions").fetchone()["n"] == 0


def test_archive_pages_sorted_naturally_and_flat(tmp_path):
    db = Database(tmp_path / "ws" / "project.db")
    pid = service.create_project(db, "comic", "zh", "en")
    z = tmp_path / "vol.cbz"
    src = page(tmp_path).read_bytes()
    with zipfile.ZipFile(z, "w") as f:
        for n in ("p10.png", "p2.png", "../evil.png", "notes.txt"):
            f.writestr(n, src if n.endswith(".png") else "x")
    assert images.import_images(db, pid, z, detect=lambda img: []) == 3
    assert [c["title"] for c in db.chapters(pid)] == ["vol 0001", "vol 0002", "vol 0003"]
    for d in db.conn.execute("SELECT path FROM documents WHERE format='image'"):
        assert Path(d["path"]).parent == tmp_path / "ws" / "pages" / str(pid)  # nothing written outside the project folder
    with pytest.raises(ValueError):
        images.read_image(b"not an image")


def test_typeset_fits_long_text_inside_round_bubble(tmp_path):
    img = images.read_image(page(tmp_path))
    block = fake_detect(img)[0]
    clean, fills = images.erase_text(img, [block])
    out = images.typeset(clean, [block], ["Luo Tian struggled to his feet, his handsome face bruised and swollen."], fills)
    ink = ((out[:, :, :].sum(axis=2) < 150) & (clean[:, :, :].sum(axis=2) >= 150)).nonzero()
    assert ink[0].size > 200                                   # text was drawn
    assert ink[1].min() > 40 and ink[1].max() < 360            # ...and stays inside the ellipse's horizontal extent
    assert ink[0].min() > 40 and ink[0].max() < 200            # ...and vertical extent


# ---- web ---------------------------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREBRIDGE_HOME", str(tmp_path))
    web.JOBS.clear()
    return TestClient(web.app)


def wait(c, job_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def _cjk_font():
    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    import make_sample_page
    try:
        make_sample_page.font(20)
    except SystemExit:
        return None
    return make_sample_page


def test_web_pages_end_to_end_with_real_ocr(client, tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    sample = _cjk_font()
    if sample is None:
        pytest.skip("no CJK font on this machine to draw the sample page")
    img = tmp_path / "第一页.png"
    sample.make(str(img))
    pid = client.post("/api/projects", json={"name": "Manhua", "source_lang": "zh", "target_lang": "en"}, headers=H).json()["id"]
    assert client.post(f"/api/projects/{pid}/import", files={"file": ("a.png", img.read_bytes())}, headers=H).status_code == 400  # images go via /pages
    r = client.post(f"/api/projects/{pid}/pages", files=[("files", ("page.png", img.read_bytes(), "image/png"))], headers=H)
    assert r.status_code == 200, r.text
    job = wait(client, r.json()["job"]["id"])
    assert job["status"] == "done" and job["result"]["pages_added"] == 1, job

    chapters = client.get(f"/api/projects/{pid}/chapters").json()
    assert len(chapters) == 1 and chapters[0]["page"] and chapters[0]["segments"] == 3
    cid = chapters[0]["id"]
    segs = client.get(f"/api/projects/{pid}/chapters/{cid}/segments").json()
    assert segs["chapter"]["page"] and [s["source"] for s in segs["segments"]][1:] == ["这废物不会死了吧？", "快撤！"]

    # OCR slips are fixable, and a fix sends the block back to the queue
    sid = segs["segments"][2]["id"]
    assert client.put(f"/api/projects/{pid}/segments/{sid}/source", json={"text": "快撤吧！"}, headers=H).json()["source"] == "快撤吧！"
    assert client.get(f"/api/projects/{pid}/chapters/{cid}/segments").json()["segments"][2]["state"] == "ANALYZED"

    job = wait(client, client.post(f"/api/projects/{pid}/jobs", json={"kind": "translate", "provider": "mock", "model": "mock"}, headers=H).json()["id"])
    assert job["status"] == "done"
    for version in ("original", "translated"):
        pr = client.get(f"/api/projects/{pid}/chapters/{cid}/page?version={version}")
        assert pr.status_code == 200 and pr.headers["content-type"] == "image/png" and pr.content[:8] == b"\x89PNG\r\n\x1a\n"
    cbz = client.get(f"/api/projects/{pid}/export?format=cbz")
    assert cbz.status_code == 200 and zipfile.ZipFile(io.BytesIO(cbz.content)).namelist() == ["0001.png"]
    assert client.get(f"/api/projects/{pid}").json()["pages"] == 1
    assert client.delete(f"/api/projects/{pid}", headers=H).status_code == 200


def test_source_fix_refused_for_text_chapters(client):
    pid = client.post("/api/projects", json={"name": "Novel"}, headers=H).json()["id"]
    client.post(f"/api/projects/{pid}/paste", json={"text": "林凡走进青云宗。", "title": "一"}, headers=H)
    cid = client.get(f"/api/projects/{pid}/chapters").json()[0]["id"]
    sid = client.get(f"/api/projects/{pid}/chapters/{cid}/segments").json()["segments"][0]["id"]
    assert client.put(f"/api/projects/{pid}/segments/{sid}/source", json={"text": "x"}, headers=H).status_code == 400
    assert client.get(f"/api/projects/{pid}/chapters/{cid}/page").status_code == 404   # not an image page


def test_job_refuses_upload_names_that_climb_out_of_the_folder(client):
    pid = client.post("/api/projects", json={"name": "Manhua"}, headers=H).json()["id"]
    j = client.post(f"/api/projects/{pid}/jobs", json={"kind": "import_images", "uploads": ["../../x.png"]}, headers=H)
    job = wait(client, j.json()["id"])
    assert job["status"] == "error" and "invalid upload name" in job["error"]
