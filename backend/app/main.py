from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from app.access import make_access
from app.ai import ai_router
from app.approvals import approvals_router
from app.chat import chat_router
from app.database import make_engine
from app.documents import documents_router
from app.embeddings import EmbeddingWorker
from app.embeddings import enabled as embeddings_enabled
from app.identity import identity_router
from app.ingestion import ingestion_router
from app.storage import BlobStore


def create_app() -> FastAPI:
    engine = make_engine()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker = EmbeddingWorker(engine) if embeddings_enabled() else None
        if worker:
            worker.start()
        try:
            yield
        finally:
            if worker:
                worker.stop()
            engine.dispose()

    app = FastAPI(
        title="Private AI Workspace — Document Core",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    access = make_access(engine)
    app.include_router(identity_router(engine, access))
    storage = BlobStore.from_environment()
    app.include_router(documents_router(access, storage))
    app.include_router(approvals_router(access, storage))
    app.include_router(ingestion_router(access, storage))
    app.include_router(ai_router(access))
    app.include_router(chat_router(access))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        # Default validation responses may echo a submitted password in `input`.
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", response_model=None)
    def ready() -> JSONResponse:
        try:
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                if revision != "0014_llm_routing":
                    return JSONResponse({"status": "unavailable"}, status_code=503)
        except SQLAlchemyError:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    return app
