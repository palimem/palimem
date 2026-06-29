#!/usr/bin/env bash
# Shared helpers for readiness gates (flat product repo and monorepo layouts).

resolve_repo_paths() {
  local script_dir="$1"
  ROOT="$(cd "$script_dir/../../.." && pwd)"
  if [ -d "$ROOT/../../components" ]; then
    REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
  else
    REPO_ROOT="$ROOT"
  fi
}

assert_validation_artifacts() {
  local expected_spec="${1:-1.7.0}"
  "$PYTHON" - <<PY
import json
import pathlib
import sys

root = pathlib.Path("${ROOT}")
comp = json.loads((root / "tests/artifacts/latest-results.json").read_text())
passed = sum(1 for r in comp["results"] if r["status"] == "pass")
total = len(comp["results"])
if passed != total or comp["spec_version"] != "${expected_spec}":
    print(
        f"validation artifacts: {passed}/{total} pass, spec {comp['spec_version']} (expected ${expected_spec})",
        file=sys.stderr,
    )
    sys.exit(1)
print(f"validation artifacts: {passed}/{total} pass, spec {comp['spec_version']}")
PY
}
