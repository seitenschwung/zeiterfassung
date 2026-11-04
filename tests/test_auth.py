import sqlite3

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Mount, Route

from app import auth
from app.main import app
from tests.conftest import BASE, CREDS, PASSWORD, USERNAME

STATIC_PROBE = "/static/app.js"  # exists, is not public


def _paths() -> list[tuple[str, str]]:
    """(method, concrete path) for every registered route, so new routes are covered automatically."""
    out = []
    routes = []
    for route in app.routes:
        # newer FastAPI wraps include_router() results; the wrapped router has the full paths
        routes.extend(route.original_router.routes if hasattr(route, "original_router") else [route])
    for route in routes:
        if isinstance(route, APIRoute | Route):
            path = route.path.replace("{entry_id}", "1").replace("{day}", "2026-11-04")
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                out.append((method, path))
        elif isinstance(route, Mount) and route.path == "/static":
            out.append(("GET", STATIC_PROBE))
        else:
            raise AssertionError(f"unhandled route type, extend this test: {route!r}")
    return out


def test_route_listing_is_not_empty():
    paths = _paths()
    assert ("GET", "/api/v1/backup") in paths
    assert ("GET", "/api/v1/export.csv") in paths


@pytest.mark.parametrize(("method", "path"), _paths())
def test_every_route_requires_a_session_unless_public(anon_client, method, path):
    res = anon_client.request(method, path, json={} if method in {"POST", "PUT"} else None)
    if auth.is_public(path):
        assert res.status_code != 401
    else:
        assert res.status_code == 401, (method, path)
        assert res.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/backup", "/api/v1/export.csv", "/api/v1/nope", "/static/index.html", "/static/login.html"],
)
def test_unknown_and_sensitive_paths_denied(anon_client, path):
    assert anon_client.get(path).status_code == 401


def test_public_set_is_exactly_what_the_issue_allows():
    assert auth.PUBLIC_PATHS == {
        "/",
        "/login",
        "/healthz",
        "/version.txt",
        "/robots.txt",
        "/api/v1/auth/login",
    }
    assert all(p.startswith("/static/") for p in auth.PUBLIC_STATIC)


def test_public_pages_work_without_session(anon_client):
    assert anon_client.get("/healthz").status_code == 200
    assert anon_client.get("/version.txt").status_code == 200
    assert anon_client.get("/static/style.css").status_code == 200
    root = anon_client.get("/")
    assert root.status_code == 200
    assert "text/html" in root.headers["content-type"]
    assert "Anmelden" in root.text
    assert "Überstunden" not in root.text


def test_root_serves_app_with_session(client):
    assert "Überstunden" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/v1/overview").status_code == 200


def test_csp_unchanged_and_strict(anon_client):
    csp = anon_client.get("/").headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "unsafe" not in csp


def test_wrong_password_rejected(anon_client):
    res = anon_client.post("/api/v1/auth/login", json={**CREDS, "password": "nope"})
    assert res.status_code == 401
    assert "set-cookie" not in res.headers
    assert anon_client.get("/api/v1/overview").status_code == 401


def test_fail_closed_without_hash(anon_client, monkeypatch):
    monkeypatch.delenv("ZEIT_PASSWORD_HASH")
    assert anon_client.post("/api/v1/auth/login", json=CREDS).status_code == 401
    assert anon_client.post("/api/v1/auth/login", json={**CREDS, "password": ""}).status_code == 401
    assert anon_client.get("/healthz").status_code == 200


@pytest.mark.parametrize(
    "bad", ["", "   ", "garbage", "scrypt:1:8:1:zz:zz", "scrypt:16384:8:1:00:", "$scrypt$x"]
)
def test_malformed_hash_rejects_everything(anon_client, monkeypatch, bad):
    monkeypatch.setenv("ZEIT_PASSWORD_HASH", bad)
    assert anon_client.post("/api/v1/auth/login", json=CREDS).status_code == 401


def test_hash_format_has_no_dollar_and_verifies():
    h = auth.hash_password("pw-12345678901", n=1024)
    assert "$" not in h
    assert auth.verify_password("pw-12345678901", h)
    assert not auth.verify_password("other", h)
    assert auth.hash_password("pw-12345678901", n=1024) != h  # random salt


def test_cookie_flags_and_server_side_session(anon_client, tmp_path):
    res = anon_client.post("/api/v1/auth/login", json=CREDS)
    cookie = res.headers["set-cookie"].lower()
    for flag in ("httponly", "secure", "samesite=strict", f"max-age={30 * 24 * 3600}"):
        assert flag in cookie
    token = anon_client.cookies.get(auth.COOKIE)
    db = sqlite3.connect(tmp_path / "db" / "zeit.db")
    rows = db.execute("SELECT token_hash FROM sessions").fetchall()
    assert len(rows) == 1
    assert token not in rows[0][0]  # only a digest is stored


def test_secure_flag_can_be_disabled_for_local_http(anon_client, monkeypatch):
    monkeypatch.setenv("ZEIT_COOKIE_SECURE", "0")
    res = anon_client.post("/api/v1/auth/login", json=CREDS)
    assert "secure" not in res.headers["set-cookie"].lower()


def test_logout_ends_session(client):
    token = client.cookies.get(auth.COOKIE)
    assert client.post("/api/v1/auth/logout").status_code == 204
    client.cookies.set(auth.COOKIE, token)  # replaying the old token must not work
    assert client.get("/api/v1/overview").status_code == 401


def test_session_expires_after_30_days(client, clock):
    clock.set("2026-12-03T09:00")
    assert client.get("/api/v1/overview").status_code == 200
    clock.set("2026-12-05T09:00")
    assert client.get("/api/v1/overview").status_code == 401


def test_forged_token_rejected(anon_client):
    anon_client.cookies.set(auth.COOKIE, "x" * 43)
    assert anon_client.get("/api/v1/overview").status_code == 401


def test_throttle_grows_and_blocks_correct_password(anon_client):
    ip = {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(3):
        assert (
            anon_client.post("/api/v1/auth/login", json={**CREDS, "password": "bad"}, headers=ip).status_code
            == 401
        )
    res = anon_client.post("/api/v1/auth/login", json={**CREDS, "password": "bad"}, headers=ip)
    assert res.status_code == 401  # 4th failure triggers the first wait
    blocked = anon_client.post("/api/v1/auth/login", json=CREDS, headers=ip)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1


def test_throttle_is_per_ip_and_spoofed_prefix_does_not_help(anon_client):
    for _ in range(6):
        anon_client.post(
            "/api/v1/auth/login",
            json={**CREDS, "password": "bad"},
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
    # another real client (the proxy appends the real address last) is not locked out
    ok = anon_client.post("/api/v1/auth/login", json=CREDS, headers={"X-Forwarded-For": "203.0.113.50"})
    assert ok.status_code == 200
    # a spoofed first entry does not dodge the limit: the last entry counts
    spoof = anon_client.post(
        "/api/v1/auth/login", json=CREDS, headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.9"}
    )
    assert spoof.status_code == 429


def test_throttle_wait_is_capped_and_forgotten():
    t = auth.Throttle()
    for _ in range(40):
        t.failure("a")
    assert t.wait_seconds("a") <= auth.Throttle.CAP
    t.success("a")
    assert t.wait_seconds("a") == 0


def test_throttle_does_not_affect_other_ip_state():
    t = auth.Throttle()
    for _ in range(10):
        t.failure("attacker")
    assert t.wait_seconds("owner") == 0


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null"])
def test_writes_check_origin(client, origin):
    headers = {"Origin": origin} if origin else {}
    client.headers.pop("Origin", None)
    res = client.post("/api/v1/clock/start", headers=headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "bad_origin"


def test_login_also_checks_origin(anon_client):
    anon_client.headers.pop("Origin", None)
    res = anon_client.post("/api/v1/auth/login", json=CREDS, headers={"Origin": "https://evil.example"})
    assert res.status_code == 403


def test_same_origin_write_and_reads_without_origin_pass(client):
    assert client.post("/api/v1/clock/start", headers={"Origin": BASE}).status_code == 201
    client.headers.pop("Origin")
    assert client.get("/api/v1/overview").status_code == 200


def test_no_secrets_in_logs(anon_client, caplog):
    anon_client.post("/api/v1/auth/login", json={"password": "hunter2-secret"})
    assert "hunter2-secret" not in caplog.text


def test_robots_txt_allows_login_and_blocks_api(anon_client):
    res = anon_client.get("/robots.txt")
    assert res.text == "User-agent: *\nAllow: /\nDisallow: /api/\n"
    assert "noindex" in res.headers["x-robots-tag"]


@pytest.mark.parametrize("url", ["/", "/login"])
def test_login_page_is_indexable_without_session(anon_client, url):
    res = anon_client.get(url)
    assert res.status_code == 200
    assert "x-robots-tag" not in res.headers
    assert 'lang="de"' in res.text
    assert "<title>" in res.text
    assert 'name="description"' in res.text
    assert "width=device-width" in res.text
    assert "noindex" not in res.text


def test_root_with_session_stays_noindex(client):
    assert "Überstunden" in client.get("/").text
    assert client.get("/").headers["x-robots-tag"] == "noindex, nofollow"


def test_login_page_with_session_has_no_robots_header_but_no_data(client):
    res = client.get("/login")
    assert "x-robots-tag" not in res.headers
    assert "Überstunden" not in res.text


@pytest.mark.parametrize(
    "url", ["/healthz", "/version.txt", "/api/v1/overview", "/api/v1/nope", "/static/app.js"]
)
@pytest.mark.parametrize("session", [False, True])
def test_everything_else_is_noindex(request, url, session):
    c = request.getfixturevalue("client" if session else "anon_client")
    assert c.get(url).headers["x-robots-tag"] == "noindex, nofollow"


def test_login_api_noindex(anon_client):
    res = anon_client.post("/api/v1/auth/login", json={**CREDS, "password": "bad"})
    assert res.headers["x-robots-tag"] == "noindex, nofollow"


def _take_slots():
    for _ in range(auth.MAX_HASHES):
        assert auth._hash_slots.acquire(blocking=False)


def _free_slots():
    for _ in range(auth.MAX_HASHES):
        auth._hash_slots.release()


def test_busy_hashing_answers_429_without_hashing(anon_client, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "_scrypt", lambda *a, **k: calls.append(1) or b"")
    _take_slots()
    try:
        res = anon_client.post("/api/v1/auth/login", json=CREDS)
        assert res.status_code == 429
        assert int(res.headers["retry-after"]) >= 1
        monkeypatch.delenv("ZEIT_PASSWORD_HASH")  # also without a configured hash
        assert anon_client.post("/api/v1/auth/login", json={**CREDS, "password": "x"}).status_code == 429
        assert calls == []
    finally:
        _free_slots()
    monkeypatch.undo()
    assert anon_client.post("/api/v1/auth/login", json={**CREDS, "password": "bad"}).status_code == 401


def test_busy_rejections_do_not_count_against_the_ip(anon_client):
    ip = {"X-Forwarded-For": "203.0.113.77"}
    _take_slots()
    try:
        for _ in range(10):
            assert (
                anon_client.post(
                    "/api/v1/auth/login", json={**CREDS, "password": "x"}, headers=ip
                ).status_code
                == 429
            )
    finally:
        _free_slots()
    assert anon_client.post("/api/v1/auth/login", json=CREDS, headers=ip).status_code == 200


def test_parallel_logins_are_limited_and_correct_password_still_works(anon_client, monkeypatch):
    import threading

    from app.db import connect

    connect().close()  # create the database before the threads race on it
    inside, release = threading.Semaphore(0), threading.Event()
    running, peak, lock = [0], [0], threading.Lock()
    real = auth.verify_password

    def slow(password, stored):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        inside.release()
        release.wait(5)
        with lock:
            running[0] -= 1
        return real(password, stored)

    monkeypatch.setattr(auth, "verify_password", slow)
    codes = []

    def attempt(i):
        r = anon_client.post("/api/v1/auth/login", json=CREDS, headers={"X-Forwarded-For": f"198.51.100.{i}"})
        codes.append(r.status_code)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for _ in range(auth.MAX_HASHES):
        assert inside.acquire(timeout=5)
    for _ in range(100):  # let the other four get rejected while the slots are held
        if len(codes) >= 4:
            break
        threading.Event().wait(0.05)
    release.set()
    for t in threads:
        t.join(10)
    assert peak[0] <= auth.MAX_HASHES
    assert sorted(codes) == [200, 200, 429, 429, 429, 429]
    monkeypatch.setattr(auth, "verify_password", real)
    assert anon_client.post("/api/v1/auth/login", json=CREDS).status_code == 200


def test_parallel_attempts_of_one_ip_are_counted_before_the_hash(anon_client, monkeypatch):
    """While the first (slow) hash runs, its attempt is already counted: 4 in flight lock the IP."""
    t = auth.throttle
    for _ in range(auth.Throttle.FREE):
        assert t.begin("9.9.9.9") == 0  # nothing hashed yet, still counted
    assert t.begin("9.9.9.9") == 0  # 4th is the one that sets the wait
    assert t.begin("9.9.9.9") > 0
    t.success("9.9.9.9")
    assert t.begin("9.9.9.9") == 0


def test_undo_takes_one_attempt_back():
    t = auth.Throttle()
    for _ in range(4):
        t.begin("a")
    assert t.wait_seconds("a") > 0
    t.undo("a")
    assert t.wait_seconds("a") == 0


MSG = "Benutzername oder Passwort falsch."


def _login(c, **kw):
    return c.post("/api/v1/auth/login", json={**CREDS, **kw})


def test_login_ok(anon_client):
    assert _login(anon_client).status_code == 200


@pytest.mark.parametrize(
    "kw", [{"username": "other"}, {"username": "Philipp"}, {"password": "wrong"}, {"username": ""}]
)
def test_wrong_name_or_password_same_message(anon_client, kw):
    res = _login(anon_client, **kw)
    assert res.status_code == 401
    assert res.json()["error"]["message"] == MSG
    assert "set-cookie" not in res.headers


def test_missing_username_env_fails_closed(anon_client, monkeypatch):
    monkeypatch.delenv("ZEIT_USERNAME")
    for kw in ({}, {"username": ""}):
        res = _login(anon_client, **kw)
        assert res.status_code == 401
        assert res.json()["error"]["message"] == MSG


def test_hash_runs_even_for_wrong_username(anon_client, monkeypatch):
    calls = []
    real = auth._scrypt
    monkeypatch.setattr(auth, "_scrypt", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert _login(anon_client, username="nobody").status_code == 401
    assert len(calls) == 1
    monkeypatch.delenv("ZEIT_USERNAME")
    assert _login(anon_client).status_code == 401
    assert len(calls) == 2


def test_login_requires_username_field(anon_client):
    assert anon_client.post("/api/v1/auth/login", json={"password": PASSWORD}).status_code == 422
    assert USERNAME
