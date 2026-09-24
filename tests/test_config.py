from __future__ import annotations

from app.core.config import (
    BootstrapSettings,
    RuntimeSettings,
    Settings,
    build_execution_settings,
)


def test_settings_provide_production_defaults_without_external_config_file(
    monkeypatch,
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    settings = Settings(_env_file=None)

    assert settings.app_name == "SDR Agent Template"
    assert settings.app_slug == "sdr-agent-template"
    assert settings.app_env == "production"
    assert settings.pipefacil_timeout_seconds == 20.0
    assert settings.pipefacil_media_max_bytes == 25_000_000
    assert settings.pipefacil_conversation_history_path == "/api/v1/messages"
    assert settings.generated_audio_enabled is False
    assert settings.generated_audio_storage_dir == ".runtime/generated-audio"
    assert settings.elevenlabs_voice_id is None
    assert settings.elevenlabs_output_format == "opus_48000_96"
    assert settings.openai_model == "gpt-5.6-luna"
    assert settings.openai_transcription_model == "gpt-4o-mini-transcribe"
    assert settings.openai_specialists_enabled is False


def test_settings_do_not_load_an_implicit_dotenv_file(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("APP_NAME=Hidden dotenv value\n")
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.app_name == "SDR Agent Template"


def test_runtime_settings_are_explicit_and_do_not_read_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "SDR Pipefacil Agent")
    monkeypatch.setenv("APP_SLUG", "sdr-pipefacil")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-br")
    monkeypatch.setenv("PIPEFACIL_MAX_TOKENS_PER_LEAD", "5000")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-custom")

    settings = RuntimeSettings()

    assert settings.app_name == "SDR Agent Template"
    assert settings.app_slug == "sdr-agent-template"
    assert settings.elevenlabs_voice_id is None
    assert settings.pipefacil_max_tokens_per_lead == 0
    assert settings.openai_model == "gpt-5.6-luna"


def test_bootstrap_settings_reads_only_secret_environment_values(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "not-used")

    bootstrap = BootstrapSettings()
    settings = Settings.model_validate(bootstrap.model_dump())

    assert bootstrap.openai_api_key == "sk-test"
    assert settings.openai_model == "gpt-5.6-luna"


def test_execution_settings_ignore_operational_environment_values(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "not-used")
    monkeypatch.setenv("APP_NAME", "not-used")

    settings = build_execution_settings(
        BootstrapSettings(openai_api_key="sk-test"),
        RuntimeSettings(openai_model="gpt-4.1-mini", app_name="SDR salvo"),
    )

    assert settings.openai_api_key == "sk-test"
    assert settings.openai_model == "gpt-4.1-mini"
    assert settings.app_name == "SDR salvo"
