"""Drive the live-translation flow in a real browser (Playwright) against a REAL model: text appears while it is written,
finished paragraphs show up before the job ends, Stop works mid-paragraph, a double click adds and translates once,
the batch picker sends a word limit. Needs a running Ollama with the model; takes a few minutes on CPU.

    LOREBRIDGE_HOME=eval_runs/ui_live_ws python -m uvicorn lorebridge.web:app --port 8765 &
    python scripts/ui_live_smoke.py --url http://127.0.0.1:8765 --model huihui_ai/qwen3-abliterated:4b --out eval_runs/ui_live
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

CHAPTER = """林凡走进青云宗的山门，天空下着小雨。

大师兄站在台阶上，冷冷地看着他。

“你就是新来的弟子？”大师兄问道。

林凡点了点头，没有说话。

大师兄转身离开，示意他跟上。

两人沿着石阶向山上走去。"""


GO = ".hero .btn.primary:not(.block)"  # "Download translated EPUB" is also .btn.primary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--model", default="huihui_ai/qwen3-abliterated:4b")
    ap.add_argument("--out", default="eval_runs/ui_live")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    requests: list[tuple[str, str, str]] = []
    shot = lambda page, name, **kw: page.screenshot(path=str(out / name), **kw)  # noqa: E731
    posts = lambda frag: [r for r in requests if r[0] == "POST" and frag in r[1]]  # noqa: E731

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        page = b.new_context(viewport={"width": 1440, "height": 940}).new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
        page.on("request", lambda r: requests.append((r.method, r.url, r.post_data or "")))
        page.add_init_script("localStorage.setItem('st-cfg', JSON.stringify(%s))" % json.dumps(
            {"provider": "ollama", "model": a.model, "baseUrl": "", "summarize": False, "chosen": True}))
        page.goto(a.url)

        page.wait_for_selector(".welcome, .grid")
        page.click("text=Create your first work" if page.locator(".welcome").count() else "text=New work")
        page.fill("#form-work [name=name]", "Live demo")
        page.select_option("#lang-source", "zh")
        page.click("#work-submit")
        page.wait_for_selector(".hero")

        # 1. double click on "Add and translate": one chapter, one translation job
        page.click("button:has-text('Paste chapter')")
        page.fill("#form-paste [name=title]", "第一章 山门")
        page.fill("#paste-text", CHAPTER)
        page.dblclick("#form-paste button[value=translate]")
        page.wait_for_selector(".seg", timeout=60000)
        assert len(posts("/paste")) == 1, f"paste sent {len(posts('/paste'))} times"
        assert len(posts("/jobs")) == 0  # the job comes back inside the paste response, never from a second request
        page.wait_for_selector("#job-slot .job")
        assert page.locator(".seg").count() == 6

        # 2. text appears while it is written, finished paragraphs appear before the job ends
        t0 = time.time()
        page.wait_for_selector(".seg.writing .tgt .txt", timeout=180000)
        page.wait_for_function("document.querySelector('.seg.writing .tgt .txt')?.textContent.trim().length > 3 && document.querySelector('.seg.writing .tgt .txt').textContent !== 'Writing…'", timeout=180000)
        first_text = page.locator(".seg.writing .tgt .txt").inner_text()
        assert page.locator("#job-slot .job .spin").count() == 1, "the job is still running while its text is on screen"
        print(f"first streamed text after {time.time() - t0:.0f}s: {first_text[:60]!r}")
        shot(page, "01_writing.png")
        page.wait_for_function("[...document.querySelectorAll('.seg')].filter(s => !s.classList.contains('writing') && s.querySelector('.tgt:not(.pending)')).length >= 1", timeout=300000)
        assert page.locator("#job-slot .job .spin").count() == 1, "a paragraph was finished and visible before the job ended"
        shot(page, "02_first_done_still_running.png")
        assert page.locator(".seg.writing").count() <= 1

        # 3. Stop works mid-paragraph
        page.click("#job-slot button:has-text('Stop')")
        page.wait_for_selector("#job-slot button:has-text('Stopping')", timeout=5000)
        page.wait_for_function("document.querySelector('#job-slot')?.textContent.startsWith('Stopped')", timeout=60000)
        left = page.locator(".seg .tgt.pending").count()
        print(f"stopped with {left} of 6 paragraphs left")
        assert page.locator(".seg.writing").count() == 0
        shot(page, "03_stopped.png")

        # 4. work page: batch picker sends a limit; double click starts one job; the chapter row shows where it is working
        page.click("a:has-text('All chapters')")
        page.wait_for_selector(".hero")
        if page.locator("select.batch").count():
            page.select_option("select.batch", index=1)
            label = page.locator(GO).inner_text()
            assert "Translate next ~2,000 characters" in label, label
            before = len(posts("/jobs"))
            page.dblclick(GO)
            page.wait_for_selector("#job-slot .job .spin", timeout=30000)
            time.sleep(1)
            sent = posts("/jobs")[before:]
            assert len(sent) == 1, f"translate sent {len(sent)} times"
            assert json.loads(sent[0][2])["max_words"] == 2000
            page.wait_for_selector(".ch-row.working", timeout=180000)
            page.wait_for_selector("#job-slot .live-txt, #job-slot .live-done", timeout=180000)
            shot(page, "04_work_live.png")
            page.set_viewport_size({"width": 390, "height": 800})
            time.sleep(1)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "no sideways scroll on a phone"
            shot(page, "05_phone_live.png")
            page.wait_for_function("document.querySelector('#job-slot')?.textContent.startsWith('Done')", timeout=600000)
        else:
            print("nothing left to translate: skipped the batch check")

        if errors:
            print("\n".join(errors))
            return 1
        b.close()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
