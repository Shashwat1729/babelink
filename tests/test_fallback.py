"""Failover chains: order, refusal-terminal, provenance. No network, no models."""
from babelink.fallback import ChainProvider, resolve_chains
from babelink.prompts import ProviderError, RefusalError
from babelink.providers.base import MockProvider


class Down(MockProvider):
    def translate(self, source, **kw):
        raise ProviderError("down")


class Refuses(MockProvider):
    def translate(self, source, **kw):
        raise RefusalError("no")


class MTStub(MockProvider):
    translation_only = True
    name = "hymt:stub"


def test_chain_serves_primary_first():
    c = ChainProvider([MockProvider(), MockProvider()])
    assert c.translate("hello") == "hello"
    assert c.served_by == "mock" and c.usage["failovers"] == 0


def test_chain_fails_over_on_provider_error():
    c = ChainProvider([Down(), MockProvider()])
    assert c.translate("hello") == "hello"
    assert c.usage["failovers"] == 1 and c.usage["calls"] == 1


def test_chain_refusal_is_terminal():
    c = ChainProvider([Refuses(), MockProvider()])
    try:
        c.translate("hello")
    except RefusalError:
        pass
    else:
        raise AssertionError("refusal must propagate, not fail over")
    assert c.usage["failovers"] == 0  # no fallback consumed


def test_chain_all_down_raises_provider_error():
    c = ChainProvider([Down(), Down()])
    try:
        c.translate("hello")
    except ProviderError as e:
        assert "all 2 members failed" in str(e)
    else:
        raise AssertionError("expected ProviderError")


def test_chain_needs_members():
    try:
        ChainProvider([])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_chain_translation_only_requires_all_members():
    assert ChainProvider([MTStub(), MTStub()]).translation_only is True
    assert ChainProvider([MTStub(), MockProvider()]).translation_only is False


def test_chain_describe_provenance():
    c = ChainProvider([Down(), MockProvider()], name="translate[x]")
    c.translate("hi")
    d = c.describe()
    assert d == {"chain": "translate[x]", "members": ["mock", "mock"],
                 "served_by": "mock", "failovers": 1, "calls": 1}


def test_resolve_chains_mt_gets_helper_fallback():
    def make(kind, model="", base_url=""):
        return MTStub() if kind == "hymt" else MockProvider()

    import babelink.service as service
    orig = service.helper_for
    service.helper_for = lambda provider, kind="", base_url="", helper_model="": MockProvider()
    try:
        translator, helper = resolve_chains("hymt", make=make)
    finally:
        service.helper_for = orig
    assert isinstance(translator, ChainProvider)
    assert [m.name for m in translator.members] == ["hymt:stub", "mock"]


def test_resolve_chains_general_engine_stands_alone():
    translator, _ = resolve_chains("ollama", model="qwen",
                                   make=lambda kind, model="", base_url="": MockProvider())
    assert not isinstance(translator, ChainProvider)


def test_auto_picks_engine_per_direction():
    """'auto' resolves to CAT for ja->en (bake-off E04) and Hy-MT2 otherwise;
    an explicit kind never consults direction."""
    from babelink import service

    assert service.default_engine("ja", "en")[0] == "cat"
    assert service.default_engine("zh", "en")[0] == "hymt"
    assert service.default_engine("ja", "zh")[0] == "hymt"
    seen = []

    def make(kind, model="", base_url=""):
        seen.append((kind, model))
        return MockProvider()

    resolve_chains("auto", make=make, source_lang="ja", target_lang="en")
    assert seen[0][0] == "cat", seen
    resolve_chains("auto", make=make, source_lang="zh", target_lang="en")
    assert seen[1][0] == "hymt", seen


def test_chain_translate_project_fails_over_mid_run(tmp_path):
    """A dead primary no longer aborts the book: per-segment failover inside
    the chain delivers every paragraph from the fallback engine."""
    from babelink.db import Database
    from babelink.importers.base import store_chapters
    from babelink.pipeline import translate_project
    from babelink.segment import ParsedChapter, to_html

    db = Database(tmp_path / "c.db")
    pid = db.create_project("c", source_lang="zh", target_lang="en")
    doc = db.add_document(pid, "x", "eval", "x")
    paras = ["林凡來了。", "林凡去了。", "林凡又來了。"]
    cids = store_chapters(db, pid, doc, [ParsedChapter("c", to_html(paras), paras)])
    chain = ChainProvider([Down(), MockProvider()])
    assert translate_project(db, pid, chain, chapter_id=cids[0]) == 3
    # 3 segments x (first draw + CJK-leftover guided retry) x failover each:
    # the mock echoes source CJK, so every draw retries, all via fallback.
    assert chain.usage["failovers"] == 6
    assert chain.describe()["served_by"] == "mock"


def test_maybe_chain_policy():
    """The web job path and resolve_chains share one failover policy: only a
    translation-only engine with a differently-named helper gets wrapped."""
    from babelink.fallback import ChainProvider, maybe_chain

    class MTOnly(MockProvider):
        name = "hymt:x"
        translation_only = True

    engine, helper = MTOnly(), MockProvider()
    chained = maybe_chain(engine, helper)
    assert isinstance(chained, ChainProvider)
    assert chained.members == [engine, helper]
    assert maybe_chain(MockProvider(), MockProvider()) is not None
    general = MockProvider()
    assert maybe_chain(general, MockProvider()) is general  # general stands alone
    twin = MTOnly()
    assert maybe_chain(engine, twin) is engine  # same-named helper adds nothing
