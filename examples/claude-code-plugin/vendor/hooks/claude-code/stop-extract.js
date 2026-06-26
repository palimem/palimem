#!/usr/bin/env node
"use strict";

/**
 * Stop hook: backfill tool failures from the session transcript and write a turn summary.
 *
 * PostToolUseFailure handles live capture; Stop scans transcript tail for missed Bash errors.
 */

const fs = require("node:fs");
const {
  readHookInput,
  namespaceForHook,
  rememberPayload,
  buildToolFailurePayload,
  slug,
} = require("./hook-lib");

function parseTranscriptFailures(transcriptPath) {
  if (!transcriptPath || !fs.existsSync(transcriptPath)) {
    return [];
  }
  const failures = [];
  const lines = fs.readFileSync(transcriptPath, "utf8").split(/\r?\n/).filter(Boolean);
  for (const line of lines.slice(-400)) {
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      continue;
    }
    const message = record.message || record;
    const content = message.content;
    if (!Array.isArray(content)) {
      continue;
    }
    for (const block of content) {
      if (block.type !== "tool_result" || !block.is_error) {
        continue;
      }
      const toolUseId = block.tool_use_id || slug(String(block.content).slice(0, 80), 40);
      failures.push({
        hook_event_name: "Stop",
        tool_name: "Bash",
        tool_input: { command: "transcript-backfill" },
        tool_use_id: `transcript-${toolUseId}`,
        error: String(block.content || "tool error").slice(0, 2000),
        cwd: record.cwd,
        session_id: record.session_id,
      });
    }
  }
  return failures;
}

function main() {
  const input = readHookInput();
  if (input.stop_hook_active) {
    process.exit(0);
  }

  const namespace = namespaceForHook(input.cwd || undefined);
  let ingested = 0;

  for (const failure of parseTranscriptFailures(input.transcript_path)) {
    const payload = buildToolFailurePayload({ ...failure, cwd: input.cwd || failure.cwd });
    if (rememberPayload(payload, { skipIfExists: true, quiet: true })) {
      ingested += 1;
    }
  }

  const summary = (input.last_assistant_message || "").trim();
  if (summary.length >= 40) {
    const field = slug(`turn-${input.session_id || "stop"}`, 40);
    rememberPayload(
      {
        scope: "session",
        namespace,
        memory_type: "fact",
        topic: "session_turn",
        field,
        value: summary.slice(0, 4000),
        provenance: {
          source: "claude-code-hook",
          tool: "Stop",
          actor: "hook",
          request_id: field,
        },
      },
      { skipIfExists: true, quiet: true }
    );
  }

  if (process.env.MEMORY_SERVICE_HOOK_DEBUG === "1") {
    process.stderr.write(`stop-extract ingested ${ingested} transcript failure(s)\n`);
  }
  process.exit(0);
}

main();
