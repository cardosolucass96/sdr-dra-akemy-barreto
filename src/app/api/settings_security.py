"""Small, dependency-free authentication helpers for the settings page."""

import base64
import hashlib
import hmac
import secrets

SCRYPT_PREFIX = "scrypt"


def verify_settings_access_key(value: str, encoded_hash: str | None) -> bool:
    """Verify the access key against the documented scrypt representation."""

    if not encoded_hash:
        return False
    try:
        algorithm, n, r, p, encoded_salt, encoded_digest = encoded_hash.split("$", 5)
        if algorithm != SCRYPT_PREFIX:
            return False
        salt = _decode(encoded_salt)
        expected = _decode(encoded_digest)
        actual = hashlib.scrypt(
            value.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def create_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))
