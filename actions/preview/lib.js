// Shared by main.js and post.js. No dependencies: the runner's Node, the
// runner's Python, and the CLI from this repository's cli/ directory (the
// runner checks out the whole repository at the action's ref).
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

// Inputs arrive as INPUT_<NAME>, upper-cased, hyphens kept.
function input(name, fallback = '') {
  const value = process.env[`INPUT_${name.toUpperCase()}`];
  return value === undefined || value.trim() === '' ? fallback : value.trim();
}

function append(file, text) {
  if (file) fs.appendFileSync(file, text);
}

function setOutput(name, value) {
  const marker = `ephemera_${crypto.randomBytes(8).toString('hex')}`;
  append(process.env.GITHUB_OUTPUT, `${name}<<${marker}\n${value}\n${marker}\n`);
}

function saveState(name, value) {
  append(process.env.GITHUB_STATE, `${name}=${value}\n`);
}

function state(name) {
  return process.env[`STATE_${name}`] || '';
}

function event() {
  try {
    return JSON.parse(fs.readFileSync(process.env.GITHUB_EVENT_PATH, 'utf8'));
  } catch (e) {
    return {};
  }
}

// The CLI to run: EPHEMERA_CLI (a JSON array, for tests or a pip-installed
// copy), else python3 -m ephemera_cli from this repository.
function cliCommand() {
  if (process.env.EPHEMERA_CLI) return JSON.parse(process.env.EPHEMERA_CLI);
  return ['python3', '-m', 'ephemera_cli'];
}

function runCli(args, token, url) {
  const [cmd, ...prefix] = cliCommand();
  const cliDir = path.resolve(__dirname, '..', '..', 'cli');
  const result = spawnSync(cmd, [...prefix, ...args], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'inherit'],   // progress (stderr) shows in the log as it happens
    env: { ...process.env, EPHEMERA_TOKEN: token, EPHEMERA_URL: url,
           PYTHONPATH: [cliDir, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
  });
  if (result.error) return { code: 4, stdout: '', error: result.error.message };
  return { code: result.status === null ? 4 : result.status, stdout: result.stdout || '' };
}

function annotate(kind, message) {
  // One line per annotation: newlines must be escaped.
  const escaped = String(message).replace(/%/g, '%25').replace(/\r/g, '%0D').replace(/\n/g, '%0A');
  console.log(`::${kind}::${escaped}`);
}

module.exports = { input, setOutput, saveState, state, event, runCli, annotate };
