from __future__ import annotations

import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.integrations.generated_audio.contracts import (
    GeneratedAudioStorageError,
    GeneratedAudioStoredFileNotFoundError,
)

GENERATED_AUDIO_FILENAME_PATTERN = re.compile(r"^audio_[a-f0-9]{32}\.(?:ogg|mp3)$")
OGG_OPUS_CONTENT_TYPE = "audio/ogg"
MP3_CONTENT_TYPE = "audio/mpeg"


@dataclass(frozen=True, slots=True)
class StoredGeneratedAudioFile:
    path: Path
    content_type: str


def store_generated_audio(
    content: bytes,
    *,
    extension: str,
    storage_dir: Path,
    ttl_seconds: int,
) -> str:
    filename = f"audio_{uuid4().hex}{extension}"
    file_path = storage_dir / filename
    temporary_path = storage_dir / f".{filename}.{uuid4().hex}.tmp"

    try:
        _cleanup_expired_generated_audio(storage_dir, ttl_seconds=ttl_seconds)
        storage_dir.mkdir(parents=True, exist_ok=True)
        temporary_path.write_bytes(content)
        temporary_path.replace(file_path)
    except OSError as exc:
        _unlink_after_failed_write(temporary_path)
        raise GeneratedAudioStorageError("Generated audio file storage failed.") from exc

    return filename


def resolve_stored_generated_audio_file(
    filename: str,
    *,
    storage_dir: Path,
    ttl_seconds: int,
) -> StoredGeneratedAudioFile:
    normalized_filename = filename.strip()
    if not GENERATED_AUDIO_FILENAME_PATTERN.fullmatch(normalized_filename):
        raise GeneratedAudioStoredFileNotFoundError("Generated audio file not found.")

    file_path = storage_dir / normalized_filename
    try:
        file_stat = file_path.stat()
    except FileNotFoundError as exc:
        raise GeneratedAudioStoredFileNotFoundError("Generated audio file not found.") from exc
    except OSError as exc:
        raise GeneratedAudioStorageError("Generated audio file metadata lookup failed.") from exc

    if not stat.S_ISREG(file_stat.st_mode):
        raise GeneratedAudioStoredFileNotFoundError("Generated audio file not found.")

    if _is_expired(file_stat.st_mtime, ttl_seconds=ttl_seconds):
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise GeneratedAudioStorageError("Generated audio file cleanup failed.") from exc
        raise GeneratedAudioStoredFileNotFoundError("Generated audio file not found.")

    return StoredGeneratedAudioFile(
        path=file_path,
        content_type=OGG_OPUS_CONTENT_TYPE if file_path.suffix == ".ogg" else MP3_CONTENT_TYPE,
    )


def _cleanup_expired_generated_audio(storage_dir: Path, *, ttl_seconds: int) -> None:
    try:
        entries = list(storage_dir.iterdir())
    except FileNotFoundError:
        return

    for file_path in entries:
        try:
            file_stat = file_path.stat()
            if stat.S_ISREG(file_stat.st_mode) and _is_expired(
                file_stat.st_mtime,
                ttl_seconds=ttl_seconds,
            ):
                file_path.unlink()
        except FileNotFoundError:
            continue


def _is_expired(modified_at: float, *, ttl_seconds: int) -> bool:
    return (time.time() - modified_at) > ttl_seconds


def _unlink_after_failed_write(file_path: Path) -> None:
    try:
        file_path.unlink()
    except OSError:
        return


__all__ = [
    "MP3_CONTENT_TYPE",
    "OGG_OPUS_CONTENT_TYPE",
    "StoredGeneratedAudioFile",
    "resolve_stored_generated_audio_file",
    "store_generated_audio",
]
