from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from app.integrations.generated_audio.contracts import (
    GeneratedAudioConversionError,
    GeneratedAudioStorageError,
)

FFMPEG_TIMEOUT_SECONDS = 60.0


def convert_audio_to_ogg_opus(content: bytes, *, source_extension: str) -> bytes:
    try:
        with tempfile.TemporaryDirectory(prefix="generated-audio-") as temp_dir:
            temp_path = Path(temp_dir)
            source_path = temp_path / f"source{source_extension}"
            output_path = temp_path / "output.ogg"
            source_path.write_bytes(content)

            command = [
                "ffmpeg",
                "-y",
                "-i",
                str(source_path),
                "-map_metadata",
                "-1",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "libopus",
                "-b:a",
                "48k",
                "-vbr",
                "on",
                "-application",
                "voip",
                "-frame_duration",
                "20",
                str(output_path),
            ]
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    timeout=FFMPEG_TIMEOUT_SECONDS,
                )
            except FileNotFoundError as exc:
                raise GeneratedAudioConversionError(
                    "ffmpeg is required to convert generated audio to Ogg Opus.",
                    error_code="ffmpeg_missing",
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise GeneratedAudioConversionError(
                    "ffmpeg timed out while converting generated audio to Ogg Opus.",
                    error_code="ffmpeg_conversion_timeout",
                ) from exc
            except subprocess.CalledProcessError as exc:
                raise GeneratedAudioConversionError(
                    "ffmpeg failed to convert generated audio to Ogg Opus.",
                    error_code="ffmpeg_conversion_failed",
                ) from exc

            output = output_path.read_bytes()
            if not output:
                raise GeneratedAudioConversionError(
                    "ffmpeg returned an empty generated audio file.",
                    error_code="ffmpeg_output_empty",
                )

            return output
    except GeneratedAudioConversionError:
        raise
    except OSError as exc:
        raise GeneratedAudioStorageError(
            "Generated audio temporary storage operation failed."
        ) from exc


__all__ = ["convert_audio_to_ogg_opus"]
