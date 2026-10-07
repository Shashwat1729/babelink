from storytranslate.registry import recommend
from storytranslate.benchmark import benchmark
from storytranslate.providers.base import MockProvider


def test_recommend_filters_language():
    out = recommend("en", vram_gb=6, ram_gb=10)
    assert all("en" in m.languages for m in out)
    assert out


def test_benchmark_mock():
    b = benchmark(MockProvider())
    assert b["model"] == "mock"
    assert b["seconds"] >= 0
