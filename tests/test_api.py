from __future__ import annotations

import gzip
import hashlib
import hmac
import json
from copy import deepcopy

import pytest
from fastapi import BackgroundTasks, HTTPException, Response
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app.agent.nodes.intent as intent_nodes
import app.agent.nodes.response as response_nodes
import app.api.request_body as request_body_module
import app.api.routes.chat as chat_routes
import app.api.routes.ops as ops_routes
import app.api.routes.webhooks as webhook_routes
import app.application.conversations as conversation_application
import app.application.pipefacil as pipefacil_application
import app.main as main_app
from app.agent.chains.schemas import IntentClassification
from app.api.schemas import MessageReceivedEventRequest
from app.application.dto import (
    ChatTurnResult,
    ResponsePartResult,
)
from app.application.idempotency import InMemoryMessageIdempotencyStore
from app.core.config import Settings, get_bootstrap_settings, get_settings
from app.core.logging import RawLogValue
from app.integrations.pipefacil import (
    PipefacilConversationHistory,
    PipefacilConversationMessage,
    PipefacilSendMessageError,
    resolve_message_received_session_id,
    resolve_message_received_trace_user_id,
)
from app.main import create_app
from app.observability import reset_langfuse_clients


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, object]]] = []

    def info(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("info", message, dict(kwargs.get("extra") or {})))

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("warning", message, dict(kwargs.get("extra") or {})))

    def exception(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("exception", message, dict(kwargs.get("extra") or {})))


@pytest.fixture(autouse=True)
def clear_settings_and_langfuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("SETTINGS_ADMIN_KEY_HASH", "scrypt$test")
    monkeypatch.setenv("SETTINGS_SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(main_app, "APPLICATION_ENV", "development")
    reset_langfuse_clients()
    get_bootstrap_settings.cache_clear()
    get_settings.cache_clear()
    yield
    reset_langfuse_clients()
    get_bootstrap_settings.cache_clear()
    get_settings.cache_clear()


def _message_received_payload(body: str = "ooi") -> dict[str, object]:
    return {
        "type": "message.received",
        "timestamp": "2026-07-17T19:00:35.647518560Z",
        "data": {
            "message": {
                "id": "08809645-ca3d-4030-85d1-31ec52f5e196",
                "externalId": (
                    "wamid.EXAMPLE_MESSAGE_000000000000000000000000000000000000000000000003"
                ),
                "body": body,
                "type": "text",
                "timestamp": "2026-07-17T19:00:33Z",
                "media": None,
            },
            "channel": {
                "id": "channel-example-001",
                "phoneNumberId": "111111111111111",
                "phoneNumber": "+55 11 00000-0000",
                "displayName": None,
            },
            "contact": {
                "id": "contact-example-001",
                "name": "CLIENTE EXEMPLO",
                "phone": "+5511000000001",
                "email": None,
            },
            "deal": {
                "id": "deal-example-001",
                "seq": 100,
                "name": "Cliente Exemplo",
                "properties": {"lead_source": "website"},
                "stage": {
                    "id": "stage-example-001",
                    "name": "Qualificacao IA",
                },
            },
        },
    }


def _media_message_received_payload(
    *,
    message_type: str = "image",
    media: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = deepcopy(_message_received_payload(body=""))
    payload["data"]["message"]["type"] = message_type
    payload["data"]["message"]["body"] = None
    payload["data"]["message"]["media"] = media or {
        "id": "media-123",
        "mimeType": "image/jpeg",
        "downloadUrl": "https://example.invalid/private-media.jpg?X-Amz-Signature=secret",
        "size": 123456,
    }
    return payload


def _signed_webhook_headers(
    *,
    body: bytes,
    secret: str,
    timestamp: str = "1790253675698",
) -> dict[str, str]:
    signed_payload = timestamp.encode("utf-8") + b"." + body
    signature = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Pipefacil-Signature-256": f"sha256={signature}",
        "X-Pipefacil-Timestamp": timestamp,
        "X-Pipefacil-Event": "message.received",
    }


def test_chat_endpoint_returns_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    class FakeClassifierChain:
        def invoke(self, payload, config=None):
            assert payload["latest_user_message"] == "oi"
            assert config["configurable"]["thread_id"] == "thread-1"
            return IntentClassification(intent="greeting", reason="Saudacao.")

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            assert payload["intent"] == "greeting"
            assert config["configurable"]["thread_id"] == "thread-1"
            return AIMessage(content="Oi! Como posso ajudar?")

    monkeypatch.setattr(intent_nodes, "_build_classifier_chain", lambda: FakeClassifierChain())
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    with TestClient(create_app()) as client:
        response = client.post("/chat", json={"thread_id": "thread-1", "message": "oi"})

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "intent": "greeting",
        "intent_reason": "Saudacao.",
        "response_text": "Oi! Como posso ajudar?",
        "response_messages": ["Oi! Como posso ajudar?"],
        "response_parts": [
            {
                "type": "text",
                "text": "Oi! Como posso ajudar?",
                "media_id": None,
                "caption": None,
                "content_type": None,
                "filename": None,
            }
        ],
        "status": "responded",
        "delivery_status": None,
        "delivery_error": None,
    }


def test_resume_conversation_reads_history_and_uses_resume_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        conversation_application,
        "fetch_pipefacil_conversation_history",
        lambda **_: PipefacilConversationHistory(
            messages=[
                PipefacilConversationMessage(role="user", content="Vou passar o cartao."),
                PipefacilConversationMessage(
                    role="assistant",
                    content="Perfeito, fico no aguardo.",
                ),
            ]
        ),
    )

    class FakeClassifierChain:
        def invoke(self, payload, config=None):
            captured["classifier_message"] = payload["latest_user_message"]
            return IntentClassification(intent="request", reason="Retomada comercial.")

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            captured["history"] = payload["conversation_history"]
            captured["resume_context"] = payload["resume_context"]
            return AIMessage(content="Oi! Conseguiu concluir o pagamento no cartao?")

    monkeypatch.setattr(intent_nodes, "_build_classifier_chain", lambda: FakeClassifierChain())
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    with TestClient(create_app()) as client:
        local_turn = client.post(
            "/chat",
            json={"thread_id": "deal-example-001", "message": "historico local antigo"},
        )
        response = client.post(
            "/conversations/resume",
            json={
                "thread_id": "deal-example-001",
                "deal_seq": 100,
                "context": "Faz 3 dias que ele nao responde e ficou de passar o cartao.",
                "send_response": False,
            },
        )

    assert local_turn.status_code == 200
    assert response.status_code == 200
    assert response.json()["history_message_count"] == 2
    assert response.json()["response_text"] == "Oi! Conseguiu concluir o pagamento no cartao?"
    assert captured["classifier_message"] == "Vou passar o cartao."
    assert captured["resume_context"] == (
        "Faz 3 dias que ele nao responde e ficou de passar o cartao."
    )
    assert [message.content for message in captured["history"][0:2]] == [
        "Vou passar o cartao.",
        "Perfeito, fico no aguardo.",
    ]


def test_resume_conversation_requires_history_identifier() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/conversations/resume",
            json={"thread_id": "deal-example-001", "send_response": False},
        )

    assert response.status_code == 422


def test_development_registers_internal_routes_and_api_documentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    with TestClient(create_app()) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        assert client.post("/chat", json={}).status_code == 422
        thread_response = client.get("/threads/missing/state")

    assert thread_response.status_code == 404
    assert thread_response.json() == {"detail": "Thread 'missing' was not found."}


def test_chat_route_delegates_to_application(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    captured: dict[str, object] = {}

    def fake_run_chat_turn(**kwargs):
        captured.update(kwargs)
        return ChatTurnResult(
            thread_id="thread-1",
            intent="question",
            intent_reason="Pergunta curta.",
            response_text="Resposta roteada.",
            status="responded",
        )

    monkeypatch.setattr(chat_routes, "run_chat_turn", fake_run_chat_turn)

    with TestClient(create_app()) as client:
        response = client.post(
            "/chat",
            json={
                "thread_id": "thread-1",
                "message": "oi",
                "user_id": "user-1",
                "metadata": {"source": "test"},
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "intent": "question",
        "intent_reason": "Pergunta curta.",
        "response_text": "Resposta roteada.",
        "response_messages": [],
        "response_parts": [],
        "status": "responded",
        "delivery_status": None,
        "delivery_error": None,
    }
    assert captured["message"] == "oi"
    assert captured["thread_id"] == "thread-1"
    assert captured["user_id"] == "user-1"
    assert captured["metadata"] == {"source": "test"}
    assert captured["graph"] is not None


def test_chat_persists_history_for_same_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    class FakeClassifierChain:
        def invoke(self, payload, config=None):
            return IntentClassification(intent="question", reason="Pergunta neutra.")

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            latest_user_message = payload["latest_user_message"]
            return AIMessage(content=f"Resposta para: {latest_user_message}")

    monkeypatch.setattr(intent_nodes, "_build_classifier_chain", lambda: FakeClassifierChain())
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    with TestClient(create_app()) as client:
        first = client.post("/chat", json={"thread_id": "thread-1", "message": "primeira"})
        second = client.post("/chat", json={"thread_id": "thread-1", "message": "segunda"})
        state = client.get("/threads/thread-1/state")

    assert first.status_code == 200
    assert second.status_code == 200
    assert state.status_code == 200
    payload = state.json()
    assert payload["thread_id"] == "thread-1"
    assert payload["latest_user_message"] == "segunda"
    assert payload["status"] == "responded"
    assert [message["content"] for message in payload["messages"]] == [
        "primeira",
        "Resposta para: primeira",
        "segunda",
        "Resposta para: segunda",
    ]


def test_chat_does_not_trigger_pipefacil_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    outbound_calls: list[dict[str, object]] = []

    class FakeClassifierChain:
        def invoke(self, payload, config=None):
            return IntentClassification(intent="question", reason="Pergunta neutra.")

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            return AIMessage(content="Resposta sem webhook.")

    monkeypatch.setattr(intent_nodes, "_build_classifier_chain", lambda: FakeClassifierChain())
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: outbound_calls.append(kwargs),
    )

    with TestClient(create_app()) as client:
        response = client.post("/chat", json={"thread_id": "thread-1", "message": "oi"})

    assert response.status_code == 200
    assert outbound_calls == []


def test_threads_are_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    class FakeClassifierChain:
        def invoke(self, payload, config=None):
            return IntentClassification(intent="request", reason="Pedido neutro.")

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            return AIMessage(content=f"Echo: {payload['latest_user_message']}")

    monkeypatch.setattr(intent_nodes, "_build_classifier_chain", lambda: FakeClassifierChain())
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    with TestClient(create_app()) as client:
        client.post("/chat", json={"thread_id": "thread-a", "message": "mensagem a"})
        client.post("/chat", json={"thread_id": "thread-b", "message": "mensagem b"})
        thread_a = client.get("/threads/thread-a/state")
        thread_b = client.get("/threads/thread-b/state")

    assert [message["content"] for message in thread_a.json()["messages"]] == [
        "mensagem a",
        "Echo: mensagem a",
    ]
    assert [message["content"] for message in thread_b.json()["messages"]] == [
        "mensagem b",
        "Echo: mensagem b",
    ]


def test_thread_state_returns_404_for_missing_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    with TestClient(create_app()) as client:
        response = client.get("/threads/missing/state")

    assert response.status_code == 404
    assert response.json()["detail"] == "Thread 'missing' was not found."


def test_health_endpoint_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_events_request_logs_http_boundary_for_unknown_event_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setattr(request_body_module, "LOGGER", fake_logger)

    with TestClient(create_app()) as client:
        response = client.post("/events/unknown", json={"type": "message.received"})

    started_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "info" and message == "http.request.started"
    )
    completed_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "info" and message == "http.request.completed"
    )

    assert response.status_code == 404
    assert started_log["http_method"] == "POST"
    assert started_log["http_path"] == "/events/unknown"
    assert "content-type" in started_log["request_header_names"]
    assert completed_log["http_method"] == "POST"
    assert completed_log["http_path"] == "/events/unknown"
    assert completed_log["status_code"] == 404
    assert "duration_ms" in completed_log


def test_ready_endpoint_returns_ready_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    with TestClient(create_app()) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "runtime": {"status": "ok"},
            "checkpointer": {"status": "ok", "type": "InMemorySaver"},
            "database": {
                "status": "skipped",
                "reason": "database_url_not_configured",
            },
        },
    }


def test_ready_endpoint_returns_503_when_runtime_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    with TestClient(create_app()) as client:
        client.app.state.graph = None
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["runtime"] == {
        "status": "error",
        "reason": "graph_missing",
    }


def test_ready_endpoint_returns_503_when_database_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setattr(
        ops_routes,
        "_check_database",
        lambda settings: {
            "status": "error",
            "reason": "database_unavailable",
            "type": "Postgres",
        },
    )

    with TestClient(create_app()) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["database"] == {
        "status": "error",
        "reason": "database_unavailable",
        "type": "Postgres",
    }


def test_database_ready_check_runs_postgres_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query: str) -> None:
            assert query == "SELECT 1"

        def fetchone(self) -> tuple[int]:
            return (1,)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self) -> FakeCursor:
            return FakeCursor()

    def fake_connect(database_url: str, *, connect_timeout: int):
        calls.append((database_url, connect_timeout))
        return FakeConnection()

    monkeypatch.setattr(ops_routes.psycopg, "connect", fake_connect)

    result = ops_routes._check_database(
        Settings(_env_file=None, database_url="postgresql://postgres@example/db")
    )

    assert result == {"status": "ok", "type": "Postgres"}
    assert calls == [("postgresql://postgres@example/db", 2)]


def test_database_ready_check_uses_normalized_jdbc_url_and_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []
    prepared_schemas: list[str | None] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query: str) -> None:
            assert query == "SELECT 1"

        def fetchone(self) -> tuple[int]:
            return (1,)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self) -> FakeCursor:
            return FakeCursor()

    def fake_connect(database_url: str, *, connect_timeout: int):
        calls.append((database_url, connect_timeout))
        return FakeConnection()

    monkeypatch.setattr(ops_routes.psycopg, "connect", fake_connect)
    monkeypatch.setattr(ops_routes, "postgres_schema_exists", lambda connection, schema: True)
    monkeypatch.setattr(
        ops_routes,
        "prepare_postgres_connection",
        lambda connection, schema: prepared_schemas.append(schema),
    )

    result = ops_routes._check_database(
        Settings(
            _env_file=None,
            database_url="jdbc:postgresql://192.0.2.10:5432/postgres",
            langgraph_checkpoint_schema="sdr_ia",
        )
    )

    assert result == {"status": "ok", "type": "Postgres", "schema": "sdr_ia"}
    assert calls == [("postgresql://192.0.2.10:5432/postgres", 2)]
    assert prepared_schemas == ["sdr_ia"]


def test_database_ready_check_reports_missing_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        ops_routes.psycopg,
        "connect",
        lambda database_url, *, connect_timeout: FakeConnection(),
    )
    monkeypatch.setattr(ops_routes, "postgres_schema_exists", lambda connection, schema: False)

    result = ops_routes._check_database(
        Settings(
            _env_file=None,
            database_url="postgresql://postgres@example/db",
            langgraph_checkpoint_schema="sdr_ia",
        )
    )

    assert result == {
        "status": "error",
        "reason": "database_schema_missing",
        "type": "Postgres",
    }


def test_database_ready_check_handles_postgres_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_connect(database_url: str, *, connect_timeout: int):
        raise ops_routes.psycopg.OperationalError("connection failed")

    monkeypatch.setattr(ops_routes.psycopg, "connect", fake_connect)

    result = ops_routes._check_database(
        Settings(_env_file=None, database_url="postgresql://postgres@example/db")
    )

    assert result == {
        "status": "error",
        "reason": "database_unavailable",
        "type": "Postgres",
    }


def test_message_received_route_acknowledges_and_processes_in_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    captured: dict[str, object] = {}

    def fake_handle_message_received(payload, *, graph, settings, idempotency_store):
        captured["payload"] = payload
        captured["graph"] = graph
        captured["settings"] = settings
        captured["idempotency_store"] = idempotency_store
        return ChatTurnResult(
            thread_id="deal-example-001",
            intent="greeting",
            intent_reason="Saudacao curta.",
            response_text="Oi! Recebi sua mensagem.",
            status="responded",
            delivery_status="sent",
        )

    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        fake_handle_message_received,
    )

    with TestClient(create_app()) as client:
        response = client.post("/events/message-received", json=_message_received_payload())

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "deal-example-001",
        "intent": None,
        "intent_reason": "Pipefacil message accepted for background processing.",
        "response_text": "",
        "response_messages": [],
        "response_parts": [],
        "status": "accepted",
        "delivery_status": None,
        "delivery_error": None,
    }
    assert isinstance(captured["payload"], MessageReceivedEventRequest)
    assert captured["graph"] is not None
    assert captured["settings"] is not None
    assert isinstance(captured["idempotency_store"], InMemoryMessageIdempotencyStore)


def test_message_received_enqueues_processing_without_running_it_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background_tasks = BackgroundTasks()
    payload = MessageReceivedEventRequest.model_validate(_message_received_payload())
    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        lambda *args, **kwargs: pytest.fail("processing must not run before the route returns"),
    )

    result = webhook_routes.message_received(
        payload,
        background_tasks=background_tasks,
        graph=object(),
        settings=Settings(_env_file=None),
        idempotency_store=InMemoryMessageIdempotencyStore(),
        response=Response(),
    )

    assert result.status == "accepted"
    assert result.response_text == ""
    assert len(background_tasks.tasks) == 1


def test_message_received_background_failure_is_logged_after_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    payload = MessageReceivedEventRequest.model_validate(_message_received_payload())
    monkeypatch.setattr(webhook_routes, "LOGGER", fake_logger)
    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("background boom")),
    )

    result = webhook_routes._process_pipefacil_message_received(
        payload,
        thread_id="deal-example-001",
        graph=object(),
        settings=Settings(_env_file=None),
        idempotency_store=InMemoryMessageIdempotencyStore(),
    )

    assert result is None
    failure_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "exception" and message == "pipefacil.webhook.processing_failed"
    )
    assert failure_log["error_code"] == "unexpected_background_processing_error"
    assert failure_log["error_detail"] == "background boom"


@pytest.mark.parametrize(
    "business_status",
    ["contact_without_lead_ignored", "duplicate_message_ignored"],
)
def test_message_received_route_returns_200_for_ignored_business_statuses(
    monkeypatch: pytest.MonkeyPatch,
    business_status: str,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        lambda *args, **kwargs: ChatTurnResult(
            thread_id="deal-example-001",
            intent=None,
            intent_reason="Ignored safely.",
            response_text="",
            status=business_status,
        ),
    )

    with TestClient(create_app()) as client:
        response = client.post("/events/message-received", json=_message_received_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["response_text"] == ""


def test_message_received_route_accepts_flattened_pipefacil_message_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    captured: dict[str, object] = {}
    payload = _message_received_payload()
    message = payload["data"].pop("message")
    payload["data"].update(message)

    def fake_handle_message_received(payload, *, graph, settings, idempotency_store):
        captured["payload"] = payload
        return ChatTurnResult(
            thread_id="deal-example-001",
            intent="greeting",
            intent_reason="Saudacao curta.",
            response_text="Oi! Recebi sua mensagem.",
            status="responded",
            delivery_status="sent",
        )

    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        fake_handle_message_received,
    )

    with TestClient(create_app()) as client:
        response = client.post("/events/message-received", json=payload)

    assert response.status_code == 200
    assert isinstance(captured["payload"], MessageReceivedEventRequest)
    assert captured["payload"].data.message.id == message["id"]
    assert captured["payload"].data.message.body == message["body"]


def test_message_received_accepts_valid_webhook_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "test-webhook-secret"
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("PIPEFACIL_WEBHOOK_SIGNATURE_SECRET", secret)
    captured: dict[str, object] = {}

    def fake_handle_message_received(payload, *, graph, settings, idempotency_store):
        captured["payload"] = payload
        return ChatTurnResult(
            thread_id="deal-example-001",
            intent="greeting",
            intent_reason="Saudacao curta.",
            response_text="Oi! Recebi sua mensagem.",
            status="responded",
            delivery_status="sent",
        )

    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        fake_handle_message_received,
    )
    body = json.dumps(_message_received_payload()).encode("utf-8")

    with TestClient(create_app()) as client:
        response = client.post(
            "/events/message-received",
            content=body,
            headers=_signed_webhook_headers(body=body, secret=secret),
        )

    assert response.status_code == 200
    assert isinstance(captured["payload"], MessageReceivedEventRequest)


def test_message_received_logs_raw_payload_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(webhook_routes, "LOGGER", fake_logger)
    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        lambda *args, **kwargs: ChatTurnResult(
            thread_id="deal-example-001",
            intent="greeting",
            intent_reason="Saudacao curta.",
            response_text="Oi! Recebi sua mensagem.",
            status="responded",
            delivery_status="sent",
        ),
    )
    payload = MessageReceivedEventRequest.model_validate(_message_received_payload())

    webhook_routes.message_received(
        payload,
        background_tasks=BackgroundTasks(),
        graph=object(),
        settings=Settings(_env_file=None, log_inbound_payloads=True),
        idempotency_store=InMemoryMessageIdempotencyStore(),
        response=Response(),
    )

    received_log = next(
        extra
        for _, message, extra in fake_logger.records
        if message == "pipefacil.webhook.received"
    )
    accepted_log = next(
        extra
        for _, message, extra in fake_logger.records
        if message == "pipefacil.webhook.accepted"
    )

    assert isinstance(received_log["raw_payload"], RawLogValue)
    assert received_log["raw_payload"].value["data"]["contact"]["phone"] == "+5511000000001"
    assert received_log["pipeline_step"] == "pipefacil.webhook.received"
    assert accepted_log["pipeline_step"] == "pipefacil.webhook.accepted"
    assert accepted_log["status_code"] == 200


def test_message_received_logs_sanitized_raw_media_payload_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(webhook_routes, "LOGGER", fake_logger)
    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        lambda *args, **kwargs: ChatTurnResult(
            thread_id="deal-example-001",
            intent="greeting",
            intent_reason="Saudacao curta.",
            response_text="Oi! Recebi sua mensagem.",
            status="responded",
            delivery_status="sent",
        ),
    )
    payload = MessageReceivedEventRequest.model_validate(
        _media_message_received_payload(message_type="image")
    )

    webhook_routes.message_received(
        payload,
        background_tasks=BackgroundTasks(),
        graph=object(),
        settings=Settings(_env_file=None, log_inbound_payloads=True),
        idempotency_store=InMemoryMessageIdempotencyStore(),
        response=Response(),
    )

    received_log = next(
        extra
        for _, message, extra in fake_logger.records
        if message == "pipefacil.webhook.received"
    )
    raw_payload = received_log["raw_payload"].value
    serialized_raw_payload = json.dumps(raw_payload)

    assert isinstance(received_log["raw_payload"], RawLogValue)
    assert "https://example.invalid/private-media.jpg" not in serialized_raw_payload
    assert "X-Amz-Signature" not in serialized_raw_payload
    assert raw_payload["data"]["message"]["media"]["download_url_present"] is True
    assert "downloadUrl" not in raw_payload["data"]["message"]["media"]


def test_message_received_does_not_log_raw_payload_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(webhook_routes, "LOGGER", fake_logger)
    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        lambda *args, **kwargs: ChatTurnResult(
            thread_id="deal-example-001",
            intent="greeting",
            intent_reason="Saudacao curta.",
            response_text="Oi! Recebi sua mensagem.",
            status="responded",
            delivery_status="sent",
        ),
    )
    payload = MessageReceivedEventRequest.model_validate(_message_received_payload())

    webhook_routes.message_received(
        payload,
        background_tasks=BackgroundTasks(),
        graph=object(),
        settings=Settings(_env_file=None, log_inbound_payloads=False),
        idempotency_store=InMemoryMessageIdempotencyStore(),
        response=Response(),
    )

    received_log = next(
        extra
        for _, message, extra in fake_logger.records
        if message == "pipefacil.webhook.received"
    )

    assert "raw_payload" not in received_log
    assert received_log["pipeline_step"] == "pipefacil.webhook.received"


def test_message_received_logs_media_summary_when_media_is_received(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(webhook_routes, "LOGGER", fake_logger)
    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        lambda *args, **kwargs: ChatTurnResult(
            thread_id="deal-example-001",
            intent="request",
            intent_reason="Imagem recebida.",
            response_text="Recebi a imagem.",
            status="responded",
            delivery_status="sent",
        ),
    )
    payload = MessageReceivedEventRequest.model_validate(
        _media_message_received_payload(message_type="image")
    )

    response = webhook_routes.message_received(
        payload,
        background_tasks=BackgroundTasks(),
        graph=object(),
        settings=Settings(_env_file=None, log_inbound_payloads=False),
        idempotency_store=InMemoryMessageIdempotencyStore(),
        response=Response(),
    )

    received_log = next(
        extra
        for _, message, extra in fake_logger.records
        if message == "pipefacil.webhook.received"
    )

    assert response.status == "accepted"
    assert response.response_text == ""
    assert received_log["message_type"] == "image"
    assert received_log["has_media"] is True
    assert received_log["media_id"] == "media-123"
    assert received_log["media_mime_type"] == "image/jpeg"
    assert received_log["media_size"] == 123456
    assert received_log["media_keys"] == ["downloadUrl", "id", "mimeType", "size"]
    assert "raw_payload" not in received_log


@pytest.mark.parametrize(
    ("message_type", "mime_type"),
    [
        ("image", "image/jpeg"),
        ("sticker", "image/webp"),
        ("audio", "audio/ogg"),
        ("document", "application/pdf"),
    ],
)
def test_message_received_accepts_multimodal_payloads_and_logs_safe_summary(
    monkeypatch: pytest.MonkeyPatch,
    message_type: str,
    mime_type: str,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(webhook_routes, "LOGGER", fake_logger)
    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        lambda *args, **kwargs: ChatTurnResult(
            thread_id="deal-example-001",
            intent="request",
            intent_reason="Midia recebida.",
            response_text="Recebi sua mensagem.",
            status="responded",
            delivery_status="sent",
        ),
    )
    payload = MessageReceivedEventRequest.model_validate(
        _media_message_received_payload(
            message_type=message_type,
            media={
                "mimeType": mime_type,
                "filename": None,
                "downloadUrl": "https://example.invalid/private-media?X-Amz-Signature=secret",
            },
        )
    )

    response = webhook_routes.message_received(
        payload,
        background_tasks=BackgroundTasks(),
        graph=object(),
        settings=Settings(_env_file=None, log_inbound_payloads=True),
        idempotency_store=InMemoryMessageIdempotencyStore(),
        response=Response(),
    )

    received_log = next(
        extra
        for _, message, extra in fake_logger.records
        if message == "pipefacil.webhook.received"
    )
    raw_payload = received_log["raw_payload"].value
    serialized_raw_payload = json.dumps(raw_payload)

    assert response.status == "accepted"
    assert response.response_text == ""
    assert received_log["message_type"] == message_type
    assert received_log["has_media"] is True
    assert received_log["media_mime_type"] == mime_type
    assert received_log["media_keys"] == ["downloadUrl", "filename", "mimeType"]
    assert "downloadUrl" not in raw_payload["data"]["message"]["media"]
    assert "X-Amz-Signature" not in serialized_raw_payload
    assert raw_payload["data"]["message"]["media"]["download_url_present"] is True


def test_message_received_accepts_gzip_encoded_multimodal_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "test-webhook-secret"
    fake_main_logger = FakeLogger()
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("PIPEFACIL_WEBHOOK_SIGNATURE_SECRET", secret)
    monkeypatch.setenv("LOG_INBOUND_PAYLOADS", "true")
    monkeypatch.setattr(request_body_module, "LOGGER", fake_main_logger)
    captured: dict[str, object] = {}

    def fake_handle_message_received(payload, *, graph, settings, idempotency_store):
        captured["payload"] = payload
        return ChatTurnResult(
            thread_id="deal-example-001",
            intent="request",
            intent_reason="Audio recebido.",
            response_text="Recebi o audio.",
            status="responded",
            delivery_status="sent",
        )

    monkeypatch.setattr(
        webhook_routes,
        "handle_pipefacil_message_received",
        fake_handle_message_received,
    )
    body = json.dumps(
        _media_message_received_payload(
            message_type="audio",
            media={
                "mimeType": "audio/ogg",
                "filename": None,
                "downloadUrl": "https://example.invalid/private-audio?X-Amz-Signature=secret",
            },
        )
    ).encode("utf-8")
    compressed_body = gzip.compress(body)
    headers = {
        **_signed_webhook_headers(body=body, secret=secret),
        "Content-Encoding": "gzip",
    }

    with TestClient(create_app()) as client:
        response = client.post(
            "/events/message-received",
            content=compressed_body,
            headers=headers,
        )

    started_log = next(
        extra
        for level, message, extra in fake_main_logger.records
        if level == "info" and message == "http.request.started"
    )
    completed_log = next(
        extra
        for level, message, extra in fake_main_logger.records
        if level == "info" and message == "http.request.completed"
    )

    assert response.status_code == 200
    assert isinstance(captured["payload"], MessageReceivedEventRequest)
    assert captured["payload"].data.message.type == "audio"
    assert captured["payload"].data.message.media["mimeType"] == "audio/ogg"
    assert started_log["content_encoding"] == "gzip"
    assert completed_log["status_code"] == 200


def test_message_received_logs_inbound_rejection_without_raw_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(webhook_routes, "LOGGER", fake_logger)
    payload = MessageReceivedEventRequest.model_validate(_message_received_payload(body="  "))

    with pytest.raises(HTTPException) as exc_info:
        webhook_routes.message_received(
            payload,
            background_tasks=BackgroundTasks(),
            graph=object(),
            settings=Settings(_env_file=None, log_inbound_payloads=False),
            idempotency_store=InMemoryMessageIdempotencyStore(),
            response=Response(),
        )

    rejected_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "warning" and message == "pipefacil.webhook.rejected"
    )

    assert exc_info.value.status_code == 422
    assert rejected_log["pipeline_step"] == "pipefacil.webhook.rejected"
    assert rejected_log["error_code"] == "pipefacil_inbound_error"
    assert rejected_log["status_code"] == 422
    assert "raw_payload" not in rejected_log


def test_message_received_logs_schema_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setattr(main_app, "LOGGER", fake_logger)
    payload = _message_received_payload()
    del payload["data"]["message"]["id"]

    with TestClient(create_app()) as client:
        response = client.post("/events/message-received", json=payload)

    validation_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "warning" and message == "pipefacil.webhook.validation_failed"
    )

    assert response.status_code == 422
    assert validation_log["pipeline_step"] == "pipefacil.webhook.validation_failed"
    assert validation_log["error_code"] == "request_validation_error"
    assert validation_log["http_method"] == "POST"
    assert validation_log["http_path"] == "/events/message-received"
    assert validation_log["status_code"] == 422
    assert validation_log["validation_error_count"] == 1
    assert validation_log["validation_error_locations"] == ["body.data.message.id"]
    assert validation_log["request_json_root_type"] == "object"
    assert "data.message.body" in validation_log["request_json_key_paths"]
    assert "message" in validation_log["request_json_data_keys"]
    assert "raw_payload" not in validation_log


def test_message_received_keeps_acknowledgement_when_background_delivery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setattr(webhook_routes, "LOGGER", fake_logger)

    class FakeClassifierChain:
        def invoke(self, payload, config=None):
            return IntentClassification(intent="greeting", reason="Saudacao curta.")

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            return AIMessage(content="Oi! Recebi sua mensagem.")

    monkeypatch.setattr(intent_nodes, "_build_classifier_chain", lambda: FakeClassifierChain())
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **_: (_ for _ in ()).throw(
            PipefacilSendMessageError(
                "boom",
                error_code="pipefacil_upstream_error",
                status_code=502,
                request_id="req-outbound-1",
            )
        ),
    )

    with TestClient(create_app()) as client:
        response = client.post("/events/message-received", json=_message_received_payload())

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "deal-example-001",
        "intent": None,
        "intent_reason": "Pipefacil message accepted for background processing.",
        "response_text": "",
        "response_messages": [],
        "response_parts": [],
        "status": "accepted",
        "delivery_status": None,
        "delivery_error": None,
    }
    processing_log = next(
        extra
        for _, message, extra in fake_logger.records
        if message == "pipefacil.webhook.processing_completed"
    )
    assert processing_log["delivery_status"] == "failed"
    assert processing_log["error_code"] == "pipefacil_upstream_error"


def test_chat_response_can_return_text_and_media_response_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    def fake_run_chat_turn(**kwargs):
        return ChatTurnResult(
            thread_id="thread-1",
            intent="request",
            intent_reason="Usuario pediu catalogo.",
            response_text="Claro, segue o catalogo.",
            response_messages=["Claro, segue o catalogo."],
            response_parts=[
                ResponsePartResult(type="text", text="Claro, segue o catalogo."),
                ResponsePartResult(
                    type="document",
                    media_id="catalogo-pdf",
                    caption="Catalogo em PDF",
                    content_type="application/pdf",
                    filename="catalogo.pdf",
                ),
            ],
            status="responded",
        )

    monkeypatch.setattr(chat_routes, "run_chat_turn", fake_run_chat_turn)

    with TestClient(create_app()) as client:
        response = client.post("/chat", json={"thread_id": "thread-1", "message": "catalogo"})

    assert response.status_code == 200
    assert response.json()["response_parts"] == [
        {
            "type": "text",
            "text": "Claro, segue o catalogo.",
            "media_id": None,
            "caption": None,
            "content_type": None,
            "filename": None,
        },
        {
            "type": "document",
            "text": None,
            "media_id": "catalogo-pdf",
            "caption": "Catalogo em PDF",
            "content_type": "application/pdf",
            "filename": "catalogo.pdf",
        },
    ]


def test_message_received_requires_non_empty_text_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    event_payload = _message_received_payload(body="   ")
    event_payload["data"]["message"]["externalId"] = "wamid.123"

    with TestClient(create_app()) as client:
        response = client.post("/events/message-received", json=event_payload)

    assert response.status_code == 422
    assert response.json()["detail"] == "Inbound text message body cannot be empty."


def test_unexpected_exception_returns_500_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setattr(main_app, "LOGGER", fake_logger)
    monkeypatch.setattr(
        chat_routes,
        "run_chat_turn",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.post("/chat", json={"thread_id": "thread-1", "message": "oi"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    error_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "exception" and message == "Unhandled application error."
    )
    assert error_log["error_code"] == "unhandled_application_error"
    assert error_log["status_code"] == 500
    assert error_log["http_method"] == "POST"
    assert error_log["http_path"] == "/chat"


def test_trace_identity_uses_contact_id_for_user_and_lead_for_session() -> None:
    first_payload = MessageReceivedEventRequest.model_validate(_message_received_payload())
    second_payload_dict = deepcopy(_message_received_payload())
    second_payload_dict["data"]["deal"]["id"] = "deal-example-001-2"
    second_payload = MessageReceivedEventRequest.model_validate(second_payload_dict)

    assert resolve_message_received_trace_user_id(first_payload) == ("contact:contact-example-001")
    assert resolve_message_received_trace_user_id(second_payload) == ("contact:contact-example-001")
    assert resolve_message_received_session_id(first_payload) == "deal-example-001"
    assert resolve_message_received_session_id(second_payload) == "deal-example-001-2"


def test_trace_identity_can_opt_in_to_recognizable_contact_identity() -> None:
    payload = MessageReceivedEventRequest.model_validate(_message_received_payload())

    user_id = resolve_message_received_trace_user_id(
        payload,
        mode="contact_name_phone",
    )

    assert user_id == ("CLIENTE EXEMPLO | +5511000000001 | contact:contact-example-001")
    assert len(user_id) <= 200


def test_trace_identity_normalizes_and_limits_recognizable_contact_identity() -> None:
    payload_data = _message_received_payload()
    payload_data["data"]["contact"]["name"] = "  Joao da Silva   " * 30
    payload_data["data"]["contact"]["phone"] = "+55 (11) 99999-0000"
    payload = MessageReceivedEventRequest.model_validate(payload_data)

    user_id = resolve_message_received_trace_user_id(
        payload,
        mode="contact_name_phone",
    )

    assert len(user_id) == 200
    assert "  " not in user_id
    assert "Joao da Silva" in user_id
    assert "+5511999990000" in user_id
    assert "contact:contact-example-001" in user_id
