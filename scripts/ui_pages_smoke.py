"""Drive the comic-page flow in a real browser (Playwright): add a page image to a work (OCR), see the page card,
fix an OCR slip, translate with the mock provider, paste a second image from the clipboard, download the CBZ,
check the phone layout. Needs the `images` extra and a CJK font (scripts/make_sample_page.py draws the page).

    BABELINK_HOME=eval_runs/ui_ws python -m uvicorn babelink.web:app --port 8765 &
    python scripts/ui_pages_smoke.py --url http://127.0.0.1:8765 --out eval_runs/ui_pages
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--out", default="eval_runs/ui_pages")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sample = Path(tempfile.mkdtemp(prefix="st_pg_")) / "page.png"
    subprocess.run([sys.executable, str(Path(__file__).with_name("make_sample_page.py")), str(sample)], check=True)
    errors: list[str] = []
    shot = lambda page, name, **kw: page.screenshot(path=str(out / name), **kw)  # noqa: E731

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        ctx = b.new_context(viewport={"width": 1440, "height": 940}, accept_downloads=True)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
        page.add_init_script("if(!localStorage.getItem('st-cfg'))localStorage.setItem('st-cfg', JSON.stringify({provider:'mock',model:'mock',baseUrl:'',summarize:true,chosen:true}))")
        page.goto(a.url)

        page.wait_for_selector(".welcome, .grid")
        page.click("text=Create your first work" if page.locator(".welcome").count() else "text=New work")
        page.fill("#form-work [name=name]", "Manhua demo")
        page.select_option("#lang-source", "zh")
        page.click("#work-submit")
        page.wait_for_selector(".drop.big")
        page.set_input_files(".drop.big input", str(sample))
        page.wait_for_selector(".ch-row", timeout=120000)
        page.wait_for_function("document.querySelector('#job-slot')?.textContent.startsWith('Done')", timeout=120000)
        assert page.locator(".ch-row").count() == 1
        shot(page, "01_work_with_page.png")

        page.click(".ch-row .ch-main")
        page.wait_for_selector(".page-card img")
        page.wait_for_function("[...document.querySelectorAll('.page-img')].every(i => i.complete && i.naturalWidth > 0)")
        assert page.locator(".page-img").count() == 2, "side by side shows original and translated"
        assert page.locator(".seg").count() == 3, "three bubbles read"
        shot(page, "02_page_before.png", full_page=True)

        # fix an OCR slip, then translate with the mock provider (it echoes the source)
        page.locator(".seg button:has-text('Fix text')").nth(2).click()
        page.fill(".seg textarea", "快撤吧！")
        page.click("button:has-text('Save text')")
        page.wait_for_selector(".seg:nth-of-type(3) .src:has-text('快撤吧')")
        page.click("button:has-text('Translate 3 paragraphs'), button:has-text('Translate 3 blocks')")
        page.wait_for_function("document.querySelector('#job-slot')?.textContent.startsWith('Done')", timeout=60000)
        page.wait_for_function("[...document.querySelectorAll('.page-img')].every(i => i.complete && i.naturalWidth > 0)")
        page.wait_for_timeout(600)  # a re-render swaps the <img> elements; let the new ones load before the shot
        page.wait_for_function("[...document.querySelectorAll('.page-img')].every(i => i.complete && i.naturalWidth > 0)")
        assert page.locator("h1.serif").inner_text() == "page", "title is the file name, not the stored name"
        shot(page, "03_page_translated.png", full_page=True)
        for mode in ("Translated", "Original"):
            page.click(f".seg-ctl button:has-text('{mode}')")
            assert page.locator(".page-img").count() == 1
            shot(page, f"04_page_{mode.lower()}.png", full_page=True)
        page.click(".seg-ctl button:has-text('Side by side')")

        with page.expect_download() as dl:
            page.click("a:has-text('Save PNG')")
        assert dl.value.suggested_filename.endswith(".png")

        # paste an image from the clipboard onto the work
        page.click("a:has-text('All chapters')")
        page.wait_for_selector(".ch-row")
        data = list(sample.read_bytes())
        page.evaluate(
            """(arr) => { const dt = new DataTransfer(); dt.items.add(new File([new Uint8Array(arr)], 'image.png', {type: 'image/png'}));
                const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}); document.body.dispatchEvent(ev); }""", data)
        page.wait_for_function("document.querySelectorAll('.ch-row').length === 2", timeout=120000)
        shot(page, "05_two_pages.png")

        with page.expect_download() as dl:
            page.click("button:has-text('Download translated pages (CBZ)')")
            if page.locator("#dlg-confirm[open]").count():
                page.click("#confirm-ok")
        print("cbz:", dl.value.suggested_filename, Path(dl.value.path()).stat().st_size, "bytes")
        assert dl.value.suggested_filename.endswith(".cbz")

        page.set_viewport_size({"width": 390, "height": 844})
        page.click(".ch-row:nth-child(1) .ch-main")
        page.wait_for_selector(".page-card img")
        page.wait_for_timeout(500)
        shot(page, "06_phone_page.png", full_page=True)
        over = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        print("horizontal overflow at 390px:", over)
        assert not over
        b.close()
    print("console errors:", errors or "none")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
