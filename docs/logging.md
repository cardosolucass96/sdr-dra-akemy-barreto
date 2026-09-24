# Logging

Este documento define o contrato de logs da aplicacao.

Logs e traces nao sao a mesma coisa:

- logs explicam eventos operacionais do runtime HTTP e das integracoes;
- traces Langfuse explicam a execucao do agente, chamadas LLM, prompts e fluxo conversacional.

Use os dois de forma complementar, sem duplicar tudo nos dois lugares.

## Formato

A configuracao operacional e salva no snapshot de runtime, pelo painel `/settings`:

- `log_level`: nivel minimo, por padrao `INFO`;
- `log_format`: `json` ou `text`;
- `log_inbound_payloads`: quando `true`, registra payload inbound validado e sanitizado.

As alteracoes valem para as proximas requests. O contrato de acesso e persistencia fica em
[`configuracao-runtime.md`](configuracao-runtime.md).

O Coolify e runtimes containerizados coletam logs de `stdout`/`stderr`, entao a aplicacao nao
escreve arquivos de log dentro do container.

Para diagnostico inicial, selecione `text` e `DEBUG` no painel; o payload inbound bruto deve
continuar desligado. Em plataformas com coletor estruturado, selecione `json`; os mesmos campos
sao preservados.

Use `text` para leitura local no terminal. Nesse modo, o formatter encurta IDs longos,
agrupa partes de mensagens e oculta alguns campos repetitivos quando eles nao ajudam na
linha; os campos continuam disponiveis no formato JSON. As cores so sao ativadas quando a
saida e um TTY, entao `docker logs` e o painel do Coolify nao recebem sequencias ANSI.

Os logs de acesso do Uvicorn passam pelo mesmo formatter da aplicacao. A rota e a query string
sao normalizadas para evitar que parametros sensiveis aparecam no log; probes bem-sucedidos de
`/health`, `/ready`, `/docs` e `/openapi.json` sao ocultados, mas respostas `4xx`/`5xx` continuam
visiveis.

O script `make local` tambem reduz ruido do servidor por padrao:

- `UVICORN_LOG_LEVEL=warning`;
- `UVICORN_ACCESS_LOG=false`.

Para diagnosticar o servidor HTTP diretamente, rode com override no shell:

```bash
UVICORN_LOG_LEVEL=info UVICORN_ACCESS_LOG=true make local
```

## Payload Inbound

Para uma investigacao local controlada, `LOG_INBOUND_PAYLOADS=true` inclui `raw_payload` no
evento `pipefacil.webhook.received`.

Esse payload e:

- o JSON validado pelo Pydantic;
- sanitizado para nao expor `downloadUrl`, base64 ou arquivo bruto de midia;
- preservado com marcadores como `download_url_present=true` quando a midia veio com URL
  assinada.

Use somente quando houver necessidade operacional. Em producao, mantenha
`LOG_INBOUND_PAYLOADS=false` salvo durante uma investigacao controlada e curta.

## Campos

Logs estruturados devem usar `extra`, nao interpolar tudo na mensagem.

Bom:

```python
LOGGER.info(
    "pipefacil.outbound.delivered",
    extra={
        "pipeline_step": "pipefacil.outbound.delivered",
        "thread_id": thread_id,
        "status_code": status_code,
    },
)
```

Evite:

```python
LOGGER.info("Delivered to %s with token %s.", phone, api_key)
```

Campos recomendados:

- `pipeline_step`: etapa estavel do fluxo;
- `pipeline_run_id`: correlacao do evento Pipefacil, usando `message.externalId` ou `message.id`;
- `thread_id`: correlacao da conversa;
- `user_id`: identificador estavel quando permitido;
- `event_type`: tipo do evento inbound;
- `message_type`: tipo da mensagem inbound (`text`, `image`, `audio`, `sticker`, `file`,
  etc.);
- `message_id`: ID interno da mensagem;
- `external_message_id`: ID externo da mensagem;
- `has_media`: indica se a mensagem veio com objeto `media`;
- `media_id`, `media_type`, `media_mime_type`, `media_size`, `media_duration`: resumo seguro
  de midia inbound quando esses campos existirem no payload;
- `media_keys`: nomes das chaves recebidas dentro de `media`, sem logar URLs ou arquivo bruto;
- `transcription_length`: tamanho da transcricao de audio inbound, sem logar o texto
  transcrito;
- `specialist_name`, `specialist_status`, `specialist_confidence`,
  `specialist_error_code`: envelope seguro de especialistas OpenAI Agents SDK;
- `delivery_status`: resultado da entrega outbound;
- `message_part_index`: indice 1-based da parte outbound enviada;
- `message_part_count`: quantidade total de partes outbound derivadas de `response_parts`;
- `message_part_type`: `text`, `image`, `video`, `audio` ou `document`;
- `media_content_type`: content type publico da midia outbound selecionada;
- `generated_audio_media_id`, `generated_audio_content_type`,
  `generated_audio_text_length`: resumo seguro de audio gerado dinamicamente, sem URL;
- `generated_audio_attempt_count`: quantidade de tentativas usadas na geracao;
- `generated_audio_explicit`: indica se o audio foi pedido explicitamente pelo plano do
  agente ou acionado pela regra automatica;
- `pipefacil_idempotency_store`: adaptador ativo (`InMemoryMessageIdempotencyStore` ou
  `PostgresMessageIdempotencyStore`);
- `pipefacil_idempotency_key`: hash SHA-256 da identidade do webhook, nunca os IDs crus
  concatenados;
- `deal_seq` e `pipefacil_stage_id`: direcao explicita de uma atualizacao de etapa;
- `upstream_status_code`: status final de falha do provedor de audio, quando existir;
- `request_id`: request ID de API externa;
- `status_code`: status HTTP externo;
- `error_code`: codigo estavel de erro;
- `http_method`: metodo HTTP;
- `http_path`: rota HTTP, de preferencia template;
- `duration_ms`: duracao da request HTTP no boundary da aplicacao;
- `content_type`, `content_encoding` e `content_length`: resumo HTTP para request invalido
  ou webhook investigado;
- `user_agent`: agente HTTP chamador, mascarado/truncado pelo formatter quando necessario;
- `request_header_names`: nomes de headers recebidos em `/events/*`, sem valores sensiveis;
- `validation_error_count`, `validation_error_locations`, `validation_error_types`: resumo
  seguro de falha de schema/validacao antes da rota;
- `app_env` e `app_version`: lifecycle da aplicacao;
- `checkpointer`: tipo de persistencia ativa.

Eventos inbound multimodais relevantes:

- `http.request.started`: entrada de request em `/events/*`, antes de parsear JSON;
- `http.request.completed`: saida HTTP para `/events/*` ou qualquer status `>=400`;
- `http.request.body_decode_failed`: body com `Content-Encoding` nao suportado ou invalido;
- `pipefacil.inbound.media_downloaded`: midia ou arquivo baixado e validado com
  tipo/tamanho;
- `pipefacil.inbound.audio_transcribed`: audio convertido/transcrito, sem texto literal;
- `pipefacil.inbound.media_failed`: falha recuperavel de download/conversao/transcricao,
  normalmente seguida de resposta fallback ao contato.

Eventos de robustez do Pipefacil:

- `pipefacil.webhook.idempotency_memory_store`: fallback local ativo; restart e replicas
  diferentes perdem a garantia compartilhada;
- `pipefacil.inbound.duplicate_ignored`: webhook ja reivindicado dentro do TTL;
- `pipefacil.inbound.contact_without_lead_ignored`: contato interno ou deal removido;
- `pipefacil.deal.stage_update_started`, `pipefacil.deal.stage_update_completed` e
  `pipefacil.deal.stage_update_failed`: extensao direcional de etapa, sem corpo externo ou
  credenciais.

Eventos de especialistas:

- `specialist.run.started`: inicio de execucao do especialista, sem prompt/historico literal;
- `specialist.run.completed`: resultado estruturado disponivel para o responder;
- `specialist.run.failed`: falha conhecida no SDK, limite de turns, API key ou registry;
- `specialist.run.skipped`: especialista nao necessario ou desativado por feature flag.

## Niveis

- `DEBUG`: detalhe operacional adicional; fica ligado no perfil de producao deste projeto para
  facilitar diagnostico sem registrar prompt, historico, payload bruto ou credenciais.
- `INFO`: eventos operacionais esperados, como startup concluido e entrega outbound enviada.
- `WARNING`: degradacao recuperavel, retry ou estado incompleto que nao impede resposta.
- `ERROR`: falha operacional que precisa investigacao, sem duplicar stack trace.
- `EXCEPTION`: erro inesperado ou boundary final onde a stack trace e util.

Regra pratica: uma falha deve ter um log principal. Evite logar o mesmo erro em todas as
camadas.

## Dados Sensiveis

Nunca logue:

- chaves de API;
- tokens;
- senhas;
- Authorization headers;
- payload inbound completo sem `LOG_INBOUND_PAYLOADS=true` e sem sanitizacao de midia;
- corpo de mensagem do usuario quando nao for indispensavel;
- telefone, email ou documento sem mascaramento.

Mesmo quando `LANGFUSE_PIPEFACIL_USER_ID_MODE=contact_name_phone` habilita identidade
reconhecivel no atributo `user.id` do Langfuse, logs continuam passando pelo mascaramento
normal. O opt-in nao autoriza telefone cru em `stdout`.

O formatter aplica mascaramento defensivo para campos sensiveis, emails e telefones, mas
isso e uma ultima camada de protecao. Valores criados com `raw_log_value(...)` pulam esse
masking de proposito e devem ser usados apenas para payload inbound quando a flag explicita
estiver ligada.

## Onde Logar

### `src/app/main.py`

Responsavel por:

- startup;
- shutdown;
- excecoes inesperadas de request.

### `src/app/application/`

Responsavel por eventos de caso de uso:

- entrega outbound concluida;
- entrega outbound falhou;
- fluxos degradados.

### `src/app/integrations/`

Pode normalizar erros e IDs de request externos, mas deve evitar log duplicado quando a
camada `application` ja registra o resultado operacional.

### `src/app/agent/`

Nodes e chains nao devem logar prompt completo, historico de conversa ou payload LLM.

Quando precisar de investigacao do agente, prefira Langfuse para trace/model/prompt e logs
somente para eventos operacionais de alto nivel.

## Logs Atuais

Hoje a aplicacao registra:

- startup iniciado/concluido;
- shutdown concluido;
- erro inesperado de request;
- entrada/saida HTTP de `/events/*`, incluindo `Content-Encoding`;
- falha de descompactacao de body `gzip`/`deflate`;
- rejeicao de schema/validacao HTTP antes da rota;
- recebimento, aceite, conclusao ou falha do processamento em background do webhook Pipefacil;
- recebimento/rejeicao de mensagem Pipefacil com resumo de tipo e midia;
- resolucao de inbound Pipefacil;
- inicio e conclusao da execucao do agente;
- inicio do outbound Pipefacil;
- resposta outbound Pipefacil entregue, com indice e total quando a resposta tem multiplas
  partes;
- falha outbound Pipefacil com `thread_id`, `error_code`, `status_code`, `request_id`,
  indice da parte e total de partes.

## Checklist Antes de Adicionar um Log

1. Esse evento ajuda a operar producao?
2. Existe um campo de correlacao, como `thread_id` ou `request_id`?
3. A mensagem e estavel e sem dado dinamico sensivel?
4. O dado dinamico foi para `extra`?
5. Nao existe outro log registrando a mesma falha?
6. O evento pertence a log ou a trace Langfuse?
