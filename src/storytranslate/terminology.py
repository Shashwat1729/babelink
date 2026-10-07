"""Recurring-term mining for progressive terminology (spec §79).

Simple heuristic: mine frequent 2-6 char CJK n-grams and frequent capitalized
multi-word phrases. Suggestions only — user/LLM confirms.
"""
from __future__ import annotations

import re
from collections import Counter

_CJK_RUN = re.compile(r"[一-鿿]{2,}")
_CAP_PHRASE = re.compile(r"\b(?:[A-Z][a-z]+(?:\s+|$)){1,4}")


def mine_terms(texts: list[str], min_count: int = 3, top: int = 50) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for text in texts:
        for run in _CJK_RUN.findall(text):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    counts[run[i : i + n]] += 1
        for m in _CAP_PHRASE.findall(text):
            phrase = m.strip()
            if len(phrase) > 2:
                counts[phrase] += 1
    # drop substrings dominated by a longer term: keep terms whose count >= min_count
    candidates = [(t, c) for t, c in counts.items() if c >= min_count]
    # prefer longer terms on tie
    candidates.sort(key=lambda x: (-x[1], -len(x[0])))
    keep: list[tuple[str, int]] = []
    kept_terms: list[str] = []
    for term, c in candidates:
        if any(term != k and term in k for k in kept_terms):
            continue
        keep.append((term, c))
        kept_terms.append(term)
        if len(keep) >= top:
            break
    return keep
