#!/usr/bin/env node
"use strict";

/**
 * PostToolUseFailure hook: capture Bash/tool failures into session-scoped episodes.
 *
 * Matcher recommendation in hooks.json: Bash
 */

const { readHookInput, buildToolFailurePayload, rememberPayload } = require("./hook-lib");

function main() {
  const input = readHookInput();
  if (input.is_interrupt) {
    process.exit(0);
  }
  const payload = buildToolFailurePayload(input);
  const ok = rememberPayload(payload, { skipIfExists: true, quiet: true });
  process.exit(ok ? 0 : 1);
}

main();
