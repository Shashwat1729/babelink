"""Text segmentation: chapters and paragraphs from plain text/html.

Real-world sources are messy (hard-wrapped Gutenberg dumps, one giant <p>, nested <div><p>,
boilerplate). Everything funnels through `parse_stream` so TXT, paste, HTML and EPUB agree.
"""
from __future__ import annotations

import html as _html
import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)  # EPUB XHTML parses fine with lxml's HTML mode


@dataclass
class ParsedChapter:
    title: str
    html: str
    paragraphs: list[str]


_NUM = r"(?:[零〇一二三四五六七八九十百千万0-9]+|[ivxlcdmIVXLCDM]+|\d+)"
CHAPTER_HEADING = re.compile(
    rf"^\s*(?:chapter\s+{_NUM}\b\.?|第\s*{_NUM}\s*[章节節回卷](?=[\s　:：.]|$))",
    re.IGNORECASE,
)
_SEPARATOR = re.compile(r"^[\s—\-=─_*~·.]{4,}$")
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_SENTENCE_END = re.compile(r"[。．.！？!?”\"」』]$")
_SENT_SPLIT = re.compile(r"(?<=[。．！？!?；;])")


def split_long(text: str, max_chars: int = 300) -> list[str]:
    """MT-call-sized pieces of one segment (E13): split on sentence boundaries
    only when over max_chars; short segments pass through untouched (single
    piece) so behavior is identical unless splitting triggers. Pieces join
    with '' (CJK) — the translate path rejoins and stores one segment, so DB
    granularity never changes."""
    if len(text) <= max_chars:
        return [text]
    sents = [s for s in _SENT_SPLIT.split(text) if s]
    if len(sents) <= 1:
        return [text]  # one giant sentence: splitting would break it mid-clause
    pieces, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) > max_chars:
            pieces.append(cur)
            cur = s
        else:
            cur += s
    if cur:
        pieces.append(cur)
    return pieces or [text]
_HEADING_TAGS = ["h1", "h2", "h3", "h4"]
_BLOCK_TAGS = ["p", "div", "blockquote", "li"] + _HEADING_TAGS


def _clean(s: str) -> str:
    return s.replace("\xa0", " ").replace("　", " ").strip()


def _join(lines: list[str]) -> str:
    """Join hard-wrapped lines: no space around CJK, one space between Latin words."""
    out = ""
    for ln in lines:
        if out and not (_CJK.search(out[-1]) or _CJK.search(ln[:1])):
            out += " "
        out += ln
    return out


def block_tags(soup) -> list:
    """Leaf-most block elements that carry text (so <div><p>x</p></div> yields x once).
    Shared by importer and exporter so paragraph counts line up."""
    out = []
    for tag in soup.find_all(_BLOCK_TAGS):
        if tag.find(_BLOCK_TAGS):
            continue
        if tag.get_text(strip=True):
            out.append(tag)
    return out


def tag_text(tag) -> str:
    for br in tag.find_all("br"):
        br.replace_with("\n")
    return tag.get_text("")


def parse_stream(text: str, per_line: bool | None = None) -> list[tuple[str, str]]:
    """Plain text -> [('heading'|'para', text)]. Handles hard-wrapped lines, indent-marked
    paragraphs, separator rules, and 'Chapter N' / '第N章/回' headings (+ their subtitle line)."""
    items: list[tuple[str, str]] = []
    cur: list[str] = []
    blank_seen = bool(re.search(r"\n[ \t　\xa0]*\n", text))

    def flush():
        if cur:
            items.append(("para", _join(cur)))
            cur.clear()

    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        line = _clean(raw)
        if not line:
            flush()
            continue
        if _SEPARATOR.match(line):
            flush()
            continue
        m = CHAPTER_HEADING.match(line)
        if m:
            flush()
            if len(line) <= 80:
                title = line
            else:  # long line starting with a heading: heading + argument paragraph
                title, rest = line[: m.end()].strip(), line[m.end():].strip(" .:：")
                items.append(("heading", title))
                if rest:
                    items.append(("para", rest))
                continue
            # bare heading ("第三回") + short subtitle line (e.g. a 回目 couplet), maybe after a rule
            j = i
            while j < len(lines) and (not _clean(lines[j]) or _SEPARATOR.match(_clean(lines[j]))):
                j += 1
            if j < len(lines) and not _clean(line[m.end():]):
                nxt = _clean(lines[j])
                alone = not blank_seen or j + 1 >= len(lines) or not _clean(lines[j + 1])  # else it is line 1 of a wrapped paragraph
                if alone and 0 < len(nxt) <= 40 and not _SENTENCE_END.search(nxt) and not CHAPTER_HEADING.match(nxt):
                    title = f"{title} {nxt}"
                    i = j + 1
            items.append(("heading", title))
            continue
        indented = raw[:1] in ("　", "\xa0") or raw.startswith("  ")
        if not blank_seen:  # one paragraph per line
            flush()
            cur.append(line)
            flush()
            continue
        if indented:
            flush()
        cur.append(line)
    flush()
    return items


def strip_boilerplate(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop Project Gutenberg header/footer if present."""
    start = next((k for k, (_, t) in enumerate(items) if re.search(r"\*\*\*\s*START OF", t)), None)
    if start is not None:
        items = items[start + 1:]
    end = next((k for k, (_, t) in enumerate(items) if re.search(r"\*\*\*\s*END OF", t)), None)
    if end is not None:
        items = items[:end]
    return items


def chapters_from_stream(items: list[tuple[str, str]], default_title: str = "Chapter 1") -> list[ParsedChapter]:
    """Headings open chapters; consecutive headings merge into one title; empty chapters are dropped."""
    chapters: list[ParsedChapter] = []
    title, paras = default_title, []

    def close():
        if paras:
            chapters.append(ParsedChapter(title, to_html(paras), list(paras)))

    for kind, text in items:
        if kind == "heading":
            if paras:
                close()
                title, paras = text, []
            else:
                title = text if title == default_title else f"{title} {text}"
        else:
            paras.append(text)
    close()
    if not chapters:
        chapters.append(ParsedChapter(default_title, "", []))
    return chapters


def to_html(paras: list[str]) -> str:
    return "".join(f"<p>{_html.escape(p)}</p>" for p in paras)


def chapters_from_text(text: str, default_title: str = "Chapter 1") -> list[ParsedChapter]:
    """Split plain text on chapter headings; fallback to one chapter named `default_title`."""
    return chapters_from_stream(strip_boilerplate(parse_stream(text)), default_title)


def segment_tags(soup) -> list:
    """Block tags that become segments (headings are chapter titles, not segments)."""
    return [t for t in block_tags(soup) if t.name not in _HEADING_TAGS]


def stream_from_html(html: str) -> list[tuple[str, str]]:
    """XHTML -> [('heading'|'para', text)], in document order."""
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[str, str]] = []
    buf: list[str] = []

    def flush():
        if buf:
            out.extend(parse_stream("\n\n".join(buf), per_line=False))
            buf.clear()

    for tag in block_tags(soup):
        if tag.name in _HEADING_TAGS:
            flush()
            text = " ".join(_clean(tag_text(tag)).split())
            if text:
                out.append(("heading", text))
        else:
            buf.append(tag_text(tag))
    flush()
    return out


def paragraphs_from_html(html: str) -> list[str]:
    paras = [t for k, t in stream_from_html(html) if k == "para"]
    if not paras:
        text = " ".join(BeautifulSoup(html, "lxml").get_text(" ", strip=True).split())
        if text:
            paras = [text]
    return paras
