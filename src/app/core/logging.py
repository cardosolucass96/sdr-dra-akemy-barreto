from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TextIO
from urllib.parse import urlsplit

from app.core.config import Settings

MANAGED_HANDLER_ATTR = "_sdr_pipefacil_managed_handler"
SUPPORTED_LOG_FORMATS = {"json", "text"}

EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")
PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d[\d(). -]{8,}\d(?!\w)")
SENSITIVE_FIELD_HINTS = (
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "password",
    "token",
    "private_key",
    "public_key",
)
STANDARD_RECORD_ATTRIBUTES = set(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
}
TEXT_FIELD_ALIASES = {
    "agent_status": "agent",
    "app_env": "env",
    "app_version": "version",
    "automation_event_type": "event",
    "client": "client",
    "contact_id": "contact",
    "content_length": "content_length",
    "content_encoding": "encoding",
    "content_type": "content_type",
    "deal_seq": "deal",
    "delivery_status": "delivery",
    "duration_ms": "ms",
    "error_code": "error",
    "event_type": "event",
    "external_message_id": "external",
    "http_method": "method",
    "http_path": "path",
    "http_version": "http",
    "message_id": "msg",
    "message_part_count": "parts",
    "message_part_index": "part",
    "message_part_type": "part_type",
    "message_type": "type",
    "media_content_type": "content",
    "has_media": "media",
    "media_duration": "duration",
    "media_id": "media_id",
    "media_keys": "media_keys",
    "media_mime_type": "mime",
    "media_size": "size",
    "media_type": "media_type",
    "message_body_length": "body_length",
    "pipeline_run_id": "run",
    "request_header_names": "headers",
    "request_id": "request",
    "specialist_confidence": "confidence",
    "specialist_error_code": "specialist_error",
    "specialist_name": "specialist",
    "specialist_status": "specialist_status",
    "status_code": "status",
    "thread_id": "thread",
    "transcription_length": "transcription_length",
    "user_agent": "ua",
    "user_id": "user",
    "validation_error_count": "validation_errors",
    "validation_error_locations": "locations",
    "validation_error_types": "types",
    "generated_audio_attempt_count": "audio_attempts",
    "generated_audio_content_type": "audio_content",
    "generated_audio_explicit": "audio_explicit",
    "generated_audio_media_id": "audio_media",
    "generated_audio_text_length": "audio_chars",
    "part": "part",
    "parts": "parts",
}
TEXT_FIELD_ORDER = (
    "thread_id",
    "contact_id",
    "deal_seq",
    "user_id",
    "message_type",
    "has_media",
    "media_type",
    "media_mime_type",
    "media_size",
    "media_duration",
    "transcription_length",
    "intent",
    "specialist_name",
    "specialist_status",
    "specialist_confidence",
    "specialist_error_code",
    "agent_status",
    "delivery_status",
    "status_code",
    "error_code",
    "client",
    "request_id",
    "message_id",
    "pipeline_run_id",
    "external_message_id",
    "event_type",
    "automation_event_type",
    "media_id",
    "media_keys",
    "message_body_length",
    "part",
    "parts",
    "message_part_index",
    "message_part_count",
    "message_part_type",
    "media_content_type",
    "generated_audio_media_id",
    "generated_audio_content_type",
    "generated_audio_text_length",
    "generated_audio_attempt_count",
    "generated_audio_explicit",
    "http_method",
    "http_path",
    "http_version",
    "duration_ms",
    "content_type",
    "content_encoding",
    "content_length",
    "user_agent",
    "request_header_names",
    "validation_error_count",
    "validation_error_locations",
    "validation_error_types",
    "app_env",
    "app_version",
    "checkpointer",
)
TEXT_VALUE_MAX_LENGTH = 48
TEXT_VALUE_HEAD_LENGTH = 24
TEXT_VALUE_TAIL_LENGTH = 12
PLAIN_TEXT_VALUE_PATTERN = re.compile(r"^[^\s\"'=]+$")
TEXT_LEVEL_NAMES = {
    logging.DEBUG: "DBG",
    logging.INFO: "INF",
    logging.WARNING: "WRN",
    logging.ERROR: "ERR",
    logging.CRITICAL: "CRT",
}
TEXT_MESSAGE_ALIASES = {
    "Application startup started.": "startup.started",
    "Application startup completed.": "startup.completed",
    "Application shutdown completed.": "shutdown.completed",
}
TEXT_EVENT_PREFIXES = (
    ("http.", "http"),
    ("pipefacil.webhook.", "webhook"),
    ("pipefacil.inbound.", "inbound"),
    ("pipefacil.outbound.", "outbound"),
    ("pipefacil.deal.", "deal"),
    ("pipefacil.automation_start.", "auto"),
    ("agent.run.", "agent"),
    ("specialist.run.", "specialist"),
)
ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_CYAN = "\033[36m"
TEXT_LEVEL_COLORS = {
    logging.DEBUG: ANSI_DIM,
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}
TEXT_SOURCE_COLORS = {
    "auto": ANSI_CYAN,
    "deal": "\033[33m",
    "http": ANSI_CYAN,
    "main": ANSI_CYAN,
    "webhook": ANSI_CYAN,
    "inbound": "\033[34m",
    "agent": "\033[35m",
    "specialist": "\033[35m",
    "outbound": "\033[32m",
}
UVICORN_ACCESS_LOGGER = "uvicorn.access"
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", UVICORN_ACCESS_LOGGER)
QUIET_ACCESS_PATHS = {"/health", "/ready", "/docs", "/openapi.json"}
LOGGER_SOURCE_ALIASES = {
    "uvicorn": "server",
    "uvicorn.error": "server",
}


@dataclass(frozen=True)
class RawLogValue:
    value: Any


def raw_log_value(value: Any) -> RawLogValue:
    return RawLogValue(value=value)


def _mask_string(value: str) -> str:
    masked = EMAIL_PATTERN.sub("[EMAIL_REDACTED]", value)

    def replace_phone(match: re.Match[str]) -> str:
        candidate = match.group(0)
        digits = re.sub(r"\D", "", candidate)
        if 10 <= len(digits) <= 15:
            return "[PHONE_REDACTED]"
        return candidate

    return PHONE_PATTERN.sub(replace_phone, masked)


def _is_sensitive_field(key: str) -> bool:
    normalized_key = key.lower()
    return any(hint in normalized_key for hint in SENSITIVE_FIELD_HINTS)


def _json_safe(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if isinstance(value, RawLogValue):
        return _raw_json_safe(value.value, depth=depth)

    if _is_sensitive_field(key):
        return "[REDACTED]"

    if depth > 3:
        return str(value)

    if isinstance(value, str):
        return _mask_string(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, dict):
        return {
            str(item_key): _json_safe(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [_json_safe(item, depth=depth + 1) for item in value]

    return str(value)


def _raw_json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return str(value)

    if isinstance(value, str | bool | int | float) or value is None:
        return value
    if isinstance(value, dict):
        return {
            str(item_key): _raw_json_safe(item_value, depth=depth + 1)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [_raw_json_safe(item, depth=depth + 1) for item in value]

    return str(value)


def _record_extra(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: _json_safe(value, key=key)
        for key, value in record.__dict__.items()
        if key not in STANDARD_RECORD_ATTRIBUTES and not key.startswith("_")
    }


def _timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, tz=UTC).isoformat().replace("+00:00", "Z")


def _text_timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S.%f")[:12] + "Z"


def _short_logger_name(logger_name: str) -> str:
    if logger_name in LOGGER_SOURCE_ALIASES:
        return LOGGER_SOURCE_ALIASES[logger_name]
    if logger_name == "app":
        return "app"
    if logger_name.startswith("app."):
        return logger_name.removeprefix("app.")
    return logger_name


def _text_level_name(levelno: int) -> str:
    return TEXT_LEVEL_NAMES.get(levelno, logging.getLevelName(levelno)[:3])


def _text_source_and_event(logger_name: str, message: str) -> tuple[str, str]:
    if logger_name == UVICORN_ACCESS_LOGGER:
        return "http", "access"

    aliased_message = TEXT_MESSAGE_ALIASES.get(message, message)
    for prefix, source in TEXT_EVENT_PREFIXES:
        if aliased_message.startswith(prefix):
            return source, aliased_message.removeprefix(prefix)

    return _short_logger_name(logger_name), aliased_message


def _uvicorn_access_log_context(record: logging.LogRecord) -> dict[str, Any] | None:
    if record.name != UVICORN_ACCESS_LOGGER or not isinstance(record.args, tuple):
        return None
    if len(record.args) < 5:
        return None

    client_addr, http_method, full_path, http_version, status_code = record.args[:5]
    try:
        normalized_status_code = int(status_code)
    except (TypeError, ValueError):
        return None

    normalized_path = urlsplit(str(full_path)).path or "/"
    return {
        "client": str(client_addr),
        "http_method": str(http_method),
        "http_path": normalized_path,
        "http_version": str(http_version),
        "status_code": normalized_status_code,
    }


class UvicornAccessNoiseFilter(logging.Filter):
    """Hide successful probe traffic while keeping failed probes visible."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = _uvicorn_access_log_context(record)
        if context is None:
            return True

        return not (context["http_path"] in QUIET_ACCESS_PATHS and context["status_code"] < 400)


def _style_text(value: str, ansi_code: str, *, enabled: bool) -> str:
    if not enabled:
        return value
    return f"{ansi_code}{value}{ANSI_RESET}"


def _shorten_text_string(value: str) -> str:
    if len(value) <= TEXT_VALUE_MAX_LENGTH:
        return value
    return f"{value[:TEXT_VALUE_HEAD_LENGTH]}...{value[-TEXT_VALUE_TAIL_LENGTH:]}"


def _format_text_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        shortened_value = _shorten_text_string(value)
        if PLAIN_TEXT_VALUE_PATTERN.fullmatch(shortened_value):
            return shortened_value
        return json.dumps(shortened_value, ensure_ascii=False)
    if isinstance(value, list | tuple | set):
        sequence_items: list[str] = []
        for item in value:
            formatted_item = _format_text_sequence_item(item)
            if formatted_item is None:
                break
            sequence_items.append(formatted_item)
        else:
            return _shorten_text_string(f"[{','.join(sequence_items)}]")

    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(_shorten_text_string(serialized), ensure_ascii=False)


def _format_text_sequence_item(value: Any) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        shortened_value = _shorten_text_string(value)
        if PLAIN_TEXT_VALUE_PATTERN.fullmatch(shortened_value):
            return shortened_value
        return json.dumps(shortened_value, ensure_ascii=False)

    return None


def _merge_access_context(payload: dict[str, Any], record: logging.LogRecord) -> None:
    access_context = _uvicorn_access_log_context(record)
    if access_context is None:
        return

    payload.update(access_context)
    payload.pop("http_method", None)
    payload.pop("http_path", None)
    payload.pop("http_version", None)


def _compact_message_parts(payload: dict[str, Any]) -> None:
    message_part_index = payload.pop("message_part_index", None)
    message_part_count = payload.pop("message_part_count", None)
    if message_part_index is not None and message_part_count is not None:
        payload["part"] = f"{message_part_index}/{message_part_count}"
    elif message_part_index is not None:
        payload["part"] = message_part_index
    elif message_part_count is not None:
        payload["parts"] = message_part_count


def _text_record_extra(record: logging.LogRecord) -> dict[str, Any]:
    payload = _record_extra(record)
    message = record.getMessage()
    _merge_access_context(payload, record)

    if payload.get("pipeline_step") == message:
        payload.pop("pipeline_step")
    if "external_message_id" in payload and payload.get("external_message_id") == payload.get(
        "pipeline_run_id"
    ):
        payload.pop("external_message_id")
    if payload.get("event_type") == "message.received":
        payload.pop("event_type")
    if "thread_id" in payload:
        payload.pop("message_id", None)
    if payload.get("user_id") == "[PHONE_REDACTED]":
        payload.pop("user_id")
    if message == "http.request.started":
        payload.pop("request_header_names", None)

    _compact_message_parts(payload)

    return {key: value for key, value in payload.items() if value is not None}


def _format_text_details(payload: dict[str, Any], *, use_colors: bool) -> str:
    ordered_keys = [key for key in TEXT_FIELD_ORDER if key in payload]
    ordered_keys.extend(sorted(key for key in payload if key not in TEXT_FIELD_ORDER))
    details: list[str] = []

    for key in ordered_keys:
        formatted_value = _format_text_value(payload[key])
        if formatted_value is None:
            continue
        display_key = TEXT_FIELD_ALIASES.get(key, key)
        display_key = _style_text(display_key, ANSI_DIM, enabled=use_colors)
        details.append(f"{display_key}={formatted_value}")

    return " ".join(details)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        access_context = _uvicorn_access_log_context(record)
        payload: dict[str, Any] = {
            "timestamp": _timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": "http.access" if access_context is not None else record.getMessage(),
        }
        payload.update(_record_extra(record))
        if access_context is not None:
            payload.update(access_context)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self, *, use_colors: bool = False) -> None:
        super().__init__()
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload = _text_record_extra(record)
        details = _format_text_details(payload, use_colors=self.use_colors)
        source, event = _text_source_and_event(record.name, message)
        access_context = _uvicorn_access_log_context(record)
        if access_context is not None:
            event = f"{access_context['http_method']} {access_context['http_path']}"

        timestamp = _style_text(_text_timestamp(record), ANSI_DIM, enabled=self.use_colors)
        level = _style_text(
            f"{_text_level_name(record.levelno):<3}",
            TEXT_LEVEL_COLORS.get(record.levelno, ANSI_DIM),
            enabled=self.use_colors,
        )
        source_column = _style_text(
            f"{source[:9]:<9}",
            TEXT_SOURCE_COLORS.get(source, ANSI_CYAN),
            enabled=self.use_colors,
        )
        event = _style_text(event, ANSI_BOLD, enabled=self.use_colors)
        base = f"{timestamp} {level} {source_column} {event}"
        if details:
            base = f"{base} | {details}"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            base = f"{base}\n{self.formatStack(record.stack_info)}"

        return base


def _resolve_log_level(value: str | None) -> int:
    normalized_value = (value or "INFO").strip().upper()
    resolved_level = getattr(logging, normalized_value, logging.INFO)
    if isinstance(resolved_level, int):
        return resolved_level

    return logging.INFO


def _resolve_log_format(settings: Settings) -> str:
    configured_format = (settings.log_format or "").strip().lower()
    if configured_format in SUPPORTED_LOG_FORMATS:
        return configured_format

    if settings.app_env.strip().lower() == "production":
        return "json"

    return "text"


def _stream_supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False

    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _build_formatter(settings: Settings, *, stream: TextIO) -> logging.Formatter:
    resolved_format = _resolve_log_format(settings)
    if resolved_format == "json":
        return JsonFormatter()

    return TextFormatter(use_colors=_stream_supports_color(stream))


def _replace_logger_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        if getattr(handler, MANAGED_HANDLER_ATTR, False):
            handler.close()


def _build_handler(
    settings: Settings,
    *,
    stream: TextIO,
    level: int,
) -> logging.Handler:
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(_build_formatter(settings, stream=stream))
    setattr(handler, MANAGED_HANDLER_ATTR, True)
    return handler


def configure_logging(settings: Settings, *, stream: TextIO | None = None) -> None:
    logger = logging.getLogger("app")
    level = _resolve_log_level(settings.log_level)

    for handler in list(logger.handlers):
        if getattr(handler, MANAGED_HANDLER_ATTR, False):
            logger.removeHandler(handler)
            handler.close()

    resolved_stream = stream or sys.stderr
    handler = _build_handler(settings, stream=resolved_stream, level=level)

    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False

    uvicorn_logger = logging.getLogger(UVICORN_LOGGERS[0])
    error_logger = logging.getLogger(UVICORN_LOGGERS[1])
    access_logger = logging.getLogger(UVICORN_LOGGERS[2])
    access_logging_enabled = not access_logger.disabled and (
        bool(access_logger.handlers) or access_logger.propagate
    )
    for uvicorn_logger_instance in (
        uvicorn_logger,
        access_logger,
        error_logger,
    ):
        _replace_logger_handlers(uvicorn_logger_instance)
        uvicorn_logger_instance.setLevel(level)

    uvicorn_logger.addHandler(_build_handler(settings, stream=resolved_stream, level=level))
    if access_logging_enabled:
        access_handler = _build_handler(settings, stream=resolved_stream, level=level)
        access_handler.addFilter(UvicornAccessNoiseFilter())
        access_logger.addHandler(access_handler)

    uvicorn_logger.propagate = False
    access_logger.propagate = False
    error_logger.propagate = True
