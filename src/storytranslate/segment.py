"""Text segmentation: chapters and paragraphs from plain text/html."""
from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup


@dataclass
class ParsedChapter:
    title: str
    html: str
    paragraphs: list[str]


CHAPTER_HEADING = re.compile(
    r"^\s*(chapter|chap\.?|第[零一二三四五六七八九十百千万0-9]+[章节回])",
    re.IGNORECASE,
)


def paragraphs_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    paras = []
    for tag in soup.find_all(["p", "div", "blockquote", "li"]):
        text = " ".join(tag.get_text(" ", strip=True).split())
        if text:
            paras.append(text)
    if not paras:
        text = " ".join(soup.get_text(" ", strip=True).split())
        if text:
            paras = [text]
    return paras


def chapters_from_text(text: str) -> list[ParsedChapter]:
    """Split plain text on chapter headings; fallback to one chapter."""
    lines = text.splitlines()
    chapters: list[ParsedChapter] = []
    current_title = "Chapter 1"
    current: list[str] = []
    for line in lines:
        if CHAPTER_HEADING.match(line.strip()):
            if current:
                paras = [p.strip() for p in "\n".join(current).split("\n\n") if p.strip()]
                chapters.append(
                    ParsedChapter(current_title, "<p>" + "</p><p>".join(paras) + "</p>", paras)
                )
            current_title = line.strip()
            current = []
        else:
            current.append(line)
    if current:
        paras = [p.strip() for p in "\n".join(current).split("\n\n") if p.strip()]
        chapters.append(
            ParsedChapter(current_title, "<p>" + "</p><p>".join(paras) + "</p>", paras)
        )
    if not chapters:
        chapters.append(ParsedChapter("Chapter 1", "", []))
    return chapters
