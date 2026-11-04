from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import services
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


@pytest.fixture
def client(tmp_path, monkeypatch, clock) -> TestClient:
    monkeypatch.setenv("ZEIT_DB_PATH", str(tmp_path / "db" / "zeit.db"))
    from app.main import app

    return TestClient(app)
