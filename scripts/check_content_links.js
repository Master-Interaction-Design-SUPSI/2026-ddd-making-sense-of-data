const fs = require('node:fs');
const references = require('../static/references.js');
const media = require('../static/media-table.js');
const input = fs.readFileSync(0, 'utf8');
try {
  if (process.argv.includes('--validate')) {
    const { text, files, schemes, table } = JSON.parse(input);
    const parsed = typeof table === 'string' ? media.read(table) : null;
    process.stdout.write(JSON.stringify(references.validate(text, files, schemes, parsed)));
  } else {
    process.stdout.write(JSON.stringify(references.collect(input)));
  }
} catch (error) {
  process.stderr.write(error.message);
  process.exitCode = 2;
}
