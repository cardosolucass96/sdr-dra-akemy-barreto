from __future__ import annotations

import pytest

from app.core.config import get_bootstrap_settings, get_settings


@pytest.fixture(autouse=True)
def use_non_production_runtime_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SETTINGS_ADMIN_KEY_HASH", "scrypt$test")
    monkeypatch.setenv("SETTINGS_SESSION_SECRET", "test-session-secret")
    get_bootstrap_settings.cache_clear()
    get_settings.cache_clear()
    yield
    get_bootstrap_settings.cache_clear()
    get_settings.cache_clear()
