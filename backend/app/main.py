from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth import router as auth_router
from app.api.discovery import router as discovery_router
from app.api.events import router as events_router
from app.api.ideas import router as ideas_router
from app.api.routes import router as api_router
from app.api.runs import router as runs_router
from app.api.sandbox import router as sandbox_router
from app.api.settings import router as settings_router
from app.core.auth import SingleAccountAuthMiddleware
from app.core.config import get_settings
from app.services.discovery import configure_discovery_scheduler


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Research Orchestration API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    allowed_origins = [settings.self_url]
    if settings.allowed_cors_origin:
        allowed_origins.append(settings.allowed_cors_origin)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SingleAccountAuthMiddleware, settings=settings)

    app.include_router(auth_router)
    app.include_router(discovery_router)
    app.include_router(events_router)
    app.include_router(ideas_router)
    app.include_router(runs_router)
    app.include_router(sandbox_router)
    app.include_router(settings_router)
    app.include_router(api_router)

    static_dir = Path(__file__).resolve().parents[2] / "static"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str) -> FileResponse:
            requested_path = static_dir / full_path
            if full_path and requested_path.is_file():
                return FileResponse(requested_path)
            return FileResponse(static_dir / "index.html")

    configure_discovery_scheduler(app, settings)
    return app


app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.app_env == "development")
