import importlib
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import app.agent.nodes.intent as intent_nodes
import app.agent.nodes.response as response_nodes
from app.agent import run_agent
from app.agent.chains.schemas import IntentClassification
from app.core.config import get_settings
from app.observability import reset_langfuse_clients
from app.outbound_media import OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT

graph_module = importlib.import_module("app.agent.graph")


@pytest.fixture(autouse=True)
def no_outbound_media(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        response_nodes,
        "_get_available_media_prompt_view",
        lambda: OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT,
    )


def test_langgraph_config_points_to_graph() -> None:
    config = json.loads(Path("langgraph.json").read_text())

    assert config["graphs"]["sdr_pipefacil"] == "./src/app/agent/agent.py:graph"
    assert config["env"] == "./.env"


def test_langgraph_scaffold_graph_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    reset_langfuse_clients()

    class FakeClassifierChain:
        def invoke(
            self,
            payload: dict[str, str],
            config: object | None = None,
        ) -> IntentClassification:
            assert payload["latest_user_message"] == "teste de estrutura"
            return IntentClassification(
                intent="question",
                reason="Mensagem curta tratada como pergunta neutra.",
            )

    class FakeResponderChain:
        def invoke(self, payload: dict[str, object], config: object | None = None) -> AIMessage:
            assert payload["intent"] == "question"
            assert payload["conversation_history"][0].content == "teste de estrutura"
            return AIMessage(content="Resposta de scaffold.")

    monkeypatch.setattr(intent_nodes, "_build_classifier_chain", lambda: FakeClassifierChain())
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())
    result = run_agent({"messages": [HumanMessage(content="teste de estrutura")]})

    assert result["status"] == "responded"
    assert result["latest_user_message"] == "teste de estrutura"
    assert result["intent"] == "question"
    assert result["intent_reason"] == "Mensagem curta tratada como pergunta neutra."
    assert result["response_text"] == "Resposta de scaffold."
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content == "Resposta de scaffold."
    reset_langfuse_clients()
    get_settings.cache_clear()


def test_langgraph_can_route_through_specialist_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    reset_langfuse_clients()

    class FakeClassifierChain:
        def invoke(
            self,
            payload: dict[str, str],
            config: object | None = None,
        ) -> IntentClassification:
            return IntentClassification(
                intent="request",
                reason="Pedido explicito de especialista.",
                requires_specialist=True,
                specialist_name="test_specialist",
                specialist_reason="Rodar especialista de teste.",
            )

    class FakeResponderChain:
        def invoke(self, payload: dict[str, object], config: object | None = None) -> AIMessage:
            assert payload["specialist_result"]["summary"] == "Resumo especialista."
            return AIMessage(content="Resposta depois do especialista.")

    def fake_delegate_specialist(state, config=None):
        assert state["requires_specialist"] is True
        return {
            "specialist_status": "completed",
            "specialist_result": {
                "status": "completed",
                "summary": "Resumo especialista.",
                "response_guidance": "Responder com base no resumo.",
                "extracted_fields": {},
                "confidence": 0.8,
                "error_code": None,
            },
        }

    monkeypatch.setattr(intent_nodes, "_build_classifier_chain", lambda: FakeClassifierChain())
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())
    monkeypatch.setattr(graph_module, "delegate_specialist", fake_delegate_specialist)

    graph = graph_module.build_graph()
    result = run_agent(
        {"messages": [HumanMessage(content="preciso de um especialista aqui")]},
        graph=graph,
    )

    assert result["specialist_status"] == "completed"
    assert result["response_text"] == "Resposta depois do especialista."
    reset_langfuse_clients()
    get_settings.cache_clear()


def test_run_agent_supports_protocol_dict_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    reset_langfuse_clients()

    class FakeClassifierChain:
        def invoke(
            self,
            payload: dict[str, str],
            config: object | None = None,
        ) -> IntentClassification:
            assert payload["latest_user_message"] == "Oi via protocolo"
            return IntentClassification(
                intent="greeting",
                reason="Mensagem vinda do protocolo local.",
            )

    class FakeResponderChain:
        def invoke(self, payload: dict[str, object], config: object | None = None) -> AIMessage:
            assert payload["conversation_history"][0]["content"] == "Oi via protocolo"
            return AIMessage(content="Resposta compatível com protocolo.")

    monkeypatch.setattr(intent_nodes, "_build_classifier_chain", lambda: FakeClassifierChain())
    monkeypatch.setattr(response_nodes, "_build_responder_chain", lambda: FakeResponderChain())

    result = run_agent({"messages": [{"type": "human", "content": "Oi via protocolo"}]})

    assert result["status"] == "responded"
    assert result["intent"] == "greeting"
    assert result["response_text"] == "Resposta compatível com protocolo."
    reset_langfuse_clients()
    get_settings.cache_clear()
