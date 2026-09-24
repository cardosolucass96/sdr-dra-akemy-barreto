from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.api.dependencies import get_settings
from app.application.generated_audio import (
    GeneratedAudioNotFoundError,
    resolve_generated_audio_file,
)
from app.core.config import Settings

generated_audio_router = APIRouter()
SettingsDep = Annotated[Settings, Depends(get_settings)]


@generated_audio_router.get("/generated-audio/{filename}")
def generated_audio(filename: str, settings: SettingsDep) -> FileResponse:
    try:
        audio_file = resolve_generated_audio_file(filename, settings=settings)
    except GeneratedAudioNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generated audio not found.",
        ) from exc

    return FileResponse(
        audio_file.path,
        media_type=audio_file.content_type,
        filename=filename,
    )
