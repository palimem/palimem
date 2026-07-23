# Interop outlook

Palimem stores governed memory in a local WAL today. Interchange and org-serving protocols evolve faster than any single runtime. This page records **future export targets** — store correctly now, wire formats tomorrow.

Related: [positioning.md](./positioning.md) · [okf-bridge.md](./okf-bridge.md)

---

## Principle: runtime first, bridges second

The correctness path is spec v1.7.0 + 143 validation behaviors + `memory_audit_export`. Protocol adapters are **projections** of that truth, not alternate authorities.

```
  Palimem WAL (authoritative)
        │
        ├── JSONL audit export     ← shipped (memory_audit_export)
        ├── Markdown profile       ← shipped (export_memory.py)
        ├── OKF bundle             ← design (okf-bridge.md)
        ├── UMP envelope           ← future
        └── Relay packet           ← future
```

---

## UMP (Universal Memory Protocol)

**Status:** watch; no implementation in v1.7.0.

UMP-style envelopes describe memory units for cross-agent exchange. Palimem already has the primitives UMP would need to project:

| Palimem concept | UMP-shaped field (illustrative) |
|-----------------|----------------------------------|
| `scope` + `namespace` | context / tenant routing |
| `topic` + `field` | subject identity |
| `value` + `memory_type` | unit payload |
| `seq`, `valid_from_seq`, `valid_to_seq` | versioning |
| `provenance` | source attribution |

**Planned approach:** export adapter that maps `memory_audit_export` or current-state JSONL to UMP envelopes. Import adapter would call `memory_remember` with provenance — supersession stays Palimem-native.

**Non-goals:** adopting UMP as the WAL schema; dual-write to external UMP brokers.

---

## Relay

**Status:** watch; no implementation in v1.7.0.

Relay protocols focus on packetized memory transfer between agents or hosts. Palimem’s fleet stub (`fleet_push` / `fleet_pull` in validation mode) explores segment replication but remains **local-authoritative**.

**Planned approach:** treat Relay as an org-serving transport layer above runtime memory. Palimem exports committed WAL segments or audit slices; a Relay gateway handles routing. Runtime semantics unchanged.

**Non-goals:** making Relay the write path for governed remembers.

---

## AGENTS.md / MEMORY.md

Not a separate protocol — plain markdown interchange already supported:

```bash
python3 app/import_markdown.py \
  --data-dir .ai-memory/data \
  examples/markdown/USER.md.sample \
  examples/markdown/MEMORY.md.sample
```

OKF generalizes this pattern with frontmatter and bundle layout. Palimem does not require projects to choose one; bridges coexist.

---

## Priority order (proposed)

1. **OKF import/export** — highest overlap with repository-scope workflows ([okf-bridge.md](./okf-bridge.md))
2. **JSONL audit** — already shipped; extend documentation for compliance operators
3. **UMP projection** — when a stable public schema and reference implementation exist
4. **Relay transport** — when fleet / multi-agent routing becomes a shipped product requirement

---

## References

- [palimem.com/docs](https://palimem.com/docs/) — user documentation
- [Spec §19 portability](../spec/README.md) — export/import contract
- [export_memory.py](../app/export_memory.py) · [import_markdown.py](../app/import_markdown.py) — shipped CLIs
