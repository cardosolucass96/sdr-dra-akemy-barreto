from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.outbound_media import (
    OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT,
    build_outbound_media_prompt_view,
    load_outbound_media_catalog,
)


def _media_entry(**overrides):
    entry = {
        "id": "catalogo-pdf",
        "type": "document",
        "title": "Catalogo PDF",
        "description": "Catalogo comercial resumido.",
        "when_to_use": "Quando o usuario pedir o catalogo.",
        "media_url": "https://cdn.example.com/catalogo.pdf",
        "content_type": "application/pdf",
        "filename": "catalogo.pdf",
        "enabled": True,
    }
    entry.update(overrides)
    return entry


def test_outbound_media_catalog_loads_valid_entries(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({"media": [_media_entry()]}), encoding="utf-8")

    catalog = load_outbound_media_catalog(catalog_path)

    assert len(catalog) == 1
    assert catalog[0].id == "catalogo-pdf"
    assert catalog[0].type == "document"


def test_outbound_media_catalog_allows_empty_catalog(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({"media": []}), encoding="utf-8")

    catalog = load_outbound_media_catalog(catalog_path)

    assert catalog == ()
    assert build_outbound_media_prompt_view(catalog) == OUTBOUND_MEDIA_CATALOG_UNAVAILABLE_TEXT


def test_outbound_media_example_catalog_is_valid() -> None:
    catalog = load_outbound_media_catalog(Path("src/app/outbound_media/catalog.example.json"))
    audio_asset = next(asset for asset in catalog if asset.type == "audio")

    assert {asset.type for asset in catalog} == {"image", "audio", "document"}
    assert all(not asset.enabled for asset in catalog)
    assert audio_asset.content_type == "audio/ogg"
    assert audio_asset.filename.endswith(".ogg")


def test_outbound_media_catalogs_are_included_in_package_data() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    package_data = pyproject["tool"]["setuptools"]["package-data"]["app.outbound_media"]

    assert set(package_data) >= {"catalog.json", "catalog.example.json"}


def test_outbound_media_catalog_rejects_invalid_type(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps({"media": [_media_entry(type="sticker")]}),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_outbound_media_catalog(catalog_path)


def test_outbound_media_catalog_rejects_non_https_url(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps({"media": [_media_entry(media_url="http://cdn.example.com/catalogo.pdf")]}),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_outbound_media_catalog(catalog_path)


def test_outbound_media_prompt_view_omits_disabled_media_and_urls(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "media": [
                    _media_entry(),
                    _media_entry(
                        id="audio-desativado",
                        type="audio",
                        title="Audio desativado",
                        media_url="https://cdn.example.com/audio.ogg",
                        content_type="audio/ogg",
                        filename="audio.ogg",
                        enabled=False,
                    ),
                ]
            }
        ),
        encoding="utf-8",
    )

    prompt_view = build_outbound_media_prompt_view(load_outbound_media_catalog(catalog_path))

    assert "catalogo-pdf" in prompt_view
    assert "audio-desativado" not in prompt_view
    assert "https://cdn.example.com" not in prompt_view
    assert "media_url" not in prompt_view
