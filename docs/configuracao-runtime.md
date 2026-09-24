# Configuracao de Runtime e Painel Administrativo

Este documento separa dois tipos de configuracao que nao devem ser confundidos:

- **bootstrap imutavel e secreto**, lido do ambiente quando o processo inicia;
- **operacao nao sensivel**, persistida no Postgres e alterada pelo painel `/settings`.

O codigo correspondente fica em `src/app/core/config.py`,
`src/app/application/runtime_settings.py` e
`src/app/integrations/postgres_runtime_settings.py`.

## Bootstrap do processo

O ambiente — normalmente carregado de `.env` pelos scripts locais ou injetado pela
plataforma — contem apenas segredos e infraestrutura que nao podem ser alterados pelo painel:

| Variavel | Uso |
| --- | --- |
| `DATABASE_URL` | banco, checkpoints, idempotencia e configuracao operacional |
| `OPENAI_API_KEY` | chamadas OpenAI |
| `PIPEFACIL_API_KEY` | chamadas autenticadas ao Pipefacil |
| `PIPEFACIL_WEBHOOK_SIGNATURE_SECRET` | autenticacao dos webhooks |
| `ELEVENLABS_API_KEY` | TTS, quando habilitado |
| `LANGFUSE_PUBLIC_KEY` e `LANGFUSE_SECRET_KEY` | observabilidade e prompts remotos |
| `SETTINGS_ADMIN_KEY_HASH` | hash scrypt da chave de acesso ao painel |
| `SETTINGS_SESSION_SECRET` | assinatura da sessao HTTP do painel |

Configuracao de assinatura de webhook, catalogo de midia, armazenamento de audio, pool e schema
do checkpointer sao constantes internas: nao pertencem ao painel nem ao ENV operacional. O
schema e inferido da propria `DATABASE_URL`.

Banco, OpenAI, Pipefacil, segredo de assinatura, hash administrativo e segredo de sessao sao
obrigatorios em todos os ambientes. Nunca persista nem exponha no painel uma chave de API,
segredo de webhook, senha, URL de banco ou segredo de sessao.

Para gerar uma chave nova e seus valores de bootstrap, execute localmente:

```bash
make settings-key
```

Guarde a linha `Settings access key` num gestor de senhas. Copie somente as outras duas linhas
para os segredos da plataforma ou para o arquivo local ignorado. O script nao grava arquivo nem
envia a chave para rede.

## Configuracao operacional persistida

O modelo `RuntimeSettings` guarda os valores nao sensiveis na tabela singleton
`sdr_runtime_settings`, no mesmo Postgres e schema do runtime LangGraph. Ele e inicializado com
defaults tipados e contem, entre outros:

- identidade do SDR e modelos OpenAI;
- limites e URLs nao secretas do Pipefacil;
- comportamento e parametros publicos de audio;
- host, label e flags nao secretas do Langfuse;
- nivel/formato de log e politica de identificacao nao sensivel.

Cada request obtem um snapshot validado atual. Uma alteracao confirmada no painel entra em vigor
na proxima mensagem; ela nao exige restart. O banco usa uma versao crescente para impedir que
uma aba antiga sobrescreva uma edicao mais nova: o painel devolve `409` e pede revisao quando a
versao enviada nao e a mais recente.

Variaveis de ambiente como `OPENAI_MODEL`, `APP_NAME`, `PIPEFACIL_BASE_URL` ou
`LANGFUSE_PROMPT_LABEL` **nao sao o canal de configuracao operacional**. O valor efetivo vem do
snapshot persistido. Use `/settings` depois do primeiro startup, em vez de editar
`.env`,
para esses ajustes.

## Painel `/settings`

As rotas do painel nao aparecem no OpenAPI e continuam disponiveis em producao:

- `GET /settings/login`: formulario de acesso;
- `POST /settings/login`: cria sessao autenticada;
- `GET /settings`: le o snapshot atual;
- `POST /settings`: valida e salva uma nova versao;
- `POST /settings/logout`: encerra a sessao.

O painel usa uma chave de acesso verificada contra `SETTINGS_ADMIN_KEY_HASH` com comparacao em
tempo constante. A sessao e assinada com `SETTINGS_SESSION_SECRET`, dura 12 horas, usa
`SameSite=Lax` e recebe flag `Secure` em producao. Formularios de login, salvamento e logout
levam token CSRF. Isso protege o painel administrativo, mas nao substitui controles de rede:
restrinja o acesso administrativo no proxy, VPN ou allowlist quando possivel.

Nao trate o painel como API publica. Ele nao e incluído no schema e nao deve receber credenciais
por query string ou ser exposto sem protecao de borda.

## Operacao inicial

1. Rode `make env-init` para criar `.env` com permissao restrita e preencha os
   segredos de bootstrap, ou configure os mesmos segredos na plataforma.
2. Rode `make db-setup` para preparar o schema do checkpointer.
3. Inicie a aplicacao. O startup cria, quando necessario, a tabela de configuracao operacional
   e seu snapshot inicial.
4. Acesse `/settings/login` com a chave gerada localmente.
5. Ajuste os valores operacionais, salve e teste uma mensagem.

Em desenvolvimento sem `DATABASE_URL`, a aplicacao usa store em memoria apenas para esse
snapshot; alteracoes somem ao reiniciar. Producao exige Postgres, portanto o painel nao opera
com esse fallback.

## Limites deliberados

O painel nao atualiza segredo, schema, pool, checkpointer ou infraestrutura de entrega. Ele
tambem nao substitui o versionamento de prompts no Langfuse: `langfuse_prompt_label` seleciona
o label que o runtime busca, enquanto criacao, revisao e promocao de prompt seguem
[`observabilidade-langfuse.md`](observabilidade-langfuse.md).
