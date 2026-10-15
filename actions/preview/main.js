// Deploy the preview (and wait for it), then expose its links as outputs.
const { input, setOutput, saveState, event, runCli, annotate } = require('./lib');

const EXIT = { 1: 'the preview failed', 2: 'a usage or configuration error', 3: 'timed out waiting',
               4: 'Ephemera refused the request or could not be reached' };

function main() {
  const token = input('token');
  const name = input('name');
  const repo = input('repository', process.env.GITHUB_REPOSITORY || '');
  const url = input('ephemera-url', 'https://ephemera-api.devpreview.app');
  if (!token) {
    annotate('error', 'The token input is empty. Store a pipeline token from the Ephemera dashboard as a secret '
      + 'and pass it as token. Pull requests from forks get no secrets.');
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
