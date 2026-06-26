#!/usr/bin/env node
"use strict";

const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { readHookInput, namespaceForHook, dataDir, python, pythonArgs } = require("./hook-lib");

const appRoot = path.resolve(__dirname, "..", "..", "app");

function main() {
  if (process.env.MEMORY_SERVICE_RUN_CONSOLIDATION_ON_SESSION_END !== "1") {
    process.exit(0);
  }

  readHookInput(); // drain stdin
  const namespace = `${namespaceForHook(process.cwd())}-repo`;
  const consolidateScript = path.join(appRoot, "run_consolidation.py");
  const reviewPath = process.env.MEMORY_SERVICE_REVIEW_EXPORT_PATH;

  const args = [
    "--data-dir",
    dataDir,
    "--scope",
    "repository",
    "--namespace",
    namespace,
    "--quiet",
  ];
  if (reviewPath) {
    args.push("--export-review", reviewPath);
  }

  const result = spawnSync(python, pythonArgs(consolidateScript, args), {
    cwd: process.cwd(),
    env: process.env,
    encoding: "utf-8",
  });
  if (result.status !== 0) {
    process.stderr.write(result.stderr || "session-end consolidation failed\n");
  }
  process.exit(0);
}

main();
