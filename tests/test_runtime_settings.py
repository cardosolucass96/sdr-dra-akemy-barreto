from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.agent.chains.llm as llm_chains
from app.agent.context import execution_settings_from_runtime
from app.application.runtime_settings import (
    InMemoryRuntimeSettingsStore,
    RuntimeSettingsConflictError,
    RuntimeSettingsService,
    RuntimeSettingsSnapshot,
    RuntimeSettingsValidationError,
    validate_runtime_settings,
)
from app.core import RuntimeConfigurationError
from app.core.config import BootstrapSettings, RuntimeSettings, Settings
from app.main import (
    _validate_generated_audio_settings,
    _validate_production_settings,
    _validate_settings_access,
)


def test_runtime_settings_service_builds_execution_settings_from_snapshot() -> None:
    store = InMemoryRuntimeSettingsStore()
    service = RuntimeSettingsService(
        store,
        BootstrapSettings(openai_api_key="sk-test", pipefacil_api_key="pf-test"),
        app_env="staging",
        app_version="2026.9.1",
    )
    initial = service.get_snapshot()

    saved = service.update_snapshot(
        RuntimeSettingsSnapshot(
            settings=RuntimeSettings(
                app_name="SDR de teste",
                openai_model="gpt-4.1-mini",
            ),
            version=initial.version,
            updated_at=initial.updated_at,
        ),
        expected_version=initial.version,
    )
    execution = service.get_execution_settings()

    assert saved.version == 2
    assert execution.app_name == "SDR de teste"
    assert execution.openai_model == "gpt-4.1-mini"
    assert execution.openai_api_key == "sk-test"
    assert execution.pipefacil_api_key == "pf-test"
    assert execution.app_env == "staging"
    assert execution.app_version == "2026.9.1"


def test_in_memory_runtime_settings_store_rejects_stale_update() -> None:
    store = InMemoryRuntimeSettingsStore()
    initial = store.get()
    store.update(RuntimeSettings(app_name="SDR atualizado"), expected_version=initial.version)

    with pytest.raises(RuntimeSettingsConflictError):
        store.update(RuntimeSettings(), expected_version=initial.version)


def test_runtime_settings_service_rejects_invalid_audio_combination() -> None:
    store = InMemoryRuntimeSettingsStore()
    service = RuntimeSettingsService(store, BootstrapSettings())
    current = service.get_snapshot()

    with pytest.raises(RuntimeSettingsValidationError, match="áudio automático"):
        service.update_snapshot(
            RuntimeSettingsSnapshot(
                settings=RuntimeSettings(generated_audio_auto_enabled=True),
                version=current.version,
                updated_at=current.updated_at,
            ),
            expected_version=current.version,
        )


@pytest.mark.parametrize(
    ("settings", "message"),
    (
        (RuntimeSettings(generated_audio_enabled=True), "voz configurada"),
        (RuntimeSettings(generated_audio_enabled=True, elevenlabs_voice_id="voice"), "URL pública"),
        (
            RuntimeSettings(
                generated_audio_enabled=True,
                elevenlabs_voice_id="voice",
                generated_audio_public_base_url="https://",
            ),
            "URL pública",
        ),
    ),
)
def test_runtime_settings_validation_rejects_invalid_generated_audio_configuration(
    settings: RuntimeSettings,
    message: str,
) -> None:
    with pytest.raises(RuntimeSettingsValidationError, match=message):
        validate_runtime_settings(settings)


def test_runtime_settings_validation_accepts_complete_generated_audio_configuration() -> None:
    validate_runtime_settings(
        RuntimeSettings(
            generated_audio_enabled=True,
            elevenlabs_voice_id="voice",
            generated_audio_public_base_url="https://audio.example.test",
        )
    )


def test_runtime_snapshot_carries_its_update_timestamp() -> None:
    updated_at = datetime.now(UTC)
    snapshot = RuntimeSettingsSnapshot(
        settings=RuntimeSettings(),
        version=7,
        updated_at=updated_at,
    )

    assert snapshot.version == 7
    assert snapshot.updated_at is updated_at


def _production_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "pipefacil_api_key": "pf-test",
        "pipefacil_webhook_signature_enabled": True,
        "pipefacil_webhook_signature_secret": "signature-secret",
        "database_url": "postgresql://user:password@db.test:5432/sdr",
        "openai_api_key": "sk-test",
    }
    values.update(changes)
    return Settings(**values)


def test_production_validation_covers_each_required_bootstrap_value() -> None:
    _validate_production_settings(Settings(app_env="development"))
    _validate_production_settings(_production_settings())

    failures = (
        ({"pipefacil_api_key": ""}, "PIPEFACIL_API_KEY"),
        ({"pipefacil_webhook_signature_enabled": False}, "must remain enabled"),
        ({"pipefacil_webhook_signature_secret": ""}, "PIPEFACIL_WEBHOOK_SIGNATURE_SECRET"),
        ({"database_url": ""}, "DATABASE_URL"),
        ({"openai_api_key": ""}, "OPENAI_API_KEY"),
    )
    for changes, message in failures:
        with pytest.raises(RuntimeConfigurationError, match=message):
            _validate_production_settings(_production_settings(**changes))


def test_settings_access_and_generated_audio_validation_cover_valid_and_invalid_paths() -> None:
    with pytest.raises(RuntimeConfigurationError, match="SETTINGS_ADMIN_KEY_HASH"):
        _validate_settings_access(Settings())
    with pytest.raises(RuntimeConfigurationError, match="SETTINGS_SESSION_SECRET"):
        _validate_settings_access(Settings(settings_admin_key_hash="scrypt$test"))
    _validate_settings_access(
        Settings(
            settings_admin_key_hash="scrypt$test",
            settings_session_secret="session-secret",
        )
    )

    _validate_generated_audio_settings(Settings())
    with pytest.raises(RuntimeConfigurationError, match="ELEVENLABS_API_KEY"):
        _validate_generated_audio_settings(Settings(generated_audio_enabled=True))
    with pytest.raises(RuntimeConfigurationError, match="ELEVENLABS_VOICE_ID"):
        _validate_generated_audio_settings(
            Settings(generated_audio_enabled=True, elevenlabs_api_key="eleven-test")
        )
    with pytest.raises(RuntimeConfigurationError, match="GENERATED_AUDIO_PUBLIC_BASE_URL"):
        _validate_generated_audio_settings(
            Settings(
                generated_audio_enabled=True,
                elevenlabs_api_key="eleven-test",
                elevenlabs_voice_id="voice-test",
            )
        )
    with pytest.raises(RuntimeConfigurationError, match="absolute HTTPS"):
        _validate_generated_audio_settings(
            Settings(
                generated_audio_enabled=True,
                elevenlabs_api_key="eleven-test",
                elevenlabs_voice_id="voice-test",
                generated_audio_public_base_url="http://audio.test",
            )
        )
    _validate_generated_audio_settings(
        Settings(
            generated_audio_enabled=True,
            elevenlabs_api_key="eleven-test",
            elevenlabs_voice_id="voice-test",
            cloudflare_tunnel_hostname="audio.test",
        )
    )


def test_legacy_agent_helpers_handle_an_empty_runtime_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(llm_chains, "ChatOpenAI", FakeChatOpenAI)

    assert (
        execution_settings_from_runtime(SimpleNamespace(context=None)).app_name
        == "SDR Agent Template"
    )
    llm_chains.get_chat_model(
        settings=RuntimeSettings(openai_model="gpt-4.1-mini"),
        temperature=None,
    )

    assert captured == {"model": "gpt-4.1-mini"}
