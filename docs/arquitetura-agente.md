# Arquitetura Interna do Agente

Este documento e o contrato arquitetural de `src/app/agent/`.

A base assume **um agente por repositorio**. Novos agentes devem nascer a partir deste
template, mantendo o nucleo em `src/app/agent/` singular e deixando variacoes de negocio em
estado, nodes, chains, routing, tools e prompts.

## Objetivo

`src/app/agent/` concentra o fluxo conversacional e a execucao do grafo LangGraph.

Ele deve ser reutilizavel por diferentes agentes Pipefacil sem depender de detalhes do
webhook, transporte HTTP externo ou contratos especificos de integracao.

Fluxo atual:

```text
START -> classify-intent -> respond -> END
```

Quando o classificador marca `requires_specialist=true`, o fluxo passa por um especialista
interno antes do responder:

```text
START -> classify-intent -> delegate-specialist -> respond -> END
```

O especialista OpenAI Agents SDK nunca envia mensagem ao Pipefacil. Ele retorna trabalho
estruturado para o `respond`, que continua montando a resposta final.

O fluxo acima e somente o scaffold executavel da base. Ele nao deve ser copiado como o fluxo
comercial completo de um SDR derivado. O contrato para expandi-lo, inclusive a fronteira entre
nodes e a proibicao de parsing semantico, esta em
[`derivacao-agente.md`](derivacao-agente.md).

## Estrutura

```text
src/app/agent/
  __init__.py      API publica do pacote do agente
  agent.py         compatibilidade com LangGraph Studio
  graph.py         montagem e compilacao do StateGraph
  routing.py       nomes de nodes e funcoes puras de roteamento
  state.py         schema do estado compartilhado
  messages.py      utilitarios para mensagens LangChain/protocolo
  service.py       execucao do grafo, tracing e leitura de estado
  runtime.py       lifecycle do grafo e escolha de checkpointer
  specialists/     contratos, registry e runner de especialistas OpenAI Agents SDK
  nodes/           steps finos do grafo
  chains/          prompts + modelos + structured output
  prompts/         definicoes locais e nomes Langfuse
  tools/           tools expostas ao agente
```

O catalogo versionado de midias outbound fica em `src/app/outbound_media/`. O catalogo
padrao fica vazio; entradas reais devem seguir
[`catalogo-midias-outbound.md`](catalogo-midias-outbound.md). O agente usa apenas a visao
segura desse catalogo no prompt: ID, tipo, titulo, descricao e quando usar. URLs, arquivos
brutos e chamadas HTTP externas ficam fora de `src/app/agent/`.

### `nodes/`

Nodes sao steps do grafo. Um node deve:

- ler dados do `AgentState`;
- chamar uma chain, tool ou regra local;
- retornar apenas updates parciais do estado.

Node nao deve conter transporte HTTP, parsing de webhook, prompt grande inline ou regra de
integracao externa.

O tamanho do node nao e o criterio isolado para criar uma fronteira. Crie um node quando
houver uma responsabilidade coesa que decide uma regra, interpreta conversa, consulta dados,
produz um efeito externo ou precisa de retry, checkpoint, teste ou observabilidade propria.
Nao fragmente mapeamentos triviais; tambem nao concentre varias dessas responsabilidades em
um unico node.

### `chains/`

Chains montam chamadas LLM.

Responsabilidades:

- escolher prompt;
- escolher modelo;
- configurar structured output;
- lidar com compatibilidades de modelo, como fallback de temperatura.

Chain nao decide o proximo node e nao executa acao externa de negocio.

Use structured output quando significado conversacional controlar passos seguintes, por
exemplo fatos novos, correcoes, perguntas, objecoes ou recusa. A chain interpreta; o routing
decide a transicao a partir do resultado validado.

Use structured output nativo e validacao tipada, nunca JSON solicitado como texto seguido de
parsing manual, para significado que controlar estado, routing, qualificacao, handoff ou
acoes. O parsing deterministico continua restrito a formatos formais, como payloads, IDs,
datas e comandos.

### `routing.py`

`routing.py` guarda nomes de nodes e funcoes puras de decisao para
`add_conditional_edges` ou `Command`.

Funcoes de routing devem depender apenas do estado ja disponivel. Elas nao devem chamar LLM,
HTTP, banco, Langfuse ou integracoes.

Evite routing por regex ou palavras soltas para inferir significado. Comparacao direta e
regex permanecem apropriados para comandos exatos e formatos deterministas.

### `state.py`

`AgentState` guarda dados brutos que precisam sobreviver entre nodes.

Boas regras:

- use reducer em listas acumulativas, como `messages`;
- guarde fatos reutilizaveis, nao texto pronto de prompt;
- adicione campos de estado antes de espalhar dicionarios soltos pelos nodes.
- em agentes derivados, modele explicitamente fatos conhecidos, suas fontes e o objetivo
  pendente quando esses dados precisarem sobreviver entre turnos;
- quando um fato comercial, status, etapa, tag, responsavel ou handoff tambem for uma
  projecao do Pipefacil, adicione uma operacao ao `pipefacil_sync_outbox` na mesma atualizacao local. O
  action node de sincronizacao deve consumir essa pendencia imediatamente e registrar receipt
  ou falha sem descarta-la;
- `resume_context` e uma orientacao operacional interna para retomadas; nunca o trate como
  mensagem do lead nem o revele na resposta.
- mensagens multimodais devem chegar ao agente como `HumanMessage`/content blocks LangChain
  ja normalizados pela camada de aplicacao/integracao; o grafo nao conhece o contrato
  Pipefacil nem URLs assinadas de midia/arquivo.
- `response_media` pode guardar a selecao validada de midias outbound por ID, tipo,
  caption e metadados publicos. Nao guarde `media_url` no estado.
- `response_audio` pode guardar apenas o roteiro textual e a razao para gerar audio
  dinamico. Geracao, conversao, armazenamento temporario e URL publica ficam na camada de
  aplicacao/integracoes, nunca no node do agente.

### `tools/`

`tools/` guarda tools que o agente pode chamar.

Uma tool pode adaptar a interface para o agente, mas detalhes de API externa continuam em
`src/app/integrations/`. Exemplo: uma tool `buscar_lead` pode chamar uma funcao de
`application` ou `integrations`, mas nao deve espalhar `httpx` ou contrato externo dentro do
grafo.

Para uma escrita no Pipefacil, o node/tool recebe uma operacao pendente ja validada e delega a um
caso de uso em `application`. Nao espere a resposta final, a mudanca de etapa ou outra mensagem
para sincronizar campos que ja foram confirmados no estado. O contrato de outbox, receipt e
idempotencia esta em [`derivacao-agente.md`](derivacao-agente.md).

### `specialists/`

`specialists/` guarda a base para delegar tarefas complexas a agentes do OpenAI Agents SDK.
Essa camada recebe contexto sanitizado do LangGraph, roda um especialista stateless e retorna
`SpecialistResult` para o estado. MCPs, tools, handoffs e skills pertencem a factory de cada
especialista, enquanto o core exige contrato, limite de turns, fallback e logs seguros.

## Fronteiras

- `api` recebe HTTP, valida payloads e delega para `application`.
- `application` orquestra casos de uso e chama `agent`/`integrations`.
- `agent` modela fluxo, estado, nodes, prompts, chains, routing e tools.
- `integrations` concentra contratos externos, mapping e clientes HTTP.
- `observability` concentra Langfuse, callbacks, prompt loading e mascaramento.

Detalhes de labels, promocao de prompts e tracing ficam em
[`observabilidade-langfuse.md`](observabilidade-langfuse.md).

O contrato de interpretacao semantica, retomada de contexto e comunicacao consultiva fica em
[`modelagem-conversacional.md`](modelagem-conversacional.md).

Regras protegidas por teste:

- rotas HTTP nao importam `app.agent` nem `app.integrations.pipefacil` diretamente;
- codigo dentro de `src/app/agent` nao importa `app.integrations.pipefacil`;
- `agent/nodes` e `agent/tools` nao importam `httpx` diretamente;
- `langgraph.json` continua apontando para `./src/app/agent/agent.py:graph`.

## Como adicionar uma capacidade nova

1. Leia [`derivacao-agente.md`](derivacao-agente.md) e defina as responsabilidades, dados,
   transicoes e efeitos externos da capacidade.
2. Defina qual dado precisa sobreviver e atualize `state.py`.
3. Modele interpretacao, objetivo pendente e resposta conforme
   [`modelagem-conversacional.md`](modelagem-conversacional.md).
4. Crie ou ajuste prompts em `prompts/`.
5. Crie a chain em `chains/` quando houver LLM.
6. Crie a tool em `tools/` quando o agente precisar de uma ferramenta chamavel.
7. Crie o node fino em `nodes/`.
8. Adicione nomes e funcoes de decisao em `routing.py` quando houver branch.
9. Atualize `graph.py` com nodes e edges.
10. Cubra o fluxo com testes de node, grafo e cenario conversacional.

Para adicionar um especialista, registre uma `SpecialistDefinition` em `specialists/`,
adicione a decisao no classificador e garanta que o responder consuma apenas o resultado
estruturado, nunca uma acao direta de envio.

O nome retornado pelo classificador passa por allowlist do registry. No v1, aliases como
`deep agent` e `testing/deep agent` sao normalizados para `test_specialist`; nomes fora do
registry viram falha controlada `specialist_unknown`.

## Onde nao colocar codigo

- API externa nova nao entra em `agent`; entra em `integrations/`.
- Caso de uso HTTP/webhook nao entra em `nodes`; entra em `application/`.
- Configuracao de ambiente nao entra em chain/node; entra em `core/config.py`.
- Tracing compartilhado nao entra em node; entra em `observability/` ou `service.py`.
