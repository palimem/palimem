#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const workspace = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const dataDir =
  process.env.MEMORY_SERVICE_DATA_DIR || path.join(workspace, ".ai-memory", "data");
const appRoot = path.resolve(__dirname, "..", "..", "app");
const rememberScript = path.join(appRoot, "hook_remember.py");
const searchScript = path.join(appRoot, "hook_search.py");
const python = process.env.MEMORY_SERVICE_PYTHON || (process.platform === "win32" ? "py" : "python3");

function pythonArgs(scriptPath, extra) {
  const base =
    process.platform === "win32" && python === "py" ? ["-3", scriptPath] : [scriptPath];
  return [...base, ...extra];
}

function readHookInput() {
  const raw = fs.readFileSync(0, "utf8");
  if (!raw.trim()) {
    return {};
  }
  return JSON.parse(raw);
}

function namespaceForHook(cwd) {
  return (
    process.env.MEMORY_SERVICE_NAMESPACE ||
    path.basename(cwd || workspace).replace(/[^a-zA-Z0-9_-]+/g, "-").slice(0, 64) ||
    "default"
  );
}

function ensureDataDir() {
  fs.mkdirSync(dataDir, { recursive: true });
}

function rememberPayload(payload, { skipIfExists = false, quiet = true } = {}) {
  ensureDataDir();
  const args = ["--data-dir", dataDir, "--stdin-json"];
  if (skipIfExists) {
    args.push("--skip-if-exists");
  }
  if (quiet) {
    args.push("--quiet");
  }
  const result = spawnSync(python, pythonArgs(rememberScript, args), {
    input: JSON.stringify(payload),
    encoding: "utf-8",
    cwd: workspace,
    env: process.env,
  });
  if (result.status !== 0) {
    process.stderr.write(result.stderr || "hook_remember failed\n");
    return false;
  }
  return true;
}

function extractPaths(text) {
  if (!text) {
    return [];
  }
  const matches = text.match(/(?:\/[\w./-]+|[A-Za-z]:\\[\w\\.-]+)/g) || [];
  return [...new Set(matches)].slice(0, 8);
}

function exitCodeFromError(errorText) {
  const match = /exit(?:ed)? with (?:non-zero )?status(?: code)? (\d+)/i.exec(errorText || "");
  return match ? Number(match[1]) : 1;
}

function slug(value, max = 48) {
  return String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, max);
}

function buildToolFailurePayload(hookInput) {
  const toolName = hookInput.tool_name || "unknown";
  const toolInput = hookInput.tool_input || {};
  const command = toolInput.command || toolInput.file_path || JSON.stringify(toolInput);
  const errorText = hookInput.error || "tool failure";
  const toolUseId = hookInput.tool_use_id || slug(`${toolName}:${command}:${errorText}`, 40);
  const cwd = hookInput.cwd || workspace;
  const namespace = namespaceForHook(cwd);
  const stderrExcerpt = errorText.slice(0, 2000);
  const paths = extractPaths(`${command}\n${stderrExcerpt}`);
  const observation = {
    kind: "tool_failure",
    tool_name: toolName,
    exit_code: exitCodeFromError(errorText),
    stderr_excerpt: stderrExcerpt,
  };
  if (paths.length > 0) {
    observation.paths = paths;
  }
  const value = {
    summary: `${toolName} failed: ${command}`,
    command,
    error: stderrExcerpt,
    cwd,
    session_id: hookInput.session_id || null,
    tool_use_id: toolUseId,
    ...observation,
  };
  return {
    scope: "session",
    namespace,
    memory_type: "episode",
    topic: "tool_observation",
    field: toolUseId,
    value,
    observation,
    episode_id: toolUseId,
    provenance: {
      source: "claude-code-hook",
      tool: hookInput.hook_event_name || "PostToolUseFailure",
      actor: "hook",
      request_id: toolUseId,
    },
  };
}

module.exports = {
  workspace,
  dataDir,
  appRoot,
  searchScript,
  python,
  pythonArgs,
  readHookInput,
  namespaceForHook,
  rememberPayload,
  extractPaths,
  buildToolFailurePayload,
  slug,
};
