# Insomnia e OpenAPI

Este guia deixa a API pronta para teste manual no Insomnia.

O artefato importavel fica em:

- [`openapi.json`](openapi.json)

Esse arquivo e gerado a partir do proprio `FastAPI` com `.env` quando existir, portanto documenta
somente os endpoints publicos da configuracao de producao. Ele deve ser atualizado sempre que
endpoints, schemas ou responses mudarem. O endpoint OpenAPI ao vivo permanece desabilitado em
producao; use este artefato para importar a collection.

O painel `/settings` e deliberadamente excluido do OpenAPI e do Insomnia: ele usa formularios
HTML, sessao assinada e CSRF, e nao e uma API de integracao. Consulte
[`../configuracao-runtime.md`](../configuracao-runtime.md) para operacao e seguranca.

## Regenerar o OpenAPI

```bash
make openapi
```

O comando acima executa [`scripts/export_openapi.py`](../../scripts/export_openapi.py), carrega
`.env` quando existir e sobrescreve `docs/api/openapi.json`.

## Importar no Insomnia

1. Abra o Insomnia.
2. Clique em `Import`.
3. Escolha importacao por arquivo.
4. Selecione [`docs/api/openapi.json`](openapi.json).
5. Confirme a importacao e gere a collection.

Referencia oficial do Insomnia:

- https://developer.konghq.com/insomnia/import-export/

Depois da importacao, use a URL local:

```text
http://localhost:8000
```

Para subir a API local com o perfil unico de producao:

```bash
make app
```

Use `make local` quando tambem precisar expor a API via Cloudflare Tunnel.

## Variaveis uteis

Configure no ambiente do Insomnia, se preferir:

```json
{
  "base_url": "http://localhost:8000",
  "thread_id": "insomnia-local-001",
  "user_id": "insomnia-user",
  "webhook_secret": "dev-webhook-secret"
}
```

O OpenAPI exportado ja aponta para `http://localhost:8000`. A variavel `base_url` e util
quando voce quiser alternar entre local, tunnel e deploy.

## Ordem boa para testar

1. `GET /health`
2. `GET /ready`
3. `POST /chat`
4. `POST /conversations/resume`
5. `GET /threads/{thread_id}/state`
6. `POST /events/message-received`

## `POST /chat`

Use este body para uma rodada manual sem Pipefacil:

```json
{
  "thread_id": "insomnia-local-001",
  "message": "Oi, quero entender como funciona.",
  "user_id": "insomnia-user",
  "metadata": {
    "client": "insomnia",
    "channel": "manual"
  }
}
```

Resposta esperada:

- `200 OK`
- `delivery_status: null`, porque `/chat` nao envia outbound para o Pipefacil.
- `response_text`: resposta canonica do agente.
- `response_messages`: partes deterministicas que seriam enviadas no WhatsApp.
- `response_parts`: plano ordenado para debug, com partes `text` e possiveis midias por
  `media_id`, sem URLs de midia.

Depois rode:

```text
GET /threads/insomnia-local-001/state
```

## `POST /conversations/resume`

Use esta rota para iniciar ou retomar uma conversa a partir do historico carregado do
Pipefacil. O campo `context` e uma orientacao interna para a IA; ele nao sera apresentado ao
lead como se fosse uma mensagem dele.

```json
{
  "thread_id": "deal-example-001",
  "deal_seq": 100,
  "recipient_phone": "+5511000000001",
  "sender_phone_number_id": "111111111111111",
  "context": "Faz 3 dias que ele nao responde e ficou de passar o cartao.",
  "send_response": false
}
```

O filtro do historico pode usar `deal_seq`, `deal_id`, `contact_id` ou `channel_id`. Para
enviar a resposta ao lead, mantenha `send_response=true` e informe `recipient_phone`. A rota
retorna `history_message_count` junto com a resposta do agente. `history_limit` aceita de 1 a
500 mensagens e usa o mesmo validador de assinatura do webhook. A assinatura deve incluir o
timestamp e o JSON exato enviado:

```text
X-Pipefacil-Timestamp: <timestamp em milissegundos>
X-Pipefacil-Signature-256: sha256=<HMAC-SHA256(secret UTF-8, timestamp + "." + JSON)>
```

Respostas esperadas:

- `200 OK` com o resultado do agente; `send_response=false` faz dry run.
- `401 Unauthorized` quando a assinatura estiver ausente ou invalida.
- `422 Unprocessable Content` quando faltar um filtro de historico ou houver erro de entrada.
- `502 Bad Gateway` quando a consulta de historico ou a entrega outbound falhar no Pipefacil.

## `POST /events/message-received`

Use este body para simular um webhook inbound de texto:

```json
{
  "type": "message.received",
  "timestamp": "2026-07-22T13:26:43.124642219Z",
  "data": {
    "message": {
      "id": "c31c49ef-2eab-4345-bf07-3dd06d8f451e",
      "externalId": "wamid.EXAMPLE_MESSAGE_000000000000000000000000000000000000000000000001",
      "body": "Oi, quero falar com o atendimento.",
      "type": "text",
      "timestamp": "2026-07-22T13:26:39Z",
      "media": null
    },
    "channel": {
      "id": "channel-example-001",
      "phoneNumberId": "111111111111111",
      "phoneNumber": "+55 11 00000-0000",
      "displayName": null
    },
    "contact": {
      "id": "contact-example-001",
      "name": "CLIENTE EXEMPLO",
      "phone": "+5511000000001",
      "email": null
    },
    "deal": {
      "id": "deal-example-001",
      "seq": 100,
      "name": "Cliente Exemplo",
      "stage": {
        "id": "stage-example-001",
        "name": "Qualificacao IA"
      }
    }
  }
}
```

Em desenvolvimento, se `PIPEFACIL_WEBHOOK_SIGNATURE_ENABLED=false`, a rota nao exige
assinatura. Se a flag estiver `true`, deixar `PIPEFACIL_WEBHOOK_SIGNATURE_SECRET` vazio
tambem desabilita a validacao.

Com secret configurado, use os dois headers do CRM:

```text
X-Pipefacil-Timestamp: <timestamp em milissegundos>
X-Pipefacil-Signature-256: sha256=<HMAC-SHA256(secret UTF-8, timestamp + "." + JSON)>
```

Calcule o HMAC-SHA256 usando o secret como texto UTF-8 e a mensagem
`<timestamp>.<JSON exato enviado>`. O header de assinatura precisa conter o prefixo `sha256=` e
64 caracteres hexadecimais minusculos. Se alterar espacos, quebras de linha ou pretty print do
JSON, gere a assinatura novamente. Em pedidos gzip, assine o JSON antes de comprimi-lo.

Respostas esperadas:

- `200 OK` com `status=accepted` assim que assinatura, contrato e conteudo local aplicavel
  forem validados. Agente e outbound continuam em background, com `response_text` vazio no
  aceite.
- `401 Unauthorized` quando a assinatura estiver ausente ou invalida.
- `422 Unprocessable Content` quando o payload inbound nao puder virar uma mensagem valida.

## Observacoes

- Para testar `/chat`, configure `OPENAI_API_KEY`.
- Para testar webhook com outbound real de texto e midia, configure `PIPEFACIL_API_KEY`.
- Para testar somente parsing/contrato do webhook, rode sem `PIPEFACIL_API_KEY`; o aceite
  continua sendo `200`, e a falha outbound aparece nos logs do processamento em background.
- Nao coloque chaves reais, tokens ou secrets em arquivos versionados.
