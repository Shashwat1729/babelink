from fastapi.testclient import TestClient

from storytranslate.web import app


def test_index():
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Story Translate" in r.text


def test_segments_empty():
    client = TestClient(app)
    r = client.get("/segments")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
