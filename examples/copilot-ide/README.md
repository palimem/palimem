# Copilot IDE integration

Register **memory-service** for GitHub Copilot in the IDE via VS Code–style `.vscode/mcp.json` (stdio MCP).

This harness shares the same connect CLI and MCP surface as [VS Code + Copilot Agent](../vscode-copilot-agent/README.md).

## Prerequisites

- IDE with Copilot MCP support (VS Code, Visual Studio, or compatible host)
- Node.js ≥ 18
- Repository cloned; dependencies installed (`cd app && npm install`)

## Quick connect

From repository root:

```bash
node app/scripts/ai-memory.js connect vscode \
  --project-root "$(pwd)" \
  --data-dir .ai-memory/data
```

Writes `.vscode/mcp.json` with a `servers.memory-service` stdio entry.

## Verify

```bash
bash examples/copilot-ide/demo/copilot-ide-smoke.sh
```

This delegates to the VS Code Copilot Agent smoke (same eleven-tool MCP surface).

## Related

- [VS Code + Copilot Agent](../vscode-copilot-agent/README.md)
- [Copilot CLI integration](../copilot/README.md)
- [Harness patterns](../../docs/02-harness-integration.md)
