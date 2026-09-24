# Como Derivar e Desenvolver um SDR a Partir Desta Base

Este e o guia unico para transformar este template em um SDR de um cliente. Leia-o antes de
alterar `AgentState`, nodes, chains, routing, prompts ou tools.

Ele evita dois erros recorrentes: tratar o grafo minimo como fluxo comercial completo e
concentrar interpretacao, regra de negocio, chamadas externas e resposta em um node grande.
Os limites das camadas continuam em [`arquitetura-agente.md`](arquitetura-agente.md), e o
contrato de contexto e comunicacao comercial fica em
[`modelagem-conversacional.md`](modelagem-conversacional.md).

## Comece pelo processo, nao pelo prompt

Antes de escrever prompt ou codigo, descreva:

- qual problema comercial o SDR resolve;
- os dados que precisa obter e quais fontes os confirmam;
- regras de qualificacao, cobertura, elegibilidade e exclusao;
- resultados, status e criterios de handoff humano;
- efeitos externos e quais deles devem aparecer imediatamente no Pipefacil;
- excecoes, retries e o que precisa sobreviver entre turnos.

Perguntas que precisam ter resposta antes da implementacao incluem:

- O que torna um lead `apto`, `em_analise`, `fora_cobertura` ou `perdido`?
- O que ocorre quando ele responde parcialmente, corrige um dado ou envia uma imagem depois?
- Como responder uma duvida ou objecao sem perder a etapa comercial pendente?
- Quando parar, encaminhar para humano ou tentar novamente uma integracao?

O objetivo e modelar um processo claro. Um prompt nao substitui regra de negocio, politica
comercial nem arquitetura de recuperacao.

## O grafo atual e um scaffold, nao um fluxo comercial pronto

O fluxo inicial existe para tornar a base executavel e testavel:

```text
START -> classify-intent -> respond -> END
```

Ele nao e a arquitetura recomendada para um SDR que qualifica, consulta Pipefacil, aplica
regras comerciais, trata objecoes ou dispara acoes. Um fluxo derivado pode ser, por exemplo:

```text
interpretar-turno
  -> atualizar-fatos
  -> decidir-proxima-etapa
  -> [buscar-pipefacil | validar-cobertura | qualificar | tratar-objecao | handoff]
  -> compor-resposta
```

Os nomes e a ordem dependem do dominio. O objetivo nao e aumentar a quantidade de nodes; e
deixar cada decisao, efeito externo e ponto de recuperacao explicito, observavel e testavel.

## Nodes: fronteiras e responsabilidades

Crie um node quando a responsabilidade tiver pelo menos uma destas propriedades:

| Situacao | Exemplo |
| --- | --- |
| Decide uma regra de negocio | avaliar cobertura ou elegibilidade |
| Interpreta significado conversacional | identificar fatos, correcao, recusa ou objecao |
| Consulta dados ou ferramenta | buscar lead ou disponibilidade |
| Produz efeito externo | atualizar etapa no Pipefacil ou criar handoff |
| Precisa de retry, checkpoint ou falha propria | consulta temporariamente indisponivel |
| Merece teste e observabilidade independentes | tratar objecao de preco |

Pense nos grupos `LLM node`, `data node`, `action node` e `human node`. Essa separacao deixa
claro o que depende de modelo, dados externos, efeito operacional ou intervencao humana.

Nao crie um node apenas para converter um campo ou montar um dicionario local. Essas
transformacoes pertencem ao node que tem a responsabilidade coesa. Pelo mesmo motivo, nao use
um unico `resolver_conversa` para classificar mensagem, chamar Pipefacil, aplicar regras,
alterar sistemas e redigir a resposta.

Cada node le o estado, executa uma responsabilidade e retorna somente o update parcial que
produziu. Ele nao deve mutar o estado. O router le esse estado validado e decide o proximo
destino; ele nao chama LLM nem interpreta a mensagem original.

## Estado, fatos e memoria

Guarde no estado dados brutos e reutilizaveis, nunca prompts inteiros ou texto previamente
formatado. Em um SDR derivado, geralmente sao uteis:

- fatos conhecidos e a fonte que confirmou cada um;
- classificacao e interpretacao estruturada do turno atual;
- etapa conversacional e objetivo pendente;
- evidencia recebida, resultado de ferramenta e motivo de decisao;
- status de handoff e operacoes pendentes de sincronizacao.

Se o dado precisa sobreviver entre nodes ou turnos, guarde-o. Se pode ser reconstruido com
seguranca e baixo custo, gere-o sob demanda. Quando fontes forem conflitantes, o dominio deve
definir a precedencia; por exemplo, uma foto validada pode substituir um valor digitado. Registre
a origem da confirmacao, reavalie a regra aplicavel e nunca assuma que o primeiro dado e final.

`pending_goal` representa o resultado ainda necessario, como `confirm_delivery_city`, e nao a
ultima frase enviada. Assim uma pergunta lateral pode ser respondida sem reiniciar a triagem ou
repetir algo que ja foi confirmado.

## Interpretacao semantica: saida estruturada, nao parsing de texto

Use inferencia do LLM com saida estruturada nativa sempre que o significado da conversa puder
alterar estado, routing, qualificacao, handoff ou uma acao. Isso inclui, em especial:

- atos presentes no turno;
- fatos novos e fatos corrigidos;
- perguntas, objecoes, recusa e pedido de humano;
- etapa conversacional e objetivo pendente;
- classificacoes que habilitam regra comercial.

O schema representa o dominio do cliente. Prefira enums e campos tipados para valores que
controlam transicao. A chain configura `with_structured_output`; a validacao converte a resposta
do provedor para o objeto tipado antes de atualizar estado ou rotear.

Nao use para semantica conversacional:

- `json.loads` ou extracao de bloco JSON de uma resposta textual;
- regex, busca de palavras ou `if "preco" in mensagem` para decidir intencao, interesse,
  objecao, recusa ou mudanca de assunto;
- campo textual livre que o router precisara interpretar novamente.

Regex e parsing continuam adequados para formatos formais e deterministas: payload HTTP,
assinatura, comando exato, ID, codigo, data, telefone e validacao de contrato. Depois da
interpretacao validada, regras de negocio e routing voltam a ser codigo deterministico.

Um turno pode conter resposta parcial, fato novo, pergunta, objecao, correcao e recusa ao
mesmo tempo. Responda primeiro ao que o lead trouxe, preserve o contexto e retome exatamente o
objetivo pendente quando a conversa continuar. Em recusa ou opt-out, encerre sem nova tentativa
de persuasao.

## Regras comerciais e routing

Regras criticas precisam ser objetivas no codigo ou no estado, nao escondidas em linguagem
vaga do prompt. Para toda decisao importante deve ser possivel responder qual regra foi aplicada,
com qual evidencia e em qual node.

Exemplos: estados atendidos, valor minimo, quando uma foto e obrigatoria, precedencia entre
foto e texto, criterio de perda, limite de insistencia e handoff humano.

Routers sao puros e retornam apenas destinos registrados. Use `add_edge` em fluxo fixo e
`add_conditional_edges` ou `Command` quando houver decisao dinamica. A rota decide sobre fatos
estruturados, nao sobre prosa, regex ou resultado de HTTP.

Defina um conjunto pequeno de status, inclusive os intermediarios. Cada status deve ter criterio
de entrada, resposta esperada e proximo passo operacional. O handoff humano entra para excecao
real, como conflito de evidencia, risco financeiro, irritacao, fora de regra ou baixa confianca
relevante — nao como fuga generica de automacao.

## Sincronizacao imediata da projecao do Pipefacil

O estado local nao pode se tornar mais atual que o Pipefacil. Sempre que decisao comercial
confirmar ou corrigir dado que deve aparecer no Pipefacil — campo de lead, tag, responsavel,
status, etapa de deal ou handoff — a mesma atualizacao de estado deve criar uma operacao de
sincronizacao pendente e duravel. Nunca acumule esses campos para enviar apenas ao encerrar a
conversa ou ao mudar outra etapa.

Modele a pendencia como um outbox de operacoes, por exemplo `pipefacil_sync_outbox`, e nao como
um campo unico que uma mudanca posterior pode sobrescrever. Cada item identifica entidade,
campos ou acao, versao e chave de idempotencia.

```text
node que confirma a decisao
  -> atualiza estado local + adiciona item ao pipefacil_sync_outbox na mesma transicao
  -> sync-pipefacil (action node imediato)
  -> confirma receipt no estado
  -> proximo node comercial ou resposta

falha de sync-pipefacil
  -> item permanece no outbox + erro e tentativa registrados
  -> retry idempotente ou recuperacao operacional
```

Uma decisao pode enviar numa so operacao campos que ela acabou de confirmar. Isso evita
chamadas redundantes, mas nao autoriza postergar uma decisao comercial independente.

`sync-pipefacil` recebe operacao ja validada, delega a um caso de uso em `application`, que
usa o cliente em `integrations`; o node nao implementa HTTP ou contrato Pipefacil. A chave de
idempotencia deve ser estavel, em geral derivada de thread, entidade e versao da mudanca. Em
sucesso, grave receipt, data ou versao sincronizada. Em erro, mantenha payload, tentativas e um
erro seguro para que a repeticao nao perca nem duplique a mudanca.

Nao ha transacao atomica entre checkpoint LangGraph e API Pipefacil. O outbox duravel permite
encontrar a operacao se o processo parar depois de persistir estado. O fluxo conversa deve tentar
sincronizar imediatamente; se a garantia tambem for necessaria quando a conversa para apos uma
falha, o SDR derivado precisa de worker ou agenda de retry. Esta base nao inclui fila nem retry
distribuido.

## Erros, prompts e comunicacao

Trate erros por tipo:

- transiente: timeout, rate limit ou indisponibilidade; aplique retry delimitado;
- recuperavel pelo agente: estado ou resultado insuficiente; registre o contexto e siga rota
  prevista;
- dependente do usuario: falta uma foto ou dado; explique o que falta e pause o objetivo;
- inesperado: nao esconda com fallback generico; deixe propagar, registre e investigue.

Prompts orientam comportamento local do node: papel, contexto relevante, schema de saida e
limites. Eles nao devem conter todo o processo, regra comercial, UX e fallback em um bloco
monolitico. O guia de [`modelagem-conversacional.md`](modelagem-conversacional.md) detalha
escuta ativa, descoberta, beneficio contextualizado, objecao, microcompromissos e limites de
seguranca comercial.

## Sequencia obrigatoria para expandir o template

1. Descreva status, regras, efeitos externos, excecoes, handoff e projecoes imediatas no
   Pipefacil.
2. Desenhe o fluxo por responsabilidades e defina o que precisa sobreviver entre turnos.
3. Adicione fatos reutilizaveis, fontes, objetivo pendente e resultados estruturados ao estado.
4. Defina schemas para cada decisao semantica que altera o fluxo.
5. Implemente chains de inferencia e nodes coesos para interpretacao, regras, dados e acoes.
6. Para Pipefacil, crie outbox, sync imediato, receipt, idempotencia e plano de retry.
7. Modele em `routing.py` transicoes puras sobre estado validado e registre nodes e edges em
   `graph.py`.
8. Cubra nodes, rotas e cenarios de interrupcao, correcao, objecao, recusa e falha de
   ferramenta.

Antes de encerrar alteracao de codigo, execute `make quality` na raiz.

## Checklists

Antes de criar um node, confirme:

- ele tem uma responsabilidade coesa e precisa mesmo existir;
- LLM, dado externo, efeito e intervencao humana estao separados;
- entrada, update parcial, regra e rotas estao claros;
- falha, retry, handoff e teste necessario estao definidos.

Antes de considerar um SDR pronto, confirme:

- regras criticas e status estao definidos e rastreaveis;
- perguntas laterais, objecoes, correcoes e opt-out nao quebram o objetivo pendente;
- dados conflitantes provocam reavaliacao;
- mudancas projetadas no Pipefacil tem outbox, idempotencia e receipt;
- logs e traces explicam classificacao, regra e ponto do grafo;
- cenarios conversacionais e de integracao foram testados;
- respostas respeitam contexto, nao inventam capacidades e nao insistem apos recusa.

## Instrucoes para agentes de codigo

As regras deste guia fazem parte do contrato de qualquer pedido para criar ou alterar um SDR.
Quando o pedido nao especificar o fluxo, identifique primeiro dados persistentes, decisoes de
negocio, efeitos externos e fronteiras de node; nunca conclua que o grafo minimo basta.

Uma implementacao esta incompleta se depende de parsing de linguagem natural para tomar decisao
comercial que deveria resultar de classificacao estruturada. Muitos nodes triviais tambem nao
melhoram o fluxo: a fronteira correta e uma responsabilidade coesa, observavel e testavel.
