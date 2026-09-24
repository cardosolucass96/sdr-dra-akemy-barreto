#!/usr/bin/env python3
"""Print a new settings access key and its scrypt hash for the deployment secret."""

import base64
import hashlib
import secrets

N = 2**14
R = 8
P = 1


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def main() -> None:
    key = secrets.token_urlsafe(32)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(key.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=32)
    print(f"Settings access key: {key}")
    print(f"SETTINGS_ADMIN_KEY_HASH=scrypt${N}${R}${P}${_encode(salt)}${_encode(digest)}")
    print(f"SETTINGS_SESSION_SECRET={secrets.token_urlsafe(48)}")


if __name__ == "__main__":
    main()
