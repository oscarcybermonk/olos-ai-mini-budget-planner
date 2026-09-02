import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const excluded = new Set(['.git', '.tmp', '.venv', '.hackathon-runtime', '__pycache__', 'data', 'node_modules']);
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

test('public source contains no personal-app path, port, name, or data namespace', () => {
  const forbidden = [
    ['localhost', '8765'].join(':'),
    ['Olos', 'Personal', 'Budget', 'Tracker'].join(' '),
    ['OLOS', 'BUDGET', 'DATA', 'DIR'].join('_'),
    ['OLOS', 'PERSONAL', 'DATA', 'DIR'].join('_'),
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

test('hackathon storage, port, cookie, launcher, and health identity are namespaced', () => {
  const db = fs.readFileSync('backend/db.py', 'utf8');
  const demo = fs.readFileSync('backend/demo.py', 'utf8');
  const backend = fs.readFileSync('backend/main.py', 'utf8');
  const runner = fs.readFileSync('run.ps1', 'utf8');
  const hidden = fs.readFileSync('scripts/launch-hidden.ps1', 'utf8');
  assert.equal(fs.readFileSync('INSTANCE_ID', 'utf8').trim(), 'olos-ai-mini-budget-planner-hackathon');
  assert.match(db, /OLOS_HACKATHON_DATA_DIR/);
  assert.match(db, /\.hackathon-runtime/);
  assert.match(db, /olos-mini-budget-hackathon\.sqlite3/);
  assert.match(demo, /OLOS_HACKATHON_DEMO_MODE/);
  assert.match(demo, /olos_hackathon_demo_session/);
  assert.match(backend, /APP_INSTANCE_ID = "olos-ai-mini-budget-planner-hackathon"/);
  assert.match(runner, /\[int\]\$Port = 8876/);
  assert.match(hidden, /\$health\.application -eq \$expectedApplication/);
});
