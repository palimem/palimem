# OKF bridge (design)

This document proposes how Palimem **composes with** [Open Knowledge Format](https://github.com/google/open-knowledge-format) (OKF) v0.1. OKF is an interchange format; Palimem is a runtime. The bridge translates between them — it does **not** reimplement OKF inside the WAL.

Related: [positioning.md](./positioning.md) · [interop.md](./interop.md)

---

## Goals

| Direction | Purpose |
|-----------|---------|
| **Import** | Seed or refresh **repository** scope from an OKF bundle (curated project knowledge → governed records) |
| **Export** | Emit reviewable OKF-shaped artifacts from audit, review queue, or current repository-scope truth |

## Non-goals

- Storing OKF bundle structure as the WAL authority model
- Defining OKF merge or supersession semantics inside Palimem
- Replacing `import_markdown.py` for plain `USER.md` / `MEMORY.md` (that path remains first-class)
- Validating full OKF schema compliance in v1 (bridge maps a practical subset)

---

## OKF recap (relevant fields)

OKF bundles are directories of markdown files with YAML frontmatter. Typical frontmatter keys include `type`, `title`, `tags`, and bundle metadata. Body markdown carries human-readable knowledge.

Palimem maps OKF entries to governed records with:

- `scope`: `repository` (default for bundle import)
- `namespace`: bundle name or caller-supplied namespace
- `topic` / `field`: derived from OKF `type`, `title`, and heading structure
- `memory_type`: mapped from OKF `type` (see table below)
- `provenance`: `{"source": "okf-import", "bundle": "<path>", "file": "<relative path>"}`

---

## Import path (proposed)

### Operator flow

```bash
# Future CLI (not yet implemented)
python3 app/okf_import.py \
  --data-dir .ai-memory/data \
  --bundle /path/to/okf-bundle \
  --namespace my-project \
  [--dry-run]
```

### Algorithm (sketch)

1. **Discover** — Walk bundle root; index `*.md` with YAML frontmatter (reuse heading/bullet parsers from `memory_service.portability.import_markdown_file` where possible).
2. **Normalize** — For each file:
   - Parse frontmatter (`type`, `title`, optional `id`, `tags`)
   - Parse body into one or more memory drafts (headings → topic/field; bullets → values)
3. **Map scope** — Default `repository`; allow `--scope` override for operator imports only.
4. **Write** — Call `memory_remember` (or internal service equivalent) per draft:
   - Same subject key → supersede (Palimem semantics, not OKF file merge)
   - Record provenance for audit export
5. **Report** — JSON summary: `{imported, superseded, skipped, errors}`

### Type mapping (initial)

| OKF `type` (examples) | Palimem `memory_type` |
|-----------------------|------------------------|
| `fact`, `reference` | `fact` |
| `procedure`, `howto` | `procedure` |
| `constraint`, `policy` | `constraint` |
| `preference` | `preference` |
| `log`, `decision`, `episode` | `episode` |
| unknown | `fact` (with warning in import report) |

### Reuse from existing code

- `app/import_markdown.py` — CLI pattern, `--data-dir`, `--dry-run`, scope/namespace overrides
- `memory_service.portability` — `MarkdownMemoryDraft`, `import_markdown_file`, slugify/field helpers
- No WAL schema changes required

### Import acceptance criteria (when implemented)

- Idempotent re-import of unchanged bundle does not duplicate current state
- Changed OKF file content supersedes prior governed value for the same subject key
- Import report lists every file with action taken
- Black-box validation: new scenarios under Phase 7 portability (optional follow-up)

---

## Export path (proposed)

### Operator flow

```bash
# Future CLI (not yet implemented)
python3 app/okf_export.py \
  --data-dir .ai-memory/data \
  --output /path/to/export-bundle \
  --scope repository \
  --namespace my-project \
  [--from-review]   # export memory_review accepted proposals only
  [--from-audit SEQ] # slice audit window
```

### Algorithm (sketch)

1. **Select records** — `iter_current_records` filtered by scope/namespace; optional review or audit filters.
2. **Group** — Cluster by `topic` → one OKF file per topic (or per record for `episode` types).
3. **Emit frontmatter** — Per file:

```yaml
---
type: fact          # reverse type mapping
title: "Deployment policy"
source: palimem
exported_at: "2026-07-23T15:00:00Z"
palimem:
  scope: repository
  namespace: my-project
  topic: deployment
  field: policy
  seq: 42
---
```

4. **Emit body** — Markdown body from current `value`; optional `log.md` style append for episodes.
5. **Write bundle** — Directory layout:

```
export-bundle/
  manifest.yaml          # optional: bundle metadata
  repository/
    deployment-policy.md
    testing-style.md
```

### Export sources

| Source | Use case |
|--------|----------|
| Current state | Human-readable snapshot of governed repository memory |
| Review queue (accepted) | Publish curated promotions after `memory_review` |
| Audit slice | Compliance export paired with `memory_audit_export` JSONL |

### Reuse from existing code

- `app/export_memory.py` — `iter_current_records`, `export_markdown_profile` as formatting reference
- `memory_audit_export` MCP tool — authoritative audit JSONL; OKF export is a **derived**, human-facing view

---

## API sketch (library)

If implemented as a module `app/okf_bridge.py`:

```python
def import_okf_bundle(
    service: MemoryService,
    bundle_path: Path,
    *,
    namespace: str,
    scope: str = "repository",
    dry_run: bool = False,
) -> dict[str, Any]: ...

def export_okf_bundle(
    service: MemoryService,
    output_path: Path,
    *,
    scope: str = "repository",
    namespace: str | None = None,
    from_review: bool = False,
) -> dict[str, Any]: ...
```

Both functions return machine-readable summaries suitable for CLI stderr (matching `export_memory.py` / `import_markdown.py` conventions).

---

## Implementation status

| Piece | Status |
|-------|--------|
| Design (this doc) | **Current** |
| `okf_import.py` / `okf_export.py` CLIs | Not started |
| Validation scenarios | Not started |
| Website docs | Publish via palimem.com when CLIs land |

**Recommendation:** Land import first (repository-scope seeding from bundles); export second (review → OKF for human publication). Each as a focused PR with portability tests only — no WAL changes.

---

## Security and operations

- Import runs with operator trust: bundle content becomes governed memory in the target namespace.
- Export may include PII if present in governed records — respect `pii_scan` and retention policies before publishing bundles.
- Bundles are **derived artifacts**; WAL remains authoritative for runtime truth.
