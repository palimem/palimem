#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const path = require("node:path");

const appRoot = path.resolve(__dirname, "..");

function resolvePython() {
  if (process.env.MEMORY_SERVICE_PYTHON) {
    return { command: process.env.MEMORY_SERVICE_PYTHON, prefixArgs: [] };
  }
  if (process.platform === "win32") {
    return { command: "py", prefixArgs: ["-3"] };
  }
  return { command: "python3", prefixArgs: [] };
}

function runPython(scriptName, args) {
  const scriptPath = path.join(appRoot, scriptName);
  const { command, prefixArgs } = resolvePython();
  const result = spawnSync(command, [...prefixArgs, scriptPath, ...args], {
    cwd: appRoot,
    env: process.env,
    stdio: "inherit",
  });
  if (result.error) {
    process.stderr.write(`ai-memory failed: ${result.error.message}\n`);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

const CONNECT_HARNESSES = {
  copilot: "connect_copilot.py",
  cursor: "connect_cursor.py",
  windsurf: "connect_windsurf.py",
  codex: "connect_codex.py",
  vscode: "connect_vscode.py",
  gemini: "connect_gemini.py",
};

function usage() {
  const harnessList = Object.keys(CONNECT_HARNESSES).join("|");
  process.stderr.write(
    [
      "ai-memory — memory-service operator CLI",
      "",
      "Usage:",
      `  ai-memory connect <harness> [--project-root PATH] [--data-dir PATH] [--replace] [--dry-run]`,
      `    Supported harnesses: ${harnessList}`,
      "  ai-memory review list|export|accept|reject --data-dir <dir> [options]",
      "  ai-memory consolidate --data-dir <dir> [--dry-run] [--export-review path]",
      "",
    ].join("\n")
  );
  process.exit(2);
}

const [sub, action, ...rest] = process.argv.slice(2);
if (!sub) {
  usage();
}

if (sub === "connect") {
  if (!action) {
    usage();
  }
  const script = CONNECT_HARNESSES[action];
  if (!script) {
    process.stderr.write(
      `ai-memory connect: unknown harness '${action}'\n` +
        `Supported harnesses: ${Object.keys(CONNECT_HARNESSES).join(", ")}\n`
    );
    process.exit(2);
  }
  runPython(script, rest);
}

if (sub === "review") {
  if (!action || !["list", "export", "accept", "reject"].includes(action)) {
    usage();
  }
  runPython("review_memory.py", [action, ...rest]);
}

if (sub === "consolidate") {
  runPython("run_consolidation.py", process.argv.slice(3));
}

usage();
