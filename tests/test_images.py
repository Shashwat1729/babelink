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

from babelink import images, service, web  # noqa: E402
from babelink.db import Database  # noqa: E402
from babelink.pipeline import translate_project  # noqa: E402
from babelink.providers.base import MockProvider  # noqa: E402

H = {"X-Requested-With": "babelink"}


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
    monkeypatch.setenv("BABELINK_HOME", str(tmp_path))
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


def _korean_model_files():
    import os
    d = Path(os.environ.get("BABELINK_OCR_MODELS", r"D:\StoryTranslation_data\ocr_models"))
    rec, keys = d / "korean_mobile_v2.0_rec_infer.onnx", d / "korean_dict.txt"
    return (rec, keys) if rec.is_file() and keys.is_file() else None


def test_korean_ocr_finds_lines_where_default_finds_none(tmp_path):
    """Korean pages use the Korean rec model (weights on D:, not pip): the
    default engine reads near-nothing on Korean text, the ko engine finds
    every content line (syllable confusions allowed — detection is the bar)."""
    pytest.importorskip("rapidocr_onnxruntime")
    if _korean_model_files() is None:
        pytest.skip("no Korean OCR weights (BABELINK_OCR_MODELS)")
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf", 44)
    except OSError:
        pytest.skip("no Korean font on this machine")
    from babelink.images import detect_blocks
    img = Image.new("RGB", (700, 340), "white")
    d = ImageDraw.Draw(img)
    for i, t in enumerate(["나노 머신이 깨어났다", "천마가 돌아왔다", "빨리 도망쳐!", "사형, 조심하세요"]):
        d.text((40, 30 + i * 75), t, font=font, fill="black")
    p = tmp_path / "ko.png"
    img.save(p)
    import cv2
    cv = cv2.imread(str(p))
    assert len(detect_blocks(cv)) <= 1  # control: default engine is blind here
    from babelink.images import ocr_lines
    lines = [t for _, t, s in ocr_lines(cv, lang="ko") if s >= 0.5]
    joined = " ".join(lines)
    # detection bar (all 4 content lines surface) + the phrases the v1 mobile
    # model reads cleanly; its syllable confusions (돌아왔다→아왔다) are a
    # documented weakness, fixed by a newer ko rec model, not by code.
    assert len(lines) >= 4 and "깨어났다" in joined and "도망" in joined, joined


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


# ---- reading order, two-pass OCR, refit clamp --------------------------------


def _synthetic_page(rects, w=700, h=700):
    """Synthetic page built with cv2 rectangles (white bg, outlined boxes) for
    the given (x0, y0, x1, y1) text areas; returns the cv2 image."""
    import numpy as np
    import cv2 as _cv2
    img = np.full((h, w, 3), 255, np.uint8)
    for (x0, y0, x1, y1) in rects:
        _cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 0), 3)
        _cv2.rectangle(img, (x0 + 4, y0 + 4), (x1 - 4, y1 - 4), (220, 220, 220), -1)
    return img


def test_reading_order_vertical_columns_rtl_synthetic_page():
    """Vertical manga: a synthetic page (cv2 rectangles) with two tall columns
    each holding a top and a bottom bubble must read right-to-left,
    top-to-bottom within a column. Input order is deliberately shuffled so
    only geometry decides."""
    import cv2 as _cv2  # noqa: F401
    # (x0, y0, x1, y1): right column first in reading order
    r_top = (500, 20, 550, 140)
    r_bot = (500, 220, 550, 340)
    l_top = (200, 20, 250, 140)
    l_bot = (200, 220, 250, 340)
    page_img = _synthetic_page([r_top, r_bot, l_top, l_bot])
    assert page_img.shape[0] == 700 and page_img.shape[1] == 700
    # fake OCR lines at exactly the drawn rectangles, in scrambled order
    lines = [
        (quad(*l_bot), "L-bottom", 0.9),
        (quad(*r_top), "R-top", 0.9),
        (quad(*l_top), "L-top", 0.9),
        (quad(*r_bot), "R-bottom", 0.9),
    ]
    blocks = images.group_blocks(lines)
    assert [b.text for b in blocks] == ["R-top", "R-bottom", "L-top", "L-bottom"]
    # explicit mode agrees; horizontal mode gives the wrong (pre-fix) order
    rtl = images.order_blocks(blocks, mode="rtl_columns")
    assert [b.text for b in rtl] == ["R-top", "R-bottom", "L-top", "L-bottom"]
    horiz = images.order_blocks(blocks, mode="horizontal")
    assert [b.text for b in horiz] == ["L-top", "R-top", "L-bottom", "R-bottom"]
    assert images._layout_mode(blocks, mode="auto") == "rtl_columns"


def test_reading_order_horizontal_synthetic_page_rows():
    """Horizontal page: rows top-to-bottom, left-to-right, even when the
    input lines arrive reversed."""
    r_top_left = (20, 20, 170, 60)
    r_top_right = (350, 20, 500, 60)
    r_bottom = (20, 300, 170, 340)
    _synthetic_page([r_top_left, r_top_right, r_bottom])
    lines = [
        (quad(*r_bottom), "third", 0.9),
        (quad(*r_top_right), "second", 0.9),
        (quad(*r_top_left), "first", 0.9),
    ]
    blocks = images.group_blocks(lines)
    assert [b.text for b in blocks] == ["first", "second", "third"]
    assert images._layout_mode(blocks, mode="auto") == "horizontal"


def test_two_pass_reocr_triggers_on_low_confidence_block(tmp_path, monkeypatch):
    """Low-confidence block is re-OCR'd at 2x upscale and the higher-scoring
    reading wins; a high-confidence block is not re-OCR'd. No word lists."""
    import numpy as np
    img = np.full((300, 400, 3), 255, np.uint8)
    calls = []

    def fake_ocr(im, lang=""):
        calls.append(im.shape[:2])
        if len(calls) == 1:
            return [(quad(100, 100, 200, 160), "lowread", 0.60)]
        # upscaled crop call: return a better reading in upscaled coords
        uh, uw = im.shape[:2]
        return [([[0, 0], [uw, 0], [uw, uh], [0, uh]], "betterread", 0.92)]

    monkeypatch.setattr(images, "ocr_lines", fake_ocr)
    blocks = images.detect_blocks(img, min_score=0.5, lang="")
    assert len(calls) == 2                                  # full page + one upscaled re-OCR
    pad = 6
    exp_crop_h, exp_crop_w = 160 + pad - (100 - pad), 200 + pad - (100 - pad)
    assert calls[0] == (300, 400)
    assert calls[1] == (exp_crop_h * 2, exp_crop_w * 2)     # proves 2x upscale
    assert [b.text for b in blocks] == ["betterread"]       # higher score wins
    assert blocks[0].score == pytest.approx(0.92)

    calls.clear()

    def fake_ocr_high(im, lang=""):
        calls.append(im.shape[:2])
        return [(quad(100, 100, 200, 160), "confident", 0.95)]

    monkeypatch.setattr(images, "ocr_lines", fake_ocr_high)
    blocks = images.detect_blocks(img, min_score=0.5, lang="")
    assert len(calls) == 1                                  # never re-OCR'd
    assert [b.text for b in blocks] == ["confident"]


def test_two_pass_keeps_original_when_upscaled_scores_lower(monkeypatch):
    import numpy as np
    img = np.full((300, 400, 3), 255, np.uint8)

    def fake_ocr(im, lang=""):
        return [(quad(100, 100, 200, 160), "original", 0.60)]

    monkeypatch.setattr(images, "ocr_lines", fake_ocr)
    blocks = images.detect_blocks(img, min_score=0.5, lang="")
    assert [b.text for b in blocks] == ["original"]


def _ink_outside_polygon(out, clean, polygon):
    """Copies of ink pixels (new dark pixels) whose (x, y) lie outside
    `polygon` (list of [x, y] vertices, single convex ring)."""
    import numpy as np
    import cv2
    ink = ((out[:, :, :].sum(axis=2) < 150) & (clean[:, :, :].sum(axis=2) >= 150))
    mask = np.zeros((out.shape[0], out.shape[1]), np.uint8)
    cv2.fillPoly(mask, [np.array(polygon, np.int32)], 255)
    ys, xs = np.nonzero(ink)
    return [(int(x), int(y)) for x, y in zip(xs, ys) if not mask[y, x]]


def test_typeset_refit_never_overflows_bubble_mask(tmp_path):
    """Fitted text must never spill outside the bubble mask polygon: try a
    sequence of increasingly long translations on the round-bubble fixture and
    assert no ink pixel lands outside the ellipse."""
    img = images.read_image(page(tmp_path))
    block = fake_detect(img)[0]
    clean, fills = images.erase_text(img, [block])
    texts = [
        "Hi",
        "They carried him into the courtyard and laid him on the stone bench.",
        "Luo Tian struggled to his feet, his handsome face bruised and swollen. " * 3,
        "A" * 200,
    ]
    # widen the ellipse polygon slightly: use the inscribed rect (_bubble_area)
    # shrunk into a polygon, which we also require to contain the ink.
    for text in texts:
        blk = images.Block(block.box, list(block.polys), "", block.score)
        flags: list[bool] = []
        out = images.typeset(clean, [blk], [text], list(fills), flags=flags)
        assert flags == [False] and blk.needs_review is False
        ax0, ay0, ax1, ay1 = images._bubble_area(clean, blk.box)
        poly = images._area_polygon((ax0 + 2, ay0 + 2, ax1 - 2, ay1 - 2))
        assert _ink_outside_polygon(out, clean, poly) == [], text[:40]


def test_typeset_flags_needs_review_instead_of_clipping(tmp_path):
    """When even the minimum font does not fit, typeset flags needs_review and
    draws nothing — it must not clip/overflow to make room."""
    import numpy as np
    from PIL import Image
    img = np.full((120, 120, 3), 255, np.uint8)  # clean white page
    tiny_box = (50, 50, 80, 70)
    blk = images.Block(tiny_box, [quad(*tiny_box)], "", 0.9)
    flags: list[bool] = []
    out = images.typeset(img, [blk], ["A very long translation that cannot possibly fit in this tiny bubble " * 3],
                         [None], flags=flags)
    assert flags == [True] and blk.needs_review is True
    # also the return_flags form agrees
    blk2 = images.Block(tiny_box, [quad(*tiny_box)], "", 0.9)
    out2, flags2 = images.typeset(img, [blk2], ["A very long translation that cannot possibly fit in this tiny bubble " * 3],
                                   [None], return_flags=True)
    assert flags2 == [True]
    # the flagged block produced no ink outside its own small box region
    assert _ink_outside_polygon(out, img, images._area_polygon((45, 45, 85, 75))) == []
    assert _ink_outside_polygon(out2, img, images._area_polygon((45, 45, 85, 75))) == []
