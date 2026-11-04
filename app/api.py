import csv
import io
import os
import sqlite3
import tempfile
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from app import auth, calc, services
from app.config import TZ
from app.db import get_conn
from app.schemas import AbsenceIn, EntryIn, LoginIn, SettingsIn
from app.services import AppError

router = APIRouter(prefix="/api/v1")
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _ok(data: object, **meta: object) -> dict:
    return {"data": data, "meta": meta}


def _too_many(message: str, retry_after: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": "too_many_attempts", "message": message}},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


@router.post("/auth/login")
def login(body: LoginIn, request: Request) -> Response:
    ip = auth.client_ip(request)
    wait = auth.throttle.begin(ip)  # counts this attempt before hashing
    if wait:
        return _too_many(f"Zu viele Versuche. Bitte in {wait} s erneut versuchen.", wait)
    try:
        ok = auth.check_login(body.username, body.password)
    except auth.Busy:
        auth.throttle.undo(ip)
        return _too_many("Server ist ausgelastet. Bitte gleich erneut versuchen.", auth.BUSY_RETRY_AFTER)
    if not ok:
        raise AppError(401, "invalid_credentials", "Benutzername oder Passwort falsch.")
    auth.throttle.success(ip)
    token = auth.create_session(services.now_ts())
    response = JSONResponse(_ok({"ok": True}))
    response.set_cookie(
        auth.COOKIE,
        token,
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        secure=auth.cookie_secure(),
        samesite="strict",
        path="/",
    )
    return response


@router.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    auth.delete_session(request.cookies.get(auth.COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(
        auth.COOKIE, path="/", httponly=True, secure=auth.cookie_secure(), samesite="strict"
    )
    return response


@router.get("/overview")
def overview(conn: Conn, date: date | None = None) -> dict:
    return _ok(services.overview(conn, date))


@router.post("/clock/start", status_code=201)
def clock_start(conn: Conn) -> dict:
    return _ok(services.clock_start(conn))


@router.post("/clock/stop")
def clock_stop(conn: Conn) -> dict:
    return _ok(services.clock_stop(conn))


@router.get("/entries")
def list_entries(
    conn: Conn,
    first: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    items, total = services.list_entries(conn, first, to, limit, offset)
    return _ok(items, total=total, limit=limit, offset=offset)


@router.post("/entries", status_code=201)
def create_entry(body: EntryIn, conn: Conn) -> dict:
    return _ok(services.create_entry(conn, body.start, body.end))


@router.put("/entries/{entry_id}")
def update_entry(entry_id: int, body: EntryIn, conn: Conn) -> dict:
    return _ok(services.update_entry(conn, entry_id, body.start, body.end))


@router.delete("/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: int, conn: Conn) -> Response:
    services.delete_entry(conn, entry_id)
    return Response(status_code=204)


@router.put("/absences/{day}")
def set_absence(day: date, body: AbsenceIn, conn: Conn) -> dict:
    return _ok(services.set_absence(conn, day, body.kind, body.fraction))


@router.delete("/absences/{day}", status_code=204)
def delete_absence(day: date, conn: Conn) -> Response:
    services.delete_absence(conn, day)
    return Response(status_code=204)


@router.get("/settings")
def get_settings(conn: Conn) -> dict:
    return _ok(services.settings_out(services.load_settings(conn)))


@router.put("/settings")
def put_settings(body: SettingsIn, conn: Conn) -> dict:
    return _ok(services.settings_out(services.save_settings(conn, body.model_dump())))


def _hours(seconds: int) -> str:
    return f"{seconds / 3600:.2f}".replace(".", ",")


@router.get("/export.csv")
def export_csv(conn: Conn) -> Response:
    """Semicolon-separated, decimal comma, UTF-8 with BOM: opens directly in German Excel."""
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow(["Typ", "Datum", "Start", "Ende", "Stunden"])
    fmt = "%Y-%m-%d %H:%M"
    for r in conn.execute("SELECT * FROM entries ORDER BY start_ts"):
        s = datetime.fromtimestamp(r["start_ts"], TZ)
        if r["end_ts"] is None:
            w.writerow(["Arbeit", s.date().isoformat(), s.strftime(fmt), "", ""])
        else:
            e = datetime.fromtimestamp(r["end_ts"], TZ)
            w.writerow(
                [
                    "Arbeit",
                    s.date().isoformat(),
                    s.strftime(fmt),
                    e.strftime(fmt),
                    _hours(r["end_ts"] - r["start_ts"]),
                ]
            )
    settings = services.load_settings(conn)
    for r in conn.execute("SELECT * FROM absences ORDER BY day"):
        credit = round(calc.day_soll(date.fromisoformat(r["day"]), settings) * r["fraction"])
        kind = "Urlaub" if r["kind"] == "urlaub" else "Krank"
        w.writerow(
            [kind + (" (halber Tag)" if r["fraction"] == 0.5 else ""), r["day"], "", "", _hours(credit)]
        )
    name = f"zeiterfassung-{services.today().isoformat()}.csv"
    return Response(
        "﻿" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


@router.get("/backup")
def backup(conn: Conn) -> FileResponse:
    """Consistent copy of the database (SQLite online backup, safe while the app is writing)."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    dst = sqlite3.connect(tmp)
    try:
        conn.backup(dst)
    except sqlite3.Error as exc:
        dst.close()
        os.unlink(tmp)
        raise AppError(500, "backup_failed", f"Backup fehlgeschlagen: {exc}") from exc
    dst.close()
    stamp = datetime.fromtimestamp(services.now_ts(), TZ).strftime("%Y%m%d-%H%M%S")
    return FileResponse(
        tmp,
        media_type="application/vnd.sqlite3",
        filename=f"zeiterfassung-{stamp}.db",
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(os.unlink, tmp),
    )
