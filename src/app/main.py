from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.agent import AgentGraphRuntime, build_runtime
from app.api.request_body import (
    DECODED_REQUEST_BODY_SCOPE_KEY,
    RAW_REQUEST_BODY_SCOPE_KEY,
    EventRequestMiddleware,
)
from app.api.router import build_api_router
from app.application.idempotency import (
    InMemoryMessageIdempotencyStore,
    MessageIdempotencyStore,
)
from app.application.runtime_settings import InMemoryRuntimeSettingsStore, RuntimeSettingsService
from app.core import (
    RuntimeConfigurationError,
    RuntimeSettings,
    Settings,
    build_execution_settings,
    configure_logging,
    get_bootstrap_settings,
)
from app.integrations.postgres_idempotency import PostgresMessageIdempotencyStore
from app.integrations.postgres_runtime_settings import PostgresRuntimeSettingsStore
from app.observability import flush_langfuse, warm_up_langfuse

LOGGER = logging.getLogger(__name__)
MESSAGE_RECEIVED_PATH = "/events/message-received"
MAX_VALIDATION_LOG_ITEMS = 8
MAX_VALIDATION_BODY_SHAPE_ITEMS = 80
MAX_VALIDATION_BODY_SHAPE_DEPTH = 5
APPLICATION_ENV = "production"


def _is_production_environment(app_env: str) -> bool:
    return app_env.strip().lower() == "production"


def _validate_startup_settings(settings: Settings) -> None:
    _validate_production_settings(settings)
    _validate_settings_access(settings)
    _validate_generated_audio_settings(settings)


def _validate_production_settings(settings: Settings) -> None:
    if not _is_production_environment(settings.app_env):
        return
    if not (settings.pipefacil_api_key or "").strip():
        raise RuntimeConfigurationError("PIPEFACIL_API_KEY is required in production.")
    if not settings.pipefacil_webhook_signature_enabled:
        raise RuntimeConfigurationError(
            "Webhook signature verification must remain enabled in production."
        )
    if not (settings.pipefacil_webhook_signature_secret or "").strip():
        raise RuntimeConfigurationError(
            "PIPEFACIL_WEBHOOK_SIGNATURE_SECRET is required in production."
        )
    if not (settings.database_url or "").strip():
        raise RuntimeConfigurationError("DATABASE_URL is required in production.")
    if not (settings.openai_api_key or "").strip():
        raise RuntimeConfigurationError("OPENAI_API_KEY is required in production.")


def _validate_settings_access(settings: Settings) -> None:
    if not (settings.settings_admin_key_hash or "").strip():
        raise RuntimeConfigurationError("SETTINGS_ADMIN_KEY_HASH is required.")
    if not (settings.settings_session_secret or "").strip():
        raise RuntimeConfigurationError("SETTINGS_SESSION_SECRET is required.")


def _validate_generated_audio_settings(settings: Settings) -> None:
    if not settings.generated_audio_enabled:
        return
    if not (settings.elevenlabs_api_key or "").strip():
        raise RuntimeConfigurationError(
            "ELEVENLABS_API_KEY is required when GENERATED_AUDIO_ENABLED=true."
        )
    if not (settings.elevenlabs_voice_id or "").strip():
        raise RuntimeConfigurationError(
            "ELEVENLABS_VOICE_ID is required when GENERATED_AUDIO_ENABLED=true."
        )
    public_base_url = _generated_audio_public_base_url(settings)
    if not public_base_url:
        raise RuntimeConfigurationError(
            "GENERATED_AUDIO_PUBLIC_BASE_URL or CLOUDFLARE_TUNNEL_HOSTNAME is required "
            "when GENERATED_AUDIO_ENABLED=true."
        )
    parsed_url = urlparse(public_base_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise RuntimeConfigurationError(
            "Generated audio public base URL must be absolute HTTPS when "
            "GENERATED_AUDIO_ENABLED=true."
        )


def _generated_audio_public_base_url(settings) -> str | None:
    configured_url = (settings.generated_audio_public_base_url or "").strip()
    if configured_url:
        return configured_url

    hostname = (settings.cloudflare_tunnel_hostname or "").strip()
    if hostname:
        return hostname if "://" in hostname else f"https://{hostname}"

    return None


def _build_pipefacil_message_idempotency_store(
    runtime: AgentGraphRuntime,
) -> MessageIdempotencyStore:
    if runtime.database_pool is None:
        return InMemoryMessageIdempotencyStore()

    store = PostgresMessageIdempotencyStore(
        runtime.database_pool,
        schema=runtime.database_schema,
    )
    store.setup()
    return store


def _build_runtime_settings_service(
    runtime: AgentGraphRuntime,
    *,
    bootstrap,
    app_env: str,
) -> RuntimeSettingsService:
    if runtime.database_pool is None:
        store = InMemoryRuntimeSettingsStore()
    else:
        store = PostgresRuntimeSettingsStore(
            runtime.database_pool,
            schema=runtime.database_schema,
        )
    store.setup()
    return RuntimeSettingsService(store, bootstrap, app_env=app_env)


def _route_path(request: Request) -> str:
    return getattr(request.scope.get("route"), "path", request.url.path)


def _validation_error_path(error: dict[str, Any]) -> str:
    location = error.get("loc", ())
    if isinstance(location, list | tuple):
        return ".".join(str(part) for part in location)
    return str(location)


def _validation_error_values(
    errors: list[dict[str, Any]],
    *,
    key: str,
) -> list[str]:
    values: list[str] = []
    for error in errors[:MAX_VALIDATION_LOG_ITEMS]:
        value = error.get(key)
        if value is not None:
            values.append(str(value))
    return values


def _validation_error_locations(errors: list[dict[str, Any]]) -> list[str]:
    return [_validation_error_path(error) for error in errors[:MAX_VALIDATION_LOG_ITEMS]]


def _json_type_name(value: Any) -> str:
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def _json_key_paths(
    value: Any,
    *,
    prefix: str = "",
    depth: int = 0,
    paths: list[str] | None = None,
) -> list[str]:
    resolved_paths = paths if paths is not None else []
    if len(resolved_paths) >= MAX_VALIDATION_BODY_SHAPE_ITEMS:
        return resolved_paths
    if depth >= MAX_VALIDATION_BODY_SHAPE_DEPTH:
        return resolved_paths

    if isinstance(value, Mapping):
        for key in sorted(str(item_key) for item_key in value):
            path = f"{prefix}.{key}" if prefix else key
            resolved_paths.append(path)
            if len(resolved_paths) >= MAX_VALIDATION_BODY_SHAPE_ITEMS:
                return resolved_paths
            _json_key_paths(
                value.get(key),
                prefix=path,
                depth=depth + 1,
                paths=resolved_paths,
            )
        return resolved_paths

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        if not value:
            return resolved_paths
        path = f"{prefix}[]" if prefix else "[]"
        resolved_paths.append(path)
        if len(resolved_paths) >= MAX_VALIDATION_BODY_SHAPE_ITEMS:
            return resolved_paths
        return _json_key_paths(
            value[0],
            prefix=path,
            depth=depth + 1,
            paths=resolved_paths,
        )

    return resolved_paths


def _validation_body_shape(payload: Any) -> dict[str, Any]:
    context: dict[str, Any] = {
        "request_json_root_type": _json_type_name(payload),
        "request_json_key_paths": _json_key_paths(payload),
    }
    if isinstance(payload, Mapping):
        data = payload.get("data")
        if isinstance(data, Mapping):
            context["request_json_data_keys"] = sorted(str(key) for key in data)
    return context


async def _request_validation_body_shape(request: Request) -> dict[str, Any]:
    body = request.scope.get(DECODED_REQUEST_BODY_SCOPE_KEY)
    if not isinstance(body, bytes):
        body = request.scope.get(RAW_REQUEST_BODY_SCOPE_KEY)
    if not isinstance(body, bytes):
        try:
            body = await request.body()
        except RuntimeError:
            return {"request_json_unavailable": True}

    if not body:
        return {"request_json_root_type": "empty"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"request_json_root_type": "invalid_json"}

    return _validation_body_shape(payload)


def create_app(*, app_env: str | None = None) -> FastAPI:
    resolved_app_env = app_env or APPLICATION_ENV
    bootstrap = get_bootstrap_settings()
    initial_settings = build_execution_settings(bootstrap, RuntimeSettings()).model_copy(
        update={"app_env": resolved_app_env}
    )
    configure_logging(initial_settings)
    is_production = _is_production_environment(initial_settings.app_env)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        LOGGER.info(
            "Application startup started.",
            extra={
                "app_env": initial_settings.app_env,
                "app_version": initial_settings.app_version,
            },
        )
        _validate_production_settings(initial_settings)
        _validate_settings_access(initial_settings)
        runtime = build_runtime(initial_settings)
        try:
            idempotency_store = _build_pipefacil_message_idempotency_store(runtime)
            runtime_settings_service = _build_runtime_settings_service(
                runtime,
                bootstrap=bootstrap,
                app_env=resolved_app_env,
            )
            settings = runtime_settings_service.get_execution_settings()
            _validate_generated_audio_settings(settings)
            warm_up_langfuse(settings)
        except Exception:
            runtime.close()
            raise
        app.state.graph_runtime = runtime
        app.state.graph = runtime.graph
        app.state.checkpointer = runtime.checkpointer
        app.state.pipefacil_message_idempotency_store = idempotency_store
        app.state.bootstrap_settings = bootstrap
        app.state.runtime_settings_service = runtime_settings_service
        app.state.settings = settings
        if isinstance(idempotency_store, InMemoryMessageIdempotencyStore):
            LOGGER.warning(
                "pipefacil.webhook.idempotency_memory_store",
                extra={
                    "pipeline_step": "pipefacil.webhook.idempotency_memory_store",
                    "pipefacil_idempotency_store": type(idempotency_store).__name__,
                    "pipefacil_idempotency_scope": "process",
                    "pipefacil_idempotency_restart_safe": False,
                    "pipefacil_idempotency_multi_replica_safe": False,
                },
            )
        LOGGER.info(
            "Application startup completed.",
            extra={
                "app_env": settings.app_env,
                "app_version": settings.app_version,
                "checkpointer": type(runtime.checkpointer).__name__,
                "pipefacil_idempotency_store": type(idempotency_store).__name__,
                "pipefacil_webhook_signature_enabled": (
                    settings.pipefacil_webhook_signature_enabled
                ),
            },
        )

        try:
            yield
        finally:
            runtime.close()
            flush_langfuse(runtime_settings_service.get_execution_settings())
            LOGGER.info("Application shutdown completed.")

    app = FastAPI(
        title=initial_settings.app_name,
        version=initial_settings.app_version,
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=bootstrap.settings_session_secret or "settings-session-not-configured",
        session_cookie="sdr_settings_session",
        max_age=43_200,
        same_site="lax",
        https_only=is_production,
    )
    app.mount(
        "/settings/static",
        StaticFiles(directory=str(Path(__file__).resolve().parent / "api" / "static")),
        name="settings-static",
    )
    app.add_middleware(EventRequestMiddleware)

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception(
            "Unhandled application error.",
            extra={
                "http_method": request.method,
                "http_path": _route_path(request),
                "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error_code": "unhandled_application_error",
            },
            exc_info=exc,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal Server Error"},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        errors = exc.errors()
        http_path = _route_path(request)
        pipeline_step = (
            "pipefacil.webhook.validation_failed"
            if http_path == MESSAGE_RECEIVED_PATH
            else "http.request.validation_failed"
        )
        log_extra = {
            "pipeline_step": pipeline_step,
            "error_code": "request_validation_error",
            "http_method": request.method,
            "http_path": http_path,
            "content_type": request.headers.get("content-type"),
            "content_length": request.headers.get("content-length"),
            "status_code": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "validation_error_count": len(errors),
            "validation_error_locations": _validation_error_locations(errors),
            "validation_error_types": _validation_error_values(errors, key="type"),
        }
        if http_path == MESSAGE_RECEIVED_PATH:
            log_extra.update(await _request_validation_body_shape(request))

        LOGGER.warning(
            pipeline_step,
            extra=log_extra,
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": jsonable_encoder(errors)},
        )

    app.include_router(build_api_router(include_internal_routes=not is_production))
    return app


app = create_app()
