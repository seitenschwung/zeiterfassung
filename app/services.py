"""Business logic. Routers stay thin; every failure is an AppError (German message)."""

import json
import sqlite3
import time
from datetime import date, datetime, timedelta

from app import calc
from app.calc import Settings
from app.config import TZ
from app.db import write_tx


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def now_ts() -> int:
    return int(time.time())


def today() -> date:
    return calc.local_date(now_ts())


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds")


def local_str(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%dT%H:%M")


def parse_ts(value: str) -> int:
    """ISO datetime -> epoch. Without offset it is Berlin local time; ambiguous times take the first
    occurrence, times skipped by the spring DST jump are rejected."""
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        raise AppError(422, "invalid_datetime", f"Ungültiges Datum/Uhrzeit: {value!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
        if datetime.fromtimestamp(dt.timestamp(), TZ).replace(tzinfo=None) != dt.replace(tzinfo=None):
            raise AppError(422, "nonexistent_time", "Diese Uhrzeit gibt es wegen der Zeitumstellung nicht.")
    return int(dt.timestamp())


# --- settings -------------------------------------------------------------------------------------


def load_settings(conn: sqlite3.Connection) -> Settings:
    row = conn.execute("SELECT value FROM settings WHERE key = 'config'").fetchone()
    if not row:
        return Settings()
    raw = json.loads(row["value"])
    return Settings(
        weekly_hours=raw["weekly_hours"],
        workdays=tuple(raw["workdays"]),
        vacation_days=raw["vacation_days"],
        start_date=date.fromisoformat(raw["start_date"]) if raw.get("start_date") else None,
        start_balance_hours=raw["start_balance_hours"],
    )


def settings_out(s: Settings) -> dict:
    return {
        "weekly_hours": s.weekly_hours,
        "workdays": list(s.workdays),
        "vacation_days": s.vacation_days,
        "start_date": s.start_date.isoformat() if s.start_date else None,
        "start_balance_hours": s.start_balance_hours,
        "daily_soll": s.daily_soll,
    }


def save_settings(conn: sqlite3.Connection, data: dict) -> Settings:
    payload = {**data, "start_date": data["start_date"].isoformat() if data.get("start_date") else None}
    with write_tx(conn):
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('config', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(payload),),
        )
    return load_settings(conn)


# --- entries --------------------------------------------------------------------------------------


def entry_out(row: sqlite3.Row, now: int) -> dict:
    s, e = row["start_ts"], row["end_ts"]
    return {
        "id": row["id"],
        "start": iso(s),
        "end": iso(e) if e is not None else None,
        "start_local": local_str(s),
        "end_local": local_str(e) if e is not None else None,
        "running": e is None,
        "duration": (e if e is not None else max(now, s)) - s,
    }


def _check(conn: sqlite3.Connection, start: int, end: int | None, now: int, exclude: int | None) -> None:
    if start > now:
        raise AppError(422, "in_future", "Der Start liegt in der Zukunft.")
    if end is not None:
        if end <= start:
            raise AppError(422, "end_before_start", "Das Ende muss nach dem Start liegen.")
        if end > now:
            raise AppError(422, "in_future", "Das Ende liegt in der Zukunft.")
    clash = conn.execute(
        "SELECT id, start_ts, end_ts FROM entries "
        "WHERE id IS NOT ? AND start_ts < ? AND COALESCE(end_ts, 9223372036854775807) > ? LIMIT 1",
        (exclude, 9223372036854775807 if end is None else end, start),
    ).fetchone()
    if clash:
        s = local_str(clash["start_ts"]).replace("T", " ")
        e = local_str(clash["end_ts"]).replace("T", " ") if clash["end_ts"] is not None else "läuft noch"
        raise AppError(409, "overlap", f"Überschneidung mit einem anderen Eintrag ({s} – {e}).")


def _get(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        raise AppError(404, "not_found", "Eintrag nicht gefunden.")
    return row


def list_entries(
    conn: sqlite3.Connection, first: date | None, last: date | None, limit: int, offset: int
) -> tuple[list[dict], int]:
    where, args = "", []
    if first:
        where += " AND COALESCE(end_ts, 9223372036854775807) > ?"
        args.append(calc.day_bounds(first)[0])
    if last:
        where += " AND start_ts < ?"
        args.append(calc.day_bounds(last)[1])
    total = conn.execute(f"SELECT COUNT(*) FROM entries WHERE 1=1{where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM entries WHERE 1=1{where} ORDER BY start_ts DESC LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    now = now_ts()
    return [entry_out(r, now) for r in rows], total


def create_entry(conn: sqlite3.Connection, start: str, end: str | None) -> dict:
    if end is None:
        raise AppError(422, "end_missing", "Für einen manuellen Eintrag ist ein Ende nötig.")
    s, e, now = parse_ts(start), parse_ts(end), now_ts()
    with write_tx(conn):
        _check(conn, s, e, now, None)
        cur = conn.execute("INSERT INTO entries (start_ts, end_ts) VALUES (?, ?)", (s, e))
    return entry_out(_get(conn, cur.lastrowid), now)


def update_entry(conn: sqlite3.Connection, entry_id: int, start: str, end: str | None) -> dict:
    s, now = parse_ts(start), now_ts()
    with write_tx(conn):
        row = _get(conn, entry_id)
        if end is None and row["end_ts"] is not None:
            raise AppError(422, "end_missing", "Ein beendeter Eintrag braucht ein Ende.")
        e = parse_ts(end) if end is not None else None
        _check(conn, s, e, now, entry_id)
        conn.execute("UPDATE entries SET start_ts = ?, end_ts = ? WHERE id = ?", (s, e, entry_id))
    return entry_out(_get(conn, entry_id), now)


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    with write_tx(conn):
        _get(conn, entry_id)
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))


def clock_start(conn: sqlite3.Connection) -> dict:
    now = now_ts()
    with write_tx(conn):
        if conn.execute("SELECT 1 FROM entries WHERE end_ts IS NULL").fetchone():
            raise AppError(409, "already_running", "Es läuft bereits eine Zeiterfassung.")
        _check(conn, now, None, now, None)
        cur = conn.execute("INSERT INTO entries (start_ts) VALUES (?)", (now,))
    return entry_out(_get(conn, cur.lastrowid), now)


def clock_stop(conn: sqlite3.Connection) -> dict:
    now = now_ts()
    with write_tx(conn):
        row = conn.execute("SELECT * FROM entries WHERE end_ts IS NULL").fetchone()
        if not row:
            raise AppError(409, "not_running", "Es läuft keine Zeiterfassung.")
        if now <= row["start_ts"]:
            raise AppError(409, "too_short", "Die Erfassung läuft noch keine Sekunde.")
        conn.execute("UPDATE entries SET end_ts = ? WHERE id = ?", (now, row["id"]))
    return entry_out(_get(conn, row["id"]), now)


# --- absences -------------------------------------------------------------------------------------


def set_absence(conn: sqlite3.Connection, day: date, kind: str, fraction: float) -> dict:
    settings = load_settings(conn)
    if not calc.is_working_day(day, settings):
        raise AppError(422, "no_workday", "An diesem Tag ist kein Arbeitstag (Wochenende oder Feiertag).")
    with write_tx(conn):
        conn.execute(
            "INSERT INTO absences (day, kind, fraction) VALUES (?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET kind = excluded.kind, fraction = excluded.fraction",
            (day.isoformat(), kind, fraction),
        )
    return {"date": day.isoformat(), "kind": kind, "fraction": fraction}


def delete_absence(conn: sqlite3.Connection, day: date) -> None:
    with write_tx(conn):
        cur = conn.execute("DELETE FROM absences WHERE day = ?", (day.isoformat(),))
    if cur.rowcount == 0:
        raise AppError(404, "not_found", "Keine Abwesenheit an diesem Tag.")


# --- overview -------------------------------------------------------------------------------------


def _all_absences(conn: sqlite3.Connection) -> dict[date, tuple[str, float]]:
    return {
        date.fromisoformat(r["day"]): (r["kind"], r["fraction"])
        for r in conn.execute("SELECT day, kind, fraction FROM absences")
    }


def effective_start(conn: sqlite3.Connection, settings: Settings, today_: date) -> date:
    if settings.start_date:
        return settings.start_date
    candidates = [today_]
    row = conn.execute("SELECT MIN(start_ts) FROM entries").fetchone()
    if row[0] is not None:
        candidates.append(calc.local_date(row[0]))
    row = conn.execute("SELECT MIN(day) FROM absences").fetchone()
    if row[0] is not None:
        candidates.append(date.fromisoformat(row[0]))
    return min(candidates)


def overview(conn: sqlite3.Connection, ref: date | None) -> dict:
    now = now_ts()
    today_ = calc.local_date(now)
    ref = ref or today_
    settings = load_settings(conn)
    monday = ref - timedelta(days=ref.isoweekday() - 1)
    sunday = monday + timedelta(days=6)
    start = effective_start(conn, settings, today_)
    first, last = min(start, monday), max(today_, sunday)
    lo, hi = calc.day_bounds(first)[0], calc.day_bounds(last)[1]
    rows = conn.execute(
        "SELECT * FROM entries WHERE start_ts < ? AND COALESCE(end_ts, 9223372036854775807) > ? "
        "ORDER BY start_ts",
        (hi, lo),
    ).fetchall()
    absences = _all_absences(conn)
    days = calc.build_days(
        first, last, [(r["start_ts"], r["end_ts"]) for r in rows], absences, settings, start, today_, now
    )
    by_date = {d["date"]: d for d in days}

    week_days = []
    for i in range(7):
        d = dict(by_date[monday + timedelta(days=i)])
        d_lo, d_hi = calc.day_bounds(d["date"])
        d["entries"] = []
        for r in rows:
            end = r["end_ts"] if r["end_ts"] is not None else max(now, r["start_ts"] + 1)
            if r["start_ts"] < d_hi and end > d_lo:
                d["entries"].append(
                    {
                        **entry_out(r, now),
                        "day_seconds": min(end, d_hi) - max(r["start_ts"], d_lo),
                        "from_previous_day": r["start_ts"] < d_lo,
                        "into_next_day": end > d_hi,
                    }
                )
        d["date"] = d["date"].isoformat()
        week_days.append(d)

    counted = [d for d in days if d["counted"]]
    balance = settings.start_balance + sum(d["diff"] for d in counted)
    td = by_date[today_]
    iso_year, iso_week, _ = monday.isocalendar()
    used = sum(fr for day, (kind, fr) in absences.items() if kind == "urlaub" and day.year == today_.year)
    running = conn.execute("SELECT * FROM entries WHERE end_ts IS NULL").fetchone()
    return {
        "now": {"ts": now, "iso": iso(now), "date": today_.isoformat()},
        "running": entry_out(running, now) if running else None,
        "today": {"date": today_.isoformat(), "soll": td["soll"], "ist": td["ist"], "diff": td["diff"]},
        "week": {
            "year": iso_year,
            "week": iso_week,
            "from": monday.isoformat(),
            "to": sunday.isoformat(),
            "soll": sum(d["soll"] for d in week_days),
            "ist": sum(d["ist"] for d in week_days),
            "diff": sum(d["diff"] for d in week_days),
            "days": week_days,
        },
        "balance": {
            "total": balance,
            "before_today": balance - td["diff"],
            "start_date": start.isoformat(),
            "start_balance": settings.start_balance,
        },
        "vacation": {
            "year": today_.year,
            "total": settings.vacation_days,
            "used": used,
            "remaining": settings.vacation_days - used,
        },
        "settings": settings_out(settings),
    }
