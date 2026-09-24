from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

import app.application.chat as chat_application
import app.application.pipefacil as pipefacil_application
from app.application.dto import (
    ChatTurnResult,
    ResponseAudioResult,
    ResponsePartResult,
    SerializedMessageResult,
    ThreadStateResult,
)
from app.application.generated_audio import GeneratedAudioAsset, GeneratedAudioError
from app.application.idempotency import InMemoryMessageIdempotencyStore
from app.core.config import Settings, get_settings
from app.integrations.pipefacil import (
    MessageReceivedEventRequest,
    NormalizedInboundMessage,
    PipefacilMediaProcessingError,
    PipefacilSendMessageError,
    PipefacilSendMessageResult,
)
from app.observability import reset_langfuse_clients


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, object]]] = []

    def info(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("info", message, dict(kwargs.get("extra") or {})))

    def debug(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("debug", message, dict(kwargs.get("extra") or {})))

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("warning", message, dict(kwargs.get("extra") or {})))

    def exception(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append(("exception", message, dict(kwargs.get("extra") or {})))


@pytest.fixture(autouse=True)
def clear_settings_and_langfuse() -> None:
    reset_langfuse_clients()
    get_settings.cache_clear()
    yield
    reset_langfuse_clients()
    get_settings.cache_clear()


def _message_received_payload(
    body: str = "ooi",
) -> MessageReceivedEventRequest:
    deal: dict[str, object] = {
        "id": "deal-example-001",
        "seq": 100,
        "name": "Cliente Exemplo",
        "stage": {
            "id": "stage-example-001",
            "name": "Qualificacao IA",
        },
    }
    return MessageReceivedEventRequest.model_validate(
        {
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
                    "phone": "+55 (11) 00000-0001",
                    "email": None,
                },
                "deal": deal,
            },
        }
    )


def _message_batch_received_payload(*bodies: str) -> MessageReceivedEventRequest:
    payload_data = _message_received_payload().model_dump(mode="json")
    first_message = payload_data["data"].pop("message")
    payload_data["data"]["messages"] = [
        {
            **first_message,
            "id": f"batch-message-{index}",
            "externalId": f"batch-external-message-{index}",
            "body": body,
            "timestamp": f"2026-07-17T19:00:{32 + index:02d}Z",
        }
        for index, body in enumerate(bodies, start=1)
    ]
    return MessageReceivedEventRequest.model_validate(payload_data)


def _message_received_payload_with_deal_extra(
    extra: dict[str, object],
) -> MessageReceivedEventRequest:
    payload = _message_received_payload().model_dump(mode="json")
    payload["data"]["deal"].update(extra)
    return MessageReceivedEventRequest.model_validate(payload)


def _message_received_payload_without_deal() -> MessageReceivedEventRequest:
    payload = _message_received_payload().model_dump(mode="json")
    payload["data"]["deal"] = None
    return MessageReceivedEventRequest.model_validate(payload)


def test_merge_normalized_inbound_messages_combines_media_content_and_totals() -> None:
    merged = pipefacil_application._merge_normalized_inbound_messages(
        [
            NormalizedInboundMessage(
                message_type="text",
                content="Primeira mensagem.",
                text="Primeira mensagem.",
            ),
            NormalizedInboundMessage(
                message_type="image",
                content=[{"type": "image", "base64": "anVzdC1ieXRlcw=="}],
                text="Imagem recebida.",
                has_media=True,
                media_size=10,
            ),
            NormalizedInboundMessage(
                message_type="audio",
                content="Audio transcrito.",
                text="Audio transcrito.",
                has_media=True,
                media_size=15,
                transcription_length=18,
            ),
        ]
    )

    assert merged.message_type == "batch"
    assert merged.text == (
        "Mensagem 1:\nPrimeira mensagem.\n\n"
        "Mensagem 2:\nImagem recebida.\n\n"
        "Mensagem 3:\nAudio transcrito."
    )
    assert merged.content == [
        {"type": "text", "text": "Mensagem 1 recebida."},
        {"type": "text", "text": "Primeira mensagem."},
        {"type": "text", "text": "Mensagem 2 recebida."},
        {"type": "image", "base64": "anVzdC1ieXRlcw=="},
        {"type": "text", "text": "Mensagem 3 recebida."},
        {"type": "text", "text": "Audio transcrito."},
    ]
    assert merged.media_size == 25
    assert merged.transcription_length == 18


def test_merge_normalized_inbound_messages_rejects_empty_batch() -> None:
    with pytest.raises(ValueError, match="At least one inbound message"):
        pipefacil_application._merge_normalized_inbound_messages([])


def test_run_chat_turn_calls_agent_with_thread_and_trace_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_agent(state, **kwargs):
        captured["state"] = state
        captured.update(kwargs)
        return {
            "intent": "question",
            "intent_reason": "Pergunta curta.",
            "response_text": "Resposta centralizada.",
            "status": "responded",
        }

    monkeypatch.setattr(chat_application, "run_agent", fake_run_agent)

    result = chat_application.run_chat_turn(
        message="oi",
        thread_id="thread-1",
        graph=object(),
        session_id="session-1",
        user_id="user-1",
        metadata={"source": "api"},
    )

    assert result == ChatTurnResult(
        thread_id="thread-1",
        intent="question",
        intent_reason="Pergunta curta.",
        response_text="Resposta centralizada.",
        status="responded",
        response_messages=["Resposta centralizada."],
        response_parts=[ResponsePartResult(type="text", text="Resposta centralizada.")],
    )
    assert captured["state"]["messages"][0].content == "oi"
    assert captured["session_id"] == "session-1"
    assert captured["user_id"] == "user-1"
    assert captured["metadata"] == {"source": "api"}
    assert captured["config"] == {"configurable": {"thread_id": "thread-1"}}
    assert captured["graph"] is not None


def test_run_chat_turn_accepts_multimodal_content_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    content = [
        {"type": "text", "text": "Tipo de mensagem: image\nArquivo recebido: imagem anexada."},
        {"type": "image", "base64": "anVzdC1ieXRlcw==", "mime_type": "image/jpeg"},
    ]

    def fake_run_agent(state, **kwargs):
        captured["state"] = state
        captured.update(kwargs)
        return {
            "intent": "request",
            "intent_reason": "Mensagem com imagem.",
            "response_text": "Imagem recebida.",
            "status": "responded",
        }

    monkeypatch.setattr(chat_application, "run_agent", fake_run_agent)

    result = chat_application.run_chat_turn(
        message=content,
        thread_id="thread-1",
    )

    assert result.response_text == "Imagem recebida."
    assert result.response_messages == ["Imagem recebida."]
    assert result.response_parts == [ResponsePartResult(type="text", text="Imagem recebida.")]
    assert captured["state"]["messages"][0].content == content


def test_run_chat_turn_builds_ordered_text_and_media_response_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_agent(state, **kwargs):
        return {
            "intent": "request",
            "intent_reason": "Usuario pediu material.",
            "response_text": "Claro, te mando aqui.",
            "response_media": [
                {
                    "media_id": "catalogo-pdf",
                    "type": "document",
                    "caption": "Catalogo em PDF",
                    "content_type": "application/pdf",
                    "filename": "catalogo.pdf",
                }
            ],
            "status": "responded",
        }

    monkeypatch.setattr(chat_application, "run_agent", fake_run_agent)

    result = chat_application.run_chat_turn(
        message="me manda o catalogo",
        thread_id="thread-1",
    )

    assert result.response_messages == ["Claro, te mando aqui."]
    assert result.response_parts == [
        ResponsePartResult(type="text", text="Claro, te mando aqui."),
        ResponsePartResult(
            type="document",
            media_id="catalogo-pdf",
            caption="Catalogo em PDF",
            content_type="application/pdf",
            filename="catalogo.pdf",
        ),
    ]


def test_run_chat_turn_maps_generated_audio_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_agent(state, **kwargs):
        return {
            "intent": "request",
            "intent_reason": "Usuario pediu audio.",
            "response_text": "Te mandei um audio explicando melhor.",
            "response_audio": {
                "text": "Esse e o roteiro falado do audio.",
                "reason": "Usuario pediu audio.",
            },
            "status": "responded",
        }

    monkeypatch.setattr(chat_application, "run_agent", fake_run_agent)

    result = chat_application.run_chat_turn(
        message="me manda em audio",
        thread_id="thread-1",
    )

    assert result.response_audio == ResponseAudioResult(
        text="Esse e o roteiro falado do audio.",
        reason="Usuario pediu audio.",
    )
    assert result.response_parts == [
        ResponsePartResult(type="text", text="Te mandei um audio explicando melhor.")
    ]


def test_handle_pipefacil_message_received_responds_even_when_deal_has_disabled_custom_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    run_calls: list[dict[str, object]] = []

    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: (
            run_calls.append(kwargs)
            or ChatTurnResult(
                thread_id=kwargs["thread_id"],
                intent="greeting",
                intent_reason="Saudacao curta.",
                response_text="Oi!",
                status="responded",
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: PipefacilSendMessageResult(
            status_code=201,
            request_id="req-custom-field-ignored",
            payload={"data": {"id": "msg-custom-field-ignored"}},
        ),
    )
    payload = _message_received_payload_with_deal_extra(
        {
            "customFields": [
                {
                    "field": {"slug": "legacy_ai_gate"},
                    "value": "desligado",
                }
            ]
        }
    )
    settings = Settings(
        _env_file=None,
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        payload,
        graph=object(),
        settings=settings,
    )

    assert result.status == "responded"
    assert result.response_text == "Oi!"
    assert result.delivery_status == "sent"
    assert len(run_calls) == 1
    assert [extra["pipeline_step"] for level, _, extra in fake_logger.records if level == "info"][
        :2
    ] == [
        "pipefacil.inbound.resolved",
        "agent.run.started",
    ]


def test_handle_pipefacil_message_received_resets_thread_before_agent_or_token_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    reset_threads: list[str] = []
    outbound_calls: list[dict[str, object]] = []

    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "reset_thread_state",
        lambda thread_id, *, graph: reset_threads.append(thread_id),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "fetch_thread_state",
        lambda *args, **kwargs: pytest.fail("fetch_thread_state should not run for /reset"),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "normalize_message_received_content",
        lambda *args, **kwargs: pytest.fail(
            "normalize_message_received_content should not run for /reset"
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: pytest.fail("run_chat_turn should not run for /reset"),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            outbound_calls.append(kwargs)
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-reset",
                payload={"data": {"id": "msg-reset"}},
            )
        ),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(body="  /RESET  "),
        graph=object(),
        settings=Settings(
            pipefacil_api_key="pf_live_1234567890abcdef.example",
            pipefacil_base_url="https://api.pipefacil.test",
            pipefacil_max_tokens_per_lead=1,
        ),
    )

    assert result.status == "thread_reset"
    assert result.response_text == pipefacil_application.THREAD_RESET_RESPONSE_TEXT
    assert result.delivery_status == "sent"
    assert reset_threads == ["deal-example-001"]
    assert outbound_calls[0]["text"] == pipefacil_application.THREAD_RESET_RESPONSE_TEXT
    info_steps = [
        extra["pipeline_step"] for level, _, extra in fake_logger.records if level == "info"
    ]
    assert info_steps == [
        "pipefacil.inbound.resolved",
        "pipefacil.inbound.thread_reset",
        "pipefacil.outbound.started",
        "pipefacil.outbound.delivered",
    ]


def test_handle_pipefacil_message_received_reports_reset_failure_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    outbound_calls: list[dict[str, object]] = []

    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "reset_thread_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            pipefacil_application.ThreadStateResetError("database unavailable")
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            outbound_calls.append(kwargs)
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-reset-failed",
                payload={"data": {"id": "msg-reset-failed"}},
            )
        ),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(body="/reset"),
        graph=object(),
        settings=Settings(
            pipefacil_api_key="pf_live_1234567890abcdef.example",
            pipefacil_base_url="https://api.pipefacil.test",
        ),
    )

    assert result.status == "thread_reset_failed"
    assert result.response_text == pipefacil_application.THREAD_RESET_FAILED_RESPONSE_TEXT
    assert result.delivery_status == "sent"
    assert outbound_calls[0]["text"] == pipefacil_application.THREAD_RESET_FAILED_RESPONSE_TEXT
    assert any(
        level == "exception" and message == "pipefacil.inbound.thread_reset_failed"
        for level, message, _ in fake_logger.records
    )


@pytest.mark.parametrize(
    ("body", "message_type", "media"),
    [
        ("/reset agora", "text", None),
        ("/reset", "image", None),
        (
            "/reset",
            "text",
            {"downloadUrl": "https://example.invalid/private-media.jpg"},
        ),
    ],
)
def test_thread_reset_command_requires_exact_text_without_media(
    body: str,
    message_type: str,
    media: dict[str, object] | None,
) -> None:
    payload_data = _message_received_payload(body=body).model_dump(mode="json")
    payload_data["data"]["message"]["type"] = message_type
    payload_data["data"]["message"]["media"] = media
    payload = MessageReceivedEventRequest.model_validate(payload_data)

    assert pipefacil_application._is_thread_reset_command(payload) is False


def test_handle_pipefacil_message_received_ignores_contact_without_lead_before_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    for function_name in (
        "fetch_thread_state",
        "normalize_message_received_content",
        "run_chat_turn",
        "send_public_text_message",
    ):
        monkeypatch.setattr(
            pipefacil_application,
            function_name,
            lambda *args, _function_name=function_name, **kwargs: pytest.fail(
                f"{_function_name} should not be called"
            ),
        )

    store = InMemoryMessageIdempotencyStore()
    monkeypatch.setattr(
        store,
        "claim",
        lambda *args, **kwargs: pytest.fail("idempotency claim should not be called"),
    )
    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload_without_deal(),
        graph=object(),
        settings=Settings(_env_file=None, pipefacil_max_tokens_per_lead=1),
        idempotency_store=store,
    )

    assert result == ChatTurnResult(
        thread_id="contact-example-001",
        intent=None,
        intent_reason="Pipefacil contact message has no lead/deal.",
        response_text="",
        status="contact_without_lead_ignored",
    )
    assert [record[1] for record in fake_logger.records] == [
        "pipefacil.inbound.resolved",
        "pipefacil.inbound.contact_without_lead_ignored",
    ]


def test_handle_pipefacil_message_received_ignores_duplicate_before_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_calls: list[str] = []
    outbound_calls: list[str] = []
    store = InMemoryMessageIdempotencyStore()
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: (
            run_calls.append(kwargs["thread_id"])
            or ChatTurnResult(
                thread_id=kwargs["thread_id"],
                intent="greeting",
                intent_reason="Saudacao curta.",
                response_text="Oi!",
                status="responded",
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            outbound_calls.append(kwargs["text"])
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-once",
                payload={"data": {"id": "msg-once"}},
            )
        ),
    )
    payload = _message_received_payload()
    settings = Settings(_env_file=None, pipefacil_api_key="pf-live")

    first = pipefacil_application.handle_pipefacil_message_received(
        payload,
        graph=object(),
        settings=settings,
        idempotency_store=store,
    )
    for function_name in (
        "fetch_thread_state",
        "normalize_message_received_content",
        "run_chat_turn",
        "send_public_text_message",
    ):
        monkeypatch.setattr(
            pipefacil_application,
            function_name,
            lambda *args, _function_name=function_name, **kwargs: pytest.fail(
                f"{_function_name} should not be called for a duplicate"
            ),
        )
    duplicate = pipefacil_application.handle_pipefacil_message_received(
        payload,
        graph=object(),
        settings=Settings(
            _env_file=None,
            pipefacil_api_key="pf-live",
            pipefacil_max_tokens_per_lead=1,
        ),
        idempotency_store=store,
    )

    assert first.delivery_status == "sent"
    assert duplicate == ChatTurnResult(
        thread_id="deal-example-001",
        intent=None,
        intent_reason="Duplicate Pipefacil message webhook ignored.",
        response_text="",
        status="duplicate_message_ignored",
    )
    assert len(run_calls) == 1
    assert outbound_calls == ["Oi!"]


def test_handle_pipefacil_message_received_processes_a_payload_batch_in_one_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    outbound_calls: list[str] = []
    store = InMemoryMessageIdempotencyStore()
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: (
            captured.update(kwargs)
            or ChatTurnResult(
                thread_id=kwargs["thread_id"],
                intent="question",
                intent_reason="Duas mensagens no mesmo turno.",
                response_text="Entendi as duas mensagens.",
                status="responded",
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            outbound_calls.append(kwargs["text"])
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-batch",
                payload={"data": {"id": "msg-batch"}},
            )
        ),
    )
    payload = _message_batch_received_payload("Primeira mensagem.", "Segunda mensagem.")
    settings = Settings(_env_file=None, pipefacil_api_key="pf-live")

    result = pipefacil_application.handle_pipefacil_message_received(
        payload,
        graph=object(),
        settings=settings,
        idempotency_store=store,
    )

    assert result.delivery_status == "sent"
    assert captured["message"] == (
        "Mensagem 1:\nPrimeira mensagem.\n\nMensagem 2:\nSegunda mensagem."
    )
    assert outbound_calls == ["Entendi as duas mensagens."]

    duplicate = pipefacil_application.handle_pipefacil_message_received(
        payload,
        graph=object(),
        settings=settings,
        idempotency_store=store,
    )

    assert duplicate.status == "duplicate_message_ignored"


def test_handle_pipefacil_message_received_processes_only_new_messages_from_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    store = InMemoryMessageIdempotencyStore()
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: (
            captured.update(kwargs)
            or ChatTurnResult(
                thread_id=kwargs["thread_id"],
                intent="question",
                intent_reason="Mensagem nova.",
                response_text="Resposta.",
                status="responded",
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: PipefacilSendMessageResult(
            status_code=201,
            request_id="req-new-message",
            payload={"data": {"id": "msg-new-message"}},
        ),
    )
    settings = Settings(_env_file=None, pipefacil_api_key="pf-live")
    first_payload = _message_batch_received_payload("Já processada.")
    pipefacil_application.handle_pipefacil_message_received(
        first_payload,
        graph=object(),
        settings=settings,
        idempotency_store=store,
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_batch_received_payload("Já processada.", "Mensagem nova."),
        graph=object(),
        settings=settings,
        idempotency_store=store,
    )

    assert result.delivery_status == "sent"
    assert captured["message"] == "Mensagem nova."


def test_pipefacil_message_idempotency_key_falls_back_to_message_id() -> None:
    payload_data = _message_received_payload().model_dump(mode="json")
    payload_data["data"]["message"]["externalId"] = None
    first_payload = MessageReceivedEventRequest.model_validate(payload_data)
    first_key = pipefacil_application._pipefacil_message_idempotency_key(first_payload)

    payload_data["data"]["message"]["id"] = "different-message-id"
    second_payload = MessageReceivedEventRequest.model_validate(payload_data)
    second_key = pipefacil_application._pipefacil_message_idempotency_key(second_payload)

    assert len(first_key) == 64
    assert first_key != second_key


def test_handle_pipefacil_message_received_keeps_claim_after_controlled_delivery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryMessageIdempotencyStore()
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="greeting",
            intent_reason="Saudacao curta.",
            response_text="Oi!",
            status="responded",
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (_ for _ in ()).throw(
            PipefacilSendMessageError(
                "upstream",
                error_code="pipefacil_upstream_error",
                status_code=503,
            )
        ),
    )
    payload = _message_received_payload()
    settings = Settings(_env_file=None, pipefacil_api_key="pf-live")

    first = pipefacil_application.handle_pipefacil_message_received(
        payload,
        graph=object(),
        settings=settings,
        idempotency_store=store,
    )
    duplicate = pipefacil_application.handle_pipefacil_message_received(
        payload,
        graph=object(),
        settings=settings,
        idempotency_store=store,
    )

    assert first.delivery_status == "failed"
    assert duplicate.status == "duplicate_message_ignored"


def test_handle_pipefacil_message_received_releases_claim_after_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryMessageIdempotencyStore()
    payload = _message_received_payload()
    settings = Settings(_env_file=None, pipefacil_api_key="pf-live")
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("unexpected")),
    )

    with pytest.raises(RuntimeError, match="unexpected"):
        pipefacil_application.handle_pipefacil_message_received(
            payload,
            graph=object(),
            settings=settings,
            idempotency_store=store,
        )

    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="greeting",
            intent_reason="Saudacao curta.",
            response_text="Oi!",
            status="responded",
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: PipefacilSendMessageResult(
            status_code=201,
            request_id="req-retry",
            payload={"data": {"id": "msg-retry"}},
        ),
    )

    retry = pipefacil_application.handle_pipefacil_message_received(
        payload,
        graph=object(),
        settings=settings,
        idempotency_store=store,
    )

    assert retry.delivery_status == "sent"


def test_handle_pipefacil_message_received_allows_distinct_channels_and_ttl_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_calls: list[str] = []
    store = InMemoryMessageIdempotencyStore()
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: (
            run_calls.append(kwargs["thread_id"])
            or ChatTurnResult(
                thread_id=kwargs["thread_id"],
                intent="greeting",
                intent_reason="Saudacao curta.",
                response_text="Oi!",
                status="responded",
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: PipefacilSendMessageResult(
            status_code=201,
            request_id="req-distinct",
            payload={"data": {"id": "msg-distinct"}},
        ),
    )
    first_payload = _message_received_payload()
    second_payload_data = first_payload.model_dump(mode="json")
    second_payload_data["data"]["channel"]["id"] = "channel-2"
    second_payload = MessageReceivedEventRequest.model_validate(second_payload_data)
    enabled_settings = Settings(
        _env_file=None,
        pipefacil_api_key="pf-live",
    )

    for payload in (first_payload, second_payload):
        result = pipefacil_application.handle_pipefacil_message_received(
            payload,
            graph=object(),
            settings=enabled_settings,
            idempotency_store=store,
        )
        assert result.delivery_status == "sent"

    disabled_settings = Settings(
        _env_file=None,
        pipefacil_api_key="pf-live",
        pipefacil_webhook_idempotency_ttl_seconds=0,
    )
    for _ in range(2):
        result = pipefacil_application.handle_pipefacil_message_received(
            first_payload,
            graph=object(),
            settings=disabled_settings,
            idempotency_store=store,
        )
        assert result.delivery_status == "sent"

    assert len(run_calls) == 4


def test_handle_pipefacil_message_received_skips_agent_when_lead_token_limit_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()

    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "fetch_thread_state",
        lambda *args, **kwargs: ThreadStateResult(thread_id=args[0]),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "normalize_message_received_content",
        lambda *args, **kwargs: pytest.fail("normalize_message_received_content should not run"),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: pytest.fail("run_chat_turn should not be called"),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: pytest.fail("send_public_text_message should not be called"),
    )
    payload = _message_received_payload(body="a")
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
        pipefacil_max_tokens_per_lead=1,
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        payload,
        graph=object(),
        settings=settings,
    )

    assert result == ChatTurnResult(
        thread_id="deal-example-001",
        intent=None,
        intent_reason="Pipefacil lead token budget exceeded.",
        response_text="",
        status="lead_token_limit_exceeded",
    )
    info_records = [extra for level, _, extra in fake_logger.records if level == "info"]
    assert [record["pipeline_step"] for record in info_records] == [
        "pipefacil.inbound.resolved",
        "pipefacil.inbound.lead_token_limit_exceeded",
    ]
    limit_log = info_records[1]
    assert limit_log["lead_current_tokens"] == 0
    assert limit_log["lead_incoming_tokens"] >= 1
    assert limit_log["lead_total_tokens"] >= limit_log["lead_max_tokens"]


def test_handle_pipefacil_message_received_counts_existing_thread_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipefacil_application,
        "fetch_thread_state",
        lambda *args, **kwargs: ThreadStateResult(
            thread_id=args[0],
            messages=[
                SerializedMessageResult(role="user", content="uma mensagem antiga"),
                SerializedMessageResult(role="assistant", content="uma resposta antiga"),
            ],
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "normalize_message_received_content",
        lambda *args, **kwargs: pytest.fail("normalize_message_received_content should not run"),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: pytest.fail("run_chat_turn should not be called"),
    )
    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(
            pipefacil_api_key="pf_live_1234567890abcdef.example",
            pipefacil_base_url="https://api.pipefacil.test",
            pipefacil_max_tokens_per_lead=2,
        ),
    )

    assert result.status == "lead_token_limit_exceeded"


def test_handle_pipefacil_message_received_maps_event_and_sends_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    outbound_calls: list[dict[str, object]] = []

    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="greeting",
            intent_reason="Saudacao curta.",
            response_text="Oi! Recebi sua mensagem.",
            status="responded",
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            outbound_calls.append(kwargs)
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-123",
                payload={"data": {"id": "msg-1"}},
            )
        ),
    )

    payload = _message_received_payload()
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        payload,
        graph=object(),
        settings=settings,
    )

    assert result == ChatTurnResult(
        thread_id="deal-example-001",
        intent="greeting",
        intent_reason="Saudacao curta.",
        response_text="Oi! Recebi sua mensagem.",
        status="responded",
        response_messages=["Oi! Recebi sua mensagem."],
        response_parts=[ResponsePartResult(type="text", text="Oi! Recebi sua mensagem.")],
        delivery_status="sent",
    )
    assert outbound_calls == [
        {
            "to": "+55 (11) 00000-0001",
            "text": "Oi! Recebi sua mensagem.",
            "sender_phone_number_id": "111111111111111",
            "profile_name": "CLIENTE EXEMPLO",
            "settings": settings,
        }
    ]
    info_steps = [
        extra["pipeline_step"] for level, _, extra in fake_logger.records if level == "info"
    ]
    debug_steps = [
        extra["pipeline_step"] for level, _, extra in fake_logger.records if level == "debug"
    ]

    assert info_steps == [
        "pipefacil.inbound.resolved",
        "agent.run.started",
        "agent.run.completed",
        "pipefacil.outbound.started",
        "pipefacil.outbound.delivered",
    ]
    assert debug_steps == [
        "pipefacil.inbound.details",
        "agent.run.details",
    ]
    assert all(
        extra["pipeline_run_id"]
        == "wamid.EXAMPLE_MESSAGE_000000000000000000000000000000000000000000000003"
        for _, _, extra in fake_logger.records
    )


def test_pipefacil_recognizable_langfuse_identity_does_not_leak_into_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    captured_chat_turn: dict[str, object] = {}
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: (
            captured_chat_turn.update(kwargs)
            or ChatTurnResult(
                thread_id=kwargs["thread_id"],
                intent="greeting",
                intent_reason="Saudacao curta.",
                response_text="Oi!",
                status="responded",
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: PipefacilSendMessageResult(
            status_code=201,
            request_id="req-pii-mode",
            payload={"data": {"id": "msg-pii-mode"}},
        ),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(
            _env_file=None,
            pipefacil_api_key="pf-live",
            langfuse_pipefacil_user_id_mode="contact_name_phone",
        ),
    )

    trace_user_id = captured_chat_turn["user_id"]
    assert result.delivery_status == "sent"
    assert trace_user_id == ("CLIENTE EXEMPLO | +5511000000001 | contact:contact-example-001")
    assert captured_chat_turn["metadata"]["trace_user_id"] == trace_user_id
    serialized_logs = repr(fake_logger.records)
    assert "CLIENTE EXEMPLO" not in serialized_logs
    assert "+5511000000001" not in serialized_logs
    assert "+55 (11) 00000-0001" not in serialized_logs
    assert "contact:contact-example-001" in serialized_logs


def test_handle_pipefacil_message_received_passes_visual_content_to_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    captured_chat_turn: dict[str, object] = {}
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "normalize_message_received_content",
        lambda *args, **kwargs: NormalizedInboundMessage(
            message_type="image",
            content=[
                {"type": "text", "text": "Tipo de mensagem: image\nArquivo recebido: imagem."},
                {"type": "image", "base64": "anVzdC1ieXRlcw==", "mime_type": "image/jpeg"},
            ],
            text="Tipo de mensagem: image\nArquivo recebido: imagem.",
            has_media=True,
            media_mime_type="image/jpeg",
            media_size=10,
            media_status_code=200,
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: (
            captured_chat_turn.update(kwargs)
            or ChatTurnResult(
                thread_id=kwargs["thread_id"],
                intent="request",
                intent_reason="Imagem recebida.",
                response_text="Vou analisar a imagem.",
                status="responded",
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: PipefacilSendMessageResult(
            status_code=201,
            request_id="req-123",
            payload={"data": {"id": "msg-1"}},
        ),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(_env_file=None, pipefacil_api_key="pf-live"),
    )

    media_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "info" and message == "pipefacil.inbound.media_downloaded"
    )
    assert result.delivery_status == "sent"
    assert captured_chat_turn["message"][1]["mime_type"] == "image/jpeg"
    assert captured_chat_turn["metadata"]["normalized_message"] == {
        "message_type": "image",
        "has_media": True,
        "media_mime_type": "image/jpeg",
        "media_size": 10,
        "transcription_length": None,
    }
    assert media_log["media_mime_type"] == "image/jpeg"
    assert media_log["media_size"] == 10


def test_handle_pipefacil_message_received_sends_fallback_when_media_processing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    outbound_calls: list[dict[str, object]] = []
    run_chat_turn_called = False
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "normalize_message_received_content",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PipefacilMediaProcessingError(
                "download failed",
                error_code="pipefacil_media_download_transport_error",
                status_code=None,
                media_mime_type="image/jpeg",
            )
        ),
    )

    def fake_run_chat_turn(**kwargs):
        nonlocal run_chat_turn_called
        run_chat_turn_called = True
        return ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="fallback",
            intent_reason="Should not run.",
            response_text="Should not run.",
            status="responded",
        )

    monkeypatch.setattr(pipefacil_application, "run_chat_turn", fake_run_chat_turn)
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            outbound_calls.append(kwargs)
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-123",
                payload={"data": {"id": "msg-1"}},
            )
        ),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(_env_file=None, pipefacil_api_key="pf-live"),
    )

    media_failed_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "warning" and message == "pipefacil.inbound.media_failed"
    )
    assert run_chat_turn_called is False
    assert result.status == "media_failed"
    assert result.delivery_status == "sent"
    assert result.response_messages == [pipefacil_application.MEDIA_FALLBACK_RESPONSE_TEXT]
    assert outbound_calls[0]["text"] == pipefacil_application.MEDIA_FALLBACK_RESPONSE_TEXT
    assert media_failed_log["error_code"] == "pipefacil_media_download_transport_error"
    assert media_failed_log["media_mime_type"] == "image/jpeg"


def test_handle_pipefacil_message_received_returns_failed_delivery_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="greeting",
            intent_reason="Saudacao curta.",
            response_text="Oi! Recebi sua mensagem.",
            status="responded",
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (_ for _ in ()).throw(
            PipefacilSendMessageError(
                "boom",
                error_code="pipefacil_upstream_error",
                status_code=502,
                request_id="req-outbound-1",
            )
        ),
    )

    payload = _message_received_payload()
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        payload,
        graph=object(),
        settings=settings,
    )

    assert result == ChatTurnResult(
        thread_id="deal-example-001",
        intent="greeting",
        intent_reason="Saudacao curta.",
        response_text="Oi! Recebi sua mensagem.",
        status="responded",
        response_messages=["Oi! Recebi sua mensagem."],
        response_parts=[ResponsePartResult(type="text", text="Oi! Recebi sua mensagem.")],
        delivery_status="failed",
        delivery_error="pipefacil_upstream_error",
    )
    failed_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "exception" and message == "pipefacil.outbound.failed"
    )

    assert failed_log["pipeline_step"] == "pipefacil.outbound.failed"
    assert failed_log["delivery_status"] == "failed"
    assert failed_log["error_code"] == "pipefacil_upstream_error"
    assert failed_log["status_code"] == 502
    assert failed_log["request_id"] == "req-outbound-1"
    assert failed_log["message_part_index"] == 1
    assert failed_log["message_part_count"] == 1


def test_handle_pipefacil_message_received_sends_response_messages_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    outbound_calls: list[dict[str, object]] = []
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="question",
            intent_reason="Pergunta com resposta longa.",
            response_text="Parte um.\n\nParte dois.",
            response_messages=["Parte um.", "Parte dois."],
            status="responded",
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            outbound_calls.append(kwargs)
            or PipefacilSendMessageResult(
                status_code=201,
                request_id=f"req-{len(outbound_calls)}",
                payload={"data": {"id": f"msg-{len(outbound_calls)}"}},
            )
        ),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(_env_file=None, pipefacil_api_key="pf-live"),
    )

    assert result.delivery_status == "sent"
    assert result.response_messages == ["Parte um.", "Parte dois."]
    assert [call["text"] for call in outbound_calls] == ["Parte um.", "Parte dois."]
    delivered_logs = [
        extra
        for level, message, extra in fake_logger.records
        if level == "info" and message == "pipefacil.outbound.delivered"
    ]
    assert [log["message_part_index"] for log in delivered_logs] == [1, 2]
    assert all(log["message_part_count"] == 2 for log in delivered_logs)


def test_handle_pipefacil_message_received_stops_after_first_multipart_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    outbound_calls: list[str] = []
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="question",
            intent_reason="Pergunta com resposta longa.",
            response_text="Parte um.\n\nParte dois.\n\nParte tres.",
            response_messages=["Parte um.", "Parte dois.", "Parte tres."],
            status="responded",
        ),
    )

    def fake_send_public_text_message(**kwargs):
        outbound_calls.append(kwargs["text"])
        if kwargs["text"] == "Parte dois.":
            raise PipefacilSendMessageError(
                "boom",
                error_code="pipefacil_upstream_error",
                status_code=502,
                request_id="req-part-2",
            )

        return PipefacilSendMessageResult(
            status_code=201,
            request_id="req-part-1",
            payload={"data": {"id": "msg-1"}},
        )

    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        fake_send_public_text_message,
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(_env_file=None, pipefacil_api_key="pf-live"),
    )

    assert result.delivery_status == "failed"
    assert result.delivery_error == "pipefacil_upstream_error"
    assert outbound_calls == ["Parte um.", "Parte dois."]
    failed_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "exception" and message == "pipefacil.outbound.failed"
    )
    assert failed_log["message_part_index"] == 2
    assert failed_log["message_part_count"] == 3


def test_handle_pipefacil_message_received_sends_media_parts_after_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    outbound_calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="request",
            intent_reason="Usuario pediu audio.",
            response_text="Te mando um exemplo.",
            response_messages=["Te mando um exemplo."],
            response_parts=[
                ResponsePartResult(type="text", text="Te mando um exemplo."),
                ResponsePartResult(
                    type="audio",
                    media_id="audio-exemplo",
                    caption="Audio exemplo",
                    content_type="audio/ogg",
                    filename="exemplo.ogg",
                ),
            ],
            status="responded",
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "get_outbound_media_asset",
        lambda media_id: type(
            "MediaAsset",
            (),
            {
                "media_url": "https://cdn.example.com/audio/exemplo.ogg",
                "filename": "exemplo.ogg",
                "content_type": "audio/ogg",
            },
        )(),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            outbound_calls.append(("text", kwargs))
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-text",
                payload={"data": {"id": "msg-text"}},
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_whatsapp_media_message",
        lambda **kwargs: (
            outbound_calls.append(("media", kwargs))
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-media",
                payload={"data": {"id": "msg-media"}},
            )
        ),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(_env_file=None, pipefacil_api_key="pf-live"),
    )

    assert result.delivery_status == "sent"
    assert [call_type for call_type, _ in outbound_calls] == ["text", "media"]
    assert outbound_calls[1][1]["media_type"] == "audio"
    assert outbound_calls[1][1]["media_url"] == "https://cdn.example.com/audio/exemplo.ogg"
    assert outbound_calls[1][1]["caption"] == "Audio exemplo"
    assert outbound_calls[1][1]["filename"] == "exemplo.ogg"
    assert outbound_calls[1][1]["mime_type"] == "audio/ogg"
    assert outbound_calls[1][1]["channel_id"] == "channel-example-001"
    delivered_logs = [
        extra
        for level, message, extra in fake_logger.records
        if level == "info" and message == "pipefacil.outbound.delivered"
    ]
    assert [log["message_part_type"] for log in delivered_logs] == ["text", "audio"]
    assert delivered_logs[1]["media_id"] == "audio-exemplo"
    assert delivered_logs[1]["media_content_type"] == "audio/ogg"
    assert "https://cdn.example.com/audio/exemplo.ogg" not in str(fake_logger.records)


def test_handle_pipefacil_message_received_sends_generated_audio_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    outbound_calls: list[tuple[str, dict[str, object]]] = []
    generated_texts: list[str] = []
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="request",
            intent_reason="Usuario pediu audio.",
            response_text="Te mandei um audio explicando melhor.",
            response_messages=["Te mandei um audio explicando melhor."],
            response_parts=[
                ResponsePartResult(
                    type="text",
                    text="Te mandei um audio explicando melhor.",
                )
            ],
            response_audio=ResponseAudioResult(
                text="Esse e o texto que vai virar audio para o lead.",
                reason="Usuario pediu audio.",
            ),
            status="responded",
        ),
    )

    def fake_prepare_generated_audio(**kwargs):
        generated_texts.append(kwargs["text"])
        return GeneratedAudioAsset(
            media_id="generated-audio:audio_test",
            media_url="https://agent.example.com/generated-audio/audio_test.ogg",
            content_type="audio/ogg",
            filename="audio_test.ogg",
            text=kwargs["text"],
        )

    monkeypatch.setattr(
        pipefacil_application,
        "prepare_generated_audio",
        fake_prepare_generated_audio,
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            outbound_calls.append(("text", kwargs))
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-text",
                payload={"data": {"id": "msg-text"}},
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_whatsapp_media_message",
        lambda **kwargs: (
            outbound_calls.append(("media", kwargs))
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-media",
                payload={"data": {"id": "msg-media"}},
            )
        ),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(
            _env_file=None,
            pipefacil_api_key="pf-live",
            generated_audio_enabled=True,
            generated_audio_public_base_url="https://agent.example.com",
            elevenlabs_api_key="el-live",
            elevenlabs_voice_id="voice-br",
        ),
    )

    assert result.delivery_status == "sent"
    assert generated_texts == ["Esse e o texto que vai virar audio para o lead."]
    assert [call_type for call_type, _ in outbound_calls] == ["text", "media"]
    assert outbound_calls[1][1]["media_type"] == "audio"
    assert (
        outbound_calls[1][1]["media_url"]
        == "https://agent.example.com/generated-audio/audio_test.ogg"
    )
    assert outbound_calls[1][1]["filename"] == "audio_test.ogg"
    assert outbound_calls[1][1]["mime_type"] == "audio/ogg"
    assert result.response_parts[-1] == ResponsePartResult(
        type="audio",
        media_id="generated-audio:audio_test",
        content_type="audio/ogg",
        filename="audio_test.ogg",
    )
    assert "https://agent.example.com/generated-audio/audio_test.ogg" not in str(
        fake_logger.records
    )


def test_handle_pipefacil_message_received_auto_generates_audio_for_long_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    outbound_calls: list[tuple[str, dict[str, object]]] = []
    generated_texts: list[str] = []
    long_text = (
        "Aqui vai uma explicacao mais completa para o lead, com varios detalhes sobre "
        "o processo e proximos passos."
    )
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="question",
            intent_reason="Pergunta ampla.",
            response_text=long_text,
            response_messages=[long_text],
            response_parts=[ResponsePartResult(type="text", text=long_text)],
            status="responded",
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "prepare_generated_audio",
        lambda **kwargs: (
            generated_texts.append(kwargs["text"])
            or GeneratedAudioAsset(
                media_id="generated-audio:audio_auto",
                media_url="https://agent.example.com/generated-audio/audio_auto.ogg",
                content_type="audio/ogg",
                filename="audio_auto.ogg",
                text=kwargs["text"],
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            outbound_calls.append(("text", kwargs))
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-text",
                payload={"data": {"id": "msg-text"}},
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_whatsapp_media_message",
        lambda **kwargs: (
            outbound_calls.append(("media", kwargs))
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-media",
                payload={"data": {"id": "msg-media"}},
            )
        ),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(
            _env_file=None,
            pipefacil_api_key="pf-live",
            generated_audio_enabled=True,
            generated_audio_auto_enabled=True,
            generated_audio_auto_min_chars=20,
            generated_audio_auto_text="Mandei um audio com os detalhes.",
            generated_audio_public_base_url="https://agent.example.com",
            elevenlabs_api_key="el-live",
            elevenlabs_voice_id="voice-br",
        ),
    )

    assert result.delivery_status == "sent"
    assert generated_texts == [long_text]
    assert outbound_calls[0][1]["text"] == "Mandei um audio com os detalhes."
    assert outbound_calls[1][1]["media_type"] == "audio"
    assert result.response_messages == ["Mandei um audio com os detalhes."]
    assert result.response_parts == [
        ResponsePartResult(type="text", text="Mandei um audio com os detalhes."),
        ResponsePartResult(
            type="audio",
            media_id="generated-audio:audio_auto",
            content_type="audio/ogg",
            filename="audio_auto.ogg",
        ),
    ]


def test_handle_pipefacil_message_received_falls_back_to_text_for_explicit_audio_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    sent_texts: list[str] = []
    audio_text = "Conteudo sensivel que deveria ser falado no audio."
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="request",
            intent_reason="Usuario pediu audio.",
            response_text="Te mandei um audio.",
            response_messages=["Te mandei um audio."],
            response_parts=[ResponsePartResult(type="text", text="Te mandei um audio.")],
            response_audio=ResponseAudioResult(text=audio_text, reason="Pedido explicito."),
            status="responded",
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "prepare_generated_audio",
        lambda **kwargs: (_ for _ in ()).throw(
            GeneratedAudioError(
                "upstream body must not be logged",
                error_code="elevenlabs_upstream_error",
                status_code=503,
                attempt_count=2,
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            sent_texts.append(kwargs["text"])
            or PipefacilSendMessageResult(201, "req-text", {"data": {"id": "message"}})
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_whatsapp_media_message",
        lambda **kwargs: pytest.fail("audio should not be sent after generation failure"),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(
            _env_file=None,
            pipefacil_api_key="pf-live",
            generated_audio_enabled=True,
        ),
    )

    assert result.delivery_status == "sent"
    assert sent_texts == [
        f"{pipefacil_application.GENERATED_AUDIO_DELIVERY_FALLBACK_PREFIX}\n{audio_text}"
    ]
    failed_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "warning" and message == "generated_audio.failed"
    )
    assert failed_log["error_code"] == "elevenlabs_upstream_error"
    assert failed_log["upstream_status_code"] == 503
    assert failed_log["generated_audio_attempt_count"] == 2
    assert audio_text not in str(fake_logger.records)
    assert "upstream body must not be logged" not in str(fake_logger.records)


def test_generated_audio_does_not_replace_copyable_text_by_length() -> None:
    copyable_text = (
        "Plano Essencial: R$ 129,90 por mes. Vencimento: 10/08. Codigo para copiar: PF-48291. "
    ) * 10
    response = ChatTurnResult(
        thread_id="thread-copyable-data",
        intent="question",
        intent_reason="Usuario pediu valores e codigo.",
        response_text=copyable_text,
        response_messages=[copyable_text],
        response_parts=[ResponsePartResult(type="text", text=copyable_text)],
        status="responded",
    )

    audio_text = pipefacil_application._resolve_generated_audio_text(
        response,
        response.response_parts,
        settings=Settings(
            _env_file=None,
            generated_audio_enabled=True,
            generated_audio_auto_enabled=False,
            generated_audio_auto_min_chars=20,
        ),
    )

    assert len(copyable_text) > 650
    assert audio_text is None


def test_handle_pipefacil_message_received_keeps_original_text_for_auto_audio_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_texts: list[str] = []
    long_text = (
        "Resposta longa que deve continuar sendo enviada integralmente quando o audio falha."
    )
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="question",
            intent_reason="Pergunta ampla.",
            response_text=long_text,
            response_messages=[long_text],
            response_parts=[ResponsePartResult(type="text", text=long_text)],
            status="responded",
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "prepare_generated_audio",
        lambda **kwargs: (_ for _ in ()).throw(
            GeneratedAudioError(
                "storage failed",
                error_code="generated_audio_storage_error",
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            sent_texts.append(kwargs["text"])
            or PipefacilSendMessageResult(201, "req-text", {"data": {"id": "message"}})
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_whatsapp_media_message",
        lambda **kwargs: pytest.fail("audio should not be sent after generation failure"),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(
            _env_file=None,
            pipefacil_api_key="pf-live",
            generated_audio_enabled=True,
            generated_audio_auto_enabled=True,
            generated_audio_auto_min_chars=20,
        ),
    )

    assert result.delivery_status == "sent"
    assert sent_texts == [long_text]
    assert result.response_messages == [long_text]
    assert result.response_parts == [ResponsePartResult(type="text", text=long_text)]


def test_handle_pipefacil_message_received_stops_when_media_part_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_logger = FakeLogger()
    text_calls: list[str] = []
    media_calls: list[str] = []
    monkeypatch.setattr(pipefacil_application, "LOGGER", fake_logger)
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="request",
            intent_reason="Usuario pediu materiais.",
            response_text="Envio em seguida.",
            response_messages=["Envio em seguida."],
            response_parts=[
                ResponsePartResult(type="text", text="Envio em seguida."),
                ResponsePartResult(
                    type="image",
                    media_id="foto-1",
                    caption="Foto 1",
                    content_type="image/jpeg",
                    filename="foto-1.jpg",
                ),
                ResponsePartResult(
                    type="document",
                    media_id="doc-2",
                    caption="Documento 2",
                    content_type="application/pdf",
                    filename="doc-2.pdf",
                ),
            ],
            status="responded",
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "get_outbound_media_asset",
        lambda media_id: type(
            "MediaAsset",
            (),
            {
                "media_url": f"https://cdn.example.com/{media_id}",
                "filename": f"{media_id}.bin",
                "content_type": "application/octet-stream",
            },
        )(),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (
            text_calls.append(kwargs["text"])
            or PipefacilSendMessageResult(
                status_code=201,
                request_id="req-text",
                payload={"data": {"id": "msg-text"}},
            )
        ),
    )

    def fake_send_whatsapp_media_message(**kwargs):
        media_calls.append(kwargs["media_type"])
        raise PipefacilSendMessageError(
            "boom",
            error_code="pipefacil_upstream_error",
            status_code=502,
            request_id="req-media-1",
        )

    monkeypatch.setattr(
        pipefacil_application,
        "send_whatsapp_media_message",
        fake_send_whatsapp_media_message,
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(_env_file=None, pipefacil_api_key="pf-live"),
    )

    assert result.delivery_status == "failed"
    assert result.delivery_error == "pipefacil_upstream_error"
    assert text_calls == ["Envio em seguida."]
    assert media_calls == ["image"]
    failed_log = next(
        extra
        for level, message, extra in fake_logger.records
        if level == "exception" and message == "pipefacil.outbound.failed"
    )
    assert failed_log["message_part_index"] == 2
    assert failed_log["message_part_count"] == 3
    assert failed_log["message_part_type"] == "image"
    assert failed_log["media_id"] == "foto-1"


def test_handle_pipefacil_message_received_does_not_send_media_after_text_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_calls: list[str] = []
    monkeypatch.setattr(
        pipefacil_application,
        "run_chat_turn",
        lambda **kwargs: ChatTurnResult(
            thread_id=kwargs["thread_id"],
            intent="request",
            intent_reason="Usuario pediu imagem.",
            response_text="Vou enviar.",
            response_parts=[
                ResponsePartResult(type="text", text="Vou enviar."),
                ResponsePartResult(
                    type="image",
                    media_id="foto-1",
                    caption="Foto",
                    content_type="image/jpeg",
                    filename="foto.jpg",
                ),
            ],
            status="responded",
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_public_text_message",
        lambda **kwargs: (_ for _ in ()).throw(
            PipefacilSendMessageError(
                "boom",
                error_code="pipefacil_upstream_error",
                status_code=502,
                request_id="req-text",
            )
        ),
    )
    monkeypatch.setattr(
        pipefacil_application,
        "send_whatsapp_media_message",
        lambda **kwargs: media_calls.append(kwargs["media_type"]),
    )

    result = pipefacil_application.handle_pipefacil_message_received(
        _message_received_payload(),
        graph=object(),
        settings=Settings(_env_file=None, pipefacil_api_key="pf-live"),
    )

    assert result.delivery_status == "failed"
    assert media_calls == []


def test_fetch_thread_state_returns_serialized_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSnapshot:
        config = {"configurable": {"thread_id": "thread-1"}}
        values = {
            "latest_user_message": "oi",
            "intent": "greeting",
            "intent_reason": "Saudacao curta.",
            "response_text": "Oi! Como posso ajudar?",
            "status": "responded",
            "messages": [AIMessage(content="Oi! Como posso ajudar?")],
        }

    monkeypatch.setattr(
        chat_application,
        "get_thread_state",
        lambda *args, **kwargs: FakeSnapshot(),
    )
    monkeypatch.setattr(
        chat_application,
        "serialize_thread_state",
        lambda snapshot: {
            "thread_id": "thread-1",
            "latest_user_message": "oi",
            "intent": "greeting",
            "intent_reason": "Saudacao curta.",
            "response_text": "Oi! Como posso ajudar?",
            "status": "responded",
            "messages": [{"role": "assistant", "content": "Oi! Como posso ajudar?"}],
        },
    )

    result = chat_application.fetch_thread_state("thread-1", graph=object())

    assert result == ThreadStateResult(
        thread_id="thread-1",
        latest_user_message="oi",
        intent="greeting",
        intent_reason="Saudacao curta.",
        response_text="Oi! Como posso ajudar?",
        status="responded",
        messages=[
            chat_application.SerializedMessageResult(
                role="assistant",
                content="Oi! Como posso ajudar?",
            )
        ],
    )
