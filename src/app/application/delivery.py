from __future__ import annotations

from typing import Any, cast

from app.application.dto import ResponsePartResult, ResponsePartType

MEDIA_RESPONSE_PART_TYPES = {"image", "video", "audio", "document"}


def build_response_parts(
    *,
    response_messages: list[str],
    response_media: list[dict[str, Any]] | None = None,
) -> list[ResponsePartResult]:
    parts = [
        ResponsePartResult(type="text", text=message.strip())
        for message in response_messages
        if message.strip()
    ]

    for media in response_media or []:
        media_type = str(media.get("type", "")).strip()
        media_id = str(media.get("media_id", "")).strip()
        if media_type not in MEDIA_RESPONSE_PART_TYPES or not media_id:
            continue

        caption = media.get("caption")
        content_type = media.get("content_type")
        filename = media.get("filename")
        parts.append(
            ResponsePartResult(
                type=cast(ResponsePartType, media_type),
                media_id=media_id,
                caption=caption.strip() if isinstance(caption, str) and caption.strip() else None,
                content_type=content_type if isinstance(content_type, str) else None,
                filename=filename if isinstance(filename, str) else None,
            )
        )

    return parts
