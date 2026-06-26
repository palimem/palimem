# memory-service pipelines

Azure DevOps CI/CD for the `memory-service` component.

| File | Purpose |
|------|---------|
| [ci.yml](ci.yml) | Component-scoped validation + Docker build verification |
| [cd.yml](cd.yml) | Release image publish after successful CI on `main` |

## Onboarding placeholders

Replace these before first live run:

| Placeholder | Meaning |
|-------------|---------|
| `<build-agent-pool>` | Linux agent pool name |
| `<registry-service-connection>` | Azure DevOps service connection for the container registry |
| `registry-host.invalid` | Registry hostname |
| `registry-namespace` | Registry namespace / project |
| `project-prefix` | Image name prefix (full image: `<host>/<namespace>/<prefix>-memory-service`) |

## Image tags

Published tags follow AGENTS.md:

- Immutable: `<spec-version>.<revision>` (revision resets to `0` on spec version change)
- Floating: `<spec-version>` (deployment overlays reference this tag)

Current spec version: **1.6.0**.

## Triggers

- **CI** runs on pull requests and pushes to `main` when files under `components/memory-service/` change.
- **CD** runs only after successful CI completion on `main`; it does not trigger from file changes alone.

## Local equivalent

```bash
python3 components/memory-service/tests/run_validation.py
docker build -t memory-service:1.6.0 -f components/memory-service/Dockerfile components/memory-service
```

GitHub Actions also runs component validation on PRs and `main` via [`.github/workflows/memory-service-validation.yml`](../../../.github/workflows/memory-service-validation.yml).
