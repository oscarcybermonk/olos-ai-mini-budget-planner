import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const html=fs.readFileSync('frontend/index.html','utf8');
const js=fs.readFileSync('frontend/assets/app.js','utf8');
const css=fs.readFileSync('frontend/assets/app.css','utf8');
const manifest=JSON.parse(fs.readFileSync('frontend/manifest.webmanifest','utf8'));
const launcher=fs.readFileSync('run.ps1','utf8');
const shortcutInstaller=fs.readFileSync('install-desktop-shortcut.ps1','utf8');
const hiddenLauncher=fs.readFileSync('scripts/launch-hidden.ps1','utf8');
const hiddenLauncherVbs=fs.readFileSync('scripts/launch-hidden.vbs','utf8');

test('one page has accessible transaction entry and mobile metadata',()=>{
  assert.match(html,/viewport-fit=cover/);assert.match(html,/id="transaction-form"/);assert.match(html,/aria-live="polite"/);assert.match(html,/type="date"/);
});

test('absolute-path PowerShell launch resolves the backend from the project root',()=>{
  assert.match(launcher,/uvicorn backend\.main:app --app-dir \$projectRoot/);
  assert.match(launcher,/scripts\\open-when-ready\.ps1/);assert.match(launcher,/\[switch\]\$OpenBrowser/);
  assert.match(shortcutInstaller,/Olos Personal Budget Tracker\.lnk/);assert.match(shortcutInstaller,/olos-personal-budget\.ico/);
  assert.match(shortcutInstaller,/wscript\.exe/);assert.match(shortcutInstaller,/launch-hidden\.vbs/);
  assert.match(hiddenLauncher,/127\.0\.0\.1:8765\/api\/health/);assert.match(hiddenLauncher,/WindowStyle Hidden/);
  assert.match(hiddenLauncherVbs,/shell\.Run command, 0, False/);
});

test('opening raw HTML explains that the desktop launcher is required',()=>{
  assert.match(html,/window\.location\.protocol === 'file:'/);
  assert.match(html,/Use the Olos desktop launcher/);
  assert.match(html,/http:\/\/localhost:8765/);
});

test('Quick Add preserves content type when idempotency headers are added',()=>{
  assert.match(js,/const headers = \{'Content-Type': 'application\/json', \.\.\.\(options\.headers \|\| \{\}\)\}/);
  assert.match(js,/fetch\(path, \{\.\.\.options, headers\}\)/);
  assert.doesNotMatch(js,/headers:\{'Content-Type':[\s\S]{0,80},\.\.\.options\}/);
});

test('all transaction types use field-level validation and reset after save',()=>{
  for(const type of ['expense','income','bill','savings'])assert.match(html,new RegExp(`data-type="${type}"`));
  assert.match(js,/validateTransactionForm/);assert.match(js,/showFormErrors/);assert.match(js,/resetForm\(\); await refresh\(\)/);
  assert.match(html,/id="amount-error"/);assert.match(html,/id="description-error"/);assert.match(html,/id="transaction-date-error"/);assert.match(html,/id="category-error"/);
});

test('type switching preserves useful draft fields and announces the new type',()=>{
  assert.match(js,/Switching type deliberately preserves amount/);assert.match(js,/Entry kept — now recording/);assert.match(html,/id="entry-kind"/);
});

test('activity guidance and visible edit-delete controls are present',()=>{
  assert.match(js,/This example is not saved/);assert.match(js,/data-example-action/);assert.match(css,/\.action-delete/);assert.match(css,/\.action-edit/);assert.match(js,/confirm\('Delete this transaction/);
});

test('voice remains a confirmed draft',()=>{
  assert.match(js,/Voice draft ready/);assert.match(html,/Nothing has been recorded yet/);assert.doesNotMatch(js,/recognition\.onresult[\s\S]{0,500}transactions.*POST/);
});

test('calendar and recurring cues are accessible',()=>{
  assert.match(html,/id="calendar-dialog"/);assert.match(js,/\/api\/calendar\//);assert.match(js,/Expected income/);assert.match(js,/Planned bill/);assert.match(js,/Planned savings/);assert.match(js,/Record as paid/);
});

test('credit and payment-method UI stays optional',()=>{
  assert.match(html,/Credit, Pay Later &amp; Loans/);assert.match(html,/id="payment-method"/);assert.match(html,/id="expense-facility"/);assert.match(js,/\/api\/credit-facilities/);assert.match(js,/transaction_role==='credit_payment'/);
});

test('fixed loans, compact position strip, and display-only APR are represented',()=>{
  assert.match(html,/id="liability-strip"/);assert.match(html,/value="fixed_loan"/);assert.match(html,/Annual interest rate \/ APR/);assert.match(html,/id="balance-dialog"/);
  assert.match(js,/estimated owing/);assert.match(js,/APR is display-only/);assert.match(js,/\/reconcile/);assert.match(js,/actual_loan_payments/);
});

test('fixed loans can be saved first and linked later',()=>{
  assert.match(html,/None — link later/);assert.match(html,/Optional\. Save the loan now/);
  assert.match(js,/linked_recurring_rule_id:type==='fixed_loan'&&\$\('#facility-rule'\)\.value\?Number/);
  assert.match(js,/formatted=raw\.replace\(\/\^\\\$\\s\*\//);assert.match(js,/\\d\{1,3\}.*\\d\{3\}/);assert.match(js,/formatted\.replace\(\/,\/g,''\)/);
});

test('local Olos identity is used by the page and PWA',()=>{
  assert.match(html,/assets\/olos-ecosystem-logo-128\.png/);assert.match(html,/OLOS AI/);assert.match(html,/Personal Budget Tracker/);
  assert.ok(fs.statSync('frontend/assets/olos-personal-budget-192.png').size>1000);
  assert.ok(fs.statSync('frontend/assets/olos-personal-budget-512.png').size>1000);
  assert.ok(fs.statSync('frontend/assets/olos-personal-budget.ico').size>1000);
  assert.deepEqual(manifest.icons.map(icon=>icon.sizes),['192x192','512x512']);
  assert.match(css,/--signal/);assert.match(css,/\.metric\.savings::before/);
});

test('recurring management distinguishes skip from rule deletion',()=>{
  assert.match(html,/Manage recurring rules/);assert.match(html,/id="recurring-list-dialog"/);assert.match(js,/Skip this occurrence only/);assert.match(js,/Delete this recurring rule/);assert.match(js,/history preserved/);
});

test('recurring rules support adjustable week month and year intervals',()=>{
  assert.match(html,/id="interval-count"/);assert.match(html,/>Weeks</);assert.match(html,/>Months</);assert.match(html,/>Years</);
  assert.match(js,/interval_count:intervalCount/);assert.match(js,/recurringInterval/);assert.match(js,/every \$\{interval\.count\}/);
  assert.match(js,/frequency==='fortnightly'/);
});

test('reset requires exact typed confirmation and offers backup first',()=>{
  assert.match(html,/Create JSON backup/);assert.match(html,/Type <strong>RESET<\/strong>/);assert.match(js,/value!==\'RESET\'/);assert.match(js,/\/api\/reset/);
  assert.doesNotMatch(html,/value="cancel"/);assert.match(html,/class="icon-button form-close" type="button"/);assert.match(js,/\.form-close/);
});

test('dark mode defines explicit form and autofill surfaces',()=>{
  assert.match(css,/:root\[data-theme="dark"\] input/);assert.match(css,/input:-webkit-autofill/);assert.match(css,/\.type-tab\.active/);assert.match(css,/\.draft-note/);
});

test('PWA manifest is local and standalone',()=>{
  assert.equal(manifest.display,'standalone');assert.equal(manifest.start_url,'/');assert.equal(manifest.name,'Olos Personal Budget Tracker');
});

test('user text is escaped before rendering',()=>{
  assert.match(js,/escapeHtml/);assert.match(js,/escapeHtml\(transaction\.description\)/);assert.match(js,/escapeHtml\(item\.description\)/);assert.match(js,/escapeHtml\(f\.name\)/);
});
