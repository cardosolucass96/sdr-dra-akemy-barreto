from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Annotated, NoReturn

from fastapi import Depends, HTTPException, Request, status
from starlette.datastructures import Headers

from app.core.config import Settings, get_settings

LOGGER = logging.getLogger(__name__)
HEX_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PIPEFACIL_TIMESTAMP_HEADER = "X-Pipefacil-Timestamp"
DEFAULT_SIGNATURE_HEADER = "X-Pipefacil-Signature-256"
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def verify_pipefacil_webhook_signature(
    request: Request,
    settings: SettingsDep,
) -> None:
    if not settings.pipefacil_webhook_signature_enabled:
        return

    secret = settings.pipefacil_webhook_signature_secret or ""
    if not secret.strip():
        return

    header_name = DEFAULT_SIGNATURE_HEADER
    received_signature = request.headers.get(header_name)
    if received_signature is None:
        _reject_invalid_signature(
            reason="signature_missing",
            header_name=header_name,
            request_headers=request.headers,
        )

    timestamp = _normalized_header_value(request.headers, PIPEFACIL_TIMESTAMP_HEADER)
    if timestamp is None:
        _reject_invalid_signature(
            reason="timestamp_missing",
            header_name=header_name,
            request_headers=request.headers,
            received_signature=received_signature,
        )

    # EventRequestMiddleware decompresses supported Content-Encoding values before
    # this dependency runs. Pipefacil signs the original JSON before compression.
    body = await request.body()

    if _signature_matches(
        body=body,
        secret=secret,
        timestamp=timestamp,
        received_signature=received_signature,
    ):
        return

    if _signature_debug_enabled(settings):
        _log_signature_diagnostics(
            received_signature=received_signature,
            body=body,
            timestamp=timestamp,
            request_headers=request.headers,
        )

    _reject_invalid_signature(
        reason="signature_mismatch",
        header_name=header_name,
        request_headers=request.headers,
        received_signature=received_signature,
    )


def _normalized_header_value(headers: Headers, header_name: str) -> str | None:
    value = headers.get(header_name)
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def _signature_matches(
    *,
    body: bytes,
    secret: str,
    timestamp: str,
    received_signature: str,
) -> bool:
    normalized_signature = _normalized_sha256_signature(received_signature)
    if normalized_signature is None:
        return False

    expected_signature = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(normalized_signature, expected_signature)


def _normalized_sha256_signature(value: str) -> str | None:
    normalized = value.strip()
    if not normalized.startswith("sha256="):
        return None
    normalized = normalized[len("sha256=") :]

    if not HEX_SHA256_PATTERN.fullmatch(normalized):
        return None

    return normalized


def _reject_invalid_signature(
    *,
    reason: str,
    header_name: str,
    request_headers: Headers,
    received_signature: str | None = None,
) -> NoReturn:
    extra = {
        "pipeline_step": "pipefacil.webhook.signature_rejected",
        "error_code": "pipefacil_webhook_signature_invalid",
        "reason": reason,
        "signature_header": header_name,
        "request_header_names": sorted(request_headers.keys()),
        "status_code": status.HTTP_401_UNAUTHORIZED,
    }
    if received_signature is not None:
        extra.update(_signature_log_context(received_signature))

    LOGGER.warning(
        "pipefacil.webhook.signature_rejected",
        extra=extra,
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid webhook signature.",
    )


def _signature_log_context(received_signature: str) -> dict[str, object]:
    normalized_signature = received_signature.strip()
    return {
        "signature_value_length": len(normalized_signature),
        "signature_value_has_sha256_prefix": normalized_signature.lower().startswith("sha256="),
    }


def _signature_debug_enabled(settings: Settings) -> bool:
    return settings.app_env.strip().lower() != "production"


def _log_signature_diagnostics(
    *,
    received_signature: str,
    body: bytes,
    timestamp: str | None,
    request_headers: Headers,
) -> None:
    LOGGER.info(
        "pipefacil.webhook.signature_debug",
        extra={
            "pipeline_step": "pipefacil.webhook.signature_debug",
            "signature_value_length": len(received_signature.strip()),
            "signature_body_length": len(body),
            "signature_body_sha256_prefix": hashlib.sha256(body).hexdigest()[:16],
            "signature_timestamp_present": timestamp is not None,
            "request_header_names": sorted(request_headers.keys()),
        },
    )
