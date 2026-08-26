import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';
const html=fs.readFileSync('frontend/index.html','utf8'),js=fs.readFileSync('frontend/assets/app.js','utf8'),manifest=JSON.parse(fs.readFileSync('frontend/manifest.webmanifest','utf8'));
test('one page has accessible transaction entry and mobile metadata',()=>{assert.match(html,/viewport-fit=cover/);assert.match(html,/id="transaction-form"/);assert.match(html,/aria-live="polite"/);assert.match(html,/type="date"/)});
test('voice remains a confirmed draft',()=>{assert.match(js,/Voice draft ready/);assert.match(html,/Nothing has been recorded yet/);assert.doesNotMatch(js,/recognition\.onresult[\s\S]{0,500}transactions.*POST/)});
test('PWA manifest is local and standalone',()=>{assert.equal(manifest.display,'standalone');assert.equal(manifest.start_url,'/');assert.equal(manifest.name,'Olos-AI Mini Budget Planner')});
test('user text is escaped before rendering',()=>{assert.match(js,/escapeHtml/);assert.match(js,/escapeHtml\(t\.description\)/);assert.match(js,/escapeHtml\(i\.description\)/)});
