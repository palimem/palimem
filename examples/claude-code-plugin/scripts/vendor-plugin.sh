#!/usr/bin/env bash
# Vendor hook scripts and the Python MCP app into claude-code-plugin/vendor/ for marketplace distribution.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPONENT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
VENDOR="$PLUGIN_ROOT/vendor"
HOOKS_SRC="$COMPONENT_ROOT/examples/claude-code"
APP_SRC="$COMPONENT_ROOT/app"

rm -rf "$VENDOR"
mkdir -p "$VENDOR/hooks/claude-code" "$VENDOR/app/scripts" "$VENDOR/app/memory_service"

echo "== copy hook JavaScript =="
cp "$HOOKS_SRC"/*.js "$VENDOR/hooks/claude-code/"

echo "== copy Python MCP app =="
cp "$APP_SRC"/run_production_stdio_server.py "$VENDOR/app/"
cp "$APP_SRC"/hook_remember.py "$APP_SRC"/hook_search.py "$VENDOR/app/"
cp -R "$APP_SRC/memory_service" "$VENDOR/app/"
cp "$APP_SRC/scripts/memory-service-mcp.js" "$VENDOR/app/scripts/"

echo "== verify hook-lib resolves vendor app =="
node -e "
const path = require('path');
const hookLib = path.join('$VENDOR/hooks/claude-code/hook-lib.js');
const appFromHook = path.resolve(path.dirname(hookLib), '..', '..', 'app');
if (!require('fs').existsSync(path.join(appFromHook, 'hook_remember.py'))) {
  process.stderr.write('vendor layout broken: hook-lib cannot find app\\n');
  process.exit(1);
}
"

echo "Vendored into $VENDOR"
echo "Next: commit vendor/ and publish via .claude-plugin/marketplace.json at repo root."
