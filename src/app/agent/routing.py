"""Node names and routing helpers for the agent graph."""

from typing import Literal

from app.agent.state import AgentState

CLASSIFY_INTENT_NODE = "classify-intent"
DELEGATE_SPECIALIST_NODE = "delegate-specialist"
RESPOND_NODE = "respond"


def route_after_intent(state: AgentState) -> Literal["delegate-specialist", "respond"]:
    if state.get("requires_specialist") and state.get("specialist_name"):
        return DELEGATE_SPECIALIST_NODE
    return RESPOND_NODE


__all__ = [
    "CLASSIFY_INTENT_NODE",
    "DELEGATE_SPECIALIST_NODE",
    "RESPOND_NODE",
    "route_after_intent",
]
