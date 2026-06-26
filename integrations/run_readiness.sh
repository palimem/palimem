#!/usr/bin/env bash
# run_readiness.sh — integration readiness gate for memory-service (Phase 6).
#
# Executes every catalog entry in integrations.yaml where tier is A or B
# and smoke_entrypoint is non-null.
#
# Exit 0: all smokes passed.
# Exit 1: one or more smokes failed (failing harness_id printed to stderr).
#
# Usage:
#   components/memory-service/integrations/run_readiness.sh [--catalog PATH]
#
# Options:
#   --catalog PATH   Path to integrations.yaml (default: auto-detected from script location)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPONENT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CATALOG="${CATALOG:-$COMPONENT_ROOT/spec/integrations.yaml}"

# Allow override via --catalog flag
while [[ $# -gt 0 ]]; do
  case "$1" in
    --catalog)
      CATALOG="$2"
      shift 2
      ;;
    *)
      echo "Usage: $0 [--catalog PATH]" >&2
      exit 2
      ;;
  esac
done

if [ ! -f "$CATALOG" ]; then
  echo "ERROR: integrations catalog not found: $CATALOG" >&2
  exit 1
fi

# Parse catalog with Python (avoid yq dependency)
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"

ENTRIES=$("$PYTHON" - "$CATALOG" <<'PY'
import sys
import re

catalog_path = sys.argv[1]
text = open(catalog_path, encoding="utf-8").read()

# Minimal YAML parser for the known catalog structure
# Extracts harness_id, tier, smoke_entrypoint per entry block
entries = []
current = {}
for line in text.splitlines():
    m = re.match(r"^\s+-\s+harness_id:\s+(.+)$", line)
    if m:
        if current:
            entries.append(current)
        current = {"harness_id": m.group(1).strip()}
        continue
    for key in ("tier", "smoke_entrypoint"):
        m = re.match(rf"^\s+{key}:\s+(.+)$", line)
        if m:
            val = m.group(1).strip()
            current[key] = None if val == "null" else val
if current:
    entries.append(current)

for e in entries:
    tier = e.get("tier", "")
    smoke = e.get("smoke_entrypoint")
    if tier in ("A", "B") and smoke:
        print(f"{e['harness_id']}|{smoke}")
PY
)

if [ -z "$ENTRIES" ]; then
  echo "No tier A/B entries with smoke_entrypoint found in catalog." >&2
  exit 0
fi

PASS=0
FAIL=0
FAILED_IDS=()

# Monorepo: integrations/ is under components/<name>/integrations (workspace root two levels up).
# Flat product repo: integrations/ is at repo root (component root one level up).
if [ -d "$COMPONENT_ROOT/../../components" ]; then
  REPO_ROOT="$(cd "$COMPONENT_ROOT/../.." && pwd)"
else
  REPO_ROOT="$COMPONENT_ROOT"
fi

resolve_smoke_path() {
  local smoke_path="$1"
  local rel="${smoke_path#components/memory-service/}"

  if [[ "$smoke_path" = /* ]]; then
    printf '%s' "$smoke_path"
    return
  fi
  if [ -f "$COMPONENT_ROOT/$rel" ]; then
    printf '%s' "$COMPONENT_ROOT/$rel"
    return
  fi
  if [ -f "$REPO_ROOT/$smoke_path" ]; then
    printf '%s' "$REPO_ROOT/$smoke_path"
    return
  fi
  printf '%s' "$REPO_ROOT/$smoke_path"
}

echo "== Integration readiness: $(echo "$ENTRIES" | wc -l | tr -d ' ') smoke(s) to run =="

while IFS='|' read -r HARNESS_ID SMOKE_PATH; do
  SMOKE_ABS="$(resolve_smoke_path "$SMOKE_PATH")"

  if [ ! -f "$SMOKE_ABS" ]; then
    echo "FAIL [$HARNESS_ID]: smoke script not found: $SMOKE_ABS" >&2
    FAILED_IDS+=("$HARNESS_ID")
    FAIL=$((FAIL + 1))
    continue
  fi

  echo "-- Running smoke for $HARNESS_ID: $SMOKE_ABS"
  if bash "$SMOKE_ABS"; then
    echo "PASS [$HARNESS_ID]"
    PASS=$((PASS + 1))
  else
    echo "FAIL [$HARNESS_ID]: $SMOKE_ABS exited non-zero" >&2
    FAILED_IDS+=("$HARNESS_ID")
    FAIL=$((FAIL + 1))
  fi
done <<< "$ENTRIES"

echo ""
echo "== Results: $PASS passed, $FAIL failed =="

if [ $FAIL -gt 0 ]; then
  for ID in "${FAILED_IDS[@]}"; do
    echo "FAILED: $ID" >&2
  done
  exit 1
fi

exit 0
