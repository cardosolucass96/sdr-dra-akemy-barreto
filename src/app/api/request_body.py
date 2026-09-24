from __future__ import annotations

import logging
import time
import zlib
from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOGGER = logging.getLogger(__name__)
RAW_REQUEST_BODY_SCOPE_KEY = "sdr_pipefacil_raw_request_body"
DECODED_REQUEST_BODY_SCOPE_KEY = "sdr_pipefacil_decoded_request_body"
MAX_ENCODED_REQUEST_BODY_BYTES = 2 * 1024 * 1024
MAX_DECODED_REQUEST_BODY_BYTES = 8 * 1024 * 1024
QUIET_HTTP_PATHS = {"/health", "/ready", "/docs", "/openapi.json"}


class RequestBodyDecodeError(ValueError):
    pass


class RequestBodyTooLargeError(RequestBodyDecodeError):
    pass


class RequestBodyClientDisconnectedError(RequestBodyDecodeError):
    pass


def _scope_header_value(scope: Scope, header_name: str) -> str | None:
    expected_name = header_name.lower().encode("latin-1")
    for name, value in scope.get("headers", ()):
        if name.lower() == expected_name:
            return value.decode("latin-1")

    return None


def _scope_header_names(scope: Scope) -> list[str]:
    return sorted(name.decode("latin-1").lower() for name, _ in scope.get("headers", ()))


def _http_scope_log_context(scope: Scope) -> dict[str, Any]:
    return {
        "http_method": scope.get("method"),
        "http_path": scope.get("path"),
        "content_type": _scope_header_value(scope, "content-type"),
        "content_encoding": _scope_header_value(scope, "content-encoding"),
        "content_length": _scope_header_value(scope, "content-length"),
        "user_agent": _scope_header_value(scope, "user-agent"),
    }


def _scope_content_encodings(scope: Scope) -> tuple[str, ...]:
    content_encoding = _scope_header_value(scope, "content-encoding")
    if not content_encoding:
        return ()

    return tuple(
        encoding.strip().lower() for encoding in content_encoding.split(",") if encoding.strip()
    )


def _should_log_http_request(*, path: str, status_code: int) -> bool:
    if path in QUIET_HTTP_PATHS:
        return False

    return path.startswith("/events/") or status_code >= 400


def _bounded_zlib_decompress(
    body: bytes,
    *,
    wbits: int,
    max_bytes: int,
    encoding: str,
) -> bytes:
    decompressor = zlib.decompressobj(wbits)
    try:
        decoded_body = decompressor.decompress(body, max_bytes + 1)
    except zlib.error as exc:
        raise RequestBodyDecodeError(f"Could not decompress {encoding} request body.") from exc

    if len(decoded_body) > max_bytes or decompressor.unconsumed_tail:
        raise RequestBodyTooLargeError("Decoded request body exceeds the configured size limit.")
    if not decompressor.eof or decompressor.unused_data:
        raise RequestBodyDecodeError(f"Could not decompress {encoding} request body.")

    return decoded_body


def _decode_request_body(
    raw_body: bytes,
    encodings: tuple[str, ...],
    *,
    max_decoded_bytes: int = MAX_DECODED_REQUEST_BODY_BYTES,
) -> bytes:
    decoded_body = raw_body
    for encoding in reversed(encodings):
        if encoding == "identity":
            continue
        if encoding in {"gzip", "x-gzip"}:
            decoded_body = _bounded_zlib_decompress(
                decoded_body,
                wbits=16 + zlib.MAX_WBITS,
                max_bytes=max_decoded_bytes,
                encoding="gzip",
            )
            continue
        if encoding == "deflate":
            decoded_body = _bounded_zlib_decompress(
                decoded_body,
                wbits=zlib.MAX_WBITS,
                max_bytes=max_decoded_bytes,
                encoding="deflate",
            )
            continue

        raise RequestBodyDecodeError(f"Unsupported request body content encoding: {encoding}.")

    return decoded_body


def _declared_content_length(scope: Scope) -> int | None:
    content_length = _scope_header_value(scope, "content-length")
    if content_length is None:
        return None

    try:
        parsed_content_length = int(content_length)
    except ValueError as exc:
        raise RequestBodyDecodeError("Invalid Content-Length request header.") from exc

    if parsed_content_length < 0:
        raise RequestBodyDecodeError("Invalid Content-Length request header.")

    return parsed_content_length


async def _read_asgi_body(
    receive: Receive,
    *,
    max_bytes: int = MAX_ENCODED_REQUEST_BODY_BYTES,
) -> bytes:
    body = bytearray()
    more_body = True
    while more_body:
        message = await receive()
        message_type = message.get("type")
        if message_type == "http.disconnect":
            raise RequestBodyClientDisconnectedError(
                "Client disconnected before the request body was complete."
            )
        if message_type != "http.request":
            raise RequestBodyDecodeError(
                f"Unexpected ASGI message while reading request body: {message_type!r}."
            )

        chunk = message.get("body", b"")
        if not isinstance(chunk, bytes):
            raise RequestBodyDecodeError("ASGI request body chunks must be bytes.")
        if len(body) + len(chunk) > max_bytes:
            raise RequestBodyTooLargeError(
                "Encoded request body exceeds the configured size limit."
            )

        body.extend(chunk)
        more_body = bool(message.get("more_body", False))

    return bytes(body)


def _replay_body_receive(body: bytes, original_receive: Receive) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return await original_receive()

        sent = True
        return {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }

    return receive


def _replace_scope_body_headers(scope: Scope, body: bytes) -> None:
    removed_headers = {b"content-encoding", b"content-length"}
    headers = [
        (name, value)
        for name, value in scope.get("headers", ())
        if name.lower() not in removed_headers
    ]
    headers.append((b"content-length", str(len(body)).encode("ascii")))
    scope["headers"] = headers


async def _prepare_downstream_receive(scope: Scope, receive: Receive) -> Receive:
    content_length = _declared_content_length(scope)
    if content_length is not None and content_length > MAX_ENCODED_REQUEST_BODY_BYTES:
        raise RequestBodyTooLargeError("Encoded request body exceeds the configured size limit.")

    encodings = _scope_content_encodings(scope)
    raw_body = await _read_asgi_body(receive)
    decoded_body = _decode_request_body(raw_body, encodings)
    scope[RAW_REQUEST_BODY_SCOPE_KEY] = raw_body
    scope[DECODED_REQUEST_BODY_SCOPE_KEY] = decoded_body
    if encodings:
        _replace_scope_body_headers(scope, decoded_body)

    return _replay_body_receive(decoded_body, receive)


class EventRequestMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        http_path = str(scope.get("path") or "")
        request_log_context = _http_scope_log_context(scope)

        if http_path.startswith("/events/"):
            LOGGER.info(
                "http.request.started",
                extra={
                    **request_log_context,
                    "request_header_names": _scope_header_names(scope),
                },
            )

        try:
            downstream_receive = await _prepare_downstream_receive(scope, receive)
        except RequestBodyClientDisconnectedError:
            self._log_client_disconnect(
                start=start,
                request_log_context=request_log_context,
            )
            return
        except RequestBodyTooLargeError as exc:
            await self._send_body_error(
                scope=scope,
                send=send,
                start=start,
                request_log_context=request_log_context,
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                error_code="request_body_too_large",
                log_event="http.request.body_too_large",
                detail=str(exc),
            )
            return
        except RequestBodyDecodeError as exc:
            await self._send_body_error(
                scope=scope,
                send=send,
                start=start,
                request_log_context=request_log_context,
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="request_body_decode_failed",
                log_event="http.request.body_decode_failed",
                detail=str(exc),
            )
            return

        response_status_code = await self._call_downstream(
            scope=scope,
            receive=downstream_receive,
            send=send,
            start=start,
            request_log_context=request_log_context,
        )
        if _should_log_http_request(
            path=http_path,
            status_code=response_status_code,
        ):
            self._log_completed(
                start=start,
                request_log_context=request_log_context,
                status_code=response_status_code,
            )

    async def _call_downstream(
        self,
        *,
        scope: Scope,
        receive: Receive,
        send: Send,
        start: float,
        request_log_context: dict[str, Any],
    ) -> int:
        response_status_code: int | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal response_status_code
            if message["type"] == "http.response.start":
                response_status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            LOGGER.exception(
                "http.request.failed",
                extra={
                    **request_log_context,
                    "duration_ms": duration_ms,
                    "error_code": "http_request_failed",
                },
            )
            raise

        return response_status_code or status.HTTP_500_INTERNAL_SERVER_ERROR

    @staticmethod
    def _log_client_disconnect(
        *,
        start: float,
        request_log_context: dict[str, Any],
    ) -> None:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        LOGGER.info(
            "http.request.client_disconnected",
            extra={
                **request_log_context,
                "duration_ms": duration_ms,
                "error_code": "request_client_disconnected",
                "status_code": 499,
            },
        )

    @staticmethod
    def _log_completed(
        *,
        start: float,
        request_log_context: dict[str, Any],
        status_code: int,
    ) -> None:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        LOGGER.info(
            "http.request.completed",
            extra={
                **request_log_context,
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
        )

    @staticmethod
    async def _send_body_error(
        *,
        scope: Scope,
        send: Send,
        start: float,
        request_log_context: dict[str, Any],
        status_code: int,
        error_code: str,
        log_event: str,
        detail: str,
    ) -> None:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        LOGGER.warning(
            log_event,
            extra={
                **request_log_context,
                "duration_ms": duration_ms,
                "error_code": error_code,
                "status_code": status_code,
            },
        )
        response = JSONResponse(
            status_code=status_code,
            content={"detail": detail},
        )
        await response(scope, _empty_receive, send)
        EventRequestMiddleware._log_completed(
            start=start,
            request_log_context=request_log_context,
            status_code=status_code,
        )


async def _empty_receive() -> Message:
    return {
        "type": "http.request",
        "body": b"",
        "more_body": False,
    }
