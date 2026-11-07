"""Extra deterministic QA checks + roundtrip router validation (fixtures only)."""
from babelink.estimate import ROUNDTRIP_THRESHOLD, auc_score, entity_recall, fixture_auc, roundtrip_chrf, roundtrip_fixture
from babelink.escalation import escalation_needed
from babelink.qa import DETERMINISTIC, check_register_drift, check_segment, register_features


def kinds(findings):
    return {(sev, typ) for sev, typ, _ in findings}


def types(findings):
    return [t for _, t, _ in findings]


# ---- term_variant_leak ---------------------------------------------------
def test_term_variant_leak_flags_loser_verbatim():
    fs = check_segment("雨村来了。", "Yu Jun arrived.", "zh",
                       {"雨村": "Yucun"}, term_losers={"雨村": ["Yu Jun", "Yu Village"]})
    assert ("warning", "term_variant_leak") in kinds(fs)


def test_term_variant_leak_case_insensitive_and_bounded():
    assert "term_variant_leak" in types(check_segment("雨村来了。", "yu jun arrived.", "zh",
                                                    {"雨村": "Yucun"}, term_losers={"雨村": ["Yu Jun"]}))
    # Embedded inside a longer word: not a verbatim leak (word-boundary).
    assert "term_variant_leak" not in types(check_segment("雨村走了。", "Yucun walked home.", "zh",
                                                        {"雨村": "Yucun"}, term_losers={"雨村": ["Yu Jun"]}))


def test_term_variant_leak_ignores_winner_and_absent_term():
    assert "term_variant_leak" not in types(check_segment("雨村来了。", "Yucun arrived.", "zh",
                                                        {"雨村": "Yucun"}, term_losers={"雨村": ["Yu Jun"]}))
    assert "term_variant_leak" not in types(check_segment("林凡来了。", "Yu Jun arrived.", "zh",
                                                        {"雨村": "Yucun"}, term_losers={"雨村": ["Yu Jun"]}))
    assert "term_variant_leak" not in types(check_segment("雨村来了。", "Yucun arrived.", "zh",
                                                        {"雨村": "Yucun"}, term_losers={"雨村": []}))
    assert "term_variant_leak" not in types(check_segment("雨村来了。", "Yucun arrived.", "zh",
                                                        {"雨村": "Yucun"}))


# ---- antecedent_check ----------------------------------------------------
def test_antecedent_check_flags_lost_antecedent():
    assert "antecedent_check" in types(check_segment("林凡来了。", "He came.", "zh"))
    assert "antecedent_check" in types(check_segment("林凡来了。", "This is strange.", "zh"))
    assert "antecedent_check" in types(check_segment("林凡来了。", '"That evening was quiet."', "zh"))
    assert "antecedent_check" in types(check_segment("Lin Fan came.", "They left.", "en"))


def test_antecedent_check_passes_with_source_pronoun():
    assert "antecedent_check" not in types(check_segment("他来了。", "He came.", "zh"))
    assert "antecedent_check" not in types(check_segment("这本书很好。", "This book is good.", "zh"))
    assert "antecedent_check" not in types(check_segment("He came.", "He came.", "en"))
    assert "antecedent_check" not in types(check_segment("彼が来た。", "He came.", "ja"))


def test_antecedent_check_ignores_non_demonstrative_first():
    assert "antecedent_check" not in types(check_segment("林凡来了。", "Lin Fan came.", "zh"))
    assert "antecedent_check" not in types(check_segment("去了。", "went.", "zh"))
    assert "antecedent_check" not in types(check_segment("林凡来了。", "The book is good.", "zh"))


# ---- voice_register_drift -------------------------------------------------
BASELINE = [
    "Lin Fan walked into the Azure Cloud Sect and bowed to the Senior Brother.",
    "The mountain air was cold and the road ahead was long and quiet.",
    "He waited three days at the gate before anyone came to answer.",
    "Daiyu entered the garden and saw the flowers open in the spring wind.",
    "The old man nodded slowly and then turned back to his work.",
    "She carried the letter across the courtyard and gave it to Wang Furen.",
    "In the evening the rain stopped and the sky cleared over the lake.",
    "They spoke of the journey home and of the friends they would meet.",
]


def test_register_features_mechanical_only():
    f = register_features("Run! Go! Now!")
    assert set(f) == {"avg_len", "excl", "punct"}
    assert f["excl"] > 10 and f["avg_len"] < 10
    g = register_features("The wind moved through the pines and the bells rang in the distance.")
    assert 50 < g["avg_len"] < 90 and g["excl"] == 0.0


def test_register_drift_flags_outliers_not_normals():
    assert check_register_drift("The wind moved through the pines and the bells rang in the distance.", BASELINE) is None
    for outlier in ("Run! Go! Now!",
                    "Lin Fan walked into the Azure Cloud Sect and bowed to the Senior Brother while the "
                    "mountain air was cold and the road ahead was long and quiet and he waited three days "
                    "at the gate before anyone came to answer at all"):
        found = check_register_drift(outlier, BASELINE)
        assert found is not None and found[1] == "voice_register_drift"
        assert found[0] == "warning"


def test_register_drift_needs_enough_baseline_and_baseline_arg():
    assert check_register_drift("Run! Go! Now!", BASELINE[:3]) is None
    assert check_register_drift("Run! Go! Now!", None) is None
    assert check_register_drift("Run! Go! Now!", []) is None
    assert "voice_register_drift" in types(check_segment("林凡走了。", "Run! Go! Now!", "zh", register_baseline=BASELINE))
    assert "voice_register_drift" not in types(check_segment("林凡走了。", "Run! Go! Now!", "zh"))


def test_deterministic_covers_new_types():
    for t in ("term_variant_leak", "voice_register_drift", "antecedent_check"):
        assert t in DETERMINISTIC


# ---- estimate: fixture AUC + escalation wiring ---------------------------
def test_roundtrip_fixture_auc_and_threshold():
    rows = roundtrip_fixture()
    assert any(g for _, _, g in rows) and any(not g for _, _, g in rows)
    auc = fixture_auc()
    # Validated signal: separates good vs catastrophic on the mock fixture.
    assert auc > 0.8, auc
    scores = [(roundtrip_chrf(s, b), g) for s, b, g in rows]
    goods = [s for s, g in scores if g]
    bads = [s for s, g in scores if not g]
    assert auc_score(goods, bads) == auc
    # Threshold calibrated on the same fixture: catches most corrupted, spares goods.
    caught = sum(1 for b in bads if b < ROUNDTRIP_THRESHOLD)
    false = sum(1 for g in goods if g < ROUNDTRIP_THRESHOLD)
    assert caught / len(bads) >= 0.8, (caught, bads)
    assert false / len(goods) <= 0.1, (false, goods)


def test_auc_score_units():
    assert auc_score([10, 9], [1, 2]) == 1.0
    assert auc_score([1, 2], [10, 9]) == 0.0
    assert auc_score([5], [5]) == 0.5
    assert auc_score([], [1]) == 0.0


def test_roundtrip_existing_signals_intact():
    src = "黛玉去了，贾政问话。"
    assert roundtrip_chrf(src, "") == 0.0
    assert roundtrip_chrf(src, src) == 100.0
    assert entity_recall(src, "女孩去了，有人问话。", {"黛玉": "Daiyu", "贾政": "Jia Zheng"})[0] == 0.0


def test_escalation_routes_new_signals():
    assert escalation_needed("林凡走进青云宗，花了三天。", "Yucun arrived.", ["term_variant_leak"], 0)[0]
    assert escalation_needed("林凡走进青云宗，花了三天。", "He came.", ["antecedent_check"], 0)[0]
    assert escalation_needed("林凡走进青云宗，花了三天。", "Run! Go! Now!", ["voice_register_drift"], 0)[0]
    low = "Lin Fan traveled to the Azure Cloud Sect and waited three days at the gate."
    assert escalation_needed(low, "Lin Fan traveled", [], 0, roundtrip_chrf=10.0)[0]
    assert not escalation_needed(low, "Lin Fan traveled to the Azure Cloud Sect and waited three days at the gate.", [], 0, roundtrip_chrf=90.0)[0]
    assert not escalation_needed(low, "Lin Fan traveled to the Azure Cloud Sect and waited three days at the gate.", [], 0)[0]
    # Short sources are exempt: chrF is too noisy there.
    assert not escalation_needed("林凡走进青云宗去。", "Lin Fan went to the sect.", [], 0, roundtrip_chrf=0.0)[0]


def test_catch_rates_on_corrupted_fixture():
    """Each new check catches its corruption class (fixtures, not models)."""
    leak_hits = [
        check_segment("雨村来了。", "Yu Jun arrived.", "zh", {"雨村": "Yucun"}, term_losers={"雨村": ["Yu Jun"]}),
        check_segment("雨村走了。", "Yu Village left.", "zh", {"雨村": "Yucun"}, term_losers={"雨村": ["Yu Village"]}),
    ]
    leak_rate = sum("term_variant_leak" in types(f) for f in leak_hits) / len(leak_hits)

    ante_hits = [
        check_segment("林凡来了。", "He came.", "zh"),
        check_segment("林凡来了。", "This is strange.", "zh"),
        check_segment("黛玉来了。", "She smiled.", "zh"),
    ]
    ante_rate = sum("antecedent_check" in types(f) for f in ante_hits) / len(ante_hits)

    drift_hits = [
        check_segment("林凡走了。", "Run! Go! Now!", "zh", register_baseline=BASELINE),
        check_segment("林凡走了。", "Lin Fan walked " * 12, "zh", register_baseline=BASELINE),
    ]
    drift_rate = sum("voice_register_drift" in types(f) for f in drift_hits) / len(drift_hits)

    good_leak = check_segment("雨村来了。", "Yucun arrived.", "zh", {"雨村": "Yucun"}, term_losers={"雨村": ["Yu Jun"]})
    good_ante = check_segment("他来了。", "He came.", "zh")
    good_drift = check_segment("林凡走了。", "The wind moved through the pines and the bells rang in the distance.", "zh", register_baseline=BASELINE)
    assert leak_rate == 1.0 and ante_rate == 1.0 and drift_rate == 1.0
    assert "term_variant_leak" not in types(good_leak)
    assert "antecedent_check" not in types(good_ante)
    assert "voice_register_drift" not in types(good_drift)


# ---- run_qa integration (DB) ---------------------------------------------
def test_run_qa_flags_leaked_loser(tmp_path):
    import json

    from babelink.db import Database
    from babelink.importers.base import store_chapters
    from babelink.models import TerminologyEntry
    from babelink.qa import run_qa
    from babelink.segment import ParsedChapter, to_html

    db = Database(tmp_path / "q.db")
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "d", "eval", "d")
    paras = ["雨村来了。"]
    store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    seg = db.segments_for_project(pid)[0]
    db.update_segment(seg["id"], translated="Yu Jun arrived.")
    db.add_terminology(TerminologyEntry(None, pid, ["雨村"], {"en": "Yucun", "losers": ["Yu Jun"]},
                                       "character", "preferred", "confirmed", 3))
    run_qa(db, pid, {"雨村": "Yucun"})
    assert "term_variant_leak" in [q["type"] for q in db.qa_for_project(pid)]
