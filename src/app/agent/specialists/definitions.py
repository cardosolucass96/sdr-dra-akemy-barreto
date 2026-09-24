from __future__ import annotations

from app.agent.specialists.contracts import SpecialistResult
from app.agent.specialists.registry import SpecialistDefinition, SpecialistRegistry
from app.core.config import Settings

TEST_SPECIALIST_NAME = "test_specialist"
TEST_SPECIALIST_ALIASES = {
    "deep_agent",
    "deep_agent_test",
    "specialist",
    "specialist_test",
    "test",
    "testing_deep_agent",
    "testing/deep_agent",
}
TEST_SPECIALIST_OBJECTIVE = (
    "Analyze the latest user message and return concise internal work product for the responder."
)


def build_test_specialist_agent(settings: Settings):
    from agents import Agent, AgentOutputSchema

    model = (settings.openai_specialist_model or "").strip() or settings.openai_model
    return Agent(
        name="Pipefacil test specialist",
        instructions=(
            "You are an internal specialist worker for a LangGraph responder.\n"
            "Do not write the final user-facing reply.\n"
            "Return only structured work product for the responder: a short summary, "
            "response guidance, extracted fields, and confidence.\n"
            "Never include secrets, raw media, URLs, base64, or private tool arguments."
        ),
        model=model,
        output_type=AgentOutputSchema(SpecialistResult, strict_json_schema=False),
    )


DEFAULT_SPECIALIST_REGISTRY = SpecialistRegistry(
    definitions=[
        SpecialistDefinition(
            name=TEST_SPECIALIST_NAME,
            objective=TEST_SPECIALIST_OBJECTIVE,
            agent_factory=build_test_specialist_agent,
        )
    ],
    aliases={alias: TEST_SPECIALIST_NAME for alias in TEST_SPECIALIST_ALIASES},
)
