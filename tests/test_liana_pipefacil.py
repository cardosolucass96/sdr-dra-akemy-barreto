from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

import app.agent.chains.liana_progress as liana_chain
import app.agent.nodes.intent as intent_nodes
import app.agent.nodes.liana_progress as liana_nodes
import app.agent.nodes.response as response_nodes
import app.application.liana_pipefacil_sync as liana_sync
import app.application.pipefacil as pipefacil_application
import app.main as main_module
from app.agent import run_agent
from app.agent.chains.schemas import IntentClassification, LianaLeadProgress
from app.agent.context import AgentRunContext
from app.agent.liana_progress import (
    build_liana_progress_state_update,
    build_liana_sync_request,
)
from app.application.liana_pipefacil_sync import LianaPipefacilSyncService
from app.core.config import RuntimeSettings, Settings
from app.integrations.pipefacil.client import (
    PipefacilDealUpdateError,
    PipefacilDealUpdateResult,
    update_deal,
)
from app.integrations.pipefacil.liana_mapping import (
    LIANA_PIPELINE_ID,
    LIANA_STAGE_IDS,
    liana_qualification_fields,
    liana_stage_order,
)
from app.integrations.pipefacil.outbox import (
    InMemoryPipefacilSyncOutboxStore,
    PostgresPipefacilSyncOutboxStore,
)
from app.observability import reset_langfuse_clients
from app.outbound_media import OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT


def test_liana_sync_request_requires_explicit_interest_and_objective() -> None:
    state = {
        "pipefacil_deal_seq": 23,
        "pipefacil_current_stage_key": "entry",
        "pipefacil_sync_active": True,
        "liana_explicit_interest": True,
    }
    assert build_liana_sync_request(state) is None

    state["liana_stated_objective"] = "emagrecer"
    assert build_liana_sync_request(state) == {
        "operation_id": "liana:23:qualification",
        "deal_seq": 23,
        "target_stage_key": "qualification",
        "source_stage_key": "entry",
    }


def test_liana_keeps_interest_from_an_earlier_message() -> None:
    context = AgentRunContext(
        settings=RuntimeSettings(),
        pipefacil_deal_seq=85,
        pipefacil_stage_key="entry",
        pipefacil_sync_enqueue=lambda request: request,
        pipefacil_sync_handler=lambda request: {"status": "succeeded"},
    )
    result = SimpleNamespace(
        confidence=0.9,
        explicit_interest=False,
        stated_objective="emagrecer",
        appointment_requested=False,
        preferred_period=None,
    )
    state = {
        "latest_user_message": "Quero resolver a dificuldade para emagrecer.",
        "pipefacil_deal_seq": 85,
        "pipefacil_current_stage_key": "entry",
        "pipefacil_sync_active": True,
        "liana_explicit_interest": True,
    }

    update = build_liana_progress_state_update(state, context, lambda message: result)
    sync_request = build_liana_sync_request(state | update)

    assert sync_request is not None
    assert sync_request["target_stage_key"] == "qualification"


def test_liana_appointment_request_advances_and_never_targets_terminal_stages() -> None:
    state = {
        "pipefacil_deal_seq": 24,
        "pipefacil_current_stage_key": "qualification",
        "pipefacil_sync_active": True,
        "liana_appointment_requested": True,
    }
    request = build_liana_sync_request(state)
    assert request is not None
    assert request["target_stage_key"] == "appointment"

    state["pipefacil_current_stage_key"] = "won"
    assert build_liana_sync_request(state) is None
    state["pipefacil_current_stage_key"] = "appointment"
    state["liana_appointment_requested"] = False
    assert build_liana_sync_request(state)["target_stage_key"] == "appointment"
    assert liana_stage_order(None) is None


def test_interpret_liana_progress_uses_validated_signals_and_persists_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = LianaLeadProgress(
        explicit_interest=True,
        stated_objective="avaliar emagrecimento",
        appointment_requested=True,
        preferred_period="na próxima semana",
        confidence=0.94,
    )
    monkeypatch.setattr(
        liana_nodes,
        "invoke_with_temperature_fallback",
        lambda *args, **kwargs: result,
    )
    context = AgentRunContext(
        settings=RuntimeSettings(),
        pipefacil_deal_seq=23,
        pipefacil_stage_key="entry",
        pipefacil_sync_enqueue=lambda request: request,
        pipefacil_sync_handler=lambda request: {"status": "succeeded"},
    )

    update = liana_nodes.interpret_liana_progress(
        {
            "messages": [HumanMessage(content="Quero marcar para a próxima semana.")],
            "latest_user_message": "Quero marcar para a próxima semana.",
        },
        runtime=SimpleNamespace(context=context),
    )

    assert update["pipefacil_sync_active"] is True
    assert update["liana_explicit_interest"] is True
    assert update["liana_stated_objective"] == "avaliar emagrecimento"
    assert update["liana_appointment_requested"] is True
    assert update["liana_preferred_period"] == "na próxima semana"


def test_interpret_liana_progress_ignores_low_confidence_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = LianaLeadProgress(
        explicit_interest=True,
        stated_objective="emagrecer",
        appointment_requested=False,
        preferred_period=None,
        confidence=0.5,
    )
    monkeypatch.setattr(
        liana_nodes,
        "invoke_with_temperature_fallback",
        lambda *args, **kwargs: result,
    )
    context = AgentRunContext(
        settings=RuntimeSettings(),
        pipefacil_deal_seq=24,
        pipefacil_stage_key="entry",
        pipefacil_sync_enqueue=lambda request: request,
        pipefacil_sync_handler=lambda request: {"status": "succeeded"},
    )

    update = liana_nodes.interpret_liana_progress(
        {
            "messages": [HumanMessage(content="Talvez, ainda estou pensando.")],
            "latest_user_message": "Talvez, ainda estou pensando.",
        },
        runtime=SimpleNamespace(context=context),
    )

    assert update["liana_explicit_interest"] is False
    assert update["liana_stated_objective"] is None
    assert update["liana_appointment_requested"] is False


def test_sync_node_clears_terminal_operations_and_retains_retryable_ones() -> None:
    context = AgentRunContext(
        settings=RuntimeSettings(),
        pipefacil_sync_handler=lambda request: {"status": "succeeded", "receipt": {"id": "r1"}},
    )
    state = {"pending_pipefacil_sync": {"operation_id": "op1"}}
    success = liana_nodes.sync_liana_pipefacil_deal(state, runtime=SimpleNamespace(context=context))
    assert success["pending_pipefacil_sync"] is None
    assert success["pipefacil_sync_result"]["receipt"] == {"id": "r1"}

    pending_context = AgentRunContext(
        settings=RuntimeSettings(),
        pipefacil_sync_handler=lambda request: {"status": "pending", "error_code": "offline"},
    )
    pending = liana_nodes.sync_liana_pipefacil_deal(
        state,
        runtime=SimpleNamespace(context=pending_context),
    )
    assert "pending_pipefacil_sync" not in pending
    assert pending["pipefacil_sync_result"]["status"] == "pending"

    blocked_context = AgentRunContext(
        settings=RuntimeSettings(),
        pipefacil_sync_handler=lambda request: {"status": "blocked", "error_code": "mismatch"},
    )
    blocked = liana_nodes.sync_liana_pipefacil_deal(
        state,
        runtime=SimpleNamespace(context=blocked_context),
    )
    assert blocked["pending_pipefacil_sync"] is None
    unavailable = liana_nodes.sync_liana_pipefacil_deal({}, runtime=None)
    assert unavailable["pipefacil_sync_result"]["error_code"] == "handler_unavailable"


def test_liana_node_uses_context_settings_for_chain_build(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = object()
    monkeypatch.setattr(liana_nodes, "build_liana_progress_chain", lambda *args, **kwargs: expected)
    token = liana_nodes._RUNTIME_SETTINGS.set(RuntimeSettings(openai_model="test-model"))
    try:
        assert liana_nodes._build_chain() is expected
    finally:
        liana_nodes._RUNTIME_SETTINGS.reset(token)


@pytest.mark.parametrize("method", ["function_calling", "json_schema"])
def test_liana_progress_chain_uses_structured_output(
    method: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeModel:
        def with_structured_output(self, schema, *, method):
            captured.update(schema=schema, method=method)
            return RunnableLambda(
                lambda _: schema(
                    explicit_interest=True,
                    stated_objective="emagrecer",
                    appointment_requested=False,
                    preferred_period=None,
                    confidence=0.9,
                )
            )

    monkeypatch.setattr(liana_chain, "get_chat_model", lambda **kwargs: FakeModel())
    monkeypatch.setattr(liana_chain, "structured_output_method", lambda *args: method)
    chain = liana_chain.build_liana_progress_chain(RuntimeSettings())
    result = chain.invoke({"latest_user_message": "Quero cuidar do meu peso."})

    assert isinstance(result, LianaLeadProgress)
    assert captured["method"] == method
    assert captured["schema"].model_fields["stated_objective"].annotation == (str | None)


def test_update_deal_sends_stage_and_custom_fields_in_one_patch() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        captured["authorization"] = request.headers["Authorization"]
        return httpx.Response(200, json={"data": {"seq": 73}}, headers={"x-request-id": "req-73"})

    client = httpx.Client(
        base_url="https://pipefacil.test",
        transport=httpx.MockTransport(handler),
    )
    result = update_deal(
        seq=73,
        stage_id=LIANA_STAGE_IDS["qualification"],
        custom_fields={"atendente": "liana", "qualificacao_mkt": "mql"},
        settings=Settings(
            _env_file=None,
            pipefacil_api_key="test-api-key",
            pipefacil_base_url="https://pipefacil.test",
        ),
        client=client,
    )
    client.close()

    assert "73" in captured["url"]
    assert b'"stageId"' in captured["body"]
    assert b'"qualificacao_mkt"' in captured["body"]
    assert captured["authorization"] == "Bearer test-api-key"
    assert result.request_id == "req-73"


@pytest.mark.parametrize(
    ("seq", "custom_fields", "api_key", "error_code"),
    [
        (73, {"atendente": "liana"}, None, "pipefacil_api_key_missing"),
        (None, {"atendente": "liana"}, "secret", "pipefacil_deal_seq_missing"),
        (73, {}, "secret", "pipefacil_deal_properties_empty"),
    ],
)
def test_update_deal_rejects_invalid_requests(
    seq: int | None,
    custom_fields: dict[str, str],
    api_key: str | None,
    error_code: str,
) -> None:
    with pytest.raises(PipefacilDealUpdateError) as exc_info:
        update_deal(
            seq=seq,
            custom_fields=custom_fields,
            settings=Settings(_env_file=None, pipefacil_api_key=api_key),
        )
    assert exc_info.value.error_code == error_code


def test_update_deal_preserves_upstream_and_transport_errors() -> None:
    settings = Settings(
        _env_file=None,
        pipefacil_api_key="test-api-key",
        pipefacil_base_url="https://pipefacil.test",
    )
    upstream_client = httpx.Client(
        base_url=settings.pipefacil_base_url,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                json={"error": "unavailable"},
                headers={"x-request-id": "req-fail"},
            )
        ),
    )
    with pytest.raises(PipefacilDealUpdateError) as upstream_error:
        update_deal(
            seq=73,
            custom_fields={"atendente": "liana"},
            settings=settings,
            client=upstream_client,
        )
    upstream_client.close()
    assert upstream_error.value.error_code == "pipefacil_upstream_error"
    assert upstream_error.value.request_id == "req-fail"

    transport_client = httpx.Client(
        base_url=settings.pipefacil_base_url,
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline", request=request))
        ),
    )
    with pytest.raises(PipefacilDealUpdateError) as transport_error:
        update_deal(
            seq=73,
            custom_fields={"atendente": "liana"},
            settings=settings,
            client=transport_client,
        )
    transport_client.close()
    assert transport_error.value.error_code == "pipefacil_transport_error"


def test_update_deal_closes_a_client_it_creates(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        closed = False

        def patch(self, *args, **kwargs):
            return httpx.Response(
                200, json={"data": {}}, request=httpx.Request("PATCH", "https://api.test")
            )

        def close(self):
            self.closed = True

    client = FakeClient()

    monkeypatch.setattr("app.integrations.pipefacil.client._build_client", lambda settings: client)
    result = update_deal(
        seq=73,
        custom_fields={"atendente": "liana"},
        settings=Settings(_env_file=None, pipefacil_api_key="test-api-key"),
    )
    assert result.status_code == 200
    assert client.closed is True


def test_pipefacil_token_limit_helper_allows_messages_under_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipefacil_application,
        "_build_pipefacil_lead_token_usage",
        lambda *args, **kwargs: SimpleNamespace(exceeded=False),
    )
    result = pipefacil_application._resolve_lead_token_limit(
        SimpleNamespace(),
        source_payloads=(),
        graph=object(),
        session_id="thread-1",
        log_context={},
        settings=Settings(_env_file=None, pipefacil_max_tokens_per_lead=20),
    )
    assert result is None


def test_sync_service_moves_stage_and_sets_confirmed_custom_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryPipefacilSyncOutboxStore()
    settings = Settings(_env_file=None, pipefacil_api_key="test-api-key")
    request = {
        "operation_id": "liana:73:qualification",
        "deal_seq": 73,
        "target_stage_key": "qualification",
        "source_stage_key": "entry",
    }
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        liana_sync,
        "fetch_deal_by_seq",
        lambda **kwargs: {
            "stage": {"id": LIANA_STAGE_IDS["entry"]},
            "pipeline": {"id": LIANA_PIPELINE_ID},
        },
    )
    monkeypatch.setattr(
        liana_sync,
        "update_deal",
        lambda **kwargs: (
            captured.update(kwargs)
            or PipefacilDealUpdateResult(status_code=200, request_id="req-1", payload=None)
        ),
    )
    service = LianaPipefacilSyncService(store, settings_provider=lambda: settings)

    result = service.enqueue_and_process(request)
    duplicate_result = service.enqueue_and_process(request)

    assert result["status"] == "succeeded"
    assert duplicate_result["status"] == "succeeded"
    assert captured["stage_id"] == LIANA_STAGE_IDS["qualification"]
    assert captured["custom_fields"] == liana_qualification_fields("qualification")
    assert captured["custom_fields"]["qualificacao_mkt"] == "mql"
    assert captured["custom_fields"]["atendente"] == "liana"


def test_sync_service_persists_operation_before_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryPipefacilSyncOutboxStore()
    settings = Settings(_env_file=None, pipefacil_api_key="test-api-key")
    request = {
        "operation_id": "liana:84:qualification",
        "deal_seq": 84,
        "target_stage_key": "qualification",
    }
    monkeypatch.setattr(
        liana_sync,
        "fetch_deal_by_seq",
        lambda **kwargs: {"stageId": LIANA_STAGE_IDS["entry"]},
    )
    monkeypatch.setattr(
        liana_sync,
        "update_deal",
        lambda **kwargs: PipefacilDealUpdateResult(
            status_code=200,
            request_id="persisted",
            payload=None,
        ),
    )
    service = LianaPipefacilSyncService(store, settings_provider=lambda: settings)

    operation = service.enqueue_request(request)
    before_action = store.get(operation["operation_id"])
    result = service.process_operation(operation)

    assert before_action["status"] == "pending"
    assert operation["custom_fields"]["qualificacao_mkt"] == "mql"
    assert result["status"] == "succeeded"


def test_sync_service_updates_fields_without_repeating_current_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryPipefacilSyncOutboxStore()
    settings = Settings(_env_file=None, pipefacil_api_key="test-api-key")
    monkeypatch.setattr(
        liana_sync,
        "fetch_deal_by_seq",
        lambda **kwargs: {"stageId": LIANA_STAGE_IDS["qualification"]},
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        liana_sync,
        "update_deal",
        lambda **kwargs: (
            captured.update(kwargs)
            or PipefacilDealUpdateResult(status_code=200, request_id=None, payload=None)
        ),
    )
    service = LianaPipefacilSyncService(store, settings_provider=lambda: settings)
    result = service.enqueue_and_process(
        {
            "operation_id": "liana:78:qualification",
            "deal_seq": 78,
            "target_stage_key": "qualification",
            "source_stage_key": "entry",
        }
    )
    assert result["status"] == "succeeded"
    assert captured["stage_id"] is None
    assert captured["custom_fields"]["qualificacao_mkt"] == "mql"


def test_sync_service_sets_sql_at_appointment_and_does_not_regress_later_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryPipefacilSyncOutboxStore()
    settings = Settings(_env_file=None, pipefacil_api_key="test-api-key")
    request = {
        "operation_id": "liana:74:appointment",
        "deal_seq": 74,
        "target_stage_key": "appointment",
        "source_stage_key": "qualification",
    }
    captured: dict[str, Any] = {}
    current_stage = LIANA_STAGE_IDS["qualification"]
    monkeypatch.setattr(
        liana_sync,
        "fetch_deal_by_seq",
        lambda **kwargs: {
            "stage": {"id": current_stage},
            "pipeline": {"id": LIANA_PIPELINE_ID},
        },
    )
    monkeypatch.setattr(
        liana_sync,
        "update_deal",
        lambda **kwargs: (
            captured.update(kwargs)
            or PipefacilDealUpdateResult(status_code=200, request_id="req-2", payload=None)
        ),
    )
    service = LianaPipefacilSyncService(store, settings_provider=lambda: settings)
    result = service.enqueue_and_process(request)
    assert result["status"] == "succeeded"
    assert captured["stage_id"] == LIANA_STAGE_IDS["appointment"]
    assert captured["custom_fields"]["qualificacao_mkt"] == "mql"
    assert captured["custom_fields"]["qualificacao_vendas"] == "sql"

    current_stage = LIANA_STAGE_IDS["attended_no_sale"]
    blocked = service.enqueue_and_process(
        {
            "operation_id": "liana:75:qualification",
            "deal_seq": 75,
            "target_stage_key": "qualification",
            "source_stage_key": "entry",
        }
    )
    assert blocked["status"] == "blocked"
    assert blocked["error_code"] == "deal_already_advanced"


def test_sync_service_retains_failure_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryPipefacilSyncOutboxStore()
    settings = Settings(_env_file=None, pipefacil_api_key="test-api-key")
    monkeypatch.setattr(
        liana_sync,
        "fetch_deal_by_seq",
        lambda **kwargs: {"stage": {"id": LIANA_STAGE_IDS["entry"]}},
    )
    monkeypatch.setattr(
        liana_sync,
        "update_deal",
        lambda **kwargs: (_ for _ in ()).throw(
            PipefacilDealUpdateError("unavailable", error_code="pipefacil_transport_error")
        ),
    )
    service = LianaPipefacilSyncService(store, settings_provider=lambda: settings)
    result = service.enqueue_and_process(
        {
            "operation_id": "liana:76:qualification",
            "deal_seq": 76,
            "target_stage_key": "qualification",
            "source_stage_key": "entry",
        }
    )

    assert result == {"status": "pending", "error_code": "pipefacil_transport_error"}
    stored = store.get("liana:76:qualification")
    assert stored is not None
    assert stored["status"] == "pending"
    assert stored["last_error"]["error_code"] == "pipefacil_transport_error"
    assert service.process_next() is True


def test_sync_service_blocks_other_pipeline_and_unknown_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryPipefacilSyncOutboxStore()
    settings = Settings(_env_file=None, pipefacil_api_key="test-api-key")
    deal = {"stage": {"id": LIANA_STAGE_IDS["entry"]}, "pipeline": {"id": "another-pipeline"}}
    monkeypatch.setattr(liana_sync, "fetch_deal_by_seq", lambda **kwargs: deal)
    monkeypatch.setattr(
        liana_sync,
        "update_deal",
        lambda **kwargs: pytest.fail("must not patch a different pipeline"),
    )
    service = LianaPipefacilSyncService(store, settings_provider=lambda: settings)
    wrong_pipeline = service.enqueue_and_process(
        {
            "operation_id": "liana:79:qualification",
            "deal_seq": 79,
            "target_stage_key": "qualification",
            "source_stage_key": "entry",
        }
    )
    assert wrong_pipeline["error_code"] == "deal_pipeline_mismatch"

    deal = {"stageId": "unknown-stage"}
    unknown_stage = service.enqueue_and_process(
        {
            "operation_id": "liana:80:qualification",
            "deal_seq": 80,
            "target_stage_key": "qualification",
            "source_stage_key": "entry",
        }
    )
    assert unknown_stage["error_code"] == "deal_stage_unrecognized"


def test_sync_service_handles_empty_queue_invalid_request_and_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryPipefacilSyncOutboxStore()
    settings = Settings(_env_file=None, pipefacil_api_key="test-api-key")
    service = LianaPipefacilSyncService(store, settings_provider=lambda: settings)
    assert service.process_next() is False
    assert service._process_claimed(None)["error_code"] == "operation_already_claimed"
    unavailable = liana_sync._existing_operation_result(None)
    assert unavailable["error_code"] == "operation_status_unavailable"
    with pytest.raises(ValueError):
        liana_sync._build_pipefacil_operation(
            {"operation_id": "bad", "deal_seq": 1, "target_stage_key": "won"}
        )
    with pytest.raises(ValueError):
        liana_sync._build_pipefacil_operation(
            {"operation_id": "bad", "deal_seq": 0, "target_stage_key": "qualification"}
        )

    monkeypatch.setattr(
        liana_sync,
        "fetch_deal_by_seq",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("database response shape changed")),
    )
    result = service.enqueue_and_process(
        {
            "operation_id": "liana:82:qualification",
            "deal_seq": 82,
            "target_stage_key": "qualification",
        }
    )
    assert result["error_code"] == "pipefacil_sync_unexpected_error"
    assert store.get("liana:82:qualification")["last_error"]["error_code"] == result["error_code"]


def test_sync_service_blocks_unknown_target_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryPipefacilSyncOutboxStore()
    settings = Settings(_env_file=None, pipefacil_api_key="test-api-key")
    monkeypatch.setattr(
        liana_sync,
        "fetch_deal_by_seq",
        lambda **kwargs: {"stageId": LIANA_STAGE_IDS["entry"]},
    )
    service = LianaPipefacilSyncService(store, settings_provider=lambda: settings)
    malformed = {
        "operation_id": "malformed",
        "deal_seq": 83,
        "target_stage_key": "qualification",
        "stage_id": "unknown-stage",
        "custom_fields": {"atendente": "liana"},
    }
    store.enqueue(malformed)
    malformed["target_stage_key"] = "invalid"
    result = service._apply_operation(malformed)
    assert result == {"status": "blocked", "error_code": "deal_stage_unrecognized"}


class _FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self.row = None
        self.empty_claim = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, params=None) -> None:
        query_text = str(query)
        self.calls.append((query_text, params))
        if "RETURNING task.operation_id" in query_text:
            self.row = (
                None
                if self.empty_claim
                else {
                    "operation_id": "op1",
                    "payload": {"operation_id": "op1", "deal_seq": 1},
                    "attempt_count": 2,
                }
            )
        elif "SELECT status" in query_text:
            self.row = (
                None
                if params == ("missing",)
                else {"status": "pending", "receipt": None, "last_error": {"code": "x"}}
            )
        else:
            self.row = None

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self, **kwargs):
        return self.cursor_instance


class _FakePool:
    def __init__(self) -> None:
        self.connection_instance = _FakeConnection()

    def connection(self):
        return self.connection_instance


def test_postgres_outbox_builds_table_and_claims_idempotently() -> None:
    pool = _FakePool()
    store = PostgresPipefacilSyncOutboxStore(pool)
    store.setup()
    operation = {"operation_id": "op1", "deal_seq": 1}
    store.enqueue(operation)
    claimed = store.claim()
    assert claimed == {"operation_id": "op1", "deal_seq": 1, "attempt_count": 2}
    assert store.get("op1")["status"] == "pending"
    assert store.claim("op1")["operation_id"] == "op1"
    assert store.get("missing") is None
    store.mark_succeeded("op1", {"request_id": "r1"})
    store.mark_failed("op1", {"code": "x"}, retry_after_seconds=8)
    store.mark_blocked("op1", {"code": "blocked"})
    sql_calls = pool.connection_instance.cursor_instance.calls
    assert "CREATE TABLE IF NOT EXISTS" in sql_calls[0][0]
    assert "SKIP LOCKED" in next(query for query, _ in sql_calls if "SKIP LOCKED" in query)

    schema_store = PostgresPipefacilSyncOutboxStore(pool, schema="crm")
    schema_store.setup()
    pool.connection_instance.cursor_instance.empty_claim = True
    assert schema_store.claim() is None


def test_main_outbox_retry_loop_continues_until_stop() -> None:
    class FakeService:
        def __init__(self) -> None:
            self.calls = 0

        def process_next(self) -> bool:
            self.calls += 1
            return self.calls == 1

    service = FakeService()
    stop_event = asyncio.Event()

    async def stop_after_idle() -> None:
        while service.calls < 2:
            await asyncio.sleep(0)
        stop_event.set()

    async def run() -> None:
        await asyncio.gather(
            main_module._retry_liana_pipefacil_sync_outbox(service, stop_event),
            stop_after_idle(),
        )

    asyncio.run(run())
    assert service.calls == 2


def test_main_outbox_retry_loop_survives_idle_poll_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IdleService:
        def process_next(self) -> bool:
            return False

    stop_event = asyncio.Event()

    async def timeout_wait(awaitable, *, timeout):
        del timeout
        awaitable.close()
        stop_event.set()
        raise TimeoutError

    monkeypatch.setattr(main_module.asyncio, "wait_for", timeout_wait)
    asyncio.run(main_module._retry_liana_pipefacil_sync_outbox(IdleService(), stop_event))


def test_main_outbox_retry_loop_survives_database_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnstableService:
        calls = 0

        def process_next(self) -> bool:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("database temporarily unavailable")
            stop_event.set()
            return False

    service = UnstableService()
    stop_event = asyncio.Event()

    async def timeout_wait(awaitable, *, timeout):
        del timeout
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(main_module.asyncio, "wait_for", timeout_wait)
    asyncio.run(main_module._retry_liana_pipefacil_sync_outbox(service, stop_event))
    assert service.calls == 2


def test_graph_immediately_routes_explicit_progress_to_sync_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setattr(
        intent_nodes,
        "_build_classifier_chain",
        lambda: SimpleNamespace(
            invoke=lambda *args, **kwargs: IntentClassification(
                intent="question",
                reason="Pergunta comercial.",
            )
        ),
    )
    monkeypatch.setattr(
        liana_nodes,
        "invoke_with_temperature_fallback",
        lambda *args, **kwargs: LianaLeadProgress(
            explicit_interest=True,
            stated_objective="emagrecimento",
            appointment_requested=False,
            preferred_period=None,
            confidence=0.95,
        ),
    )
    monkeypatch.setattr(
        response_nodes,
        "_get_available_media_prompt_view",
        lambda: OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT,
    )
    monkeypatch.setattr(
        response_nodes,
        "_build_responder_chain",
        lambda **kwargs: SimpleNamespace(
            invoke=lambda *args, **invoke_kwargs: AIMessage(content="Entendi seu objetivo.")
        ),
    )
    enqueued: list[dict[str, Any]] = []
    captured: list[dict[str, Any]] = []
    settings = Settings(_env_file=None, langfuse_enabled=False)
    result = run_agent(
        {"messages": [HumanMessage(content="Quero ajuda para emagrecer.")]},
        settings=settings,
        pipefacil_deal_seq=81,
        pipefacil_stage_key="entry",
        pipefacil_sync_enqueue=lambda request: enqueued.append(request) or request,
        pipefacil_sync_handler=lambda request: (
            captured.append(request) or {"status": "succeeded", "receipt": {"request_id": "req-81"}}
        ),
    )

    assert enqueued[0]["target_stage_key"] == "qualification"
    assert captured[0] == enqueued[0]
    assert result["pipefacil_sync_result"]["status"] == "succeeded"
    assert result["pending_pipefacil_sync"] is None
    reset_langfuse_clients()


def test_main_sync_service_requires_persistent_runtime_database() -> None:
    service = main_module._build_liana_pipefacil_sync_service(
        SimpleNamespace(database_pool=None),
        settings_provider=lambda: Settings(_env_file=None),
    )
    assert service is None


def test_main_sync_service_builds_postgres_outbox_when_database_exists() -> None:
    pool = _FakePool()
    service = main_module._build_liana_pipefacil_sync_service(
        SimpleNamespace(database_pool=pool, database_schema="crm"),
        settings_provider=lambda: Settings(_env_file=None),
    )
    assert service is not None


def test_main_lifespan_starts_and_stops_sync_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    class FakeRuntime:
        database_pool = object()
        database_schema = None
        graph = object()
        checkpointer = object()

        def close(self) -> None:
            return None

    class FakeSettingsService:
        def get_execution_settings(self):
            return Settings(_env_file=None, langfuse_enabled=False)

    class FakeSyncService:
        def enqueue_request(self, request):
            return request

        def process_operation(self, request):
            return {"status": "succeeded"}

        def process_next(self) -> bool:
            return False

    monkeypatch.setattr(main_module, "build_runtime", lambda settings: FakeRuntime())
    monkeypatch.setattr(
        main_module,
        "_build_pipefacil_message_idempotency_store",
        lambda runtime: InMemoryPipefacilSyncOutboxStore(),
    )
    monkeypatch.setattr(
        main_module,
        "_build_runtime_settings_service",
        lambda *args, **kwargs: FakeSettingsService(),
    )
    monkeypatch.setattr(
        main_module,
        "_build_liana_pipefacil_sync_service",
        lambda *args, **kwargs: FakeSyncService(),
    )
    monkeypatch.setattr(main_module, "warm_up_langfuse", lambda settings: None)
    monkeypatch.setattr(main_module, "flush_langfuse", lambda settings: None)

    with TestClient(main_module.create_app(app_env="development")):
        pass
