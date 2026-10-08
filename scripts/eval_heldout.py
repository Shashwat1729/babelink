"""Held-out check: one Chinese chapter + a published English translation of it, both as plain text files
(paragraphs separated by blank lines). Wraps each in a one-chapter EPUB and runs the A-F experiment matrix on the
first N paragraphs. The texts stay in git-ignored corpus/; only the scores are written to the report.

    python scripts/eval_heldout.py corpus/web/qd1_zh.txt corpus/web/nf1_en.txt --title "第001章 造化天书" \
        --en-title "Chapter 1" --model huihui_ai/qwen3-abliterated:4b --paragraphs 10 --configs ABDF
"""
import argparse
import json
import re
import sys
from pathlib import Path

from ebooklib import epub

from lorebridge.eval_real import chapter_number, run_corpus_eval, write_corpus_report
from lorebridge.providers.ollama import OllamaProvider


def one_chapter_epub(txt: Path, title: str, lang: str, out: Path) -> None:
    paras = [p.strip() for p in re.split(r"\n\s*\n", txt.read_text(encoding="utf-8")) if p.strip()]
    b = epub.EpubBook()
    b.set_identifier(out.stem)
    b.set_title(title)
    b.set_language(lang)
    c = epub.EpubHtml(title=title, file_name="c1.xhtml", lang=lang)
    c.content = f"<html><body><h1>{title}</h1>" + "".join(f"<p>{p}</p>" for p in paras) + "</body></html>"
    b.add_item(c)
    b.toc = (c,)
    b.spine = ["nav", c]
    b.add_item(epub.EpubNcx())
    b.add_item(epub.EpubNav())
    epub.write_epub(str(out), b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zh_txt")
    ap.add_argument("en_txt")
    ap.add_argument("--title", default="第001章")
    ap.add_argument("--en-title", default="Chapter 1")
    ap.add_argument("--model", default="huihui_ai/qwen3-abliterated:4b")
    ap.add_argument("--paragraphs", type=int, default=10)
    ap.add_argument("--configs", default="ABDF")
    ap.add_argument("--work", default="eval_runs/heldout")
    ap.add_argument("--out", default="eval_runs/heldout.md")
    ap.add_argument("--json", default="eval_runs/heldout.json")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    zh, en = work / "zh.epub", work / "en.epub"
    one_chapter_epub(Path(a.zh_txt), a.title, "zh", zh)
    one_chapter_epub(Path(a.en_txt), a.en_title, "en", en)
    res = run_corpus_eval(zh, en, OllamaProvider(model=a.model), chapters=[chapter_number(a.title) or 1], paragraphs=a.paragraphs, configs=a.configs,
                          workdir=work / "run")
    Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    write_corpus_report(res, a.out, title="Held-out chapter evaluation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
