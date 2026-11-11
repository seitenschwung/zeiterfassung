import sqlite3

from app import auth

DAY = "2026-11-03"


def add(client, start, end):
    return client.post("/api/v1/entries", json={"start": start, "end": end})


def data(r):
    assert r.status_code < 300, r.text
    return r.json()["data"]


def test_health_and_index(client):
    assert client.get("/healthz").status_code == 200
    r = client.get("/")
    assert r.status_code == 200 and 'lang="de"' in r.text


def test_clock_start_stop_and_survives_restart(client, clock, tmp_path):
    clock.set("2026-11-04T08:00")
    assert data(client.post("/api/v1/clock/start"))["running"] is True
    assert client.post("/api/v1/clock/start").json()["error"]["code"] == "already_running"
    # "restart": a brand new app/client on the same database file still sees the running entry
    from fastapi.testclient import TestClient

    from app.main import app

    clock.set("2026-11-04T09:30")
    fresh = TestClient(app, base_url=client.base_url)
    fresh.cookies.set(auth.COOKIE, client.cookies.get(auth.COOKIE))  # sessions live in the database
    running = data(fresh.get("/api/v1/overview"))["running"]
    assert running["duration"] == 90 * 60
    stopped = data(client.post("/api/v1/clock/stop"))
    assert stopped["running"] is False and stopped["duration"] == 90 * 60
    assert client.post("/api/v1/clock/stop").json()["error"]["code"] == "not_running"


def test_end_before_start_rejected(client):
    r = add(client, "2026-11-03T10:00", "2026-11-03T09:00")
    assert r.status_code == 422 and r.json()["error"]["code"] == "end_before_start"
    assert add(client, "2026-11-03T10:00", "2026-11-03T10:00").status_code == 422


def test_overlap_rejected_but_touching_allowed(client):
    data(add(client, "2026-11-03T08:00", "2026-11-03T12:00"))
    for s, e in [
        ("2026-11-03T11:00", "2026-11-03T13:00"),
        ("2026-11-03T07:00", "2026-11-03T08:30"),
        ("2026-11-03T09:00", "2026-11-03T10:00"),
        ("2026-11-03T07:00", "2026-11-03T13:00"),
    ]:
        r = add(client, s, e)
        assert r.status_code == 409 and r.json()["error"]["code"] == "overlap"
    data(add(client, "2026-11-03T12:00", "2026-11-03T13:00"))
    data(add(client, "2026-11-03T07:00", "2026-11-03T08:00"))


def test_overlap_across_midnight(client):
    data(add(client, "2026-11-02T22:00", "2026-11-03T02:00"))
    assert add(client, "2026-11-03T01:00", "2026-11-03T03:00").status_code == 409


def test_running_entry_blocks_overlapping_manual_entry(client, clock):
    clock.set("2026-11-04T08:00")
    client.post("/api/v1/clock/start")
    clock.set("2026-11-04T09:00")
    assert add(client, "2026-11-04T08:30", "2026-11-04T08:45").status_code == 409
    data(add(client, "2026-11-04T06:00", "2026-11-04T07:00"))


def test_future_entries_rejected(client):
    assert add(client, "2026-11-04T10:00", "2026-11-04T11:00").json()["error"]["code"] == "in_future"


def test_update_and_delete(client):
    a = data(add(client, "2026-11-03T08:00", "2026-11-03T12:00"))
    b = data(add(client, "2026-11-03T13:00", "2026-11-03T17:00"))
    r = client.put(
        f"/api/v1/entries/{b['id']}", json={"start": "2026-11-03T11:00", "end": "2026-11-03T17:00"}
    )
    assert r.status_code == 409
    # an entry may be moved within its own old range (no self-overlap)
    r = client.put(
        f"/api/v1/entries/{a['id']}", json={"start": "2026-11-03T07:30", "end": "2026-11-03T12:30"}
    )
    assert data(r)["duration"] == 5 * 3600
    assert client.delete(f"/api/v1/entries/{a['id']}").status_code == 204
    assert client.delete(f"/api/v1/entries/{a['id']}").status_code == 404
    assert (
        client.put(
            "/api/v1/entries/999", json={"start": "2026-11-03T07:30", "end": "2026-11-03T08:00"}
        ).status_code
        == 404
    )


def test_edit_running_entry_start(client, clock):
    clock.set("2026-11-04T08:00")
    e = data(client.post("/api/v1/clock/start"))
    clock.set("2026-11-04T09:00")
    r = client.put(f"/api/v1/entries/{e['id']}", json={"start": "2026-11-04T07:30"})
    assert data(r)["running"] is True and data(r)["duration"] == 90 * 60


def test_spring_gap_time_rejected_and_autumn_ambiguous_accepted(client, clock):
    clock.set("2026-11-04T09:00")
    r = add(client, "2026-03-29T02:30", "2026-03-29T04:00")
    assert r.status_code == 422 and r.json()["error"]["code"] == "nonexistent_time"
    # night over the autumn change: 22:00 -> 06:00 are nine real hours
    assert data(add(client, "2026-10-24T22:00", "2026-10-25T06:00"))["duration"] == 9 * 3600


def test_explicit_offset_selects_second_occurrence_of_ambiguous_hour(client):
    e = data(add(client, "2026-10-25T02:30:00+01:00", "2026-10-25T02:45:00+01:00"))
    assert e["start"].endswith("+01:00") and e["duration"] == 15 * 60
    first = data(add(client, "2026-10-25T02:30", "2026-10-25T02:45"))  # naive = first occurrence
    assert first["start"].endswith("+02:00")


def test_entry_over_midnight_in_overview(client):
    data(add(client, "2026-11-02T22:00", "2026-11-03T02:00"))
    days = data(client.get("/api/v1/overview?date=2026-11-02"))["week"]["days"]
    assert days[0]["worked"] == 2 * 3600 and days[1]["worked"] == 2 * 3600
    assert days[0]["entries"][0]["into_next_day"] and days[1]["entries"][0]["from_previous_day"]


def test_week_and_total_balance(client, clock):
    clock.set("2026-11-06T18:00")  # Friday
    client.put("/api/v1/settings", json=dict(_settings(), start_date="2026-11-02", start_balance_hours=5))
    data(add(client, "2026-11-02T08:00", "2026-11-02T17:30"))  # +0.75 (9.5 h - 45 min legal break - 8 h)
    data(add(client, "2026-11-03T09:00", "2026-11-03T15:00"))  # -2
    client.put("/api/v1/absences/2026-11-04", json={"kind": "urlaub", "fraction": 1})  # 0
    client.put("/api/v1/absences/2026-11-05", json={"kind": "krank", "fraction": 0.5})  # -4
    o = data(client.get("/api/v1/overview"))
    # Friday has no entries yet and counts as -8
    assert o["week"]["diff"] == int((0.75 - 2 - 4 - 8) * 3600)
    assert o["balance"]["total"] == int((5 + 0.75 - 2 - 4 - 8) * 3600)
    assert o["balance"]["before_today"] == int((5 + 0.75 - 2 - 4) * 3600)
    assert o["vacation"]["used"] == 1 and o["vacation"]["remaining"] == 29
    assert o["week"]["week"] == 45


def test_absence_rules(client):
    assert (
        client.put("/api/v1/absences/2026-11-07", json={"kind": "urlaub", "fraction": 1}).status_code == 422
    )
    assert (
        client.put("/api/v1/absences/2026-12-25", json={"kind": "urlaub", "fraction": 1}).status_code == 422
    )
    assert client.put("/api/v1/absences/2026-11-03", json={"kind": "foo"}).status_code == 422
    assert (
        client.put("/api/v1/absences/2026-11-03", json={"kind": "urlaub", "fraction": 0.3}).status_code == 422
    )
    assert (
        client.put("/api/v1/absences/2026-11-03", json={"kind": "urlaub", "fraction": 0.5}).status_code == 200
    )
    assert client.delete("/api/v1/absences/2026-11-03").status_code == 204
    assert client.delete("/api/v1/absences/2026-11-03").status_code == 404


def _settings():
    return {"weekly_hours": 40, "workdays": [1, 2, 3, 4, 5], "vacation_days": 30, "start_date": None,
            "start_balance_hours": 0}  # fmt: skip


def test_settings_roundtrip_and_validation(client):
    assert data(client.get("/api/v1/settings"))["weekly_hours"] == 40
    ok = data(client.put("/api/v1/settings", json=dict(_settings(), weekly_hours=32, workdays=[4, 1, 2, 3])))
    assert ok["workdays"] == [1, 2, 3, 4] and ok["daily_soll"] == 8 * 3600
    assert client.put("/api/v1/settings", json=dict(_settings(), workdays=[])).status_code == 422
    assert client.put("/api/v1/settings", json=dict(_settings(), workdays=[1, 8])).status_code == 422


def test_list_entries_paginates(client):
    for h in range(8, 12):
        data(add(client, f"{DAY}T{h:02d}:00", f"{DAY}T{h:02d}:30"))
    r = client.get("/api/v1/entries?limit=3&offset=0").json()
    assert len(r["data"]) == 3 and r["meta"]["total"] == 4
    assert client.get("/api/v1/entries?from=2026-11-04").json()["meta"]["total"] == 0


def test_csv_export(client):
    data(add(client, "2026-11-03T08:00", "2026-11-03T12:30"))
    client.put("/api/v1/absences/2026-11-04", json={"kind": "urlaub", "fraction": 0.5})
    r = client.get("/api/v1/export.csv")
    assert (
        r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    )
    lines = r.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "Typ;Datum;Start;Ende;Stunden"
    assert lines[1] == "Arbeit;2026-11-03;2026-11-03 08:00;2026-11-03 12:30;4,50"
    assert lines[2] == "Urlaub (halber Tag);2026-11-04;;;4,00"


def test_backup_is_a_valid_sqlite_copy(client, tmp_path):
    data(add(client, "2026-11-03T08:00", "2026-11-03T12:30"))
    r = client.get("/api/v1/backup")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    copy = tmp_path / "copy.db"
    copy.write_bytes(r.content)
    assert sqlite3.connect(copy).execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1


def test_errors_use_the_envelope(client):
    r = client.get("/api/v1/entries?limit=0")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert client.get("/api/v1/nope").json()["error"]["code"] == "http_error"


def test_vacation_quota_per_year_keeps_existing_data(client, clock):
    clock.set("2026-11-04T09:00")
    data(add(client, "2026-11-03T08:00", "2026-11-03T16:00"))
    client.put("/api/v1/absences/2026-11-02", json={"kind": "urlaub", "fraction": 1})
    s = data(client.put("/api/v1/settings", json=dict(_settings(), vacation_overrides={"2026": 5})))
    assert s["vacation_overrides"] == {"2026": 5} and s["vacation_days"] == 30
    v = data(client.get("/api/v1/overview"))["vacation"]
    assert (v["total"], v["used"], v["remaining"]) == (5, 1, 4)
    assert data(client.get("/api/v1/entries?limit=5"))  # entries untouched
    bad = client.put("/api/v1/settings", json=dict(_settings(), vacation_overrides={"x": 5}))
    assert bad.status_code == 422


def test_manual_entry_without_end_starts_running_timer(client, clock):
    clock.set("2026-11-04T09:00")
    e = data(client.post("/api/v1/entries", json={"start": "2026-11-04T07:30"}))
    assert e["running"] is True and e["end"] is None and e["duration"] == 90 * 60
    clock.set("2026-11-04T10:00")
    assert data(client.get("/api/v1/overview"))["running"]["duration"] == 150 * 60
    stopped = data(client.post("/api/v1/clock/stop"))
    assert stopped["running"] is False and stopped["duration"] == 150 * 60


def test_manual_entry_without_end_rules(client, clock):
    clock.set("2026-11-04T09:00")
    client.post("/api/v1/clock/start")
    r = client.post("/api/v1/entries", json={"start": "2026-11-04T08:00"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_running"
    clock.set("2026-11-04T09:10")
    client.post("/api/v1/clock/stop")
    data(add(client, "2026-11-04T08:30", "2026-11-04T08:45"))
    assert client.post("/api/v1/entries", json={"start": "2026-11-04T08:00"}).status_code == 409  # overlap
    assert (
        client.post("/api/v1/entries", json={"start": "2026-11-04T10:00"}).json()["error"]["code"]
        == "in_future"
    )
