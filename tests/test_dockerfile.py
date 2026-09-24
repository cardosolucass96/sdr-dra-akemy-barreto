from __future__ import annotations

from pathlib import Path


def test_production_image_runs_as_an_unprivileged_user() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "USER 10001:10001" in dockerfile
    assert "groupadd --gid 10001 app" in dockerfile
    assert "useradd" in dockerfile
    assert "--shell /usr/sbin/nologin" in dockerfile
    assert "chown -R app:app /app /home/app" in dockerfile


def test_production_image_prepares_only_required_writable_storage() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "mkdir -p /app/.runtime/generated-audio" in dockerfile
    assert "PIP_DISABLE_PIP_VERSION_CHECK=1" in dockerfile
    assert "python -m pip install --no-cache-dir ." in dockerfile
    assert "pip install --no-cache-dir --upgrade pip" not in dockerfile


def test_production_image_keeps_readiness_healthcheck() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "HEALTHCHECK" in dockerfile
    assert "http://127.0.0.1:8000/ready" in dockerfile


def test_production_image_includes_the_sdr_profile_and_knowledge_base() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "COPY clara-instituto-saudavelmente-configuracao-sdr.json ./" in dockerfile
