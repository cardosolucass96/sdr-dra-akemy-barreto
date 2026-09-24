from __future__ import annotations

from typing import Any

from fastapi import Request

from app.application.idempotency import MessageIdempotencyStore
from app.application.runtime_settings import RuntimeSettingsService
from app.core.config import Settings
from app.core.logging import configure_logging


def get_graph(request: Request) -> Any:
    return request.app.state.graph


def get_settings(request: Request) -> Settings:
    settings = get_runtime_settings_service(request).get_execution_settings()
    configure_logging(settings)
    return settings


def get_runtime_settings_service(request: Request) -> RuntimeSettingsService:
    return request.app.state.runtime_settings_service


def get_pipefacil_message_idempotency_store(request: Request) -> MessageIdempotencyStore:
    return request.app.state.pipefacil_message_idempotency_store


def get_liana_pipefacil_sync_handler(request: Request):
    return getattr(request.app.state, "liana_pipefacil_sync_handler", None)


def get_liana_pipefacil_sync_enqueue(request: Request):
    return getattr(request.app.state, "liana_pipefacil_sync_enqueue", None)
