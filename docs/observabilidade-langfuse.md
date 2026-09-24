# Observabilidade e Prompt Management Com Langfuse

Este documento e o contrato local para Langfuse neste template.

Ele organiza duas responsabilidades relacionadas, mas diferentes:

- observabilidade: tracing, callbacks, contexto de execucao, metadata, tags e masking;
- prompt management: nomes canonicos, fallbacks locais, labels e promocao de versoes.

Logs operacionais seguem outro contrato:
[`docs/logging.md`](logging.md).

Antes de alterar este fluxo, consulte tambem
[`docs/contexto-desenvolvimento-langfuse.md`](contexto-desenvolvimento-langfuse.md)
para carregar a documentacao oficial atual via `llms.txt` ou MCP.

## Fronteiras

### `src/app/observability/`

`src/app/observability/langfuse.py` e o unico lugar que deve conhecer o SDK do Langfuse em
detalhe.

Responsabilidades:

- criar e cachear o cliente Langfuse;
- criar callbacks LangChain/LangGraph;
- resolver label de prompt;
- buscar prompts remotos;
- fornecer fallback local quando Langfuse estiver desabilitado;
- converter prompts chat Langfuse para `ChatPromptTemplate`;
- compilar prompts text quando eles forem usados como guias internos de estilo;
- aplicar masking de dados sensiveis em spans;
- propagar `session_id`, `user_id` e `tags`;
- fazer `flush` no shutdown.

### `src/app/agent/prompts/`

`src/app/agent/prompts/` guarda os contratos de prompt do agente:

- nome canonico usado no Langfuse;
- fallback local versionado no codigo;
- helpers que entregam templates prontos para chains.

Prompt novo entra aqui antes de ser usado por chain.

### `src/app/agent/chains/`

Chains consomem templates prontos e montam chamadas LLM.

Elas nao devem decidir label, criar cliente Langfuse, publicar prompt ou conhecer detalhes
do SDK.

### `src/app/agent/service.py`

O servico do agente abre a observacao raiz da execucao.

Hoje o trace raiz usa:

- name: `run-{app_slug}`;
- tags base: `langgraph`, `{app_slug}`;
- metadata base: `graph={app_slug}`;
- `thread_id` como `session_id` quando recebido pela API.

Callbacks LangChain/LangGraph detalhados sao injetados por `run_agent()` somente quando o
historico nao contem content blocks multimodais com `base64`, `data`, `file_data` ou `url`.
Nesses casos, o trace raiz continua existindo com input/output sanitizados, mas os detalhes
de prompt/generation sao omitidos para evitar vazar ou armazenar arquivo bruto no Langfuse.

Especialistas executados pelo OpenAI Agents SDK mantem o tracing nativo com o nome estavel
`run-{app_slug}-specialist`, mas usam `trace_include_sensitive_data=false` para nao enviar
inputs/outputs de LLM ou tools aos traces da OpenAI. O Langfuse continua sendo a fonte
principal para observar a execucao do grafo.

### `scripts/bootstrap_langfuse_prompts.py`

Script operacional para sincronizar prompts definidos em `src/app/agent/prompts/`.

Ele le a versao `staging` sem cache, normaliza o formato devolvido pelo SDK e cria uma nova
versao somente quando o conteudo canonico mudou. Versoes novas recebem hash SHA-256 e, quando
disponiveis, repositorio, commit e execucao de origem. A flag `--promote-production` aplica
as labels `staging` e `production` a versao staging encontrada ou recem-criada.

Como esse script roda fora do runtime da API, ele monta um snapshot efemero apenas para o
cliente Langfuse a partir de `LANGFUSE_ENABLED`, `LANGFUSE_BASE_URL` e das credenciais de
bootstrap. Ele nao le nem grava a configuracao operacional persistida.

Depois de um push bem-sucedido para `main`, o CI confirma que o commit ainda e o topo da
branch, sincroniza `staging` e promove a mesma versao automaticamente para `production`.
Se os testes falharem ou o commit deixar de ser o topo da `main`, a promocao nao acontece.

## Labels de Prompt

O snapshot de configuracao operacional define `langfuse_prompt_label`; o valor inicial e
`production`. O resolvedor usa o label salvo no snapshot, e uma mudanca no painel `/settings`
vale para a proxima chamada que busca prompt. Credenciais Langfuse continuam no bootstrap,
nunca no painel.

Regra operacional:

- validacoes de prompt podem usar a label `staging` de forma explicita;
- o runtime busca `production`;
- `latest` nao deve ser usado pelo runtime padrao;
- use um snapshot controlado com label `staging` apenas em preview ou validacao deliberada.

Consulte [`configuracao-runtime.md`](configuracao-runtime.md) para a separacao entre bootstrap
e configuracao operacional.

## Fluxo Para Alterar Prompt

1. Atualize ou crie o prompt em `src/app/agent/prompts/definitions.py`.
2. Garanta que placeholders usados pelo prompt existem no estado ou no input da chain.
3. Ajuste a chain em `src/app/agent/chains/` se houver nova variavel ou structured output.
4. Rode os testes de prompt e chain.
5. Valide os cenarios relevantes de [`dataset-padrao-ouro.md`](dataset-padrao-ouro.md). As
   respostas ideais sao referencias de comportamento, nao scripts para igualdade exata.
6. Sincronize `staging` localmente quando precisar validar antes do merge. Use o mesmo perfil
   de producao para as credenciais:

```bash
.venv/bin/python scripts/bootstrap_langfuse_prompts.py \
  --env-file .env
```

7. Valide traces e comportamento no tooling local. Compare a versao candidata com o dataset e
   registre revisao humana quando a mudanca afetar linguagem.
8. Faça merge na `main`; depois dos quality gates, o CI sincroniza e promove automaticamente
   a versao para `production`.
9. Para reexecutar a mesma promocao manualmente em uma recuperacao operacional, use:

```bash
.venv/bin/python scripts/bootstrap_langfuse_prompts.py \
  --env-file .env \
  --promote-production
```

10. Monitore traces por prompt version no Langfuse. Se houver regressao, mova o label
    `production` de volta para uma versao anterior revisada.

O quality gate valida contratos, testes e estrutura de forma deterministica. Ele nao infere
se uma resposta comercial ficou boa; essa parte exige dataset, traces e revisao humana.

## Fluxo Para Adicionar Observabilidade

1. Decida se o que voce precisa e trace raiz, span interno, generation, tool ou metadata.
2. Mantenha nomes estaveis e de baixa cardinalidade.
3. Coloque valores dinamicos em metadata, input/output, tags ou estado, nao no nome.
4. Use `observe_agent_run` para execucoes completas do agente.
5. Use `observe_span` para blocos manuais compartilhados.
6. Passe `session_id` em fluxos multi-turn para agrupar a conversa.
7. Passe `user_id` quando houver identificador estavel e permitido.
8. Passe tags curtas para dimensoes de negocio ou canal.
9. Garanta que dados sensiveis continuem mascarados.

No fluxo Pipefacil, o webhook pode abrir um span pai `handle-pipefacil-turn` para manter
no mesmo trace tanto o `run-{app_slug}` quanto passos posteriores de entrega. A geracao
TTS do ElevenLabs pode aparecer como generation `generate-elevenlabs-speech`, com
`usage_details.characters`, latencia da chamada e `cost_details.characters` quando
`ELEVENLABS_TTS_COST_PER_1K_CHARS_USD` estiver configurado.

## Regras de Dados

- `thread_id` da API vira `session_id` no Langfuse.
- Webhooks Pipefacil usam `LANGFUSE_PIPEFACIL_USER_ID_MODE=contact_id` por padrao. Esse
  modo produz `contact:<id>` e nao coloca nome ou telefone em `user.id`.
- `LANGFUSE_PIPEFACIL_USER_ID_MODE=contact_name_phone` e um opt-in explicito para auditoria
  humana: produz `Nome | +telefone | contact:<id>`, normalizado e limitado a 200 caracteres.
  Nesse modo somente o atributo semantico `user.id` e preservado deliberadamente pelo
  callback de masking.
- Trace input deve ser legivel para revisao humana, preferencialmente a mensagem do usuario.
- Trace output deve ser a resposta final quando existir.
- Payload bruto de webhook pode ir em metadata apenas quando for realmente necessario.
- Dados sensiveis devem passar pelo masking em `observability/langfuse.py`.
- Chaves, tokens, emails e telefones nao devem aparecer crus em outros atributos, metadata,
  input ou output. O opt-in acima nao desliga masking de credenciais, email, midia ou
  qualquer outro campo.

O projeto fixa `langfuse==4.14.1` e usa o callback oficial `mask_otel_spans`. Ao atualizar o
SDK, valide novamente os patches de atributos e mantenha o comportamento de `user.id`
explicito nos testes; uma atualizacao nao deve ampliar silenciosamente a excecao de PII.

## Scores de Avaliacao do Dataset

O dataset padrao ouro do template usa estes scores no Langfuse:

- `answer_correct`: `BOOLEAN`, fonte `ANNOTATION` quando preenchido por humano. E o
  resultado principal da revisao do trace.
- `failure_note`: `TEXT`, fonte `ANNOTATION` quando preenchido por humano. Guarda a nota
  livre sobre a falha. Scores de texto usam `stringValue`; podem nao aparecer como texto
  visivel em algumas tabelas de Experiment Run self-hosted.
- `output`: `CORRECTION`, criado pelo campo `Corrected Output (Beta)`. Guarda a resposta
  corrigida que o agente deveria ter produzido.

Esses scores devem ser anexados no nivel do trace da run, que e o nivel mais comum para
avaliacao de uma unica interacao. O contrato completo do fluxo fica em
[`docs/dataset-padrao-ouro.md`](dataset-padrao-ouro.md).

## Nomes Estaveis

Trate nomes de traces, spans, nodes e prompts como API.

Evite:

- nome com ID dinamico;
- nome com telefone, email, lead ID ou thread ID;
- nome com modelo especifico, quando o modelo ja e atributo;
- renomear observacoes sem revisar dashboards, filtros, avaliacoes e testes.

Prefira:

- `run-{app_slug}`;
- `handle-pipefacil-turn`;
- `generate-elevenlabs-speech`;
- `classify-intent`;
- `respond`;
- `send-pipefacil-message`;
- `retrieve-lead-context`.

## Prompts Atuais

Prompts canonicos deste template:

- `agent/classifier`;
- `agent/responder`;
- `agent/style/whatsapp`.

O fallback local existe para manter testes e desenvolvimento funcionando quando Langfuse
nao esta configurado. O fallback nao substitui o fluxo de versionamento em ambientes reais.
`agent/style/whatsapp` e um prompt `text`: ele nao representa uma conversa completa, e sim
um guia reutilizavel injetado no prompt `agent/responder` pela variavel `response_style`.
O prompt `agent/responder` tambem recebe `available_media`, uma visao segura do catalogo de
midias outbound sem `media_url`, arquivos brutos ou URLs assinadas.

O responder base e consultivo e contextual, mas continua sem regras, oferta ou alegacoes de
um cliente. `agent/style/whatsapp` concentra coesao, naturalidade, adaptacao de voz e a regra
de manter conversas abertas com uma pergunta util, exceto em finalizacoes reais. O contrato
completo fica em [`modelagem-conversacional.md`](modelagem-conversacional.md).

## Ao Criar um Novo Agente a Partir do Template

1. Prefira um projeto Langfuse separado por cliente, com credenciais proprias.
2. Se clientes compartilharem um projeto, renomeie todos os prompts para um namespace unico
   antes do primeiro bootstrap; labels sao resolvidas por nome de prompt.
3. Renomeie tags e metadata base apenas se o nome do produto/agente mudou.
4. Mantenha labels `staging` e `production` como convencao inicial.
5. Crie prompts novos com prefixo coerente, por exemplo `cliente/agent/qualifier`.
6. Versione fallbacks junto com o codigo.
7. Publique todos os prompts com o script antes de testar ambientes remotos.
8. Revise dashboards e filtros se mudar nomes de observacoes.

## Onde Nao Colocar Codigo

- SDK Langfuse nao entra em node ou chain.
- Publicacao de prompt nao entra no startup da API.
- MCP de documentacao nao entra em runtime.
- MCP autenticado de dados Langfuse nao deve ser commitado no template base.
- Credenciais Langfuse nao entram em arquivos versionados.
