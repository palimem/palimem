# Dogfooding Palimem on this repo (optional maintainer tooling)

**Optional.** This folder is not part of the normative spec or release gates. It shows how maintainers wire Palimem into the `palimem/palimem` repo and run automated recall probes.

Use Palimem locally while developing `palimem/palimem`. One-time setup:

```bash
bash dogfood/setup.sh
```

Then **reload Cursor** (or restart) so `.cursor/mcp.json` is picked up.

## What setup does

1. `npm install` in `app/`
2. Seeds `.ai-memory/data` from `dogfood/USER.md` and `dogfood/MEMORY.md`
3. Verifies MCP stdio (`memory_status` + eleven tools)
4. Writes **local** `.cursor/mcp.json` (gitignored — reload Cursor after setup)

`dogfood/USER.md` and `dogfood/MEMORY.md` are **example** project memory for this repo (like `examples/markdown/*.sample`). Fork them for your own project.

## Quick verify in chat

After reload, ask the agent:

```
Call memory_status with scope repository and namespace palimem.
Then memory_search for "release gate" in the same scope and namespace.
```

Expected: `phase6-readiness.sh`, spec `1.7.0`, flat `app/` layout — not monorepo `components/` paths.

**Namespace:** imports use `palimem` (matches the workspace folder name Cursor agents typically choose).

## Automated dogfood (no manual Cursor chat)

Simulates a disciplined agent that always calls `memory_status` then `memory_search` over MCP stdio:

```bash
bash dogfood/run_automated.sh
```

Modes exercised:

| Mode | What it tests |
|------|----------------|
| **none** | Empty baseline (should fail all probes) |
| **static** | `dogfood/USER.md` + `MEMORY.md` text only (no MCP) |
| **mcp** | Full MCP stdio against `.ai-memory/data` |

Optional Docker MCP transport:

```bash
DOGFOOD_DOCKER=1 bash dogfood/run_automated.sh
```

Results: `dogfood/artifacts/latest-probe-results.json`

**Limitation:** This does not drive the Cursor UI or measure whether a real agent chooses to call memory tools. It automates the recall layer and A/B baselines only.

## Comparison protocol (manual A/B)

Run each prompt in a **fresh session** under different modes. Log pass/fail and turn count.

| Mode | Setup |
|------|--------|
| **No memory** | Disable MCP server in Cursor settings |
| **Static USER.md** | `cp dogfood/USER.md USER.md` at repo root; MCP off |
| **MCP only** | `bash dogfood/setup.sh`; reload Cursor |
| **Full stack** | MCP + Claude Code hooks from `examples/claude-code/` |

### Suggested prompts

1. *What is the current release gate and how do I run it?*
2. *Where do the MCP server and connect CLI live in this repo?*
3. *How do I add Gemini CLI integration?*
4. *Integration readiness fails on MCP smokes — what needs Node?*
5. *How many validation behaviors does spec 1.7.0 require?*

### Success criteria

- Names `phase6-readiness.sh` (not phase4)
- Uses `app/` paths (not `components/memory-service/`)
- Knows `connect gemini` / `examples/gemini-cli/`
- Mentions `npm install` in `app/` for MCP smokes
- Says **143** behaviors (or runs validation and reports count)

## Claude Code full stack (optional)

```bash
cp examples/claude-code/.mcp.json .mcp.json   # merge if you already have MCP servers
# Merge examples/claude-code/hooks.json into Claude Code hook settings
bash examples/claude-code/demo/hooks-phase2-smoke.sh
```

## Reset local memory

```bash
rm -rf .ai-memory/data
bash dogfood/setup.sh
```
