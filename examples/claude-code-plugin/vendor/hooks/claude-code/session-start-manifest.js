#!/usr/bin/env node
"use strict";

/**
 * SessionStart hook sample: inject a short manifest of current user-scope memory.
 *
 * Phase 2 adds PostToolUseFailure, Stop, and PreCompact hooks — see hooks.json.
 *
 * Requirements:
 * - Python 3.10+ on PATH (or set MEMORY_SERVICE_PYTHON)
 * - memory-service data directory at MEMORY_SERVICE_DATA_DIR (see .mcp.json)
 */

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const workspace = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const dataDir =
  process.env.MEMORY_SERVICE_DATA_DIR || path.join(workspace, ".ai-memory", "data");
const appRoot = path.resolve(__dirname, "..", "..", "app");
const exportScript = path.join(appRoot, "export_memory.py");
const python = process.env.MEMORY_SERVICE_PYTHON || (process.platform === "win32" ? "py" : "python3");
const pythonArgs =
  process.platform === "win32" && python === "py" ? ["-3", exportScript] : [exportScript];

if (!fs.existsSync(path.join(dataDir, "memory_service.sqlite3"))) {
  process.stdout.write(
    [
      "<session-memory-manifest>",
      "No local memory database found yet. Use memory_remember via MCP to seed project memory.",
      "</session-memory-manifest>",
      "",
    ].join("\n")
  );
  process.exit(0);
}

const result = spawnSync(
  python,
  [...pythonArgs, "--data-dir", dataDir, "--stdout", "markdown"],
  {
    cwd: workspace,
    encoding: "utf-8",
    env: process.env,
  }
);

if (result.status !== 0) {
  process.stderr.write(result.stderr || "session-start manifest export failed\n");
  process.exit(result.status || 1);
}

const body = (result.stdout || "").trim();
process.stdout.write(
  ["<session-memory-manifest>", body || "_No current governed memories._", "</session-memory-manifest>", ""].join(
    "\n"
  )
);
