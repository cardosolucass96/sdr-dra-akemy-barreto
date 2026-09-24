from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

STAGING_LABEL = "staging"
PRODUCTION_LABEL = "production"
SOURCE_CONFIG_KEY = "_source"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _normalize_prompt_content(prompt_type: str, prompt: Any) -> Any:
    """Normalize SDK-only chat fields before comparing prompt content."""
    if prompt_type != "chat" or not isinstance(prompt, list):
        return prompt

    normalized_messages: list[Any] = []
    for message in prompt:
        if not isinstance(message, Mapping):
            normalized_messages.append(message)
            continue

        if message.get("type") == "placeholder":
            normalized_messages.append(
                {
                    "type": "placeholder",
                    "name": message.get("name"),
                }
            )
            continue

        if "role" in message and "content" in message:
            normalized_messages.append(
                {
                    "role": message["role"],
                    "content": message["content"],
                }
            )
            continue

        normalized_messages.append(dict(message))

    return normalized_messages


def _prompt_payload(*, name: str, prompt_type: str, prompt: Any) -> dict[str, Any]:
    return {
        "name": name,
        "prompt": prompt,
        "type": prompt_type,
    }


def _content_hash(*, name: str, prompt_type: str, prompt: Any) -> str:
    payload = _prompt_payload(name=name, prompt_type=prompt_type, prompt=prompt)
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _has_same_content(*, name: str, prompt_type: str, local: Any, remote: Any) -> bool:
    local_payload = _prompt_payload(
        name=name,
        prompt_type=prompt_type,
        prompt=_normalize_prompt_content(prompt_type, local),
    )
    remote_payload = _prompt_payload(
        name=name,
        prompt_type=prompt_type,
        prompt=_normalize_prompt_content(prompt_type, remote),
    )
    return _canonical_json(local_payload) == _canonical_json(remote_payload)


def _source_commit_sha() -> str | None:
    return os.getenv("PROMPT_SYNC_COMMIT_SHA") or os.getenv("GITHUB_SHA")


def _source_config(content_hash: str) -> dict[str, dict[str, str]]:
    source = {"content_sha256": content_hash}
    git_environment = {
        "GITHUB_REPOSITORY": "repository",
        "GITHUB_RUN_ID": "workflow_run_id",
    }
    for environment_name, metadata_name in git_environment.items():
        value = os.getenv(environment_name)
        if value:
            source[metadata_name] = value
    if commit_sha := _source_commit_sha():
        source["commit_sha"] = commit_sha
    return {SOURCE_CONFIG_KEY: source}


def _commit_message(prompt_name: str) -> str:
    repository = os.getenv("GITHUB_REPOSITORY")
    commit_sha = _source_commit_sha()
    if repository and commit_sha:
        return f"Sync {prompt_name} from {repository}@{commit_sha[:12]}"
    return f"Sync {prompt_name} from Git"


def _build_prompt_sync_settings():
    """Build the ephemeral Langfuse configuration used by this operational script."""
    from app.core.config import BootstrapSettings, RuntimeSettings, build_execution_settings

    return build_execution_settings(
        BootstrapSettings(),
        RuntimeSettings(
            langfuse_enabled=os.getenv("LANGFUSE_ENABLED", "true"),
            langfuse_base_url=os.getenv("LANGFUSE_BASE_URL"),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize changed Git prompt definitions to Langfuse staging."
    )
    parser.add_argument(
        "--env-file",
        action="append",
        help="Optional environment file to load before bootstrapping prompts.",
    )
    parser.add_argument(
        "--name",
        action="append",
        help="Prompt name to bootstrap. May be passed multiple times. Defaults to all prompts.",
    )
    parser.add_argument(
        "--promote-production",
        action="store_true",
        help="Also apply the production label to the synchronized staging version.",
    )
    args = parser.parse_args()

    for env_file in args.env_file or []:
        load_dotenv(env_file, override=True)

    from langfuse.api import NotFoundError

    from app.agent.prompts import get_prompt_definitions
    from app.observability.langfuse import get_langfuse_client, using_langfuse_settings

    with using_langfuse_settings(_build_prompt_sync_settings()):
        client = get_langfuse_client()
    if client is None:
        print(
            "Langfuse is not configured. Set LANGFUSE_ENABLED, LANGFUSE_PUBLIC_KEY, "
            "LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL before bootstrapping prompts."
        )
        return 1

    selected_names = set(args.name or [])
    definitions = [
        definition
        for definition in get_prompt_definitions()
        if not selected_names or definition.name in selected_names
    ]
    missing_names = selected_names - {definition.name for definition in definitions}
    if missing_names:
        print(f"Unknown prompt names: {', '.join(sorted(missing_names))}")
        return 1

    created_count = 0
    unchanged_count = 0
    promoted_count = 0

    for definition in definitions:
        try:
            remote_prompt = client.get_prompt(
                name=definition.name,
                type=definition.type,
                label=STAGING_LABEL,
                cache_ttl_seconds=0,
                max_retries=0,
            )
        except NotFoundError:
            remote_prompt = None
        except Exception as exc:
            print(
                f"Could not read Langfuse staging prompt '{definition.name}': {exc}",
                file=sys.stderr,
            )
            return 1

        synchronized_version: int | None = None
        if remote_prompt is not None and _has_same_content(
            name=definition.name,
            prompt_type=definition.type,
            local=definition.prompt,
            remote=remote_prompt.prompt,
        ):
            synchronized_version = remote_prompt.version
            print(f"Unchanged staging prompt: {definition.name}@v{synchronized_version}")
            unchanged_count += 1
        else:
            content_hash = _content_hash(
                name=definition.name,
                prompt_type=definition.type,
                prompt=definition.prompt,
            )
            try:
                created_prompt = client.create_prompt(
                    name=definition.name,
                    type=definition.type,
                    prompt=definition.prompt,
                    labels=[STAGING_LABEL],
                    config=_source_config(content_hash),
                    commit_message=_commit_message(definition.name),
                )
            except Exception as exc:
                print(
                    f"Could not create Langfuse staging prompt '{definition.name}': {exc}",
                    file=sys.stderr,
                )
                return 1

            synchronized_version = created_prompt.version
            print(f"Created staging prompt: {definition.name}@v{synchronized_version}")
            created_count += 1

        if args.promote_production:
            try:
                client.update_prompt(
                    name=definition.name,
                    version=synchronized_version,
                    new_labels=[STAGING_LABEL, PRODUCTION_LABEL],
                )
            except Exception as exc:
                print(
                    f"Could not promote Langfuse prompt '{definition.name}': {exc}",
                    file=sys.stderr,
                )
                return 1
            print(f"Promoted prompt to production: {definition.name}@v{synchronized_version}")
            promoted_count += 1

    try:
        client.flush()
    except Exception as exc:
        print(f"Could not flush Langfuse prompt changes: {exc}", file=sys.stderr)
        return 1

    print(
        "Prompt sync complete: "
        f"{created_count} created, {unchanged_count} unchanged, {promoted_count} promoted."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
