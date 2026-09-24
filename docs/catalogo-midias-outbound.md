# Catalogo de Midias Outbound

Este documento explica como cadastrar midias que o agente pode escolher para enviar no
WhatsApp.

O catalogo real fica em [`src/app/outbound_media/catalog.json`](../src/app/outbound_media/catalog.json).
Por padrao ele fica vazio:

```json
{
  "media": []
}
```

Quando o catalogo esta vazio, o agente nao recebe nenhuma midia disponivel no prompt e nao
gera `media_choices`. A resposta continua funcionando normalmente com texto e
`response_messages`.

## Responsabilidades

- `agent`: ve apenas a lista segura com `media_id`, tipo, titulo, descricao e quando usar.
- `application`: valida se o `media_id` existe e esta `enabled=true`.
- `integrations`: resolve a URL real e chama a Pipefacil.

O LLM nunca deve ver `media_url`, arquivo bruto, URL assinada, token ou API externa.

## Como Adicionar Uma Midia

1. Suba o arquivo em um storage externo com URL HTTPS publica ou acessivel pela Pipefacil.
2. Confirme que a URL baixa o arquivo direto, nao uma pagina HTML.
3. Confirme `Content-Type`, tamanho e status com `curl -I -L`.
4. Adicione uma entrada em `catalog.json`.
5. Use `enabled=false` enquanto estiver validando.
6. Rode `make test` e `make lint`.
7. Teste pelo Insomnia ou pelo webhook real.
8. Troque para `enabled=true` apenas depois que o envio funcionar no WhatsApp.

Exemplo de validacao:

```bash
curl -I -L "https://cdn.example.com/path/catalogo.pdf"
```

O retorno esperado deve ter `HTTP/2 200` ou `HTTP/1.1 200`, `content-type` coerente e
`content-length` presente quando possivel.

## Estrutura

Cada item tem estes campos:

```json
{
  "id": "catalogo_comercial",
  "type": "document",
  "title": "Catalogo comercial",
  "description": "Documento PDF com informacoes comerciais do produto.",
  "when_to_use": "Quando o lead pedir catalogo, proposta ou material completo.",
  "media_url": "https://cdn.example.com/path/catalogo.pdf",
  "content_type": "application/pdf",
  "filename": "catalogo.pdf",
  "enabled": true
}
```

Campos:

- `id`: identificador estavel escolhido pelo time. Use letras, numeros, `_`, `-`, `.`
  ou `:`.
- `type`: `image`, `video`, `audio` ou `document`.
- `title`: nome curto para o agente entender o material.
- `description`: descricao objetiva do conteudo.
- `when_to_use`: regra conversacional clara de quando enviar.
- `media_url`: URL HTTPS direta do arquivo.
- `content_type`: MIME type enviado para a Pipefacil.
- `filename`: nome do arquivo enviado no WhatsApp.
- `enabled`: quando `false`, o item fica versionado, mas nao aparece para o agente.

## Exemplo Completo

Veja [`src/app/outbound_media/catalog.example.json`](../src/app/outbound_media/catalog.example.json)
para um exemplo com imagem, audio e documento. As entradas do exemplo ficam com
`enabled=false` de proposito.

Para usar como base:

```bash
cp src/app/outbound_media/catalog.example.json src/app/outbound_media/catalog.json
```

Depois substitua IDs, textos, URLs e nomes de arquivo pelos materiais reais.

## URLs do MinIO

Links da interface do MinIO geralmente nao servem para envio. Evite URLs como:

```text
https://minio.example.com/browser/bucket/arquivo.pdf
```

Esse tipo de link costuma retornar HTML da interface web. Use a URL direta do objeto:

```text
https://s3-minio.example.com/bucket/arquivo.pdf
```

Quando precisar forcar header do objeto, MinIO/S3 pode aceitar query params como:

```text
?response-content-disposition=attachment%3B%20filename%3D%22catalogo.pdf%22&response-content-type=application%2Fpdf
```

Use isso apenas quando o storage estiver servindo um `Content-Type` ruim ou quando o
WhatsApp/Pipefacil precisar de filename explicito.

## Formatos Recomendados

Imagem:

- `image/jpeg` com `.jpg` ou `.jpeg`
- `image/png` com `.png`

Video:

- `video/mp4` com `.mp4`

Audio:

- use `audio/ogg` com `.ogg`
- o arquivo deve ser Ogg Opus, mono, 48 kHz
- use bitrate perto de 48 kbps para audio de voz
- remova metadados do arquivo quando possivel
- valide audio no WhatsApp antes de habilitar

Comando recomendado para gerar o arquivo oficial:

```bash
ffmpeg -y -i entrada.wav -map_metadata -1 -ac 1 -ar 48000 -c:a libopus -b:a 48k -vbr on -application voip -frame_duration 20 apresentacao.ogg
```

Validacao recomendada:

```bash
ffprobe -v error -show_entries format=format_name,bit_rate:stream=codec_name,sample_rate,channels -of json apresentacao.ogg
```

O retorno esperado deve indicar `codec_name=opus`, `format_name=ogg`, `sample_rate=48000`
e `channels=1`.

Documento:

- `application/pdf` com `.pdf`
- `text/plain` com `.txt` ou arquivos textuais simples, quando o provedor aceitar

## Seguranca

- Nao coloque URLs privadas, temporarias ou com token sensivel se elas puderem aparecer em
  stack traces ou ferramentas externas.
- Nao use link de painel administrativo.
- Nao coloque dados pessoais no `id`, `title`, `description` ou `when_to_use`.
- Nao exponha `media_url` em prompt, API ou logs.

O codigo atual ja evita expor `media_url` em `ChatResponse`, logs de entrega e prompt do
agente. Ainda assim, trate o catalogo como configuracao operacional sensivel.

## Audio Gerado Dinamicamente

Audios gerados por ElevenLabs nao precisam entrar no `catalog.json`. Eles usam outro fluxo:

1. o responder preenche `generated_audio` no plano estruturado quando uma explicacao falada
   ajuda; dados exatos ou copiaveis permanecem em `response_text`, permitindo respostas
   hibridas sem duplicar conteudo. A regra legada por tamanho so e acionada quando
   `GENERATED_AUDIO_AUTO_ENABLED=true` e `response_text` passa de
   `GENERATED_AUDIO_AUTO_MIN_CHARS`;
2. a aplicacao chama a ElevenLabs com `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` e
   `ELEVENLABS_MODEL_ID` do ambiente;
   estabilidade, similaridade, estilo, speaker boost e velocidade podem ser ajustados pelas
   variaveis `ELEVENLABS_VOICE_*`, sempre com teste auditivo da voz escolhida;
   erros de transporte, `429` e `5xx` usam no maximo
   `ELEVENLABS_MAX_ATTEMPTS`, com pausa definida em `ELEVENLABS_RETRY_BACKOFF_SECONDS`;
3. o arquivo ja e gerado diretamente como Ogg Opus por padrao; a conversao com FFmpeg fica
   disponivel apenas para compatibilidade quando `GENERATED_AUDIO_CONVERT_TO_OGG_OPUS=true`;
4. o arquivo e gravado de forma atomica e fica temporariamente em
   `GENERATED_AUDIO_STORAGE_DIR` do ambiente;
5. o webhook envia uma mensagem de texto curta e depois uma parte `audio` com uma URL
   publica temporaria em `/generated-audio/...`.

Para esse fluxo funcionar em WhatsApp real, configure `GENERATED_AUDIO_PUBLIC_BASE_URL` com
uma URL HTTPS publica do proprio servico, ou configure `CLOUDFLARE_TUNNEL_HOSTNAME`. A
Pipefacil precisa conseguir baixar essa URL diretamente.

Se a geracao explicita falhar, a aplicacao envia o roteiro como texto. Se a regra automatica
falhar, a resposta textual original e preservada. O armazenamento padrao e local: multiplas
replicas exigem volume compartilhado ou roteamento compativel com a instancia que criou o
arquivo.

## Troubleshooting

Se a Pipefacil retorna `201/ACCEPTED`, mas o WhatsApp mostra erro de upload:

- confira se a URL baixa arquivo direto e nao HTML;
- confira `Content-Type`;
- confira se o formato e aceito pelo WhatsApp;
- teste outro MIME type somente se o arquivo real for compativel;
- para audio, confira se o `.ogg` esta em codec Opus, mono e 48 kHz;
- deixe o item com `enabled=false` ate o envio real funcionar.
