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

Validation (fixture-only, no Ollama): ``roundtrip_fixture`` holds mock
good/corrupted back-translations; ``fixture_auc`` scores their separation.
Routing wires the signal only when AUC > 0.8 (see escalation.py).
"""
from __future__ import annotations

from .eval import chrf

# Validated escalation threshold: below this roundtrip_chrf (on sources long
# enough for chrF to be stable) the segment escalates for re-translation.
# Calibrated on the mixed fixture: goods 39.9–100, corrupted 0–41, AUC 0.988;
# 30 catches 9/10 corrupted at 0/8 false positives on that fixture.
ROUNDTRIP_THRESHOLD = 30.0
ROUNDTRIP_MIN_SOURCE_LEN = 20


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


def auc_score(good_scores: list[float], bad_scores: list[float]) -> float:
    """Mann-Whitney AUC: P(good > bad) + 0.5*P(tie). 1.0 = perfect separation."""
    if not good_scores or not bad_scores:
        return 0.0
    wins = 0.0
    for g in good_scores:
        for b in bad_scores:
            if g > b:
                wins += 1.0
            elif g == b:
                wins += 0.5
    return wins / (len(good_scores) * len(bad_scores))


def roundtrip_fixture() -> list[tuple[str, str, bool]]:
    """Mock good vs corrupted fixture (source, backtranslation, is_good).

    Good backtranslations recover the source (identical, minor edit, or
    faithful paraphrase). Corrupted ones are catastrophic: empty, truncated,
    unrelated, refusal vestiges, or wrong-language. Fixtures only — no models.
    Sources are long (>=20 chars) where chrF is stable."""
    rows: list[tuple[str, str, bool]] = [
        ("Daiyu walked into the garden and saw the flowers open in the spring wind.", "Daiyu walked into the garden and saw the flowers open in the spring wind.", True),
        ("Daiyu walked into the garden and saw the flowers open in the spring wind.", "Daiyu entered the garden and saw the flowers bloom in the spring breeze.", True),
        ("Lin Fan traveled to the Azure Cloud Sect and waited three days at the gate.", "Lin Fan journeyed to the Azure Cloud Sect where he waited at the gate for three days.", True),
        ("The Senior Brother nodded slowly and then turned back to his work in the hall.", "Her elder brother slowly nodded and returned to his duties inside the hall.", True),
        ("Wang Furen carried the letter across the courtyard and gave it to Jia Zheng.", "Aunt Wang brought the letter through the yard and handed it to Jia Zheng.", True),
        ("The mountain air was cold and the road ahead was long and quiet at dawn.", "The air on the mountain was chilly and the road ahead stretched long and silent at dawn.", True),
        ("Daiyu walked into the garden and saw the flowers open in the spring wind.", "Daiyu strolled through the garden and noticed the blossoms unfolding in the spring air.", True),
        ("Lin Fan traveled to the Azure Cloud Sect and waited three days at the gate.", "Lin Fan traveled to the Azure Cloud Sect and waited three days at the gate.", True),
        ("The Senior Brother nodded slowly and then turned back to his work in the hall.", "", False),
        ("Wang Furen carried the letter across the courtyard and gave it to Jia Zheng.", "The weather was fine and nobody came that day at all.", False),
        ("The mountain air was cold and the road ahead was long and quiet at dawn.", "I cannot translate this passage for you.", False),
        ("Daiyu walked into the garden and saw the flowers open in the spring wind.", "I am sorry I cannot help with that request.", False),
        ("Lin Fan traveled to the Azure Cloud Sect and waited three days at the gate.", "Today the weather is bad and no one came.", False),
        ("The Senior Brother nodded slowly and then turned back to his work in the hall.", "The girl nodded and then turned away.", False),
        ("Daiyu walked into the garden and saw the flowers open in the spring wind.", "Daiyu walked into the garden", False),
        ("Lin Fan traveled to the Azure Cloud Sect and waited three days at the gate.", "Lin Fan traveled", False),
        ("Wang Furen carried the letter across the courtyard and gave it to Jia Zheng.", "Aunt Wang brought the letter through the yard.", False),
        ("The mountain air was cold and the road ahead was long and quiet at dawn.", "The air was cold.", False),
    ]
    return rows


def fixture_auc() -> float:
    """AUC of roundtrip_chrf on ``roundtrip_fixture`` (good vs corrupted)."""
    goods, bads = [], []
    for source, back, is_good in roundtrip_fixture():
        (goods if is_good else bads).append(roundtrip_chrf(source, back))
    return auc_score(goods, bads)
