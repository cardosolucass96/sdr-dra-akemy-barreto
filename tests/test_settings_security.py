from __future__ import annotations

import base64
import hashlib

from app.api.settings_security import create_csrf_token, verify_settings_access_key


def _encoded_hash(value: str) -> str:
    salt = b"settings-test-salt"
    n, r, p = 1024, 8, 1
    digest = hashlib.scrypt(value.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    encoded_salt = base64.urlsafe_b64encode(salt).decode()
    encoded_digest = base64.urlsafe_b64encode(digest).decode()
    return f"scrypt${n}${r}${p}${encoded_salt}${encoded_digest}"


def test_verify_settings_access_key_accepts_only_the_matching_scrypt_key() -> None:
    encoded_hash = _encoded_hash("correct-key")

    assert verify_settings_access_key("correct-key", encoded_hash) is True
    assert verify_settings_access_key("wrong-key", encoded_hash) is False
    assert verify_settings_access_key("correct-key", None) is False


def test_verify_settings_access_key_rejects_malformed_hashes() -> None:
    assert verify_settings_access_key("key", "sha256$invalid") is False
    assert verify_settings_access_key("key", "scrypt$bad$8$1$salt$digest") is False


def test_create_csrf_token_is_url_safe_and_unique() -> None:
    first = create_csrf_token()
    second = create_csrf_token()

    assert first != second
    assert len(first) >= 32
    assert "/" not in first
    assert "+" not in first
