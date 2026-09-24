from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Request, Response, status

from app.core.config import Settings
from app.core.database import (
    postgres_schema_exists,
    prepare_postgres_connection,
    resolve_postgres_database_config,
)

ops_router = APIRouter()


@ops_router.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@ops_router.get("/ready")
def readiness(request: Request, response: Response) -> dict[str, Any]:
    checks = {
        "runtime": _check_runtime(request),
        "checkpointer": _check_checkpointer(request),
        "database": _check_database(request.app.state.settings),
    }

    if any(check["status"] == "error" for check in checks.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "checks": checks}

    return {"status": "ready", "checks": checks}


def _check_runtime(request: Request) -> dict[str, str]:
    if getattr(request.app.state, "graph", None) is None:
        return {"status": "error", "reason": "graph_missing"}

    return {"status": "ok"}


def _check_checkpointer(request: Request) -> dict[str, str]:
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        return {"status": "error", "reason": "checkpointer_missing"}

    return {"status": "ok", "type": type(checkpointer).__name__}


def _check_database(settings: Settings) -> dict[str, str]:
    database_config = resolve_postgres_database_config(
        settings.database_url,
        schema=settings.langgraph_checkpoint_schema,
    )
    if database_config is None:
        return {"status": "skipped", "reason": "database_url_not_configured"}

    try:
        with psycopg.connect(database_config.url, connect_timeout=2) as connection:
            if not postgres_schema_exists(connection, database_config.schema):
                return {
                    "status": "error",
                    "reason": "database_schema_missing",
                    "type": "Postgres",
                }
            prepare_postgres_connection(connection, database_config.schema)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
    except psycopg.Error:
        return {"status": "error", "reason": "database_unavailable", "type": "Postgres"}

    result = {"status": "ok", "type": "Postgres"}
    if database_config.schema:
        result["schema"] = database_config.schema
    return result
