# Contexto de Desenvolvimento LangGraph

Este repositorio deve carregar contexto de documentacao atual para quem for desenvolver
novos agentes a partir desta base.

Essas configuracoes sao **somente de desenvolvimento**. Elas nao fazem parte do runtime da
API, nao entram no Dockerfile de producao e nao devem virar dependencia obrigatoria da
aplicacao.

## Fontes Oficiais

- Indice completo da documentacao: https://docs.langchain.com/llms.txt
- MCP server da documentacao LangChain: https://docs.langchain.com/mcp

Use o `llms.txt` para descobrir paginas disponiveis antes de explorar a documentacao.
Quando o cliente de IA suportar MCP, prefira conectar no servidor `docs-langchain`.

## Configuracoes Versionadas

Este repo versiona configuracoes locais para facilitar onboarding:

- [`.mcp.json`](../.mcp.json): formato generico usado por clientes MCP que leem config por projeto.
- [`.cursor/mcp.json`](../.cursor/mcp.json): config de MCP do Cursor.
- [`.vscode/mcp.json`](../.vscode/mcp.json): config de MCP do VS Code.
- [`AGENTS.md`](../AGENTS.md): contexto inicial para assistentes de codigo.

## Comandos Uteis

Claude Code, escopo do projeto:

```bash
claude mcp add --transport http docs-langchain https://docs.langchain.com/mcp
```

Codex CLI, escopo global:

```bash
codex mcp add langchain-docs --url https://docs.langchain.com/mcp
```

O comando do Codex CLI e global porque essa e a forma documentada atualmente pela
documentacao da LangChain. Para manter contexto dentro do repo, este projeto tambem guarda
`.mcp.json` e `AGENTS.md`.

## Regra de Uso

Antes de alterar codigo de LangGraph/LangChain neste template:

1. Consulte `https://docs.langchain.com/llms.txt`.
2. Use o MCP `docs-langchain` quando disponivel.
3. Abra as docs locais em `docs/` para respeitar a arquitetura deste repo.
4. Mantenha qualquer ferramenta de documentacao/MCP fora das dependencias de producao.

Se uma mudanca exigir pacote Python novo apenas para explorar documentacao ou integrar uma
IDE/assistente, coloque em `[project.optional-dependencies].dev`, nunca em
`[project].dependencies`.
