import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const excluded = new Set(['.git', '.tmp', '.venv', '.mini-runtime', '.hackathon-runtime', '__pycache__', 'data', 'node_modules']);
const binaryExtensions = new Set(['.ico', '.png', '.webp', '.jpg', '.jpeg', '.pyc']);

function sourceFiles(directory = '.') {
  const files = [];
  for (const entry of fs.readdirSync(directory, {withFileTypes: true})) {
    if (excluded.has(entry.name)) continue;
    const candidate = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...sourceFiles(candidate));
    else if (!binaryExtensions.has(path.extname(entry.name).toLowerCase())) files.push(candidate);
  }
  return files;
}

test('public baseline contains no personal-app binding or hackathon integration', () => {
  const forbidden = [
    ['localhost', '8765'].join(':'),
    ['Olos', 'Personal', 'Budget', 'Tracker'].join(' '),
    ['OLOS', 'BUDGET', 'DATA', 'DIR'].join('_'),
    ['OLOS', 'PERSONAL', 'DATA', 'DIR'].join('_'),
    ['OLOS', 'HACKATHON'].join('_'),
    ['Web', 'M', 'C', 'P'].join(''),
    ['model', 'Context'].join(''),
    ['budget', 'pocket', 'tool'].join(' '),
    ['C:', 'Users', 'tonys'].join('\\'),
  ];
  const violations = [];
  for (const file of sourceFiles()) {
    const content = fs.readFileSync(file, 'utf8');
    for (const token of forbidden) if (content.includes(token)) violations.push(`${file}: ${token}`);
  }
  assert.deepEqual(violations, []);
});

test('public Mini storage, port, launcher, and health identity are namespaced', () => {
  const db = fs.readFileSync('backend/db.py', 'utf8');
  const backend = fs.readFileSync('backend/main.py', 'utf8');
  const runner = fs.readFileSync('run.ps1', 'utf8');
  const hidden = fs.readFileSync('scripts/launch-hidden.ps1', 'utf8');
  assert.equal(fs.readFileSync('INSTANCE_ID', 'utf8').trim(), 'olos-ai-mini-budget-planner');
  assert.match(db, /OLOS_MINI_DATA_DIR/);
  assert.match(db, /\.mini-runtime/);
  assert.match(backend, /APP_INSTANCE_ID = "olos-ai-mini-budget-planner"/);
  assert.match(runner, /\[int\]\$Port = 8876/);
  assert.match(hidden, /\$health\.application -eq \$expectedApplication/);
});
