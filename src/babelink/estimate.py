"""Unsupervised per-segment quality estimation (E11).

The MT engine translates both directions (Hy-MT2: zh/ja<->en). A shipped EN
segment back-translated to the source language should recover the source's
load-bearing content. Two deterministic signals, no judge model:

- roundtrip_chrf: chrF(backtranslated, source) — catastrophic drops
  (truncation, refusal vestiges, wrong-language output) collapse this.
- entity_recall: fraction of the segment's glossary terms whose SOURCE form
  reappears in the back-translation. If EN said 'the girl' for 黛玉, the
  back-translation says 女孩 and 黛玉 is missing — a dropped name, caught
  without any reference translation.

Thresholds are NOT set here: E11 validates correlation against term
adherence + QA on shipped DG16 outputs first, then wires routing
(escalate/refine) only if the signal predicts failures.
"""
from __future__ import annotations

from .eval import chrf


def roundtrip_chrf(source: str, backtranslated: str) -> float:
    """chrF(backtranslated, source), 0-100. Collapses on truncation, leftover
    source echo (100 but entity_recall disambiguates), wrong language."""
    if not backtranslated.strip():
        return 0.0
    return chrf(backtranslated, source)


def entity_recall(source: str, backtranslated: str, glossary: dict[str, str]) -> tuple[float, list[str]]:
    """Fraction of the segment's source terms present in the back-translation.
    Returns (recall, [missing forms]). Empty term set -> (1.0, []) (vacuous)."""
    forms = [f for f in glossary if f and f in source]
    if not forms:
        return 1.0, []
    missing = [f for f in forms if f not in (backtranslated or "")]
    return (len(forms) - len(missing)) / len(forms), missing
