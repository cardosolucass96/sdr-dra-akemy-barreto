from __future__ import annotations

from copy import deepcopy
from threading import Lock
from typing import Any, Protocol

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.core.database import prepare_postgres_connection

OUTBOX_TABLE_NAME = "pipefacil_sync_outbox"


class PipefacilSyncOutboxStore(Protocol):
    def enqueue(self, operation: dict[str, Any]) -> None: ...

    def claim(self, operation_id: str | None = None) -> dict[str, Any] | None: ...

    def get(self, operation_id: str) -> dict[str, Any] | None: ...

    def mark_succeeded(self, operation_id: str, receipt: dict[str, Any]) -> None: ...

    def mark_failed(
        self,
        operation_id: str,
        error: dict[str, Any],
        *,
        retry_after_seconds: int,
    ) -> None: ...

    def mark_blocked(self, operation_id: str, error: dict[str, Any]) -> None: ...


class PostgresPipefacilSyncOutboxStore:
    def __init__(self, pool: ConnectionPool, *, schema: str | None = None) -> None:
        self._pool = pool
        self._schema = schema

    def setup(self) -> None:
        with self._pool.connection() as connection:
            prepare_postgres_connection(connection, self._schema, create_schema=True)
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            operation_id TEXT PRIMARY KEY,
                            deal_seq BIGINT NOT NULL,
                            payload JSONB NOT NULL,
                            status TEXT NOT NULL DEFAULT 'pending',
                            attempt_count INTEGER NOT NULL DEFAULT 0,
                            available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            lease_until TIMESTAMPTZ,
                            receipt JSONB,
                            last_error JSONB,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    ).format(self._table_identifier())
                )
                cursor.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, available_at)").format(
                        sql.Identifier(f"{OUTBOX_TABLE_NAME}_ready_idx"),
                        self._table_identifier(),
                    )
                )

    def enqueue(self, operation: dict[str, Any]) -> None:
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "INSERT INTO {} (operation_id, deal_seq, payload) "
                    "VALUES (%s, %s, %s) ON CONFLICT (operation_id) DO NOTHING"
                ).format(self._table_identifier()),
                (
                    operation["operation_id"],
                    operation["deal_seq"],
                    Jsonb(operation),
                ),
            )

    def claim(self, operation_id: str | None = None) -> dict[str, Any] | None:
        condition = sql.SQL("AND operation_id = %s") if operation_id else sql.SQL("")
        params: tuple[Any, ...] = (operation_id,) if operation_id else ()
        query = sql.SQL(
            """
            WITH candidate AS (
                SELECT operation_id FROM {}
                WHERE (
                    status = 'pending'
                    OR (status = 'processing' AND lease_until < CURRENT_TIMESTAMP)
                )
                    AND available_at <= CURRENT_TIMESTAMP {}
                ORDER BY created_at
                LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            UPDATE {} AS task
            SET status = 'processing', lease_until = CURRENT_TIMESTAMP + INTERVAL '2 minutes',
                updated_at = CURRENT_TIMESTAMP
            FROM candidate
            WHERE task.operation_id = candidate.operation_id
            RETURNING task.operation_id, task.payload, task.attempt_count
            """
        ).format(self._table_identifier(), condition, self._table_identifier())
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(query, params)
                row = cursor.fetchone()
        if row is None:
            return None
        result = dict(row["payload"])
        result["attempt_count"] = row["attempt_count"]
        return result

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT status, receipt, last_error FROM {} WHERE operation_id = %s"
                    ).format(self._table_identifier()),
                    (operation_id,),
                )
                row = cursor.fetchone()
        return dict(row) if row else None

    def mark_succeeded(self, operation_id: str, receipt: dict[str, Any]) -> None:
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "UPDATE {} SET status = 'succeeded', receipt = %s, last_error = NULL, "
                    "lease_until = NULL, updated_at = CURRENT_TIMESTAMP WHERE operation_id = %s"
                ).format(self._table_identifier()),
                (Jsonb(receipt), operation_id),
            )

    def mark_failed(
        self,
        operation_id: str,
        error: dict[str, Any],
        *,
        retry_after_seconds: int,
    ) -> None:
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "UPDATE {} SET status = 'pending', attempt_count = attempt_count + 1, "
                    "last_error = %s, lease_until = NULL, "
                    "available_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'), "
                    "updated_at = CURRENT_TIMESTAMP WHERE operation_id = %s"
                ).format(self._table_identifier()),
                (Jsonb(error), retry_after_seconds, operation_id),
            )

    def mark_blocked(self, operation_id: str, error: dict[str, Any]) -> None:
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "UPDATE {} SET status = 'blocked', last_error = %s, lease_until = NULL, "
                    "updated_at = CURRENT_TIMESTAMP WHERE operation_id = %s"
                ).format(self._table_identifier()),
                (Jsonb(error), operation_id),
            )

    def _table_identifier(self) -> sql.Identifier:
        if self._schema:
            return sql.Identifier(self._schema, OUTBOX_TABLE_NAME)
        return sql.Identifier(OUTBOX_TABLE_NAME)


class InMemoryPipefacilSyncOutboxStore:
    """Process-local fallback for development without a configured database."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._operations: dict[str, dict[str, Any]] = {}

    def enqueue(self, operation: dict[str, Any]) -> None:
        with self._lock:
            self._operations.setdefault(
                operation["operation_id"],
                {"payload": deepcopy(operation), "status": "pending", "attempt_count": 0},
            )

    def claim(self, operation_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            for key, item in self._operations.items():
                if item["status"] == "pending" and (operation_id is None or key == operation_id):
                    item["status"] = "processing"
                    return deepcopy(item["payload"] | {"attempt_count": item["attempt_count"]})
        return None

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._operations.get(operation_id)
            if item is None:
                return None
            return {
                "status": item["status"],
                "receipt": deepcopy(item.get("receipt")),
                "last_error": deepcopy(item.get("last_error")),
            }

    def mark_succeeded(self, operation_id: str, receipt: dict[str, Any]) -> None:
        self._set_result(operation_id, status="succeeded", receipt=receipt)

    def mark_failed(
        self,
        operation_id: str,
        error: dict[str, Any],
        *,
        retry_after_seconds: int,
    ) -> None:
        del retry_after_seconds
        self._set_result(operation_id, status="pending", last_error=error, increment=True)

    def mark_blocked(self, operation_id: str, error: dict[str, Any]) -> None:
        self._set_result(operation_id, status="blocked", last_error=error)

    def _set_result(
        self,
        operation_id: str,
        *,
        status: str,
        receipt: dict[str, Any] | None = None,
        last_error: dict[str, Any] | None = None,
        increment: bool = False,
    ) -> None:
        with self._lock:
            item = self._operations[operation_id]
            item.update(status=status, receipt=receipt, last_error=last_error)
            if increment:
                item["attempt_count"] += 1


__all__ = [
    "InMemoryPipefacilSyncOutboxStore",
    "OUTBOX_TABLE_NAME",
    "PipefacilSyncOutboxStore",
    "PostgresPipefacilSyncOutboxStore",
]
