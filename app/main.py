import os

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

app = FastAPI(title="Project template demo")


@app.get("/healthz", response_class=PlainTextResponse, include_in_schema=False)
def healthz() -> str:
    return "ok\n"


@app.get("/version.txt", response_class=PlainTextResponse, include_in_schema=False)
def version() -> PlainTextResponse:
    return PlainTextResponse(os.environ.get("APP_VERSION", "dev"), headers={"Cache-Control": "no-store"})


@app.get("/")
def index() -> dict:
    return {"data": {"service": "demo", "docs": "/docs"}}


@app.get("/api/v1/ping")
def ping() -> dict:
    # Success responses use the envelope {"data": ..., "meta": ...}; keep new endpoints consistent.
    return {"data": {"pong": True}, "meta": {}}
