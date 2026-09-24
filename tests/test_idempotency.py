from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import ANY

import pytest

import app.integrations.postgres_idempotency as postgres_idempotency
from app.application.idempotency import InMemoryMessageIdempotencyStore
from app.integrations.postgres_idempotency import PostgresMessageIdempotencyStore


def test_in_memory_store_claims_releases_and_reclaims() -> None:
    store = InMemoryMessageIdempotencyStore()

    assert store.claim("message-1", ttl_seconds=60) is True
    assert store.claim("message-1", ttl_seconds=60) is False

    store.release("message-1")

    assert store.claim("message-1", ttl_seconds=60) is True


def test_in_memory_store_expires_claims_and_disables_deduplication_at_zero() -> None:
    current_time = [100.0]
    store = InMemoryMessageIdempotencyStore(clock=lambda: current_time[0])

    assert store.claim("expiring", ttl_seconds=5) is True
    current_time[0] = 104.9
    assert store.claim("expiring", ttl_seconds=5) is False
    current_time[0] = 105.0
    assert store.claim("expiring", ttl_seconds=5) is True

    assert store.claim("disabled", ttl_seconds=0) is True
    assert store.claim("disabled", ttl_seconds=0) is True


def test_in_memory_store_claim_is_atomic_under_concurrency() -> None:
    store = InMemoryMessageIdempotencyStore()

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(
            executor.map(
                lambda _: store.claim("same-message", ttl_seconds=60),
                range(64),
            )
        )

    assert results.count(True) == 1
    assert results.count(False) == 63


class _FakeCursor:
    def __init__(self, *, row: tuple[str] | None = None) -> None:
        self.row = row
        self.executions: list[tuple[object, object | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, query: object, params: object | None = None) -> None:
        self.executions.append((query, params))

    def fetchone(self) -> tuple[str] | None:
        return self.row


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self._cursor


class _FakePool:
    def __init__(self, cursors: list[_FakeCursor]) -> None:
        self._cursors = iter(cursors)
        self.connection_calls = 0

    def connection(self) -> _FakeConnection:
        self.connection_calls += 1
        return _FakeConnection(next(self._cursors))


def test_postgres_store_sets_up_claims_and_releases_with_shared_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_cursor = _FakeCursor()
    claim_cursor = _FakeCursor(row=("message-1",))
    release_cursor = _FakeCursor()
    pool = _FakePool([setup_cursor, claim_cursor, release_cursor])
    setup_calls: list[tuple[object, str | None, bool]] = []
    monkeypatch.setattr(
        postgres_idempotency,
        "prepare_postgres_connection",
        lambda connection, schema, *, create_schema=False: setup_calls.append(
            (connection, schema, create_schema)
        ),
    )
    store = PostgresMessageIdempotencyStore(pool, schema="sdr_ia")

    store.setup()
    claimed = store.claim("message-1", ttl_seconds=86_400)
    store.release("message-1")

    assert claimed is True
    assert setup_calls == [(ANY, "sdr_ia", True)]
    assert len(setup_cursor.executions) == 2
    assert claim_cursor.executions[0][1] == ("message-1", 86_400)
    assert release_cursor.executions[0][1] == ("message-1",)
    assert pool.connection_calls == 3


def test_postgres_store_returns_duplicate_and_skips_database_when_disabled() -> None:
    duplicate_cursor = _FakeCursor(row=None)
    pool = _FakePool([duplicate_cursor])
    store = PostgresMessageIdempotencyStore(pool)

    assert store.claim("message-1", ttl_seconds=60) is False
    assert store.claim("message-2", ttl_seconds=0) is True
    assert pool.connection_calls == 1
