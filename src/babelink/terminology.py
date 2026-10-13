"""Progressive terminology (spec §15, §79): mine -> LLM classify/translate -> user accepts/edits/ignores.

Status flow: suggested -> confirmed | ignored.  Only non-ignored terms with a translation reach prompts.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from .prompts import ProviderError, RefusalError
from .story_bible import load_section, save_section

_CJK_RUN = re.compile(r"[一-鿿]{2,}")
_KATAKANA_RUN = re.compile(r"[ァ-ヶー]{2,}")  # ja loanword/name runs (no particles live here)
_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]*|[.!?]+|[^\sA-Za-z]")
_CJK_ANY = re.compile(r"[一-鿿぀-ヿ가-힯]")

# Candidates that are never terms, dropped before the helper ever sees them.
# Only STRUCTURAL patterns live here (shapes, never word judgments):
# pure number+counter (三日, 二人), and speech-verb + 曰 ("said").
# Everything else — which ordinary words, kinship terms or fragments are not
# terms — is decided by statistics (dispersion rank below), the helper's own
# verdict, translator agreement, and the book election. Word lists that used
# to live here (_STOP_WORDS, _EDGE_STOP, _TIME_WORD, _JUNK_WORDS, _KIN_SUFFIX)
# were deleted in E17: they overfit the eval books and silently drop real
# terms in new books; a statistical + model-judgment pipeline
# generalizes where a list cannot.
_NUMBER_COUNTER = re.compile(r"^[一二三四五六七八九十百千萬兩〇零\d]+[日月年天時分秒人個次回位名隻匹頭]+$")
# Speech-verb + 曰 ("said") can never be a name; name + 曰 (異史氏曰 — the
# narrator's tag) stays classifiable. Bare 曰/道/云 alone are never terms.
_SAYING_VERB = re.compile(r"^(曰|道|云|[笑答怒問對謂請道云]曰)$")


def _is_mining_junk(term: str) -> bool:
    """Structural 'never a term': number+counter shapes and saying verbs.
    Kept for the inner n-gram loop (cheap shape test); the statistical
    dispersion rank in `mine_terms` does the rest of the filtering."""
    return bool(_NUMBER_COUNTER.match(term) or _SAYING_VERB.search(term))

# RENDERINGS: no word lists here. What used to be _JUNK_WORDS (ordinary-word
# renderings) and _KIN_SUFFIX (kinship-compound addresses) is now decided by
# MODEL JUDGMENT, not lists: translator agreement at suggest time (a second
# model must converge on the same rendering before it enforces), and the book
# election + demote_unused_terms post-translation (the outputs vote). Lists
# overfit the eval books; agreement generalizes. Deleted in E17.

# Pinyin syllables (toneless): a transliteration-shaped rendering ("San Ri",
# "Ruci", "Yuzhen") is only trustworthy for actual names.
_PINYIN_INIT = r"b|p|m|f|d|t|n|l|g|k|h|j|q|x|zh|ch|sh|r|z|c|s|y|w"
_PINYIN_FIN = (r"a|o|e|i|u|v|er|ai|ei|ui|ao|ou|iu|ie|ve|ue|an|en|in|un|vn|ang|eng|ing|ong"
              r"|ian|uan|uang|iang|iong")
_PINYIN_SYL = re.compile(rf"(?:(?:{_PINYIN_INIT})?(?:{_PINYIN_FIN}))+")  # fullmatch: engine backtracks syllable splits

# Japanese romaji morae: the ja->en twin of the pinyin gate (transliteration
# only for names: シャリ=Shari, ヘンリー=Henri; loanwords like アルメリア fail).
_ROMAJI_INIT = r"k|g|s|z|t|d|n|h|b|p|m|y|r|w|ky|gy|sh|j|ch|ny|hy|by|py|my|ry|ts|f|v"
_ROMAJI_FIN = r"a|i|u|e|o|n"
_ROMAJI_SYL = re.compile(rf"(?:(?:{_ROMAJI_INIT})?(?:{_ROMAJI_FIN}))+")
_NAME_TYPES = ("character", "location", "organization")


def _is_romaji_shaped(text: str) -> bool:
    tokens = [t for t in re.split(r"[^A-Za-züÜ]+", _strip_accents(text)) if t]
    return bool(tokens) and all(
        _ROMAJI_SYL.fullmatch(t.lower()) for t in tokens)


def _strip_accents(text: str) -> str:
    """Pinyin with tone marks (Wuhé) must still read as pinyin."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _is_pinyin_shaped(text: str) -> bool:
    """Every token parses as pinyin syllables ('San Ri', 'Yuzhen', 'Ruci').
    Real English ('Prince', 'Madam', 'Tell', 'Day') does not: 'ell', 'ay',
    'adam', 'ince' are not pinyin finals."""
    tokens = [t for t in re.split(r"[^A-Za-züÜ]+", _strip_accents(text)) if t]
    return bool(tokens) and all(
        _PINYIN_SYL.fullmatch(t.lower().replace("ü", "v")) for t in tokens)


def _cjk_candidates(texts: list[str]) -> Counter:
    counts: Counter[str] = Counter()
    for text in texts:
        for run in _CJK_RUN.findall(text):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    gram = run[i:i + n]
                    if _is_mining_junk(gram):
                        continue
                    counts[gram] += 1
    return counts


def _latin_candidates(texts: list[str]) -> Counter:
    """Capitalized word runs, ignoring a lone capitalized sentence opener ("The", "He")."""
    counts: Counter[str] = Counter()
    for text in texts:
        toks = _LATIN_TOKEN.findall(text)
        i, sentence_start = 0, True
        while i < len(toks):
            tok = toks[i]
            if tok[0].isupper() and tok[1:2].islower():
                j = i
                while j + 1 < len(toks) and toks[j + 1][:1].isupper() and toks[j + 1][1:2].islower():
                    j += 1
                if j > i or not sentence_start:
                    counts[" ".join(toks[i:j + 1])] += 1
                i, sentence_start = j + 1, False
                continue
            sentence_start = tok[0] in ".!?" or (sentence_start and not tok[0].isalnum())
            i += 1
    return counts


def mine_terms(texts: list[str], min_count: int = 3, top: int = 50) -> list[tuple[str, int]]:
    """Term-like n-grams ranked by TF-IDF-ish weight (count × log(N/df)): names
    recur a lot in FEW passages; grammar (的/了/是…) is everywhere and sinks.
    No word lists: burstiness is the statistical signature of a term, in any
    language. Fragments of longer terms are still folded structurally below."""
    import math

    counts = _cjk_candidates(texts) + _latin_candidates(texts)
    for text in texts:  # katakana names/loanwords: particles never appear here, so runs are safe terms
        for run in _KATAKANA_RUN.findall(text):
            run = run.lstrip("ー")  # a trailing ー is phonemic (ヘンリー), never strip it
            if len(run) >= 2:
                counts[run] += 1
    n = max(1, len(texts))
    df: dict[str, int] = {}
    for text in texts:
        for t in set(_CJK_RUN.findall(text)):
            for gram_len in (2, 3, 4):
                for i in range(len(t) - gram_len + 1):
                    df[t[i:i + gram_len]] = df.get(t[i:i + gram_len], 0) + 1
    cand = {}
    for t, c in counts.items():
        if c < min_count:
            continue
        if n >= 10 and df.get(t, n) / n > 0.8:
            continue  # grammar: present in nearly every passage (的/了/是…).
            # Small corpora exempt (n<10): every n-gram is diffuse there, and
            # names must still surface.
        cand[t] = c
    # extension index: for each candidate, the best count among candidates that extend it by one char/word
    ext: dict[str, int] = {}
    for t, c in cand.items():
        if " " in t:
            parts = t.split(" ")
            subs = [" ".join(parts[1:]), " ".join(parts[:-1])] if len(parts) > 1 else []
        else:
            subs = [t[1:], t[:-1]] if len(t) > 2 else []
        for s in subs:
            if s:
                ext[s] = max(ext.get(s, 0), c)
    keep: list[tuple[str, int, float]] = []
    for t, c in cand.items():
        if _is_mining_junk(t):
            continue
        if ext.get(t, 0) >= 0.7 * c:  # fragment of a longer, equally frequent term
            continue
        if " " in t:
            parts = t.split(" ")
            subs = [" ".join(parts[1:]), " ".join(parts[:-1])]
        else:
            subs = [t[1:], t[:-1]] if len(t) > 2 else []
        if any(s in cand and c < 0.3 * cand[s] for s in subs):  # rare extension of a common term
            continue
        w = c * math.log(n / max(1, df.get(t, 1)))  # burstiness, not raw count
        keep.append((t, c, w))
    keep.sort(key=lambda x: (-x[2], -x[1], -len(x[0]), x[0]))
    return [(t, c) for t, c, _ in keep[:top]]


# ---- glossary / DB-backed flow ------------------------------------------------------------------
def build_glossary(db, project_id: int) -> dict[str, str]:
    """source form -> target rendering, for every non-ignored term that has one.
    Suggested terms pass the same quality gates as at suggest time (a stale bad
    rendering sitting in the DB must not reach the prompt); user-confirmed
    terms always pass."""
    project = db.get_project(project_id)
    target = project["target_lang"]
    glossary: dict[str, str] = {}
    for t in db.terminology_for_project(project_id):
        if t["status"] == "ignored":
            continue
        tr = json.loads(t["canonical_translation"]).get(target)
        if not tr:
            continue
        if t["status"] != "confirmed" and _is_bad_translation(
                json.loads(t["source_forms"])[0], tr, target, t["concept"] or "", project["source_lang"]):
            continue
        for form in json.loads(t["source_forms"]):
            glossary[form] = tr
    return glossary


def upsert_candidates(db, project_id: int, candidates: list[tuple[str, int]]) -> int:
    """Add new candidates; refresh occurrence counts of known ones. Returns number added."""
    from .models import TerminologyEntry

    known: dict[str, int] = {}
    for t in db.terminology_for_project(project_id):
        for f in json.loads(t["source_forms"]):
            known[f] = t["id"]
    added = 0
    for term, count in candidates:
        if term in known:
            db.update_terminology(known[term], occurrences=count)
        else:
            db.add_terminology(TerminologyEntry(None, project_id, [term], {}, "", "preferred", "suggested", count))
            added += 1
    return added


def _example(db, project_id: int, term: str) -> str:
    row = db.conn.execute("SELECT source_text FROM segments WHERE project_id=? AND instr(source_text, ?)>0 ORDER BY length(source_text) LIMIT 1",
                          (project_id, term)).fetchone()
    return row["source_text"] if row else ""


def _is_common_word(translation: str) -> bool:
    """A cased rendering that starts lower-case ("waste", "a", "body") is an ordinary word, not a name or fixed term.
    Enforcing it would rewrite normal prose ("this waste die?"), so it never enters the glossary."""
    return translation.lower() != translation.upper() and not translation[:1].isupper()


def _normalize_case(tr: str) -> str:
    """The terms prompt asks for natural casing, so good renderings arrive
    lower-case ('his majesty', 'knight') alongside junk ('three days',
    'laughed'). Title-case all-lowercase renderings first — the content gates
    below then keep 'His Majesty'/'Knight' and drop 'Three Days'/'Laughed'.
    Already-cased and CJK renderings pass through untouched. Medial capitals
    inside pinyin-shaped words ('Wang FuRen', 'DaiYu') are helper noise and
    flatten ('Wang Furen'): no pinyin word has an internal capital."""
    tr = tr.strip()
    if tr and tr == tr.lower():
        return tr.title()
    return " ".join(_flatten_medial_caps(w) for w in tr.split(" "))


def _flatten_medial_caps(word: str) -> str:
    core = word.strip("'-")
    if core[:1].isupper() and core[1:] != core[1:].lower() and _is_pinyin_shaped(core):
        return word.replace(core, core[:1] + core[1:].lower())
    return word


def _pinyin_matches(term: str, rendering: str) -> bool:
    """Phonetic authority: every pinyin-shaped word of the rendering must align
    to consecutive chars' kMandarin readings (any polyphone reading counts).
    Non-pinyin words ('Lady' in 'Lady Wang') are descriptive and pass freely;
    term chars may go unrendered (林黛玉->'Dai Yu' drops the surname, 王夫人's 夫人
    is covered by 'Lady'). Chars missing from the table impose nothing, and a
    term with NO reading data passes (fail open). Tone marks are stripped
    before matching ('Wuhòu' reads as 'wuhou'). What this rejects is
    misreading: 黛玉='Da Yu' (dai≠da), 賈赦='Jiasha' (she≠sha),
    雨村='Yu Jun' (cun≠jun) — never enforced, proposed, or voted on.
    No model calls; table is vendored Unihan data."""
    import unicodedata
    from functools import lru_cache

    from .pinyin_table import readings as _table_readings

    chars = [c for c in term if "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf"]
    ascii_render = "".join(c for c in unicodedata.normalize("NFKD", rendering)
                           if not unicodedata.combining(c))
    words = re.findall(r"[A-Za-z]+", ascii_render.lower())
    if not chars or not words:
        return True
    rsets = [_table_readings(c) for c in chars]
    if any(not r for r in rsets):
        return True

    @lru_cache(maxsize=None)
    def ok(ci: int, wi: int, off: int) -> bool:
        if wi == len(words):
            return True  # every word accounted for; leftover chars are drops
        if ci >= len(chars):
            return False
        w = words[wi]
        if off == 0 and not _is_pinyin_shaped(w):
            if ok(ci, wi + 1, 0):  # descriptive word: passes freely
                return True
        if off == 0 and ok(ci + 1, wi, 0):  # unrendered char (dropped surname part)
            return True
        return any(w.startswith(r, off) and (ok(ci + 1, wi, off + len(r)) if off + len(r) < len(w)
                                             else ok(ci + 1, wi + 1, 0))
                   for r in rsets[ci])

    return ok(0, 0, 0)


_RENDAKU = {"k": "g", "s": "z", "t": "d", "h": "b"}  # sequential voicing (本田 Honda: TA→DA)
# Honorifics/titles ride on the name ('Mr. Tanaka', 'Tanaka-san') — strip them
# before phonetic matching so the gate judges the name, not the politeness.
_HONORIFICS = {"mr", "mrs", "ms", "miss", "master", "san", "sama", "kun", "chan",
               "dono", "sensei", "senpai", "sama", "lord", "lady", "sir"}


def _fold_romaji(s: str) -> str:
    """Long vowels collapse for matching (Tokyo=TOUKYOU, Osaka O=OO); letters only."""
    s = s.lower().replace("ou", "o").replace("oo", "o").replace("uu", "u")
    return "".join(c for c in s if c.isalpha())


# Verb/adjective endings drop in compounds (高橋 Takahashi=TAKA+HASHI from
# TAKAI; 渡辺 Watanabe=WATA+BE from WATARU). Suffixes chosen so real readings
# never truncate into nothing useful: HASHI/HAYASHI/WATASHI end in SHI/HI and
# are untouched; only true verb/adj endings strip, keeping length>=2.
_CONJUGATE = ("RU", "SU", "TSU", "KU", "GU", "BU", "MU", "NU", "U", "AI", "OI")


def _stem_variants(reading: str) -> set[str]:
    out = {reading}
    for suf in _CONJUGATE:
        if reading.endswith(suf) and len(reading) - len(suf) >= 2:
            out.add(reading[:-len(suf)])
    if reading.endswith("I") and len(reading) >= 5:  # TAKAI->TAKA (HASHI->HASH harmless: never proposed)
        out.add(reading[:-1])
    return out


def _romaji_matches(term: str, rendering: str) -> bool:
    """Phonetic authority for Japanese (mirrors `_pinyin_matches`): every kanji
    must consume a prefix of the rendering via one of its Unihan kun/on
    readings (+rendaku, +long-vowel fold). Hiragana/katakana/okurigana are not
    in the table and impose nothing; a term with NO kanji reading data passes
    (fail open). Rejects misreads (山本='Yamamoyo'); table gaps (阪='Saka')
    stay unenforced rather than wrongly enforced. No model calls."""
    from functools import lru_cache

    from .romaji_table import READINGS as _kj

    chars = [c for c in term if c in _kj]
    if not chars:
        return True
    toks = [t for t in re.findall(r"[A-Za-z]+", rendering.lower()) if t not in _HONORIFICS]
    target = _fold_romaji("".join(toks))
    if not target:
        return True
    opts = []
    for ch in chars:
        alt = set()
        for r in _kj[ch]:
            for stem in _stem_variants(r):
                f = _fold_romaji(stem)
                alt.add(f)
                if f and f[0] in _RENDAKU:
                    alt.add(_RENDAKU[f[0]] + f[1:])
        opts.append(alt)

    @lru_cache(maxsize=None)
    def dp(i: int, j: int) -> bool:
        if i == len(opts):
            return j == len(target)
        return any(target.startswith(o, j) and dp(i + 1, j + len(o)) for o in opts[i])

    if dp(0, 0):
        return True
    if len(opts) >= 2:
        # Ateji/compound surnames (渡辺 Watanabe: 辺=NABE is not a listed
        # reading): the middle is unverifiable, but anchored ends are still a
        # strong signal. Near-miss misreads (Yamamoyo: 本≠*moyo tail,
        # Watanaba: 辺≠*ba tail) fail it. The first anchor also accepts a
        # shared initial (大阪 Osaka: 大=O- is real though Unihan lists only
        # OO-forms) — initials are stable, tails must still match in full.
        first_ok = any(target.startswith(o) for o in opts[0] if o)
        first_ok = first_ok or any(o.startswith(target[0]) for o in opts[0] if o)
        last_ok = any(target.endswith(o) for o in opts[-1] if o)
        if first_ok and last_ok:
            return True
    return False


def _is_bad_translation(term: str, translation: str, target: str, concept: str = "", source_lang: str = "zh") -> bool:
    """Reject model echoes: empty, identical to the source, or still in the
    source script (a ja term "translated" as 陛下 for target en). The model
    correctly ignores such mappings at translate time, so keeping them only
    poisons term-adherence scoring and the prompt. Also rejects:
    ALL-CAPS renderings ("AND SAY"); phrases made only of time/number/saying
    words ("Next Day", "Honest Tell", "Come Up"); and transliteration-shaped
    renderings for non-names ("San Ri" for 三日, "Daisu" misreadings) — a
    transliteration is only trustworthy for actual names
    (character/location/organization). `concept` is the helper's type label;
    user-confirmed terms bypass every gate."""
    if not translation or translation == term:
        return True
    if target == "en" and _CJK_ANY.search(translation):
        return True
    cased_latin = translation.lower() != translation.upper()  # CJK has no case: skip the rule below
    if cased_latin and translation == translation.upper():
        return True  # ALL-CAPS ("AND SAY", "GENERAL") is never a name rendering
    words = [w.strip("'-") for w in re.split(r"[^A-Za-züÜ]+", translation) if w.strip("'-")]
    content = [w for w in words if w.lower() not in {
        "a", "an", "the", "of", "and", "or", "to", "for", "with", "as", "at", "by", "on", "from", "in"}]
    if content and target == "en" and concept not in _NAME_TYPES:
        if _is_pinyin_shaped(translation):
            return True
        if source_lang == "ja" and _is_romaji_shaped(translation):
            return True
    if (content and target == "en" and concept in _NAME_TYPES and source_lang == "zh"
            and _is_pinyin_shaped(translation) and not _pinyin_matches(term, translation)):
        return True  # phonetic authority: misread names (Da Yu, Jiasha, Yu Jun) never enforce
    if (content and target == "en" and concept in _NAME_TYPES and source_lang == "ja"
            and _is_romaji_shaped(translation) and not _romaji_matches(term, translation)):
        return True  # phonetic authority (ja): misread kanji names never enforce; table gaps fail open elsewhere
    return _is_common_word(translation)


def _norm_pinyin(s: str) -> str:
    """Agreement form: letters only, lowercased, leading article dropped.

    Articles are grammar, not vocabulary: 'The Old Lady' and 'Old Lady' are
    the same rendering decision (DH21: determiner-only differences demoted
    老太太-class names to vote-only). Closed-class strip, applied everywhere
    the norm is used so elections tally the same way."""
    return re.sub(r"^(the|an|a)", "", re.sub(r"[^a-z]", "", s.lower()))


def _title_pinyin(probe: str) -> str:
    """'yucun' -> 'Yucun'; 'LADY WANG'/'Lady Wang' -> 'Lady Wang': title-case
    per word, preserving breaks. Single-token capitalize ('Ladywang') is a
    distorted surface form that must never become a candidate — the vote
    tallies letters-only, so it would match every 'Lady Wang' output and win
    while breaking substring adherence and future prompts."""
    words = re.findall(r"[A-Za-z']+", probe)
    return " ".join(w[:1].upper() + w[1:].lower() for w in words)


def _latin_phrases(text: str) -> list[str]:
    """1-3 word runs of capitalized Latin words ('Lady Wang', 'Wang Lady',
    'Rong Mansion') — the surfaces translations actually use for names."""
    words = re.findall(r"[A-Za-z][a-z]*'?[a-z]*", text)
    out = []
    for i, w in enumerate(words):
        if not w[:1].isupper():
            continue
        for n in (3, 2, 1):
            run = words[i:i + n]
            if len(run) == n and all(x[:1].isupper() for x in run):
                out.append(" ".join(run))
                break
    return out


def reconcile_glossary(db, project_id: int, target: str = "en", min_share: float = 0.8, min_occ: int = 3) -> dict:
    """Post-translation vote with output-mined surfaces: candidates define each
    term's reading bucket (letters-only norm), but the WINNER is the bucket's
    most frequent verbatim phrase in the term's own translated segments — not
    necessarily a recorded candidate. Recorded renderings can be distorted
    twins ('Ladywang') or guesses the book never uses ('Wang Furen'); the
    shipped text is the oracle, and it may know the natural form ('Lady Wang')
    that no component proposed. No model calls. Returns {term: winner}."""
    won = {}
    terms = db.terminology_for_project(project_id)
    for t in terms:
        canon = json.loads(t["canonical_translation"] or "{}")
        cands = canon.get("candidates") or ([canon.get(target)] if canon.get(target) else [])
        if not cands or t["status"] == "confirmed":
            continue
        forms = json.loads(t["source_forms"])
        term = forms[0] if forms else ""
        if not term:
            continue
        segs = [s for s in db.segments_for_project(project_id, translated_only=True)
                if term in (s["source_text"] or "")]
        if len(segs) < min_occ:
            continue
        norm = lambda s: re.sub(r"[^a-z]", "", s.lower())
        bucket = {norm(c) for c in cands if norm(c)}
        freq: dict[str, int] = {}
        for s in segs:
            for phrase in set(_latin_phrases(s["translated_text"] or "")):
                if norm(phrase) in bucket:
                    freq[phrase] = freq.get(phrase, 0) + 1
        if freq:
            # Most frequent verbatim surface; ties prefer spaced natural forms.
            best = max(sorted(freq), key=lambda p: (freq[p], " " in p, len(p)))
            n = freq[best]
            if n >= min_occ and n / len(segs) >= min_share:
                canon[target] = best
                # kept: normalize() rewrites these (union — misread losers recorded
                # at suggest time must survive election). Same-reading twins that
                # differ verbatim ('Ladywang' vs 'Lady Wang') are losers too: the
                # distortion must normalize to the elected surface.
                canon["losers"] = sorted(set(canon.get("losers", []))
                                         | {c for c in cands if c != best})
                db.update_terminology(t["id"], canonical_translation=canon, concept=t["concept"] or "name",
                                       status="suggested")  # elected: back in play
                won[term] = best
                continue
        # No candidate surface is used by the book's own outputs (DG17: enforced
        # 'Underground'/'Proper' that no output contains) — not a term, ignore
        # it so it stops eating helper budget, prompt tokens and proxies.
        # Case-insensitive: a lowercase book usage ('underground') still counts.
        if t["status"] == "suggested" and target in canon and not freq:
            lowers = [c.lower() for c in cands if c]
            if not any(any(lw in (s["translated_text"] or "").lower() for lw in lowers) for s in segs):
                db.update_terminology(t["id"], status="ignored")
    return won


def normalize_renderings(db, project_id: int, winners: dict[str, str], segment_ids=None,
                         target: str = "en") -> int:
    """Deterministic last mile, engine-agnostic: rewrite recorded losing
    renderings ('Yu Village') to the elected winner ('Yucun') in translated
    outputs. Only machine-translated segments (never human-approved/edited);
    re-runs deterministic QA on touched segments. Returns segments fixed."""
    from .models import SegmentState
    from .qa import qa_segment

    if not winners:
        return 0
    terms = {json.loads(t["source_forms"])[0]: t for t in db.terminology_for_project(project_id)
             if json.loads(t["source_forms"])}
    project = db.get_project(project_id)
    glossary = build_glossary(db, project_id)
    fixed = 0
    segs = db.segments_for_project(project_id, translated_only=True)
    if segment_ids is not None:
        segs = [s for s in segs if s["id"] in segment_ids]
    for s in segs:
        if s["state"] == SegmentState.HUMAN_APPROVED.value:  # a person's wording stays
            continue
        text = s["translated_text"] or ""
        new = text
        for term, winner in winners.items():
            if term not in (s["source_text"] or ""):
                continue
            row = terms.get(term)
            if not row:
                continue
            canon = json.loads(row["canonical_translation"] or "{}")
            losers = canon.get("losers", [])
            dropped = canon.get(target, "")  # a stored dropped/helper rendering also loses to the winner
            if dropped and _norm_pinyin(dropped) != _norm_pinyin(winner) and dropped not in losers:
                losers = [*losers, dropped]
            for loser in sorted(losers, key=len, reverse=True):
                if loser and loser != winner:
                    new = re.sub(r"(?<!\w)" + re.escape(loser) + r"(?!\w)", winner, new, flags=re.IGNORECASE)
        if new != text:
            db.update_segment(s["id"], translated=new)
            qa_segment(db, project_id, s["id"], s["source_text"], new,
                       project["source_lang"], {k: v for k, v in glossary.items() if k in s["source_text"]})
            fixed += 1
    return fixed


def measure_drift(db, project_id: int) -> dict:
    """Corpus-wide consistency number (O2): for every enforced glossary term,
    share of translated segments containing the term whose output carries the
    rendering. 100 = the whole book says the name one way. No model calls."""
    glossary = build_glossary(db, project_id)
    if not glossary:
        return {"score": 100.0, "terms": {}}
    segs = db.segments_for_project(project_id, translated_only=True)
    terms = {}
    for term, rendering in glossary.items():
        hits = tot = 0
        for s in segs:
            if term in (s["source_text"] or ""):
                tot += 1
                if rendering in (s["translated_text"] or ""):
                    hits += 1
        if tot:
            terms[term] = {"hits": hits, "total": tot}
    score = round(100 * sum(t["hits"] for t in terms.values()) / max(1, sum(t["total"] for t in terms.values())), 1)
    return {"score": score, "terms": terms}


def review_confirmed_terms(db, project_id: int, min_occ: int = 3) -> list[dict]:
    """Backward-revision trigger (O1, detection half): confirmed terms whose
    enforced rendering is the MINORITY in shipped outputs — later chapters voted
    otherwise (e.g. 國 enforced 'kingdom' but the book converged on 'palace').
    Returns [{term, enforced, observed, share}] for a human (or a retranslation
    pass) to adjudicate; never auto-applies. No model calls."""
    glossary = build_glossary(db, project_id)
    confirmed = {json.loads(t["source_forms"])[0] for t in db.terminology_for_project(project_id)
                 if t["status"] == "confirmed" and json.loads(t["source_forms"])}
    segs = db.segments_for_project(project_id, translated_only=True)
    norm = lambda s: re.sub(r"[^a-z]", "", s.lower())
    flagged = []
    for term in sorted(set(glossary) & confirmed):
        rendering = glossary[term]
        with_term = [s for s in segs if term in (s["source_text"] or "")]
        if len(with_term) < min_occ:
            continue
        hits = sum(1 for s in with_term if rendering in (s["translated_text"] or ""))
        if hits / len(with_term) < 0.5:
            words: dict[str, int] = {}
            for s in with_term:
                for w in re.findall(r"[A-Za-z][A-Za-z']+", s["translated_text"] or ""):
                    if len(w) > 2 and w[:1].isupper() and norm(w) != norm(rendering):  # Title-case only: lowercase
                        words[w] = words.get(w, 0) + 1  # prose verbs ('said') must not outvote names
            alt, n = max(words.items(), key=lambda kv: kv[1]) if words else ("", 0)
            flagged.append({"term": term, "enforced": rendering, "observed": alt,
                            "share": round(100 * hits / len(with_term), 1)})
    return flagged


def finalize_terms(db, project_id: int, segment_ids=None, target: str = "en") -> dict:
    """Book/chapter-level close-out: elect winners from shipped renderings,
    then normalize losing variants to them. Returns {term: winner}."""
    won = reconcile_glossary(db, project_id, target)
    normalize_renderings(db, project_id, won, segment_ids, target)
    return won


def _probe_transliteration(translator, term: str, source_lang: str, target: str) -> str:
    """The translation model's own rendering of the bare term, letters-only
    ('' when the probe fails — failures fail open upstream)."""
    try:
        probe = translator.translate(term, glossary={}, source_lang=source_lang, target_lang=target)
    except Exception:  # noqa: BLE001 — provider down, refusal, anything
        return ""
    return _norm_pinyin(probe) or ""


def _probe_title(translator, term: str, source_lang: str, target: str) -> str:
    """Seed-grade probe rendering: word breaks preserved ('Jia She', not
    'Jiashe') — the normed form used for agreement checks erases them."""
    try:
        probe = translator.translate(term, glossary={}, source_lang=source_lang, target_lang=target)
    except Exception:  # noqa: BLE001 — provider down, refusal, anything
        return ""
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", probe or "")
    if not words:
        return ""
    return _normalize_case(" ".join(words[:4]))  # a name, not a sentence


def _translator_agrees(translator, term: str, translation: str, source_lang: str, target: str) -> bool:
    """Letters-only agreement between the helper's pinyin rendering and the
    translation model's own transliteration habit (kept for tests/callers;
    the pipeline adopts the probe rendering on mismatch — see above)."""
    probe = _probe_transliteration(translator, term, source_lang, target)
    return bool(probe) and probe == _norm_pinyin(translation)


def _apply_suggestion(db, project_id: int, row, f: dict, target: str, source_lang: str, translator=None) -> bool:
    """Store one helper verdict. Returns True when the term gained a rendering.
    Shared by the batched pass and the solo second-chance pass."""
    raw = str(f["translation"])
    tr = _normalize_case(raw)
    concept = str(f.get("type", "")).strip().lower()
    term = str(f["term"]).strip()
    if (raw and raw == raw.lower() and concept not in (*_NAME_TYPES, "title")
            and row["status"] != "confirmed"):
        # The terms prompt's own contract: a lower-case rendering signals an
        # ordinary word ('three days') — never enforcement-grade, but still a
        # vote candidate (the book election prunes what outputs never use).
        # Structural case signal, not a word judgment. Titles ('his majesty')
        # and names still normalize + enforce below.
        canon = {**json.loads(row["canonical_translation"]), target: tr}
        canon["candidates"] = sorted({*canon.get("candidates", []), tr})
        db.update_terminology(row["id"], canonical_translation=canon,
                               concept=concept, status="ignored")
        return False
    if row["status"] != "confirmed" and f.get("enforce") is False:
        canon = {**json.loads(row["canonical_translation"]), target: tr}
        if tr and not _is_bad_translation(term, tr, target, concept, source_lang):
            # Dropped but well-formed ("Jia She" for 賈赦): the book may still
            # converge on it — record as a vote candidate for reconcile.
            canon["candidates"] = sorted({*canon.get("candidates", []), tr})
        db.update_terminology(row["id"], canonical_translation=canon,
                              concept=concept, status="ignored")  # helper's own verdict: not enforcement-grade
        return False
    if row["status"] != "confirmed" and _is_bad_translation(
            term, tr, target, concept, source_lang):
        # …unless the MT probe redeems the helper: a phonetic misread by the
        # helper (Da Yu) with a correct probe habit (Daiyu) still yields a vote
        # candidate instead of nothing — checked below, so only non-name and
        # non-pinyin junk returns here.
        if not (translator is not None and target == "en" and concept in _NAME_TYPES
                and _is_pinyin_shaped(tr)):
            return False
    title = ""
    if (translator is not None and row["status"] != "confirmed" and target == "en"
            and concept in _NAME_TYPES):
        # E17 agreement gate (replaces the deleted word lists): a second model
        # must converge on the same rendering before it enforces. Helper-only
        # junk (Brother, Xie Yimai, Da Yu) disagrees with the translator's own
        # habit and becomes a vote candidate; the book election decides.
        # Probe outage fails open (helper's verdict stands); only explicit
        # disagreement demotes. Descriptive agreements (Older Brother) still
        # enforce immediately.
        probe = _probe_transliteration(translator, str(f["term"]).strip(), source_lang, target)
        if probe and probe != _norm_pinyin(tr):
            title = _probe_title(translator, str(f["term"]).strip(), source_lang, target)
            _store_vote(db, row, target, concept, {tr, title})
            return False
    if row["status"] != "confirmed" and _is_bad_translation(
            term, tr, target, concept, source_lang):
        if title or (translator is not None and target == "en" and concept in _NAME_TYPES):
            # Gate-rejected names still vote: the book election (not the gate)
            # picks the surface the outputs converge on; losers normalize.
            if not title and translator is not None:
                title = _probe_title(translator, str(f["term"]).strip(), source_lang, target)
            _store_vote(db, row, target, concept, {tr, title})
        return False
    canon = json.loads(row["canonical_translation"])
    canon[target] = tr
    db.update_terminology(row["id"], canonical_translation=canon, concept=concept)
    return True


def _store_vote(db, row, target: str, concept: str, surfaces: set[str]) -> None:
    """Record vote candidates AND normalize-losers: whichever surface the book
    elects, the others rewrite to it in shipped outputs."""
    canon = json.loads(row["canonical_translation"])
    cands = sorted({c for c in {canon.get(target, ""), *surfaces} if c})
    canon["candidates"] = cands
    canon["losers"] = sorted(set(canon.get("losers", [])) | set(cands))
    db.update_terminology(row["id"], canonical_translation=canon, concept=concept, status="ignored")


def suggest_term_translations(db, project_id: int, provider, *, batch: int = 12, limit: int = 60,
                              only_forms: set[str] | None = None,
                              retry_present: set[str] | None = None, retry_limit: int = 20,
                              verbose: bool = False, translator=None) -> int:
    """One LLM call per `batch` untranslated candidates: keep real terms, classify, translate.
    Candidates the model drops, or renders as a lower-case common word, become `ignored` (only when the batch
    produced a parsable answer). `retry_present`: forms occurring in the text about to be translated get one
    solo second chance each (up to `retry_limit`, most frequent first) — batch competition drops real names
    (Red Chamber: 賈政/王夫人 fell out of 12-term batches but classify fine alone), and excerpt recall is
    exactly where a missing rendering hurts. Terms at/above 100 occurrences get a second solo roll: one
    sample of a 0.0-temperature 4B helper still varies run to run. `translator`: the MT model cross-checks
    pinyin-shaped name renderings against its own transliteration habit (see `_translator_agrees`)."""
    project = db.get_project(project_id)
    target = project["target_lang"]
    todo = [t for t in db.terminology_for_project(project_id)
            if t["status"] == "suggested" and target not in json.loads(t["canonical_translation"])
            and (only_forms is None or only_forms & set(json.loads(t["source_forms"])))]
    # Single characters (的/了/是…) outnumber real names 10:1 by occurrences but
    # are function words, never glossary-grade: multi-char terms spend the slow
    # helper budget first, single chars only with budget to spare.
    todo.sort(key=lambda t: (len(json.loads(t["source_forms"])[0]) < 2, -t["occurrences"]))
    todo = todo[:limit]
    done = 0
    for i in range(0, len(todo), batch):
        rows = todo[i:i + batch]
        by_term = {json.loads(r["source_forms"])[0]: r for r in rows}
        try:
            found = provider.suggest_terms([(t, _example(db, project_id, t)) for t in by_term], project["source_lang"], target)
        except (ProviderError, RefusalError):  # ponytail: helper is best-effort enrichment — a missing/down
            return done  # helper must never abort the translation run (upload-and-go)
        if not found:
            for row in rows:
                db.update_terminology(row["id"], status="ignored")  # batch dropped all: considered, not pending
            continue
        seen = set()
        for f in found:
            row = by_term.get(str(f["term"]).strip())
            if not row:
                continue
            if _apply_suggestion(db, project_id, row, f, target, project["source_lang"], translator):
                seen.add(row["id"])
                done += 1
        for row in rows:
            if row["id"] not in seen:
                db.update_terminology(row["id"], status="ignored")
    if retry_present:
        retry = [t for t in db.terminology_for_project(project_id)
                 if t["status"] == "ignored" and target not in json.loads(t["canonical_translation"])
                 and retry_present & set(json.loads(t["source_forms"]))]
        # DH22: the 12 solo slots went to diffuse words (那里/去了: high count,
        # present in most passages) while bursty names (王夫人/老太太) never got
        # classified. Same dispersion statistic as mining, scoped to the
        # translation window: slots go by burstiness (count x log(N/df)), not
        # raw occurrences — a name recurring in few passages outranks filler
        # spread thinly. No lists, no thresholds that never fire (DH24: nothing
        # exceeds df 0.8 in a 57-passage window, so a cutoff rule is dead).
        _forms = set()
        for t in retry:
            _forms.update(json.loads(t["source_forms"]))
        _texts = [r["source_text"] or "" for r in
                  db.conn.execute("SELECT source_text FROM segments WHERE project_id=?", (project_id,)).fetchall()]
        _texts = [x for x in _texts if any(f in x for f in _forms)] or _texts
        _n = max(1, len(_texts))
        import math as _math
        _df: dict[int, float] = {}
        for t in retry:
            form = json.loads(t["source_forms"])[0]
            _df[t["id"]] = sum(1 for x in _texts if form in x) / _n
        retry.sort(key=lambda t: (-t["occurrences"] * _math.log(_n / max(1, _df[t["id"]] * _n)),
                                  -t["occurrences"]))
        for row in retry[:retry_limit]:
            form = json.loads(row["source_forms"])[0]
            tries = 2 if row["occurrences"] >= 100 else 1  # frequent names get a second roll of the helper dice
            rescued, why = False, "still dropped"
            for _ in range(tries):
                try:
                    found = provider.suggest_terms([(form, _example(db, project_id, form))], project["source_lang"], target)
                except (ProviderError, RefusalError) as e:
                    why = type(e).__name__
                    break  # one bad solo must not cancel the rest
                for f in found or []:
                    if str(f["term"]).strip() == form and _apply_suggestion(
                            db, project_id, row, f, target, project["source_lang"], translator):
                        db.update_terminology(row["id"], status="suggested")  # rescued: back in play
                        done += 1
                        rescued = True
                        break
                if rescued:
                    break
            if verbose:
                print(f"  retry {form}: {'RESCUED' if rescued else why}", flush=True)
    if translator is not None:
        _seed_probe_candidates(db, project_id, translator, target, project["source_lang"], verbose)
    _merge_aliases(db, project_id, target)
    return done


def _seed_probe_candidates(db, project_id: int, translator, target: str, source_lang: str,
                            verbose: bool = False, min_occ: int = 20, cap: int = 20) -> int:
    """E16: the helper drops some frequent names on every attempt (no verdict,
    no candidates — the election has nothing to elect). Ask the translation
    model itself how it renders the bare term and seed that as a vote
    candidate: the book election then confirms or rejects it from shipped
    outputs. Only transliteration-shaped probes seed (self-selecting for
    names); the phonetic authority filters probe hallucinations
    (Ujimura/Jiasha never seed). Ignored rows only — never overrides the
    helper or the user. No model calls when translator is None."""
    seeded = 0
    ignored = [t for t in db.terminology_for_project(project_id)
               if t["status"] == "ignored" and target not in json.loads(t["canonical_translation"])
               and not json.loads(t["canonical_translation"]).get("candidates")]
    ignored.sort(key=lambda t: -t["occurrences"])
    for row in ignored:
        if seeded >= cap:
            break
        if row["occurrences"] < min_occ:
            break  # sorted: the rest are rarer
        form = json.loads(row["source_forms"])[0]
        if len(form) < 2 or _is_mining_junk(form):
            continue
        probe = _probe_transliteration(translator, form, source_lang, target)
        if not probe:
            continue
        title = _probe_title(translator, form, source_lang, target)
        if not title:
            continue
        shaped = _is_pinyin_shaped(title) if source_lang == "zh" else _is_romaji_shaped(title)
        if not shaped:
            continue
        if _is_bad_translation(form, title, target, "character", source_lang):
            if verbose:
                print(f"  seed {form}: probe {title} fails phonetic gate", flush=True)
            continue
        canon = {**json.loads(row["canonical_translation"]), "candidates": [title]}
        db.update_terminology(row["id"], canonical_translation=canon)
        seeded += 1
        if verbose:
            print(f"  seed {form}: probe candidate {title}", flush=True)
    return 0  # seeds are election input, not enforcement: never inflate the translated count


def _merge_aliases(db, project_id: int, target: str) -> None:
    """Forms with the identical rendering are one term (黛玉 + 林黛玉 -> 'Dai Yu'):
    fold their forms and counts into the first entry, ignore the rest. A name
    enforced under two glossary keys can otherwise fight itself across chapters."""
    by_render: dict[str, dict] = {}
    for t in db.terminology_for_project(project_id):
        if t["status"] == "ignored":
            continue
        tr = json.loads(t["canonical_translation"]).get(target)
        if not tr:
            continue
        first = by_render.setdefault(tr, t)
        if first["id"] == t["id"]:
            continue
        forms = json.loads(first["source_forms"]) + [f for f in json.loads(t["source_forms"])
                                                     if f not in json.loads(first["source_forms"])]
        db.update_terminology(first["id"], occurrences=max(first["occurrences"], t["occurrences"]))
        db.conn.execute("UPDATE terminology SET source_forms=?, occurrences=? WHERE id=?",
                        (json.dumps(forms, ensure_ascii=False), max(first["occurrences"], t["occurrences"]), first["id"]))
        db.update_terminology(t["id"], status="ignored")
    db.conn.commit()


def demote_unused_terms(db, project_id: int, min_occurrences: int = 3) -> int:
    """The model votes with its output: a suggested term present in N translated
    segments but applied in none of them is either a wrong mapping or an
    unenforceable one — enforcing it only poisons prompts. Demote to ignored.
    Fully generic (no word lists): the harness corrects itself from its own QA
    signal on any book, any language. User-confirmed terms are never touched."""
    target = db.get_project(project_id)["target_lang"]
    demoted = 0
    for t in db.terminology_for_project(project_id):
        if t["status"] != "suggested":
            continue
        tr = json.loads(t["canonical_translation"]).get(target)
        if not tr:
            continue
        cond = " OR ".join(["instr(source_text, ?) > 0"] * len(json.loads(t["source_forms"])))
        forms = json.loads(t["source_forms"])
        row = db.conn.execute(
            f"SELECT COUNT(*) n, SUM(instr(translated_text, ?) > 0) hit FROM segments "
            f"WHERE project_id=? AND translated_text IS NOT NULL AND ({cond})",
            (tr, project_id, *forms)).fetchone()
        if (row["n"] or 0) >= min_occurrences and not row["hit"]:
            db.update_terminology(t["id"], status="ignored")
            demoted += 1
    return demoted


def set_term(db, project_id: int, term_id: int, translation: str | None = None, status: str | None = None,
             concept: str | None = None) -> None:
    """Accept / edit / ignore a term (user action). Editing a translation marks affected segments STALE."""
    row = next((t for t in db.terminology_for_project(project_id) if t["id"] == term_id), None)
    if row is None:
        raise KeyError(f"term {term_id}")
    target = db.get_project(project_id)["target_lang"]
    fields: dict = {}
    if translation is not None:
        canon = json.loads(row["canonical_translation"])
        changed = canon.get(target) != translation
        canon[target] = translation
        fields["canonical_translation"] = canon
        fields["status"] = "confirmed"
    if status:
        fields["status"] = status
    if concept is not None:
        fields["concept"] = concept
    db.update_terminology(term_id, **fields)
    if translation is not None and changed:
        from .models import SegmentState

        for form in json.loads(row["source_forms"]):
            for s in db.conn.execute("SELECT id FROM segments WHERE project_id=? AND instr(source_text, ?)>0 AND translated_text IS NOT NULL "
                                     "AND state NOT IN ('HUMAN_APPROVED','STALE')", (project_id, form)).fetchall():
                db.update_segment(s["id"], state=SegmentState.STALE)


_BIBLE_SECTION = {"character": "characters", "location": "locations", "organization": "organizations", "item": "items",
                  "technique": "abilities", "realm": "abilities"}


def sync_bible(db, project_id: int, project_dir: str) -> None:
    """Mirror the terminology table into Story Bible JSON. Entries without origin=auto are user-owned: untouched."""
    target = db.get_project(project_id)["target_lang"]
    sections: dict[str, list[dict]] = {k: [] for k in ("terminology", *set(_BIBLE_SECTION.values()))}
    for t in db.terminology_for_project(project_id):
        if t["status"] == "ignored":
            continue
        forms = json.loads(t["source_forms"])
        canon = json.loads(t["canonical_translation"])
        entry = {"origin": "auto", "source_forms": forms, "canonical_translation": canon, "concept": t["concept"],
                 "policy": t["policy"], "status": t["status"], "occurrences": t["occurrences"]}
        sections["terminology"].append(entry)
        if target in canon and t["concept"] in _BIBLE_SECTION:
            sections[_BIBLE_SECTION[t["concept"]]].append(
                {"origin": "auto", "source": forms[0], "translation": canon[target], "type": t["concept"],
                 "status": "confirmed" if t["status"] == "confirmed" else "inferred", "occurrences": t["occurrences"]})
    def key(e: dict) -> str:
        return e.get("source") or (e.get("source_forms") or [""])[0]

    for name, entries in sections.items():
        old = load_section(project_dir, name)
        old = [e for e in old if isinstance(e, dict)] if isinstance(old, list) else []
        prev = {key(e): e for e in old}
        merged = [{**{k: v for k, v in prev.get(key(e), {}).items() if k not in e}, **e} for e in entries]  # keep user notes
        save_section(project_dir, name, merged + [e for e in old if e.get("origin") != "auto"])


def detect_contradictions(db, project_id: int, project_dir: str) -> list[dict]:
    """Same source form with different canonical translations (spec §25). Rewrites the section: no duplicates."""
    from collections import defaultdict

    seen: dict[str, set[str]] = defaultdict(set)
    for row in db.terminology_for_project(project_id):
        if row["status"] == "ignored":
            continue
        canon = json.loads(row["canonical_translation"])
        target = next(iter(canon.values()), "")
        if target:
            for f in json.loads(row["source_forms"]):
                seen[f].add(target)
    conflicts = [{"type": "terminology_drift", "source_form": form, "translations": sorted(ts), "severity": "warning"}
                 for form, ts in seen.items() if len(ts) > 1]
    save_section(project_dir, "contradictions", conflicts)
    return conflicts
