from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from psycopg import Connection, sql

JDBC_POSTGRES_PREFIX = "jdbc:postgresql:"
JDBC_CURRENT_SCHEMA_KEYS = {"currentschema", "current_schema"}
POSTGRES_URL_SCHEMES = {"postgres", "postgresql"}


@dataclass(frozen=True)
class PostgresDatabaseConfig:
    url: str
    schema: str | None = None


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


def resolve_postgres_database_config(
    database_url: str | None,
    *,
    schema: str | None = None,
) -> PostgresDatabaseConfig | None:
    raw_database_url = _blank_to_none(database_url)
    if raw_database_url is None:
        return None

    normalized_url, url_schema = normalize_postgres_database_url(raw_database_url)
    target_schema = _blank_to_none(schema) or url_schema
    return PostgresDatabaseConfig(url=normalized_url, schema=target_schema)


def normalize_postgres_database_url(database_url: str) -> tuple[str, str | None]:
    raw_database_url = database_url.strip()
    if raw_database_url.startswith(JDBC_POSTGRES_PREFIX):
        raw_database_url = "postgresql:" + raw_database_url.removeprefix(JDBC_POSTGRES_PREFIX)

    parts = urlsplit(raw_database_url)
    if parts.scheme not in POSTGRES_URL_SCHEMES:
        return raw_database_url, None

    query_items = parse_qsl(parts.query, keep_blank_values=True)
    schema_from_query = next(
        (
            value
            for key, value in query_items
            if key.replace("_", "").lower() in JDBC_CURRENT_SCHEMA_KEYS
        ),
        None,
    )
    query_items = [
        (key, value)
        for key, value in query_items
        if key.replace("_", "").lower() not in JDBC_CURRENT_SCHEMA_KEYS
    ]

    path_segments = [segment for segment in parts.path.split("/") if segment]
    schema_from_path = None
    path = parts.path
    if len(path_segments) > 1:
        database_name = unquote(path_segments[0])
        schema_from_path = "/".join(unquote(segment) for segment in path_segments[1:])
        path = f"/{quote(database_name, safe='')}"

    normalized_url = urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            path,
            urlencode(query_items, doseq=True),
            parts.fragment,
        )
    )
    return normalized_url, _blank_to_none(schema_from_query) or _blank_to_none(schema_from_path)


def ensure_postgres_schema(connection: Connection, schema: str | None) -> None:
    target_schema = _blank_to_none(schema)
    if target_schema is None:
        return

    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(target_schema))
        )


def postgres_schema_exists(connection: Connection, schema: str | None) -> bool:
    target_schema = _blank_to_none(schema)
    if target_schema is None:
        return True

    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regnamespace(%s)", (target_schema,))
        row = cursor.fetchone()

    return bool(row and row[0])


def set_postgres_search_path(connection: Connection, schema: str | None) -> None:
    target_schema = _blank_to_none(schema)
    if target_schema is None:
        return

    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(target_schema)))


def prepare_postgres_connection(
    connection: Connection,
    schema: str | None,
    *,
    create_schema: bool = False,
) -> None:
    if create_schema:
        ensure_postgres_schema(connection, schema)

    set_postgres_search_path(connection, schema)
