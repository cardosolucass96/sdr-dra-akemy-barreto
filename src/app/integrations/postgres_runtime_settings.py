"""Postgres persistence for the singleton runtime settings document."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.core.config import RuntimeSettings
from app.core.database import prepare_postgres_connection

RUNTIME_SETTINGS_TABLE_NAME = "sdr_runtime_settings"


class RuntimeSettingsConflictError(RuntimeError):
    """Raised when a browser tries to save an outdated configuration revision."""


@dataclass(frozen=True, slots=True)
class RuntimeSettingsSnapshot:
    settings: RuntimeSettings
    version: int
    updated_at: datetime


class PostgresRuntimeSettingsStore:
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
                            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
                            version INTEGER NOT NULL CHECK (version >= 1),
                            payload JSONB NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    ).format(self._table_identifier())
                )
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (singleton, version, payload)
                        VALUES (TRUE, 1, %s)
                        ON CONFLICT (singleton) DO NOTHING
                        """
                    ).format(self._table_identifier()),
                    (Jsonb(RuntimeSettings().model_dump(mode="json")),),
                )

    def get(self) -> RuntimeSettingsSnapshot:
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT version, payload, updated_at FROM {} WHERE singleton = TRUE"
                    ).format(self._table_identifier())
                )
                row = cursor.fetchone()

        if row is None:
            raise RuntimeError("Runtime settings have not been initialized.")
        return self._snapshot_from_row(row)

    def update(
        self,
        settings: RuntimeSettings,
        *,
        expected_version: int,
    ) -> RuntimeSettingsSnapshot:
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET payload = %s,
                            version = version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE singleton = TRUE AND version = %s
                        RETURNING version, payload, updated_at
                        """
                    ).format(self._table_identifier()),
                    (Jsonb(settings.model_dump(mode="json")), expected_version),
                )
                row = cursor.fetchone()

        if row is None:
            raise RuntimeSettingsConflictError("Runtime settings changed in another session.")
        return self._snapshot_from_row(row)

    @staticmethod
    def _snapshot_from_row(row: dict[str, Any]) -> RuntimeSettingsSnapshot:
        return RuntimeSettingsSnapshot(
            settings=RuntimeSettings.model_validate(row["payload"]),
            version=int(row["version"]),
            updated_at=row["updated_at"],
        )

    def _table_identifier(self) -> sql.Identifier:
        if self._schema:
            return sql.Identifier(self._schema, RUNTIME_SETTINGS_TABLE_NAME)
        return sql.Identifier(RUNTIME_SETTINGS_TABLE_NAME)
