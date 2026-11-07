"""Comic / manhua pages: OCR the speech text, translate it through the normal pipeline, put it back on the image.

Flow: import_images() runs OCR and stores each text block as a segment (so names, glossary, translation memory and the
Story Bible apply exactly as for prose) plus its pixel box in `regions`. render_page() erases the original text and
typesets the translation into the same bubble; export_pages() bundles every page as a CBZ.

OCR is RapidOCR (PP-OCR models on ONNX Runtime, CPU, Chinese + English); Korean pages use a Korean rec model
from BABELINK_OCR_MODELS when present. Japanese pages still need another recognizer.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .db import Database
from .models import Chapter, Segment, SegmentState

from .importers import ARCHIVE_EXT, IMAGE_EXT  # noqa: E402  (single source of truth for the extensions)

MAX_PAGES = 400
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_FONTS = ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Helvetica.ttc", "NotoSans-Regular.ttf")
_CJK_FONTS = ("msyh.ttc", "NotoSansCJK-Regular.ttc", "NotoSansSC-Regular.otf", "PingFang.ttc", "simhei.ttf", "wqy-microhei.ttc")
_FONT_DIRS = (r"C:\Windows\Fonts", "/usr/share/fonts", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation",
              "/usr/share/fonts/opentype/noto", "/System/Library/Fonts", "/Library/Fonts")


@dataclass
class Block:
    box: tuple[int, int, int, int]        # x0, y0, x1, y1 around all lines of the bubble
    polys: list[list[list[int]]]          # one quad per OCR line
    text: str
    score: float = 1.0
    needs_review: bool = False            # typeset could not fit the text inside the bubble without overflowing


def _deps():
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("image support needs: pip install 'babelink[images]'") from e
    return cv2, np, Image, ImageDraw, ImageFont


# ---- OCR ----------------------------------------------------------------------------------------------
# Recognition models beyond Chinese+English live outside the repo (C: is small
# on dev machines): BABELINK_OCR_MODELS points at a folder holding e.g.
# korean_mobile_v2.0_rec_infer.onnx + korean_dict.txt. Missing files fail soft
# back to the default engine — a page is never refused for its script.
_OCR_MODEL_DIR = Path(os.environ.get("BABELINK_OCR_MODELS", r"D:\StoryTranslation_data\ocr_models"))
_engines: dict[str, object] = {}


def _korean_config() -> str | None:
    rec = _OCR_MODEL_DIR / "korean_mobile_v2.0_rec_infer.onnx"
    keys = _OCR_MODEL_DIR / "korean_dict.txt"
    if not (rec.is_file() and keys.is_file()):
        return None
    import rapidocr_onnxruntime
    pkg = Path(rapidocr_onnxruntime.__file__).parent
    text = (pkg / "config.yaml").read_text(encoding="utf-8")
    for name in ("ch_PP-OCRv4_det_infer.onnx", "ch_PP-OCRv4_rec_infer.onnx",
                 "ch_ppocr_mobile_v2.0_cls_infer.onnx"):
        text = text.replace(f"models/{name}", (pkg / "models" / name).as_posix())
    text = text.replace((pkg / "models" / "ch_PP-OCRv4_rec_infer.onnx").as_posix(), rec.as_posix())
    text += f"\n    rec_keys_path: {keys.as_posix()}\n"
    out = _OCR_MODEL_DIR / "rapidocr_ko.yaml"
    out.write_text(text, encoding="utf-8")
    return str(out)


def _ocr_engine(lang: str = ""):
    """Per-script recognition: Korean pages get the Korean rec model (same
    detector), everything else the bundled Chinese+English engine."""
    key = "ko" if lang == "ko" else "default"
    if key not in _engines:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("image support needs: pip install 'babelink[images]'") from e
        if key == "ko":
            cfg = _korean_config()
            _engines[key] = RapidOCR(config_path=cfg) if cfg else RapidOCR()
        else:
            _engines[key] = RapidOCR()
    return _engines[key]


def ocr_lines(img, lang: str = "") -> list[tuple[list[list[int]], str, float]]:
    """(quad, text, score) per detected text line. Tall webtoon strips are cut into overlapping tiles first, because the
    detector downsizes its input and would otherwise lose small lettering. `lang='ko'` uses the Korean rec model."""
    h, w = img.shape[:2]
    tile = max(int(w * 1.4), 600)
    step = int(tile * 0.88)
    found: list[tuple[list[list[int]], str, float]] = []
    for y0 in range(0, max(h - int(tile * 0.12), 1), step) if h > tile * 1.25 else [0]:
        y1 = min(y0 + tile, h) if h > tile * 1.25 else h
        res, _ = _ocr_engine(lang)(img[y0:y1])
        for quad, text, score in res or []:
            quad = [[int(p[0]), int(p[1]) + y0] for p in quad]
            cy = sum(p[1] for p in quad) / 4
            cx = sum(p[0] for p in quad) / 4
            if any(abs(cy - sum(q[1] for q in f[0]) / 4) < 6 and abs(cx - sum(q[0] for q in f[0]) / 4) < 6 and f[1] == text for f in found):  # same line seen in the overlap
                continue
            found.append((quad, text.strip(), float(score)))
    return [f for f in found if f[1]]


def _rect(quad) -> tuple[int, int, int, int]:
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    return min(xs), min(ys), max(xs), max(ys)


def _median(values) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _layout_mode(blocks: list[Block], *, mode: str = "auto") -> str:
    """`rtl_columns` for vertical manga, `horizontal` otherwise. Pure geometry:
    a page is vertical when at least half its blocks are tall (h > 1.3 * w).
    No new deps, no OCR content involved."""
    if mode in ("rtl_columns", "horizontal"):
        return mode
    if not blocks:
        return "horizontal"
    if len(blocks) < 2:
        return "horizontal"
    tall = sum(1 for b in blocks if (b.box[3] - b.box[1]) > 1.3 * max(b.box[2] - b.box[0], 1))
    return "rtl_columns" if tall * 2 >= len(blocks) else "horizontal"


def order_blocks(blocks: list[Block], *, mode: str = "auto") -> list[Block]:
    """Layout-aware reading order from bubble geometry alone.

    - `horizontal` (default / non-vertical): cluster blocks into rows by their
      y-centres (tolerance ~0.55x median height), rows top-to-bottom,
      left-to-right within a row.
    - `rtl_columns` (vertical manga): cluster into columns by x-centres
      (tolerance ~0.55x median width), columns right-to-left, top-to-bottom
      within a column.
    `mode="auto"` picks between them via :func:`_layout_mode`.
    Returns a new ordered list; the input is not mutated.
    """
    if len(blocks) <= 1:
        return list(blocks)
    m = _layout_mode(blocks, mode=mode)
    if m == "rtl_columns":
        med_w = max(_median([b.box[2] - b.box[0] for b in blocks]), 1.0)
        tol = max(med_w * 0.55, 8.0)
        by_cx = sorted(blocks, key=lambda b: -((b.box[0] + b.box[2]) / 2))
        columns: list[list[Block]] = []
        column_cx: list[float] = []
        for b in by_cx:
            cx = (b.box[0] + b.box[2]) / 2
            for k, ccx in enumerate(column_cx):
                if abs(cx - ccx) <= tol:
                    columns[k].append(b)
                    column_cx[k] = (ccx * len(columns[k]) + cx) / (len(columns[k]) + 1)
                    break
            else:
                columns.append([b])
                column_cx.append(cx)
        ordered_cols = sorted(zip(column_cx, columns), key=lambda t: -t[0])
        out: list[Block] = []
        for _, col in ordered_cols:
            col.sort(key=lambda b: (b.box[1], b.box[0]))
            out.extend(col)
        return out
    med_h = max(_median([b.box[3] - b.box[1] for b in blocks]), 1.0)
    tol = max(med_h * 0.55, 8.0)
    by_cy = sorted(blocks, key=lambda b: (b.box[1] + b.box[3]) / 2)
    rows: list[list[Block]] = []
    row_cy: list[float] = []
    for b in by_cy:
        cy = (b.box[1] + b.box[3]) / 2
        for k, ccy in enumerate(row_cy):
            if abs(cy - ccy) <= tol:
                rows[k].append(b)
                row_cy[k] = (ccy * len(rows[k]) + cy) / (len(rows[k]) + 1)
                break
        else:
            rows.append([b])
            row_cy.append(cy)
    ordered_rows = sorted(zip(row_cy, rows), key=lambda t: t[0])
    out = []
    for _, row in ordered_rows:
        row.sort(key=lambda b: (b.box[0], b.box[1]))
        out.extend(row)
    return out


def group_blocks(lines, *, joiner: str | None = None, order: str = "auto") -> list[Block]:
    """Merge OCR lines that belong to one bubble: stacked lines of similar height that overlap horizontally, or
    neighbours on one row. Reading order is layout-aware via :func:`order_blocks` (bubble geometry only):
    columns right-to-left for vertical manga, top-to-bottom rows otherwise (`order="auto"`).
    Within a vertical bubble the lines are columns too, read right-to-left, top-to-bottom.
    """
    n = len(lines)
    rects = [_rect(q) for q, _, _ in lines]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            a, b = rects[i], rects[j]
            ah, bh, aw, bw = a[3] - a[1], b[3] - b[1], a[2] - a[0], b[2] - b[0]
            # vertical text (a column taller than wide) uses its width as the line size
            asz, bsz = (aw if ah > 1.6 * aw else ah), (bw if bh > 1.6 * bw else bh)
            if not 0.55 <= asz / max(bsz, 1) <= 1.8:
                continue
            gap_y = max(a[1], b[1]) - min(a[3], b[3])
            gap_x = max(a[0], b[0]) - min(a[2], b[2])
            overlap_x = min(a[2], b[2]) - max(a[0], b[0])
            overlap_y = min(a[3], b[3]) - max(a[1], b[1])
            stacked = gap_y <= 0.9 * min(asz, bsz) and overlap_x >= 0.3 * min(aw, bw)
            side_by_side = gap_x <= 1.0 * min(asz, bsz) and overlap_y >= 0.5 * min(ah, bh)
            if stacked or side_by_side:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    blocks = []
    for idx in groups.values():
        # vertical bubble: most lines tall -> columns right-to-left, top-to-bottom
        tall = sum(1 for i in idx if (rects[i][3] - rects[i][1]) > 1.6 * max(rects[i][2] - rects[i][0], 1))
        if len(idx) > 1 and tall * 2 >= len(idx):
            idx.sort(key=lambda i: (-(rects[i][0] + rects[i][2]) / 2, rects[i][1]))
        else:
            idx.sort(key=lambda i: (rects[i][1], rects[i][0]))
        texts = [lines[i][1] for i in idx]
        sep = joiner if joiner is not None else ("" if sum(bool(_CJK.search(t)) for t in texts) > len(texts) / 2 else " ")
        xs = [rects[i] for i in idx]
        blocks.append(Block((min(r[0] for r in xs), min(r[1] for r in xs), max(r[2] for r in xs), max(r[3] for r in xs)),
                            [lines[i][0] for i in idx], sep.join(texts), min(lines[i][2] for i in idx)))
    return order_blocks(blocks, mode=order)


def read_image(data: bytes | str | Path):
    cv2, np, *_ = _deps()
    raw = data if isinstance(data, bytes) else Path(data).read_bytes()
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("not a readable image")
    return img


def _reocr_block_upscaled(img, block: Block, lang: str = "") -> Block | None:
    """Re-OCR one block cropped from `img`, upscaled 2x, coordinates mapped back
    to full-page coords. Returns the grouped block or None if nothing is found.
    Uses the module-level :func:`ocr_lines` so tests can fake it."""
    cv2, np, *_ = _deps()
    x0, y0, x1, y1 = block.box
    pad = 6
    h, w = img.shape[:2]
    cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
    cx1, cy1 = min(w, x1 + pad), min(h, y1 + pad)
    if cx1 - cx0 < 8 or cy1 - cy0 < 8:
        return None
    crop = img[cy0:cy1, cx0:cx1]
    up = cv2.resize(crop, (crop.shape[1] * 2, crop.shape[0] * 2), interpolation=cv2.INTER_CUBIC)
    lines = ocr_lines(up, lang)
    if not lines:
        return None
    remapped = [([[int(p[0] // 2 + cx0), int(p[1] // 2 + cy0)] for p in quad], text, score)
                for quad, text, score in lines]
    grouped = group_blocks(remapped)
    if not grouped:
        return None
    return max(grouped, key=lambda b: (b.score, len(b.text)))


def reocr_low_confidence_blocks(img, blocks: list[Block], *, lang: str = "", threshold: float = 0.75) -> list[Block]:
    """Second pass: re-OCR every block whose score (min line confidence) is below
    `threshold` on a 2x-upscaled crop. Statistical, dictionary-free replacement:
    the upscaled result replaces the original only when its score is strictly
    higher. Order is re-applied afterwards so geometry stays consistent.
    No word lists, no spelling heuristics — spacing slips like 'MYLEG' vs
    'MY LEG' are only fixed this way when the upscaled recognizer actually
    scores higher on 'MY LEG'."""
    out = []
    for b in blocks:
        if b.score >= threshold:
            out.append(b)
            continue
        try:
            alt = _reocr_block_upscaled(img, b, lang)
        except Exception:
            alt = None
        out.append(alt if (alt is not None and alt.score > b.score) else b)
    return order_blocks(out)


def detect_blocks(img, min_score: float = 0.5, lang: str = "", *, two_pass: bool = True,
                  reocr_threshold: float = 0.75) -> list[Block]:
    """First pass whole-page OCR, group into bubbles, then (when `two_pass`)
    re-OCR low-confidence blocks at 2x upscale and keep the higher-scoring
    reading. Calls the module-level :func:`ocr_lines` so tests can fake it."""
    blocks = group_blocks([ln for ln in ocr_lines(img, lang) if ln[2] >= min_score])
    if not two_pass or not blocks:
        return blocks
    try:
        return reocr_low_confidence_blocks(img, blocks, lang=lang, threshold=reocr_threshold)
    except Exception:
        return blocks


# ---- import ------------------------------------------------------------------------------------------
def _page_files(path: Path, store: Path) -> list[Path]:
    """Copy the page image(s) into the project's own folder so the work does not depend on where the file came from."""
    tag = uuid.uuid4().hex[:6]
    if path.suffix.lower() in IMAGE_EXT:
        dest = store / f"{tag}_{path.name}"
        shutil.copyfile(path, dest)
        return [dest]
    pages = []
    with zipfile.ZipFile(path) as z:
        names = sorted((n for n in z.namelist() if Path(n).suffix.lower() in IMAGE_EXT and not Path(n).name.startswith(".")),
                       key=lambda n: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", n)])
        if not names:
            raise ValueError("the archive has no images")
        for i, n in enumerate(names[:MAX_PAGES]):
            dest = store / f"{tag}_{i + 1:04d}{Path(n).suffix.lower()}"  # flat, numbered: never trust names inside the archive
            with z.open(n) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            pages.append(dest)
    return pages


def import_images(db: Database, project_id: int, path: str | Path, *, detect=None, on_progress=None,
                  should_stop=lambda: False, title: str | None = None) -> int:
    """One chapter per page; each OCR block becomes a segment with its box. `title` overrides the file name stem
    (the web layer stores uploads under a prefixed name). The project's source language picks the OCR script
    (Korean pages use the Korean rec model when its files are present). Returns pages added."""
    path = Path(path)
    name = title or path.stem
    _deps()
    lang = ""
    try:
        lang = db.get_project(project_id).get("source_lang") or ""
    except Exception:
        lang = ""
    if detect is None:
        detect = (lambda img: detect_blocks(img, lang="ko")) if lang == "ko" else detect_blocks
    base = db.conn.execute("SELECT COALESCE(MAX(order_index), -1) + 1 AS b FROM chapters WHERE project_id=?", (project_id,)).fetchone()["b"]
    store = db.path.parent / "pages" / str(project_id)
    store.mkdir(parents=True, exist_ok=True)
    pages = _page_files(path, store)
    added = 0
    for page in pages:
        if should_stop():
            break
        if on_progress:
            on_progress(added, len(pages), f"Reading {page.name.split('_', 1)[-1]}")
        img = read_image(page)
        h, w = img.shape[:2]
        doc = db.add_document(project_id, str(page), "image", page.stem)
        ch_title = name if path.suffix.lower() in IMAGE_EXT else f"{name} {page.stem.split('_', 1)[-1]}"
        ch = db.add_chapter(Chapter(None, project_id, doc, base + added, ch_title, "", None))
        for j, b in enumerate(detect(img)):
            sid = db.add_segment(Segment(None, project_id, doc, ch, None, j, b.text, None, SegmentState.UNPROCESSED))
            db.conn.execute("INSERT INTO regions(segment_id, x0, y0, x1, y1, polys, page_w, page_h) VALUES (?,?,?,?,?,?,?,?)",
                            (sid, *b.box, json.dumps(b.polys), w, h))
        db.conn.commit()
        added += 1
    if on_progress:
        on_progress(added, len(pages), "")
    return added


def is_page(db: Database, chapter_id: int) -> bool:
    r = db.conn.execute("SELECT d.format FROM chapters c JOIN documents d ON d.id=c.document_id WHERE c.id=?", (chapter_id,)).fetchone()
    return bool(r and r["format"] == "image")


def page_path(db: Database, chapter_id: int) -> Path:
    r = db.conn.execute("SELECT d.path, d.format FROM chapters c JOIN documents d ON d.id=c.document_id WHERE c.id=?", (chapter_id,)).fetchone()
    if not r or r["format"] != "image" or not Path(r["path"]).is_file():
        raise FileNotFoundError("this chapter is not an image page, or its file is gone")
    return Path(r["path"])


# ---- render ------------------------------------------------------------------------------------------
def _font(size: int, cjk: bool):
    *_, ImageFont = _deps()
    if p := os.environ.get("BABELINK_FONT"):
        return ImageFont.truetype(p, size)
    for d in _FONT_DIRS:
        for name in (_CJK_FONTS if cjk else _FONTS):
            f = Path(d) / name
            if f.is_file():
                try:
                    return ImageFont.truetype(str(f), size)
                except OSError:
                    continue
    return ImageFont.load_default(size)


_MIN_FONTSIZE = 6


def _wrap(draw, text: str, font, width: int) -> list[str]:
    """Greedy wrap with a hard width clamp: a single token wider than `width`
    is hard-split character by character so a line never exceeds `width`
    (unless one glyph alone is wider than the box)."""
    width = max(int(width), 1)
    cjk = bool(_CJK.search(text))
    tokens = list(text.replace("\n", " ")) if cjk else text.replace("\n", " ").split(" ")
    lines, cur = [], ""

    def emit(line):
        lines.append(line)

    for t in tokens:
        if t == "":
            continue
        if draw.textlength(t, font=font) > width:
            # overlong token (no spaces, or a URL): hard-break by characters
            if cur:
                emit(cur)
                cur = ""
            piece = ""
            for ch in t:
                trial = piece + ch
                if draw.textlength(trial, font=font) <= width or not piece:
                    piece = trial
                else:
                    emit(piece)
                    piece = ch
            cur = piece
            continue
        trial = (cur + ("" if cjk else " ") + t) if cur else t
        if draw.textlength(trial, font=font) <= width:
            cur = trial
        else:
            emit(cur)
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _fit(draw, text: str, box_w: int, box_h: int, cjk: bool, stroke: int = 0):
    """Largest font size at which the wrapped text fits the box, hard-clamped:
    no width slack (the old 1.02 tolerance is gone), stroke space reserved,
    shrink down to ``_MIN_FONTSIZE``. The caller must still check overflow —
    when even the minimum size does not fit, the returned lines overflow and
    typeset flags the block instead of drawing it."""
    usable_w = max(int(box_w) - 2 * int(stroke), 1)
    line_pad = int(stroke) * 2
    for size in range(min(box_h, 56), _MIN_FONTSIZE - 1, -1):
        font = _font(size, cjk)
        lines = _wrap(draw, text, font, usable_w)
        line_h = size * 1.12 + line_pad
        if lines and len(lines) * line_h <= box_h and all(draw.textlength(ln, font=font) + 2 * stroke <= box_w for ln in lines):
            return font, lines, size
    font = _font(_MIN_FONTSIZE, cjk)
    return font, _wrap(draw, text, font, usable_w), _MIN_FONTSIZE


def _fits_in_area(draw, lines, font, size: int, box_w: int, box_h: int, stroke: int = 0) -> bool:
    """True iff every line fits the (inscribed) area with stroke reserved."""
    line_h = size * 1.12 + int(stroke) * 2
    if not lines:
        return True
    if len(lines) * line_h > box_h:
        return False
    return all(draw.textlength(ln, font=font) + 2 * int(stroke) <= box_w for ln in lines)


def _bubble_area(img, box):
    """Interior of the speech bubble around `box`: the connected region of the fill colour, capped to a sane size."""
    cv2, np, *_ = _deps()
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    px, py = int(bw * 1.2) + 12, int(bh * 1.2) + 12
    ax0, ay0, ax1, ay1 = max(0, x0 - px), max(0, y0 - py), min(w, x1 + px), min(h, y1 + py)
    crop = img[ay0:ay1, ax0:ax1].copy()
    mask = np.zeros((crop.shape[0] + 2, crop.shape[1] + 2), np.uint8)
    seed = ((x0 + x1) // 2 - ax0, (y0 + y1) // 2 - ay0)
    tol = (14, 14, 14)
    cv2.floodFill(crop, mask, seed, (0, 0, 0), tol, tol, 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8))
    ys, xs = np.nonzero(mask[1:-1, 1:-1])
    if len(xs) == 0:
        return x0, y0, x1, y1
    gx0, gy0, gx1, gy1 = ax0 + xs.min(), ay0 + ys.min(), ax0 + xs.max() + 1, ay0 + ys.max() + 1
    # a flood that escaped into the page background fills the whole crop: fall back to a modest growth of the text box
    if (gx1 - gx0) >= (ax1 - ax0) * 0.98 and (gy1 - gy0) >= (ay1 - ay0) * 0.98:
        return max(0, x0 - bw // 6), max(0, y0 - bh // 6), min(w, x1 + bw // 6), min(h, y1 + bh // 6)
    # round bubbles: the writable rectangle is the inscribed one (0.71 of the bounding box); square ones keep it all
    fill = len(xs) / max((gx1 - gx0) * (gy1 - gy0), 1)
    k = 1 - 0.29 * min(max((1 - fill) / 0.22, 0.0), 1.0)
    cx, cy, hw, hh = (gx0 + gx1) / 2, (gy0 + gy1) / 2, (gx1 - gx0) * k / 2, (gy1 - gy0) * k / 2
    return int(cx - hw), int(cy - hh), int(cx + hw), int(cy + hh)


def erase_text(img, blocks: list[Block]):
    """Remove the original lettering. Flat backgrounds (speech bubbles) are filled with their own colour; anything
    textured is inpainted. Returns (clean image, fill colour per block or None)."""
    cv2, np, *_ = _deps()
    out = img.copy()
    h, w = img.shape[:2]
    fills = []
    for b in blocks:
        mask = np.zeros((h, w), np.uint8)
        for q in b.polys:
            cv2.fillPoly(mask, [np.array(q, np.int32)], 255)
        grow = max(3, (b.box[3] - b.box[1]) // 14)
        mask = cv2.dilate(mask, np.ones((grow, grow), np.uint8))
        x0, y0, x1, y1 = b.box
        pad = 8 + grow
        rx0, ry0, rx1, ry1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)
        ring = out[ry0:ry1, rx0:rx1][mask[ry0:ry1, rx0:rx1] == 0]
        if len(ring) > 20 and ring.std(axis=0).max() < 22:
            color = np.median(ring, axis=0).astype(np.uint8)
            out[mask > 0] = color
            fills.append(tuple(int(c) for c in color))
        else:
            out = cv2.inpaint(out, mask, 3, cv2.INPAINT_TELEA)
            fills.append(None)
    return out, fills


def _area_polygon(area) -> list[list[int]]:
    """Rectangle polygon for an inscribed area (test helper + documentation of
    the guarantee: drawn lines are clamped to this rectangle, which is inside
    the bubble mask for elliptical bubbles)."""
    x0, y0, x1, y1 = area
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def typeset(img, blocks: list[Block], texts: list[str], fills, flags: list[bool] | None = None, *, return_flags: bool = False):
    """Erase-free re-typesetting into the inscribed bubble area.

    Hard clamp: the fitted font is hard-clamped to the inscribed area
    (:func:`_fit` + :func:`_fits_in_area`, stroke reserved). When even the
    minimum size does not fit, the block is flagged `needs_review` and its
    text is not drawn at all, instead of overflowing/clipping the mask.

    Flag access (pipeline.py/qa.py/web.py are untouched): the optional `flags`
    list is cleared and filled per block (True = needs_review), every block
    also gets `block.needs_review` set, and `return_flags=True` returns
    ``(image, flags)``. Default callers keep getting just the image.
    """
    cv2, np, Image, ImageDraw, ImageFont = _deps()
    out_flags: list[bool] = []
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    for b, text, fill in zip(blocks, texts, fills):
        text = (text or "").strip()
        if not text:
            out_flags.append(False)
            continue
        ax0, ay0, ax1, ay1 = _bubble_area(img, b.box) if fill else b.box
        mx, my = int((ax1 - ax0) * 0.04), int((ay1 - ay0) * 0.04)
        area = (ax0 + mx, ay0 + my, ax1 - mx, ay1 - my)
        area_w = max(area[2] - area[0], 20)
        area_h = max(area[3] - area[1], 14)
        stroke = 0 if fill else 2
        cjk = bool(_CJK.search(text))
        font, lines, size = _fit(draw, text, area_w, area_h, cjk, stroke=stroke)
        if not _fits_in_area(draw, lines, font, size, area_w, area_h, stroke=stroke):
            # overflow even at the minimum size: flag instead of clipping
            b.needs_review = True
            out_flags.append(True)
            continue
        b.needs_review = False
        out_flags.append(False)
        bg = fill or tuple(int(c) for c in img[(b.box[1] + b.box[3]) // 2, (b.box[0] + b.box[2]) // 2])
        lum = 0.114 * bg[0] + 0.587 * bg[1] + 0.299 * bg[2]
        ink, halo = ((20, 20, 20), (255, 255, 255)) if lum > 128 else ((245, 245, 245), (0, 0, 0))
        line_h = size * 1.12 + stroke * 2
        # clamp the vertical span inside the area (centred span already fits by
        # _fits_in_area; clamp guards rounding / glyph-metric drift)
        span = len(lines) * line_h
        if span > area_h:
            span = float(area_h)
            line_h = span / max(len(lines), 1)
        top = (area[1] + area[3]) / 2 - span / 2
        cx = (area[0] + area[2]) / 2
        for i, ln in enumerate(lines):
            y = top + (i + 0.5) * line_h
            y = max(area[1] + line_h / 2, min(y, area[3] - line_h / 2))
            draw.text((cx, y), ln, font=font, fill=ink, anchor="mm", stroke_width=stroke, stroke_fill=halo)
    if flags is not None:
        flags.clear()
        flags.extend(out_flags)
    out = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    return (out, out_flags) if return_flags else out


def render_page(db: Database, chapter_id: int, out_path: str | Path | None = None, *, translated: bool = True) -> bytes:
    """PNG bytes of the page with the translated text in place (or the untouched original)."""
    cv2, *_ = _deps()
    src = page_path(db, chapter_id)
    img = read_image(src)
    if not translated:
        ok, buf = cv2.imencode(".png", img)
        return buf.tobytes()
    rows = db.conn.execute("SELECT s.translated_text t, r.* FROM segments s JOIN regions r ON r.segment_id=s.id "
                           "WHERE s.chapter_id=? ORDER BY s.order_index", (chapter_id,)).fetchall()
    blocks = [Block((r["x0"], r["y0"], r["x1"], r["y1"]), json.loads(r["polys"]), "") for r in rows]
    texts = [r["t"] or "" for r in rows]
    keep = [i for i, t in enumerate(texts) if t.strip()]  # untranslated blocks stay as drawn
    clean, fills = erase_text(img, [blocks[i] for i in keep])
    out = typeset(clean, [blocks[i] for i in keep], [texts[i] for i in keep], fills)
    ok, buf = cv2.imencode(".png", out)
    if out_path:
        Path(out_path).write_bytes(buf.tobytes())
    return buf.tobytes()


def export_pages(db: Database, project_id: int, out: str | Path) -> Path:
    """CBZ of every image page in reading order (untranslated pages are included as they are)."""
    out = Path(out)
    pages = [c for c in db.chapters(project_id) if is_page(db, c["id"])]
    if not pages:
        raise ValueError("this work has no image pages")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for i, c in enumerate(pages, 1):
            z.writestr(f"{i:04d}.png", render_page(db, c["id"]))
    return out
