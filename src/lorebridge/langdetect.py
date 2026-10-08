"""Language detection: langdetect with a CJK pre-check for stability."""
from __future__ import annotations

import re

from langdetect import detect, DetectorFactory

DetectorFactory.seed = 0

_CJK = re.compile(r"[一-鿿]")
_JP = re.compile(r"[぀-ヿ]")
_KR = re.compile(r"[가-힯]")


def detect_language(text: str) -> str:
    sample = text[:5000]
    if _JP.search(sample):
        return "ja"
    if _KR.search(sample):
        return "ko"
    if _CJK.search(sample):
        return "zh"
    try:
        return detect(sample)
    except Exception:
        return "und"
