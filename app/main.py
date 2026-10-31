# app/main.py

from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
from threading import Event, Thread
from services.mail_sync import scheduler_loop
from routers import mail_sync

@asynccontextmanager
async def lifespan(app):
    stop = Event()
    thread = Thread(target=scheduler_loop, args=(stop,), daemon=True, name="mail-sync")
    if get_settings().mail_scheduler_enabled:
        thread.start()
    yield
    stop.set()

from fastapi.responses import JSONResponse
from services.groq_client import AIError
from core.config import get_settings
from postgrest.exceptions import APIError
from fastapi.middleware.cors import CORSMiddleware

from routers import (
    profile,
    analysis,
    applications,
    mailboxes,
    recommendations,
    resumes,
)


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
        title="Job Application Tracker API",
        description=(
            "Application tracking, resumé recommendations, "
            "and Gmail-driven lifecycle updates."
        ),
        version="0.1.0",
    )

    @app.exception_handler(AIError)
    async def handle_ai_error(request: Request, exc: AIError):
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)}, headers=headers)

    @app.exception_handler(APIError)
    async def handle_database_error(request: Request, exc: APIError):
        code = str(exc.code)
        status_code, message = {
            "40001": (409, "The job or resume changed during generation. Reload and generate again."),
            "P0002": (404, "Record not found"), "23505": (409, "This record already exists"),
            "23503": (422, "The selected record is unavailable"), "42501": (403, "Access denied"),
            "P0001": (400, "The requested operation is not valid for this record"),
        }.get(code, (502, "Unable to complete the database operation. Please retry."))
        return JSONResponse(status_code=status_code, content={"detail": message})

    app.include_router(mail_sync.router, prefix="/api/v1")
    app.include_router(profile.router, prefix="/api/v1")
    app.include_router(analysis.router, prefix="/api/v1")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(
        resumes.router,
        prefix="/api/v1",
    )

    app.include_router(
        applications.router,
        prefix="/api/v1",
    )

    app.include_router(
        recommendations.router,
        prefix="/api/v1",
    )

    app.include_router(
        mailboxes.router,
        prefix="/api/v1",
    )

    @app.get(
        "/health",
        tags=["system"],
    )
    def health_check() -> dict[str, str | bool]:
        return {
            "status": "ok",
            "service": "zoin",
            "ai_model": get_settings().groq_model,
            "ai_configured": bool(get_settings().groq_api_key.get_secret_value()),
        }

    return app


app = create_app()