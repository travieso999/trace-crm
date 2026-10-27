import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import client_ip
from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, log

logger = logging.getLogger("app")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Trace Studio Outreach API",
        version="1.0.0",
        description="Internal cold-outreach platform API. All endpoints except /api/health, OAuth callbacks and "
                    "unsubscribe pages require a session cookie; unsafe methods also require X-CSRF-Token.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            key = request.cookies.get(settings.session_cookie_name, "")[:16] or (client_ip(request) or "anon")
            if not ratelimit.hit(f"api:{key}", settings.api_rate_limit_per_minute):
                return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/docs"):
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            response.headers.setdefault("Cache-Control", "no-store")
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [{"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse({"detail": "Validation failed", "errors": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log(logger, logging.ERROR, "unhandled_error", path=request.url.path, error=type(exc).__name__)
        logger.exception("unhandled exception")
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    @app.get("/api/health", tags=["health"])
    def health():
        db_ok = True
        try:
            with SessionLocal() as db:
                db.execute(text("select 1"))
        except Exception:
            db_ok = False
        return JSONResponse({"status": "ok" if db_ok else "degraded", "database": db_ok},
                            status_code=200 if db_ok else 503)

    from app.api.routes import auth, companies, contacts, dashboard
    from app.api.routes import settings as settings_routes

    for module in (auth, dashboard, contacts, companies, settings_routes):
        app.include_router(module.router)

    problems = settings.validate_for_runtime()
    if problems:
        log(logger, logging.WARNING, "configuration_problems", problems=problems)
        if settings.is_production:
            raise RuntimeError("Invalid production configuration: " + "; ".join(problems))
    return app


app = create_app()
