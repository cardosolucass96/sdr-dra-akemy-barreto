# Estrutura LangGraph do Projeto

Este projeto usa `LangGraph` como biblioteca de orquestracao, com `FastAPI` como runtime HTTP real e `langgraph dev` apenas como ferramenta de desenvolvimento.

## Objetivo

A organizacao atual separa:

- montagem do grafo
- nodes finos
- routing puro
- runtime HTTP da aplicacao
- persistencia por checkpointer
- observabilidade e prompts versionados

Assim a gente consegue manter o fluxo visual do LangGraph sem depender do Agent Server licenciado.

## Arquivos principais

### `langgraph.json`

Continua existindo para `langgraph dev`.

Responsabilidades:

- apontar o grafo exportado para Studio
- carregar o perfil unico de producao usado pelo `make dev`
- manter o projeto compativel com debugging visual

### `src/app/main.py`

Ponto de entrada da FastAPI.

Responsabilidades:

- criar a app
- inicializar Langfuse
- montar o grafo de runtime com checkpointer apropriado
- fechar recursos no shutdown

### `src/app/agent/agent.py`

Constroi o grafo base e exporta `graph` para o caminho de desenvolvimento visual.

### `src/app/agent/graph.py`

Monta o `StateGraph`, registra nodes e edges e compila o grafo. Os callbacks Langfuse sao
injetados por `run_agent()` em `src/app/agent/service.py`, conforme o tipo de conteudo da
execucao.

O fluxo principal continua:

```text
START -> classify-intent -> respond -> END
```

O grafo tambem tem um caminho condicional para trabalho interno mais complexo:

```text
START -> classify-intent -> delegate-specialist -> respond -> END
```

O classificador decide se a rodada precisa de especialista, o node executa o OpenAI Agents
SDK atras de feature flag e o responder mantem a mensagem final.

Este fluxo minimo nao tenta representar toda conversa comercial. Em agentes derivados, a IA
deve interpretar linguagem em campos estruturados e o grafo deve rotear sobre esses campos,
conforme [`modelagem-conversacional.md`](modelagem-conversacional.md).

### `src/app/agent/routing.py`

Centraliza nomes de nodes e funcoes puras de decisao para `add_conditional_edges` ou
`Command`.

Routing nao deve chamar LLM, HTTP, banco, Langfuse ou integracoes.

Routing tambem nao deve tentar inferir interesse, objecao ou mudanca de assunto por regex.
Esses significados entram no estado depois de interpretacao semantica estruturada.

### `src/app/agent/nodes/`

Implementa steps finos do grafo. Cada node le `AgentState`, chama uma chain/tool/regra e
retorna um update parcial de estado.

`delegate-specialist` chama especialistas OpenAI Agents SDK quando `requires_specialist`
esta marcado no estado. Ele nao envia mensagem ao usuario e retorna apenas resultado
estruturado para o responder.

### `src/app/agent/chains/`

Monta prompts, modelos, structured outputs e compatibilidades de modelo usadas pelos nodes.

### `src/app/agent/prompts/`

Define nomes de prompts Langfuse e fallbacks locais.

O responder base adota voz consultiva, proxima e adaptavel, mas nao contem produto, ICP,
argumentos ou etapas comerciais de um cliente. Essas informacoes devem ser fornecidas pelo
projeto derivado como contexto confirmado.

O contrato de labels, versionamento e promocao desses prompts fica em
[`observabilidade-langfuse.md`](observabilidade-langfuse.md).

### `src/app/agent/tools/`

Espaco para tools expostas ao agente. Detalhes de API externa continuam em
`src/app/integrations/`.

### `src/app/agent/runtime.py`

Decide qual checkpointer usar depois da validacao de startup:

- `InMemorySaver` apenas em execucoes controladas que sobrescrevem o ambiente e deixam
  `DATABASE_URL` vazia
- `PostgresSaver` com `psycopg_pool.ConnectionPool` quando `DATABASE_URL` estiver
  configurada

`src/app/main.py` impede producao sem `DATABASE_URL`, portanto `InMemorySaver` nao e um
checkpointer de producao.

Tambem centraliza normalizacao de URL JDBC/Postgres, inferencia do schema pela URL,
configuracao do pool e bootstrap do schema do Postgres.

O mesmo pool e schema tambem atendem a idempotencia do webhook e o documento singleton de
configuracao operacional. O grafo e montado no startup; cada request recebe seu snapshot atual
de configuracao por meio da camada `application`, sem recompilar a topologia.

### `src/app/agent/service.py`

Concentra a execucao do agente e a leitura/serializacao do estado da thread.

Tambem abre a observacao raiz do agente e propaga `session_id`, `user_id`, `tags` e
`metadata` para o Langfuse quando a observabilidade esta habilitada.

O contrato interno completo de `src/app/agent/` esta em
[`arquitetura-agente.md`](arquitetura-agente.md).

## Persistencia

O projeto usa short-term memory por `thread_id`.

Boas praticas adotadas:

- toda execucao HTTP passa `configurable.thread_id`
- o perfil unico exige Postgres
- cada deploy usa um SDR/schema; o schema, quando necessario, e inferido da propria
  `DATABASE_URL`
- o `setup()` do Postgres roda em passo explicito, nao no startup da API

## Comandos

### Desenvolvimento local

```bash
make dev
```

O alvo carrega `.env` diretamente pelo `langgraph.json`. Esse arquivo ou a plataforma
fornece segredos e infraestrutura de bootstrap. Modelos, limites, audio, Langfuse e logs nao
sensíveis sao configuracao operacional persistida; consulte
[`configuracao-runtime.md`](configuracao-runtime.md).

### Bootstrap do checkpointer Postgres

```bash
make db-setup
```

Exemplo recomendado para um deploy isolado no database `postgres`:

```env
DATABASE_URL=postgresql://user:password@db.example.com:5432/postgres/sdr_cliente
```

### Stack self-hosted

```bash
make prod
```

## O que fica fora deste v1

Este scaffold ainda nao implementa:

- streaming
- autenticacao
- memoria de longo prazo
- endpoints estilo LangSmith
- regras comerciais da Pipefacil
- schema generico de `pending_goal`, fatos comerciais ou multiplos atos conversacionais

Esses campos dependem do dominio de cada SDR. O template documenta o padrao sem aumentar o
estado ou o grafo base.
