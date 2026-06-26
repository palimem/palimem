# Portability examples

Sample markdown files for the import adapter:

| File | Default scope | Default namespace | Default memory type |
|------|---------------|-------------------|---------------------|
| [`USER.md.sample`](./USER.md.sample) | `user` | `default` | `preference` |
| [`MEMORY.md.sample`](./MEMORY.md.sample) | `repository` | parent directory name | `fact` |

## Import

```bash
python3 app/import_markdown.py \
  --data-dir /path/to/data \
  examples/markdown/USER.md.sample \
  examples/markdown/MEMORY.md.sample
```

## Export

```bash
python3 app/export_memory.py \
  --data-dir /path/to/data \
  --jsonl /tmp/memory-export.jsonl \
  --markdown /tmp/memory-profile.md
```

Round-trip fidelity is preserved for current governed records when exporting to JSONL and re-importing with the same data directory semantics (supersession applies on duplicate subject keys).

## Markdown conventions

- `# Heading` sets the topic slug for following entries
- `## Subheading` can switch inferred memory type (`Preferences` → preference, `Procedures` → procedure)
- Bullet lines (`- item`) become individual memories
- `key: value` lines map to field slug + value

These conventions are intentionally simple for Phase 1 migration from workspace markdown truth files.
