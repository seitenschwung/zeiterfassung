from fastapi.testclient import TestClient

from app.main import app


def test_healthz():
    assert TestClient(app).get("/healthz").status_code == 200


def test_version_comes_from_env(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "abc123")
    assert TestClient(app).get("/version.txt").text == "abc123"
