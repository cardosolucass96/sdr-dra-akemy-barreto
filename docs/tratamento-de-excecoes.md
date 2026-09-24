# Tratamento de Excecoes

Este documento descreve a politica atual de erros da aplicacao.

## Objetivo

A base segue tres principios:

- erros esperados devem ser classificados de forma explicita;
- erros inesperados devem ser logados e responder `500`;
- o webhook do Pipefacil deve distinguir processamento do agente de entrega outbound.

Nao existe envelope customizado de erro. A API usa o formato padrao do FastAPI com `detail`.

## Taxonomia atual

### Erro de configuracao de runtime

Classe principal:

- [`RuntimeConfigurationError`](../src/app/core/exceptions.py)

Uso atual:

- startup em `production` sem `DATABASE_URL`, `OPENAI_API_KEY`, `PIPEFACIL_API_KEY` ou
  segredo de assinatura;
- startup em `production` com validacao de assinatura desabilitada.
- startup em `production` sem `SETTINGS_ADMIN_KEY_HASH` ou `SETTINGS_SESSION_SECRET`.

Comportamento:

- a aplicacao nao sobe;
- o erro nao e convertido em resposta HTTP porque acontece no startup.

### Erro de payload ou regra de entrada

Classe principal:

- [`PipefacilInboundMessageError`](../src/app/integrations/pipefacil/mapping.py)

Exemplos:

- payload sem texto utilizavel;
- mensagem inbound com tipo nao suportado;
- impossibilidade de resolver `thread_id`.

Comportamento:

- o endpoint `POST /events/message-received` responde `422`;
- o corpo segue o padrao FastAPI com `detail`.

### Erro recuperavel de midia inbound

Classe principal:

- [`PipefacilMediaProcessingError`](../src/app/integrations/pipefacil/mapping.py)

Exemplos:

- `media.downloadUrl` ausente ou invalida;
- download de midia acima de `PIPEFACIL_MEDIA_MAX_BYTES`;
- download de arquivo/documento acima de `PIPEFACIL_MEDIA_MAX_BYTES`;
- falha ao converter `audio/ogg` para `wav` com `ffmpeg`;
- falha de transcricao OpenAI.

Comportamento:

- o webhook nao responde `422`;
- a aplicacao registra `pipefacil.inbound.media_failed`;
- uma resposta fallback curta e enviada ao contato pedindo reenvio ou texto;
- se essa entrega outbound falhar, o erro fica no resultado e nos logs do processamento em
  background; o aceite HTTP ja foi enviado.

### Erro recuperavel de audio gerado

Classe exposta pelo caso de uso:

- [`GeneratedAudioError`](../src/app/application/generated_audio.py)

Codigos estaveis:

- configuracao e entrada: `audio_text_empty`, `generated_audio_public_base_url_missing`,
  `generated_audio_public_base_url_invalid`;
- ElevenLabs: `elevenlabs_api_key_missing`, `elevenlabs_voice_id_missing`,
  `elevenlabs_transport_error`, `elevenlabs_upstream_error`, `elevenlabs_audio_empty`,
  `elevenlabs_content_type_invalid`;
- formato: `generated_audio_content_type_unsupported`;
- FFmpeg: `ffmpeg_missing`, `ffmpeg_conversion_failed`, `ffmpeg_output_empty`;
- filesystem: `generated_audio_storage_error`.

Erros de transporte, HTTP `429` e HTTP `5xx` da ElevenLabs usam ate duas tentativas totais.
Demais respostas `4xx`, corpo vazio e content type invalido falham sem retry. Depois da falha
final:

- pedido explicito de audio vira fallback com o roteiro em texto;
- geracao automatica preserva a resposta textual original;
- o resultado da entrega do fallback fica nos logs do processamento em background;
- o log inclui codigo, status final e quantidade de tentativas, sem roteiro, URL, corpo da
  resposta externa ou credenciais.

No endpoint `GET /generated-audio/{filename}`, nome invalido, arquivo ausente e arquivo
expirado levantam `GeneratedAudioNotFoundError` e respondem `404`. Falhas operacionais de
filesystem nao sao mascaradas como ausencia: elas chegam ao handler central e respondem
`500`.

### Erro operacional de entrega outbound

Classe principal:

- [`PipefacilSendMessageError`](../src/app/integrations/pipefacil/client.py)

Campos relevantes:

- `error_code`
- `status_code`
- `request_id`
- `response_body`

Codigos estaveis atualmente usados em `delivery_error`:

- `pipefacil_api_key_missing`
- `recipient_phone_missing`
- `response_text_empty`
- `response_media_url_missing`
- `response_media_url_invalid`
- `response_media_type_unsupported`
- `pipefacil_transport_error`
- `pipefacil_upstream_error`

Comportamento:

- o agente pode responder com sucesso, mas a entrega outbound falhar;
- respostas com `response_parts` sao enviadas parte por parte, em ordem;
- midias outbound usam somente IDs validados e falham sem expor `media_url`;
- a primeira falha interrompe o envio das partes restantes;
- nesse caso `delivery_status="failed"` e `delivery_error` ficam disponiveis no resultado
  interno e nos logs; a resposta HTTP de aceite nao espera essa conclusao.

### Regras controladas do webhook

Contato sem lead e webhook duplicado nao sao erros HTTP:

- `contact_without_lead_ignored`: `deal` ausente no evento; nenhum lookup, estado, midia,
  LLM ou outbound e acessado depois dessa decisao;
- `duplicate_message_ignored`: a chave `event + channel + externalId/id` ja foi reivindicada
  dentro do TTL; a resposta de negocio e vazia.

Ambos encerram o processamento em background de forma controlada. Somente excecao inesperada
libera uma reivindicacao idempotente.
Resultados controlados, inclusive limite de tokens, fallback de midia e falha outbound,
continuam registrados para impedir reprocessamento ou reenvio parcial.

### Erros de consulta e atualizacao de deal

Classes principais:

- [`PipefacilDealLookupError`](../src/app/integrations/pipefacil/client.py)
- [`PipefacilDealUpdateError`](../src/app/integrations/pipefacil/client.py)

Codigos estaveis compartilhados incluem `pipefacil_api_key_missing`,
`pipefacil_deal_seq_missing`, `pipefacil_transport_error` e `pipefacil_upstream_error`.
Atualizacao de propriedades acrescenta `pipefacil_deal_properties_empty`; movimentacao de
etapa acrescenta `pipefacil_stage_id_missing`. O caso de uso de etapa registra somente seq,
stage ID, codigo, status e request ID, nunca credencial ou corpo externo.

### Erro inesperado

Classe pratica:

- qualquer `Exception` nao tratada explicitamente.

Comportamento:

- log centralizado com stack trace;
- resposta `500`;
- corpo padrao: `{"detail": "Internal Server Error"}`.

### Erros controlados do painel `/settings`

O painel administrativo nao usa o envelope JSON da API publica:

- `401 Unauthorized`: chave de acesso invalida no login;
- `403 Forbidden`: token CSRF ausente ou expirado;
- `409 Conflict`: a configuracao foi salva por outra sessao e a versao do formulario ficou
  antiga;
- `422 Unprocessable Content`: campo operacional invalido.

Essas respostas preservam o formulario HTML e nao revelam segredo ou valor de bootstrap.

## Mapeamento para HTTP

- `200 OK`: webhook autenticado e validado localmente, aceito para processamento em
  background com `status=accepted`.
- `400 Bad Request`: body HTTP invalido antes da validacao do webhook, incluindo
  `Content-Encoding` nao suportado ou gzip/deflate corrompido.
- `401 Unauthorized`: assinatura do webhook ausente ou invalida.
- `404 Not Found`: thread inexistente ou audio gerado invalido, ausente ou expirado.
- `401`, `403`, `409` e `422`: autenticacao, CSRF, concorrencia ou validacao do painel
  `/settings`, em resposta HTML.
- `422 Unprocessable Content`: payload ou regra de entrada invalida no webhook.
- `502 Bad Gateway`: falha conhecida ao consultar o historico ou entregar uma resposta pelo
  Pipefacil em `POST /conversations/resume`.
- `500 Internal Server Error`: erro inesperado em request.

Falhas conhecidas no processamento de midia suportada nao usam `422`; elas geram fallback
conversacional em background.

Requests em `/events/*` com `Content-Encoding: gzip` ou `deflate` sao descompactadas antes
do parser JSON. A assinatura sempre considera o corpo bruto recebido: tanto o formato
Pipefacil `sha256(corpo_bruto + segredo)` quanto os formatos HMAC legados usam esses bytes.

## Politica do webhook Pipefacil

O endpoint [`POST /events/message-received`](../src/app/api/routes/webhooks.py) separa duas
fases:

1. valida assinatura e entrada, agenda o trabalho e responde `200` com `status=accepted`;
2. depois da resposta, processa a mensagem com o agente e tenta entregar o outbound.

O `200` confirma recebimento, nao conclusao. Falhas posteriores sao observadas pelos logs
`pipefacil.webhook.processing_completed` e `pipefacil.webhook.processing_failed`.

## `delivery_status` e `delivery_error`

Os endpoints que devolvem `ChatResponse` agora podem carregar:

- `delivery_status`: `sent`, `failed` ou `null`
- `delivery_error`: codigo estavel ou `null`

Leitura esperada:

- `sent`: fluxo outbound concluido;
- `failed`: fluxo do agente concluiu, mas a entrega outbound nao;
- `null`: nao houve tentativa de entrega outbound ou ela ainda nao concluiu. A resposta de
  aceite do webhook sempre usa `null`; `/chat` tambem nao envia outbound.

## Startup em producao

Em producao, `DATABASE_URL`, `OPENAI_API_KEY`, `PIPEFACIL_API_KEY`,
`PIPEFACIL_WEBHOOK_SIGNATURE_ENABLED=true`, `PIPEFACIL_WEBHOOK_SIGNATURE_SECRET`,
`SETTINGS_ADMIN_KEY_HASH` e `SETTINGS_SESSION_SECRET` sao obrigatorios.

Quando `GENERATED_AUDIO_ENABLED=true`, a aplicacao tambem exige chave, voz e URL publica
HTTPS para o fluxo de audio gerado.

Sem essas configuracoes:

- a app falha no startup;
- o webhook nao entra em modo degradado silencioso.
- o endpoint publico nao aceita eventos sem prova de origem.

Essa validacao acontece em [`src/app/main.py`](../src/app/main.py).

Em producao, falha ao conectar ao Postgres ou criar/acessar a tabela
`pipefacil_webhook_idempotency` ou `sdr_runtime_settings` tambem impede o startup. Isso evita subir uma replica que
parece saudavel mas nao oferece persistencia e idempotencia distribuidas. Apenas execucoes
controladas fora do perfil unico podem usar memoria local sem `DATABASE_URL`, com warning
operacional sobre restart e multiplas replicas.

## Logging

Os logs operacionais seguem o contrato de [`logging.md`](logging.md).

Os logs principais de erro ficam em dois pontos:

- [`src/app/application/pipefacil.py`](../src/app/application/pipefacil.py)
  registra falhas outbound e de audio gerado com contexto seguro, codigo estavel, status e
  quantidade de tentativas.
- [`src/app/api/routes/webhooks.py`](../src/app/api/routes/webhooks.py)
  registra recebimento, rejeicao 422 e conclusao do webhook Pipefacil.
- [`src/app/main.py`](../src/app/main.py)
  registra excecoes inesperadas de request antes de responder `500`.

Boa pratica para evolucoes futuras:

- manter codigos de erro estaveis;
- evitar logs duplicados do mesmo erro em varias camadas;
- logar contexto operacional via `extra`;
- nao expor segredos, telefone, email ou mensagem do usuario sem necessidade;
- usar `LOG_INBOUND_PAYLOADS=true` apenas quando o payload validado e sanitizado for
  necessario para diagnostico.
