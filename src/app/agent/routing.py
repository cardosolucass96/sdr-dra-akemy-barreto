"""Node names and routing helpers for the agent graph."""

from typing import Literal

from app.agent.state import AgentState

CLASSIFY_INTENT_NODE = "classify-intent"
DELEGATE_SPECIALIST_NODE = "delegate-specialist"
RESPOND_NODE = "respond"
INTERPRET_LIANA_PROGRESS_NODE = "interpret-liana-progress"
PLAN_LIANA_SYNC_NODE = "plan-liana-pipefacil-sync"
SYNC_LIANA_DEAL_NODE = "sync-liana-pipefacil-deal"


def route_after_intent(
    state: AgentState,
) -> Literal["delegate-specialist", "interpret-liana-progress"]:
    if state.get("requires_specialist") and state.get("specialist_name"):
        return DELEGATE_SPECIALIST_NODE
    return INTERPRET_LIANA_PROGRESS_NODE


def route_after_liana_plan(state: AgentState) -> Literal["sync-liana-pipefacil-deal", "respond"]:
    if state.get("pending_pipefacil_sync"):
        return SYNC_LIANA_DEAL_NODE
    return RESPOND_NODE


__all__ = [
    "CLASSIFY_INTENT_NODE",
    "DELEGATE_SPECIALIST_NODE",
    "INTERPRET_LIANA_PROGRESS_NODE",
    "PLAN_LIANA_SYNC_NODE",
    "RESPOND_NODE",
    "SYNC_LIANA_DEAL_NODE",
    "route_after_intent",
    "route_after_liana_plan",
]
