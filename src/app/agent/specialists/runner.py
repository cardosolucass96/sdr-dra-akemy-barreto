from __future__ import annotations

import json
from typing import Any

from app.agent.specialists.contracts import (
    SpecialistRequest,
    SpecialistResult,
    failed_specialist_result,
)
from app.agent.specialists.definitions import DEFAULT_SPECIALIST_REGISTRY
from app.agent.specialists.registry import SpecialistRegistry
from app.core.config import Settings


class OpenAISpecialistRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        registry: SpecialistRegistry = DEFAULT_SPECIALIST_REGISTRY,
    ) -> None:
        self._settings = settings
        self._registry = registry

    def run(self, *, specialist_name: str, request: SpecialistRequest) -> SpecialistResult:
        definition = self._registry.get(specialist_name)
        if definition is None:
            return failed_specialist_result("specialist_unknown")

        if not (self._settings.openai_api_key or "").strip():
            return failed_specialist_result("openai_api_key_missing")

        try:
            from agents import RunConfig, Runner
        except Exception:
            return failed_specialist_result("openai_agents_unavailable")

        try:
            agent = definition.agent_factory(self._settings)
            run_result = Runner.run_sync(
                agent,
                self._format_input(definition.objective, request),
                max_turns=self._settings.openai_specialist_max_turns,
                run_config=RunConfig(
                    trace_include_sensitive_data=False,
                    workflow_name=f"run-{self._settings.app_slug}-specialist",
                ),
            )
        except Exception as exc:
            return failed_specialist_result(self._error_code(exc), summary=str(exc))

        try:
            return self._coerce_result(run_result.final_output)
        except Exception as exc:
            return failed_specialist_result(
                "openai_specialist_output_invalid",
                summary=str(exc),
            )

    @staticmethod
    def _format_input(objective: str, request: SpecialistRequest) -> str:
        payload = request.model_dump(mode="json")
        payload["specialist_definition_objective"] = objective
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _coerce_result(final_output: Any) -> SpecialistResult:
        if isinstance(final_output, SpecialistResult):
            return final_output
        if isinstance(final_output, dict):
            return SpecialistResult.model_validate(final_output)

        text_output = str(final_output)
        return SpecialistResult(
            status="completed",
            summary=text_output,
            response_guidance=text_output,
        )

    @staticmethod
    def _error_code(exc: Exception) -> str:
        exception_name = exc.__class__.__name__
        if exception_name == "MaxTurnsExceeded":
            return "openai_specialist_max_turns_exceeded"
        if exception_name.endswith("TripwireTriggered"):
            return "openai_specialist_guardrail_triggered"
        if exception_name == "ModelBehaviorError":
            return "openai_specialist_model_behavior_error"
        if exception_name == "ToolTimeoutError":
            return "openai_specialist_tool_timeout"
        return "openai_specialist_error"
