"""QA: deterministic checks (spec §36) + optional AI review (spec §37).

Deterministic checks are cheap, always run, and replace their own earlier findings (no duplicate rows).
Only `error` findings move a segment to NEEDS_REVIEW; warnings are listed but do not block.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .db import Database
from .models import SegmentState

_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")
# A quoted inscription with a parenthetical gloss ("敕造宁國府" (Built by
# imperial order…)) is translated, not leftover: the CJK is the quoted source.
_GLOSSED = re.compile(r"[\"“「『][一-鿿぀-ヿ가-힯]+[\"”」』]\s*\([^()]*\)")
_NUM = re.compile(r"\d+")
_LEAK = re.compile(r"<think>|\[(?:Passage|Glossary|Story context|Draft|Source)\b|^\s*(?:Translation|Output)\s*:", re.IGNORECASE | re.MULTILINE)
# Unmapped entity proxies (?E200?, [E4], @@E3@@, __E1__, ⟦E2⟧) are never
# legitimate English — the model rewrote or invented them instead of passing
# them through (Red Chamber DG18: ?E200?). An error, not a warning: the
# segment needs re-translation, and the finding routes it there.
_PROXY_REMANT = re.compile(r"\u27e6E\d+\u27e7|@@E\d+@@|__E\d__|\[E\d+\]|\?E\d+\?")
_LOOP = re.compile(r"(\b\w+(?:\W+\w+){2,5}\b)(?:\W+\1){3,}", re.IGNORECASE)
# Closed-class English demonstratives/pronouns for the antecedent check. Only
# grammatical word-forms are listed — no open-vocabulary word lists.
_DEMONSTRATIVE = {"this", "that", "these", "those", "he", "she", "they"}
# Source-side equivalents (also closed-class grammar):
_ZH_PRONOUN = "这那他她它我你"
_JA_PRONOUN = ("これ", "それ", "あれ", "この", "その", "あの", "彼", "彼女", "私", "僕", "俺")
# Mechanical register proxy: punctuation/exclamation density + sentence length.
# Bilingual punctuation (Latin + CJK) keeps it language-agnostic; no word lists.
_SENT_SPLIT = re.compile(r"[^.!?。！？…]+[.!?。！？…]?")
_PUNCT_CHARS = re.compile(r"[.!?,;:\"'—…，、；：？！。”‘’]")
_EXCL_CHARS = re.compile(r"[!！]")
_REGISTER_FLOOR = {"avg_len": 4.0, "excl": 1.0, "punct": 0.5}
REGISTER_MIN_BASELINE = 8

DETERMINISTIC = ("missing_translation", "prompt_leak", "proxy_remnant", "untranslated_source", "number_mismatch", "suspicious_omission",
                 "suspicious_expansion", "repetition", "term_not_applied", "truncated_ending",
                 "term_variant_leak", "voice_register_drift", "antecedent_check")


def _first_content_token(text: str) -> str:
    m = re.search(r"[A-Za-z']+", text or "")
    return m.group(0).lower().strip("'") if m else ""


def _source_has_antecedent(source: str, source_lang: str = "zh") -> bool:
    """True if the source carries a demonstrative/pronoun equivalent."""
    if not source:
        return False
    lang = (source_lang or "").lower()
    if lang.startswith("en"):
        toks = {t.lower().strip("'") for t in re.findall(r"[A-Za-z']+", source)}
        return bool(toks & _DEMONSTRATIVE)
    if lang.startswith("ja"):
        return any(p in source for p in _JA_PRONOUN)
    return any(c in source for c in _ZH_PRONOUN)


def register_features(text: str) -> dict[str, float]:
    """Mechanical register features: avg sentence char length + densities per 100 chars.
    No word lists: length and punctuation only."""
    text = text or ""
    sents = [s.strip() for s in _SENT_SPLIT.findall(text) if s.strip()]
    if not sents:
        return {"avg_len": float(len(text)), "excl": 0.0, "punct": 0.0}
    avg_len = sum(len(s) for s in sents) / len(sents)
    n = max(1, len(text))
    return {
        "avg_len": avg_len,
        "excl": 100 * len(_EXCL_CHARS.findall(text)) / n,
        "punct": 100 * len(_PUNCT_CHARS.findall(text)) / n,
    }


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def check_register_drift(translated: str, register_baseline: Optional[list[str]] = None):
    """Flag when the candidate's register features deviate >2.5 * IQR from the
    baseline median on any feature. Returns a finding tuple or None.
    Small baselines (<8) never flag (fail open)."""
    if not translated or not translated.strip():
        return None
    base = [t for t in (register_baseline or []) if t and t.strip()]
    if len(base) < REGISTER_MIN_BASELINE:
        return None
    base_feats = [register_features(t) for t in base]
    feat = register_features(translated)
    for key in ("avg_len", "excl", "punct"):
        vals = sorted(f[key] for f in base_feats)
        med = _percentile(vals, 50)
        q1, q3 = _percentile(vals, 25), _percentile(vals, 75)
        iqr = max(0.0, q3 - q1)
        # Floor avoids degenerate IQR=0 (e.g. no '!' in baseline) collapsing the
        # threshold to zero and flagging a single exclamation.
        effective = max(iqr, _REGISTER_FLOOR[key])
        if abs(feat[key] - med) > 2.5 * effective:
            return ("warning", "voice_register_drift",
                    f"Register outlier vs project median: {key}={feat[key]:.1f} vs {med:.1f} (IQR {iqr:.1f}).")
    return None


def _losers_from_db(db: Database, project_id: int) -> dict[str, list[str]]:
    """Collect {source_term: loser surfaces} from terminology canonical JSON."""
    out: dict[str, list[str]] = {}
    try:
        rows = db.terminology_for_project(project_id)
    except Exception:  # noqa: BLE001 — QA must not crash on terminology shape
        return {}
    for t in rows:
        try:
            forms = json.loads(t["source_forms"] or "[]")
        except Exception:  # noqa: BLE001
            forms = []
        try:
            canon = json.loads(t["canonical_translation"] or "{}")
        except Exception:  # noqa: BLE001
            canon = {}
        losers = canon.get("losers") or []
        clean = [str(x) for x in losers if x]
        if not clean:
            continue
        for f in forms:
            out.setdefault(str(f), []).extend(clean)
    return out


def check_segment(source: str, translated: str, source_lang: str = "zh", glossary: Optional[dict[str, str]] = None,
                  term_losers: Optional[dict[str, list[str]]] = None,
                  register_baseline: Optional[list[str]] = None) -> list[tuple[str, str, str]]:
    """-> [(severity, type, message)]. Pure function: easy to unit-test."""
    if not translated or not translated.strip():
        return [("error", "missing_translation", "Segment has no translation.")]
    found: list[tuple[str, str, str]] = []
    if _LEAK.search(translated):
        found.append(("error", "prompt_leak", "Output contains prompt or reasoning markup."))
    if _PROXY_REMANT.search(translated):
        found.append(("error", "proxy_remnant", "Output contains an unmapped entity proxy (?E200?/[E4]); re-translate."))
    cjk_source = bool(_CJK.search(source))
    if cjk_source and _CJK.search(_GLOSSED.sub("", translated)):
        found.append(("warning", "untranslated_source", "Translation still contains CJK characters."))
    norm_in = re.sub(r"[\W_]+", "", source or "").casefold()  # Nano Machine: screams came back
    norm_out = re.sub(r"[\W_]+", "", translated or "").casefold()  # unchanged ('ACK!!!' -> 'ACK！！！')
    if norm_in and norm_in == norm_out and len(norm_in) <= 30 and re.search(r"[^\W\d_]", norm_in):
        found.append(("warning", "untranslated_source", "Translation copies the source unchanged."))
    src_nums = _NUM.findall(source)
    if src_nums and not all(n in translated for n in src_nums):
        found.append(("warning", "number_mismatch", f"Numbers {src_nums} not all present in translation."))
    # English is ~2.5x longer than Chinese by characters, so the thresholds depend on the script
    lo, hi = (0.8, 12.0) if cjk_source else (0.3, 4.0)
    if len(source) >= 12 and len(translated) < len(source) * lo:
        found.append(("warning", "suspicious_omission", "Translation much shorter than source."))
    if len(translated) > max(80, len(source) * hi):
        found.append(("warning", "suspicious_expansion", "Translation much longer than source."))
    if _LOOP.search(translated):
        found.append(("warning", "repetition", "Translation repeats the same phrase."))
    if len(source) >= 60 and not re.search(r"[.!?…?\"”」』)\]]\s*$", translated):
        # Rashomon CAT: a 322-char segment stopped mid-word ('religious ') at
        # 1/5 of its token budget — early EOS, not a budget cutoff. Length
        # ratios miss it (output was longer than source); the missing terminal
        # punctuation is the tell. Warning: flags for re-translation review.
        found.append(("warning", "truncated_ending", "Translation ends without terminal punctuation (possible early stop)."))
    low = translated.lower()
    for src, tgt in (glossary or {}).items():
        if src in source and tgt.lower() not in low:
            found.append(("warning", "term_not_applied", f"'{src}' should be rendered '{tgt}'."))
    # term_variant_leak: a losing surface that normalize_renderings should have
    # rewritten is still verbatim in the output. Mirrors the rewriter's
    # word-boundary, case-insensitive match; winner itself never flags.
    for src, losers in (term_losers or {}).items():
        if not src or src not in source:
            continue
        winner = (glossary or {}).get(src, "")
        for loser in (losers or []) if isinstance(losers, (list, tuple)) else [losers]:
            loser = str(loser)
            if not loser or (winner and loser.lower() == winner.lower()):
                continue
            if re.search(r"(?<!\w)" + re.escape(loser) + r"(?!\w)", translated, flags=re.IGNORECASE):
                found.append(("warning", "term_variant_leak", f"'{src}' leaked losing variant '{loser}'."))
                break
    # antecedent_check: demonstrative-first translation with no source-side
    # demonstrative/pronoun — possible lost antecedent (warning, review).
    first = _first_content_token(translated)
    if first in _DEMONSTRATIVE and not _source_has_antecedent(source, source_lang):
        found.append(("warning", "antecedent_check",
                      f"Translation starts with '{first}' but the source has no demonstrative/pronoun — possible lost antecedent."))
    # voice_register_drift: mechanical register outlier vs project baseline.
    drift = check_register_drift(translated, register_baseline)
    if drift is not None:
        found.append(drift)
    return found


def qa_segment(db: Database, project_id: int, segment_id: int, source: str, translated: str, source_lang: str = "zh",
               glossary: Optional[dict[str, str]] = None, term_losers: Optional[dict[str, list[str]]] = None,
               register_baseline: Optional[list[str]] = None) -> list[str]:
    """Re-run deterministic checks for one segment. Returns the finding types."""
    db.clear_qa(segment_id, DETERMINISTIC)
    findings = check_segment(source, translated, source_lang, glossary, term_losers, register_baseline)
    for sev, typ, msg in findings:
        db.add_qa(project_id, segment_id, sev, typ, msg)
    return [t for _, t, _ in findings]


def run_qa(db: Database, project_id: int, glossary: Optional[dict[str, str]] = None, chapter_id: Optional[int] = None,
           term_losers: Optional[dict[str, list[str]]] = None) -> dict:
    """QA stage over translated segments: errors -> NEEDS_REVIEW, otherwise TRANSLATED/REFINED -> QA_PASSED.
    HUMAN_APPROVED segments are checked but never moved."""
    project = db.get_project(project_id)
    if term_losers is None:
        term_losers = _losers_from_db(db, project_id)
    # Rolling-median baseline for voice_register_drift: project-wide translations.
    # Requires >= REGISTER_MIN_BASELINE segments so small projects stay dormant.
    try:
        rows = db.conn.execute("SELECT translated_text FROM segments WHERE project_id=? AND translated_text IS NOT NULL AND translated_text<>''",
                               (project_id,)).fetchall()
        baseline = [r[0] for r in rows if r[0] and r[0].strip()]
        if len(baseline) < REGISTER_MIN_BASELINE:
            baseline = None
    except Exception:  # noqa: BLE001 — drift is advisory; never block QA
        baseline = None
    q = ("SELECT s.* FROM segments s JOIN chapters c ON c.id=s.chapter_id WHERE s.project_id=? "
         "AND s.translated_text IS NOT NULL")
    args: list = [project_id]
    if chapter_id is not None:
        q += " AND s.chapter_id=?"
        args.append(chapter_id)
    stats = {"checked": 0, "passed": 0, "needs_review": 0, "warnings": 0}
    for s in db.conn.execute(q + " ORDER BY c.order_index, s.order_index", args).fetchall():
        sevs = {(sev) for sev, _, _ in check_segment(s["source_text"], s["translated_text"], project["source_lang"], glossary, term_losers, baseline)}
        qa_segment(db, project_id, s["id"], s["source_text"], s["translated_text"], project["source_lang"], glossary, term_losers, baseline)
        stats["checked"] += 1
        stats["warnings"] += "warning" in sevs
        if s["state"] == SegmentState.HUMAN_APPROVED.value:
            continue
        if "error" in sevs:
            db.update_segment(s["id"], state=SegmentState.NEEDS_REVIEW)
            stats["needs_review"] += 1
        elif s["state"] in (SegmentState.TRANSLATED.value, SegmentState.REFINED.value, SegmentState.NEEDS_REVIEW.value, SegmentState.QA_PASSED.value):
            db.update_segment(s["id"], state=SegmentState.QA_PASSED)
            stats["passed"] += 1
    return stats


def ai_qa(db: Database, project_id: int, provider, *, limit: int = 50, chapter_id: Optional[int] = None) -> int:
    """Run provider.review on translated segments; store structured findings (spec §37).
    Earlier AI findings for a segment are replaced, not appended."""
    q = "SELECT * FROM segments WHERE project_id=? AND translated_text IS NOT NULL"
    args: list = [project_id]
    if chapter_id is not None:
        q += " AND chapter_id=?"
        args.append(chapter_id)
    n = 0
    for s in db.conn.execute(q + " ORDER BY chapter_id, order_index LIMIT ?", (*args, limit)).fetchall():
        db.conn.execute("DELETE FROM qa_results WHERE segment_id=? AND type LIKE 'ai_%'", (s["id"],))
        try:
            findings = provider.review(s["source_text"], s["translated_text"])
        except Exception as exc:  # provider down/misconfigured -> deterministic findings remain
            db.add_qa(project_id, s["id"], "error", "ai_qa_failed", str(exc))
            continue
        for f in findings:
            db.add_qa(project_id, s["id"], f.get("severity", "warning"), "ai_" + str(f.get("type", "issue")).removeprefix("ai_"), f.get("message", ""))
        n += 1
    db.conn.commit()
    return n
