"""Typed configuration with a strict boundary between secrets and runtime behavior."""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseModel):
    """Non-sensitive settings persisted in Postgres and editable from ``/settings``."""

    app_name: str = "SDR Agent Template"
    app_slug: str = Field(default="sdr-agent-template", pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    pipefacil_base_url: str = "https://pipefacil-server.matchsales.com.br"
    pipefacil_conversation_history_path: str = "/api/v1/messages"
    pipefacil_timeout_seconds: float = Field(default=20.0, gt=0)
    pipefacil_media_max_bytes: int = Field(default=25_000_000, ge=1)
    pipefacil_webhook_idempotency_ttl_seconds: int = Field(default=86_400, ge=0)
    pipefacil_max_tokens_per_lead: int = Field(default=0, ge=0)
    generated_audio_enabled: bool = False
    generated_audio_auto_enabled: bool = False
    generated_audio_auto_min_chars: int = Field(default=650, ge=1)
    generated_audio_max_chars: int = Field(default=1_200, ge=1)
    generated_audio_public_base_url: str | None = None
    generated_audio_ttl_seconds: int = Field(default=86_400, ge=60)
    generated_audio_auto_text: str = "Te mandei um audio para explicar melhor."
    generated_audio_convert_to_ogg_opus: bool = False
    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    elevenlabs_voice_id: str | None = None
    elevenlabs_model_id: str = "eleven_v3"
    elevenlabs_output_format: str = "opus_48000_96"
    elevenlabs_tts_cost_per_1k_chars_usd: float | None = Field(default=None, ge=0)
    elevenlabs_timeout_seconds: float = Field(default=30.0, gt=0)
    elevenlabs_max_attempts: int = Field(default=2, ge=1, le=2)
    elevenlabs_retry_backoff_seconds: float = Field(default=0.5, ge=0, le=5)
    elevenlabs_voice_stability: float | None = Field(default=0.45, ge=0, le=1)
    elevenlabs_voice_similarity_boost: float | None = Field(default=0.85, ge=0, le=1)
    elevenlabs_voice_style: float | None = Field(default=0.35, ge=0, le=1)
    elevenlabs_voice_use_speaker_boost: bool | None = True
    elevenlabs_voice_speed: float | None = Field(default=1.0, gt=0, le=2)
    langfuse_enabled: bool = True
    langfuse_base_url: str | None = None
    langfuse_tracing_environment: str | None = "production"
    langfuse_prompt_label: str | None = "production"
    langfuse_debug: bool = False
    langfuse_pipefacil_user_id_mode: Literal["contact_id", "contact_name_phone"] = "contact_id"
    log_level: str = "INFO"
    log_format: str | None = None
    log_inbound_payloads: bool = False
    openai_model: str = "gpt-5.6-luna"
    openai_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = "low"
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    openai_specialists_enabled: bool = False
    openai_specialist_model: str | None = None
    openai_specialist_max_turns: int = Field(default=8, ge=1)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_generated_audio(self) -> "RuntimeSettings":
        if (
            self.generated_audio_auto_enabled
            and self.generated_audio_auto_min_chars > self.generated_audio_max_chars
        ):
            raise ValueError(
                "GENERATED_AUDIO_AUTO_MIN_CHARS cannot exceed GENERATED_AUDIO_MAX_CHARS."
            )
        return self


class BootstrapSettings(BaseSettings):
    """Secrets and immutable process bootstrap inputs loaded only from the environment."""

    database_url: str | None = None
    openai_api_key: str | None = None
    pipefacil_api_key: str | None = None
    pipefacil_webhook_signature_secret: str | None = None
    elevenlabs_api_key: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    settings_admin_key_hash: str | None = None
    settings_session_secret: str | None = None

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", frozen=True)


class Settings(RuntimeSettings):
    """Flat execution view used by integrations and compatibility tests.

    The application builds this object from a runtime snapshot and ``BootstrapSettings``.
    Request handling uses the explicit runtime snapshot. Operational fields always use their
    typed defaults or a persisted snapshot; they are never read from the environment.
    """

    app_env: str = "production"
    app_version: str = "0.1.0"
    database_url: str | None = None
    langgraph_checkpoint_schema: str | None = None
    langgraph_checkpoint_pool_min_size: int = Field(default=1, ge=1)
    langgraph_checkpoint_pool_max_size: int = Field(default=10, ge=1)
    langgraph_checkpoint_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    cloudflare_tunnel_token: str | None = None
    cloudflare_tunnel_hostname: str | None = None
    cloudflare_tunnel_url: str | None = None
    cloudflare_tunnel_metrics: str | None = None
    cloudflare_tunnel_loglevel: str | None = None
    pipefacil_webhook_signature_enabled: bool = True
    pipefacil_api_key: str | None = None
    pipefacil_webhook_signature_secret: str | None = None
    outbound_media_catalog_path: str | None = None
    generated_audio_storage_dir: str = ".runtime/generated-audio"
    elevenlabs_api_key: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    openai_api_key: str | None = None
    settings_admin_key_hash: str | None = None
    settings_session_secret: str | None = None

    model_config = ConfigDict(extra="ignore", frozen=True)

    @model_validator(mode="after")
    def validate_checkpoint_pool(self) -> "Settings":
        if self.langgraph_checkpoint_pool_max_size < self.langgraph_checkpoint_pool_min_size:
            raise ValueError(
                "LANGGRAPH_CHECKPOINT_POOL_MAX_SIZE must be greater than or equal to "
                "LANGGRAPH_CHECKPOINT_POOL_MIN_SIZE."
            )
        return self


def build_execution_settings(
    bootstrap: BootstrapSettings,
    runtime: RuntimeSettings,
) -> Settings:
    """Compose a flat request-scoped settings value without persisting secrets."""

    return Settings.model_validate(
        {
            **runtime.model_dump(),
            **bootstrap.model_dump(),
        }
    )


def runtime_settings_from_execution(settings: Settings) -> RuntimeSettings:
    """Extract the non-sensitive portion of an execution snapshot."""

    return RuntimeSettings.model_validate(
        {name: getattr(settings, name) for name in RuntimeSettings.model_fields}
    )


@lru_cache
def get_bootstrap_settings() -> BootstrapSettings:
    return BootstrapSettings()


@lru_cache
def get_settings() -> Settings:
    """Compatibility default for isolated scripts and unit-level helpers."""

    return build_execution_settings(get_bootstrap_settings(), RuntimeSettings())
