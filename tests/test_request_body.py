from __future__ import annotations

import asyncio
import gzip
import json
import zlib
from collections.abc import Callable

import pytest

import app.api.request_body as request_body
from app.api.request_body import (
    DECODED_REQUEST_BODY_SCOPE_KEY,
    MAX_DECODED_REQUEST_BODY_BYTES,
    MAX_ENCODED_REQUEST_BODY_BYTES,
    RAW_REQUEST_BODY_SCOPE_KEY,
    EventRequestMiddleware,
    RequestBodyClientDisconnectedError,
    RequestBodyDecodeError,
    RequestBodyTooLargeError,
    _decode_request_body,
    _prepare_downstream_receive,
    _read_asgi_body,
    _replace_scope_body_headers,
    _replay_body_receive,
    _should_log_http_request,
)


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, object]]] = []

    def info(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("info", message, dict(kwargs.get("extra") or {})))

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("warning", message, dict(kwargs.get("extra") or {})))

    def exception(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("exception", message, dict(kwargs.get("extra") or {})))


def _receive_messages(*messages: dict[str, object]):
    iterator = iter(messages)

    async def receive() -> dict[str, object]:
        return next(iterator, {"type": "http.disconnect"})

    return receive


def _http_scope(
    *,
    path: str = "/events/message-received",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, object]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }


@pytest.mark.parametrize(
    ("encoding", "encoder"),
    [
        ("identity", lambda body: body),
        ("gzip", gzip.compress),
        ("x-gzip", gzip.compress),
        ("deflate", zlib.compress),
    ],
)
def test_decode_request_body_supports_declared_encodings(
    encoding: str,
    encoder: Callable[[bytes], bytes],
) -> None:
    body = b'{"message":"hello"}'

    assert _decode_request_body(encoder(body), (encoding,)) == body


def test_decode_request_body_reverses_chained_content_encodings() -> None:
    body = b'{"message":"hello"}'
    encoded = gzip.compress(zlib.compress(body))

    assert _decode_request_body(encoded, ("deflate", "gzip")) == body


@pytest.mark.parametrize(
    ("body", "encodings"),
    [
        (b"not-gzip", ("gzip",)),
        (b"not-deflate", ("deflate",)),
        (b"payload", ("br",)),
        (gzip.compress(b"payload") + b"trailing", ("gzip",)),
        (gzip.compress(b"payload")[:-2], ("gzip",)),
    ],
)
def test_decode_request_body_rejects_invalid_payloads(
    body: bytes,
    encodings: tuple[str, ...],
) -> None:
    with pytest.raises(RequestBodyDecodeError):
        _decode_request_body(body, encodings)


def test_decode_request_body_rejects_decompression_expansion_over_limit() -> None:
    compressed_body = gzip.compress(b"x" * 1024)

    with pytest.raises(RequestBodyTooLargeError):
        _decode_request_body(
            compressed_body,
            ("gzip",),
            max_decoded_bytes=128,
        )


def test_read_asgi_body_accumulates_chunks_without_repeated_bytes_concat() -> None:
    receive = _receive_messages(
        {"type": "http.request", "body": b"hello ", "more_body": True},
        {"type": "http.request", "body": b"world", "more_body": False},
    )

    body = asyncio.run(_read_asgi_body(receive, max_bytes=11))

    assert body == b"hello world"


def test_read_asgi_body_rejects_payload_over_limit() -> None:
    receive = _receive_messages(
        {"type": "http.request", "body": b"1234", "more_body": True},
        {"type": "http.request", "body": b"56", "more_body": False},
    )

    with pytest.raises(RequestBodyTooLargeError):
        asyncio.run(_read_asgi_body(receive, max_bytes=5))


@pytest.mark.parametrize(
    "message",
    [
        {"type": "http.disconnect"},
        {"type": "websocket.receive", "bytes": b"body"},
        {"type": "http.request", "body": "not-bytes", "more_body": False},
    ],
)
def test_read_asgi_body_rejects_invalid_asgi_messages(
    message: dict[str, object],
) -> None:
    expected_error = (
        RequestBodyClientDisconnectedError
        if message["type"] == "http.disconnect"
        else RequestBodyDecodeError
    )

    with pytest.raises(expected_error):
        asyncio.run(_read_asgi_body(_receive_messages(message)))


def test_replay_body_receive_forwards_to_original_receive_after_body() -> None:
    original_receive = _receive_messages({"type": "http.disconnect"})
    replay_receive = _replay_body_receive(b"payload", original_receive)

    first = asyncio.run(replay_receive())
    second = asyncio.run(replay_receive())

    assert first == {
        "type": "http.request",
        "body": b"payload",
        "more_body": False,
    }
    assert second == {"type": "http.disconnect"}


def test_replace_scope_body_headers_removes_stale_encoding_and_length() -> None:
    scope = _http_scope(
        headers=[
            (b"content-type", b"application/json"),
            (b"content-encoding", b"gzip"),
            (b"content-length", b"999"),
        ]
    )

    _replace_scope_body_headers(scope, b"{}")

    assert scope["headers"] == [
        (b"content-type", b"application/json"),
        (b"content-length", b"2"),
    ]


def test_prepare_downstream_receive_preserves_raw_and_decoded_bodies() -> None:
    body = b'{"message":"hello"}'
    encoded_body = gzip.compress(body)
    scope = _http_scope(
        headers=[
            (b"content-type", b"application/json"),
            (b"content-encoding", b"gzip"),
            (b"content-length", str(len(encoded_body)).encode("ascii")),
        ]
    )
    receive = _receive_messages({"type": "http.request", "body": encoded_body, "more_body": False})

    downstream_receive = asyncio.run(_prepare_downstream_receive(scope, receive))

    assert scope[RAW_REQUEST_BODY_SCOPE_KEY] == encoded_body
    assert scope[DECODED_REQUEST_BODY_SCOPE_KEY] == body
    assert (b"content-encoding", b"gzip") not in scope["headers"]
    assert (b"content-length", str(len(body)).encode("ascii")) in scope["headers"]
    assert asyncio.run(downstream_receive())["body"] == body


def test_prepare_downstream_receive_buffers_plain_body_for_signature_verification() -> None:
    body = b'{"message":"hello"}'
    scope = _http_scope(headers=[(b"content-length", str(len(body)).encode("ascii"))])
    receive = _receive_messages({"type": "http.request", "body": body, "more_body": False})

    downstream_receive = asyncio.run(_prepare_downstream_receive(scope, receive))

    assert scope[RAW_REQUEST_BODY_SCOPE_KEY] == body
    assert scope[DECODED_REQUEST_BODY_SCOPE_KEY] == body
    assert asyncio.run(downstream_receive())["body"] == body


@pytest.mark.parametrize("content_length", ["invalid", "-1"])
def test_prepare_downstream_receive_rejects_invalid_content_length(
    content_length: str,
) -> None:
    scope = _http_scope(headers=[(b"content-length", content_length.encode("ascii"))])

    with pytest.raises(RequestBodyDecodeError):
        asyncio.run(
            _prepare_downstream_receive(
                scope,
                _receive_messages({"type": "http.request", "body": b"", "more_body": False}),
            )
        )


def test_prepare_downstream_receive_rejects_declared_body_over_limit() -> None:
    scope = _http_scope(
        headers=[
            (
                b"content-length",
                str(MAX_ENCODED_REQUEST_BODY_BYTES + 1).encode("ascii"),
            )
        ]
    )

    with pytest.raises(RequestBodyTooLargeError):
        asyncio.run(
            _prepare_downstream_receive(
                scope,
                _receive_messages({"type": "http.request", "body": b"", "more_body": False}),
            )
        )


def test_event_middleware_replays_body_and_logs_event_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(request_body, "LOGGER", fake_logger)
    captured: dict[str, object] = {}
    sent_messages: list[dict[str, object]] = []

    async def app(scope, receive, send) -> None:
        captured["body"] = (await receive())["body"]
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def send(message: dict[str, object]) -> None:
        sent_messages.append(message)

    body = b'{"message":"hello"}'
    scope = _http_scope(headers=[(b"content-length", str(len(body)).encode("ascii"))])
    receive = _receive_messages({"type": "http.request", "body": body, "more_body": False})

    asyncio.run(EventRequestMiddleware(app)(scope, receive, send))

    assert captured["body"] == body
    assert sent_messages[0]["status"] == 202
    assert [record[1] for record in fake_logger.records] == [
        "http.request.started",
        "http.request.completed",
    ]


@pytest.mark.parametrize(
    ("headers", "body", "expected_status", "expected_detail"),
    [
        (
            [(b"content-encoding", b"br")],
            b"payload",
            400,
            "Unsupported request body content encoding: br.",
        ),
        (
            [
                (
                    b"content-length",
                    str(MAX_ENCODED_REQUEST_BODY_BYTES + 1).encode("ascii"),
                )
            ],
            b"",
            413,
            "Encoded request body exceeds the configured size limit.",
        ),
        (
            [(b"content-encoding", b"gzip")],
            gzip.compress(b"x" * (MAX_DECODED_REQUEST_BODY_BYTES + 1)),
            413,
            "Decoded request body exceeds the configured size limit.",
        ),
    ],
)
def test_event_middleware_returns_bounded_body_errors(
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    expected_status: int,
    expected_detail: str,
) -> None:
    downstream_called = False
    sent_messages: list[dict[str, object]] = []

    async def app(scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def send(message: dict[str, object]) -> None:
        sent_messages.append(message)

    scope = _http_scope(headers=headers)
    receive = _receive_messages({"type": "http.request", "body": body, "more_body": False})

    asyncio.run(EventRequestMiddleware(app)(scope, receive, send))

    assert downstream_called is False
    assert sent_messages[0]["status"] == expected_status
    assert json.loads(sent_messages[1]["body"]) == {"detail": expected_detail}


def test_event_middleware_stops_cleanly_after_client_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(request_body, "LOGGER", fake_logger)
    downstream_called = False
    sent_messages: list[dict[str, object]] = []

    async def app(scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def send(message: dict[str, object]) -> None:
        sent_messages.append(message)

    asyncio.run(
        EventRequestMiddleware(app)(
            _http_scope(),
            _receive_messages({"type": "http.disconnect"}),
            send,
        )
    )

    assert downstream_called is False
    assert sent_messages == []
    assert fake_logger.records[-1][1] == "http.request.client_disconnected"
    assert fake_logger.records[-1][2]["status_code"] == 499


def test_event_middleware_logs_and_reraises_downstream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(request_body, "LOGGER", fake_logger)

    async def app(scope, receive, send) -> None:
        raise RuntimeError("boom")

    async def send(message: dict[str, object]) -> None:
        raise AssertionError("send should not be called")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(
            EventRequestMiddleware(app)(
                _http_scope(path="/chat"),
                _receive_messages({"type": "http.request", "body": b"", "more_body": False}),
                send,
            )
        )

    assert fake_logger.records[-1][1] == "http.request.failed"


def test_event_middleware_delegates_non_http_scope() -> None:
    captured: dict[str, object] = {}

    async def app(scope, receive, send) -> None:
        captured["scope"] = scope

    async def receive() -> dict[str, object]:
        return {"type": "lifespan.startup"}

    async def send(message: dict[str, object]) -> None:
        return None

    scope = {"type": "lifespan"}

    asyncio.run(EventRequestMiddleware(app)(scope, receive, send))

    assert captured["scope"] == scope


def test_quiet_paths_are_not_logged_for_successful_requests() -> None:
    assert not _should_log_http_request(path="/health", status_code=200)
    assert not _should_log_http_request(path="/health", status_code=500)
