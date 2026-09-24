# Mapeamento PipeFácil — Liana

Snapshot de leitura da API pública do workspace em 2026-09-24. Não foram alterados leads.

## Funil e avanço

O funil padrão ativo é **Funil De Aquisição** (seq `5`, id `4adb0e22-cefe-4da3-9e06-245efa87ee2b`). O workspace também tem funis de Reengajamento, Retenção e Instagram; os eventos do agente devem avançar apenas negócios no funil de Aquisição.

| Chave interna | Etapa | ID da etapa | Ordem |
| --- | --- | --- | ---: |
| `entry` | Etapa de Entrada | `a981eeb4-7cd6-4cfd-a736-5c08c93d8510` | 1 |
| `qualification` | Qualificação | `f738b9b6-e94e-4075-ae9d-6585b34f1f6b` | 2 |
| `appointment` | Agendamento | `003bf3ef-cb11-4236-9c4e-361b05f8f628` | 3 |
| `attended_no_sale` | Compareceu (não fechou) | `a4eaa712-03ba-4212-8ca1-7cc317aafb9a` | 4 |
| `unresponsive` | Não Respondeu/oportunidade | `8765a8bd-c319-4217-b620-3e735ebda00c` | 5 |
| `courtesy` | Cortesias | `dc23fb16-c27e-4145-8a78-e073a918ccc2` | 6 |
| `won` | Ganho | `5ad9b3ad-45c6-4307-8505-943a88914f81` | 7 |
| `lost` | Venda perdida | `c6bc20ea-92f1-448f-b1de-ea01fa0b9cd4` | 8 |

Critério comercial confirmado: avançar para **Qualificação** quando houver interesse explícito e objetivo declarado; avançar para **Agendamento** quando a pessoa pedir horário ou indicar período. Liana não move para Ganho ou Venda perdida. Todas as etapas retornaram `isRequired=false`; não foram encontrados campos obrigatórios no funil consultado.

O agente interpreta os sinais com saída estruturada e só considera sinais com confiança mínima de `0.75`. Interesse e objetivo podem ser registrados em mensagens diferentes da mesma conversa. Um pedido explícito de horário/período pode levar diretamente a Agendamento. Se o negócio já estiver em etapa posterior, o sincronizador não o move para trás.

## Campos do negócio

| Campo | Slug | Tipo/opções atuais | Uso proposto |
| --- | --- | --- | --- |
| Atendente | `atendente` | Select: `liana`, `mayara`, `Angela` | Pode receber o valor literal `liana`, pois identifica a assistente que atende. |
| Especialidade Procurada | `especialidade_procurada` | API informa `text`, mas também devolve opções | Só preencher a partir de objetivo declarado pela pessoa. A combinação de tipo `text` com opções precisa ser validada antes de automatizar escrita. |
| Qualificação de Leads | `qualificacao_mkt` | Select: `mql`, `sql` | Gravar `mql` em Qualificação e manter `mql` em Agendamento. |
| Qualificação Vendas | `qualificacao_vendas` | Select: `sql` | Gravar `sql` ao avançar para Agendamento. |
| Pagamento | `pagamento` | Select, com opções de PIX/cartão e variantes de à vista | Não inferir preferência nem pagamento realizado. |
| Consulta Agendada | `consulta_agendada` | Data e hora | Liana não agenda nem confirma horário; deixar para atualização humana. |
| Venda | `venda` | Moeda | Não preencher com o preço da consulta; preço não significa venda fechada. |
| Fonte do Lead | `fonte_do_lead` | Select: Google, Meta Ads, indicação, Unimed, Instagram, etc. | Não inferir a origem a partir de uma conversa recebida pelo WhatsApp. |
| Motivo de perda | `motivo_de_perda` | Select | Não preencher; Liana não marca lead como perdido. |

Os outros campos de programas e consultas são de acompanhamento de pacientes e não se aplicam ao primeiro atendimento da Liana. A API não retornou campos customizados da entidade contato.

## Contrato de escrita

O `PATCH /api/v1/deals/{seq}` aceita `stageId` e `customFields` no mesmo corpo; `customFields` usa slugs. A atualização da etapa deve ser idempotente, manter uma operação durável pendente até confirmação e registrar receipt ou falha. A API exige `lostReason` ao mover para uma etapa de perda; o agente não deve fazer essa transição.

Na implementação, o graph registra a solicitação de sincronização no estado e segue imediatamente para um node de ação dedicado. A aplicação grava uma operação idempotente em `pipefacil_sync_outbox`, consulta novamente o negócio antes do PATCH, valida funil/etapa e então atualiza etapa e campos em uma única requisição. O worker PostgreSQL reprocessa falhas com backoff, guarda receipt ou erro e ignora operações antigas se o negócio já tiver avançado. Sem banco PostgreSQL, essa integração fica desativada para não prometer uma fila durável.

Os campos `especialidade_procurada`, pagamento, consulta agendada, venda, fonte e motivo de perda ficam fora da escrita automática: a API reporta conflito de tipo/opções para especialidade, e os demais exigem confirmação humana ou informação que a conversa não determina com segurança.

Referências: [API pública e OpenAPI](https://developers.matchsales.com.br/api/), [campos personalizados](https://developers.matchsales.com.br/configuracoes/campos/), [mover um lead](https://developers.matchsales.com.br/pipeline/mover-lead/).
