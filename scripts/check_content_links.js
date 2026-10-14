const fs = require('node:fs');
const references = require('../static/references.js');
const input = fs.readFileSync(0, 'utf8');
try {
  if (process.argv.includes('--validate')) {
    const { text, files, schemes } = JSON.parse(input);
    process.stdout.write(JSON.stringify(references.validate(text, files, schemes)));
  } else {
    process.stdout.write(JSON.stringify(references.collect(input)));
  }
} catch (error) {
  process.stderr.write(error.message);
  process.exitCode = 2;
}
