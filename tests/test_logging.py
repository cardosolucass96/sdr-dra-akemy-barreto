from __future__ import annotations

import json
import logging
import re
from io import StringIO

from app.core.config import Settings
from app.core.logging import MANAGED_HANDLER_ATTR, configure_logging, raw_log_value

ANSI_PATTERN = re.compile(r"\033\[[0-9;]*m")


class TTYStringIO(StringIO):
    def isatty(self) -> bool:
        return True


def _reset_app_logger() -> None:
    for logger_name in ("app", "uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        for handler in list(logger.handlers):
            if getattr(handler, MANAGED_HANDLER_ATTR, False):
                logger.removeHandler(handler)
                handler.close()
        logger.propagate = True


def _strip_ansi(value: str) -> str:
    return ANSI_PATTERN.sub("", value)


def test_configure_logging_emits_json_in_production() -> None:
    stream = StringIO()
    settings = Settings(app_env="production", log_level="INFO", log_format=None)

    try:
        configure_logging(settings, stream=stream)
        logging.getLogger("app.test").info(
            "Pipefacil outbound reply delivered.",
            extra={"thread_id": "thread-1", "status_code": 200},
        )

        payload = json.loads(stream.getvalue())

        assert payload["level"] == "INFO"
        assert payload["logger"] == "app.test"
        assert payload["message"] == "Pipefacil outbound reply delivered."
        assert payload["thread_id"] == "thread-1"
        assert payload["status_code"] == 200
    finally:
        _reset_app_logger()


def test_configure_logging_masks_sensitive_extra_fields() -> None:
    stream = StringIO()
    settings = Settings(app_env="production", log_level="INFO", log_format="json")

    try:
        configure_logging(settings, stream=stream)
        logging.getLogger("app.test").info(
            "Safe structured log.",
            extra={
                "api_key": "sk-test-secret",
                "contact_email": "lead@example.com",
                "contact_phone": "+55 11 91234-5678",
            },
        )

        payload = json.loads(stream.getvalue())

        assert payload["api_key"] == "[REDACTED]"
        assert payload["contact_email"] == "[EMAIL_REDACTED]"
        assert payload["contact_phone"] == "[PHONE_REDACTED]"
    finally:
        _reset_app_logger()


def test_raw_log_value_preserves_literal_payload() -> None:
    stream = StringIO()
    settings = Settings(app_env="production", log_level="INFO", log_format="json")

    try:
        configure_logging(settings, stream=stream)
        logging.getLogger("app.test").info(
            "pipefacil.webhook.received",
            extra={
                "raw_payload": raw_log_value(
                    {
                        "contact": {
                            "email": "lead@example.com",
                            "phone": "+55 11 91234-5678",
                        },
                        "api_key": "literal-key-for-debug",
                    }
                )
            },
        )

        payload = json.loads(stream.getvalue())

        assert payload["raw_payload"] == {
            "contact": {
                "email": "lead@example.com",
                "phone": "+55 11 91234-5678",
            },
            "api_key": "literal-key-for-debug",
        }
    finally:
        _reset_app_logger()


def test_log_inbound_payloads_setting_defaults_to_false() -> None:
    settings = Settings(_env_file=None)

    assert settings.log_inbound_payloads is False


def test_log_inbound_payloads_setting_accepts_true() -> None:
    settings = Settings(_env_file=None, log_inbound_payloads=True)

    assert settings.log_inbound_payloads is True


def test_openai_specialists_settings_default_to_disabled() -> None:
    settings = Settings(_env_file=None)

    assert settings.openai_specialists_enabled is False
    assert settings.openai_specialist_model is None
    assert settings.openai_specialist_max_turns == 8


def test_configure_logging_defaults_to_text_outside_production() -> None:
    stream = StringIO()
    settings = Settings(app_env="development", log_level="INFO", log_format=None)

    try:
        configure_logging(settings, stream=stream)
        logging.getLogger("app.test").info(
            "Application startup completed.",
            extra={"app_env": "development"},
        )

        output = stream.getvalue()
        line = _strip_ansi(output)

        assert "INF test      startup.completed |" in line
        assert "env=development" in line
        assert "\033[" not in output
    finally:
        _reset_app_logger()


def test_text_logging_compacts_pipeline_fields() -> None:
    stream = StringIO()
    settings = Settings(
        app_env="development",
        log_level="INFO",
        log_format="text",
    )
    long_message_id = "wamid.EXAMPLE_MESSAGE_000000000000000000000000000000000000000000000004"

    try:
        configure_logging(settings, stream=stream)
        logging.getLogger("app.application.pipefacil").info(
            "pipefacil.outbound.delivered",
            extra={
                "delivery_status": "sent",
                "event_type": "message.received",
                "external_message_id": long_message_id,
                "has_media": True,
                "media_keys": ["id", "mimeType", "size", "url"],
                "media_mime_type": "image/jpeg",
                "message_id": "729563d6-62d8-4fff-8312-4844ff1a20c6",
                "message_type": "image",
                "pipeline_run_id": long_message_id,
                "pipeline_step": "pipefacil.outbound.delivered",
                "request_id": "374acecb-6ece-40b2-94dd-a38c8a5b6c24",
                "status_code": 201,
                "thread_id": "deal-example-001",
                "user_id": "[PHONE_REDACTED]",
            },
        )

        output = stream.getvalue()
        line = _strip_ansi(output)

        assert "INF outbound  delivered |" in line
        assert "[app.application.pipefacil]" not in line
        assert "pipeline_step=" not in line
        assert "external=" not in line
        assert "event=" not in line
        assert "msg=" not in line
        assert "user=" not in line
        assert "type=image" in line
        assert "media=true" in line
        assert "mime=image/jpeg" in line
        assert "media_keys=[id,mimeType,size,url]" in line
        assert "delivery=sent" in line
        assert "status=201" in line
        assert "run=wamid.EXAMPLE_MESSAGE_00...000000000004" in line
        assert "\033[" not in output
    finally:
        _reset_app_logger()


def test_configure_logging_formats_uvicorn_access_like_application_logs() -> None:
    stream = StringIO()
    settings = Settings(app_env="staging", log_level="INFO", log_format="text")

    try:
        configure_logging(settings, stream=stream)
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "10.0.1.10:37098",
            "POST",
            "/events/conversation-start?campanha=790",
            "1.1",
            200,
        )

        line = _strip_ansi(stream.getvalue())

        assert "INF http      POST /events/conversation-start |" in line
        assert "status=200" in line
        assert "client=10.0.1.10:37098" in line
        assert "campanha=790" not in line
    finally:
        _reset_app_logger()


def test_configure_logging_formats_uvicorn_server_logs() -> None:
    stream = StringIO()
    settings = Settings(app_env="staging", log_level="INFO", log_format="text")

    try:
        configure_logging(settings, stream=stream)
        logging.getLogger("uvicorn").info("Started server process")

        line = _strip_ansi(stream.getvalue())
        assert "INF server    Started server process" in line
    finally:
        _reset_app_logger()


def test_configure_logging_handles_malformed_uvicorn_access_records() -> None:
    stream = StringIO()
    settings = Settings(app_env="staging", log_level="INFO", log_format="text")

    try:
        configure_logging(settings, stream=stream)
        access_logger = logging.getLogger("uvicorn.access")
        access_logger.info("access record without structured arguments")
        access_logger.info(
            "%s %s %s %s %s",
            "127.0.0.1:8000",
            "GET",
            "/health",
            "1.1",
            "unknown",
        )

        lines = [_strip_ansi(line) for line in stream.getvalue().splitlines()]
        assert len(lines) == 2
        assert "access" in lines[0]
        assert "access" in lines[1]
    finally:
        _reset_app_logger()


def test_configure_logging_hides_successful_probe_access_logs() -> None:
    stream = StringIO()
    settings = Settings(app_env="staging", log_level="INFO", log_format="text")

    try:
        configure_logging(settings, stream=stream)
        access_logger = logging.getLogger("uvicorn.access")
        access_logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:54826",
            "GET",
            "/ready",
            "1.1",
            200,
        )
        assert stream.getvalue() == ""

        access_logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:54826",
            "GET",
            "/ready",
            "1.1",
            503,
        )

        line = _strip_ansi(stream.getvalue())
        assert "GET /ready" in line
        assert "status=503" in line
    finally:
        _reset_app_logger()


def test_configure_logging_preserves_uvicorn_no_access_log() -> None:
    stream = StringIO()
    settings = Settings(app_env="staging", log_level="INFO", log_format="text")
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = False

    try:
        configure_logging(settings, stream=stream)

        assert access_logger.handlers == []
        assert access_logger.propagate is False
    finally:
        _reset_app_logger()


def test_text_logging_compacts_partial_message_parts() -> None:
    stream = StringIO()
    settings = Settings(app_env="staging", log_level="INFO", log_format="text")

    try:
        configure_logging(settings, stream=stream)
        logger = logging.getLogger("app.application.pipefacil")
        logger.info("pipefacil.outbound.delivered", extra={"message_part_index": 2})
        logger.info("pipefacil.outbound.delivered", extra={"message_part_count": 5})

        output = _strip_ansi(stream.getvalue())
        assert "part=2" in output
        assert "parts=5" in output
    finally:
        _reset_app_logger()


def test_text_logging_uses_colors_only_for_tty_streams(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = TTYStringIO()
    settings = Settings(app_env="staging", log_level="INFO", log_format="text")

    try:
        configure_logging(settings, stream=stream)
        logging.getLogger("app.test").info("tty output")

        assert "\033[32mINF\033[0m" in stream.getvalue()
    finally:
        _reset_app_logger()


def test_text_logging_honors_no_color(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = TTYStringIO()
    settings = Settings(app_env="staging", log_level="INFO", log_format="text")

    try:
        configure_logging(settings, stream=stream)
        logging.getLogger("app.test").info("no color output")

        assert "\033[" not in stream.getvalue()
    finally:
        _reset_app_logger()


def test_configure_logging_formats_uvicorn_access_as_structured_json() -> None:
    stream = StringIO()
    settings = Settings(app_env="production", log_level="INFO", log_format="json")

    try:
        configure_logging(settings, stream=stream)
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "10.0.1.10:37098",
            "POST",
            "/events/conversation-start?campanha=790",
            "1.1",
            200,
        )

        payload = json.loads(stream.getvalue())

        assert payload["message"] == "http.access"
        assert payload["http_method"] == "POST"
        assert payload["http_path"] == "/events/conversation-start"
        assert payload["status_code"] == 200
        assert payload["client"] == "10.0.1.10:37098"
    finally:
        _reset_app_logger()
