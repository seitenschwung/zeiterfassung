import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth, services
from app.api import router
from app.config import STATIC_DIR
from app.services import AppError

app = FastAPI(title="Zeiterfassung", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

CSP = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


@app.exception_handler(AppError)
async def _app_error(_: Request, exc: AppError) -> JSONResponse:
    return _error(exc.status, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first["loc"] if p not in ("body", "query", "path"))
    return _error(422, "validation_error", f"{where}: {first['msg']}" if where else first["msg"])


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error(exc.status_code, "http_error", str(exc.detail))


@app.middleware("http")
async def _auth(request: Request, call_next):
    """Default deny: every path needs a valid session unless it is listed as public in app.auth."""
    path = request.url.path
    if request.method in auth.UNSAFE_METHODS and not auth.origin_ok(request):
        return _error(403, "bad_origin", "Anfrage von fremder Herkunft abgelehnt.")
    if not auth.is_public(path):
        token = request.cookies.get(auth.COOKIE)
        if not await run_in_threadpool(auth.session_valid, token, services.now_ts()):
            return _error(401, "unauthorized", "Bitte anmelden.")
    return await call_next(request)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/healthz", response_class=PlainTextResponse, include_in_schema=False)
def healthz() -> str:
    return "ok\n"


@app.get("/version.txt", response_class=PlainTextResponse, include_in_schema=False)
def version() -> PlainTextResponse:
    return PlainTextResponse(os.environ.get("APP_VERSION", "dev"), headers={"Cache-Control": "no-store"})


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
def robots() -> str:
    return "User-agent: *\nDisallow: /\n"


def _page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, headers={"Cache-Control": "no-store"})


@app.get("/", include_in_schema=False)
def index(request: Request) -> FileResponse:
    """The app for a valid session, otherwise the login page (HTTP 200, so health probes can use it)."""
    if auth.session_valid(request.cookies.get(auth.COOKIE), services.now_ts()):
        return _page("index.html")
    return _page("login.html")


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return _page("login.html")
