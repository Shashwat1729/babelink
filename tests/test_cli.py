"""CLI end to end with the mock provider: import -> paste -> analyze -> translate -> edit -> qa -> validate -> export."""
import io
import json

import pytest

from babelink import config, service
from babelink.cli import main
from babelink.providers.base import MockProvider

BOOK = "第一章 开始\n\n林凡走进青云宗。\n\n林凡看见大师兄。\n\n林凡点了点头。\n"
NEXT = "第二章 山门\n\n林凡离开了山门。\n"


def run(tmp_path, *args):
    return main(["--project-dir", str(tmp_path / "ws"), *args])


def test_full_flow(tmp_path, capsys, monkeypatch):
    book = tmp_path / "book.txt"
    book.write_text(BOOK, encoding="utf-8")
    assert run(tmp_path, "import", str(book), "--name", "Demo", "--source-lang", "zh") == 0
    assert run(tmp_path, "analyze", "1") == 0
    assert run(tmp_path, "translate", "1", "--chapter", "1") == 0

    monkeypatch.setattr("sys.stdin", io.StringIO(NEXT))
    assert run(tmp_path, "paste", "1", "--translate") == 0
    capsys.readouterr()
    assert run(tmp_path, "chapters", "1") == 0
    out = capsys.readouterr().out
    assert "第一章 开始" in out and "第二章 山门" in out and "3/3" in out and "1/1" in out

    assert run(tmp_path, "terminology", "1") == 0
    assert run(tmp_path, "edit", "1", "1", "--text", "Lin Fan entered the sect.", "--scope", "project") == 0
    assert run(tmp_path, "qa", "1") == 0
    capsys.readouterr()
    assert run(tmp_path, "projects") == 0
    assert "Demo" in capsys.readouterr().out

    epub, txt = tmp_path / "o.epub", tmp_path / "o.txt"
    assert run(tmp_path, "export", "1", "--out", str(epub)) == 0
    assert run(tmp_path, "export", "1", "--out", str(txt), "--bilingual") == 0
    assert "Lin Fan entered the sect." in txt.read_text(encoding="utf-8")
    assert epub.stat().st_size > 0
    run(tmp_path, "validate", "1", "--file", str(epub))  # mock output still contains source text; must report, not crash
    assert run(tmp_path, "validate", "1", "--file", str(tmp_path / "missing.epub")) == 1


def test_unknown_project_and_datasets(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path, "chapters", "99")
    assert main(["datasets", "--check"]) == 0


def test_config_file_defaults_and_flag_precedence(tmp_path, monkeypatch):
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "babelink.yaml").write_text(
        "provider: ollama\nmodel: base\nmodels: {translate: big}\ntarget_lang: fr\nstyle: literary\nn_prev: 5\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr("babelink.cli._provider", lambda a: seen.update(provider=a.provider, model=a.model) or MockProvider())
    monkeypatch.setattr("babelink.cli.translate_project", lambda *a, **kw: seen.update(n_prev=kw["n_prev"], summarize=kw["summarize"]) or 0)
    book = tmp_path / "b.txt"
    book.write_text(BOOK, encoding="utf-8")
    assert run(tmp_path, "import", str(book), "--name", "D", "--source-lang", "zh") == 0
    assert run(tmp_path, "translate", "1") == 0
    assert seen == {"provider": "ollama", "model": "big", "n_prev": 5, "summarize": False}
    assert run(tmp_path, "translate", "1", "--model", "cli", "--n-prev", "1", "--summaries", "--provider", "mock") == 0
    assert seen == {"provider": "mock", "model": "cli", "n_prev": 1, "summarize": True}
    db = service.open_workspace(tmp_path / "ws")
    p = db.get_project(1)
    assert (p["target_lang"], p["style"]) == ("fr", "literary")

    bad = tmp_path / "bad.yaml"
    bad.write_text("modle: x\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="unknown key"):
        config.load(bad)
    with pytest.raises(SystemExit, match="not found"):
        config.load(tmp_path / "nope.yaml", required=True)


def test_datasets_prints_on_a_cp1252_console(monkeypatch):
    import io
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")   # a legacy Windows console cannot encode the CJK names
    monkeypatch.setattr("sys.stdout", out)
    assert main(["datasets"]) == 0
    out.flush()
    assert "gutenberg-24264" in out.buffer.getvalue().decode("cp1252")
