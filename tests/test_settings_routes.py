from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import app.api.routes.settings as settings_routes
from app.core.config import RuntimeSettings
from app.main import create_app

CSRF_PATTERN = re.compile(r'name="csrf_token" value="([^"]+)"')
VERSION_PATTERN = re.compile(r'name="version" value="(\d+)"')


@pytest.fixture(autouse=True)
def settings_bootstrap_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_bootstrap_settings

    monkeypatch.setenv("SETTINGS_ADMIN_KEY_HASH", "scrypt$test")
    monkeypatch.setenv("SETTINGS_SESSION_SECRET", "test-session-secret")
    get_bootstrap_settings.cache_clear()
    yield
    get_bootstrap_settings.cache_clear()


@pytest.fixture
def settings_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings_routes, "verify_settings_access_key", lambda *_: True)
    with TestClient(create_app(app_env="development")) as client:
        yield client


def _hidden_value(response, pattern: re.Pattern[str]) -> str:
    match = pattern.search(response.text)
    assert match is not None
    return match.group(1)


def _settings_form(*, csrf_token: str, version: str, **changes: object) -> dict[str, str]:
    values: dict[str, str] = {"csrf_token": csrf_token, "version": version}
    current = RuntimeSettings().model_dump()
    current.update(changes)
    for name, value in current.items():
        if isinstance(value, bool):
            if value:
                values[name] = "true"
        elif value is None:
            values[name] = ""
        else:
            values[name] = str(value)
    return values


def _login(client: TestClient) -> str:
    login_page = client.get("/settings/login")
    csrf_token = _hidden_value(login_page, CSRF_PATTERN)
    response = client.post(
        "/settings/login",
        data={"csrf_token": csrf_token, "access_key": "test-key"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    page = client.get("/settings")
    assert page.status_code == 200
    return _hidden_value(page, CSRF_PATTERN)


def test_settings_page_requires_login_and_saves_runtime_configuration(
    settings_client: TestClient,
) -> None:
    redirect = settings_client.get("/settings", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/settings/login"
    save_redirect = settings_client.post("/settings", follow_redirects=False)
    assert save_redirect.status_code == 303
    assert save_redirect.headers["location"] == "/settings/login"
    logout_redirect = settings_client.post("/settings/logout", follow_redirects=False)
    assert logout_redirect.status_code == 303
    assert logout_redirect.headers["location"] == "/settings/login"

    csrf_token = _login(settings_client)
    already_authenticated = settings_client.get("/settings/login", follow_redirects=False)
    assert already_authenticated.status_code == 303
    assert already_authenticated.headers["location"] == "/settings"
    page = settings_client.get("/settings")
    version = _hidden_value(page, VERSION_PATTERN)
    response = settings_client.post(
        "/settings",
        data=_settings_form(
            csrf_token=csrf_token,
            version=version,
            app_name="SDR atualizado",
            openai_model="gpt-4.1-mini",
        ),
    )

    assert response.status_code == 200
    assert "Configurações salvas." in response.text
    assert "SDR atualizado" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert settings_client.app.state.runtime_settings_service.get_snapshot().version == 2


def test_settings_login_and_save_failures_are_rendered_to_the_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_routes, "verify_settings_access_key", lambda *_: False)
    with TestClient(create_app(app_env="development")) as client:
        login_page = client.get("/settings/login")
        csrf_token = _hidden_value(login_page, CSRF_PATTERN)
        invalid_key = client.post(
            "/settings/login",
            data={"csrf_token": csrf_token, "access_key": "wrong"},
        )
        invalid_csrf = client.post(
            "/settings/login",
            data={"csrf_token": "invalid", "access_key": "wrong"},
        )

        assert invalid_key.status_code == 401
        assert "Chave de acesso inválida." in invalid_key.text
        assert invalid_csrf.status_code == 403
        assert "Sessão expirada." in invalid_csrf.text

    monkeypatch.setattr(settings_routes, "verify_settings_access_key", lambda *_: True)
    with TestClient(create_app(app_env="development")) as client:
        csrf_token = _login(client)
        page = client.get("/settings")
        version = _hidden_value(page, VERSION_PATTERN)
        invalid = client.post(
            "/settings",
            data=_settings_form(
                csrf_token=csrf_token,
                version=version,
                generated_audio_auto_enabled=True,
                generated_audio_auto_min_chars=900,
                generated_audio_max_chars=100,
            ),
        )
        expired = client.post(
            "/settings",
            data=_settings_form(csrf_token="expired", version=version),
        )
        invalid_audio = client.post(
            "/settings",
            data=_settings_form(
                csrf_token=csrf_token,
                version=version,
                generated_audio_auto_enabled=True,
            ),
        )

        assert invalid.status_code == 422
        assert "Valor inválido" in invalid.text
        assert expired.status_code == 403
        assert "Sessão expirada." in expired.text
        assert invalid_audio.status_code == 422
        assert "áudio automático" in invalid_audio.text


def test_settings_detects_conflicting_updates_and_logs_out(settings_client: TestClient) -> None:
    csrf_token = _login(settings_client)
    page = settings_client.get("/settings")
    version = _hidden_value(page, VERSION_PATTERN)
    service = settings_client.app.state.runtime_settings_service
    current = service.get_snapshot()
    service.update_snapshot(
        type(current)(
            settings=RuntimeSettings(app_name="Outra sessão"),
            version=current.version,
            updated_at=current.updated_at,
        ),
        expected_version=current.version,
    )

    conflict = settings_client.post(
        "/settings",
        data=_settings_form(csrf_token=csrf_token, version=version),
    )
    logout = settings_client.post(
        "/settings/logout",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert conflict.status_code == 409
    assert "outra sessão" in conflict.text
    assert logout.status_code == 303
    assert logout.headers["location"] == "/settings/login"


def test_settings_logout_with_invalid_csrf_keeps_the_session(settings_client: TestClient) -> None:
    _login(settings_client)

    response = settings_client.post(
        "/settings/logout",
        data={"csrf_token": "invalid"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert settings_client.get("/settings").status_code == 200
