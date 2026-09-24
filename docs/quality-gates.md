# Quality Gate Deterministico

Este documento define o contrato automatizado usado para revisar codigo deste template,
inclusive codigo gerado por assistentes de IA. O gate avalia o diff e a arquitetura; ele nao
tenta descobrir quem escreveu o codigo e nao usa LLM-as-a-judge.

As fontes conceituais principais sao:

- [`derivacao-agente.md`](derivacao-agente.md) e
  [`arquitetura-agente.md`](arquitetura-agente.md);
- [Thinking in LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph);
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api);
- [Langfuse tracing best practices](https://langfuse.com/docs/observability/best-practices);
- [Langfuse masking](https://langfuse.com/docs/observability/features/masking);
- [Langfuse prompt version control](https://langfuse.com/docs/prompt-management/features/prompt-version-control).

## Como executar

```bash
make quality
```

O comando produz estes artefatos ignorados pelo Git:

- `build/quality/python-structure.md`;
- `build/quality/coverage.xml`;
- `build/quality/diff-coverage.md`.

Para comparar com outra base:

```bash
QUALITY_BASE_REF=<sha-ou-ref> make quality
```

A resolucao usa, em ordem, `--base`, `QUALITY_BASE_REF`, `origin/main` e `HEAD^`. No CI,
pull requests usam o SHA da base e pushes usam o SHA anterior do evento. Arquivos Python
novos e ainda nao rastreados sao considerados integralmente alterados.

## Catalogo de regras

Os IDs aparecem nos relatorios dos CLIs ou identificam contratos pytest discretos. "Global"
significa que legado invalido tambem bloqueia; "ratchet" significa que o limite so passa a
valer quando a funcao, modulo ou aresta e tocada.

| ID | Severidade | Limite ou contrato | Evidencia automatizada | Fonte |
| --- | --- | --- | --- | --- |
| `PY-COMPLEXITY-001` | Bloqueio | Complexidade 15+ em funcao alterada | AST + linhas do diff | [Guia de derivacao](derivacao-agente.md) |
| `PY-COMPLEXITY-002` | Alerta | Complexidade entre 11 e 14 em funcao alterada | AST + linhas do diff | [Guia de derivacao](derivacao-agente.md) |
| `PY-NODE-001` | Bloqueio | Complexidade maxima 8 em funcao alterada de node | AST + linhas do diff | [Thinking in LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph) |
| `PY-NODE-002` | Bloqueio | Maximo 40 linhas efetivas em funcao alterada de node | AST + linhas do diff | [Thinking in LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph) |
| `PY-NODE-003` | Bloqueio | Maximo 4 imports internos em modulo de node alterado | AST + paths do diff | [Arquitetura local](arquitetura-agente.md) |
| `ARCH-CYCLE-001` | Bloqueio global | Nenhum ciclo de imports, inclusive via `__init__.py` | Grafo de imports por AST | [Arquitetura local](arquitetura-aplicacao.md) |
| `ARCH-LAYER-001` | Bloqueio ratchet | Nenhuma aresta interna nova contraria as camadas | Diff entre grafos de imports | [Arquitetura local](arquitetura-aplicacao.md) |
| `ARCH-SDK-001` | Bloqueio global | SDKs permanecem nas camadas proprietarias | Imports por AST | [Arquitetura local](arquitetura-aplicacao.md) |
| `LG-NODE-001` | Bloqueio global | Node nao muta o estado, inclusive valores aninhados | AST de `agent/nodes` | [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| `LG-NODE-002` | Bloqueio global | Node nunca retorna `state` inteiro | AST de `agent/nodes` | [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| `LG-NODE-003` | Bloqueio global | Node registrado recebe `state: AgentState` e somente `config`/`runtime` extras | AST do grafo e das funcoes | [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| `LG-NODE-004` | Bloqueio global | Node registrado declara retorno `dict` ou `Command` | AST do grafo e das funcoes | [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| `LG-STATE-001` | Bloqueio global | Toda chave literal retornada existe em `AgentState` | AST de retornos e `Command.update` | [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| `LG-GRAPH-001/002` | Bloqueio global | Modulos do grafo existem e callables registrados podem ser inspecionados | AST de `graph.py` | [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| `LG-EDGE-001` | Bloqueio global | Node nao combina edge estatica com roteamento dinamico | AST de `graph.py` | [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| `LG-CMD-001` | Bloqueio global | `Command[Literal[...]]`, nodes registrados e `goto` sao coerentes | AST de anotacoes e chamadas | [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| `LG-ROUTE-001..004` | Bloqueio global | Router e puro, recebe `AgentState`, retorna `Literal` e aponta para nodes registrados | AST de `routing.py` e `graph.py` | [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| `TEST-COV-001` | Bloqueio ratchet | Minimo 85% para linhas e branches executaveis alterados | Diff Git + Cobertura XML | Politica local |
| `LF-TRACE-001` | Bloqueio global | Nomes estaveis, input/output uteis e `thread_id` propagado como sessao | `test_runtime.py`, `test_application.py` | [Langfuse best practices](https://langfuse.com/docs/observability/best-practices) |
| `LF-MASK-001` | Bloqueio global | Ambiente e masking ligados ao cliente; callbacks binarios sensiveis removidos | `test_langfuse_observability.py`, `test_runtime.py` | [Langfuse masking](https://langfuse.com/docs/observability/features/masking) |
| `LF-PROMPT-001` | Bloqueio global | Labels `staging`/`production`, fallback e vinculo de prompt preservados | `test_langfuse_prompts.py` | [Prompt version control](https://langfuse.com/docs/prompt-management/features/prompt-version-control) |
| `LF-FLUSH-001` | Bloqueio global | Cliente configurado recebe `flush` no encerramento | `test_langfuse_observability.py` + lifespan da aplicacao | [Langfuse best practices](https://langfuse.com/docs/observability/best-practices) |
| `LF-PROMOTION-001` | Bloqueio global | Promocao `production` so ocorre apos `quality` em push da `main` | `test_ci_workflow.py` | [Prompt version control](https://langfuse.com/docs/prompt-management/features/prompt-version-control) |

Complexidade entre 11 e 14 fora de `agent/nodes` gera o alerta
`PY-COMPLEXITY-002`, sem bloquear. O ratchet nao cobra refatoracao de uma funcao legada
intocada; ao tocar um node legado acima dos limites estritos, a mesma mudanca deve
simplifica-lo.

Linhas ausentes do XML de cobertura sao tratadas como nao executaveis. Quando nao existem
linhas executaveis ou branches alterados, a metrica correspondente vale 100% e nao cria um
bloqueio artificial.

## Fronteiras de dependencia

- Rotas HTTP delegam para `application`, sem importar `agent` ou `integrations`.
- `application` nao depende de `api`.
- `integrations` nao depende de `api`, `application` ou `agent`.
- `core` nao depende de camadas superiores; `observability` depende apenas de `core`.
- Nodes, chains, routing e state nao incorporam `application` ou `integrations`.
- Tools podem delegar para application/integrations, mas nao importam clientes HTTP.
- `httpx` pertence a `integrations`, FastAPI a `api`/`app.main`, LangGraph a `agent`,
  Langfuse a `observability` e OpenAI Agents SDK a `agent/specialists`.

## Contratos executaveis

Os testes complementam o AST com verificacoes sobre o grafo compilado:

- todos os nodes sao alcancaveis a partir de `START` e conseguem chegar a `END`;
- loops sao aceitos somente quando seus nodes ainda possuem caminho de saida;
- um node nao combina edges condicionais e estaticas;
- nomes de nodes sao estaveis e de baixa cardinalidade;
- `messages` preserva reducer acumulativo.

Para Langfuse, a suite preserva nomes estaveis, input/output legiveis, `session_id`, ambiente,
masking no export OpenTelemetry, supressao de callbacks multimodais sensiveis, fallbacks,
labels `staging`/`production`, flush e promocao de prompts depois do job `quality`. Esses
testes usam fakes e mocks; o projeto nao instala um bloqueador global de rede.

## Limites da automacao

Sem inferencia nao e possivel provar que uma regra de negocio esta correta, que um estado e
semanticamente "bruto" ou que um trace contem todo o contexto util. O gate usa proxies
objetivos — tamanho, complexidade, dependencias, topologia e cobertura — e a suite deve
continuar adicionando cenarios discretos para cada comportamento de negocio alterado.

Alterar limites, criar exclusoes, suprimir achados ou pular testes obrigatorios e uma mudanca
de politica, nao uma correcao comum. Isso exige solicitacao explicita e atualizacao deste
documento junto com os testes do gate.

O workflow roda em PRs e pushes para `main`, publica os relatorios e impede a promocao de
prompts quando falha. A branch nao possui status obrigatorio porque a protecao desse
repositorio privado depende de um plano GitHub que ofereca o recurso; `make quality` continua
sendo a verificacao obrigatoria antes do push.
