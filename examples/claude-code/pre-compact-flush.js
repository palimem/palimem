#!/usr/bin/env node
"use strict";

/**
 * PreCompact hook: flush durable facts to repository scope before Claude compacts context.
 *
 * Does not block compaction by default. Set MEMORY_SERVICE_BLOCK_AUTO_COMPACT=1 to veto
 * trigger=auto compactions until MEMORY_SERVICE_COMPACTION_SAFE_FILE exists.
 */

const fs = require("node:fs");
const { readHookInput, namespaceForHook, rememberPayload, slug } = require("./hook-lib");

function collectUserSnippets(transcriptPath) {
  if (!transcriptPath || !fs.existsSync(transcriptPath)) {
    return [];
  }
  const snippets = [];
  const lines = fs.readFileSync(transcriptPath, "utf8").split(/\r?\n/).filter(Boolean);
  for (const line of lines.slice(-200)) {
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      continue;
    }
    const message = record.message || record;
    const role = message.role || record.type;
    if (role !== "user") {
      continue;
    }
    const text = Array.isArray(message.content)
      ? message.content
          .filter((block) => block.type === "text" && block.text)
          .map((block) => block.text)
          .join("\n")
      : typeof message.content === "string"
        ? message.content
        : "";
    const trimmed = text.trim();
    if (trimmed.length >= 24) {
      snippets.push(trimmed.slice(0, 1200));
    }
  }
  return snippets.slice(-6);
}

function main() {
  const input = readHookInput();
  const namespace = namespaceForHook(input.cwd || undefined);
  const repoNamespace = `${namespace}-repo`;

  if (
    input.trigger === "auto" &&
    process.env.MEMORY_SERVICE_BLOCK_AUTO_COMPACT === "1" &&
    process.env.MEMORY_SERVICE_COMPACTION_SAFE_FILE &&
    !fs.existsSync(process.env.MEMORY_SERVICE_COMPACTION_SAFE_FILE)
  ) {
    process.stdout.write(
      JSON.stringify({
        decision: "block",
        reason:
          "Auto-compaction deferred until checkpoint file exists. PreCompact flush wrote durable facts; create MEMORY_SERVICE_COMPACTION_SAFE_FILE to allow compaction.",
      }) + "\n"
    );
    process.exit(0);
  }

  const snippets = collectUserSnippets(input.transcript_path);
  let flushed = 0;

  for (const snippet of snippets) {
    const field = slug(snippet.slice(0, 80), 48);
    const ok = rememberPayload(
      {
        scope: "repository",
        namespace: repoNamespace,
        memory_type: "fact",
        topic: "compaction_checkpoint",
        field,
        value: snippet,
        provenance: {
          source: "claude-code-hook",
          tool: "PreCompact",
          actor: "hook",
          request_id: `flush-${field}`,
        },
      },
      { skipIfExists: true, quiet: true }
    );
    if (ok) {
      flushed += 1;
    }
  }

  const instructions = (input.custom_instructions || "").trim();
  if (instructions.length > 0) {
    rememberPayload(
      {
        scope: "repository",
        namespace: repoNamespace,
        memory_type: "procedure",
        topic: "compaction_checkpoint",
        field: "custom_instructions",
        value: instructions.slice(0, 4000),
        provenance: {
          source: "claude-code-hook",
          tool: "PreCompact",
          actor: "hook",
          request_id: "flush-custom-instructions",
        },
      },
      { skipIfExists: true, quiet: true }
    );
    flushed += 1;
  }

  rememberPayload(
    {
      scope: "repository",
      namespace: repoNamespace,
      memory_type: "fact",
      topic: "compaction_checkpoint",
      field: "last_flush",
      value: JSON.stringify({
        trigger: input.trigger || "unknown",
        flushed_snippets: snippets.length,
        session_id: input.session_id || null,
        at: new Date().toISOString(),
      }),
      provenance: {
        source: "claude-code-hook",
        tool: "PreCompact",
        actor: "hook",
        request_id: `flush-meta-${input.session_id || "session"}`,
      },
    },
    { quiet: true }
  );

  if (process.env.MEMORY_SERVICE_HOOK_DEBUG === "1") {
    process.stderr.write(`pre-compact-flush wrote ${flushed} checkpoint record(s)\n`);
  }
  process.exit(0);
}

main();
