# SDR Pipefacil

<p align="center">
  <img src="docs/assets/sdr-pipefacil-hero-v2.jpg" alt="Pipefacil virtual SDR agent powered by Python, LangGraph, and Langfuse" width="100%">
</p>

Official Pipefacil template for SDR agents integrated with Pipefacil.

This base provides an HTTP runtime with `FastAPI`, conversational orchestration with
`LangGraph`, observability and prompt management with `Langfuse`, inbound/outbound
integration with the Pipefacil public API, and short-term memory persisted by `thread_id`.

The goal is to serve as a starting point for sales agents, SDRs, lead triage,
qualification, assisted support, and automations connected to Pipefacil, while keeping a
clear separation between API, graph, business rules, integrations, and observability.

## What This Repository Provides

- HTTP API for chat, webhooks, health checks, and state lookup.
- LangGraph graph with separate nodes for classification and response.
- Short-term conversation memory using `thread_id`.
- In-memory checkpointer for local environments and required Postgres persistence in
  production.
- Pipefacil integration to receive messages and send replies.
- WhatsApp-ready `response_messages` derived from the canonical `response_text`.
- Ordered `response_parts` for Insomnia/debug, including outbound media selected by ID.
- Versioned outbound media catalog for images, videos, audio, and documents.
- Versioned prompts in Langfuse.
- Tests for the API, application, runtime, graph, observability, and Pipefacil integration.
- Docker Compose for a self-hosted production environment.

## Stack

- `FastAPI` as the application's HTTP runtime.
- `LangGraph` to model the agent flow.
- `LangChain + OpenAI` in nodes that use an LLM.
- `Langfuse` for traces, callbacks, and versioned prompts.
- Optional `PostgresSaver` for persistent checkpointing.
- `langgraph dev` only for Studio and local visual debugging.

## Structure

```text
src/app/
  api/              HTTP routes, schemas, FastAPI dependencies, and settings panel
  agent/            State, prompts, nodes, graph, runtime, and agent service
  application/      Application use cases and request-scoped runtime settings
  core/             Bootstrap secrets and typed configuration
  integrations/     External clients, contracts, and mappings
  outbound_media/    Versioned outbound media catalog and safe prompt views
  observability/    Langfuse, callbacks, and trace flushing

docs/               Internal architecture and best-practice guides
scripts/            Bootstraps and operational scripts
tests/              Automated tests
```

## Create an Agent for a New Client

Use GitHub's **Use this template** action so the client receives an independent repository
without inheriting this template's Git history. Do not start by cloning this repository and
pushing back to its `origin`.

From the GitHub UI:

1. Open this repository and select **Use this template**.
2. Create a private repository named `sdr-<client>`.
3. Clone the new repository into its own local folder.

Equivalent GitHub CLI command:

```bash
gh repo create cardosolucass96/sdr-<client> \
  --private \
  --template cardosolucass96/sdr-pipefacil \
  --clone
```

Before the first deploy, configure the Postgres schema, webhook secret, Pipefacil key,
settings-panel secrets, and deployment URL. After the first startup, set the client name and
slug in `/settings`. Use a
separate Langfuse project per client; if projects must be shared, rename the canonical prompt
prefixes before bootstrapping so one client cannot move another client's `production` label.
Keep bootstrap credentials only in `.env` or in the deployment platform.

## Getting Started

Create the virtual environment and install dependencies:

```bash
make install
```

Bootstrap configuration is defined by `BootstrapSettings` in
[`src/app/core/config.py`](src/app/core/config.py). Run `make env-init`, then fill the local
ignored `.env` or the
deployment secret manager with the required secrets, using `.env.example` as a safe reference.
Configure non-sensitive SDR behavior after startup through `/settings`.

For local development with `langgraph dev`:

```bash
make dev
```

This command uses [`langgraph.json`](langgraph.json) and loads `.env`.

## Runtime Configuration

The configuration has two deliberate boundaries:

- Bootstrap: infrastructure and secrets read when the process starts.
- Operational: non-sensitive SDR behavior persisted in Postgres and changed through the protected `/settings` panel.

Bootstrap inputs include `DATABASE_URL`, OpenAI and Pipefacil credentials, the Pipefacil
signature secret, Langfuse credentials, optional ElevenLabs credentials,
`SETTINGS_ADMIN_KEY_HASH`, and `SETTINGS_SESSION_SECRET`. In production, the database,
OpenAI, Pipefacil, webhook signature, and the two panel secrets are mandatory.

After the first startup, access `/settings/login` to configure the SDR name, model and
reasoning effort, Pipefacil limits, audio behavior, Langfuse label, and log settings. Changes
are validated, versioned, and used by the next incoming message without restarting the process.

Use [`docs/configuracao-runtime.md`](docs/configuracao-runtime.md) for the exact security
model, initial setup, the list of bootstrap secrets, and the settings-panel behavior. The
versioned [`.env.example`](.env.example) is only a safe starting point: credentials go in the
local ignored `.env` or deployment secret manager, never in Git.

Main commands:

```bash
make dev          # Local LangGraph Studio/runtime
make local        # Local FastAPI + Cloudflare Tunnel
make app          # FastAPI only
make prod         # Docker Compose
```

## Agent Configuration

New SDRs default to `gpt-5.6-luna` with `low` reasoning effort. In production, select this
and the other non-sensitive agent settings in `/settings`; do not rely on an environment
override for operational behavior. For GPT-5.6 reasoning turns, the chains use OpenAI native
JSON Schema structured output; with reasoning disabled they use function calling for
compatibility.

To stop the Docker stack:

```bash
make compose-down
```

## Deployment

Use the [`Dockerfile`](Dockerfile) for containerized deployment. The production API keeps
`/chat`, thread inspection, Swagger, ReDoc, and live OpenAPI disabled; health, readiness,
generated audio, the signed Pipefacil webhook, and the protected `/settings` panel remain
available.

Startup requires `DATABASE_URL`, `OPENAI_API_KEY`, `PIPEFACIL_API_KEY`,
`PIPEFACIL_WEBHOOK_SIGNATURE_SECRET`, `SETTINGS_ADMIN_KEY_HASH`, and
`SETTINGS_SESSION_SECRET`; webhook signature validation must remain enabled. Run
`make db-setup` before the first start, then use `/settings/login` to complete the
non-sensitive operational configuration.

## Configuration Reference

The environment supplies only bootstrap secrets and infrastructure. The operational values below
are configured in `/settings` and stored in Postgres; they are listed here to explain their
behavior, not as an instruction to put them in an environment file.

- **Agent and Pipefacil:** SDR name and slug, OpenAI model/reasoning/transcription,
  specialist feature flag, Pipefacil base URL and history path, HTTP timeout, inbound-media
  limit, webhook-idempotency TTL, and token limit per lead.
- **Audio:** generated-audio and automatic-audio flags, size and TTL limits, public base URL,
  voice/model/output format, retry, and voice controls. The ElevenLabs API key remains a
  bootstrap secret.
- **Langfuse and logs:** Langfuse enablement, host, tracing environment and prompt label,
  debug flag, Pipefacil user-ID policy, and log level/format/payload flag. Langfuse API keys
  remain bootstrap secrets.
- **Process infrastructure:** database/checkpointer behavior, Pipefacil signature policy,
  outbound-media catalog, generated-audio storage, Cloudflare Tunnel, and the two
  settings-panel secrets. These do not belong in the administrative UI.

The detailed boundary, defaults, validation and first-start procedure are documented in
[`docs/configuracao-runtime.md`](docs/configuracao-runtime.md).

## API

Available endpoints:

- `GET /health`
- `GET /ready`
- `GET /generated-audio/{filename}`
- `POST /chat`
- `POST /conversations/resume`
- `GET /threads/{thread_id}/state`
- `POST /events/message-received`

The administrative routes under `/settings` are intentionally excluded from OpenAPI. They use
their own login, CSRF protection, and signed session; see
[`docs/configuracao-runtime.md`](docs/configuracao-runtime.md).

`GET /health` is a simple liveness check. `GET /ready` verifies that the runtime loaded the
graph and checkpointer and, when `DATABASE_URL` is configured, validates Postgres
connectivity plus the configured checkpoint schema. The Docker `HEALTHCHECK` uses
`/ready`.

`POST /chat` receives `thread_id` in the request body. This value is used as the Langfuse
`session_id` and as the LangGraph memory key. The response keeps `response_text` as the
canonical reply, returns `response_messages` as the deterministic WhatsApp text split, and
returns `response_parts` as the ordered delivery/debug plan.

`POST /conversations/resume` loads the conversation history from the configured Pipefacil
endpoint, runs the same LangGraph flow, and optionally sends the generated response through
Pipefacil. Use `context` for internal operational guidance that should influence the next
reply without being treated as a lead message. The endpoint accepts `deal_seq`, `deal_id`,
`contact_id`, or `channel_id` as the history filter and requires `thread_id` for LangGraph
memory. `history_limit` accepts 1 to 500 messages and defaults to `100`.
`send_response` defaults to `true`; set it to `false` for a dry run.

Example:

```json
{
  "thread_id": "deal-example-001",
  "deal_seq": 100,
  "recipient_phone": "+5511000000001",
  "sender_phone_number_id": "111111111111111",
  "context": "Faz 3 dias que ele nao responde e ficou de passar o cartao.",
  "send_response": true
}
```

The resume endpoint uses the same webhook signature validation as the inbound webhook.

`POST /events/message-received` receives the inbound Pipefacil payload, normalizes `text`,
`image`, `sticker`, `audio`, and file messages, runs the agent, derives `response_parts`,
and sends each part back in order. Text and outbound media both use `POST /api/v1/messages`
with the catalog `mediaLink` resolved only in the application/integration boundary.
The endpoint accepts Pipefacil's `data.messages` list, preserving every inbound item in received
order. The legacy `data.message` spelling remains supported for older deliveries. Every item is
deduplicated by its external ID/ID, and new items are sent to the SDR in one consolidated turn
that produces one response. A retry that mixes already-processed and new items includes only the
new items.
When generated audio is enabled, the responder chooses text for exact/copyable information,
audio for spoken explanations, or a hybrid reply containing both. The optional legacy
length rule runs only when `GENERATED_AUDIO_AUTO_ENABLED=true`. For audio and hybrid replies,
the webhook sends the useful text portion first, then an `audio` media part backed by a
temporary public URL under `/generated-audio/...`.
If generation fails, explicit audio requests fall back to the spoken script as text, while
automatic generation keeps the original text reply.
Images and stickers are sent to the LLM as multimodal content blocks. Files/documents,
including PDFs, are sent as `file` content blocks with `mime_type` and `filename`, respecting
`PIPEFACIL_MEDIA_MAX_BYTES`. `audio/ogg` audio is converted with `ffmpeg`, transcribed, and
sent as text with `Message type: audio` and the transcript.

After the webhook is authenticated and its local payload checks pass, the endpoint returns
HTTP `200` with `status=accepted`. The remaining work runs in a FastAPI background task, in
this order:

1. A contact without `deal` is treated as an internal Pipefacil contact and produces
   `contact_without_lead_ignored`. No deal lookup is attempted.
2. The message is claimed by `event + channel + externalId/id`. A repeated delivery produces
   `duplicate_message_ignored`. Controlled outcomes, including outbound failure, keep the
   claim; only unexpected exceptions release it.
3. For an existing lead, a text message whose trimmed body is exactly `/reset` (case
   insensitive) clears every persisted LangGraph checkpoint for that thread and sends a
   confirmation without calling the agent, processing media, or consuming the lead token
   budget. If the checkpoint cannot be deleted, the webhook sends a safe retry message instead.
4. For an existing lead, every other supported inbound message is normalized and sent to the
   agent; no deal custom field is consulted to decide whether the agent replies.

With `DATABASE_URL`, idempotency uses `pipefacil_webhook_idempotency` in the same configured
schema and shares the LangGraph connection pool. Without a database, non-production
environments use memory: process restart clears claims and multiple replicas do not share
them. Production requires Postgres and does not start with this fallback.

If `PIPEFACIL_MAX_TOKENS_PER_LEAD` is greater than zero, the webhook then reads the latest
LangGraph thread state for the lead and estimates the persisted conversation tokens plus the
current inbound message. When the total reaches the configured limit, the application stops
before media normalization, does not call the SDR, and does not send an outbound message.

The integration also exposes the application use case
`move_pipefacil_deal_stage(deal_seq, target_stage_id)` for client-specific workflows. It
validates and sends an explicit target stage, but this base defines no default stage,
endpoint, LLM tool or automatic movement.

Stickers are treated as WebP images. If a sticker is animated, this version does not extract
frames or describe the full motion; it is still sent to the model as an image.

Not every model accepts every file type. The inbound flow downloads and forwards any
attachment within the configured limit, but the model may reply that it cannot read a
specific format.

The Dockerfile installs `ffmpeg` in the image. In local execution, the binary must be
available in `PATH` to transcribe audio.

When `PIPEFACIL_WEBHOOK_SIGNATURE_ENABLED=true` and
`PIPEFACIL_WEBHOOK_SIGNATURE_SECRET` is configured, the endpoint requires the CRM signature
in `X-Pipefacil-Signature-256` and the `X-Pipefacil-Timestamp` header. It validates
`HMAC-SHA256(secret_utf8, timestamp + "." + json_body)` and expects the signature header as
`sha256=<64 lowercase hexadecimal characters>`. The secret is used as its literal UTF-8 text,
even when it looks like a hexadecimal string. For gzip or deflate requests, Pipefacil signs
the original JSON before compression and the application verifies the decompressed JSON.

Webhooks under `/events/*` accept bodies with `Content-Encoding: gzip` or `deflate`. The
application stores the original wire body and passes the decompressed body to signature
validation and the JSON parser.

Webhook response semantics:

- `200 OK`: the signature, request schema, and IDs passed local validation; for events with a
  deal, the message type and required text are also checked. The body has `status=accepted`,
  while agent and outbound work continue in the background.
- `401 Unauthorized`: the webhook signature is missing or invalid.
- `422 Unprocessable Content`: the request or inbound message failed validation before it
  could be accepted.

The acknowledgement does not report the final outbound result. Inspect
`pipefacil.webhook.processing_completed` and `pipefacil.webhook.processing_failed` logs for
that outcome. Background tasks run in the API process, so an accepted task can be lost if the
process stops before it finishes; use a durable external queue if restart-safe delivery becomes
a requirement.

In `production`, the application fails at startup if `DATABASE_URL`, `OPENAI_API_KEY`,
`PIPEFACIL_API_KEY`, or `PIPEFACIL_WEBHOOK_SIGNATURE_SECRET` is not configured, or if
signature validation is disabled.

### Insomnia

Import [`docs/api/openapi.json`](docs/api/openapi.json) into Insomnia to test the API
manually. Regenerate it after API changes with:

```bash
make openapi
```

See [`docs/api/insomnia.md`](docs/api/insomnia.md) for local environment setup, example
payloads, and webhook signature notes.

## Pipefacil Integration

The integration lives in `src/app/integrations/pipefacil`.

- `client.py`: HTTP client for the public API.
- `contracts.py`: expected integration contracts.
- `conversation.py`: normalization of Pipefacil conversation history into chat messages.
- `mapping.py`: translation between Pipefacil payloads and internal DTOs.

The currently recommended host is:

```text
https://pipefacil-server.matchsales.com.br
```

This is a legacy infrastructure hostname retained by the Pipefacil public API; it is not
Pipefacil's current product name.

## Outbound Media Catalog

Outbound media lives in a versioned JSON catalog at
`src/app/outbound_media/catalog.json`. The default catalog is intentionally empty so the
template does not ship client-specific test assets.

Each entry has `id`, `type`, `title`, `description`, `when_to_use`, `media_url`,
`content_type`, `filename`, and `enabled`.

The responder prompt receives only a safe view: `media_id`, type, title, description, and
when to use it. The LLM never sees `media_url` and cannot send media directly. It returns a
structured plan with `response_text` and `media_choices`; the application validates enabled
IDs, builds `response_parts`, resolves URLs internally, and stops delivery on the first
failed part.

Use [`src/app/outbound_media/catalog.example.json`](src/app/outbound_media/catalog.example.json)
as a starting point and follow
[`docs/catalogo-midias-outbound.md`](docs/catalogo-midias-outbound.md) before enabling real
assets. Only use direct HTTPS file URLs, not storage console/browser URLs. For outbound
audio, use Ogg Opus with `audio/ogg`, mono, 48 kHz, and validate the real delivery before
setting `enabled=true`.

Dynamic generated audio does not need a catalog entry. Set `GENERATED_AUDIO_ENABLED=true`,
configure ElevenLabs, and expose this app through a stable HTTPS base URL. The application
generates speech directly as Ogg Opus, stores it temporarily
under `GENERATED_AUDIO_STORAGE_DIR`, and passes only the generated URL directly to the
Pipefacil delivery client. The LLM never sees generated audio URLs.

The default storage is local to the application instance. Production deployments must use
a single replica, a shared volume, or routing that guarantees `/generated-audio/...` reaches
the instance that created the file. Distributed object storage is not provided by this base.

## LangGraph

The base graph is in `src/app/agent/graph.py`.

Current flow:

```text
START -> classify-intent -> respond -> END
```

When the classifier explicitly requests an internal specialist, the conditional path is:

```text
START -> classify-intent -> delegate-specialist -> respond -> END
```

Common customization points:

- interpret messages into structured facts, questions, objections, corrections, and refusals;
- add qualification nodes;
- query Pipefacil data;
- validate commercial rules;
- record human handoff;
- create actions to change status, tags, tickets, or opportunities in Pipefacil;
- expand the state in `src/app/agent/state.py`.

The agent's internal architecture is documented in
[`docs/arquitetura-agente.md`](docs/arquitetura-agente.md).
Before expanding the scaffold, follow
[`docs/derivacao-agente.md`](docs/derivacao-agente.md): it defines the required node
boundaries and when semantic interpretation must use native structured output rather than
text parsing.
The conversational contract for context, pending goals, consultative sales, and natural
language is documented in
[`docs/modelagem-conversacional.md`](docs/modelagem-conversacional.md).

The base responder is intentionally generic: it provides a professional, approachable, and
adaptive consultative style, but no client product facts, ICP, commercial claims, or sales
stages. Derived SDRs should use AI for semantic interpretation and structured extraction,
then route deterministically over validated state instead of growing regex-based intent
rules.

Summary of the main subfolders:

- `src/app/agent/nodes/`: fine-grained graph steps.
- `src/app/agent/chains/`: prompts, models, and structured outputs.
- `src/app/agent/prompts/`: Langfuse names and local fallbacks.
- `src/app/agent/tools/`: tools exposed to the agent.

## Persistence

- without `DATABASE_URL` outside production: `InMemorySaver`.
- with `DATABASE_URL`: `PostgresSaver` backed by a psycopg `ConnectionPool`.
- in production without `DATABASE_URL`: startup fails before the runtime is built.

Recommended shared Postgres layout:

```env
DATABASE_URL=postgresql://user:password@db.example.com:5432/postgres/sdr_cliente
```

`postgres` is the database. The optional final path segment selects the schema; a deploy has one
SDR/schema and never needs a separate schema environment variable.

The Postgres checkpointer pool validates a connection before handing it to LangGraph and uses
the schema inferred from `DATABASE_URL`. This prevents webhook runs from reusing a stale closed
connection after Postgres idle timeouts.

Before the first production startup, configure the database and prepare the schema:

```bash
make db-setup
```

## Langfuse

Versioned prompts used by this base:

- `agent/classifier`
- `agent/responder`
- `agent/style/whatsapp`

These short names are safe when every client has its own Langfuse project. When multiple
clients share one project, change the names to a client-specific namespace before the first
bootstrap; labels such as `production` are unique per prompt name and otherwise one client
could replace another client's active prompt.

The local observability, tracing, labels, and prompt promotion contract is documented in
[`docs/observabilidade-langfuse.md`](docs/observabilidade-langfuse.md).

Changes to conversational behavior should be compared against the versioned golden dataset.
The deterministic gate validates prompt and dataset contracts; response quality remains a
human review in Langfuse rather than an inference-based CI check.

Prompt bootstrap:

```bash
.venv/bin/python scripts/bootstrap_langfuse_prompts.py \
  --env-file .env
```

The script reads the current `staging` version without cache and creates a new version only
when canonical content changed. After CI quality checks, a successful push to `main`
synchronizes `staging` and automatically assigns both `staging` and `production` to that
version, using the configured GitHub secrets and `LANGFUSE_BASE_URL` variable. The workflow
records the source commit and skips stale revisions when a newer commit reaches `main`.

Manual synchronization and promotion, for operational recovery:

```bash
.venv/bin/python scripts/bootstrap_langfuse_prompts.py \
  --env-file .env \
  --promote-production
```

## Tests and Quality

```bash
make quality
make test
make lint
make format
make test-cov
make pre-commit-install
pre-commit run --all-files
```

`make quality` is the complete deterministic gate: it checks architecture and changed-code
complexity, runs Ruff and formatting verification, executes the suite with branch coverage,
and requires at least 85% line and branch coverage on executable Python lines in the diff.
The detailed contract is in [`docs/quality-gates.md`](docs/quality-gates.md).

The local `pre-commit` hook remains fast and runs file hygiene plus Ruff fixes and formatting.
GitHub Actions runs the complete gate on Python 3.12 and only synchronizes/promotes Langfuse
prompts after the quality job succeeds on a push to `main`.

## AI Development Context

This template versions documentation context for assistants and editors that support MCP:

- `.mcp.json`
- `.cursor/mcp.json`
- `.vscode/mcp.json`
- `AGENTS.md`

All of them point to the official documentation used by the template:

```text
https://docs.langchain.com/mcp
https://langfuse.com/api/mcp
```

Before changing LangGraph/LangChain or Langfuse, also consult:

```text
https://docs.langchain.com/llms.txt
https://langfuse.com/llms.txt
```

These files are development-only. They are not part of the API runtime or production
dependencies.

Details are available in
[`docs/contexto-desenvolvimento-langgraph.md`](docs/contexto-desenvolvimento-langgraph.md)
and
[`docs/contexto-desenvolvimento-langfuse.md`](docs/contexto-desenvolvimento-langfuse.md).

## Checklist for Creating a New Agent

1. Create a new private repository with **Use this template**; never reuse this `origin`.
2. Configure the database, bootstrap secrets, deployment URLs, and settings-panel credentials
   outside Git.
3. After the first startup, set the client name, slug, models, limits, and non-secret runtime
   values through `/settings`.
4. Use a separate Langfuse project or rename prompt namespaces before bootstrapping.
5. Rotate and configure client-specific Pipefacil, webhook, OpenAI, ElevenLabs, Langfuse,
   Postgres, and Cloudflare credentials outside Git.
6. Define the agent's statuses, business rules, escalation rules, and token limits.
7. Define the static client context, allowed commercial claims, conversational facts,
   pending goals, opt-out behavior, and voice.
8. Follow [`docs/derivacao-agente.md`](docs/derivacao-agente.md) to model the client flow:
   persistent state, small node responsibilities, structured semantic schemas, routing, and
   external actions.
9. Expand `AgentState`, nodes, routing, tools, and prompts as required by that client flow.
10. Add golden-dataset cases for interruptions, objections, corrections, and refusal.
11. Replace the outbound media catalog and verify every enabled asset with real delivery.
12. Adjust Pipefacil mappings according to the real Pipefacil payload.
13. Regenerate `docs/api/openapi.json`, run the full test suite, and validate the candidate
    prompt label before merging into `main`, which promotes prompts to production automatically.

Also read:

- [`docs/README.md`](docs/README.md)
- [`docs/arquitetura-aplicacao.md`](docs/arquitetura-aplicacao.md)
- [`docs/arquitetura-agente.md`](docs/arquitetura-agente.md)
- [`docs/derivacao-agente.md`](docs/derivacao-agente.md)
- [`docs/configuracao-runtime.md`](docs/configuracao-runtime.md)
- [`docs/organizacao-do-codigo.md`](docs/organizacao-do-codigo.md)
- [`docs/tratamento-de-excecoes.md`](docs/tratamento-de-excecoes.md)
- [`docs/estrutura-langgraph.md`](docs/estrutura-langgraph.md)
- [`docs/modelagem-conversacional.md`](docs/modelagem-conversacional.md)
- [`docs/contexto-desenvolvimento-langgraph.md`](docs/contexto-desenvolvimento-langgraph.md)
- [`docs/contexto-desenvolvimento-langfuse.md`](docs/contexto-desenvolvimento-langfuse.md)
- [`docs/observabilidade-langfuse.md`](docs/observabilidade-langfuse.md)
- [`docs/logging.md`](docs/logging.md)
- [`docs/api/insomnia.md`](docs/api/insomnia.md)
