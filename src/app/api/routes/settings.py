"""Small server-rendered control panel for non-sensitive SDR settings."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.api.settings_security import create_csrf_token, verify_settings_access_key
from app.application.runtime_settings import (
    RuntimeSettingsConflictError,
    RuntimeSettingsValidationError,
)
from app.core.config import RuntimeSettings

settings_router = APIRouter(prefix="/settings", include_in_schema=False)
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    help: str = ""
    wide: bool = False


SECTIONS = (
    (
        "SDR e modelo",
        (
            FieldSpec("app_name", "Nome do SDR"),
            FieldSpec("app_slug", "Identificador do SDR"),
            FieldSpec("openai_model", "Modelo OpenAI"),
            FieldSpec("openai_reasoning_effort", "Esforço de raciocínio"),
            FieldSpec("openai_transcription_model", "Modelo de transcrição"),
            FieldSpec("openai_specialists_enabled", "Habilitar especialistas"),
            FieldSpec("openai_specialist_model", "Modelo do especialista"),
            FieldSpec("openai_specialist_max_turns", "Máximo de turnos do especialista"),
        ),
    ),
    (
        "Pipefacil e limites",
        (
            FieldSpec("pipefacil_base_url", "URL base do Pipefacil", wide=True),
            FieldSpec("pipefacil_conversation_history_path", "Caminho do histórico"),
            FieldSpec("pipefacil_timeout_seconds", "Timeout do Pipefacil (segundos)"),
            FieldSpec("pipefacil_media_max_bytes", "Tamanho máximo de mídia (bytes)"),
            FieldSpec(
                "pipefacil_webhook_idempotency_ttl_seconds",
                "TTL de idempotência (segundos)",
            ),
            FieldSpec(
                "pipefacil_max_tokens_per_lead",
                "Limite de tokens por lead",
                "0 desativa o limite.",
            ),
            FieldSpec("log_inbound_payloads", "Registrar payloads de entrada"),
        ),
    ),
    (
        "Áudio",
        (
            FieldSpec("generated_audio_enabled", "Habilitar áudio gerado"),
            FieldSpec("generated_audio_auto_enabled", "Gerar áudio automaticamente"),
            FieldSpec("generated_audio_auto_min_chars", "Mínimo de caracteres para áudio"),
            FieldSpec("generated_audio_max_chars", "Máximo de caracteres por áudio"),
            FieldSpec("generated_audio_public_base_url", "URL pública para áudio", wide=True),
            FieldSpec("generated_audio_ttl_seconds", "TTL do áudio (segundos)"),
            FieldSpec("generated_audio_auto_text", "Texto de acompanhamento do áudio", wide=True),
            FieldSpec("generated_audio_convert_to_ogg_opus", "Converter áudio para OGG/Opus"),
            FieldSpec("elevenlabs_base_url", "URL base do ElevenLabs", wide=True),
            FieldSpec("elevenlabs_voice_id", "ID da voz"),
            FieldSpec("elevenlabs_model_id", "Modelo ElevenLabs"),
            FieldSpec("elevenlabs_output_format", "Formato de saída"),
            FieldSpec("elevenlabs_tts_cost_per_1k_chars_usd", "Custo por mil caracteres (USD)"),
            FieldSpec("elevenlabs_timeout_seconds", "Timeout do ElevenLabs (segundos)"),
            FieldSpec("elevenlabs_max_attempts", "Tentativas ElevenLabs"),
            FieldSpec("elevenlabs_retry_backoff_seconds", "Espera entre tentativas (segundos)"),
            FieldSpec("elevenlabs_voice_stability", "Estabilidade da voz"),
            FieldSpec("elevenlabs_voice_similarity_boost", "Similaridade da voz"),
            FieldSpec("elevenlabs_voice_style", "Estilo da voz"),
            FieldSpec("elevenlabs_voice_use_speaker_boost", "Usar speaker boost"),
            FieldSpec("elevenlabs_voice_speed", "Velocidade da voz"),
        ),
    ),
    (
        "Observabilidade e logs",
        (
            FieldSpec("langfuse_enabled", "Habilitar Langfuse"),
            FieldSpec("langfuse_base_url", "URL base do Langfuse", wide=True),
            FieldSpec("langfuse_tracing_environment", "Ambiente de tracing"),
            FieldSpec("langfuse_prompt_label", "Label de prompts"),
            FieldSpec("langfuse_debug", "Debug do Langfuse"),
            FieldSpec("langfuse_pipefacil_user_id_mode", "Identificador do usuário no Langfuse"),
            FieldSpec("log_level", "Nível de log"),
            FieldSpec("log_format", "Formato de log"),
        ),
    ),
)

BOOLEAN_FIELDS = {
    name for name, field in RuntimeSettings.model_fields.items() if field.annotation is bool
}
INTEGER_FIELDS = {
    "pipefacil_media_max_bytes",
    "pipefacil_webhook_idempotency_ttl_seconds",
    "pipefacil_max_tokens_per_lead",
    "generated_audio_auto_min_chars",
    "generated_audio_max_chars",
    "generated_audio_ttl_seconds",
    "elevenlabs_max_attempts",
    "openai_specialist_max_turns",
}
NUMBER_FIELDS = INTEGER_FIELDS | {
    "pipefacil_timeout_seconds",
    "elevenlabs_tts_cost_per_1k_chars_usd",
    "elevenlabs_timeout_seconds",
    "elevenlabs_retry_backoff_seconds",
    "elevenlabs_voice_stability",
    "elevenlabs_voice_similarity_boost",
    "elevenlabs_voice_style",
    "elevenlabs_voice_speed",
}
SELECT_OPTIONS = {
    "openai_reasoning_effort": ("none", "low", "medium", "high", "xhigh", "max"),
    "langfuse_pipefacil_user_id_mode": ("contact_id", "contact_name_phone"),
    "log_level": ("DEBUG", "INFO", "WARNING", "ERROR"),
    "log_format": ("text", "json"),
}
TEXTAREA_FIELDS = {"generated_audio_auto_text"}


@settings_router.get("/login", response_class=HTMLResponse)
def settings_login(request: Request) -> Response:
    if request.session.get("settings_authenticated"):
        return _redirect("/settings")
    return _login_response(request)


@settings_router.post("/login", response_class=HTMLResponse)
async def authenticate_settings(request: Request) -> Response:
    form = await request.form()
    if not _valid_csrf(request, form.get("csrf_token")):
        return _login_response(
            request,
            error="Sessão expirada. Tente novamente.",
            code=status.HTTP_403_FORBIDDEN,
        )

    access_key = str(form.get("access_key") or "")
    bootstrap = request.app.state.bootstrap_settings
    if not verify_settings_access_key(access_key, bootstrap.settings_admin_key_hash):
        return _login_response(
            request,
            error="Chave de acesso inválida.",
            code=status.HTTP_401_UNAUTHORIZED,
        )

    request.session.clear()
    request.session["settings_authenticated"] = True
    request.session["csrf_token"] = create_csrf_token()
    return _redirect("/settings")


@settings_router.post("/logout")
async def logout_settings(request: Request) -> Response:
    if not request.session.get("settings_authenticated"):
        return _redirect("/settings/login")
    form = await request.form()
    if _valid_csrf(request, form.get("csrf_token")):
        request.session.clear()
    return _redirect("/settings/login")


@settings_router.get("", response_class=HTMLResponse)
def settings_page(request: Request) -> Response:
    if not request.session.get("settings_authenticated"):
        return _redirect("/settings/login")
    snapshot = request.app.state.runtime_settings_service.get_snapshot()
    return _settings_response(request, snapshot.settings, version=snapshot.version)


@settings_router.post("", response_class=HTMLResponse)
async def save_settings(request: Request) -> Response:
    if not request.session.get("settings_authenticated"):
        return _redirect("/settings/login")
    form = await request.form()
    if not _valid_csrf(request, form.get("csrf_token")):
        return _settings_response(
            request,
            request.app.state.runtime_settings_service.get_snapshot().settings,
            version=request.app.state.runtime_settings_service.get_snapshot().version,
            error="Sessão expirada. Recarregue a página antes de salvar.",
            code=status.HTTP_403_FORBIDDEN,
        )

    try:
        version = int(str(form.get("version") or ""))
        settings = RuntimeSettings.model_validate(_settings_payload(form))
    except (TypeError, ValueError, ValidationError) as exc:
        snapshot = request.app.state.runtime_settings_service.get_snapshot()
        return _settings_response(
            request,
            snapshot.settings,
            version=snapshot.version,
            error=_validation_message(exc),
            code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    snapshot = request.app.state.runtime_settings_service.get_snapshot()
    try:
        saved = request.app.state.runtime_settings_service.update_snapshot(
            type(snapshot)(settings=settings, version=version, updated_at=snapshot.updated_at),
            expected_version=version,
        )
    except RuntimeSettingsValidationError as exc:
        snapshot = request.app.state.runtime_settings_service.get_snapshot()
        return _settings_response(
            request,
            snapshot.settings,
            version=snapshot.version,
            error=str(exc),
            code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except RuntimeSettingsConflictError:
        latest = request.app.state.runtime_settings_service.get_snapshot()
        return _settings_response(
            request,
            latest.settings,
            version=latest.version,
            error="As configurações foram alteradas em outra sessão. Revise e salve novamente.",
            code=status.HTTP_409_CONFLICT,
        )
    return _settings_response(
        request,
        saved.settings,
        version=saved.version,
        notice="Configurações salvas. Elas serão usadas na próxima mensagem.",
    )


def _settings_payload(form: Any) -> dict[str, object]:
    payload: dict[str, object] = {}
    for name in RuntimeSettings.model_fields:
        if name in BOOLEAN_FIELDS:
            payload[name] = name in form
            continue
        value = form.get(name)
        payload[name] = None if value == "" else value
    return payload


def _login_response(
    request: Request,
    *,
    error: str | None = None,
    code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    request.session.setdefault("csrf_token", create_csrf_token())
    return _template_response(
        "settings_login.html",
        request,
        {"csrf_token": request.session["csrf_token"], "error": error},
        code=code,
    )


def _settings_response(
    request: Request,
    settings: RuntimeSettings,
    *,
    version: int,
    error: str | None = None,
    notice: str | None = None,
    code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    request.session.setdefault("csrf_token", create_csrf_token())
    return _template_response(
        "settings.html",
        request,
        {
            "csrf_token": request.session["csrf_token"],
            "version": version,
            "sections": _sections_for(settings),
            "error": error,
            "notice": notice,
        },
        code=code,
    )


def _sections_for(settings: RuntimeSettings) -> list[dict[str, object]]:
    values = settings.model_dump()
    return [
        {
            "title": title,
            "fields": [
                {
                    "name": field.name,
                    "label": field.label,
                    "help": field.help,
                    "wide": field.wide,
                    "value": values[field.name],
                    "kind": _field_kind(field.name),
                    "options": SELECT_OPTIONS.get(field.name, ()),
                    "step": _field_step(field.name),
                }
                for field in fields
            ],
        }
        for title, fields in SECTIONS
    ]


def _field_kind(name: str) -> str:
    if name in BOOLEAN_FIELDS:
        return "boolean"
    if name in SELECT_OPTIONS:
        return "select"
    if name in TEXTAREA_FIELDS:
        return "textarea"
    if name in NUMBER_FIELDS:
        return "number"
    return "text"


def _field_step(name: str) -> str | None:
    if name in INTEGER_FIELDS:
        return "1"
    if name in NUMBER_FIELDS:
        return "any"
    return None


def _valid_csrf(request: Request, provided: object) -> bool:
    expected = request.session.get("csrf_token")
    return (
        isinstance(expected, str)
        and isinstance(provided, str)
        and hmac.compare_digest(expected, provided)
    )


def _validation_message(error: Exception) -> str:
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        field = ".".join(str(item) for item in first.get("loc", ()))
        return f"Valor inválido em {field}: {first.get('msg', 'verifique o campo.')}"
    return "Versão inválida. Recarregue a página e tente novamente."


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def _template_response(
    template: str,
    request: Request,
    context: dict[str, object],
    *,
    code: int,
) -> HTMLResponse:
    response = TEMPLATES.TemplateResponse(
        request,
        template,
        context,
        status_code=code,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
