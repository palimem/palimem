#!/usr/bin/env bash
# Phase 5 smoke wrapper.
set -euo pipefail

DEMO="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"
exec "$PYTHON" "$DEMO/phase5-smoke.py"
