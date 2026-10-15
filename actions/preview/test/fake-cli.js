// Stands in for the ephemera CLI: records its arguments and environment,
// prints FAKE_STDOUT and exits FAKE_CODE.
const fs = require('fs');
fs.appendFileSync(process.env.FAKE_LOG, JSON.stringify({ args: process.argv.slice(2),
  token: process.env.EPHEMERA_TOKEN, url: process.env.EPHEMERA_URL }) + '\n');
process.stdout.write(process.env.FAKE_STDOUT || '');
process.exit(Number(process.env.FAKE_CODE || 0));
