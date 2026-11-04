from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import auth, services
from app.config import TZ


class Clock:
    """Controllable 'now' (Berlin local time) for API tests."""

    def __init__(self, monkeypatch) -> None:
        self._mp = monkeypatch
        self.set("2026-11-04T09:00")

    def set(self, local: str) -> None:
        ts = int(datetime.fromisoformat(local).replace(tzinfo=TZ).timestamp())
        self._mp.setattr(services, "now_ts", lambda: ts)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    return Clock(monkeypatch)


PASSWORD = "correct horse battery"
USERNAME = "philipp"
CREDS = {"username": USERNAME, "password": PASSWORD}
BASE = "https://testserver"
ORIGIN = {"Origin": BASE}


@pytest.fixture
def anon_client(tmp_path, monkeypatch, clock) -> TestClient:
    """No session. Secure cookies work because the base URL is https, like behind the TLS proxy."""
    monkeypatch.setenv("ZEIT_DB_PATH", str(tmp_path / "db" / "zeit.db"))
    monkeypatch.setenv("ZEIT_USERNAME", USERNAME)
    monkeypatch.setenv("ZEIT_PASSWORD_HASH", auth.hash_password(PASSWORD, n=1024))
    auth.throttle.reset()
    from app.main import app

    return TestClient(app, base_url=BASE, headers=ORIGIN)


@pytest.fixture
def client(anon_client) -> TestClient:
    """Logged in."""
    res = anon_client.post("/api/v1/auth/login", json=CREDS)
    assert res.status_code == 200
    return anon_client
