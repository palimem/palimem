# Positioning

Palimem is **governed agent memory**: a local runtime that records what an agent believed, supersedes outdated facts, scopes recall, and preserves an audit trail. This document explains where Palimem sits in the mid-2026 agent-memory landscape and what we deliberately do not build.

User-facing docs: [palimem.com/docs](https://palimem.com/docs/)

---

## Category: governed agent memory

The market clusters into overlapping products. Palimem is not interchangeable with any of these:

| Neighbor | What it optimizes for | How Palimem differs |
|----------|---------------------|---------------------|
| **Memory APIs** (hosted retrieval layers) | Recall@k over embeddings in a cloud index | Local SQLite WAL on your disk; correctness via supersession and scopes, not leaderboard scores |
| **MCP tool dumps** | Exposing many memory-shaped tools without a contract | Eleven normative tools backed by [spec v1.7.0](../spec/README.md) and **143** black-box validation behaviors |
| **Editor-only plugins** | Wiring one host quickly | Same governed store across MCP hosts; adapters for Hermes and OpenClaw |
| **Interchange formats** (OKF, AGENTS.md wikis) | Curated, git-friendly knowledge bundles | Runtime memory with live supersession, temporal query, and audit export |

**Differentiation is governance**, not raw recall: correct current state, isolated scopes, provable behavior, and export when you need to show your work. Research benchmarks in `benchmarks/` are informative (persona recall, agent tasks, latency); they do not gate correctness and are not LongMemEval claims.

---

## Three layers (and where Palimem sits)

```
  ┌─────────────────────────────────────────────────────────────┐
  │  ORG SERVING — fleet backends, knowledge catalogs, Zep     │
  │  (multi-tenant serving, enterprise rollout)                 │
  └──────────────────────────▲──────────────────────────────────┘
                             │ optional fleet stub (local-authoritative)
  ┌──────────────────────────┴──────────────────────────────────┐
  │  RUNTIME MEMORY — MCP servers, governed WAL, live recall    │
  │  ◀ Palimem lives here                                       │
  └──────────────────────────▲──────────────────────────────────┘
                             │ import / export bridges
  ┌──────────────────────────┴──────────────────────────────────┐
  │  INTERCHANGE — OKF bundles, AGENTS.md/MEMORY.md, UMP/Relay  │
  │  (curated project knowledge, vendor-neutral files)            │
  └─────────────────────────────────────────────────────────────┘
```

- **Interchange** answers: *What does the project know, in files humans can review?*
- **Runtime memory** answers: *What did the agent believe at write time, and what is current now after supersession?*
- **Org serving** answers: *How do we operate memory across teams, replicas, or a catalog?*

Palimem is a **runtime** layer. It composes with interchange formats; it does not replace curated wikis or OKF bundles.

---

## Open Knowledge Format (OKF)

[Open Knowledge Format](https://github.com/google/open-knowledge-format) (OKF) v0.1 (June 2026) is a **format**: markdown + YAML frontmatter knowledge bundles, git-friendly and vendor-neutral.

**What OKF is good for**

- Portable, reviewable knowledge artifacts in repos
- Shared vocabulary for types, titles, and bundle layout
- Human-edited project memory that travels between tools

**What OKF does not define**

- Supersession or merge semantics when facts change
- Search ranking or live agent recall behavior
- Scoped isolation (user vs session vs repository)
- Append-only audit or point-in-time truth

**How Palimem composes with OKF**

| Concern | OKF | Palimem |
|---------|-----|---------|
| Primary artifact | Curated bundle in git | Governed WAL + derived current state |
| Updates | Edit files, merge in git | `memory_remember` supersedes prior values |
| Audience | Humans + interchange | Agents at runtime + operators |
| Proof | File history | Spec validation + `memory_audit_export` |

OKF is **what the project knows** (curated). Palimem is **what the agent believed at runtime**, with supersession and audit. See [okf-bridge.md](./okf-bridge.md) for proposed import/export paths.

---

## Honest lead / lag

### Where we lead

- **Spec + proof** — Normative [spec v1.7.0](../spec/README.md); **143/143** validation behaviors in CI; integration smokes for tier B+ editor integrations
- **Supersession** — Writes close prior versions; recall returns current truth, not a pile of contradictions
- **Scopes** — User, session, and repository isolation with optional subject filters
- **Audit** — `memory_query_temporal`, `as_of` recall, `memory_audit_export`, retention and PII controls (operator-gated)
- **Local-first** — SQLite on disk; no cloud account required for the default path

### Where we lag (v1.7.0)

- **Install** — [`@palimem/mcp`](https://www.npmjs.com/package/@palimem/mcp) on npm; `npx @palimem/mcp` for MCP hosts without a clone
- **PyPI** — Python package publish still pending (clone or npx path today)
- **Fleet coordination** — Local-authoritative fleet stub only; no multi-tenant org serving
- **Protocol export** — OKF, UMP, and Relay bridges are design-stage ([okf-bridge.md](./okf-bridge.md), [interop.md](./interop.md))
- **Cold-start UX** — Peers like PMB optimize hooks-first onboarding; we optimize correctness and spec depth first

### Close peers (category validation)

- **Seamless** — Fleet + task queue; validates org-layer demand; we stay runtime-first
- **PMB** — Hooks-first + easy install; validates onboarding pressure; we added npx without sacrificing governance
- **agent-memory-engine** — Evidence tree + cold start; validates structured recall; we lead on supersession + spec proof

MCP stdio and local SQLite are table stakes. Our moat is **governed correctness under test**, not embedding-first hybrid retrieval (research rejected that path for this product).

---

## What we are not building in v1

- **Cloud SaaS memory** — No hosted multi-tenant store; optional fleet backends remain operator-controlled
- **LongMemEval chase** — No marketing on recall@k leaderboards; benchmarks are internal research signals only
- **Replacing OKF or AGENTS.md** — Interchange stays in git; Palimem ingests and exports, does not subsume wikis
- **Embedding-first hybrid retrieval** — Lexical index + governed semantic units; embeddings optional, not the correctness path

---

## Further reading

- [OKF bridge design](./okf-bridge.md) — import/export sketch
- [Interop outlook](./interop.md) — UMP / Relay as future export targets
- [Spec](../spec/README.md) — normative contract
- [Benchmarks](../benchmarks/README.md) — informative suites (not correctness gates)
