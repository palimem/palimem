#!/usr/bin/env bash
# Nightly or cron-friendly consolidation wrapper.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
APP="$ROOT/app"
DATA_DIR="${MEMORY_SERVICE_DATA_DIR:-$REPO_ROOT/.ai-memory/data}"
SCOPE="${MEMORY_SERVICE_CONSOLIDATE_SCOPE:-repository}"
NS="${MEMORY_SERVICE_NAMESPACE:-default}-repo"
REVIEW="${MEMORY_SERVICE_REVIEW_EXPORT_PATH:-$DATA_DIR/../review.md}"

mkdir -p "$(dirname "$DATA_DIR")" "$(dirname "$REVIEW")"

python3 "$APP/run_consolidation.py" \
  --data-dir "$DATA_DIR" \
  --scope "$SCOPE" \
  --namespace "$NS" \
  --export-review "$REVIEW" \
  --quiet

echo "consolidation complete; review exported to $REVIEW"
