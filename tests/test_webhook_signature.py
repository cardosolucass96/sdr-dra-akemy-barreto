from __future__ import annotations

import asyncio
import gzip
import hashlib
import hmac

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.api.webhook_signature as webhook_signature
from app.api.request_body import RAW_REQUEST_BODY_SCOPE_KEY
from app.api.webhook_signature import (
    DEFAULT_SIGNATURE_HEADER,
    PIPEFACIL_TIMESTAMP_HEADER,
    verify_pipefacil_webhook_signature,
)
from app.core.config import Settings


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, object]]] = []

    def info(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("info", message, dict(kwargs.get("extra") or {})))

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("warning", message, dict(kwargs.get("extra") or {})))


def _request(
    body: bytes,
    *,
    headers: dict[str, str] | None = None,
    raw_body: bytes | None = None,
) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/events/message-received",
        "raw_path": b"/events/message-received",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in (headers or {}).items()
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }
    if raw_body is not None:
        scope[RAW_REQUEST_BODY_SCOPE_KEY] = raw_body

    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _settings(
    *,
    secret: str | None = "test-webhook-secret",
    enabled: bool = True,
    app_env: str = "development",
) -> Settings:
    return Settings(
        _env_file=None,
        app_env=app_env,
        pipefacil_webhook_signature_enabled=enabled,
        pipefacil_webhook_signature_secret=secret,
    )


def _pipefacil_signature(body: bytes, *, secret: str, timestamp: str) -> str:
    signed_payload = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _valid_headers(
    body: bytes,
    *,
    secret: str,
    timestamp: str = "1790253675698",
) -> dict[str, str]:
    return {
        DEFAULT_SIGNATURE_HEADER: _pipefacil_signature(body, secret=secret, timestamp=timestamp),
        PIPEFACIL_TIMESTAMP_HEADER: timestamp,
        "X-Pipefacil-Event": "message.received",
    }


def _verify(request: Request, settings: Settings) -> None:
    asyncio.run(verify_pipefacil_webhook_signature(request, settings))


@pytest.mark.parametrize(
    "settings",
    [
        _settings(enabled=False),
        _settings(secret=None),
        _settings(secret="   "),
    ],
)
def test_webhook_signature_can_be_explicitly_disabled_or_unconfigured(
    settings: Settings,
) -> None:
    _verify(_request(b"{}"), settings)


def test_webhook_signature_accepts_pipefacil_timestamp_and_json_hmac() -> None:
    body = b'{"type":"message.received"}'
    # CRM secrets are hexadecimal-looking strings, but Pipefacil uses their UTF-8 bytes.
    secret = "0f" * 32

    _verify(
        _request(body, headers=_valid_headers(body, secret=secret)),
        _settings(secret=secret),
    )


def test_webhook_signature_uses_uncompressed_json_when_request_was_gzipped() -> None:
    body = b'{"type":"message.received","data":{"text":"hello"}}'
    compressed_body = gzip.compress(body)
    secret = "test-webhook-secret"

    _verify(
        _request(
            body,
            raw_body=compressed_body,
            headers={
                **_valid_headers(body, secret=secret),
                "Content-Encoding": "gzip",
            },
        ),
        _settings(secret=secret),
    )


@pytest.mark.parametrize(
    "signature",
    [
        "0" * 64,
        "SHA256=" + "0" * 64,
        "sha256=" + "A" * 64,
        "sha256=" + "0" * 63,
        "sha256=" + "0" * 65,
        "sha256=invalid",
    ],
)
def test_webhook_signature_rejects_noncanonical_signature_header(signature: str) -> None:
    body = b"{}"
    headers = {
        DEFAULT_SIGNATURE_HEADER: signature,
        PIPEFACIL_TIMESTAMP_HEADER: "1790253675698",
    }

    with pytest.raises(HTTPException) as exc_info:
        _verify(_request(body, headers=headers), _settings())

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid webhook signature."


def test_webhook_signature_rejects_missing_signature_or_timestamp() -> None:
    body = b"{}"
    good_headers = _valid_headers(body, secret="test-webhook-secret")
    missing_signature = {PIPEFACIL_TIMESTAMP_HEADER: good_headers[PIPEFACIL_TIMESTAMP_HEADER]}
    missing_timestamp = {DEFAULT_SIGNATURE_HEADER: good_headers[DEFAULT_SIGNATURE_HEADER]}

    for headers in ({}, missing_signature, missing_timestamp):
        with pytest.raises(HTTPException) as exc_info:
            _verify(_request(body, headers=headers), _settings())
        assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    "legacy_format", ["body_hmac", "body_secret_digest", "event_timestamp_body"]
)
def test_webhook_signature_rejects_legacy_signature_formats(legacy_format: str) -> None:
    body = b'{"type":"message.received"}'
    secret = "0f" * 32
    timestamp = "1790253675698"

    if legacy_format == "body_hmac":
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    elif legacy_format == "body_secret_digest":
        digest = hashlib.sha256(body + secret.encode("utf-8")).hexdigest()
    else:
        signed_payload = b"message.received." + timestamp.encode("utf-8") + b"." + body
        digest = hmac.new(bytes.fromhex(secret), signed_payload, hashlib.sha256).hexdigest()

    with pytest.raises(HTTPException) as exc_info:
        _verify(
            _request(
                body,
                headers={
                    DEFAULT_SIGNATURE_HEADER: f"sha256={digest}",
                    PIPEFACIL_TIMESTAMP_HEADER: timestamp,
                    "X-Pipefacil-Event": "message.received",
                },
            ),
            _settings(secret=secret),
        )

    assert exc_info.value.status_code == 401


def test_webhook_signature_rejects_signature_for_different_body() -> None:
    secret = "test-webhook-secret"
    signed_body = b'{"message":"one"}'
    received_body = b'{"message":"two"}'

    with pytest.raises(HTTPException) as exc_info:
        _verify(
            _request(
                received_body,
                headers=_valid_headers(signed_body, secret=secret),
            ),
            _settings(secret=secret),
        )

    assert exc_info.value.status_code == 401


def test_signature_debug_logs_metadata_without_secret_or_signature_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(webhook_signature, "LOGGER", fake_logger)
    secret = "top-secret-value"
    body = b'{"message":"two"}'
    signature = _pipefacil_signature(body, secret=secret, timestamp="1790253675698")

    with pytest.raises(HTTPException):
        _verify(
            _request(
                b'{"message":"different"}',
                headers={
                    DEFAULT_SIGNATURE_HEADER: signature,
                    PIPEFACIL_TIMESTAMP_HEADER: "1790253675698",
                },
            ),
            _settings(secret=secret, app_env="development"),
        )

    diagnostic = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "info" and message == "pipefacil.webhook.signature_debug"
    )
    serialized = repr(diagnostic)

    assert secret not in serialized
    assert signature not in serialized
    assert diagnostic["signature_body_length"] == len(b'{"message":"different"}')


def test_signature_debug_is_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(webhook_signature, "LOGGER", fake_logger)
    body = b"{}"

    with pytest.raises(HTTPException):
        _verify(
            _request(
                body,
                headers={
                    DEFAULT_SIGNATURE_HEADER: "sha256=" + "0" * 64,
                    PIPEFACIL_TIMESTAMP_HEADER: "1790253675698",
                },
            ),
            _settings(app_env="production"),
        )

    assert [record[1] for record in fake_logger.records] == ["pipefacil.webhook.signature_rejected"]
