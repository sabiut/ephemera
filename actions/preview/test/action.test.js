// node --test actions/preview/test
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const HERE = path.resolve(__dirname, '..');
const SHA = 'a'.repeat(40);
const READY = { id: 7, repository: 'acme/shop', name: 'pr-142', status: 'ready', commit: SHA,
  url: 'https://ci-pr-142-shop-x-web.devpreview.app', urls: { web: 'https://ci-pr-142-shop-x-web.devpreview.app' } };

function run(script, { inputs = {}, state = {}, stdout = '', code = 0, event = {} } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ephemera-action-'));
  const files = { out: path.join(dir, 'out'), st: path.join(dir, 'state'), log: path.join(dir, 'log'),
    ev: path.join(dir, 'event.json') };
  for (const f of [files.out, files.st, files.log]) fs.writeFileSync(f, '');
  fs.writeFileSync(files.ev, JSON.stringify(event));
  const env = { PATH: process.env.PATH, GITHUB_OUTPUT: files.out, GITHUB_STATE: files.st, GITHUB_EVENT_PATH: files.ev,
    GITHUB_REPOSITORY: 'acme/shop', GITHUB_SHA: 'd'.repeat(40), FAKE_LOG: files.log, FAKE_STDOUT: stdout,
    FAKE_CODE: String(code), EPHEMERA_CLI: JSON.stringify(['node', path.join(__dirname, 'fake-cli.js')]) };
  for (const [k, v] of Object.entries(inputs)) env[`INPUT_${k.toUpperCase()}`] = v;
  for (const [k, v] of Object.entries(state)) env[`STATE_${k}`] = v;
  const r = spawnSync('node', [path.join(HERE, script)], { env, encoding: 'utf8' });
  const read = f => fs.readFileSync(f, 'utf8');
  return { status: r.status, log: r.stdout + r.stderr, outputs: read(files.out), state: read(files.st),
    calls: read(files.log).split('\n').filter(Boolean).map(l => JSON.parse(l)) };
}

function outputs(text) {
  const out = {};
  const re = /^(\S+)<<(\S+)\n([\s\S]*?)\n\2$/gm;
  let m;
  while ((m = re.exec(text))) out[m[1]] = m[3];
  return out;
}

const BASE = { token: 'eph_test', name: 'pr-142', images: `web=ghcr.io/acme/shop@sha256:${'1'.repeat(64)}\n\nworker=w@sha256:${'2'.repeat(64)}` };

test('deploys, waits, and exposes the links as outputs', () => {
  const r = run('main.js', { inputs: BASE, stdout: JSON.stringify(READY),
    event: { pull_request: { number: 142, head: { sha: SHA } } } });
  assert.strictEqual(r.status, 0, r.log);
  const args = r.calls[0].args;
  assert.deepStrictEqual(args.slice(0, 7), ['preview', 'deploy', '--name', 'pr-142', '--repo', 'acme/shop', '--output']);
  assert.strictEqual(args[args.indexOf('--commit') + 1], SHA);                 // the PR head, not GITHUB_SHA
  assert.strictEqual(args.filter(a => a === '--image').length, 2);            // blank lines skipped
  assert.strictEqual(args[args.indexOf('--pull-request') + 1], '142');
  assert.ok(args.includes('--wait') && args[args.indexOf('--timeout') + 1] === '10m');
  assert.strictEqual(r.calls[0].token, 'eph_test');
  const o = outputs(r.outputs);
  assert.strictEqual(o.url, READY.url);
  assert.deepStrictEqual(JSON.parse(o.urls), READY.urls);
  assert.strictEqual(o.id, '7');
  assert.strictEqual(o['exit-code'], '0');
  assert.match(r.state, /^id=7$/m);
});

test('a failed preview fails the step with the diagnosis as an annotation', () => {
  const failed = { ...READY, status: 'failed', url: null, diagnosis: { title: 'Image is private',
    explanation: "The cluster wasn't allowed to download the image.", action: 'Add a read-only registry token.' } };
  const r = run('main.js', { inputs: BASE, stdout: JSON.stringify(failed), code: 1 });
  assert.strictEqual(r.status, 1);
  assert.match(r.log, /::error::Preview pr-142 failed: Image is private\. .* What to do: Add a read-only registry token\./);
  assert.match(r.state, /^id=7$/m);                                           // still removed at the end
});

test('a refused deploy saves no id, so the post step removes nothing', () => {
  const r = run('main.js', { inputs: BASE, code: 4 });
  assert.strictEqual(r.status, 4);
  assert.match(r.log, /::error::ephemera exited 4: Ephemera refused the request/);
  assert.strictEqual(outputs(r.outputs)['exit-code'], '4');
  assert.doesNotMatch(r.state, /^id=/m);
});

test('an empty token says why (forks get no secrets)', () => {
  const r = run('main.js', { inputs: { ...BASE, token: '' } });
  assert.strictEqual(r.status, 2);
  assert.match(r.log, /Pull requests from forks get no secrets/);
  assert.strictEqual(r.calls.length, 0);
});

test('optional inputs are passed through, and wait can be turned off', () => {
  const r = run('main.js', { inputs: { ...BASE, serial: '58', 'ttl-hours': '6', compose: 'compose.preview.yml',
    commit: 'c'.repeat(40), wait: 'false', 'ephemera-url': 'https://ephemera.example.com' },
    stdout: JSON.stringify({ ...READY, status: 'pending' }) });
  const args = r.calls[0].args;
  assert.strictEqual(args[args.indexOf('--serial') + 1], '58');
  assert.strictEqual(args[args.indexOf('--ttl') + 1], '6');
  assert.strictEqual(args[args.indexOf('--compose') + 1], 'compose.preview.yml');
  assert.strictEqual(args[args.indexOf('--commit') + 1], 'c'.repeat(40));
  assert.ok(!args.includes('--wait') && !args.includes('--pull-request'));
  assert.strictEqual(r.calls[0].url, 'https://ephemera.example.com');
});

test('the post step removes the preview this run deployed', () => {
  const r = run('post.js', { inputs: { token: 'eph_test' }, state: { id: '7', name: 'pr-142' } });
  assert.strictEqual(r.status, 0);
  assert.deepStrictEqual(r.calls[0].args, ['preview', 'delete', '--id', '7']);
});

test('the post step keeps it when asked, and never fails the job', () => {
  const kept = run('post.js', { inputs: { token: 'eph_test', keep: 'true' }, state: { id: '7', name: 'pr-142' } });
  assert.strictEqual(kept.status, 0);
  assert.strictEqual(kept.calls.length, 0);
  assert.match(kept.log, /Keeping preview pr-142/);
  const broken = run('post.js', { inputs: { token: 'eph_test' }, state: { id: '7', name: 'pr-142' }, code: 4 });
  assert.strictEqual(broken.status, 0);
  assert.match(broken.log, /::warning::Could not remove preview pr-142/);
  const nothing = run('post.js', { inputs: { token: 'eph_test' }, state: { name: 'pr-142' } });
  assert.strictEqual(nothing.calls.length, 0);
});
