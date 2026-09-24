# Modelagem Conversacional e Venda Consultiva

Este guia define como transformar linguagem livre em um fluxo comercial previsivel sem
transformar o template em um roteiro rigido ou em um prompt monolitico.

O principio central e:

> A IA interpreta linguagem e contexto. O LangGraph preserva estado e coordena decisoes
> previsiveis. A IA volta a realizar a resposta em linguagem natural.

Isso nao contradiz o LangGraph. A documentacao oficial recomenda usar LLM para passos que
precisam compreender ou gerar linguagem, guardar dados brutos no estado e tomar transicoes
a partir de resultados estruturados.

Este template implementa apenas o fluxo minimo. Os schemas e exemplos abaixo orientam SDRs
derivados; eles nao sao campos obrigatorios do `AgentState` base.

## Fluxo recomendado

```text
mensagem + historico + contexto do cliente
                 |
                 v
       interpretacao estruturada pela IA
                 |
                 v
       atualizacao de fatos e objetivo pendente
                 |
                 v
          decisao deterministica do grafo
                 |
                 v
       estrategia consultiva para o turno
                 |
                 v
          resposta natural e coerente
```

Separe essas responsabilidades mesmo quando, em um agente pequeno, duas delas ainda usam a
mesma chamada de modelo. A separacao conceitual evita que regras comerciais acabem escondidas
na redacao do prompt.

## 1. Contexto estatico e contexto dinamico

O contexto estatico descreve o cliente e muda por versao ou configuracao:

- produto, servico e publico ideal;
- capacidades confirmadas e limites da oferta;
- alegacoes comerciais permitidas;
- politica de preco, cobertura, handoff e opt-out;
- voz da marca e termos que devem ou nao ser usados.

O contexto dinamico descreve a conversa atual:

- mensagens relevantes do historico;
- fatos fornecidos pelo lead e suas fontes;
- duvidas, objecoes e correcoes ainda relevantes;
- etapa comercial e objetivo pendente;
- resultados do Pipefacil, ferramentas ou especialistas.

Nao copie todo o Pipefacil nem um manual inteiro para cada chamada. Selecione apenas o contexto
necessario para a decisao e a resposta daquele node. Nunca trate uma hipotese do modelo como
fato confirmado do cliente.

## 2. Interprete o turno antes de decidir o caminho

Uma mensagem nao tem necessariamente uma unica intencao. Ela pode, ao mesmo tempo:

- responder parcialmente a uma pergunta anterior;
- fornecer um fato novo;
- corrigir uma informacao antiga;
- fazer uma pergunta lateral;
- levantar uma objecao;
- mudar de assunto;
- recusar ou pedir encerramento.

Quando esses sinais alterarem o fluxo, use structured output. Um resultado ilustrativo pode
ser:

```json
{
  "acts": ["answer", "question", "objection"],
  "new_facts": {"team_size": 8},
  "corrected_facts": {},
  "question": "Como funciona a implantacao?",
  "objection": "price",
  "refusal": false
}
```

O schema real deve usar nomes e enums do dominio do cliente. Evite campos livres quando o
valor controla routing. Se a interpretacao estiver ambigua e a decisao tiver impacto
comercial, mantenha o estado atual e faca uma pergunta de esclarecimento.

Use saida estruturada nativa da chain e validacao tipada. Nao peca um JSON em texto para
depois extrair bloco, rodar `json.loads` ou tentar reparar a resposta. A conversao tecnica da
resposta estruturada para o schema do dominio e normal; o parsing fragil de prosa do modelo
nao e. Consulte [`derivacao-agente.md`](derivacao-agente.md) para a regra completa.

## 3. Regex nao substitui compreensao semantica

Use regex ou comparacao direta para sintaxe deterministica:

- comando exato `/reset`;
- IDs, codigos, datas e formatos conhecidos;
- validacao de campos com contrato formal;
- sanitizacao e regras de transporte.

Nao use listas crescentes de palavras ou regex para concluir que o lead:

- tem interesse ou perdeu interesse;
- respondeu uma qualificacao;
- apresentou uma objecao;
- mudou de assunto;
- quer comprar, negociar ou falar com humano.

Esses significados dependem de contexto e devem ser interpretados pelo modelo com saida
estruturada. Depois da interpretacao, codigo deterministico valida os campos, atualiza o
estado e escolhe a transicao.

## 4. Estado e objetivo pendente

Guarde fatos brutos e reutilizaveis. Para um SDR derivado, normalmente importa distinguir:

- `known_facts`: informacoes confirmadas do lead;
- `fact_sources`: mensagem, Pipefacil, documento ou ferramenta que confirmou cada fato;
- `pending_goal`: objetivo comercial que continua aberto;
- `conversation_stage`: etapa atual do processo;
- `last_interpretation`: resultado estruturado do turno, quando passos seguintes precisam
  dele.

Quando um desses fatos tambem precisa aparecer no Pipefacil, o estado derivado deve carregar uma
operacao pendente de sincronizacao, seu receipt ou erro e uma chave de idempotencia. O node que
confirmou o fato cria essa pendencia na mesma atualizacao; o proximo node de acao a entrega ao
Pipefacil imediatamente. O fluxo e a recuperacao quando a conversa para estao definidos em
[`derivacao-agente.md`](derivacao-agente.md#sincronizacao-imediata-da-projecao-do-pipefacil).

`pending_goal` nao e a ultima frase do agente. E o resultado ainda necessario, por exemplo
`understand_current_process` ou `confirm_delivery_city`. Assim uma pergunta lateral pode ser
respondida sem perder o que faltava.

Nao pergunte novamente um dado ja confirmado. Quando o lead corrigir um fato, atualize o
valor e preserve a origem da correcao. Quando fontes entrarem em conflito, aplique a
precedencia explicita do dominio ou encaminhe para validacao.

## 5. Routing usa fatos estruturados

Routers devem ser puros: recebem estado e devolvem um destino conhecido. Eles nao chamam
LLM, Langfuse, HTTP, banco ou Pipefacil.

```text
interpretacao.refusal=true        -> encerrar
interpretacao.question!=null      -> responder-duvida
estado.tem_dados_para_validar     -> validar-regra
estado.pending_goal!=null         -> continuar-descoberta
caso contrario                    -> responder
```

Essa ordem e apenas ilustrativa. Cada projeto deve definir sua precedencia, especialmente
quando um turno traz mais de um ato. A decisao importante precisa permanecer testavel e
rastreavel fora do texto final do modelo.

## 6. Mudancas de assunto sem perder o contexto

Para uma interrupcao relacionada ao objetivo comercial:

1. responda primeiro a duvida ou objecao atual;
2. use apenas fatos confirmados;
3. preserve o objetivo pendente;
4. retome com uma transicao natural e uma pergunta util.

Para uma pergunta fora do escopo, responda de forma breve que aquele assunto nao faz parte
do atendimento e reconecte ao objetivo apenas se o lead nao estiver encerrando. Nao tente
vencer debates sem relacao com a oferta.

Para uma recusa, opt-out ou despedida clara, confirme o encerramento sem nova tentativa de
persuasao. Uma finalizacao real nao precisa terminar com pergunta.

## 7. Estrategia consultiva para o turno

Antes de escrever, escolha o trabalho principal da resposta:

- esclarecer uma duvida;
- compreender melhor a situacao;
- responder uma objecao;
- conectar uma capacidade confirmada a uma necessidade conhecida;
- propor um proximo passo;
- encerrar ou transferir.

Uma heuristica curta para conversas em andamento e:

1. responda ao que o lead realmente trouxe agora;
2. conecte a resposta ao contexto conhecido, quando isso acrescentar valor;
3. deixe uma pergunta aberta e util que avance a descoberta ou o proximo passo.

Isso nao exige CTA em toda mensagem. A pergunta deve ter proposito e pode ser omitida em
recusa, despedida, handoff concluido ou outra finalizacao. Evite o fechamento vazio "Posso
ajudar em algo mais?" quando existe uma pergunta especifica melhor.

## 8. Caixa pratica de venda consultiva

Use estas tecnicas como ferramentas, nao como um script obrigatorio.

### Escuta ativa

- use os termos relevantes do lead sem imitar erros ou girias;
- reconheca a preocupacao apenas quando isso acrescentar compreensao;
- nao repita toda a frase do lead para provar que entendeu;
- consuma os fatos ja informados antes de perguntar algo novo.

### Descoberta progressiva

- pergunte o que muda a recomendacao ou o proximo passo;
- prefira uma pergunta principal por turno;
- evite interrogatorios e listas de campos;
- SPIN pode ajudar a explorar situacao, problema, impacto e necessidade, mas nao deve virar
  uma sequencia mecanica.

### Beneficio contextualizado

- relacione no maximo um ou dois beneficios a uma dor declarada;
- fale de efeito pratico, nao apenas de funcionalidades;
- nao use um beneficio que dependa de capacidade ainda nao confirmada;
- nao despeje o catalogo inteiro para preencher silencio.

### Tratamento de objecao

1. compreenda a preocupacao sem confirmacao automatica;
2. responda com fatos conhecidos ou diga claramente o que precisa ser verificado;
3. conecte valor somente quando houver relevancia real;
4. continue com uma pergunta util, se a conversa permanecer aberta.

Nao ataque concorrentes, nao fique defensivo e nao esconda a resposta para forcar
qualificacao.

### Microcompromissos

Prefira o menor proximo passo que reduza incerteza: responder uma pergunta, confirmar um
dado, comparar um cenario ou autorizar uma consulta. Nao pressione por reuniao, proposta ou
fechamento antes de existir contexto suficiente.

## 9. Coesao e naturalidade

Uma resposta natural nao depende de informalidade. Ela depende de continuidade.

- comece pela resposta principal, nao por elogio automatico;
- mantenha uma ideia principal por mensagem;
- conecte frases relacionadas com transicoes naturais;
- adapte formalidade, vocabulario e tamanho ao lead;
- nao use o nome do lead em toda mensagem;
- evite iniciar todo turno com "Perfeito", "Otimo", "Excelente" ou "Claro";
- nao misture varios CTAs, perguntas ou beneficios no mesmo fechamento;
- nao force giria, intimidade ou entusiasmo que o contexto nao sustenta.

O agente pode ser caloroso sem inventar proximidade e comercial sem parecer um anuncio.

## 10. Seguranca comercial minima

Nunca invente:

- preco, desconto ou condicao;
- prazo, estoque, integracao ou disponibilidade;
- resultado garantido, comparacao ou prova social;
- urgencia, escassez ou consequencia artificial;
- acao que uma ferramenta ou pessoa ainda nao confirmou.

Respeite pedidos de parada e canais de opt-out. Persuasao neste template significa clareza,
relevancia e reducao de incerteza, nao insistencia.

## 11. Como avaliar

Nao avalie apenas se a resposta contem palavras esperadas. Revise se ela:

- usa corretamente o contexto disponivel;
- responde a mensagem atual antes de retomar o fluxo;
- nao repete pergunta ja respondida;
- mantem coesao entre resposta, beneficio e proximo passo;
- faz uma pergunta util sem transformar o turno em interrogatorio;
- evita alegacoes nao confirmadas e pressao;
- encerra sem nova pergunta quando existe recusa clara.

As `ideal_response` do dataset sao referencias de comportamento, nao textos para igualdade
exata. Use `success_criteria`, `must_not` e revisao humana no Langfuse para avaliar variacoes
validas. Testes deterministas devem validar schemas, contratos e invariantes observaveis; nao
devem fingir que conseguem inferir qualidade de prosa.

## Referencias oficiais

- [Thinking in LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph)
- [Context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [Langfuse observability best practices](https://langfuse.com/docs/observability/best-practices)
- [Langfuse prompt version control](https://langfuse.com/docs/prompt-management/features/prompt-version-control)
- [Langfuse datasets](https://langfuse.com/docs/evaluation/experiments/datasets)
