#!/usr/bin/env node
"use strict";

const { spawn } = require("node:child_process");
const path = require("node:path");

const appRoot = path.resolve(__dirname, "..");
const productionEntry = path.join(appRoot, "run_production_stdio_server.py");

function resolvePython() {
  if (process.env.MEMORY_SERVICE_PYTHON) {
    return { command: process.env.MEMORY_SERVICE_PYTHON, prefixArgs: [] };
  }
  if (process.platform === "win32") {
    return { command: "py", prefixArgs: ["-3"] };
  }
  return { command: "python3", prefixArgs: [] };
}

const { command, prefixArgs } = resolvePython();
const child = spawn(command, [...prefixArgs, productionEntry], {
  cwd: appRoot,
  env: process.env,
  stdio: "inherit",
});

child.on("error", (error) => {
  process.stderr.write(`memory-service-mcp failed to start: ${error.message}\n`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.stderr.write(`memory-service-mcp terminated by signal ${signal}\n`);
    process.exit(1);
  }
  process.exit(code ?? 0);
});
