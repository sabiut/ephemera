// Stands in for the ephemera CLI: records its arguments and environment,
// prints FAKE_STDOUT (or FAKE_ACCESS for access-header) and exits FAKE_CODE.
const fs = require('fs');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.FAKE_LOG, JSON.stringify({ args,
  token: process.env.EPHEMERA_TOKEN, url: process.env.EPHEMERA_URL }) + '\n');
if (args[1] === 'access-header') {
  process.stdout.write(process.env.FAKE_ACCESS || '');
  process.exit(process.env.FAKE_ACCESS ? 0 : 4);
}
process.stdout.write(process.env.FAKE_STDOUT || '');
process.exit(Number(process.env.FAKE_CODE || 0));
