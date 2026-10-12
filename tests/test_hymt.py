"""Hy-MT2 + CAT integration: prompt formats, provider selection, helper routing (all offline)."""
from lorebridge import service
from lorebridge.db import Database
from lorebridge.hymt import ASSISTANT, BOS, DEFAULT_MODEL, EOS, USER, full_name, is_mt_model, mt_prompt, unwrap_output, wrap_prompt
from lorebridge.importers.base import store_chapters
from lorebridge.pipeline import analyze_project, translate_project
from lorebridge.providers.base import MockProvider
from lorebridge.providers.cat_ollama import CatOllamaProvider, DEFAULT_MODEL as CAT_MODEL, cat_prompt
from lorebridge.providers.hymt_ollama import HymtOllamaProvider
from lorebridge.providers.ollama import OllamaProvider
from lorebridge.registry import recommend
from lorebridge.segment import ParsedChapter, to_html


def test_mt_prompt_official_format():
    system, user = mt_prompt("林凡走了。", glossary={"林凡": "Lin Fan", "青云宗": "Azure Cloud Sect"},
                             source_lang="zh", target_lang="en")
    assert system == ""  # Hy-MT2 has no system prompt
    assert "Lin Fan" in user and "林凡 translates to Lin Fan" in user
    assert "青云宗" not in user  # only terms present in the source
    assert "English" in user and "only output the translated result" in user


def test_mt_prompt_background_and_style():
    _, user = mt_prompt("你好。", background="Xianxia novel.", style="literary",
                        source_lang="zh", target_lang="en")
    assert "[Background Information]" in user and "Xianxia novel." in user
    _, plain = mt_prompt("你好。", source_lang="zh", target_lang="en")
    assert "[Background Information]" not in plain


def test_wrap_prompt_matches_official_template():
    # chat_template.jinja: BOS (+ system + placeholder3)? + USER + content + ASSISTANT
    wrapped = wrap_prompt("hello")
    assert wrapped == f"{BOS}{USER}hello{ASSISTANT}"
    assert wrap_prompt("x", "sys").startswith(BOS + "sys")
    out = unwrap_output(f"<think>hmm</think>Hello{EOS}")
    assert out == "Hello"


def test_full_names_and_detection():
    assert full_name("zh") == "Chinese" and full_name("ja") == "Japanese"
    assert is_mt_model(DEFAULT_MODEL) and is_mt_model("tencent/Hy-MT2-7B-GGUF:Q4_K_M")
    assert not is_mt_model("qwen2.5:7b")


def test_make_provider_selects_hymt():
    p = service.make_provider("hymt", DEFAULT_MODEL)
    assert isinstance(p, HymtOllamaProvider) and service.is_mt_only(p)
    auto = service.make_provider("ollama", DEFAULT_MODEL)
    assert isinstance(auto, HymtOllamaProvider)
    general = service.make_provider("ollama", "qwen2.5:7b")
    assert type(general) is OllamaProvider and not service.is_mt_only(general)


def test_cat_prompt_official_format():
    u = cat_prompt("猫は可愛い。", source_lang="ja", target_lang="en")
    assert u.startswith("Translate the following Japanese text into English.") and "猫は可愛い。" in u
    g = cat_prompt("猫は可愛い。", source_lang="ja", target_lang="en", glossary={"猫": "cat"})
    assert "猫 must be translated as cat" in g
    assert "Japanese" in cat_prompt("x", source_lang="ja", target_lang="en")
    assert "Chinese" in cat_prompt("x", source_lang="zh", target_lang="en")


def test_make_provider_selects_cat():
    p = service.make_provider("cat", CAT_MODEL)
    assert isinstance(p, CatOllamaProvider) and service.is_mt_only(p)
    assert p.name == f"cat:{CAT_MODEL}"
    helper = service.helper_for(p, "cat", "", "some-helper:4b")
    assert type(helper) is OllamaProvider and helper.model == "some-helper:4b"


def test_clean_output_strips_stray_eos():
    from lorebridge.prompts import clean_output
    assert clean_output("He smiled quietly.</s>") == "He smiled quietly."
    assert clean_output("[Translation]  \nHe smiled quietly.") == "He smiled quietly."
    assert clean_output("Hello") == "Hello"


def test_helper_for_routes_mt_tasks_to_general_llm():
    mt = service.make_provider("hymt", DEFAULT_MODEL)
    helper = service.helper_for(mt, "hymt", "", "some-helper:4b")
    assert type(helper) is OllamaProvider and helper.model == "some-helper:4b"
    assert helper.num_ctx == 4096  # 8k KV OOMs a 4B helper on a 16GB CPU-only box
    general = service.make_provider("ollama", "qwen2.5:7b")
    assert service.helper_for(general, "ollama", "") is general
    assert type(service.helper_for(mt, "mock", "")) is MockProvider


def test_registry_prefers_mt_for_translation():
    recs = recommend("en", 0, 16)
    assert recs and recs[0].provider == "hymt" and "translation" in recs[0].recommended_tasks


class Counting(MockProvider):
    def __init__(self):
        super().__init__()
        self.suggest_calls = 0
        self.summarize_calls = 0

    def suggest_terms(self, candidates, source_lang="auto", target_lang="en"):
        self.suggest_calls += 1
        return []  # parsable but empty: candidates become ignored

    def summarize(self, text, target_lang="en", max_tokens=250):
        self.summarize_calls += 1
        return "summary"


def _project_with_terms(db, pid="p"):
    pid = db.create_project("t", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "bench", "eval", "bench")
    paras = ["林凡走进了青云宗。", "林凡看到了大师兄。", "林凡点了点头。"]
    cids = store_chapters(db, pid, doc, [ParsedChapter("ch1", to_html(paras), paras)])
    return pid, cids[0]


def test_analyze_uses_term_provider(tmp_path):
    db = Database(tmp_path / "t.db")
    pid, _ = _project_with_terms(db, pid="p")
    main, terms = Counting(), Counting()
    analyze_project(db, pid, provider=main, term_provider=terms)
    assert terms.suggest_calls >= 1 and main.suggest_calls == 0
    db.close()


def test_translate_uses_summarize_provider(tmp_path):
    db = Database(tmp_path / "t.db")
    pid, cid = _project_with_terms(db, pid="p")
    main, summ = Counting(), Counting()
    n = translate_project(db, pid, main, chapter_id=cid, summarize=True, summarize_provider=summ)
    assert n == 3 and summ.summarize_calls >= 1 and main.summarize_calls == 0
    db.close()


def test_translate_survives_helper_outage(tmp_path):
    """Upload-and-go with no helper installed: term suggestion fails, the
    translation run still delivers every paragraph (empty glossary)."""
    from lorebridge.prompts import ProviderError
    from lorebridge.terminology import upsert_candidates

    class DownHelper(MockProvider):
        def suggest_terms(self, *a, **k):
            raise ProviderError("helper down")

    db = Database(tmp_path / "t.db")
    pid, cid = _project_with_terms(db, pid="p")
    upsert_candidates(db, pid, [("林凡", 3)])
    n = translate_project(db, pid, MockProvider(), chapter_id=cid, summarize_provider=DownHelper())
    assert n == 3
    assert all(s["translated_text"] for s in db.segments_for_chapter(cid))
    db.close()
