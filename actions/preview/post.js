// After the job (even a failed or cancelled one): remove the preview unless
// keep is true. Its expiry covers the case where this never runs.
const { input, state, runCli, annotate } = require('./lib');

function post() {
  const name = state('name');
  // Only a preview this run deployed: a run whose deploy was refused (an
  // older run, by serial) must not remove the one a newer run deployed.
  const id = state('id');
  if (!id) return 0;
  if (input('keep', 'false') === 'true') {
    console.log(`Keeping preview ${name} (keep: true); it expires after ttl-hours.`);
    return 0;
  }
  const token = input('token');
  const { code } = runCli(['preview', 'delete', '--id', id], token,
                          input('ephemera-url', 'https://ephemera-api.devpreview.app'));
  if (code !== 0) {
    // Never fail the job over cleanup: the preview's expiry removes it anyway.
    annotate('warning', `Could not remove preview ${name} (ephemera exited ${code}); it will expire on its own.`);
  } else {
    console.log(`Removed preview ${name}.`);
  }
  return 0;
}

if (require.main === module) process.exitCode = post();
module.exports = { post };
