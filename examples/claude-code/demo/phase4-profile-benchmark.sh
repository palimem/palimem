#!/usr/bin/env bash
# Phase 4 exit-criteria benchmark: profile recall >= USER.md-only Hermes baseline.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
DEMO="$(dirname "$0")"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"

echo "== Phase 4 profile recall benchmark =="
"$PYTHON" "$DEMO/phase4-profile-benchmark.py"
