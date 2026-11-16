"""FastAPI application: routers, correlation-id and latency middleware, error envelope, SPA hosting.

    uvicorn cf360.main:app --reload
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from cf360 import __version__
from cf360.api import routes_admin, routes_ops, routes_orders, routes_trades
from cf360.config import settings
from cf360.services.core import correlation_id, current_actor, jsonable, new_correlation
from cf360.services.dashboards import API_TIMINGS

log = logging.getLogger("cf360")

app = FastAPI(
    title="CapitalFlow 360 API",
    version=__version__,
    description=("Simulated multi-asset trade lifecycle platform (synthetic data only; no connectivity to real venues, "
                 "not production pricing or regulatory compliance). Every error returns "
                 "`{error: {code, message, details, correlation_id}}`."),
)


@app.middleware("http")
async def correlation_and_timing(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID") or new_correlation()
    request.state.correlation_id = cid
    tok = correlation_id.set(cid)
    atok = current_actor.set(("anonymous", "NONE"))
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        correlation_id.reset(tok)
        current_actor.reset(atok)
    ms = (time.perf_counter() - start) * 1000
    route = request.scope.get("route")
    if request.url.path.startswith("/api/"):
        API_TIMINGS.append((f"{request.method} {getattr(route, 'path', request.url.path)}", round(ms, 2)))
    response.headers["X-Correlation-ID"] = cid
    response.headers["Server-Timing"] = f"app;dur={ms:.1f}"
    return response


def _envelope(request: Request, status: int, code: str, message: str, details=None):
    return JSONResponse({"error": {"code": code, "message": message, "details": jsonable(details),
                                   "correlation_id": getattr(request.state, "correlation_id", None)}}, status_code=status)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    d = exc.detail
    if isinstance(d, dict) and "code" in d:
        return _envelope(request, exc.status_code, d["code"], d["message"], d.get("details"))
    return _envelope(request, exc.status_code, f"E_HTTP_{exc.status_code}", str(d))


@app.exception_handler(RequestValidationError)
async def schema_error(request: Request, exc: RequestValidationError):
    errs = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return _envelope(request, 422, "E_SCHEMA", "Request does not match the schema", {"errors": errs})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error")
    return _envelope(request, 500, "E_INTERNAL", "Unexpected server error; quote the correlation id")


for r in (routes_admin.router, routes_trades.router, routes_orders.router, routes_ops.router):
    app.include_router(r)


if settings.static_dir and settings.static_dir.exists():
    static = settings.static_dir
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"error": {"code": "E_NOT_FOUND", "message": "Unknown API path"}}, status_code=404)
        f = static / path
        return FileResponse(f if path and f.is_file() else static / "index.html")
