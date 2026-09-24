from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from app.agent.graph import build_graph
from app.core.config import Settings, get_settings
from app.core.database import (
    PostgresDatabaseConfig,
    prepare_postgres_connection,
    resolve_postgres_database_config,
)


@dataclass
class AgentGraphRuntime:
    graph: Any
    checkpointer: Any
    stack: ExitStack
    database_pool: ConnectionPool | None
    database_schema: str | None

    def close(self) -> None:
        self.stack.close()


def _database_config(settings: Settings) -> PostgresDatabaseConfig | None:
    return resolve_postgres_database_config(
        settings.database_url,
        schema=settings.langgraph_checkpoint_schema,
    )


def _configure_postgres_connection(
    schema: str | None,
) -> Callable[[Connection[DictRow]], None]:
    def configure(connection: Connection[DictRow]) -> None:
        prepare_postgres_connection(connection, schema)

    return configure


def _build_postgres_checkpointer(
    database_config: PostgresDatabaseConfig,
    settings: Settings,
    stack: ExitStack,
) -> tuple[Any, ConnectionPool]:
    from langgraph.checkpoint.postgres import PostgresSaver

    pool = ConnectionPool(
        database_config.url,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        min_size=settings.langgraph_checkpoint_pool_min_size,
        max_size=settings.langgraph_checkpoint_pool_max_size,
        timeout=settings.langgraph_checkpoint_pool_timeout_seconds,
        reconnect_timeout=settings.langgraph_checkpoint_pool_timeout_seconds,
        configure=_configure_postgres_connection(database_config.schema),
        check=ConnectionPool.check_connection,
        name="langgraph-checkpointer",
        open=False,
    )
    stack.callback(pool.close)
    pool.open(wait=True, timeout=settings.langgraph_checkpoint_pool_timeout_seconds)
    return PostgresSaver(pool), pool


def build_runtime(settings: Settings | None = None) -> AgentGraphRuntime:
    current_settings = settings or get_settings()
    stack = ExitStack()

    try:
        database_config = _database_config(current_settings)
        if database_config:
            checkpointer, database_pool = _build_postgres_checkpointer(
                database_config,
                current_settings,
                stack,
            )
            database_schema = database_config.schema
        else:
            checkpointer = InMemorySaver()
            database_pool = None
            database_schema = None

        runtime_graph = build_graph(checkpointer=checkpointer)
        return AgentGraphRuntime(
            graph=runtime_graph,
            checkpointer=checkpointer,
            stack=stack,
            database_pool=database_pool,
            database_schema=database_schema,
        )
    except Exception:
        stack.close()
        raise


def bootstrap_postgres_checkpointer(database_url: str, *, schema: str | None = None) -> None:
    from langgraph.checkpoint.postgres import PostgresSaver

    database_config = resolve_postgres_database_config(database_url, schema=schema)
    if database_config is None:
        raise ValueError("DATABASE_URL is required to bootstrap the Postgres checkpointer.")

    with PostgresSaver.from_conn_string(database_config.url) as checkpointer:
        prepare_postgres_connection(
            checkpointer.conn,
            database_config.schema,
            create_schema=True,
        )
        checkpointer.setup()
