# Dataset Padrao Ouro

Este guia define como organizar exemplos padrao ouro para avaliar o SDR Pipefacil.

O objetivo nao e treinar o modelo no primeiro momento. O objetivo e ter uma referencia
confiavel para responder: dado este historico e este estado da conversa, qual deveria ser o
proximo comportamento correto do agente?

## Onde fica

```text
datasets/golden/
  manifest.json
  schema.json
  examples.jsonl
```

Esse conteudo e de desenvolvimento. Ele nao faz parte do runtime HTTP, do Dockerfile, do
grafo LangGraph, dos prompts de producao ou das dependencias da aplicacao.

O arquivo JSONL e a fonte versionada no Git. O Langfuse deve receber uma copia sincronizada
desse conteudo como Dataset/DatasetItems para execucao de experimentos e avaliacoes.

## Quando usar

Use o dataset para:

- testar regressao depois de mudar prompt, node, routing, tool ou regra;
- transformar traces bem-sucedidos em casos revisados;
- avaliar pontos no meio da conversa, nao apenas conversas iniciando do zero;
- documentar criterios de qualidade do agente;
- alinhar revisores humanos sobre o que e uma boa resposta.

## Tipos de caso

### Replay de conversa

O campo `input.messages_so_far` guarda o historico ate o corte escolhido. O avaliador roda
o agente a partir desse ponto e compara a proxima resposta com `expected`.

Esse formato e bom para testar:

- continuidade de contexto;
- nao repetir perguntas ja respondidas;
- retomar o fluxo apos uma objecao;
- manter tom e politicas comerciais;
- responder uma duvida antes de retomar o objetivo pendente;
- corrigir fatos, tratar mudancas de assunto e respeitar opt-out;
- manter coesao, naturalidade e progressao consultiva.

### Snapshot de estado

O campo `input.state_snapshot` guarda fatos extraidos ou estado interno relevante do
LangGraph naquele ponto da conversa.

Esse formato e bom para testar:

- roteamento;
- dados ja conhecidos;
- dados faltantes;
- resultado anterior de ferramenta;
- status intermediario ou final.

O snapshot deve guardar fatos brutos, nao prompt pronto.

## Como transformar trace em padrao ouro

1. Escolha um trace que representa bom comportamento.
2. Defina cortes uteis: inicio, meio da qualificacao, depois de ferramenta, handoff ou fim.
3. Remova dados pessoais e sensiveis.
4. Escreva o `expected.next_behavior`.
5. Escreva uma `ideal_response` curta, sem tentar cobrir todas as variacoes possiveis.
6. Liste `success_criteria` e `must_not`.
7. Marque como `candidate`.
8. Depois da revisao, mude para `approved`.

Trace real nao vira padrao ouro automaticamente. Ele vira materia-prima. O padrao ouro e o
trace revisado, anonimizado e anotado.

`ideal_response` e uma referencia de comportamento, nao um script para igualdade textual.
Para linguagem natural, os criterios determinantes ficam em `success_criteria`, `must_not` e
na rubrica revisada por humano.

## Status

- `candidate`: exemplo ainda em revisao.
- `approved`: exemplo aceito para regressao.
- `deprecated`: exemplo antigo ou substituido, mantido apenas como historico.

## Campos principais

- `id`: identificador estavel e curto.
- `source`: origem do caso, como `synthetic`, `langfuse_trace` ou `manual_trace_export`.
- `scenario`: titulo, descricao e tags para filtro.
- `cut`: onde a conversa foi cortada.
- `input.messages_so_far`: historico ate aquele ponto.
- `input.state_snapshot`: estado opcional do grafo naquele ponto.
- `expected.next_behavior`: comportamento esperado.
- `expected.ideal_response`: uma resposta de referencia.
- `expected.success_criteria`: criterios para passar.
- `expected.must_not`: comportamentos proibidos.
- `evaluation`: tipo de avaliacao e rubrica.

## Privacidade

Nunca versionar:

- nome real;
- telefone;
- email;
- CPF ou CNPJ;
- endereco completo;
- URL assinada;
- payload bruto de audio, imagem ou arquivo;
- token, segredo ou credencial.

Use placeholders como `[nome_removido]`, `[telefone_removido]` e `[trace_id_redigido]`.

## Validacao

Rode:

```bash
python scripts/validate_golden_dataset.py
```

ou:

```bash
make golden-dataset-validate
```

A validacao confere campos obrigatorios, enums, duplicidade de `id` e alguns padroes obvios
de PII.

## Sync com Langfuse

Para criar/atualizar o dataset no Langfuse:

```bash
python scripts/sync_langfuse_golden_dataset.py \
  --env-file .env
```

ou:

```bash
make golden-dataset-sync
```

O comando:

- cria o Dataset no Langfuse com o nome de `manifest.json`, se ele ainda nao existir;
- cria ou atualiza cada DatasetItem usando um ID deterministico por caso;
- envia `input.messages_so_far` e `input.state_snapshot` como input do item;
- envia `expected.next_behavior`, `ideal_response`, `success_criteria` e `must_not` como
  expected output;
- guarda scenario, corte, origem, status local e rubrica em metadata.

Por padrao sao sincronizados casos `candidate` e `approved`. Para publicar apenas casos
revisados:

```bash
python scripts/sync_langfuse_golden_dataset.py --status approved
```

## Padrao basico de avaliacao no Langfuse

Este template usa o Langfuse como camada visual e operacional para revisar Dataset Runs.
O Git continua sendo a fonte versionada dos casos (`examples.jsonl`), enquanto o Langfuse
guarda execucoes, traces, anotacoes humanas e correcoes.

O padrao minimo de avaliacao humana e:

- `answer_correct`: Score Config `BOOLEAN`. E o pass/fail principal do caso. Use `true`
  quando a resposta atende `expected.next_behavior`, `expected.ideal_response`,
  `success_criteria` e `must_not`; use `false` quando falha em algum criterio relevante.
- `failure_note`: Score Config `TEXT`. Texto livre para registrar o que deu errado, por
  que deu errado e qual orientacao deve guiar melhoria de prompt, regra, ferramenta ou
  dataset. Preencha principalmente quando `answer_correct=false`.
- `Corrected Output (Beta)`: campo nativo de correcao do Langfuse. Pela API ele e salvo
  como score `name=output` e `dataType=CORRECTION`. Use para escrever a resposta ideal
  que deveria ter sido gerada naquele trace.

Antes da primeira revisao em um projeto Langfuse novo, crie os Score Configs
`answer_correct` e `failure_note` com esses nomes e tipos. O sync do dataset envia o
contrato em metadata quando cria o Dataset, mas nao arquiva nem recria Score Configs
existentes.

Interprete os casos assim:

- `answer_correct=true`: o caso passou. `failure_note` e `Corrected Output` sao opcionais.
- `answer_correct=false`: o caso falhou. `failure_note` e obrigatorio para explicar a
  falha; `Corrected Output` e recomendado para registrar a resposta esperada.
- sem `answer_correct`: o item ainda nao foi revisado.

Em algumas versoes self-hosted, scores `TEXT` podem aparecer como coluna vazia na tabela de
Experiment Run mesmo quando foram salvos corretamente. Isso acontece porque o valor de
`failure_note` fica em `stringValue`, nao em `value`. Para analise e automacoes, puxe pela
API/SDK em vez de depender apenas da renderizacao da tabela.

## Fluxo de revisao humana

1. Rode o Dataset Run no Langfuse.
2. Abra cada trace da run e compare `Trace Input`, `Output` e `Expected Output`.
3. Marque `answer_correct`.
4. Se falhou, preencha `failure_note`.
5. Se falhou e houver resposta ideal clara, preencha `Corrected Output (Beta)`.
6. Depois da revisao, puxe scores/correcoes pela API para gerar backlog de melhorias.

## Como usar as anotacoes para melhorar o agente

Para uma rodada de melhoria, colete:

- input do DatasetItem;
- expected output do DatasetItem;
- output real do agente;
- score `answer_correct`;
- score `failure_note`;
- correcao `output` com `dataType=CORRECTION`;
- trace id, run name, modelo, prompt label e metadata da execucao.

Use esse pacote para:

- identificar padroes de falha recorrentes;
- ajustar prompt, roteamento, regra comercial ou ferramenta;
- transformar correcoes boas em novos casos `candidate`;
- promover casos revisados para `approved`;
- rodar regressao antes de publicar mudancas.

Quando houver volume suficiente de `failure_note`, considere criar um score categorico
opcional `failure_type` para agregacao, por exemplo `wrong_city`, `missing_context`,
`wrong_intent`, `robotic_tone`, `disconnected_response`, `ignored_context`, `feature_dump`,
`forced_cta`, `excessive_questions`, `pressure_after_refusal`, `incomplete_answer` e `other`.
Ele nao faz parte do minimo obrigatorio porque o texto livre e mais importante no inicio.

Para cenarios comerciais, prefira rubricas que tornem observaveis estas dimensoes:

- uso correto do contexto;
- resposta a mensagem atual;
- coesao e naturalidade;
- progressao consultiva;
- veracidade comercial;
- respeito a recusa.

Nao adicione LLM-as-a-judge ao quality gate apenas para pontuar prosa. O gate deterministico
valida contrato e estrutura; variacoes validas de linguagem continuam sob revisao humana no
Langfuse.

## Proxima evolucao

O proximo passo natural e criar um runner de avaliacao que leia o Dataset do Langfuse,
execute o agente com `messages_so_far` e salve resultados como Dataset Runs/scores.
