// Deploy the preview (and wait for it), then expose its links as outputs.
const { input, setOutput, saveState, event, runCli, annotate } = require('./lib');

const EXIT = { 1: 'the preview failed', 2: 'a usage or configuration error', 3: 'timed out waiting',
               4: 'Ephemera refused the request or could not be reached' };

function main() {
  const token = input('token');
  const name = input('name');
  const repo = input('repository', process.env.GITHUB_REPOSITORY || '');
  const url = input('ephemera-url', 'https://ephemera-api.devpreview.app');
  // No token: OIDC, if this job may request GitHub's identity token (the CLI
  // exchanges it for a short-lived pipeline token).
  if (!token && !process.env.ACTIONS_ID_TOKEN_REQUEST_URL) {
    setOutput('exit-code', 2);
    annotate('error', 'No token: either pass a pipeline token from the Ephemera dashboard as token (stored as a '
      + 'secret), or use OIDC: give the job permissions id-token: write and allow GitHub Actions OIDC on the '
      + "repository's page in Ephemera. Pull requests from forks get neither.");
    return 2;
  }
  saveState('name', name);

  const ev = event();
  const args = ['preview', 'deploy', '--name', name, '--repo', repo, '--output', 'json'];
  const commit = input('commit', ((ev.pull_request || {}).head || {}).sha || process.env.GITHUB_SHA || '');
  if (commit) args.push('--commit', commit);
  for (const line of input('images').split('\n').map(l => l.trim()).filter(Boolean)) args.push('--image', line);
  if (input('compose')) args.push('--compose', input('compose'));
  args.push('--ttl', input('ttl-hours', '24'));
  const pr = input('pull-request', ev.pull_request ? String(ev.pull_request.number) : '');
  if (pr) args.push('--pull-request', pr);
  if (input('serial')) args.push('--serial', input('serial'));
  if (input('wait', 'true') === 'true') args.push('--wait', '--timeout', input('timeout', '10m'));

  const { code, stdout, error } = runCli(args, token, url);
  setOutput('exit-code', code);
  if (error) annotate('error', `Could not run the ephemera CLI: ${error}`);
  let preview = null;
  try {
    preview = stdout.trim() ? JSON.parse(stdout) : null;
  } catch (e) {
    console.log(stdout);
  }
  if (preview) {
    setOutput('id', preview.id);
    setOutput('status', preview.status || '');
    setOutput('url', preview.url || '');
    setOutput('urls', JSON.stringify(preview.urls || {}));
    saveState('id', preview.id);   // post.js removes it at the end of the job, even if tests fail
    console.log(`${preview.repository} ${preview.name}: ${preview.status}${preview.url ? ` at ${preview.url}` : ''}`);
  }
  if (code === 0 && preview && preview.status === 'ready') {
    // For tests against a protected preview (harmless for a public one).
    const access = runCli(['preview', 'access-header', '--id', String(preview.id), '--format', 'json',
                           '--minutes', input('access-minutes', '30')], token, url);
    try {
      const a = JSON.parse(access.stdout);
      console.log(`::add-mask::${a.value}`);   // before it could appear anywhere in the log
      setOutput('access-header', `${a.header}: ${a.value}`);
      setOutput('access-token', a.value);
    } catch (e) {
      annotate('warning', `Could not get an access header for preview ${name} (ephemera exited ${access.code}); `
        + 'tests can still reach it if its previews are public.');
    }
  }
  if (code === 1 && preview) {
    const d = preview.diagnosis || {};
    annotate('error', [`Preview ${name} failed: ${d.title || preview.error || 'see the log'}.`, d.explanation,
      d.action ? `What to do: ${d.action}` : ''].filter(Boolean).join(' '));
  } else if (code !== 0) {
    annotate('error', `ephemera exited ${code}: ${EXIT[code] || 'unexpected error'} (details above).`);
  }
  return code;
}

if (require.main === module) process.exitCode = main();
module.exports = { main };
