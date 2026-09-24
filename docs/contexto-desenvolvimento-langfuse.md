# Contexto de Desenvolvimento Langfuse

Este repositorio deve carregar contexto atualizado da documentacao Langfuse para quem for
manter observabilidade, tracing, prompt management e versionamento de prompts.

Essas configuracoes sao **somente de desenvolvimento**. Elas nao fazem parte do runtime da
API, nao entram no Dockerfile de producao e nao devem virar dependencia obrigatoria da
aplicacao.

## Fontes Oficiais

- Indice principal para assistentes: https://langfuse.com/llms.txt
- Indice completo de paginas de documentacao: https://langfuse.com/llms-docs.txt
- MCP publico de documentacao: https://langfuse.com/api/mcp
- Guia do MCP de documentacao: https://langfuse.com/docs/docs-mcp
- Busca REST de docs: https://langfuse.com/api/search-docs

Paginas importantes antes de mexer neste template:

- Observability overview: https://langfuse.com/docs/observability/overview
- Tracing best practices: https://langfuse.com/docs/observability/best-practices
- Prompt Management overview: https://langfuse.com/docs/prompt-management/overview
- Prompt data model, versions e labels: https://langfuse.com/docs/prompt-management/data-model
- Prompt version control e rollback: https://langfuse.com/docs/prompt-management/features/prompt-version-control
- Vinculo de prompt com traces: https://langfuse.com/docs/prompt-management/features/link-to-traces
- Datasets para experimentos: https://langfuse.com/docs/evaluation/experiments/datasets

Use o `llms.txt` para descobrir paginas disponiveis antes de explorar a documentacao.
Quando o cliente de IA suportar MCP, prefira conectar no servidor `langfuse-docs`.

## Configuracoes Versionadas

Este repo versiona configuracoes locais para facilitar onboarding:

- [`.mcp.json`](../.mcp.json): formato generico usado por clientes MCP que leem config por projeto.
- [`.cursor/mcp.json`](../.cursor/mcp.json): config de MCP do Cursor.
- [`.vscode/mcp.json`](../.vscode/mcp.json): config de MCP do VS Code.
- [`AGENTS.md`](../AGENTS.md): contexto inicial para assistentes de codigo.

Todos apontam para o MCP publico de documentacao:

```text
https://langfuse.com/api/mcp
```

## Comandos Uteis

Claude Code, escopo de usuario conforme documentacao oficial:

```bash
claude mcp add \
  --transport http \
  langfuse-docs \
  https://langfuse.com/api/mcp \
  --scope user
```

Busca REST leve, quando MCP nao estiver disponivel:

```bash
curl "https://langfuse.com/api/search-docs?query=prompt+management+labels"
```

Para Cursor e VS Code, este repo ja versiona os arquivos de configuracao de workspace.

## Regra de Uso

Antes de alterar codigo de Langfuse, observabilidade ou prompt management neste template:

1. Consulte `https://langfuse.com/llms.txt`.
2. Use o MCP `langfuse-docs` quando disponivel.
3. Abra [`docs/observabilidade-langfuse.md`](observabilidade-langfuse.md) para respeitar o contrato local.
4. Abra [`modelagem-conversacional.md`](modelagem-conversacional.md) quando a mudanca
   afetar contexto, tom, qualificacao, objecoes ou progressao comercial.
5. Mantenha qualquer ferramenta de documentacao/MCP fora das dependencias de producao.

Se uma mudanca exigir pacote Python novo apenas para explorar documentacao ou integrar uma
IDE/assistente, coloque em `[project.optional-dependencies].dev`, nunca em
`[project].dependencies`.

## MCP Autenticado do Langfuse

O endpoint documentado acima e publico e serve apenas para documentacao.

O Langfuse tambem tem MCP autenticado para dados da plataforma e prompt library. Esse tipo
de conector pode ler ou alterar recursos reais do projeto Langfuse, portanto nao deve ser
versionado neste template base sem decisao explicita. Se um projeto derivado precisar dele,
configure em escopo local/usuario e nunca commite credenciais.
