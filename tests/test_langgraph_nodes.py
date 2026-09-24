import importlib
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from openai import BadRequestError

import app.agent.chains.intent as intent_chains
import app.agent.chains.llm as llm_chains
import app.agent.chains.media as media_chains
import app.agent.chains.response as response_chains
import app.agent.nodes.intent as intent_nodes
import app.agent.nodes.response as response_nodes
import app.agent.response_support as response_support
from app.agent.chains.schemas import (
    AgentResponsePlan,
    GeneratedAudioChoice,
    IntentClassification,
    OpenAIAgentResponsePlan,
    OpenAIIntentClassification,
    OpenAIOutboundMediaClassification,
    OutboundMediaChoice,
    OutboundMediaClassification,
)
from app.agent.context import AgentRunContext
from app.agent.messages import has_sensitive_multimodal_content, message_to_text, serialize_messages
from app.agent.specialists import SpecialistResult
from app.core.config import RuntimeSettings, get_settings
from app.outbound_media import OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT, OutboundMediaAsset

delegate_nodes = importlib.import_module("app.agent.nodes.delegate_specialist")


@pytest.fixture(autouse=True)
def local_response_style(monkeypatch) -> None:
    monkeypatch.setattr(response_nodes, "_get_response_style", lambda: "Use WhatsApp style.")
    monkeypatch.setattr(
        response_nodes,
        "_get_available_media_prompt_view",
        lambda: OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT,
    )


def _unsupported_temperature_error() -> BadRequestError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(
        400,
        request=request,
        json={
            "error": {
                "message": (
                    "Unsupported value: 'temperature' does not support 0.0 with this "
                    "model. Only the default (1) value is supported."
                ),
                "type": "invalid_request_error",
                "param": "temperature",
                "code": "unsupported_value",
            }
        },
    )
    return BadRequestError(
        "temperature not supported",
        response=response,
        body=response.json()["error"],
    )


def _unsupported_file_error() -> BadRequestError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(
        400,
        request=request,
        json={
            "error": {
                "message": "Unsupported file type: application/vnd.pf-excel.",
                "type": "invalid_request_error",
                "param": "messages[1].content[1].file",
                "code": "unsupported_file",
            }
        },
    )
    return BadRequestError(
        "unsupported file",
        response=response,
        body=response.json()["error"],
    )


def test_classify_intent_returns_structured_update(monkeypatch) -> None:
    expected_config = {"callbacks": ["trace"]}

    class FakeClassifierChain:
        def invoke(self, payload, config=None):
            assert payload == {"latest_user_message": "Oi, tudo bem?"}
            assert config == expected_config
            return IntentClassification(
                intent="greeting",
                reason="A mensagem e uma saudacao simples.",
            )

    monkeypatch.setattr(intent_nodes, "_build_classifier_chain", lambda: FakeClassifierChain())

    result = intent_nodes.classify_intent(
        {"messages": [HumanMessage(content="Oi, tudo bem?")]},
        config=expected_config,
    )

    assert result == {
        "latest_user_message": "Oi, tudo bem?",
        "intent": "greeting",
        "intent_reason": "A mensagem e uma saudacao simples.",
        "requires_specialist": False,
        "specialist_name": None,
        "specialist_reason": None,
        "status": "classified",
    }


def test_classify_intent_can_request_test_specialist(monkeypatch) -> None:
    class FakeClassifierChain:
        def invoke(self, payload, config=None):
            return IntentClassification(
                intent="request",
                reason="Usuario pediu analise especialista.",
                requires_specialist=True,
                specialist_name="test_specialist",
                specialist_reason="Pedido explicito de especialista.",
            )

    monkeypatch.setattr(intent_nodes, "_build_classifier_chain", lambda: FakeClassifierChain())

    result = intent_nodes.classify_intent(
        {"messages": [HumanMessage(content="roda um teste com especialista")]}
    )

    assert result["intent"] == "request"
    assert result["requires_specialist"] is True
    assert result["specialist_name"] == "test_specialist"
    assert result["specialist_reason"] == "Pedido explicito de especialista."


def test_respond_appends_ai_message(monkeypatch) -> None:
    expected_config = {"callbacks": ["trace"]}
    input_message = HumanMessage(content="Me ajuda com isso.")
    output_message = AIMessage(content="Posso ajudar com uma resposta neutra.")

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            assert payload["intent"] == "request"
            assert payload["latest_user_message"] == "Me ajuda com isso."
            assert payload["conversation_history"] == [input_message]
            assert payload["specialist_result"] is None
            assert payload["specialist_context"] == "No specialist result."
            assert (
                "Identidade, limites e base de conhecimento da Liana" in payload["response_style"]
            )
            assert "O investimento informado é R$ 1.250" in payload["response_style"]
            assert "Informe o valor quando a pessoa perguntar" in payload["response_style"]
            assert "separe-as em mensagens distintas" in payload["response_style"]
            assert "Guia de estilo WhatsApp:\nUse WhatsApp style." in payload["response_style"]
            assert config == expected_config
            return output_message

    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": "Me ajuda com isso.",
            "intent": "request",
            "intent_reason": "O usuario pediu ajuda.",
            "status": "classified",
        },
        config=expected_config,
    )

    assert result["latest_user_message"] == "Me ajuda com isso."
    assert result["response_text"] == "Posso ajudar com uma resposta neutra."
    assert result["status"] == "responded"
    assert len(result["messages"]) == 1
    assert result["messages"][0] is output_message


def test_respond_passes_specialist_result_to_chain(monkeypatch) -> None:
    input_message = HumanMessage(content="Analisa isso com especialista.")
    specialist_result = {
        "status": "completed",
        "summary": "Pedido analisado.",
        "response_guidance": "Responder com proximo passo curto.",
        "extracted_fields": {"topic": "teste"},
        "confidence": 0.87,
        "error_code": None,
    }

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            assert payload["specialist_result"] == specialist_result
            assert '"summary":"Pedido analisado."' in payload["specialist_context"]
            return AIMessage(content="Resposta baseada no especialista.")

    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": "Analisa isso com especialista.",
            "intent": "request",
            "specialist_result": specialist_result,
        },
    )

    assert result["response_text"] == "Resposta baseada no especialista."


def test_respond_accepts_structured_response_without_media(monkeypatch) -> None:
    input_message = HumanMessage(content="Oi")

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            assert payload["available_media"] == OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT
            assert payload["conversation_history"] == [input_message]
            return AgentResponsePlan(response_text="Oi! Posso ajudar?", media_choices=[])

    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": "Oi",
            "intent": "greeting",
        }
    )

    assert result["response_text"] == "Oi! Posso ajudar?"
    assert result["response_media"] == []
    assert isinstance(result["messages"][0], AIMessage)
    assert result["messages"][0].content == "Oi! Posso ajudar?"


def test_respond_accepts_generated_audio_plan(monkeypatch) -> None:
    input_message = HumanMessage(content="Me explica por audio.")

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            return AgentResponsePlan(
                response_text="Te mandei um audio explicando melhor.",
                generated_audio=GeneratedAudioChoice(
                    text=(
                        "Funciona assim: primeiro eu entendo seu caso e depois te passo "
                        "o proximo passo."
                    ),
                    reason="Usuario pediu explicacao por audio.",
                ),
            )

    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": "Me explica por audio.",
            "intent": "request",
        }
    )

    assert result["response_text"] == "Te mandei um audio explicando melhor."
    assert result["response_audio"] == {
        "text": ("Funciona assim: primeiro eu entendo seu caso e depois te passo o proximo passo."),
        "reason": "Usuario pediu explicacao por audio.",
    }


def test_respond_keeps_valid_outbound_media_choice(monkeypatch) -> None:
    input_message = HumanMessage(content="Me manda o catalogo.")
    media_asset = OutboundMediaAsset(
        id="catalogo-pdf",
        type="document",
        title="Catalogo PDF",
        description="Catalogo comercial resumido.",
        when_to_use="Quando o usuario pedir o catalogo.",
        media_url="https://cdn.example.com/catalogo.pdf",
        content_type="application/pdf",
        filename="catalogo.pdf",
        enabled=True,
    )

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            assert "https://cdn.example.com" not in payload["available_media"]
            return AgentResponsePlan(
                response_text="Claro, segue o catalogo.",
                media_choices=[
                    OutboundMediaChoice(
                        media_id="catalogo-pdf",
                        caption="Catalogo em PDF",
                        reason="Usuario pediu o catalogo.",
                    )
                ],
            )

    monkeypatch.setattr(
        response_nodes,
        "_get_available_media_prompt_view",
        lambda: '{"media_id":"catalogo-pdf","type":"document"}',
    )
    monkeypatch.setattr(
        response_nodes,
        "get_enabled_outbound_media_by_id",
        lambda: {"catalogo-pdf": media_asset},
    )
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())
    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": "Me manda o catalogo.",
            "intent": "request",
        }
    )

    assert result["response_text"] == "Claro, segue o catalogo."
    assert result["response_media"] == [
        {
            "media_id": "catalogo-pdf",
            "type": "document",
            "caption": "Catalogo em PDF",
            "reason": "Usuario pediu o catalogo.",
            "content_type": "application/pdf",
            "filename": "catalogo.pdf",
        }
    ]
    assert "media_url" not in str(result["response_media"])


def test_respond_injects_delivery_context_for_requested_audio_media(monkeypatch) -> None:
    input_message = HumanMessage(
        content="Boa tarde, consegue me mandar um audio apresentando as coisas?"
    )
    media_asset = OutboundMediaAsset(
        id="audio_apresentacao_produto",
        type="audio",
        title="Audio de apresentacao do produto",
        description="Audio curto de apresentacao do produto.",
        when_to_use="Quando o lead pedir uma apresentacao rapida por audio.",
        media_url="https://cdn.example.com/apresentacao-produto.ogg",
        content_type="audio/ogg",
        filename="apresentacao-produto.ogg",
        enabled=True,
    )

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            delivery_context = payload["conversation_history"][0]
            assert delivery_context.type == "system"
            assert "Do not say you cannot send media" in delivery_context.content
            assert "audio_apresentacao_produto" in delivery_context.content
            assert "media_url" not in delivery_context.content
            assert "https://cdn.example.com" not in delivery_context.content
            assert payload["conversation_history"][1] is input_message
            return AgentResponsePlan(
                response_text="Boa tarde! Te mando um audio rapidinho.",
                media_choices=[
                    OutboundMediaChoice(
                        media_id="audio_apresentacao_produto",
                        caption=None,
                        reason="Usuario pediu audio de apresentacao.",
                    )
                ],
                generated_audio=GeneratedAudioChoice(
                    text="Este audio gerado nao deve ser enviado.",
                    reason="Catalog audio already satisfies the request.",
                ),
            )

    monkeypatch.setattr(
        response_nodes,
        "_get_available_media_prompt_view",
        lambda: (
            '{"media_id":"audio_apresentacao_produto","type":"audio",'
            '"title":"Audio de apresentacao do produto",'
            '"description":"Audio curto de apresentacao do produto.",'
            '"when_to_use":"Quando o lead pedir uma apresentacao rapida por audio."}'
        ),
    )
    monkeypatch.setattr(
        response_nodes,
        "get_enabled_outbound_media_by_id",
        lambda: {"audio_apresentacao_produto": media_asset},
    )
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": input_message.content,
            "intent": "request",
        }
    )

    assert result["response_text"] == "Boa tarde! Te mando um audio rapidinho."
    assert result["response_media"][0]["media_id"] == "audio_apresentacao_produto"
    assert result["response_media"][0]["type"] == "audio"
    assert result["response_audio"] is None


def test_respond_infers_explicit_monthly_image_when_model_omits_media_choice(
    monkeypatch,
) -> None:
    input_message = HumanMessage(content="Me manda a imagem dos preços mensais.")
    media_asset = OutboundMediaAsset(
        id="planos-mensais",
        type="image",
        title="Planos mensais",
        description="Imagem com preços mensais.",
        when_to_use="Quando o usuário pedir valores do plano mensal em imagem.",
        media_url="https://cdn.example.com/planos-mensais.png",
        content_type="image/png",
        filename="planos-mensais.png",
        enabled=True,
    )

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            return AgentResponsePlan(
                response_text="Claro, segue a imagem.",
                media_choices=[],
                generated_audio=GeneratedAudioChoice(
                    text="Também posso explicar os valores por áudio.",
                    reason="Complemento falado.",
                ),
            )

    class FakeMediaClassifierChain:
        def invoke(self, payload, config=None):
            assert payload == {
                "latest_user_message": input_message.content,
                "conversation_history": [{"role": "user", "content": input_message.content}],
                "available_media": '{"media_id":"planos-mensais","type":"image"}',
            }
            return OutboundMediaClassification(
                action="send",
                media_id="planos-mensais",
                reason="O usuário pediu a imagem dos preços mensais.",
            )

    monkeypatch.setattr(
        response_nodes,
        "_get_available_media_prompt_view",
        lambda: '{"media_id":"planos-mensais","type":"image"}',
    )
    monkeypatch.setattr(
        response_nodes,
        "get_enabled_outbound_media_by_id",
        lambda: {"planos-mensais": media_asset},
    )
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())
    monkeypatch.setattr(
        response_nodes,
        "_build_outbound_media_classifier_chain",
        lambda: FakeMediaClassifierChain(),
    )

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": input_message.content,
            "intent": "request",
        }
    )

    assert result["response_media"][0]["media_id"] == "planos-mensais"
    assert result["response_media"][0]["type"] == "image"
    assert result["response_audio"] == {
        "text": "Também posso explicar os valores por áudio.",
        "reason": "Complemento falado.",
    }


def test_respond_infers_catalog_audio_after_invalid_model_choice_and_suppresses_tts(
    monkeypatch,
) -> None:
    input_message = HumanMessage(content="Pode mandar o áudio de apresentação?")
    media_asset = OutboundMediaAsset(
        id="audio-apresentacao",
        type="audio",
        title="Áudio de apresentação",
        description="Apresentação geral em áudio.",
        when_to_use="Quando o usuário pedir um áudio de apresentação.",
        media_url="https://cdn.example.com/apresentacao.ogg",
        content_type="audio/ogg",
        filename="apresentacao.ogg",
        enabled=True,
    )

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            return AgentResponsePlan(
                response_text="Claro, vou mandar.",
                media_choices=[
                    OutboundMediaChoice(
                        media_id="inexistente",
                        reason="Escolha inválida do modelo.",
                    )
                ],
                generated_audio=GeneratedAudioChoice(
                    text="Este áudio dinâmico não deve ser enviado.",
                    reason="Catálogo já contém o áudio.",
                ),
            )

    class FakeMediaClassifierChain:
        def invoke(self, payload, config=None):
            return OutboundMediaClassification(
                action="send",
                media_id="audio-apresentacao",
                reason="O usuário pediu o áudio de apresentação.",
            )

    monkeypatch.setattr(
        response_nodes,
        "_get_available_media_prompt_view",
        lambda: '{"media_id":"audio-apresentacao","type":"audio"}',
    )
    monkeypatch.setattr(
        response_nodes,
        "get_enabled_outbound_media_by_id",
        lambda: {"audio-apresentacao": media_asset},
    )
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())
    monkeypatch.setattr(
        response_nodes,
        "_build_outbound_media_classifier_chain",
        lambda: FakeMediaClassifierChain(),
    )

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": input_message.content,
            "intent": "request",
        }
    )

    assert result["response_media"][0]["media_id"] == "audio-apresentacao"
    assert result["response_audio"] is None


def test_media_classifier_can_select_default_plan_without_period(monkeypatch) -> None:
    default_asset = OutboundMediaAsset(
        id="planos-padrao",
        type="document",
        title="Planos disponíveis",
        description="Resumo dos planos.",
        when_to_use="Quando pedirem preços sem indicar periodicidade.",
        media_url="https://cdn.example.com/planos.pdf",
        content_type="application/pdf",
        filename="planos.pdf",
        enabled=True,
    )
    monthly_asset = default_asset.model_copy(
        update={
            "id": "planos-mensais",
            "title": "Planos mensais",
            "when_to_use": "Quando pedirem preços mensais.",
            "media_url": "https://cdn.example.com/planos-mensais.pdf",
            "filename": "planos-mensais.pdf",
        }
    )
    input_message = HumanMessage(content="Quais são os preços dos planos?")

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            return AgentResponsePlan(response_text="Vou enviar a tabela disponível.")

    class FakeMediaClassifierChain:
        def invoke(self, payload, config=None):
            assert payload["latest_user_message"] == input_message.content
            return OutboundMediaClassification(
                action="send",
                media_id="planos-padrao",
                reason="O catálogo indica este material para preços sem periodicidade.",
            )

    monkeypatch.setattr(
        response_nodes,
        "_get_available_media_prompt_view",
        lambda: (
            '{"media_id":"planos-padrao","type":"document"}\n'
            '{"media_id":"planos-mensais","type":"document"}'
        ),
    )
    monkeypatch.setattr(
        response_nodes,
        "get_enabled_outbound_media_by_id",
        lambda: {
            default_asset.id: default_asset,
            monthly_asset.id: monthly_asset,
        },
    )
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())
    monkeypatch.setattr(
        response_nodes,
        "_build_outbound_media_classifier_chain",
        lambda: FakeMediaClassifierChain(),
    )

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": input_message.content,
            "intent": "question",
        }
    )

    assert result["response_media"][0]["media_id"] == "planos-padrao"


def test_media_classifier_respects_media_opt_out(monkeypatch) -> None:
    input_message = HumanMessage(content="Não me envie documento nem PDF.")
    media_asset = OutboundMediaAsset(
        id="planos-padrao",
        type="document",
        title="Planos disponíveis",
        description="Resumo dos planos.",
        when_to_use="Quando pedirem preços sem indicar periodicidade.",
        media_url="https://cdn.example.com/planos.pdf",
        content_type="application/pdf",
        filename="planos.pdf",
        enabled=True,
    )

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            return AgentResponsePlan(response_text="Certo, sigo sem enviar arquivos.")

    class FakeMediaClassifierChain:
        def invoke(self, payload, config=None):
            assert payload["latest_user_message"] == input_message.content
            return OutboundMediaClassification(
                action="none",
                media_id=None,
                reason="O usuário recusou documento e PDF.",
            )

    monkeypatch.setattr(
        response_nodes,
        "_get_available_media_prompt_view",
        lambda: '{"media_id":"planos-padrao","type":"document"}',
    )
    monkeypatch.setattr(
        response_nodes,
        "get_enabled_outbound_media_by_id",
        lambda: {media_asset.id: media_asset},
    )
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())
    monkeypatch.setattr(
        response_nodes,
        "_build_outbound_media_classifier_chain",
        lambda: FakeMediaClassifierChain(),
    )

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": input_message.content,
            "intent": "request",
        }
    )

    assert result["response_media"] == []


def test_media_classifier_fails_closed_for_unknown_or_invalid_output() -> None:
    media_asset = OutboundMediaAsset(
        id="catalogo-pdf",
        type="document",
        title="Catálogo",
        description="Catálogo comercial.",
        when_to_use="Quando o usuário pedir o catálogo.",
        media_url="https://cdn.example.com/catalogo.pdf",
        content_type="application/pdf",
        filename="catalogo.pdf",
        enabled=True,
    )

    class FakeLogger:
        def __init__(self) -> None:
            self.records: list[tuple[str, dict[str, object]]] = []

        def warning(self, message, *args, **kwargs) -> None:
            self.records.append((message, kwargs.get("extra") or {}))

    class UnknownMediaChain:
        def invoke(self, payload, config=None):
            return {"action": "send", "media_id": "inventado", "reason": "Pedido."}

    logger = FakeLogger()
    choice = response_support.infer_catalog_media_choice(
        "Envie o catálogo.",
        [HumanMessage(content="Envie o catálogo.")],
        '{"media_id":"catalogo-pdf","type":"document"}',
        config=None,
        chain_factory=lambda: UnknownMediaChain(),
        media_by_id_loader=lambda: {media_asset.id: media_asset},
        logger=logger,
    )

    assert choice is None
    assert logger.records == [
        (
            "agent.outbound_media.ignored",
            {
                "pipeline_step": "agent.outbound_media.ignored",
                "media_id": "inventado",
                "reason": "unknown_or_disabled_media",
            },
        )
    ]

    class InvalidResponseChain:
        def invoke(self, payload, config=None):
            return object()

    logger = FakeLogger()
    choice = response_support.infer_catalog_media_choice(
        "Envie o catálogo.",
        [HumanMessage(content="Envie o catálogo.")],
        '{"media_id":"catalogo-pdf","type":"document"}',
        config=None,
        chain_factory=lambda: InvalidResponseChain(),
        media_by_id_loader=lambda: {media_asset.id: media_asset},
        logger=logger,
    )

    assert choice is None
    assert logger.records == [
        (
            "agent.outbound_media.classification_failed",
            {"pipeline_step": "agent.outbound_media.classification_failed"},
        )
    ]


def test_media_classifier_skips_unavailable_or_empty_catalog() -> None:
    class FailIfCalled:
        def invoke(self, payload, config=None):
            pytest.fail("The classifier must not run without an enabled catalog.")

    logger = type("FakeLogger", (), {"warning": lambda self, *args, **kwargs: None})()
    for available_media, loader in [
        (OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT, lambda: {}),
        ('{"media_id":"catalogo-pdf","type":"document"}', lambda: {}),
    ]:
        assert (
            response_support.infer_catalog_media_choice(
                "Envie o catálogo.",
                [],
                available_media,
                config=None,
                chain_factory=lambda: FailIfCalled(),
                media_by_id_loader=loader,
                logger=logger,
            )
            is None
        )


def test_media_classifier_accepts_wire_schema_and_rejects_missing_id(monkeypatch) -> None:
    media_asset = OutboundMediaAsset(
        id="catalogo-pdf",
        type="document",
        title="Catálogo",
        description="Catálogo comercial.",
        when_to_use="Quando o usuário pedir o catálogo.",
        media_url="https://cdn.example.com/catalogo.pdf",
        content_type="application/pdf",
        filename="catalogo.pdf",
        enabled=True,
    )
    logger = type("FakeLogger", (), {"warning": lambda self, *args, **kwargs: None})()

    class WireSchemaChain:
        def invoke(self, payload, config=None):
            return OpenAIOutboundMediaClassification(
                action="send",
                media_id="catalogo-pdf",
                reason="Pedido explícito.",
            )

    choice = response_support.infer_catalog_media_choice(
        "Envie o catálogo.",
        [],
        '{"media_id":"catalogo-pdf","type":"document"}',
        config=None,
        chain_factory=lambda: WireSchemaChain(),
        media_by_id_loader=lambda: {media_asset.id: media_asset},
        logger=logger,
    )

    assert choice == OutboundMediaChoice(
        media_id="catalogo-pdf",
        reason="Pedido explícito.",
    )

    monkeypatch.setattr(
        response_support,
        "_response_to_media_classification",
        lambda response: type(
            "UnvalidatedClassification",
            (),
            {"action": "send", "media_id": None, "reason": "Não usar."},
        )(),
    )
    assert (
        response_support.infer_catalog_media_choice(
            "Envie o catálogo.",
            [],
            '{"media_id":"catalogo-pdf","type":"document"}',
            config=None,
            chain_factory=lambda: WireSchemaChain(),
            media_by_id_loader=lambda: {media_asset.id: media_asset},
            logger=logger,
        )
        is None
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "send", "media_id": None, "reason": "Pedido."},
        {"action": "none", "media_id": "catalogo-pdf", "reason": "Recusou."},
    ],
)
def test_media_classifier_schema_enforces_action_and_media_id(payload) -> None:
    with pytest.raises(ValueError):
        OutboundMediaClassification.model_validate(payload)


def test_media_classifier_rejects_unsupported_response_type() -> None:
    with pytest.raises(TypeError, match="unsupported response"):
        response_support._response_to_media_classification(object())


def test_respond_discards_unknown_outbound_media_choice(monkeypatch) -> None:
    input_message = HumanMessage(content="Me manda algo.")
    fake_logger = type(
        "FakeLogger",
        (),
        {
            "records": [],
            "warning": lambda self, message, *args, **kwargs: self.records.append(
                (message, kwargs.get("extra") or {})
            ),
        },
    )()

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            return AgentResponsePlan(
                response_text="Nao tenho esse material aqui.",
                media_choices=[
                    OutboundMediaChoice(
                        media_id="inventado",
                        caption="Arquivo",
                        reason="Escolha invalida.",
                    )
                ],
            )

    monkeypatch.setattr(response_nodes, "LOGGER", fake_logger)
    monkeypatch.setattr(response_nodes, "get_enabled_outbound_media_by_id", lambda: {})
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": "Me manda algo.",
            "intent": "request",
        }
    )

    assert result["response_media"] == []
    assert fake_logger.records == [
        (
            "agent.outbound_media.ignored",
            {
                "pipeline_step": "agent.outbound_media.ignored",
                "media_id": "inventado",
                "reason": "unknown_or_disabled_media",
            },
        )
    ]


def test_classify_intent_retries_without_temperature(monkeypatch) -> None:
    calls: list[bool] = []

    class FakeClassifierChain:
        def __init__(self, *, should_fail: bool) -> None:
            self.should_fail = should_fail

        def invoke(self, payload, config=None):
            calls.append(self.should_fail)
            assert payload == {"latest_user_message": "Oi, tudo bem?"}
            assert config == {"callbacks": ["trace"]}
            if self.should_fail:
                raise _unsupported_temperature_error()
            return IntentClassification(
                intent="greeting",
                reason="A mensagem e uma saudacao simples.",
            )

    monkeypatch.setattr(
        intent_nodes,
        "_build_classifier_chain",
        lambda *, use_custom_temperature=True: FakeClassifierChain(
            should_fail=use_custom_temperature
        ),
    )

    result = intent_nodes.classify_intent(
        {"messages": [HumanMessage(content="Oi, tudo bem?")]},
        config={"callbacks": ["trace"]},
    )

    assert calls == [True, False]
    assert result["intent"] == "greeting"
    assert result["status"] == "classified"


def test_respond_retries_without_temperature(monkeypatch) -> None:
    calls: list[bool] = []
    input_message = HumanMessage(content="Me ajuda com isso.")
    output_message = AIMessage(content="Posso ajudar com uma resposta neutra.")

    class FakeResponderChain:
        def __init__(self, *, should_fail: bool) -> None:
            self.should_fail = should_fail

        def invoke(self, payload, config=None):
            calls.append(self.should_fail)
            assert payload["conversation_history"] == [input_message]
            assert config == {"callbacks": ["trace"]}
            if self.should_fail:
                raise _unsupported_temperature_error()
            return output_message

    monkeypatch.setattr(
        response_nodes,
        "_build_responder_chain",
        lambda *, use_custom_temperature=True: FakeResponderChain(
            should_fail=use_custom_temperature
        ),
    )

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": "Me ajuda com isso.",
            "intent": "request",
            "intent_reason": "O usuario pediu ajuda.",
            "status": "classified",
        },
        config={"callbacks": ["trace"]},
    )

    assert calls == [True, False]
    assert result["response_text"] == "Posso ajudar com uma resposta neutra."
    assert result["messages"][0] is output_message


def test_respond_retries_with_text_only_history_when_file_block_is_rejected(monkeypatch) -> None:
    calls: list[list[object]] = []
    input_message = HumanMessage(
        content=[
            {"type": "text", "text": "Tipo de mensagem: file\nArquivo recebido: relatorio.xls."},
            {
                "type": "file",
                "base64": "very-sensitive-file-base64",
                "mime_type": "application/vnd.pf-excel",
                "filename": "relatorio.xls",
            },
        ]
    )

    class FakeResponderChain:
        def invoke(self, payload, config=None):
            calls.append(payload["conversation_history"])
            assert config == {"callbacks": ["trace"]}
            if len(calls) == 1:
                raise _unsupported_file_error()

            assert payload["conversation_history"] == [
                {
                    "role": "user",
                    "content": (
                        "Tipo de mensagem: file\nArquivo recebido: relatorio.xls.\n"
                        "[file mime_type=application/vnd.pf-excel]"
                    ),
                }
            ]
            assert "very-sensitive-file-base64" not in payload["conversation_history"][0]["content"]
            return AIMessage(content="Recebi o arquivo, mas preciso do conteudo em texto.")

    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    result = response_nodes.respond(
        {
            "messages": [input_message],
            "latest_user_message": (
                "Tipo de mensagem: file\nArquivo recebido: relatorio.xls.\n"
                "[file mime_type=application/vnd.pf-excel]"
            ),
            "intent": "request",
        },
        config={"callbacks": ["trace"]},
    )

    assert len(calls) == 2
    assert calls[0] == [input_message]
    assert result["response_text"] == "Recebi o arquivo, mas preciso do conteudo em texto."


def test_latest_user_message_prefers_last_human_message() -> None:
    latest_message = intent_nodes._latest_user_message(
        {
            "messages": [
                HumanMessage(content="Oi"),
                AIMessage(content="Como posso ajudar?"),
            ]
        }
    )

    assert latest_message == "Oi"


def test_latest_user_message_supports_protocol_dict_messages() -> None:
    latest_message = intent_nodes._latest_user_message(
        {
            "messages": [
                {"type": "human", "content": "Oi pelo protocolo"},
                {"type": "ai", "content": "Resposta anterior"},
            ]
        }
    )

    assert latest_message == "Oi pelo protocolo"


def test_multimodal_messages_are_serialized_without_binary_payload() -> None:
    message = HumanMessage(
        content=[
            {"type": "text", "text": "Tipo de mensagem: image\nArquivo recebido: imagem."},
            {
                "type": "image",
                "base64": "very-sensitive-base64",
                "mime_type": "image/jpeg",
            },
        ]
    )

    text = message_to_text(message)
    serialized = serialize_messages([message])

    assert text == (
        "Tipo de mensagem: image\nArquivo recebido: imagem.\n[image mime_type=image/jpeg]"
    )
    assert "very-sensitive-base64" not in text
    assert serialized == [
        {
            "role": "user",
            "content": (
                "Tipo de mensagem: image\nArquivo recebido: imagem.\n[image mime_type=image/jpeg]"
            ),
        }
    ]
    assert "very-sensitive-base64" not in serialized[0]["content"]


def test_file_messages_are_serialized_without_binary_payload() -> None:
    message = HumanMessage(
        content=[
            {"type": "text", "text": "Tipo de mensagem: file\nArquivo recebido: contrato.pdf."},
            {
                "type": "file",
                "base64": "very-sensitive-file-base64",
                "mime_type": "application/pdf",
                "filename": "contrato.pdf",
            },
        ]
    )

    text = message_to_text(message)
    serialized = serialize_messages([message])

    assert text == (
        "Tipo de mensagem: file\nArquivo recebido: contrato.pdf.\n[file mime_type=application/pdf]"
    )
    assert "very-sensitive-file-base64" not in text
    assert "contrato.pdf" in text
    assert serialized == [
        {
            "role": "user",
            "content": (
                "Tipo de mensagem: file\nArquivo recebido: contrato.pdf.\n"
                "[file mime_type=application/pdf]"
            ),
        }
    ]
    assert "very-sensitive-file-base64" not in serialized[0]["content"]


def test_sensitive_multimodal_content_is_detected() -> None:
    safe_message = HumanMessage(content="oi")
    file_message = HumanMessage(
        content=[
            {"type": "text", "text": "Tipo de mensagem: file"},
            {
                "type": "file",
                "base64": "very-sensitive-file-base64",
                "mime_type": "application/pdf",
            },
        ]
    )

    assert has_sensitive_multimodal_content([safe_message]) is False
    assert has_sensitive_multimodal_content([file_message]) is True


def test_delegate_specialist_skips_when_feature_flag_is_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_SPECIALISTS_ENABLED", "false")
    get_settings.cache_clear()

    result = delegate_nodes.delegate_specialist(
        {
            "messages": [HumanMessage(content="usa especialista")],
            "latest_user_message": "usa especialista",
            "intent": "request",
            "requires_specialist": True,
            "specialist_name": "test_specialist",
        },
        config={"configurable": {"thread_id": "thread-1"}},
        runtime=SimpleNamespace(
            context=AgentRunContext(
                settings=RuntimeSettings(
                    openai_specialists_enabled=False,
                    openai_specialist_max_turns=3,
                )
            )
        ),
    )

    assert result["specialist_status"] == "skipped"
    assert result["specialist_name"] == "test_specialist"
    assert result["specialist_result"]["error_code"] == "openai_specialists_disabled"
    get_settings.cache_clear()


def test_delegate_specialist_normalizes_known_alias_when_feature_flag_is_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_SPECIALISTS_ENABLED", "false")
    get_settings.cache_clear()

    result = delegate_nodes.delegate_specialist(
        {
            "messages": [HumanMessage(content="usa especialista")],
            "latest_user_message": "usa especialista",
            "intent": "request",
            "requires_specialist": True,
            "specialist_name": "testing/deep agent",
        },
        runtime=SimpleNamespace(
            context=AgentRunContext(settings=RuntimeSettings(openai_specialists_enabled=False))
        ),
    )

    assert result["specialist_name"] == "test_specialist"
    assert result["specialist_status"] == "skipped"
    assert result["specialist_result"]["error_code"] == "openai_specialists_disabled"
    get_settings.cache_clear()


def test_delegate_specialist_fails_unknown_specialist_before_sdk(monkeypatch) -> None:
    result = delegate_nodes.delegate_specialist(
        {
            "messages": [HumanMessage(content="usa especialista")],
            "latest_user_message": "usa especialista",
            "intent": "request",
            "requires_specialist": True,
            "specialist_name": "unknown specialist",
        },
        runtime=SimpleNamespace(
            context=AgentRunContext(settings=RuntimeSettings(openai_specialists_enabled=True))
        ),
    )

    assert result["specialist_name"] == "unknown specialist"
    assert result["specialist_status"] == "failed"
    assert result["specialist_result"]["error_code"] == "specialist_unknown"
    get_settings.cache_clear()


def test_delegate_specialist_runs_mocked_runner_when_enabled(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_specialist(*, specialist_name, request, settings):
        captured["specialist_name"] = specialist_name
        captured["request"] = request
        captured["max_turns"] = settings.openai_specialist_max_turns
        return SpecialistResult(
            status="completed",
            summary="Resumo interno.",
            response_guidance="Use o resumo.",
            extracted_fields={"kind": "test"},
            confidence=0.9,
        )

    monkeypatch.setattr(delegate_nodes, "_run_specialist", fake_run_specialist)

    result = delegate_nodes.delegate_specialist(
        {
            "messages": [HumanMessage(content="usa especialista")],
            "latest_user_message": "usa especialista",
            "intent": "request",
            "intent_reason": "Pedido explicito.",
            "requires_specialist": True,
            "specialist_name": "test_specialist",
            "specialist_reason": "Rodar analise especializada.",
        },
        config={"configurable": {"thread_id": "thread-1"}},
        runtime=SimpleNamespace(
            context=AgentRunContext(
                settings=RuntimeSettings(
                    openai_specialists_enabled=True,
                    openai_specialist_max_turns=3,
                )
            )
        ),
    )

    request = captured["request"]
    assert captured["specialist_name"] == "test_specialist"
    assert captured["max_turns"] == 3
    assert request.latest_user_message == "usa especialista"
    assert request.objective == "Rodar analise especializada."
    assert result["specialist_status"] == "completed"
    assert result["specialist_result"]["extracted_fields"] == {"kind": "test"}
    get_settings.cache_clear()


def test_delegate_specialist_records_failed_result(monkeypatch) -> None:
    def fake_run_specialist(*, specialist_name, request, settings):
        return SpecialistResult(
            status="failed",
            summary="Falhou.",
            response_guidance="Continue sem especialista.",
            error_code="openai_specialist_error",
        )

    monkeypatch.setattr(delegate_nodes, "_run_specialist", fake_run_specialist)

    result = delegate_nodes.delegate_specialist(
        {
            "messages": [HumanMessage(content="usa especialista")],
            "latest_user_message": "usa especialista",
            "intent": "request",
            "requires_specialist": True,
            "specialist_name": "test_specialist",
        },
        runtime=SimpleNamespace(
            context=AgentRunContext(settings=RuntimeSettings(openai_specialists_enabled=True))
        ),
    )

    assert result["specialist_status"] == "failed"
    assert result["specialist_result"]["error_code"] == "openai_specialist_error"
    get_settings.cache_clear()


def test_delegate_specialist_handles_invalid_request(monkeypatch) -> None:
    result = delegate_nodes.delegate_specialist(
        {
            "messages": [],
            "intent": "request",
            "requires_specialist": True,
            "specialist_name": "test_specialist",
        },
        runtime=SimpleNamespace(
            context=AgentRunContext(settings=RuntimeSettings(openai_specialists_enabled=True))
        ),
    )

    assert result["specialist_status"] == "failed"
    assert result["specialist_result"]["error_code"] == "specialist_request_invalid"
    get_settings.cache_clear()


def test_specialist_request_uses_safe_serialized_history() -> None:
    message = HumanMessage(
        content=[
            {"type": "text", "text": "Arquivo recebido."},
            {
                "type": "image",
                "base64": "very-sensitive-base64",
                "mime_type": "image/jpeg",
            },
        ]
    )

    request = delegate_nodes._build_specialist_request(
        {
            "messages": [message],
            "latest_user_message": "Arquivo recebido.\n[image mime_type=image/jpeg]",
            "intent": "request",
        }
    )

    serialized_request = request.model_dump_json()
    assert "very-sensitive-base64" not in serialized_request
    assert "downloadUrl" not in serialized_request
    assert "[image mime_type=image/jpeg]" in serialized_request


def test_gpt5_models_skip_custom_temperature(monkeypatch) -> None:
    captured_kwargs = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm_chains, "ChatOpenAI", FakeChatOpenAI)
    get_settings.cache_clear()

    llm_chains.get_chat_model(
        RuntimeSettings(openai_model="gpt-5.3-chat-latest"),
        temperature=0.3,
    )

    assert captured_kwargs == {
        "model": "gpt-5.3-chat-latest",
        "api_key": "sk-test",
    }
    get_settings.cache_clear()


def test_gpt56_reasoning_uses_compatible_temperature_and_effort(monkeypatch) -> None:
    captured_kwargs = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm_chains, "ChatOpenAI", FakeChatOpenAI)
    get_settings.cache_clear()

    llm_chains.get_chat_model(
        RuntimeSettings(openai_model="gpt-5.6-luna", openai_reasoning_effort="medium"),
        temperature=0.3,
    )

    assert captured_kwargs == {
        "model": "gpt-5.6-luna",
        "api_key": "sk-test",
        "temperature": 1,
        "reasoning_effort": "medium",
    }
    get_settings.cache_clear()


def test_gpt56_reasoning_uses_json_schema_for_structured_chains(monkeypatch) -> None:
    classifier_calls = []
    media_classifier_calls = []
    responder_calls = []

    class FakeChatOpenAI:
        def __init__(self, calls) -> None:
            self.calls = calls

        def with_structured_output(self, schema, *, method):
            self.calls.append((schema, method))
            return RunnableLambda(lambda value: value)

    monkeypatch.setattr(
        intent_chains,
        "get_chat_model",
        lambda **kwargs: FakeChatOpenAI(classifier_calls),
    )
    monkeypatch.setattr(
        media_chains,
        "get_chat_model",
        lambda **kwargs: FakeChatOpenAI(media_classifier_calls),
    )
    monkeypatch.setattr(
        response_chains,
        "get_chat_model",
        lambda **kwargs: FakeChatOpenAI(responder_calls),
    )

    settings = RuntimeSettings(openai_model="gpt-5.6-luna", openai_reasoning_effort="medium")
    intent_chains.build_classifier_chain(settings)
    media_chains.build_outbound_media_classifier_chain(settings)
    response_chains.build_responder_chain(settings)

    assert classifier_calls == [(OpenAIIntentClassification, "json_schema")]
    assert media_classifier_calls == [(OpenAIOutboundMediaClassification, "json_schema")]
    assert responder_calls == [(OpenAIAgentResponsePlan, "json_schema")]
    get_settings.cache_clear()


def test_media_classifier_chain_uses_application_schema_without_json_schema(monkeypatch) -> None:
    calls = []

    class FakeChatOpenAI:
        def with_structured_output(self, schema, *, method):
            calls.append((schema, method))
            return RunnableLambda(lambda value: value)

    monkeypatch.setattr(media_chains, "get_chat_model", lambda **kwargs: FakeChatOpenAI())

    media_chains.build_outbound_media_classifier_chain(RuntimeSettings(openai_model="gpt-4.1-mini"))

    assert calls == [(OutboundMediaClassification, "function_calling")]


def test_response_node_builds_media_classifier_with_runtime_settings(monkeypatch) -> None:
    settings = RuntimeSettings(openai_model="gpt-4.1-mini")
    captured_calls = []

    monkeypatch.setattr(
        response_nodes,
        "build_outbound_media_classifier_chain",
        lambda provided_settings, **kwargs: captured_calls.append((provided_settings, kwargs)),
    )
    token = response_nodes._RUNTIME_SETTINGS.set(settings)
    try:
        response_nodes._build_outbound_media_classifier_chain(use_custom_temperature=False)
    finally:
        response_nodes._RUNTIME_SETTINGS.reset(token)

    assert captured_calls == [(settings, {"use_custom_temperature": False})]


def test_non_gpt5_models_keep_custom_temperature(monkeypatch) -> None:
    captured_kwargs = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm_chains, "ChatOpenAI", FakeChatOpenAI)
    get_settings.cache_clear()

    llm_chains.get_chat_model(
        RuntimeSettings(openai_model="gpt-4.1-mini"),
        temperature=0.3,
    )

    assert captured_kwargs == {
        "model": "gpt-4.1-mini",
        "api_key": "sk-test",
        "temperature": 0.3,
    }
    get_settings.cache_clear()
