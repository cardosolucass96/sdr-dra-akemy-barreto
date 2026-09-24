# Golden Dataset

Este diretorio guarda a base versionada de exemplos padrao ouro do SDR Pipefacil.

Use este dataset para avaliar comportamento conversacional esperado sem depender de dados
reais de clientes. Traces reais podem virar exemplos daqui, mas antes precisam ser
anonimizados, revisados e marcados como `approved`.

As respostas ideais sao referencias, nao scripts exatos. Avalie tambem uso de contexto,
resposta a duvidas e objecoes, coesao, progressao consultiva, veracidade e respeito a recusa
pelos campos `success_criteria`, `must_not` e `rubric`.

## Arquivos

- `manifest.json`: metadados do dataset, politica de revisao e arquivo principal.
- `schema.json`: contrato JSON Schema para cada linha do arquivo JSONL.
- `examples.jsonl`: exemplos iniciais em JSON Lines.

## Ciclo de vida

- `candidate`: caso extraido ou escrito, ainda sem revisao final.
- `approved`: caso revisado e aceito como referencia de qualidade.
- `deprecated`: caso mantido por historico, mas fora da suite principal.

## Como validar

```bash
python scripts/validate_golden_dataset.py
```

ou:

```bash
make golden-dataset-validate
```

## Como enviar para o Langfuse

O JSONL versionado e a fonte revisavel no Git. Para publicar ou atualizar os casos como
DatasetItems no Langfuse:

```bash
python scripts/sync_langfuse_golden_dataset.py \
  --env-file .env
```

ou:

```bash
make golden-dataset-sync
```

Por padrao, o sync envia casos `candidate` e `approved` para o dataset definido em
`manifest.json`. Use `--status approved` quando quiser publicar apenas casos revisados.

## Avaliacao minima no Langfuse

Depois de rodar um Dataset Run, revise cada trace com este padrao:

- `answer_correct` (`BOOLEAN`): `true` se a resposta passou, `false` se falhou.
- `failure_note` (`TEXT`): texto livre sobre o que deu errado e como melhorar. Preencha
  principalmente quando `answer_correct=false`.
- `Corrected Output (Beta)`: resposta ideal. Pela API do Langfuse, isso e um score
  `output` com `dataType=CORRECTION`.

`failure_note` pode nao renderizar na tabela de Experiment Run em algumas versoes
self-hosted, mas fica salvo em `stringValue` e pode ser puxado pela API/SDK.

Veja o contrato completo em [`docs/dataset-padrao-ouro.md`](../../docs/dataset-padrao-ouro.md).

## Regra de seguranca

Nao versionar nome real, telefone, email, CNPJ, CPF, endereco completo, URL assinada,
payload bruto de midia ou qualquer segredo. Use placeholders explicitos, por exemplo
`[nome_removido]`, `[telefone_removido]` ou `[trace_id_redigido]`.
