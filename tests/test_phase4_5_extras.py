from lorebridge.escalation import escalation_needed
from lorebridge.failure_corpus import add_case, load_cases


def test_escalation_signals():
    assert escalation_needed("abc", "xyz", ["untranslated_source"], 0)[0]
    assert escalation_needed("x" * 600, "ok", [], 0)[0]
    assert not escalation_needed("正常句子。", "A normal sentence.", [], 0)[0]


def test_failure_corpus_roundtrip(tmp_path):
    p = tmp_path / "corpus.jsonl"
    add_case(p, {"source": "s", "bad_mtl": "b", "better": "g", "errors": ["wrong_word_sense"]})
    cases = load_cases(p)
    assert len(cases) == 1 and cases[0]["better"] == "g"
