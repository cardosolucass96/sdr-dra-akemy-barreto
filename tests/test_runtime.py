from __future__ import annotations

import logging
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

import app.main as main_module
from app.agent import runtime as runtime_module
from app.agent import service
from app.agent.runtime import build_runtime
from app.agent.service import run_agent
from app.core import RuntimeConfigurationError
from app.core.config import Settings, get_bootstrap_settings, get_settings
from app.core.database import resolve_postgres_database_config
from app.main import create_app
from app.observability import reset_langfuse_clients


@pytest.fixture(autouse=True)
def clear_settings_and_langfuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("SETTINGS_ADMIN_KEY_HASH", "scrypt$test")
    monkeypatch.setenv("SETTINGS_SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(main_module, "APPLICATION_ENV", "development")
    reset_langfuse_clients()
    get_bootstrap_settings.cache_clear()
    get_settings.cache_clear()
    yield
    reset_langfuse_clients()
    get_bootstrap_settings.cache_clear()
    get_settings.cache_clear()


def test_build_runtime_uses_in_memory_saver_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    runtime = build_runtime()

    assert isinstance(runtime.checkpointer, InMemorySaver)
    assert runtime.database_pool is None
    assert runtime.database_schema is None
    runtime.close()


def test_reset_thread_state_deletes_all_checkpoints_for_thread() -> None:
    deleted_threads: list[str] = []

    class FakeCheckpointer:
        def delete_thread(self, thread_id: str) -> None:
            deleted_threads.append(thread_id)

    class FakeGraph:
        checkpointer = FakeCheckpointer()

    service.reset_thread_state("thread-to-reset", graph=FakeGraph())

    assert deleted_threads == ["thread-to-reset"]


def test_postgres_database_config_normalizes_jdbc_url_and_schema() -> None:
    database_config = resolve_postgres_database_config(
        "jdbc:postgresql://192.0.2.10:5432/postgres",
        schema="sdr_ia",
    )

    assert database_config is not None
    assert database_config.url == "postgresql://192.0.2.10:5432/postgres"
    assert database_config.schema == "sdr_ia"


def test_postgres_database_config_can_read_schema_from_extra_path() -> None:
    database_config = resolve_postgres_database_config(
        "jdbc:postgresql://192.0.2.10:5432/postgres/sdr-ia"
    )

    assert database_config is not None
    assert database_config.url == "postgresql://192.0.2.10:5432/postgres"
    assert database_config.schema == "sdr-ia"


def test_settings_rejects_invalid_postgres_pool_size() -> None:
    with pytest.raises(
        ValueError,
        match="LANGGRAPH_CHECKPOINT_POOL_MAX_SIZE must be greater than or equal to",
    ):
        Settings(
            _env_file=None,
            langgraph_checkpoint_pool_min_size=8,
            langgraph_checkpoint_pool_max_size=2,
        )


def test_build_runtime_uses_pooled_postgres_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    fake_connection = object()

    def fake_check_connection(connection: object) -> None:
        calls["checked_connection"] = connection

    class FakeConnectionPool:
        check_connection = staticmethod(fake_check_connection)

        def __init__(self, conninfo: str, **kwargs: object) -> None:
            calls["pool"] = self
            calls["database_url"] = conninfo
            calls["pool_kwargs"] = kwargs

        def open(self, *, wait: bool = False, timeout: float = 30.0) -> None:
            calls["open"] = {"wait": wait, "timeout": timeout}
            configure = calls["pool_kwargs"]["configure"]
            configure(fake_connection)

        def close(self) -> None:
            calls["closed"] = True

    monkeypatch.setattr(runtime_module, "ConnectionPool", FakeConnectionPool)
    monkeypatch.setattr(
        runtime_module,
        "prepare_postgres_connection",
        lambda connection, schema: calls.update({"connection": connection, "schema": schema}),
    )
    monkeypatch.setattr(
        runtime_module,
        "build_graph",
        lambda *, checkpointer: {"checkpointer": checkpointer},
    )

    runtime = build_runtime(
        Settings(
            _env_file=None,
            database_url="jdbc:postgresql://192.0.2.10:5432/postgres",
            langgraph_checkpoint_schema="sdr_ia",
            langgraph_checkpoint_pool_min_size=2,
            langgraph_checkpoint_pool_max_size=7,
            langgraph_checkpoint_pool_timeout_seconds=3.5,
        )
    )

    assert runtime.checkpointer.conn is calls["pool"]
    assert runtime.database_pool is calls["pool"]
    assert runtime.database_schema == "sdr_ia"
    assert calls["database_url"] == "postgresql://192.0.2.10:5432/postgres"
    assert calls["connection"] is fake_connection
    assert calls["schema"] == "sdr_ia"
    assert calls["open"] == {"wait": True, "timeout": 3.5}
    pool_kwargs = calls["pool_kwargs"]
    assert pool_kwargs["kwargs"] == {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": runtime_module.dict_row,
    }
    assert pool_kwargs["min_size"] == 2
    assert pool_kwargs["max_size"] == 7
    assert pool_kwargs["timeout"] == 3.5
    assert pool_kwargs["reconnect_timeout"] == 3.5
    assert pool_kwargs["check"] is fake_check_connection
    assert pool_kwargs["name"] == "langgraph-checkpointer"
    assert pool_kwargs["open"] is False
    runtime.close()
    assert calls["closed"] is True


def test_postgres_idempotency_store_setup_failure_prevents_startup_and_closes_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    calls: dict[str, object] = {}

    class FakeRuntime:
        graph = object()
        checkpointer = object()
        database_pool = object()
        database_schema = "sdr_ia"

        def close(self) -> None:
            calls["closed"] = True

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(main_module, "build_runtime", lambda settings: fake_runtime)
    monkeypatch.setattr(
        main_module,
        "_build_runtime_settings_service",
        lambda *args, **kwargs: main_module.RuntimeSettingsService(
            main_module.InMemoryRuntimeSettingsStore(),
            kwargs["bootstrap"],
            app_env=kwargs["app_env"],
        ),
    )
    monkeypatch.setattr(
        main_module,
        "_build_pipefacil_message_idempotency_store",
        lambda runtime: (_ for _ in ()).throw(RuntimeError("idempotency setup failed")),
    )

    with pytest.raises(RuntimeError, match="idempotency setup failed"):
        with TestClient(create_app(app_env="development")):
            pass

    assert calls["closed"] is True


def test_settings_rejects_unknown_langfuse_pipefacil_user_id_mode() -> None:
    with pytest.raises(ValueError, match="langfuse_pipefacil_user_id_mode"):
        Settings(
            _env_file=None,
            langfuse_pipefacil_user_id_mode="unsafe-mode",
        )


def test_build_runtime_closes_postgres_pool_when_graph_build_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class FakeConnectionPool:
        check_connection = staticmethod(lambda connection: None)

        def __init__(self, conninfo: str, **kwargs: object) -> None:
            calls["pool"] = self
            calls["pool_kwargs"] = kwargs

        def open(self, *, wait: bool = False, timeout: float = 30.0) -> None:
            calls["open"] = True

        def close(self) -> None:
            calls["closed"] = True

    def fail_build_graph(*, checkpointer):
        raise RuntimeError("graph failed")

    monkeypatch.setattr(runtime_module, "ConnectionPool", FakeConnectionPool)
    monkeypatch.setattr(
        runtime_module,
        "prepare_postgres_connection",
        lambda connection, schema: None,
    )
    monkeypatch.setattr(runtime_module, "build_graph", fail_build_graph)

    with pytest.raises(RuntimeError, match="graph failed"):
        build_runtime(
            Settings(
                _env_file=None,
                database_url="postgresql://postgres@example/db",
                langgraph_checkpoint_schema="sdr_ia",
            )
        )

    assert calls["closed"] is True


def test_bootstrap_postgres_checkpointer_creates_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    from langgraph.checkpoint.postgres import PostgresSaver

    calls: dict[str, object] = {}

    class FakeCheckpointer:
        conn = object()

        def setup(self) -> None:
            calls["setup"] = True

    fake_checkpointer = FakeCheckpointer()

    @contextmanager
    def fake_from_conn_string(database_url: str):
        calls["database_url"] = database_url
        yield fake_checkpointer

    monkeypatch.setattr(
        PostgresSaver,
        "from_conn_string",
        staticmethod(fake_from_conn_string),
    )
    monkeypatch.setattr(
        runtime_module,
        "prepare_postgres_connection",
        lambda connection, schema, *, create_schema=False: calls.update(
            {
                "connection": connection,
                "schema": schema,
                "create_schema": create_schema,
            }
        ),
    )

    runtime_module.bootstrap_postgres_checkpointer(
        "jdbc:postgresql://192.0.2.10:5432/postgres",
        schema="sdr_ia",
    )

    assert calls == {
        "database_url": "postgresql://192.0.2.10:5432/postgres",
        "connection": fake_checkpointer.conn,
        "schema": "sdr_ia",
        "create_schema": True,
        "setup": True,
    }


def test_production_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("PIPEFACIL_API_KEY", "pf_live_1234567890abcdef.example")
    monkeypatch.setenv("PIPEFACIL_WEBHOOK_SIGNATURE_SECRET", "test-webhook-secret")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    with pytest.raises(RuntimeConfigurationError, match="DATABASE_URL is required"):
        with TestClient(create_app(app_env="production")):
            pass


def test_production_requires_openai_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@db.test:5432/postgres")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("PIPEFACIL_API_KEY", "pf_live_1234567890abcdef.example")
    monkeypatch.setenv("PIPEFACIL_WEBHOOK_SIGNATURE_SECRET", "test-webhook-secret")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    with pytest.raises(RuntimeConfigurationError, match="OPENAI_API_KEY is required"):
        with TestClient(create_app(app_env="production")):
            pass


def test_production_requires_pipefacil_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PIPEFACIL_API_KEY", "")
    monkeypatch.setenv("PIPEFACIL_WEBHOOK_SIGNATURE_SECRET", "test-webhook-secret")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    with pytest.raises(RuntimeConfigurationError, match="PIPEFACIL_API_KEY is required"):
        with TestClient(create_app(app_env="production")):
            pass


def test_production_requires_pipefacil_webhook_signature_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PIPEFACIL_API_KEY", "pf_live_1234567890abcdef.example")
    monkeypatch.setenv("PIPEFACIL_WEBHOOK_SIGNATURE_SECRET", "")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    with pytest.raises(
        RuntimeConfigurationError,
        match="PIPEFACIL_WEBHOOK_SIGNATURE_SECRET is required",
    ):
        with TestClient(create_app(app_env="production")):
            pass


def test_production_rejects_disabled_webhook_signature_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(
        RuntimeConfigurationError,
        match="Webhook signature verification must remain enabled",
    ):
        main_module._validate_production_settings(
            Settings(
                app_env="production",
                pipefacil_api_key="pf_live_1234567890abcdef.example",
                pipefacil_webhook_signature_enabled=False,
            )
        )


def test_production_hides_internal_routes_and_api_documentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRuntime:
        database_pool = None
        database_schema = None
        checkpointer = InMemorySaver()
        graph = object()

        def close(self) -> None:
            pass

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@db.test:5432/postgres")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("PIPEFACIL_API_KEY", "pf_live_1234567890abcdef.example")
    monkeypatch.setenv("PIPEFACIL_WEBHOOK_SIGNATURE_ENABLED", "true")
    monkeypatch.setenv("PIPEFACIL_WEBHOOK_SIGNATURE_SECRET", "test-webhook-secret")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setattr(main_module, "build_runtime", lambda settings: FakeRuntime())

    with TestClient(create_app(app_env="production")) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/chat", json={"thread_id": "test", "message": "oi"}).status_code == 404
        assert client.get("/threads/test/state").status_code == 404
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_startup_logs_signature_configuration(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("PIPEFACIL_WEBHOOK_SIGNATURE_ENABLED", "true")
    monkeypatch.setenv("PIPEFACIL_WEBHOOK_SIGNATURE_SECRET", "test-webhook-secret")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    caplog.set_level(logging.INFO, logger="app.main")

    with TestClient(create_app(app_env="staging")):
        pass

    startup_record = next(
        record for record in caplog.records if record.message == "Application startup completed."
    )

    assert startup_record.pipefacil_webhook_signature_enabled is True
    assert not hasattr(startup_record, "pipefacil_webhook_signature_has_secret")


def test_run_agent_uses_injected_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    class FakeGraph:
        def __init__(self) -> None:
            self.calls = []

        def invoke(self, state, config=None):
            self.calls.append((state, config))
            return {
                "messages": [],
                "status": "responded",
                "intent": "fallback",
                "latest_user_message": "",
                "response_text": "ok",
            }

    fake_graph = FakeGraph()
    result = run_agent({"messages": []}, graph=fake_graph)

    assert result["response_text"] == "ok"
    assert len(fake_graph.calls) == 1


def test_run_agent_adds_langchain_callbacks_for_text_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    class FakeGraph:
        def __init__(self) -> None:
            self.config = None

        def invoke(self, state, config=None):
            self.config = config
            return {
                "messages": [],
                "status": "responded",
                "intent": "fallback",
                "latest_user_message": "oi",
                "response_text": "ok",
            }

    fake_graph = FakeGraph()
    monkeypatch.setattr(service, "get_langchain_callbacks", lambda: ["trace"])

    run_agent({"messages": [{"type": "human", "content": "oi"}]}, graph=fake_graph)

    assert fake_graph.config["callbacks"] == ["trace"]


def test_run_agent_suppresses_langchain_callbacks_for_binary_multimodal_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    class FakeGraph:
        def __init__(self) -> None:
            self.config = None

        def invoke(self, state, config=None):
            self.config = config
            return {
                "messages": [],
                "status": "responded",
                "intent": "fallback",
                "latest_user_message": "arquivo",
                "response_text": "ok",
            }

    fake_graph = FakeGraph()
    monkeypatch.setattr(service, "get_langchain_callbacks", lambda: ["trace"])
    message = HumanMessage(
        content=[
            {"type": "text", "text": "Tipo de mensagem: file"},
            {
                "type": "file",
                "base64": "very-sensitive-file-base64",
                "mime_type": "application/pdf",
                "filename": "contrato.pdf",
            },
        ]
    )

    run_agent(
        {"messages": [message]},
        graph=fake_graph,
        config={"callbacks": ["existing-trace"]},
    )

    assert "callbacks" not in fake_graph.config
    assert fake_graph.config["configurable"]["thread_id"].startswith("agent-run-")


def test_run_agent_records_clean_text_trace_input_and_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    captured_kwargs: list[dict[str, object]] = []
    captured_updates: list[dict[str, object]] = []

    class FakeGraph:
        def invoke(self, state, config=None):
            return {
                "messages": [],
                "status": "responded",
                "intent": "greeting",
                "latest_user_message": "oi",
                "response_text": "Oi! Como posso ajudar?",
            }

    class FakeObservation:
        def update(self, **kwargs):
            captured_updates.append(kwargs)

    @contextmanager
    def fake_observe_agent_run(**kwargs):
        captured_kwargs.append(kwargs)
        yield FakeObservation()

    monkeypatch.setattr(service, "observe_agent_run", fake_observe_agent_run)

    run_agent({"messages": [{"type": "human", "content": "oi"}]}, graph=FakeGraph())

    assert captured_kwargs == [
        {
            "name": "run-sdr-agent-template",
            "input": "oi",
            "session_id": None,
            "user_id": None,
            "tags": ["langgraph", "sdr-agent-template"],
            "metadata": {"graph": "sdr-agent-template"},
        }
    ]
    assert captured_updates == [{"output": "Oi! Como posso ajudar?"}]
