from __future__ import annotations

from psycopg import sql
from psycopg_pool import ConnectionPool

from app.core.database import prepare_postgres_connection

IDEMPOTENCY_TABLE_NAME = "pipefacil_webhook_idempotency"


class PostgresMessageIdempotencyStore:
    def __init__(
        self,
        pool: ConnectionPool,
        *,
        schema: str | None = None,
    ) -> None:
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
                            idempotency_key TEXT PRIMARY KEY,
                            expires_at TIMESTAMPTZ NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    ).format(self._table_identifier())
                )
                cursor.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (expires_at)").format(
                        sql.Identifier(f"{IDEMPOTENCY_TABLE_NAME}_expires_at_idx"),
                        self._table_identifier(),
                    )
                )

    def claim(self, key: str, *, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            return True

        with self._pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        WITH expired AS (
                            DELETE FROM {}
                            WHERE expires_at <= CURRENT_TIMESTAMP
                        )
                        INSERT INTO {} (idempotency_key, expires_at)
                        VALUES (
                            %s,
                            CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                        )
                        ON CONFLICT (idempotency_key) DO NOTHING
                        RETURNING idempotency_key
                        """
                    ).format(self._table_identifier(), self._table_identifier()),
                    (key, ttl_seconds),
                )
                return cursor.fetchone() is not None

    def release(self, key: str) -> None:
        with self._pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DELETE FROM {} WHERE idempotency_key = %s").format(
                        self._table_identifier()
                    ),
                    (key,),
                )

    def _table_identifier(self) -> sql.Identifier:
        if self._schema:
            return sql.Identifier(self._schema, IDEMPOTENCY_TABLE_NAME)
        return sql.Identifier(IDEMPOTENCY_TABLE_NAME)
