from __future__ import annotations

import re
import subprocess
from pathlib import Path

from app.core.config import BootstrapSettings

ENV_EXAMPLE = Path(".env.example")
ENV_KEY_PATTERN = re.compile(r"^#?\s*([A-Z0-9_]+)=")
REQUIRED_BOOTSTRAP_KEYS = {
    "DATABASE_URL",
    "OPENAI_API_KEY",
    "PIPEFACIL_API_KEY",
    "PIPEFACIL_WEBHOOK_SIGNATURE_SECRET",
    "SETTINGS_ADMIN_KEY_HASH",
    "SETTINGS_SESSION_SECRET",
}
OPERATIONAL_KEYS = {
    "APP_ENV",
    "APP_NAME",
    "APP_SLUG",
    "OPENAI_MODEL",
    "OPENAI_REASONING_EFFORT",
    "LANGFUSE_PROMPT_LABEL",
    "PIPEFACIL_WEBHOOK_SIGNATURE_ENABLED",
}


def _env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text().splitlines():
        match = ENV_KEY_PATTERN.match(line)
        if match:
            keys.add(match.group(1))
    return keys


def _bootstrap_keys() -> set[str]:
    return {field_name.upper() for field_name in BootstrapSettings.model_fields}


def test_env_example_only_documents_supported_settings() -> None:
    undocumented_keys = _env_keys(ENV_EXAMPLE) - _bootstrap_keys()
    assert undocumented_keys == set(), (
        f"{ENV_EXAMPLE} contains unsupported settings: {sorted(undocumented_keys)}"
    )


def test_environment_example_includes_required_bootstrap_secrets() -> None:
    assert REQUIRED_BOOTSTRAP_KEYS <= _env_keys(ENV_EXAMPLE)


def test_environment_example_does_not_offer_operational_configuration() -> None:
    assert _env_keys(ENV_EXAMPLE).isdisjoint(OPERATIONAL_KEYS)


def test_shell_env_loader_preserves_platform_precedence(tmp_path: Path) -> None:
    base_env = tmp_path / ".env.test"
    base_env.write_text("FROM_BASE=base\nSHARED_VALUE=base\nPLATFORM_VALUE=base\n")

    command = """
source scripts/env.sh
load_env_file "$BASE_ENV" false
printf '%s|%s|%s' "$FROM_BASE" "$SHARED_VALUE" "$PLATFORM_VALUE"
"""
    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env={
            "BASE_ENV": str(base_env),
            "PATH": "/usr/bin:/bin",
            "PLATFORM_VALUE": "platform",
        },
    )

    assert result.stdout == "base|base|platform"
