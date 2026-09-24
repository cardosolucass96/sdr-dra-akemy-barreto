import operator
from typing import Annotated, Any, Literal, NotRequired

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

IntentType = Literal["greeting", "question", "request", "fallback"]


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]
    resume_context: NotRequired[str]
    latest_user_message: NotRequired[str]
    intent: NotRequired[IntentType]
    intent_reason: NotRequired[str]
    requires_specialist: NotRequired[bool]
    specialist_name: NotRequired[str | None]
    specialist_reason: NotRequired[str | None]
    specialist_status: NotRequired[str | None]
    specialist_result: NotRequired[dict[str, Any] | None]
    response_text: NotRequired[str]
    response_media: NotRequired[list[dict[str, Any]]]
    response_audio: NotRequired[dict[str, Any] | None]
    status: NotRequired[str]
    liana_explicit_interest: NotRequired[bool]
    liana_stated_objective: NotRequired[str | None]
    liana_appointment_requested: NotRequired[bool]
    liana_preferred_period: NotRequired[str | None]
    liana_progress_confidence: NotRequired[float]
    pipefacil_deal_seq: NotRequired[int | None]
    pipefacil_current_stage_key: NotRequired[str | None]
    pipefacil_sync_active: NotRequired[bool]
    pending_pipefacil_sync: NotRequired[dict[str, Any] | None]
    pipefacil_sync_result: NotRequired[dict[str, Any] | None]
