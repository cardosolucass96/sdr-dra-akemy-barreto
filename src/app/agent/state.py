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
