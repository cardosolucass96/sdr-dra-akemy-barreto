from __future__ import annotations

import base64
import json

import httpx
import pytest

import app.integrations.pipefacil.mapping as pipefacil_mapping
from app.core.config import Settings
from app.integrations.pipefacil import (
    MessageReceivedEventRequest,
    PipefacilConversationHistoryLookupError,
    PipefacilDealLookupError,
    PipefacilDealUpdateError,
    PipefacilDownloadedMedia,
    PipefacilMediaDownloadError,
    PipefacilSendMessageError,
    build_message_received_metadata,
    build_message_received_raw_log_payload,
    download_pipefacil_media,
    fetch_deal_by_seq,
    fetch_pipefacil_conversation_history,
    normalize_message_received_content,
    send_public_text_message,
    send_whatsapp_media_message,
    update_deal_properties,
    update_deal_stage,
)

SIGNED_DOWNLOAD_URL = "https://storage.example.com/pipefacil/media/file?X-Amz-Signature=secret"


def _message_received_payload(
    *,
    message_type: str = "text",
    body: str | None = "ooi",
    mime_type: str | None = None,
    filename: str | None = None,
) -> MessageReceivedEventRequest:
    media = None
    if message_type != "text":
        media = {
            "mimeType": mime_type,
            "filename": filename,
            "downloadUrl": SIGNED_DOWNLOAD_URL,
        }

    return MessageReceivedEventRequest.model_validate(
        {
            "type": "message.received",
            "timestamp": "2026-07-22T13:26:43.124642219Z",
            "data": {
                "message": {
                    "id": "c31c49ef-2eab-4345-bf07-3dd06d8f451e",
                    "externalId": (
                        "wamid.EXAMPLE_MESSAGE_000000000000000000000000000000000000000000000001"
                    ),
                    "body": body,
                    "type": message_type,
                    "timestamp": "2026-07-22T13:26:39Z",
                    "media": media,
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
                    "stage": {
                        "id": "stage-example-001",
                        "name": "Qualificacao IA",
                    },
                },
            },
        }
    )


def _message_received_payload_with_deal_extra(
    extra: dict[str, object],
) -> MessageReceivedEventRequest:
    payload = _message_received_payload().model_dump(mode="json")
    payload["data"]["deal"].update(extra)
    return MessageReceivedEventRequest.model_validate(payload)


def test_message_received_payload_accepts_flattened_message_data() -> None:
    payload_data = _message_received_payload().model_dump(mode="json")
    message = payload_data["data"].pop("message")
    payload_data["data"].update(message)

    payload = MessageReceivedEventRequest.model_validate(payload_data)

    assert payload.data.message.id == message["id"]
    assert payload.data.message.externalId == message["externalId"]
    assert payload.data.message.body == "ooi"
    assert payload.data.message.type == "text"
    assert payload.data.channel.id == "channel-example-001"
    assert payload.data.contact.id == "contact-example-001"
    assert payload.data.deal is not None
    assert payload.data.deal.id == "deal-example-001"


def test_message_received_payload_accepts_flattened_message_aliases() -> None:
    payload_data = _message_received_payload().model_dump(mode="json")
    message = payload_data["data"].pop("message")
    payload_data["data"].update(
        {
            "messageId": message["id"],
            "external_id": message["externalId"],
            "text": message["body"],
            "message_type": message["type"],
            "created_at": message["timestamp"],
        }
    )

    payload = MessageReceivedEventRequest.model_validate(payload_data)

    assert payload.data.message.id == message["id"]
    assert payload.data.message.externalId == message["externalId"]
    assert payload.data.message.body == message["body"]
    assert payload.data.message.type == message["type"]


def test_message_received_payload_accepts_nested_message_container() -> None:
    payload_data = _message_received_payload().model_dump(mode="json")
    message = payload_data["data"].pop("message")
    payload_data["data"]["payload"] = {"message": message}

    payload = MessageReceivedEventRequest.model_validate(payload_data)

    assert payload.data.message.id == message["id"]
    assert payload.data.message.externalId == message["externalId"]
    assert payload.data.message.body == message["body"]


def test_message_received_payload_accepts_messages_array_and_preserves_deal_fields() -> None:
    payload = MessageReceivedEventRequest.model_validate(
        {
            "type": "message.received",
            "timestamp": "2026-07-24T18:30:25.513065368Z",
            "data": {
                "messages": [
                    {
                        "id": "8462fb4f-b884-484a-89c6-b93ce02f54ad",
                        "externalId": (
                            "wamid.EXAMPLE_MESSAGE_000000000000000000000000000000000000000000000002"
                        ),
                        "body": "oi",
                        "type": "text",
                        "timestamp": "2026-07-24T18:30:22Z",
                        "media": None,
                    }
                ],
                "channel": {
                    "id": "35e0e1fa-1be7-4280-9621-edff13e69de2",
                    "phoneNumberId": "111111111111111",
                    "phoneNumber": "+55 11 00000-0000",
                    "displayName": "111111111111111",
                },
                "contact": {
                    "id": "contact-example-001",
                    "name": "CLIENTE EXEMPLO",
                    "phone": "+5511000000001",
                    "email": None,
                },
                "deal": {
                    "id": "bba21729-84e0-4280-9852-5a423193aae4",
                    "seq": 777,
                    "name": "Cliente Exemplo | Empresa Exemplo",
                    "stage": {
                        "id": "stage-example-002",
                        "name": "Leads",
                    },
                    "customFields": {"lead_source": "website"},
                },
            },
        }
    )

    assert payload.data.message.id == "8462fb4f-b884-484a-89c6-b93ce02f54ad"
    assert payload.data.message.body == "oi"
    assert payload.data.message.type == "text"
    assert payload.data.deal is not None
    assert payload.data.deal.seq == 777
    assert payload.data.deal.model_extra == {"customFields": {"lead_source": "website"}}


def test_message_received_payload_preserves_every_message_in_messages_array() -> None:
    payload_data = _message_received_payload().model_dump(mode="json")
    first_message = payload_data["data"].pop("message")
    second_message = {
        **first_message,
        "id": "second-message-id",
        "externalId": "second-external-message-id",
        "body": "segunda mensagem",
        "timestamp": "2026-07-22T13:26:40Z",
    }
    payload_data["data"]["messages"] = [first_message, second_message]

    payload = MessageReceivedEventRequest.model_validate(payload_data)

    assert [message.id for message in payload.data.messages] == [
        first_message["id"],
        "second-message-id",
    ]
    assert [message.data.message.body for message in payload.messages_for_processing()] == [
        "ooi",
        "segunda mensagem",
    ]


def test_message_received_payload_preserves_a_large_crm_buffer() -> None:
    payload_data = _message_received_payload().model_dump(mode="json")
    first_message = payload_data["data"].pop("message")
    payload_data["data"]["messages"] = [
        {
            **first_message,
            "id": f"buffered-message-{index}",
            "externalId": f"buffered-external-message-{index}",
            "body": f"mensagem {index}",
        }
        for index in range(501)
    ]

    payload = MessageReceivedEventRequest.model_validate(payload_data)

    assert len(payload.messages_for_processing()) == 501


def test_message_received_payload_accepts_messages_in_the_legacy_message_field() -> None:
    payload_data = _message_received_payload().model_dump(mode="json")
    first_message = payload_data["data"]["message"]
    payload_data["data"]["message"] = [
        first_message,
        {
            **first_message,
            "id": "second-message-id",
            "externalId": "second-external-message-id",
            "body": "segunda mensagem",
        },
    ]

    payload = MessageReceivedEventRequest.model_validate(payload_data)

    assert [message.data.message.body for message in payload.messages_for_processing()] == [
        "ooi",
        "segunda mensagem",
    ]


def test_message_received_payload_accepts_string_message_with_flattened_identity() -> None:
    payload_data = _message_received_payload().model_dump(mode="json")
    message = payload_data["data"].pop("message")
    payload_data["data"].update(
        {
            "id": message["id"],
            "externalId": message["externalId"],
            "message": message["body"],
        }
    )

    payload = MessageReceivedEventRequest.model_validate(payload_data)

    assert payload.data.message.id == message["id"]
    assert payload.data.message.externalId == message["externalId"]
    assert payload.data.message.body == message["body"]
    assert payload.data.message.type == "text"


def test_send_public_text_message_posts_expected_public_api_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["user_agent"] = request.headers["user-agent"]
        captured["payload"] = json.loads(request.content.decode())
        return httpx.Response(
            201,
            json={"data": {"id": "msg-1"}},
            headers={"x-request-id": "req-123"},
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.pipefacil.test",
    )
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
        pipefacil_timeout_seconds=5,
        app_version="0.1.0",
    )

    result = send_public_text_message(
        to="+5511000000001",
        text="Oi! Recebi sua mensagem.",
        sender_phone_number_id="111111111111111",
        profile_name="CLIENTE EXEMPLO",
        settings=settings,
        client=client,
    )

    assert result is not None
    assert result.status_code == 201
    assert result.request_id == "req-123"
    assert captured == {
        "method": "POST",
        "url": "https://api.pipefacil.test/api/v1/messages",
        "authorization": "Bearer pf_live_1234567890abcdef.example",
        "user_agent": "SDR-Pipefacil/0.1.0",
        "payload": {
            "to": "+5511000000001",
            "type": "text",
            "text": "Oi! Recebi sua mensagem.",
            "senderPhoneNumberId": "111111111111111",
        },
    }


def test_fetch_deal_by_seq_gets_public_api_deal_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "deal-1",
                    "seq": 100,
                    "properties": {"priority": "low"},
                }
            },
            headers={"x-request-id": "req-deal-123"},
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.pipefacil.test",
    )
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
        pipefacil_timeout_seconds=5,
        app_version="0.1.0",
    )

    result = fetch_deal_by_seq(seq=100, settings=settings, client=client)

    assert result == {
        "id": "deal-1",
        "seq": 100,
        "properties": {"priority": "low"},
    }
    assert captured == {
        "method": "GET",
        "url": "https://api.pipefacil.test/api/v1/deals/100",
        "authorization": "Bearer pf_live_1234567890abcdef.example",
    }


def test_fetch_pipefacil_conversation_history_normalizes_pipefacil_messages() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "contact": {"phone": "+5511000000001", "name": "CLIENTE EXEMPLO"},
                "channel": {"id": "channel-1", "phoneNumberId": "111111111111111"},
                "data": [
                    {
                        "id": "message-2",
                        "direction": "outbound",
                        "body": "Perfeito, fico no aguardo.",
                        "timestamp": "2026-07-22T13:27:00Z",
                    },
                    {
                        "id": "message-1",
                        "direction": "inbound",
                        "body": "Vou passar o cartao.",
                        "timestamp": "2026-07-22T13:26:00Z",
                    },
                ],
            },
            headers={"x-request-id": "req-history-123"},
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.pipefacil.test",
    )
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
        pipefacil_timeout_seconds=5,
        app_version="0.1.0",
    )

    result = fetch_pipefacil_conversation_history(
        deal_seq=100,
        contact_id="contact-1",
        limit=50,
        settings=settings,
        client=client,
    )

    assert [(message.role, message.content) for message in result.messages] == [
        ("user", "Vou passar o cartao."),
        ("assistant", "Perfeito, fico no aguardo."),
    ]
    assert result.contact_phone == "+5511000000001"
    assert result.channel_id == "channel-1"
    assert result.sender_phone_number_id == "111111111111111"
    assert result.profile_name == "CLIENTE EXEMPLO"
    assert captured == {
        "method": "GET",
        "url": (
            "https://api.pipefacil.test/api/v1/messages?dealSeq=100&contactId=contact-1&limit=50"
        ),
        "authorization": "Bearer pf_live_1234567890abcdef.example",
    }


def test_fetch_pipefacil_conversation_history_requires_identifier() -> None:
    with pytest.raises(PipefacilConversationHistoryLookupError) as exc_info:
        fetch_pipefacil_conversation_history(
            settings=Settings(pipefacil_api_key="pf_live_1234567890abcdef.example")
        )

    assert exc_info.value.error_code == "pipefacil_history_identifier_missing"


def test_fetch_deal_by_seq_raises_without_api_key() -> None:
    with pytest.raises(PipefacilDealLookupError) as exc_info:
        fetch_deal_by_seq(seq=100, settings=Settings(pipefacil_api_key=None))

    assert exc_info.value.error_code == "pipefacil_api_key_missing"


def test_update_deal_properties_patches_public_api_deal_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"data": {"id": "deal-1", "seq": 100}},
            headers={"x-request-id": "req-update-123"},
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.pipefacil.test",
    )
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
        pipefacil_timeout_seconds=5,
        app_version="0.1.0",
    )

    result = update_deal_properties(
        seq=100,
        properties={"priority": "low"},
        settings=settings,
        client=client,
    )

    assert result.status_code == 200
    assert result.request_id == "req-update-123"
    assert captured == {
        "method": "PATCH",
        "url": "https://api.pipefacil.test/api/v1/deals/100",
        "authorization": "Bearer pf_live_1234567890abcdef.example",
        "payload": {"customFields": {"priority": "low"}},
    }


def test_update_deal_properties_raises_without_api_key() -> None:
    with pytest.raises(PipefacilDealUpdateError) as exc_info:
        update_deal_properties(
            seq=100,
            properties={"priority": "low"},
            settings=Settings(pipefacil_api_key=None),
        )

    assert exc_info.value.error_code == "pipefacil_api_key_missing"


def test_update_deal_stage_patches_public_api_deal_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"data": {"id": "deal-1", "seq": 100, "stageId": "stage-2"}},
            headers={"x-request-id": "req-stage-123"},
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.pipefacil.test",
    )
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
    )

    result = update_deal_stage(
        seq=100,
        stage_id="  stage-2  ",
        settings=settings,
        client=client,
    )

    assert result.status_code == 200
    assert result.request_id == "req-stage-123"
    assert captured == {
        "method": "PATCH",
        "url": "https://api.pipefacil.test/api/v1/deals/100",
        "authorization": "Bearer pf_live_1234567890abcdef.example",
        "payload": {"stageId": "stage-2"},
    }


@pytest.mark.parametrize(
    ("seq", "stage_id", "api_key", "expected_error"),
    [
        (100, "stage-2", None, "pipefacil_api_key_missing"),
        (None, "stage-2", "pf-live", "pipefacil_deal_seq_missing"),
        (100, "   ", "pf-live", "pipefacil_stage_id_missing"),
    ],
)
def test_update_deal_stage_validates_inputs(
    seq: int | None,
    stage_id: str | None,
    api_key: str | None,
    expected_error: str,
) -> None:
    with pytest.raises(PipefacilDealUpdateError) as exc_info:
        update_deal_stage(
            seq=seq,
            stage_id=stage_id,
            settings=Settings(_env_file=None, pipefacil_api_key=api_key),
        )

    assert exc_info.value.error_code == expected_error


def test_update_deal_stage_maps_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unavailable", request=request)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.pipefacil.test",
    )

    with pytest.raises(PipefacilDealUpdateError) as exc_info:
        update_deal_stage(
            seq=100,
            stage_id="stage-2",
            settings=Settings(_env_file=None, pipefacil_api_key="pf-live"),
            client=client,
        )

    assert exc_info.value.error_code == "pipefacil_transport_error"
    assert exc_info.value.status_code is None


def test_update_deal_stage_maps_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"error": "invalid stage"},
            headers={"x-request-id": "req-stage-invalid"},
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.pipefacil.test",
    )

    with pytest.raises(PipefacilDealUpdateError) as exc_info:
        update_deal_stage(
            seq=100,
            stage_id="stage-2",
            settings=Settings(_env_file=None, pipefacil_api_key="pf-live"),
            client=client,
        )

    assert exc_info.value.error_code == "pipefacil_upstream_error"
    assert exc_info.value.status_code == 422
    assert exc_info.value.request_id == "req-stage-invalid"
    assert exc_info.value.response_body == {"error": "invalid stage"}


def test_send_public_text_message_raises_without_api_key() -> None:
    settings = Settings(
        pipefacil_api_key=None,
        pipefacil_base_url="https://api.pipefacil.test",
    )

    with pytest.raises(PipefacilSendMessageError) as exc_info:
        send_public_text_message(
            to="+5511000000001",
            text="Oi! Recebi sua mensagem.",
            settings=settings,
        )

    assert exc_info.value.error_code == "pipefacil_api_key_missing"


def test_send_public_text_message_raises_on_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "Validation failed"},
            headers={"x-request-id": "req-400"},
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.pipefacil.test",
    )
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
    )

    with pytest.raises(PipefacilSendMessageError) as exc_info:
        send_public_text_message(
            to="+5511000000001",
            text="Oi! Recebi sua mensagem.",
            settings=settings,
            client=client,
        )

    assert exc_info.value.error_code == "pipefacil_upstream_error"
    assert exc_info.value.status_code == 400
    assert exc_info.value.request_id == "req-400"
    assert exc_info.value.response_body == {"error": "Validation failed"}


def test_send_public_text_message_raises_transport_error_code() -> None:
    request = httpx.Request("POST", "https://api.pipefacil.test/api/v1/messages")

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=request)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.pipefacil.test",
    )
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
    )

    with pytest.raises(PipefacilSendMessageError) as exc_info:
        send_public_text_message(
            to="+5511000000001",
            text="Oi! Recebi sua mensagem.",
            settings=settings,
            client=client,
        )

    assert exc_info.value.error_code == "pipefacil_transport_error"


@pytest.mark.parametrize(
    ("to", "text", "expected_error_code"),
    [
        (None, "Oi! Recebi sua mensagem.", "recipient_phone_missing"),
        ("+5511000000001", "   ", "response_text_empty"),
    ],
)
def test_send_public_text_message_raises_precondition_error_codes(
    to: str | None,
    text: str,
    expected_error_code: str,
) -> None:
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
    )

    with pytest.raises(PipefacilSendMessageError) as exc_info:
        send_public_text_message(
            to=to,
            text=text,
            settings=settings,
        )

    assert exc_info.value.error_code == expected_error_code


def test_send_whatsapp_media_message_posts_expected_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content.decode())
        return httpx.Response(
            201,
            json={"data": {"id": "msg-media-1"}},
            headers={"x-request-id": "req-media-123"},
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.pipefacil.test",
    )
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
        app_version="0.1.0",
    )

    result = send_whatsapp_media_message(
        to="+5511000000001",
        media_type="image",
        media_url="https://cdn.example.com/foto.jpg",
        caption="Foto do produto",
        filename="foto.jpg",
        mime_type="image/jpeg",
        channel_id=None,
        sender_phone_number_id="111111111111111",
        settings=settings,
        client=client,
    )

    assert result.status_code == 201
    assert result.request_id == "req-media-123"
    assert captured == {
        "method": "POST",
        "url": "https://api.pipefacil.test/api/v1/messages",
        "authorization": "Bearer pf_live_1234567890abcdef.example",
        "payload": {
            "to": "+5511000000001",
            "type": "image",
            "text": None,
            "channelId": None,
            "senderPhoneNumberId": "111111111111111",
            "mediaAssetId": None,
            "mediaLink": "https://cdn.example.com/foto.jpg",
            "caption": "Foto do produto",
            "filename": "foto.jpg",
            "mimeType": "image/jpeg",
            "templateName": None,
            "templateLanguageCode": None,
            "templateComponents": None,
            "quotedMessageId": None,
        },
    }


@pytest.mark.parametrize(
    ("media_type", "media_url", "expected_error_code"),
    [
        ("sticker", "https://cdn.example.com/foto.webp", "response_media_type_unsupported"),
        ("image", None, "response_media_url_missing"),
        ("image", "http://cdn.example.com/foto.jpg", "response_media_url_invalid"),
    ],
)
def test_send_whatsapp_media_message_raises_precondition_error_codes(
    media_type: str,
    media_url: str | None,
    expected_error_code: str,
) -> None:
    settings = Settings(
        pipefacil_api_key="pf_live_1234567890abcdef.example",
        pipefacil_base_url="https://api.pipefacil.test",
    )

    with pytest.raises(PipefacilSendMessageError) as exc_info:
        send_whatsapp_media_message(
            to="+5511000000001",
            media_type=media_type,
            media_url=media_url,
            settings=settings,
        )

    assert exc_info.value.error_code == expected_error_code


def test_send_whatsapp_media_message_raises_without_api_key() -> None:
    settings = Settings(
        pipefacil_api_key=None,
        pipefacil_base_url="https://api.pipefacil.test",
    )

    with pytest.raises(PipefacilSendMessageError) as exc_info:
        send_whatsapp_media_message(
            to="+5511000000001",
            media_type="image",
            media_url="https://cdn.example.com/foto.jpg",
            settings=settings,
        )

    assert exc_info.value.error_code == "pipefacil_api_key_missing"


def test_download_pipefacil_media_requires_https_url() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(PipefacilMediaDownloadError) as exc_info:
        download_pipefacil_media(download_url="http://example.invalid/file.jpg", settings=settings)

    assert exc_info.value.error_code == "pipefacil_media_download_url_invalid"


def test_download_pipefacil_media_caps_streamed_size() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"abcdef", headers={"content-type": "image/jpeg"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = Settings(_env_file=None, pipefacil_media_max_bytes=5)

    with pytest.raises(PipefacilMediaDownloadError) as exc_info:
        download_pipefacil_media(
            download_url="https://example.invalid/file.jpg",
            settings=settings,
            client=client,
        )

    assert exc_info.value.error_code == "pipefacil_media_too_large"
    assert exc_info.value.content_length == 6


def test_normalize_text_inbound_message() -> None:
    payload = _message_received_payload(body="  oi  ")

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))

    assert result.message_type == "text"
    assert result.content == "oi"
    assert result.text == "oi"
    assert result.has_media is False


def test_normalize_image_inbound_message_downloads_and_builds_content_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_download_pipefacil_media(**kwargs):
        assert kwargs["download_url"] == SIGNED_DOWNLOAD_URL
        return PipefacilDownloadedMedia(
            content=b"jpeg-bytes",
            content_type="image/jpeg",
            content_length=10,
            status_code=200,
        )

    monkeypatch.setattr(
        pipefacil_mapping,
        "download_pipefacil_media",
        fake_download_pipefacil_media,
    )
    payload = _message_received_payload(message_type="image", body="", mime_type="image/jpeg")

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))

    assert result.message_type == "image"
    assert result.has_media is True
    assert result.media_mime_type == "image/jpeg"
    assert result.media_size == 10
    assert isinstance(result.content, list)
    assert result.content[0] == {
        "type": "text",
        "text": "Tipo de mensagem: image\nArquivo recebido: imagem anexada para analise.",
    }
    assert result.content[1] == {
        "type": "image",
        "base64": base64.b64encode(b"jpeg-bytes").decode("ascii"),
        "mime_type": "image/jpeg",
    }
    assert SIGNED_DOWNLOAD_URL not in json.dumps(result.content)


def test_normalize_sticker_inbound_message_treats_webp_as_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipefacil_mapping,
        "download_pipefacil_media",
        lambda **_: PipefacilDownloadedMedia(
            content=b"webp-bytes",
            content_type="image/webp",
            content_length=10,
            status_code=200,
        ),
    )
    payload = _message_received_payload(message_type="sticker", body="", mime_type="image/webp")

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))

    assert result.message_type == "sticker"
    assert result.media_mime_type == "image/webp"
    assert isinstance(result.content, list)
    assert result.content[0]["text"] == (
        "Tipo de mensagem: sticker\nArquivo recebido: figurinha anexada para analise."
    )
    assert result.content[1]["mime_type"] == "image/webp"


@pytest.mark.parametrize(
    "download_url_key",
    ["downloadURL", "mediaUrl", "media_url", "mediaLink", "media_link"],
)
def test_normalize_sticker_accepts_download_url_aliases_and_defaults_to_webp(
    monkeypatch: pytest.MonkeyPatch,
    download_url_key: str,
) -> None:
    def fake_download_pipefacil_media(**kwargs):
        assert kwargs["download_url"] == SIGNED_DOWNLOAD_URL
        return PipefacilDownloadedMedia(
            content=b"webp-bytes",
            content_type=None,
            content_length=10,
            status_code=200,
        )

    monkeypatch.setattr(
        pipefacil_mapping,
        "download_pipefacil_media",
        fake_download_pipefacil_media,
    )
    payload_data = _message_received_payload(
        message_type="sticker",
        body="",
        mime_type=None,
    ).model_dump(mode="json")
    payload_data["data"]["message"]["media"] = {download_url_key: SIGNED_DOWNLOAD_URL}
    payload = MessageReceivedEventRequest.model_validate(payload_data)

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))

    assert result.message_type == "sticker"
    assert result.media_mime_type == "image/webp"
    assert result.content[1]["mime_type"] == "image/webp"


@pytest.mark.parametrize("mime_key", ["mimetype", "mime", "content-type"])
def test_normalize_sticker_accepts_explicit_mime_aliases(
    monkeypatch: pytest.MonkeyPatch,
    mime_key: str,
) -> None:
    monkeypatch.setattr(
        pipefacil_mapping,
        "download_pipefacil_media",
        lambda **_: PipefacilDownloadedMedia(
            content=b"png-bytes",
            content_type=None,
            content_length=9,
            status_code=200,
        ),
    )
    payload_data = _message_received_payload(
        message_type="sticker",
        body="",
        mime_type=None,
    ).model_dump(mode="json")
    payload_data["data"]["message"]["media"] = {
        "downloadUrl": SIGNED_DOWNLOAD_URL,
        mime_key: "image/png",
    }
    payload = MessageReceivedEventRequest.model_validate(payload_data)

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))

    assert result.message_type == "sticker"
    assert result.media_mime_type == "image/png"


@pytest.mark.parametrize("message_type_key", ["messageType", "message_type"])
def test_normalize_sticker_accepts_nested_message_type_aliases(
    monkeypatch: pytest.MonkeyPatch,
    message_type_key: str,
) -> None:
    monkeypatch.setattr(
        pipefacil_mapping,
        "download_pipefacil_media",
        lambda **_: PipefacilDownloadedMedia(
            content=b"webp-bytes",
            content_type=None,
            content_length=10,
            status_code=200,
        ),
    )
    payload_data = _message_received_payload(
        message_type="media",
        body="",
        mime_type=None,
    ).model_dump(mode="json")
    payload_data["data"]["message"]["media"] = {
        "downloadUrl": SIGNED_DOWNLOAD_URL,
        message_type_key: "sticker",
    }
    payload = MessageReceivedEventRequest.model_validate(payload_data)

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))

    assert result.message_type == "sticker"
    assert result.media_mime_type == "image/webp"


def test_normalize_document_inbound_message_passes_file_content_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_bytes = b"%PDF-1.7 fake-pdf"
    monkeypatch.setattr(
        pipefacil_mapping,
        "download_pipefacil_media",
        lambda **_: PipefacilDownloadedMedia(
            content=pdf_bytes,
            content_type="application/pdf",
            content_length=len(pdf_bytes),
            status_code=200,
        ),
    )
    payload = _message_received_payload(
        message_type="document",
        body="  contrato assinado  ",
        mime_type="application/pdf",
        filename="contrato.pdf",
    )

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))

    assert result.message_type == "file"
    assert result.has_media is True
    assert result.media_mime_type == "application/pdf"
    assert result.media_size == len(pdf_bytes)
    assert isinstance(result.content, list)
    assert result.content[0]["text"] == (
        "Tipo de mensagem: file\n"
        "Legenda: contrato assinado\n"
        "Arquivo recebido: contrato.pdf (application/pdf) para analise.\n"
        "Se o arquivo nao puder ser lido pelo modelo, explique isso e peca o conteudo em texto."
    )
    assert result.content[1] == {
        "type": "file",
        "base64": base64.b64encode(pdf_bytes).decode("ascii"),
        "mime_type": "application/pdf",
        "filename": "contrato.pdf",
    }
    assert SIGNED_DOWNLOAD_URL not in json.dumps(result.content)


def test_normalize_file_inbound_message_generates_filename_from_mime_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipefacil_mapping,
        "download_pipefacil_media",
        lambda **_: PipefacilDownloadedMedia(
            content=b"pdf-bytes",
            content_type="application/pdf",
            content_length=9,
            status_code=200,
        ),
    )
    payload = _message_received_payload(
        message_type="file",
        body="",
        mime_type="application/pdf",
    )

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))

    assert isinstance(result.content, list)
    assert result.content[1]["filename"] == "inbound-file.pdf"


def test_normalize_unknown_media_inbound_message_accepts_generic_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipefacil_mapping,
        "download_pipefacil_media",
        lambda **_: PipefacilDownloadedMedia(
            content=b"spreadsheet-bytes",
            content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            content_length=17,
            status_code=200,
        ),
    )
    payload = _message_received_payload(
        message_type="attachment",
        body="",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="../relatorio",
    )

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))

    assert result.message_type == "file"
    assert result.media_mime_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert isinstance(result.content, list)
    assert result.content[1]["type"] == "file"
    assert result.content[1]["filename"] == "relatorio.xlsx"


def test_normalize_audio_inbound_message_converts_and_transcribes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipefacil_mapping,
        "download_pipefacil_media",
        lambda **_: PipefacilDownloadedMedia(
            content=b"ogg-bytes",
            content_type="audio/ogg",
            content_length=9,
            status_code=200,
        ),
    )

    def fake_convert_audio_to_wav(source_path, wav_path):
        assert source_path.read_bytes() == b"ogg-bytes"
        wav_path.write_bytes(b"wav-bytes")

    def fake_transcribe_audio_file(file_path, *, settings):
        assert file_path.read_bytes() == b"wav-bytes"
        assert settings.openai_transcription_model == "gpt-4o-mini-transcribe"
        return "quero saber sobre o plano"

    monkeypatch.setattr(pipefacil_mapping, "_convert_audio_to_wav", fake_convert_audio_to_wav)
    monkeypatch.setattr(pipefacil_mapping, "transcribe_audio_file", fake_transcribe_audio_file)
    payload = _message_received_payload(message_type="audio", body="", mime_type="audio/ogg")

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))

    assert result.message_type == "audio"
    assert result.content == "Tipo de mensagem: audio\nTranscricao: quero saber sobre o plano"
    assert result.transcription_length == len("quero saber sobre o plano")
    assert result.media_mime_type == "audio/ogg"
    assert result.media_size == 9


def test_normalize_audio_inbound_message_infers_audio_from_media_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipefacil_mapping,
        "download_pipefacil_media",
        lambda **_: PipefacilDownloadedMedia(
            content=b"ogg-bytes",
            content_type="audio/ogg; codecs=opus",
            content_length=9,
            status_code=200,
        ),
    )
    monkeypatch.setattr(
        pipefacil_mapping,
        "_convert_audio_to_wav",
        lambda source_path, wav_path: wav_path.write_bytes(b"wav-bytes"),
    )
    monkeypatch.setattr(
        pipefacil_mapping,
        "transcribe_audio_file",
        lambda file_path, *, settings: "audio transcrito",
    )
    payload_data = _message_received_payload(
        message_type="voice",
        body="",
        mime_type=None,
    ).model_dump(mode="json")
    payload_data["data"]["message"]["media"] = {
        "contentType": "audio/ogg; codecs=opus",
        "mediaType": "voice",
        "sizeBytes": 9,
        "downloadUrl": SIGNED_DOWNLOAD_URL,
    }
    payload = MessageReceivedEventRequest.model_validate(payload_data)

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))
    log_context = pipefacil_mapping.build_message_received_log_context(payload)

    assert result.message_type == "audio"
    assert result.content == "Tipo de mensagem: audio\nTranscricao: audio transcrito"
    assert result.media_mime_type == "audio/ogg"
    assert log_context["has_media"] is True
    assert log_context["media_mime_type"] == "audio/ogg; codecs=opus"
    assert log_context["media_type"] == "voice"
    assert log_context["media_size"] == 9


def test_message_received_extra_media_fields_are_used_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pipefacil_mapping,
        "download_pipefacil_media",
        lambda **_: PipefacilDownloadedMedia(
            content=b"ogg-bytes",
            content_type="audio/ogg",
            content_length=9,
            status_code=200,
        ),
    )
    monkeypatch.setattr(
        pipefacil_mapping,
        "_convert_audio_to_wav",
        lambda source_path, wav_path: wav_path.write_bytes(b"wav-bytes"),
    )
    monkeypatch.setattr(
        pipefacil_mapping,
        "transcribe_audio_file",
        lambda file_path, *, settings: "audio transcrito",
    )
    payload_data = _message_received_payload(
        message_type="voice",
        body="",
        mime_type=None,
    ).model_dump(mode="json")
    payload_data["data"]["message"].pop("media", None)
    payload_data["data"]["message"]["contentType"] = "audio/ogg; codecs=opus"
    payload_data["data"]["message"]["downloadUrl"] = SIGNED_DOWNLOAD_URL
    payload_data["data"]["message"]["sizeBytes"] = 9
    payload_data["data"]["message"]["providerPayload"] = {
        "downloadUrl": SIGNED_DOWNLOAD_URL,
    }
    payload = MessageReceivedEventRequest.model_validate(payload_data)

    result = normalize_message_received_content(payload, settings=Settings(_env_file=None))
    metadata = build_message_received_metadata(
        payload,
        session_id="deal-example-001",
        user_id="+5511000000001",
    )
    raw_payload = build_message_received_raw_log_payload(payload)
    serialized_metadata = json.dumps(metadata)
    serialized_raw_payload = json.dumps(raw_payload)

    assert result.message_type == "audio"
    assert SIGNED_DOWNLOAD_URL not in serialized_metadata
    assert SIGNED_DOWNLOAD_URL not in serialized_raw_payload
    assert "downloadUrl" not in serialized_metadata
    assert "downloadUrl" not in serialized_raw_payload
    assert metadata["message"]["download_url_present"] is True
    assert raw_payload["data"]["message"]["download_url_present"] is True


def test_message_received_metadata_and_raw_payload_do_not_expose_media_download_url() -> None:
    payload = _message_received_payload(message_type="image", body="", mime_type="image/jpeg")

    metadata = build_message_received_metadata(
        payload,
        session_id="deal-example-001",
        user_id="+5511000000001",
    )
    raw_payload = build_message_received_raw_log_payload(payload)

    serialized_metadata = json.dumps(metadata)
    serialized_raw_payload = json.dumps(raw_payload)
    assert SIGNED_DOWNLOAD_URL not in serialized_metadata
    assert SIGNED_DOWNLOAD_URL not in serialized_raw_payload
    assert metadata["message"]["media"]["download_url_present"] is True
    assert raw_payload["data"]["message"]["media"]["download_url_present"] is True
    assert "downloadUrl" not in metadata["message"]["media"]
    assert "downloadUrl" not in raw_payload["data"]["message"]["media"]


def test_message_received_raw_payload_sanitizes_every_message_in_a_batch() -> None:
    payload_data = _message_received_payload(
        message_type="image", body="", mime_type="image/jpeg"
    ).model_dump(mode="json")
    first_message = payload_data["data"].pop("message")
    payload_data["data"]["messages"] = [
        first_message,
        {
            **first_message,
            "id": "second-message-id",
            "externalId": "second-external-message-id",
        },
    ]

    raw_payload = build_message_received_raw_log_payload(
        MessageReceivedEventRequest.model_validate(payload_data)
    )
    serialized_raw_payload = json.dumps(raw_payload)

    assert SIGNED_DOWNLOAD_URL not in serialized_raw_payload
    assert all(
        message["media"]["download_url_present"] is True and "downloadUrl" not in message["media"]
        for message in raw_payload["data"]["messages"]
    )
