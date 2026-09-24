from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import get_settings

OutboundMediaType = Literal["image", "video", "audio", "document"]
DEFAULT_OUTBOUND_MEDIA_CATALOG_PATH = Path(__file__).with_name("catalog.json")
OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT = "No outbound media is available."


class OutboundMediaCatalogError(RuntimeError):
    pass


class OutboundMediaAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    type: OutboundMediaType
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    when_to_use: str = Field(min_length=1)
    media_url: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    enabled: bool

    @field_validator("media_url")
    @classmethod
    def validate_https_media_url(cls, value: str) -> str:
        media_url = value.strip()
        parsed_url = urlparse(media_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ValueError("media_url must be an absolute HTTPS URL")
        return media_url


def load_outbound_media_catalog(path: str | Path) -> tuple[OutboundMediaAsset, ...]:
    catalog_path = Path(path)
    try:
        raw_payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OutboundMediaCatalogError(
            f"Outbound media catalog not found: {catalog_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise OutboundMediaCatalogError(
            f"Outbound media catalog is not valid JSON: {catalog_path}"
        ) from exc

    if isinstance(raw_payload, dict):
        raw_entries = raw_payload.get("media", [])
    else:
        raw_entries = raw_payload

    if not isinstance(raw_entries, list):
        raise OutboundMediaCatalogError("Outbound media catalog must be a list or a media object.")

    return tuple(OutboundMediaAsset.model_validate(entry) for entry in raw_entries)


def _resolve_catalog_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)

    configured_path = get_settings().outbound_media_catalog_path
    if configured_path:
        return Path(configured_path)

    return DEFAULT_OUTBOUND_MEDIA_CATALOG_PATH


@lru_cache
def _get_outbound_media_catalog_cached(path: str) -> tuple[OutboundMediaAsset, ...]:
    return load_outbound_media_catalog(path)


def clear_outbound_media_catalog_cache() -> None:
    _get_outbound_media_catalog_cached.cache_clear()


def get_outbound_media_catalog(
    path: str | Path | None = None,
) -> tuple[OutboundMediaAsset, ...]:
    return _get_outbound_media_catalog_cached(str(_resolve_catalog_path(path)))


def get_enabled_outbound_media_by_id(
    assets: tuple[OutboundMediaAsset, ...] | None = None,
) -> dict[str, OutboundMediaAsset]:
    catalog = assets if assets is not None else get_outbound_media_catalog()
    return {asset.id: asset for asset in catalog if asset.enabled}


def get_outbound_media_asset(
    media_id: str,
    *,
    assets: tuple[OutboundMediaAsset, ...] | None = None,
) -> OutboundMediaAsset | None:
    return get_enabled_outbound_media_by_id(assets).get(media_id)


def build_outbound_media_prompt_view(
    assets: tuple[OutboundMediaAsset, ...] | None = None,
) -> str:
    enabled_assets = [
        asset
        for asset in (assets if assets is not None else get_outbound_media_catalog())
        if asset.enabled
    ]
    if not enabled_assets:
        return OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT

    entries: list[str] = []
    for asset in enabled_assets:
        safe_entry: dict[str, Any] = {
            "media_id": asset.id,
            "type": asset.type,
            "title": asset.title,
            "description": asset.description,
            "when_to_use": asset.when_to_use,
        }
        entries.append(json.dumps(safe_entry, ensure_ascii=False, separators=(",", ":")))

    return "\n".join(entries)
