import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const html=fs.readFileSync('frontend/index.html','utf8');
const js=fs.readFileSync('frontend/assets/app.js','utf8');
const css=fs.readFileSync('frontend/assets/app.css','utf8');
const manifest=JSON.parse(fs.readFileSync('frontend/manifest.webmanifest','utf8'));

test('one page has accessible transaction entry and mobile metadata',()=>{
  assert.match(html,/viewport-fit=cover/);assert.match(html,/id="transaction-form"/);assert.match(html,/aria-live="polite"/);assert.match(html,/type="date"/);
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
  assert.match(html,/Credit &amp; Pay Later/);assert.match(html,/id="payment-method"/);assert.match(html,/id="expense-facility"/);assert.match(js,/\/api\/credit-facilities/);assert.match(js,/transaction_role === 'credit_payment'/);
});

test('PWA manifest is local and standalone',()=>{
  assert.equal(manifest.display,'standalone');assert.equal(manifest.start_url,'/');assert.equal(manifest.name,'Olos-AI Mini Budget Planner');
});

test('user text is escaped before rendering',()=>{
  assert.match(js,/escapeHtml/);assert.match(js,/escapeHtml\(transaction\.description\)/);assert.match(js,/escapeHtml\(item\.description\)/);assert.match(js,/escapeHtml\(facility\.name\)/);
});
