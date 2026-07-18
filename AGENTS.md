# AGENTS.md — cli-market-backend

Mirror API / paridad con producción. Pin de `cli-market-core`. Sin deploy directo.

Contexto de ecosistema: `../cli-market-world/AGENTS.md`  
L0 memoria: `%USERPROFILE%\.graphify\cli-market\CLI-MARKET-MEMORY.md`

## graphify (memoria + tokens)

Antes de explorar a ciegas o hacer grep multi-archivo:

1. L0: `%USERPROFILE%\.graphify\cli-market\CLI-MARKET-MEMORY.md`
2. Grafo producto: `%USERPROFILE%\.graphify\cli-market\product-graph.json` (o local `graphify-out/graph.json`)
3. `graphify query|path|explain --graph <G> --budget 800-1500` → abrir solo 1–3 `source_file`
4. No volcar vault Obsidian al prompt (~20k notas)

Refresh producto:

```powershell
pwsh $env:USERPROFILE\.graphify\cli-market\refresh-product-graph.ps1
```

Reglas completas: `%USERPROFILE%\.graphify\cli-market\GRAPHIFY-AGENT-RULES.md`

Tras cambios de código en este repo: `graphify update .`
