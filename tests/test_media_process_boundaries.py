from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import app.integrations.generated_audio.conversion as generated_audio_conversion
import app.integrations.pipefacil.mapping as pipefacil_mapping
from app.integrations.generated_audio.contracts import GeneratedAudioConversionError
from app.integrations.openai_audio import OpenAITranscriptionError


def test_generated_audio_ffmpeg_has_a_runtime_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> None:
        captured["command"] = command
        captured.update(kwargs)
        Path(command[-1]).write_bytes(b"ogg-opus")

    monkeypatch.setattr(generated_audio_conversion.subprocess, "run", fake_run)

    result = generated_audio_conversion.convert_audio_to_ogg_opus(
        b"source-audio",
        source_extension=".mp3",
    )

    assert result == b"ogg-opus"
    assert captured["timeout"] == generated_audio_conversion.FFMPEG_TIMEOUT_SECONDS
    assert captured["check"] is True
    assert captured["capture_output"] is True


def test_generated_audio_ffmpeg_timeout_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(generated_audio_conversion.subprocess, "run", fake_run)

    with pytest.raises(GeneratedAudioConversionError) as exc_info:
        generated_audio_conversion.convert_audio_to_ogg_opus(
            b"source-audio",
            source_extension=".mp3",
        )

    assert exc_info.value.error_code == "ffmpeg_conversion_timeout"


def test_inbound_audio_ffmpeg_has_a_runtime_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> None:
        captured["command"] = command
        captured.update(kwargs)

    monkeypatch.setattr(pipefacil_mapping.subprocess, "run", fake_run)

    pipefacil_mapping._convert_audio_to_wav(Path("input.ogg"), Path("output.wav"))

    assert captured["timeout"] == pipefacil_mapping.FFMPEG_TIMEOUT_SECONDS
    assert captured["check"] is True
    assert captured["capture_output"] is True


def test_inbound_audio_ffmpeg_timeout_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(pipefacil_mapping.subprocess, "run", fake_run)

    with pytest.raises(OpenAITranscriptionError) as exc_info:
        pipefacil_mapping._convert_audio_to_wav(
            Path("input.ogg"),
            Path("output.wav"),
        )

    assert exc_info.value.error_code == "ffmpeg_conversion_timeout"
