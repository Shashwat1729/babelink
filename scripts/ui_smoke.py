"""Drive the web UI in a real browser (Playwright): create a work with an EPUB, see its chapters, translate with the
mock provider, review names, edit/approve, paste a follow-up chapter, run checks, download the EPUB, switch theme and
accent, open the model picker, check the phone layout, delete the work. Screenshots go to --out.
Needs `pip install playwright` and a Chromium (set CHROMIUM=<path to chrome.exe> to reuse an installed one).

    LOREBRIDGE_HOME=eval_runs/ui_ws python -m uvicorn lorebridge.web:app --port 8765 &
    python scripts/ui_smoke.py --url http://127.0.0.1:8765 --out eval_runs/ui
"""
import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from ebooklib import epub
from playwright.sync_api import expect, sync_playwright

CHAPTERS = [
    ("第一章 开始", ["林凡走进青云宗。", "大师兄说：“欢迎来到青云宗。”", "林凡点了点头，看向远处的山峰。", "青云宗的大师兄看着林凡，露出了笑容。", "林凡修炼到了炼气期。", "大师兄带他走过长长的石阶。"]),
    ("第二章 山门", ["林凡站在山门前。", "大师兄笑了笑：“你来得正好。”", "青云宗的长老正在等待林凡。", "林凡进入了筑基期，大师兄很高兴。"]),
    ("第三章 修炼", ["林凡在青云宗修炼了三年。", "大师兄教他剑法。", "林凡终于突破了。"]),
]
PASTED = "林凡回到了青云宗。\n\n大师兄在山门前等着他。\n\n“你回来了。”大师兄说。"


def make_epub(path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("smoke")
    book.set_title("青云传")
    book.set_language("zh")
    items = []
    for i, (title, paras) in enumerate(CHAPTERS, 1):
        c = epub.EpubHtml(title=title, file_name=f"c{i}.xhtml", lang="zh")
        c.content = f"<html><body><h1>{title}</h1>" + "".join(f"<p>{t}</p>" for t in paras) + "</body></html>"
        book.add_item(c)
        items.append(c)
    book.toc = tuple(items)
    book.spine = ["nav", *items]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--out", default="eval_runs/ui")
    ap.add_argument("--width", type=int, default=1440)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="st_ui_"))
    book_path = tmp / "青云传.epub"
    make_epub(book_path)
    shot = lambda page, name, **kw: page.screenshot(path=str(out / name), **kw)  # noqa: E731

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        ctx = b.new_context(viewport={"width": a.width, "height": 940}, accept_downloads=True)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
        page.add_init_script("if(!localStorage.getItem('st-cfg'))localStorage.setItem('st-cfg', JSON.stringify({provider:'mock',model:'mock',baseUrl:'',summarize:true,chosen:true}))")
        page.goto(a.url)

        # --- library, new work with an EPUB -------------------------------------------------------------
        page.wait_for_selector(".welcome, .grid")
        if page.locator(".welcome").count():
            shot(page, "01_library_empty.png")
            page.click("text=Create your first work")
        else:
            page.click("text=New work")
        page.fill("#form-work [name=name]", "Cultivation sample")
        page.select_option("#lang-source", "zh")
        page.set_input_files("#work-drop input", str(book_path))
        expect(page.locator("#work-drop-title")).to_have_text("青云传.epub")
        shot(page, "02_new_work.png")
        page.click("#work-submit")
        page.wait_for_url(re.compile(r"#/work/\d+$"))
        page.wait_for_selector(".ch-row")
        assert page.locator(".ch-row").count() == 3, "EPUB chapters listed after import"
        assert "青云传" not in page.inner_text(".hero h1"), "work title is the typed name, not the book title"
        shot(page, "03_work_chapters.png")

        # --- translate everything with the mock provider ----------------------------------------------
        page.click("button:has-text('Translate remaining')")
        page.wait_for_function("document.querySelector('#job-slot')?.textContent.startsWith('Done')", timeout=30000)
        page.wait_for_function("!document.querySelector('.ch-row .btn')")  # no chapter has paragraphs left to translate
        assert page.locator(".chip:has-text('Draft')").count() == 3, "all chapters translated"
        shot(page, "04_work_translated.png")

        # --- names ------------------------------------------------------------------------------------
        page.click(".tabs a:has-text('Names')")
        page.wait_for_selector(".term-row")
        page.fill("input[type=search]", "林")
        shot(page, "05_names.png")
        page.fill("input[type=search]", "")

        # --- chapter view: edit, approve, context -----------------------------------------------------
        page.click(".tabs a:has-text('Chapters')")
        page.click(".ch-row:nth-child(1) .ch-main")
        page.wait_for_selector(".seg")
        assert page.locator(".seg").count() == 6, "chapter 1 has six paragraphs"
        shot(page, "06_chapter.png")
        page.locator(".seg button:has-text('Edit')").first.click()
        page.fill(".seg textarea", "Lin Fan walked into the Qingyun Sect.")
        page.click("button:has-text('Save and reuse')")
        page.wait_for_selector(".seg .chip:has-text('Approved')")
        page.locator(".seg button:has-text('Context')").nth(1).click()
        page.wait_for_selector("#context-body h2:has-text('Names applied')")
        shot(page, "07_context.png")
        page.click("#dlg-context [data-close]")

        # --- paste a follow-up chapter; project memory carried over -----------------------------------
        page.click("a:has-text('All chapters')")
        page.click("button:has-text('Paste chapter')")
        page.fill("#form-paste [name=title]", "第四章 归来")
        page.fill("#paste-text", PASTED)
        assert page.locator("#carried:has-text('Carried over from 3 chapters')").count() == 1
        shot(page, "08_paste.png")
        page.click("#form-paste button:has-text('Add and translate')")
        page.wait_for_url(re.compile(r"#/work/\d+/chapter/\d+$"))
        page.wait_for_function("document.querySelector('#job-slot')?.textContent.startsWith('Done')", timeout=30000)
        page.wait_for_function("document.querySelectorAll('.seg .chip.warn').length === 3")

        # --- quality ----------------------------------------------------------------------------------
        page.click(".crumbs a >> nth=1")
        page.click(".tabs a:has-text('Quality')")
        page.click("button:has-text('Run checks')")
        page.wait_for_function("document.querySelector('#job-slot')?.textContent.startsWith('Done')", timeout=30000)
        shot(page, "09_quality.png")

        # --- download: the mock echoes the source, so the book is not finished and the app must say so ---
        page.click(".tabs a:has-text('Chapters')")
        shot(page, "10_download_panel.png")
        with page.expect_download() as dl:
            page.click("button:has-text('Download translated EPUB')")
            if page.locator("#dlg-confirm[open]").count():
                page.click("#confirm-ok")
        size = Path(dl.value.path()).stat().st_size
        print("epub bytes:", size, "name:", dl.value.suggested_filename)
        assert size > 1000 and dl.value.suggested_filename.endswith(".epub")

        # --- appearance -------------------------------------------------------------------------------
        page.click("#look-btn")
        page.click("#theme-ctl button:has-text('Dark')")
        page.click("#accent-ctl [aria-label=Jade]")
        page.click("body", position={"x": 5, "y": 400})
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        shot(page, "11_dark_jade.png")
        page.reload()
        page.wait_for_selector(".hero")
        assert page.evaluate("document.documentElement.dataset.accent") == "jade", "look survives reload"
        page.click("#look-btn")
        page.click("#theme-ctl button:has-text('Light')")
        page.click("#accent-ctl [aria-label=Indigo]")
        shot(page, "12_light_indigo.png")
        page.click("#theme-ctl button:has-text('System')")
        page.click("#accent-ctl [aria-label=Vermilion]")
        page.keyboard.press("Escape")

        # --- model picker -----------------------------------------------------------------------------
        page.click("#model-chip")
        page.wait_for_selector("#dlg-model[open]")
        page.click("#model-body button:has-text('Ollama')")
        page.wait_for_selector("#model-body .status-line:not(:has(.spin))", timeout=20000)
        shot(page, "13_model_picker.png")
        page.click("#dlg-model [data-close]")

        # --- phone ------------------------------------------------------------------------------------
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        shot(page, "14_phone_work.png", full_page=True)
        over_work = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        page.click(".ch-row:nth-child(1) .ch-main")
        page.wait_for_selector(".seg")
        shot(page, "15_phone_chapter.png", full_page=True)
        over_ch = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        print("horizontal overflow at 390px: work", over_work, "chapter", over_ch)
        assert not over_work and not over_ch
        page.set_viewport_size({"width": a.width, "height": 940})

        # --- delete the work --------------------------------------------------------------------------
        page.click(".crumbs a >> nth=1")
        page.click(".tabs a:has-text('Settings')")
        page.click(".danger-zone button")
        page.click("#confirm-ok")
        page.wait_for_url(re.compile(r"#/$"))
        page.wait_for_selector(".welcome, .grid")

        # --- a work without a book, then drop an EPUB on its drop zone --------------------------------
        page.click("text=Create your first work")
        page.fill("#form-work [name=name]", "Drop test")
        page.click("#work-submit")
        page.wait_for_selector(".drop.big")
        page.evaluate(
            """(arr) => { const dt = new DataTransfer(); dt.items.add(new File([new Uint8Array(arr)], 'book.epub', {type: 'application/epub+zip'}));
                document.querySelector('.drop').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true})); }""",
            list(book_path.read_bytes()))
        page.wait_for_selector(".ch-row")
        assert page.locator(".ch-row").count() == 3, "dropped EPUB imported"
        page.click(".tabs a:has-text('Settings')")
        page.click(".danger-zone button")
        page.click("#confirm-ok")
        page.wait_for_url(re.compile(r"#/$"))
        b.close()
    print("errors:", json.dumps(errors, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
