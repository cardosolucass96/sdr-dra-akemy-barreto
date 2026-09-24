import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.prompts.chat import MessagesPlaceholder

from app.agent.prompts import (
    CLASSIFIER_PROMPT_NAME,
    OUTBOUND_MEDIA_CLASSIFIER_PROMPT_NAME,
    RESPONDER_PROMPT_NAME,
    WHATSAPP_STYLE_PROMPT_NAME,
    get_classifier_prompt_template,
    get_outbound_media_classifier_prompt_template,
    get_prompt_definitions,
    get_responder_prompt_template,
    get_whatsapp_style_prompt_text,
)
from app.core.config import Settings, get_settings
from app.observability import reset_langfuse_clients, using_langfuse_settings
from app.observability.langfuse import (
    build_langchain_chat_prompt,
    get_langfuse_prompt,
    resolve_langfuse_prompt_label,
)


@pytest.fixture(autouse=True)
def clear_settings_and_langfuse() -> None:
    reset_langfuse_clients()
    get_settings.cache_clear()
    yield
    reset_langfuse_clients()
    get_settings.cache_clear()


def test_resolve_langfuse_prompt_label_defaults_to_production_in_controlled_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PROMPT_LABEL", "")
    monkeypatch.setenv("APP_ENV", "development")

    assert resolve_langfuse_prompt_label() == "production"


def test_resolve_langfuse_prompt_label_defaults_to_production_when_staging_is_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PROMPT_LABEL", "")
    monkeypatch.setenv("APP_ENV", "staging")

    assert resolve_langfuse_prompt_label() == "production"


def test_resolve_langfuse_prompt_label_defaults_to_production_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PROMPT_LABEL", "")
    monkeypatch.setenv("APP_ENV", "production")

    assert resolve_langfuse_prompt_label() == "production"


def test_resolve_langfuse_prompt_label_uses_override(monkeypatch: pytest.MonkeyPatch) -> None:
    with using_langfuse_settings(Settings(langfuse_prompt_label="preview")):
        assert resolve_langfuse_prompt_label() == "preview"


def test_get_langfuse_prompt_uses_configured_label(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePrompt:
        def get_langchain_prompt(self):
            return [("system", "Ok"), ("human", "{latest_user_message}")]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def get_prompt(self, name, *, type, label, fallback, cache_ttl_seconds):
            self.calls.append(
                {
                    "name": name,
                    "type": type,
                    "label": label,
                    "fallback": fallback,
                    "cache_ttl_seconds": cache_ttl_seconds,
                }
            )
            return FakePrompt()

    client = FakeClient()
    monkeypatch.setattr(
        "app.observability.langfuse.get_langfuse_client",
        lambda: client,
    )

    with using_langfuse_settings(Settings(langfuse_prompt_label="preview")):
        prompt = get_langfuse_prompt(
            CLASSIFIER_PROMPT_NAME,
            prompt_type="chat",
            fallback=[{"role": "system", "content": "fallback"}],
        )

    assert isinstance(prompt, FakePrompt)
    assert client.calls == [
        {
            "name": CLASSIFIER_PROMPT_NAME,
            "type": "chat",
            "label": "preview",
            "fallback": [{"role": "system", "content": "fallback"}],
            "cache_ttl_seconds": 60,
        }
    ]


def test_build_langchain_chat_prompt_uses_local_fallback_when_langfuse_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("APP_ENV", "production")

    prompt, template = get_classifier_prompt_template()

    assert prompt.is_fallback is True
    assert prompt.name == CLASSIFIER_PROMPT_NAME
    assert prompt.labels == ["production"]
    assert template.metadata["langfuse_prompt"] is prompt
    assert "latest_user_message" in template.input_variables


def test_responder_prompt_template_preserves_message_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_PROMPT_LABEL", "")
    monkeypatch.setenv("APP_ENV", "development")

    prompt, template = get_responder_prompt_template()

    assert prompt.name == RESPONDER_PROMPT_NAME
    assert prompt.labels == ["production"]
    assert any(isinstance(message, MessagesPlaceholder) for message in template.messages)
    assert "conversation_history" in template.input_variables
    assert "response_style" in template.input_variables
    assert "available_media" in template.input_variables


def test_outbound_media_classifier_prompt_template_preserves_message_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_PROMPT_LABEL", "")
    monkeypatch.setenv("APP_ENV", "development")

    prompt, template = get_outbound_media_classifier_prompt_template()

    assert prompt.name == OUTBOUND_MEDIA_CLASSIFIER_PROMPT_NAME
    assert prompt.labels == ["production"]
    assert any(isinstance(message, MessagesPlaceholder) for message in template.messages)
    assert "conversation_history" in template.input_variables
    assert "latest_user_message" in template.input_variables
    assert "available_media" in template.input_variables


def test_outbound_media_classifier_prompt_defines_safe_send_policy() -> None:
    definitions = {definition.name: definition for definition in get_prompt_definitions()}
    media_classifier_prompt = definitions[OUTBOUND_MEDIA_CLASSIFIER_PROMPT_NAME].prompt

    assert isinstance(media_classifier_prompt, list)
    system_prompt = media_classifier_prompt[0]["content"]
    assert "action=send" in system_prompt
    assert "action=none" in system_prompt
    assert "Never send unsolicited media" in system_prompt
    assert "Never invent an ID, URL, filename, or file content" in system_prompt


def test_responder_prompt_defines_hybrid_text_and_audio_policy() -> None:
    definitions = {definition.name: definition for definition in get_prompt_definitions()}
    responder_prompt = definitions[RESPONDER_PROMPT_NAME].prompt

    assert isinstance(responder_prompt, list)
    system_prompt = responder_prompt[0]["content"]
    assert "text, generated audio, or both" in system_prompt
    assert "copyable information in response_text" in system_prompt
    assert "use a hybrid reply" in system_prompt
    assert "never choose audio only because the reply is long" in system_prompt
    assert "Do not repeat the same content in both formats" in system_prompt


def test_responder_prompt_defines_consultative_conversation_contract() -> None:
    definitions = {definition.name: definition for definition in get_prompt_definitions()}
    responder_prompt = definitions[RESPONDER_PROMPT_NAME].prompt

    assert isinstance(responder_prompt, list)
    system_prompt = responder_prompt[0]["content"]
    assert "consultative responder node" in system_prompt
    assert "do not ask again for facts the user already provided" in system_prompt
    assert "Address the current question, objection, correction, or refusal" in system_prompt
    assert "one purposeful, preferably open question" in system_prompt
    assert "Do not force a call to action" in system_prompt


def test_responder_prompt_defines_truthful_and_respectful_sales_boundaries() -> None:
    definitions = {definition.name: definition for definition in get_prompt_definitions()}
    responder_prompt = definitions[RESPONDER_PROMPT_NAME].prompt

    assert isinstance(responder_prompt, list)
    system_prompt = responder_prompt[0]["content"]
    assert "Do not dump features or make a generic sales pitch" in system_prompt
    assert "Never invent prices, discounts, terms, results" in system_prompt
    assert "Respect a refusal or opt-out immediately" in system_prompt
    assert "Never be defensive or manipulative" in system_prompt


def test_whatsapp_style_prompt_is_defined_as_text_prompt() -> None:
    definitions = {definition.name: definition for definition in get_prompt_definitions()}

    assert definitions[WHATSAPP_STYLE_PROMPT_NAME].type == "text"
    assert "WhatsApp" in definitions[WHATSAPP_STYLE_PROMPT_NAME].prompt
    assert definitions[CLASSIFIER_PROMPT_NAME].type == "chat"
    assert definitions[OUTBOUND_MEDIA_CLASSIFIER_PROMPT_NAME].type == "chat"
    assert definitions[RESPONDER_PROMPT_NAME].type == "chat"


def test_whatsapp_style_prompt_defines_cohesive_and_natural_delivery() -> None:
    definitions = {definition.name: definition for definition in get_prompt_definitions()}
    style_prompt = definitions[WHATSAPP_STYLE_PROMPT_NAME].prompt

    assert isinstance(style_prompt, str)
    assert "one coherent main job" in style_prompt
    assert "natural transitions" in style_prompt
    assert "Avoid automatic confirmations" in style_prompt
    assert "feature dumps, and several questions in a row" in style_prompt
    assert "Do not ask a question after a refusal" in style_prompt


def test_whatsapp_style_prompt_uses_local_fallback_when_langfuse_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("APP_ENV", "production")

    prompt, style = get_whatsapp_style_prompt_text()

    assert isinstance(prompt, str)
    assert "WhatsApp" in style
    assert "Do not include JSON" in style


def test_whatsapp_style_prompt_compiles_remote_text_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTextPrompt:
        def compile(self):
            return "Remote WhatsApp style."

    monkeypatch.setattr(
        "app.agent.prompts.definitions.get_langfuse_prompt",
        lambda *args, **kwargs: FakeTextPrompt(),
    )

    prompt, style = get_whatsapp_style_prompt_text()

    assert isinstance(prompt, FakeTextPrompt)
    assert style == "Remote WhatsApp style."


def _load_bootstrap_prompts_module():
    script_path = Path("scripts/bootstrap_langfuse_prompts.py")
    spec = importlib.util.spec_from_file_location("bootstrap_langfuse_prompts", script_path)
    assert spec is not None
    assert spec.loader is not None
    bootstrap_prompts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap_prompts)
    return bootstrap_prompts


class FakePromptSyncClient:
    def __init__(
        self,
        remote_prompt=None,
        *,
        get_error: Exception | None = None,
        create_error: Exception | None = None,
    ) -> None:
        self.remote_prompt = remote_prompt
        self.get_error = get_error
        self.create_error = create_error
        self.get_calls: list[dict[str, object]] = []
        self.created_prompts: list[dict[str, object]] = []
        self.promoted_prompts: list[dict[str, object]] = []
        self.flushed = False

    def get_prompt(self, name, **kwargs):
        from langfuse.api import NotFoundError

        self.get_calls.append({"name": name, **kwargs})
        if self.get_error is not None:
            raise self.get_error
        if self.remote_prompt is None:
            raise NotFoundError(body={"message": "Prompt not found"})
        return self.remote_prompt

    def create_prompt(self, **kwargs):
        if self.create_error is not None:
            raise self.create_error
        self.created_prompts.append(kwargs)
        return SimpleNamespace(version=3)

    def update_prompt(self, **kwargs):
        self.promoted_prompts.append(kwargs)

    def flush(self):
        self.flushed = True


def _run_prompt_sync(
    monkeypatch: pytest.MonkeyPatch,
    client: FakePromptSyncClient,
    *,
    name: str = WHATSAPP_STYLE_PROMPT_NAME,
    extra_args: list[str] | None = None,
) -> int:
    bootstrap_prompts = _load_bootstrap_prompts_module()

    monkeypatch.setattr(
        "app.observability.langfuse.get_langfuse_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "sys.argv",
        ["bootstrap_langfuse_prompts.py", "--name", name, *(extra_args or [])],
    )
    return bootstrap_prompts.main()


def test_prompt_sync_builds_ephemeral_settings_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://langfuse.example.com")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    bootstrap_prompts = _load_bootstrap_prompts_module()

    settings = bootstrap_prompts._build_prompt_sync_settings()

    assert settings.langfuse_enabled is True
    assert settings.langfuse_base_url == "https://langfuse.example.com"
    assert settings.langfuse_public_key == "pk-lf-test"
    assert settings.langfuse_secret_key == "sk-lf-test"


def test_prompt_sync_creates_staging_version_when_remote_prompt_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakePromptSyncClient()
    bootstrap_prompts = _load_bootstrap_prompts_module()
    monkeypatch.setattr("app.observability.langfuse.get_langfuse_client", lambda: client)
    loaded_env_files: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        bootstrap_prompts,
        "load_dotenv",
        lambda env_file, *, override: loaded_env_files.append((env_file, override)),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "bootstrap_langfuse_prompts.py",
            "--env-file",
            ".env",
            "--name",
            WHATSAPP_STYLE_PROMPT_NAME,
        ],
    )

    assert bootstrap_prompts.main() == 0
    assert loaded_env_files == [(".env", True)]
    assert client.get_calls == [
        {
            "name": WHATSAPP_STYLE_PROMPT_NAME,
            "type": "text",
            "label": "staging",
            "cache_ttl_seconds": 0,
            "max_retries": 0,
        }
    ]
    assert len(client.created_prompts) == 1
    assert client.created_prompts[0]["name"] == WHATSAPP_STYLE_PROMPT_NAME
    assert client.created_prompts[0]["type"] == "text"
    assert client.created_prompts[0]["labels"] == ["staging"]
    assert client.promoted_prompts == []
    assert client.flushed is True


def test_prompt_sync_creates_staging_version_when_remote_content_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_prompt = SimpleNamespace(
        prompt="An older WhatsApp style prompt.",
        version=2,
    )
    client = FakePromptSyncClient(remote_prompt)

    assert _run_prompt_sync(monkeypatch, client) == 0
    assert len(client.created_prompts) == 1
    assert client.created_prompts[0]["labels"] == ["staging"]


def test_prompt_sync_skips_semantically_equal_remote_chat_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = {definition.name: definition for definition in get_prompt_definitions()}
    local_prompt = definitions[CLASSIFIER_PROMPT_NAME].prompt
    assert isinstance(local_prompt, list)
    sdk_normalized_prompt = []
    for message in local_prompt:
        sdk_message = dict(reversed(list(message.items())))
        sdk_message["type"] = "message"
        sdk_normalized_prompt.append(sdk_message)
    remote_prompt = SimpleNamespace(prompt=sdk_normalized_prompt, version=7)
    client = FakePromptSyncClient(remote_prompt)

    assert _run_prompt_sync(monkeypatch, client, name=CLASSIFIER_PROMPT_NAME) == 0
    assert client.created_prompts == []
    assert client.promoted_prompts == []
    assert client.flushed is True


def test_prompt_sync_normalizes_remote_chat_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = {definition.name: definition for definition in get_prompt_definitions()}
    local_prompt = definitions[RESPONDER_PROMPT_NAME].prompt
    assert isinstance(local_prompt, list)
    sdk_normalized_prompt = []
    for message in local_prompt:
        sdk_message = dict(message)
        if sdk_message.get("type") != "placeholder":
            sdk_message["type"] = "message"
        sdk_normalized_prompt.append(sdk_message)
    client = FakePromptSyncClient(SimpleNamespace(prompt=sdk_normalized_prompt, version=8))

    assert _run_prompt_sync(monkeypatch, client, name=RESPONDER_PROMPT_NAME) == 0
    assert client.created_prompts == []


def test_prompt_sync_records_git_source_metadata_and_content_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/sdr-template")
    monkeypatch.setenv("GITHUB_SHA", "obsolete-event-sha")
    monkeypatch.setenv("GITHUB_RUN_ID", "987654")
    monkeypatch.setenv("PROMPT_SYNC_COMMIT_SHA", "abc123def456")
    client = FakePromptSyncClient()

    assert _run_prompt_sync(monkeypatch, client) == 0
    definitions = {definition.name: definition for definition in get_prompt_definitions()}
    definition = definitions[WHATSAPP_STYLE_PROMPT_NAME]
    canonical_content = json.dumps(
        {
            "name": definition.name,
            "prompt": definition.prompt,
            "type": definition.type,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    expected_hash = hashlib.sha256(canonical_content).hexdigest()
    assert client.created_prompts[0]["config"]["_source"] == {
        "content_sha256": expected_hash,
        "repository": "acme/sdr-template",
        "commit_sha": "abc123def456",
        "workflow_run_id": "987654",
    }


def test_prompt_sync_promotes_unchanged_staging_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = {definition.name: definition for definition in get_prompt_definitions()}
    definition = definitions[WHATSAPP_STYLE_PROMPT_NAME]
    client = FakePromptSyncClient(SimpleNamespace(prompt=definition.prompt, version=11))

    assert (
        _run_prompt_sync(
            monkeypatch,
            client,
            extra_args=["--promote-production"],
        )
        == 0
    )
    assert client.created_prompts == []
    assert client.promoted_prompts == [
        {
            "name": WHATSAPP_STYLE_PROMPT_NAME,
            "version": 11,
            "new_labels": ["staging", "production"],
        }
    ]


def test_prompt_sync_promotes_new_staging_version(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakePromptSyncClient()

    assert (
        _run_prompt_sync(
            monkeypatch,
            client,
            extra_args=["--promote-production"],
        )
        == 0
    )
    assert client.created_prompts[0]["labels"] == ["staging"]
    assert client.promoted_prompts == [
        {
            "name": WHATSAPP_STYLE_PROMPT_NAME,
            "version": 3,
            "new_labels": ["staging", "production"],
        }
    ]


@pytest.mark.parametrize("failure_stage", ["read", "create"])
def test_prompt_sync_returns_error_for_remote_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_stage: str,
) -> None:
    error = RuntimeError("Langfuse is unavailable")
    client = FakePromptSyncClient(
        get_error=error if failure_stage == "read" else None,
        create_error=error if failure_stage == "create" else None,
    )

    assert _run_prompt_sync(monkeypatch, client) == 1
    assert "Langfuse is unavailable" in capsys.readouterr().err
    assert client.promoted_prompts == []


def test_build_langchain_chat_prompt_attaches_langfuse_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePrompt:
        def get_langchain_prompt(self):
            return [("system", "Ok"), ("human", "{latest_user_message}")]

    monkeypatch.setattr(
        "app.observability.langfuse.get_langfuse_prompt",
        lambda *args, **kwargs: FakePrompt(),
    )

    prompt, template = build_langchain_chat_prompt(
        "agent/test",
        fallback_messages=[{"role": "system", "content": "fallback"}],
    )

    assert isinstance(prompt, FakePrompt)
    assert template.metadata["langfuse_prompt"] is prompt
