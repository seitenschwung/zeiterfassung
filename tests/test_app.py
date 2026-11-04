from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200


def test_version_comes_from_env(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "abc123")
    assert client.get("/version.txt").text == "abc123"


def test_ping_uses_the_envelope():
    assert client.get("/api/v1/ping").json() == {"data": {"pong": True}, "meta": {}}
