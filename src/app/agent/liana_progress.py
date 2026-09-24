from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agent.context import AgentRunContext
from app.agent.state import AgentState

QUALIFIED_STAGE_KEYS = frozenset({"qualification", "appointment"})
AUTOMATABLE_STAGE_KEYS = frozenset({"entry", *QUALIFIED_STAGE_KEYS})


def build_liana_sync_request(state: AgentState) -> dict[str, Any] | None:
    deal_seq = state.get("pipefacil_deal_seq")
    current_stage = state.get("pipefacil_current_stage_key")
    if not state.get("pipefacil_sync_active") or deal_seq is None:
        return None
    if current_stage not in AUTOMATABLE_STAGE_KEYS:
        return None

    has_qualification = bool(state.get("liana_explicit_interest")) and bool(
        state.get("liana_stated_objective")
    )
    appointment_requested = state.get("liana_appointment_requested") or bool(
        state.get("liana_preferred_period")
    )
    target_stage = _target_stage(current_stage, has_qualification, appointment_requested)
    if target_stage is None:
        return None

    return {
        "operation_id": f"liana:{deal_seq}:{target_stage}",
        "deal_seq": deal_seq,
        "target_stage_key": target_stage,
        "source_stage_key": current_stage,
    }


def build_liana_progress_state_update(
    state: AgentState,
    context: AgentRunContext,
    interpret: Callable[[str], Any],
) -> dict[str, Any]:
    stage_key = context.pipefacil_stage_key
    active = bool(
        context.pipefacil_deal_seq is not None
        and stage_key in AUTOMATABLE_STAGE_KEYS
        and context.pipefacil_sync_enqueue is not None
        and context.pipefacil_sync_handler is not None
    )
    update: dict[str, Any] = {
        "pipefacil_deal_seq": context.pipefacil_deal_seq,
        "pipefacil_current_stage_key": stage_key,
        "pipefacil_sync_active": active,
    }
    message = state.get("latest_user_message")
    if not active or not message:
        return update
    return update | _merge_liana_progress_result(state, interpret(message))


def _merge_liana_progress_result(state: AgentState, result: Any) -> dict[str, Any]:
    accepted = result.confidence >= 0.75
    return {
        "liana_explicit_interest": state.get("liana_explicit_interest", False)
        or (accepted and result.explicit_interest),
        "liana_stated_objective": (
            (result.stated_objective if accepted else None) or state.get("liana_stated_objective")
        ),
        "liana_appointment_requested": state.get("liana_appointment_requested", False)
        or (accepted and result.appointment_requested),
        "liana_preferred_period": (
            (result.preferred_period if accepted else None) or state.get("liana_preferred_period")
        ),
        "liana_progress_confidence": result.confidence,
    }


def _target_stage(
    current_stage: str,
    has_qualification: bool,
    appointment_requested: bool,
) -> str | None:
    if current_stage == "appointment":
        return "appointment"
    if appointment_requested:
        return "appointment"
    if current_stage == "qualification" or has_qualification:
        return "qualification"
    return None


__all__ = ["build_liana_progress_state_update", "build_liana_sync_request"]
