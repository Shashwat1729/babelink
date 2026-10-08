import json
from pathlib import Path

from lorebridge.datasets import LocalFileAdapter
from lorebridge.eval import run_benchmark, write_leaderboard
from lorebridge.providers.base import MockProvider


def test_local_adapter_and_eval(tmp_path: Path):
    ds = tmp_path / "ds.jsonl"
    ds.write_text(
        json.dumps({"id": "1", "source_language": "zh", "target_language": "en", "source": "你好", "reference": "你好"}) + "\n",
        encoding="utf-8",
    )
    records = LocalFileAdapter(ds).load()
    assert len(records) == 1
    result = run_benchmark(records, MockProvider())
    assert result["samples"] == 1
    lb = write_leaderboard([result], tmp_path / "lb.md")
    assert lb.exists()
