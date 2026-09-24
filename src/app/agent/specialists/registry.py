from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings

SpecialistAgentFactory = Callable[[Settings], Any]


@dataclass(frozen=True, slots=True)
class SpecialistDefinition:
    name: str
    objective: str
    agent_factory: SpecialistAgentFactory


class SpecialistRegistry:
    def __init__(
        self,
        definitions: list[SpecialistDefinition] | None = None,
        aliases: dict[str, str] | None = None,
    ) -> None:
        self._definitions = {definition.name: definition for definition in definitions or []}
        self._aliases = {
            _normalize_specialist_name(alias): canonical_name
            for alias, canonical_name in (aliases or {}).items()
        }

    def get(self, name: str) -> SpecialistDefinition | None:
        return self._definitions.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def resolve_name(self, name: str | None) -> str | None:
        normalized_name = _normalize_specialist_name(name)
        if not normalized_name:
            return None
        if normalized_name in self._definitions:
            return normalized_name
        if normalized_name in self._aliases:
            canonical_name = self._aliases[normalized_name]
            if canonical_name in self._definitions:
                return canonical_name

        for candidate in self._definitions:
            if normalized_name == _normalize_specialist_name(candidate):
                return candidate

        return None


def _normalize_specialist_name(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
