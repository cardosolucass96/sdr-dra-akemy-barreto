from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.routes.generated_audio as generated_audio_route
import app.application.generated_audio as generated_audio_application
import app.integrations.elevenlabs.client as elevenlabs_client
import app.integrations.generated_audio.conversion as generated_audio_conversion
import app.main as main_module
from app.application.generated_audio import (
    GeneratedAudioError,
    GeneratedAudioFile,
    GeneratedAudioNotFoundError,
)
from app.core.config import Settings, get_bootstrap_settings, get_settings
from app.integrations.elevenlabs import (
    ElevenLabsGeneratedSpeech,
    ElevenLabsSpeechGenerationError,
    generate_elevenlabs_speech,
)
from app.integrations.generated_audio import (
    GeneratedAudioConversionError,
    GeneratedAudioStorageError,
    GeneratedAudioStoredFileNotFoundError,
    convert_audio_to_ogg_opus,
    resolve_stored_generated_audio_file,
    store_generated_audio,
)
from app.main import create_app


@pytest.fixture(autouse=True)
def clear_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("SETTINGS_ADMIN_KEY_HASH", "scrypt$test")
    monkeypatch.setenv("SETTINGS_SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(main_module, "APPLICATION_ENV", "development")
    get_bootstrap_settings.cache_clear()
    get_settings.cache_clear()
    yield
    get_bootstrap_settings.cache_clear()
    get_settings.cache_clear()


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "generated_audio_public_base_url": "https://agent.example.com",
        "generated_audio_convert_to_ogg_opus": False,
        "elevenlabs_output_format": "mp3_44100_128",
        "elevenlabs_api_key": "el-key",
        "elevenlabs_voice_id": "voice-br",
        "elevenlabs_model_id": "eleven_v3",
        "elevenlabs_max_attempts": 2,
        "elevenlabs_retry_backoff_seconds": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _audio_response(status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={"content-type": "audio/mpeg"},
        content=b"audio-bytes",
    )


def test_generate_elevenlabs_speech_posts_stream_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query"] = dict(request.url.params)
        captured["api_key"] = request.headers.get("xi-api-key")
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return _audio_response()

    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = generate_elevenlabs_speech(
            text="Oi, tudo bem?",
            settings=_settings(),
            client=client,
        )

    assert captured["path"] == "/v1/text-to-speech/voice-br/stream"
    assert captured["query"] == {"output_format": "mp3_44100_128"}
    assert captured["api_key"] == "el-key"
    assert captured["payload"]["text"] == "Oi, tudo bem?"
    assert captured["payload"]["model_id"] == "eleven_v3"
    assert result.content == b"audio-bytes"
    assert result.content_type == "audio/mpeg"
    assert result.attempt_count == 1


def test_generate_elevenlabs_speech_accepts_direct_ogg_opus() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["accept"] = request.headers.get("accept")
        return httpx.Response(
            200,
            headers={"content-type": "audio/ogg"},
            content=b"OggS-direct-opus",
        )

    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = generate_elevenlabs_speech(
            text="Audio direto",
            settings=_settings(elevenlabs_output_format="opus_48000_96"),
            client=client,
        )

    assert captured["accept"] == "audio/ogg"
    assert result.content_type == "audio/ogg"
    assert result.output_format == "opus_48000_96"


def test_generate_elevenlabs_speech_records_langfuse_generation_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeObservation:
        def update(self, **kwargs: object) -> None:
            captured["update"] = kwargs

    @contextmanager
    def fake_observe_span(**kwargs: object):
        captured["start"] = kwargs
        yield FakeObservation()

    monkeypatch.setattr(elevenlabs_client, "observe_span", fake_observe_span)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "audio/mpeg",
                "character-cost": "321",
                "request-id": "req-tts-123",
                "x-trace-id": "provider-trace-456",
            },
            content=b"audio-bytes",
        )

    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = generate_elevenlabs_speech(
            text="Oi, tudo bem?",
            settings=_settings(elevenlabs_tts_cost_per_1k_chars_usd=0.10),
            client=client,
        )

    assert captured["start"] == {
        "name": "generate-elevenlabs-speech",
        "as_type": "generation",
        "input": {"text": "Oi, tudo bem?", "text_length": 13},
        "metadata": {
            "provider": "elevenlabs",
            "voice_id": "voice-br",
            "output_format": "mp3_44100_128",
            "model_id": "eleven_v3",
        },
    }
    assert captured["update"] == {
        "model": "elevenlabs/eleven_v3",
        "model_parameters": {
            "voice_id": "voice-br",
            "output_format": "mp3_44100_128",
        },
        "output": {
            "content_type": "audio/mpeg",
            "audio_bytes": len(b"audio-bytes"),
        },
        "usage_details": {"characters": 321},
        "cost_details": {"characters": 0.0321},
        "metadata": {
            "provider": "elevenlabs",
            "voice_id": "voice-br",
            "output_format": "mp3_44100_128",
            "model_id": "eleven_v3",
            "attempt_count": 1,
            "request_id": "req-tts-123",
            "provider_trace_id": "provider-trace-456",
            "billed_characters": 321,
            "upstream_status_code": 200,
        },
    }
    assert result.billed_characters == 321
    assert result.request_id == "req-tts-123"
    assert result.provider_trace_id == "provider-trace-456"
    assert result.model_id == "eleven_v3"


def test_generate_elevenlabs_speech_retries_transport_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("temporary failure", request=request)
        return _audio_response()

    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = generate_elevenlabs_speech(text="Teste", settings=_settings(), client=client)

    assert attempts == 2
    assert result.attempt_count == 2


def test_generate_elevenlabs_speech_reports_exhausted_transport_retries() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("temporary failure", request=request)

    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ElevenLabsSpeechGenerationError) as error:
            generate_elevenlabs_speech(text="Teste", settings=_settings(), client=client)

    assert attempts == 2
    assert error.value.error_code == "elevenlabs_transport_error"
    assert error.value.attempt_count == 2


def test_generate_elevenlabs_speech_waits_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return _audio_response(503) if attempts == 1 else _audio_response()

    monkeypatch.setattr(elevenlabs_client.time, "sleep", waits.append)
    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),
    ) as client:
        generate_elevenlabs_speech(
            text="Teste",
            settings=_settings(elevenlabs_retry_backoff_seconds=0.5),
            client=client,
        )

    assert waits == [0.5]


@pytest.mark.parametrize("status_code", [429, 503])
def test_generate_elevenlabs_speech_retries_transient_response(status_code: int) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return _audio_response(status_code) if attempts == 1 else _audio_response()

    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = generate_elevenlabs_speech(text="Teste", settings=_settings(), client=client)

    assert attempts == 2
    assert result.attempt_count == 2


def test_generate_elevenlabs_speech_does_not_retry_permanent_4xx() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"detail": "invalid request"})

    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ElevenLabsSpeechGenerationError) as error:
            generate_elevenlabs_speech(text="Teste", settings=_settings(), client=client)

    assert attempts == 1
    assert error.value.error_code == "elevenlabs_upstream_error"
    assert error.value.status_code == 400
    assert error.value.attempt_count == 1


def test_generate_elevenlabs_speech_reports_exhausted_retries() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"detail": "unavailable"})

    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ElevenLabsSpeechGenerationError) as error:
            generate_elevenlabs_speech(text="Teste", settings=_settings(), client=client)

    assert attempts == 2
    assert error.value.error_code == "elevenlabs_upstream_error"
    assert error.value.status_code == 503
    assert error.value.attempt_count == 2


def test_generate_elevenlabs_speech_rejects_empty_audio() -> None:
    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "audio/mpeg"})
        ),
    ) as client:
        with pytest.raises(ElevenLabsSpeechGenerationError) as error:
            generate_elevenlabs_speech(text="Teste", settings=_settings(), client=client)

    assert error.value.error_code == "elevenlabs_audio_empty"
    assert error.value.attempt_count == 1


def test_generate_elevenlabs_speech_rejects_non_audio_content_type() -> None:
    with httpx.Client(
        base_url="https://api.elevenlabs.io",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"detail": "not audio"},
            )
        ),
    ) as client:
        with pytest.raises(ElevenLabsSpeechGenerationError) as error:
            generate_elevenlabs_speech(text="Teste", settings=_settings(), client=client)

    assert error.value.error_code == "elevenlabs_content_type_invalid"
    assert error.value.attempt_count == 1


def test_prepare_generated_audio_stores_public_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        generated_audio_application,
        "generate_elevenlabs_speech",
        lambda **kwargs: ElevenLabsGeneratedSpeech(
            content=b"mp3-bytes",
            content_type="audio/mpeg",
            output_format="mp3_44100_128",
            attempt_count=2,
        ),
    )

    settings = _settings(
        generated_audio_storage_dir=str(tmp_path),
        generated_audio_max_chars=100,
    )
    asset = generated_audio_application.prepare_generated_audio(
        text="Texto que vira audio.",
        settings=settings,
    )

    assert asset.media_id.startswith("generated-audio:audio_")
    assert asset.media_url.startswith("https://agent.example.com/generated-audio/audio_")
    assert asset.content_type == "audio/mpeg"
    assert asset.filename.endswith(".mp3")
    assert asset.attempt_count == 2
    assert (tmp_path / asset.filename).read_bytes() == b"mp3-bytes"
    assert not list(tmp_path.glob("*.tmp"))

    resolved_file = generated_audio_application.resolve_generated_audio_file(
        asset.filename,
        settings=settings,
    )
    assert resolved_file.path == tmp_path / asset.filename
    assert resolved_file.content_type == "audio/mpeg"


def test_store_generated_audio_removes_temporary_file_after_replace_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "replace", lambda self, target: (_ for _ in ()).throw(OSError()))

    with pytest.raises(GeneratedAudioStorageError):
        store_generated_audio(
            b"audio",
            extension=".mp3",
            storage_dir=tmp_path,
            ttl_seconds=60,
        )

    assert list(tmp_path.iterdir()) == []


def test_store_generated_audio_normalizes_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_iterdir = Path.iterdir

    def failing_iterdir(path: Path):
        if path == tmp_path:
            raise PermissionError("denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", failing_iterdir)

    with pytest.raises(GeneratedAudioStorageError):
        store_generated_audio(
            b"audio",
            extension=".mp3",
            storage_dir=tmp_path,
            ttl_seconds=60,
        )


def test_resolve_stored_generated_audio_rejects_unsafe_or_missing_filename(
    tmp_path: Path,
) -> None:
    for filename in ("../audio_deadbeef.mp3", f"audio_{'0' * 32}.mp3"):
        with pytest.raises(GeneratedAudioStoredFileNotFoundError):
            resolve_stored_generated_audio_file(
                filename,
                storage_dir=tmp_path,
                ttl_seconds=60,
            )


def test_resolve_stored_generated_audio_deletes_expired_file(tmp_path: Path) -> None:
    filename = f"audio_{'a' * 32}.mp3"
    file_path = tmp_path / filename
    file_path.write_bytes(b"expired")
    expired_at = time.time() - 120
    os.utime(file_path, (expired_at, expired_at))

    with pytest.raises(GeneratedAudioStoredFileNotFoundError):
        resolve_stored_generated_audio_file(
            filename,
            storage_dir=tmp_path,
            ttl_seconds=60,
        )

    assert not file_path.exists()


def test_resolve_stored_generated_audio_normalizes_stat_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    filename = f"audio_{'b' * 32}.mp3"
    original_stat = Path.stat

    def failing_stat(path: Path, *args: object, **kwargs: object):
        if path.name == filename:
            raise PermissionError("denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)

    with pytest.raises(GeneratedAudioStorageError):
        resolve_stored_generated_audio_file(
            filename,
            storage_dir=tmp_path,
            ttl_seconds=60,
        )


def test_prepare_generated_audio_maps_storage_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        generated_audio_application,
        "generate_elevenlabs_speech",
        lambda **kwargs: ElevenLabsGeneratedSpeech(
            content=b"mp3-bytes",
            content_type="audio/mpeg",
            output_format="mp3_44100_128",
            attempt_count=1,
        ),
    )

    def fail_to_store(*args: object, **kwargs: object) -> str:
        raise GeneratedAudioStorageError("storage failed")

    monkeypatch.setattr(
        generated_audio_application,
        "store_generated_audio",
        fail_to_store,
    )

    with pytest.raises(GeneratedAudioError) as error:
        generated_audio_application.prepare_generated_audio(
            text="Texto",
            settings=_settings(generated_audio_storage_dir=str(tmp_path)),
        )

    assert error.value.error_code == "generated_audio_storage_error"
    assert error.value.attempt_count == 1


@pytest.mark.parametrize(
    ("raised_error", "expected_code"),
    [
        (FileNotFoundError(), "ffmpeg_missing"),
        (subprocess.CalledProcessError(1, ["ffmpeg"]), "ffmpeg_conversion_failed"),
    ],
)
def test_convert_audio_to_ogg_opus_classifies_ffmpeg_failures(
    monkeypatch: pytest.MonkeyPatch,
    raised_error: Exception,
    expected_code: str,
) -> None:
    monkeypatch.setattr(
        generated_audio_conversion.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(raised_error),
    )

    with pytest.raises(GeneratedAudioConversionError) as error:
        convert_audio_to_ogg_opus(b"mp3", source_extension=".mp3")

    assert error.value.error_code == expected_code


def test_convert_audio_to_ogg_opus_rejects_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> None:
        Path(command[-1]).write_bytes(b"")

    monkeypatch.setattr(generated_audio_conversion.subprocess, "run", fake_run)

    with pytest.raises(GeneratedAudioConversionError) as error:
        convert_audio_to_ogg_opus(b"mp3", source_extension=".mp3")

    assert error.value.error_code == "ffmpeg_output_empty"


def test_convert_audio_to_ogg_opus_normalizes_temporary_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_write_bytes = Path.write_bytes

    def failing_write(path: Path, content: bytes) -> int:
        if path.name.startswith("source"):
            raise PermissionError("denied")
        return original_write_bytes(path, content)

    monkeypatch.setattr(Path, "write_bytes", failing_write)

    with pytest.raises(GeneratedAudioStorageError):
        convert_audio_to_ogg_opus(b"mp3", source_extension=".mp3")


def test_generated_audio_route_returns_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / f"audio_{'c' * 32}.mp3"
    file_path.write_bytes(b"audio-response")
    monkeypatch.setattr(
        generated_audio_route,
        "resolve_generated_audio_file",
        lambda filename, settings: GeneratedAudioFile(file_path, "audio/mpeg"),
    )

    with TestClient(create_app()) as client:
        response = client.get(f"/generated-audio/{file_path.name}")

    assert response.status_code == 200
    assert response.content == b"audio-response"
    assert response.headers["content-type"] == "audio/mpeg"


def test_generated_audio_route_returns_404_for_unavailable_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        generated_audio_route,
        "resolve_generated_audio_file",
        lambda filename, settings: (_ for _ in ()).throw(GeneratedAudioNotFoundError()),
    )

    with TestClient(create_app()) as client:
        response = client.get(f"/generated-audio/audio_{'d' * 32}.mp3")

    assert response.status_code == 404
    assert response.json() == {"detail": "Generated audio not found."}


def test_generated_audio_route_returns_500_for_storage_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        generated_audio_route,
        "resolve_generated_audio_file",
        lambda filename, settings: (_ for _ in ()).throw(
            GeneratedAudioError("storage failed", error_code="generated_audio_storage_error")
        ),
    )

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.get(f"/generated-audio/audio_{'e' * 32}.mp3")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
