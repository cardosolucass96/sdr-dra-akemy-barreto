"""Application service for request-scoped runtime configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

from app.core.config import BootstrapSettings, RuntimeSettings, Settings, build_execution_settings
from app.integrations.postgres_runtime_settings import (
    PostgresRuntimeSettingsStore,
    RuntimeSettingsConflictError,
    RuntimeSettingsSnapshot,
)


class RuntimeSettingsValidationError(ValueError):
    """Raised when a non-secret runtime combination cannot be executed safely."""


def validate_runtime_settings(settings: RuntimeSettings) -> None:
    """Reject cross-field combinations before they reach the runtime settings store."""

    if settings.generated_audio_auto_enabled and not settings.generated_audio_enabled:
        raise RuntimeSettingsValidationError(
            "O áudio automático exige que o áudio gerado esteja habilitado."
        )
    if not settings.generated_audio_enabled:
        return

    if not (settings.elevenlabs_voice_id or "").strip():
        raise RuntimeSettingsValidationError(
            "Áudio gerado exige uma voz configurada antes de ser habilitado."
        )
    public_base_url = (settings.generated_audio_public_base_url or "").strip()
    parsed_url = urlparse(public_base_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise RuntimeSettingsValidationError(
            "Áudio gerado exige uma URL pública HTTPS antes de ser habilitado."
        )


class RuntimeSettingsService:
    """Loads a fresh, validated settings snapshot for every incoming request."""

    def __init__(
        self,
        store: PostgresRuntimeSettingsStore | InMemoryRuntimeSettingsStore,
        bootstrap: BootstrapSettings,
        *,
        app_env: str = "production",
        app_version: str = "0.1.0",
    ) -> None:
        self._store = store
        self._bootstrap = bootstrap
        self._app_env = app_env
        self._app_version = app_version

    def get_snapshot(self) -> RuntimeSettingsSnapshot:
        snapshot = self._store.get()
        validate_runtime_settings(snapshot.settings)
        return snapshot

    def get_execution_settings(self) -> Settings:
        snapshot = self.get_snapshot()
        return build_execution_settings(self._bootstrap, snapshot.settings).model_copy(
            update={"app_env": self._app_env, "app_version": self._app_version}
        )

    def update_snapshot(
        self,
        snapshot: RuntimeSettingsSnapshot,
        *,
        expected_version: int,
    ) -> RuntimeSettingsSnapshot:
        validate_runtime_settings(snapshot.settings)
        return self._store.update(snapshot.settings, expected_version=expected_version)


class InMemoryRuntimeSettingsStore:
    """Development fallback; production always uses the Postgres store."""

    def __init__(self) -> None:
        self._snapshot = RuntimeSettingsSnapshot(
            settings=RuntimeSettings(),
            version=1,
            updated_at=datetime.now(UTC),
        )

    def setup(self) -> None:
        return None

    def get(self) -> RuntimeSettingsSnapshot:
        return self._snapshot

    def update(
        self,
        settings: RuntimeSettings,
        *,
        expected_version: int,
    ) -> RuntimeSettingsSnapshot:
        if self._snapshot.version != expected_version:
            raise RuntimeSettingsConflictError("Runtime settings changed in another session.")
        self._snapshot = RuntimeSettingsSnapshot(
            settings=settings,
            version=expected_version + 1,
            updated_at=datetime.now(UTC),
        )
        return self._snapshot


__all__ = [
    "InMemoryRuntimeSettingsStore",
    "RuntimeSettingsConflictError",
    "RuntimeSettingsService",
    "RuntimeSettingsSnapshot",
    "RuntimeSettingsValidationError",
    "validate_runtime_settings",
]
