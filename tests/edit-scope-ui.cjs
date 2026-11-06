const previewResponse=require('./preview-response.cjs');
// Generalized edit scope acceptance: unique isolated DB, owns only loopback 8015.
// No production data, .env, real model or map service is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');
const { spawn, spawnSync } = require('node:child_process');
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(process.env.PLAYWRIGHT_MODULE_DIR || path.join(process.env.USERPROFILE || '', '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const root = path.resolve(__dirname, '..'), base = 'http://127.0.0.1:8015';
const python = process.env.UI_PYTHON_PATH || path.join(root, '.venv/Scripts/python.exe');
const runId = crypto.randomUUID(), dataDir = path.join(root, '.runtime', 'edit-scope-e2e-' + runId);
const database = path.join(dataDir, 'travel.sqlite3');
const suite=process.env.PHASE4_PROTOCOL_TEST==='1'?'phase4':'edit-scope';
const shots = path.join(root, 'docs/screenshots',suite), reports = path.join(root, 'docs/verification');
fs.mkdirSync(dataDir, { recursive: true }); fs.mkdirSync(shots, { recursive: true }); fs.mkdirSync(reports, { recursive: true });
const logFd = fs.openSync(path.join(dataDir, 'server.log'), 'a');
const env = { ...process.env, TRAVEL_ENV_FILE: path.join(dataDir, 'absent.env'), TRAVEL_GENUI_DATA_DIR: dataDir, AGENT_MODE: 'demo', CANDIDATE_AGENT_MODE: 'mock', CANDIDATE_TTL_SECONDS: '600', LLM_API_KEY: '', DEEPSEEK_API_KEY: '', AMAP_KEY: '', AGENT_REQUESTS_PER_MINUTE: '120', PYTHONDONTWRITEBYTECODE: '1' };
let child, ownedPid, browser, context, page, tripId = '';
const results = [], errors = [], external = [], changes = [], rejections = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function diff(a, b, prefix = '') {
  if (JSON.stringify(a) === JSON.stringify(b)) return [];
  if (a && b && typeof a === 'object' && typeof b === 'object' && !Array.isArray(a) && !Array.isArray(b)) return [...new Set([...Object.keys(a), ...Object.keys(b)])].sort().flatMap(key => diff(a[key], b[key], prefix + '/' + key.replace(/~/g, '~0').replace(/\//g, '~1')));
  return [prefix];
}
function business(s) {
  const trip = structuredClone(s.tripState);
  for (const day of Object.values(trip.days)) day.stops = { order: day.stops.map(s => s.id), byId: Object.fromEntries(day.stops.map(s => [s.id, s])) };
  return trip;
}
function stored() {
  const code = 'import sqlite3,sys;from pathlib import Path;c=sqlite3.connect(Path(sys.argv[1]).as_uri()+"?mode=ro",uri=True);r=c.execute("SELECT document FROM sessions WHERE id=?",(sys.argv[2],)).fetchone();assert r;print(r[0]);c.close()';
  const result = spawnSync(python, ['-X', 'utf8', '-c', code, database, tripId], { cwd: root, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr); return result.stdout.trim();
}
async function health() { const r = await fetch(base + '/api/health'); assert.ok(r.ok); return r.json(); }
async function startServer() {
  const busy = await new Promise(resolve => { const c = net.connect(8015, '127.0.0.1'); c.once('connect', () => { c.destroy(); resolve(true); }); c.once('error', () => resolve(false)); });
  assert.equal(busy, false, '8015 must be free; never reuse an unknown service');
  child = spawn(python, ['-B', '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8015'], { cwd: root, env, windowsHide: true, stdio: ['ignore', logFd, logFd] });
  for (let i = 0; i < 100; i++) { try { const h = await health(); assert.equal(path.resolve(h.projectRoot), root); ownedPid = h.processId; assert.ok(Number.isInteger(ownedPid)); return h; } catch (error) { if (i === 99) throw error; await delay(200); } }
}
async function stopServer() {
  if (!ownedPid) return;
  const h = await health(); assert.equal(h.processId, ownedPid); assert.equal(path.resolve(h.projectRoot), root);
  const command = `$taskProcess=Get-CimInstance Win32_Process -Filter 'ProcessId=${ownedPid}'; if($taskProcess -and $taskProcess.CommandLine -match 'backend\\.app:app' -and $taskProcess.CommandLine -match '--port 8015'){Stop-Process -Id ${ownedPid}}else{throw 'Not the owned test server'}`;
  const result = spawnSync('powershell.exe', ['-NoProfile', '-Command', command], { cwd: root, encoding: 'utf8', windowsHide: true }); assert.equal(result.status, 0, result.stderr);
  if (child && !child.killed) child.kill(); ownedPid = null;
  for (let i = 0; i < 50; i++) { try { await health(); await delay(100); } catch { return; } } throw Error('Owned test port did not close');
}
const record = name => { results.push({ name, passed: true }); console.log('PASS ' + name); };
async function current(suffix = '') { const r = await context.request.get(base + '/api/sessions/' + tripId + suffix); const b = await r.json(); assert.equal(r.status(), 200, JSON.stringify(b)); return b; }
async function api(method, suffix, body, status = 200) { const r = await context.request[method](base + '/api/sessions/' + tripId + suffix, { data: body }); const b = await r.json(); assert.equal(r.status(), status, JSON.stringify(b)); return b; }
async function action(method, suffix, callback, status = 200) { const wait = page.waitForResponse(r => r.request().method() === method && new URL(r.url()).pathname.endsWith(suffix)); await callback(); const r = await wait, b = await previewResponse(r); assert.equal(r.status(), status, JSON.stringify(b)); await delay(100); return b; }
async function unchanged(before, row) { assert.deepEqual(await current(), before); assert.equal(stored(), row, 'Preview/rejected request cannot change any stored session bytes'); }
async function reject(name, method, suffix, body, status) { const before = await current(), row = stored(); const result = await api(method, suffix, body, status); await unchanged(before, row); rejections.push({ name, status, version: before.version, unchanged: true, response: result.detail }); }
function binding(s, day, kind = 'DayCard', period) { const b = Object.values(s.componentRegistry).find(item => item.dayKey === day && item.kind === kind && (period === undefined || item.period === period)); assert.ok(b, [day, kind, period].join('/')); return b; }
function pathFor(id, field) { return '/entities/activities/' + id + '/' + field; }
async function reload() { await page.reload({ waitUntil: 'networkidle' }); await page.getByRole('button', { name: '继续上次行程', exact: true }).click(); await page.getByRole('tabpanel').waitFor(); }
async function selectDay(day) { const b = binding(await current(), day); if (await page.getByTestId('edit-mode-toggle').getAttribute('aria-pressed') !== 'true') await page.getByTestId('edit-mode-toggle').click(); await page.getByTestId('local-editor-panel').getByRole('button', { name: '选择' + b.label, exact: true }).click(); }
async function selectPeriod(day, period) { await selectDay(day); await page.getByTestId('editor-period-' + period).click(); await page.locator('#editor-activity').waitFor(); }
async function manual(field, activityId, value, expectedStatus = 200) { await page.locator('#editor-field').selectOption(field); await page.locator('#editor-activity').selectOption(activityId); if (field === 'placeId') await page.locator('#editor-value').selectOption(value); else await page.locator('#editor-value').fill(value); return action('POST', '/editor/preview', () => page.getByTestId('validate-local-patch').click(), expectedStatus); }
async function generated(instruction) { await page.locator('#instruction').fill(instruction); const b = await action('POST', '/edits/candidates', () => page.getByTestId('generate-edit-candidates').click()); await page.getByTestId('phase2-candidates').waitFor(); return b; }
async function applied(before, after, allowedBusinessPaths, name) {
  assert.equal(after.version, before.version + 1); const actual = diff(business(before), business(after)); assert.ok(actual.length);
  assert.ok(actual.every(p => allowedBusinessPaths.includes(p)), 'Unauthorized complete business diff: ' + JSON.stringify(actual));
  assert.deepEqual(after.persistedConstraints, before.persistedConstraints);
  changes.push({ name, baseVersion: before.version, version: after.version, allowedBusinessPaths, actualBusinessDiff: actual });
}
async function screenshot(name, targetTestId) {
  while (await page.getByRole('button', { name: '关闭提示', exact: true }).count()) await page.getByRole('button', { name: '关闭提示', exact: true }).first().click();
  if (targetTestId) await page.getByTestId(targetTestId).evaluate(el => { const pane = el.closest('.control-panel'); pane.scrollTop += el.getBoundingClientRect().top - pane.getBoundingClientRect().top - 12; });
  await page.screenshot({ path: path.join(shots, name), animations: 'disabled' });
}
(async () => {
  try {
    const live = await startServer();
    browser = await playwright.chromium.launch({ headless: true, executablePath: process.env.UI_BROWSER_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
    context = await browser.newContext({ viewport: { width: 1600, height: 1100 }, locale: 'zh-CN', reducedMotion: 'reduce' });
    page = await context.newPage(); page.setDefaultTimeout(20000);
    page.on('pageerror', e => errors.push(e.message)); page.on('request', r => { if (!['127.0.0.1', 'localhost'].includes(new URL(r.url()).hostname)) external.push(new URL(r.url()).origin); });
    await page.goto(base, { waitUntil: 'networkidle' });
    let session = await action('POST', '/api/demo-sessions', () => page.getByTestId('open-three-day-demo').click()); tripId = session.id;
    const idsAtStart = Object.keys(session.componentRegistry).sort();
    const firstMorning = binding(session, 'day1', 'ActivityCard', 'am');
    await selectPeriod('day1', 'am');
    const beforeMorning = await current(), morningRow = stored();
    let batch = await generated('安排轻松一些');
    assert.equal(batch.candidateCount, 3); assert.equal(batch.generationMode, 'mock');
    assert.ok(batch.effectiveScope.eligibleActivityIds.includes(firstMorning.entityId));
    for (const candidate of batch.candidates) assert.ok(candidate.patch.every(op => op.path.startsWith('/entities/activities/' + firstMorning.entityId + '/')));
    for (const letter of ['A', 'B', 'C']) { await page.getByTestId('phase2-candidate-' + letter).click(); await unchanged(beforeMorning, morningRow); }
    if(process.env.PHASE4_PROTOCOL_TEST==='1'){
      assert.ok((await page.getByTestId('generation-progress').innerText()).includes('校验'));
      await page.getByTestId('candidate-evaluation').waitFor();
      assert.deepEqual(batch.candidates.map(c=>c.evaluation.rank).sort(),[1,2,3]);
      assert.ok(batch.candidates.every(c=>c.a2ui[0].version==='v0.9.1'));
      const saved=await current(),savedRow=stored();
      for(const variant of ['disconnect','delta','component']){
        await page.getByTestId('cancel-edit-candidates').click();
        await page.route('**/edits/candidates',async route=>{
          const started={type:'RUN_STARTED',threadId:tripId,runId:'fake'};
          let events=[started];
          if(variant==='delta')events.push({type:'STATE_DELTA',delta:[{op:'replace',path:'/version',value:999}]});
          if(variant==='component'){
            const malformed=structuredClone(batch);malformed.candidates[0].a2ui[1].updateComponents.components[1].component='HTML';
            events.push({type:'CUSTOM',name:'candidates.complete',value:malformed},{type:'RUN_FINISHED',threadId:tripId,runId:'fake'});
          }
          await route.fulfill({status:200,contentType:'text/event-stream',body:events.map(e=>'data: '+JSON.stringify(e)+'\n\n').join('')});
        });
        await page.getByTestId('generate-edit-candidates').click();await page.getByTestId('phase2-error').waitFor();
        assert.equal(await page.getByTestId('phase2-candidates').count(),0);await unchanged(saved,savedRow);
        await page.unroute('**/edits/candidates');
        batch=await generated('安排轻松一些');
      }
      await page.getByTestId('cancel-edit-candidates').click();
      await page.route('**/edits/candidates',async route=>{await delay(600);await route.fulfill({status:200,contentType:'text/event-stream',body:''}).catch(()=>{});});
      await page.getByTestId('generate-edit-candidates').click();await page.getByRole('button',{name:'停止等待',exact:true}).click();
      await delay(700);assert.equal(await page.getByTestId('phase2-candidates').count(),0);await unchanged(saved,savedRow);await page.unroute('**/edits/candidates');
      batch=await generated('安排轻松一些');
      record('Phase4 real progress, ranking, declarative preview, disconnect/illegal STATE_DELTA/component rejection, retry and cancel are isolated');
    }
    await screenshot('first-morning-candidates.png', 'phase2-candidates');
    await page.getByTestId('phase2-candidate-B').click(); session = await action('POST', '/edits/commit', () => page.getByTestId('commit-edit-candidate').click());
    await applied(beforeMorning, session, ['/days/day1/stops/byId/' + firstMorning.stopId + '/startTime'], 'First-day morning mock B');
    assert.deepEqual(session.tripState.days.day2, beforeMorning.tripState.days.day2); assert.deepEqual(session.tripState.days.day3, beforeMorning.tripState.days.day3);
    record('Unlocked first-day morning supports three real mock candidates; preview is isolated and only B is saved');

    const pm = binding(session, 'day1', 'PeriodGroup', 'pm');
    session = await api('patch', '/editor/locks', { baseVersion: session.version, componentId: pm.componentId, locked: true }); await reload();
    await selectPeriod('day1', 'am');
    const lockedBefore = await current(), lockedRow = stored();
    await manual('startTime', firstMorning.entityId, '12:30', 423); await unchanged(lockedBefore, lockedRow);
    session = await api('patch', '/editor/locks', { baseVersion: session.version, componentId: pm.componentId, locked: false }); await reload(); await selectPeriod('day1', 'am');
    const crossingBefore = await current(), crossingRow = stored();
    const crossing = await manual('startTime', firstMorning.entityId, '12:30');
    assert.equal(crossing.document.entities.activities[firstMorning.entityId].period, 'pm');
    assert.ok(crossing.changedPaths.includes(pathFor(firstMorning.entityId, 'period')));
    await unchanged(crossingBefore, crossingRow);
    session = await action('POST', '/editor/apply', () => page.getByTestId('apply-local-patch').click());
    await applied(crossingBefore, session, ['/days/day1/stops/byId/' + firstMorning.stopId + '/startTime'], 'First morning activity moved into afternoon');
    assert.equal(session.componentRegistry[firstMorning.componentId].period, 'pm');
    assert.equal(session.componentRegistry[firstMorning.componentId].parentId, pm.componentId);
    assert.deepEqual(Object.keys(session.componentRegistry).sort(), idsAtStart);
    record('Valid time changes cross periods with stable IDs; moving into a locked target period is rejected');

    const thirdCard = binding(session, 'day3', 'ActivityCard', 'am');
    await reject('Other day is outside the selected activity even when unlocked', 'post', '/editor/preview', { baseVersion: session.version, selectedComponentId: firstMorning.componentId, fields: ['description'], patch: [{ op: 'replace', path: pathFor(thirdCard.entityId, 'description'), value: 'must not change' }] }, 403);
    await reject('Derived metadata cannot be patched directly', 'post', '/editor/preview', { baseVersion: session.version, selectedComponentId: firstMorning.componentId, fields: ['startTime'], patch: [{ op: 'replace', path: pathFor(firstMorning.entityId, 'period'), value: 'evening' }] }, 403);
    record('Generalized dates preserve exact selected scope and reject direct writes to derived metadata');

    await selectDay('day3');
    const thirdLastStop = session.tripState.days.day3.stops.at(-1);
    const thirdLast = Object.values(session.componentRegistry).find(item => item.kind === 'ActivityCard' && item.dayKey === 'day3' && item.stopId === thirdLastStop.id);
    const beforeEvening = await current(); await manual('startTime', thirdLast.entityId, '19:00');
    session = await action('POST', '/editor/apply', () => page.getByTestId('apply-local-patch').click());
    await applied(beforeEvening, session, ['/days/day3/stops/byId/' + thirdLast.stopId + '/startTime'], 'Third-day activity moved into evening');
    await selectPeriod('day3', 'evening');
    const eveningAuthority = await current(), eveningRow = stored();
    batch = await generated('晚上安排轻松一些'); assert.equal(batch.candidateCount, 3);
    assert.ok(batch.effectiveScope.eligibleActivityIds.includes(thirdLast.entityId));
    for (const letter of ['A', 'B', 'C']) { await page.getByTestId('phase2-candidate-' + letter).click(); await unchanged(eveningAuthority, eveningRow); }
    await screenshot('third-evening-candidates.png', 'phase2-candidates');
    await page.getByTestId('phase2-candidate-C').click(); session = await action('POST', '/edits/commit', () => page.getByTestId('commit-edit-candidate').click());
    await applied(eveningAuthority, session, ['/days/day3/stops/byId/' + thirdLast.stopId + '/startTime', '/days/day3/stops/byId/' + thirdLast.stopId + '/description'], 'Third-day evening mock C');
    assert.deepEqual(session.tripState.days.day1, eveningAuthority.tripState.days.day1); assert.deepEqual(session.tripState.days.day2, eveningAuthority.tripState.days.day2);
    record('Third-day evening supports genuine candidate edits without changing other days');

    await selectDay('day2');
    const secondCard = binding(session, 'day2', 'ActivityCard', 'am');
    const card = page.locator('.itinerary-column [data-component-id="' + secondCard.componentId + '"]'); await card.locator('h4').click();
    const beforePlace = await current(), placeRow = stored();
    const dayPlaces = new Set(beforePlace.tripState.days.day2.stops.map(s => s.placeId));
    const replacement = beforePlace.catalog.places.find(p => !dayPlaces.has(p.id)); assert.ok(replacement);
    const placePreview = await manual('placeId', secondCard.entityId, replacement.id);
    assert.equal(placePreview.document.entities.activities[secondCard.entityId].placeId, replacement.id); assert.equal(placePreview.document.entities.activities[secondCard.entityId].title, replacement.name);
    await unchanged(beforePlace, placeRow);
    session = await action('POST', '/editor/apply', () => page.getByTestId('apply-local-patch').click());
    await applied(beforePlace, session, ['/days/day2/stops/byId/' + secondCard.stopId + '/placeId'], 'Real catalog replacement');
    assert.equal(session.tripDocument.entities.activities[secondCard.entityId].title, replacement.name);
    assert.equal(session.componentRegistry[secondCard.componentId].entityId, secondCard.entityId);
    record('Manual Patch replaces a real catalog place; its title is server-derived and stable IDs are preserved');

    const placeBatchBefore = await current(), placeBatchRow = stored();
    await page.locator('.phase2-fields').getByRole('checkbox', { name: '景点安排', exact: true }).check();
    batch = await generated('换个景点'); assert.equal(batch.candidateCount, 3);
    const catalogIds = new Set(session.catalog.places.map(p => p.id));
    assert.equal(new Set(batch.candidates.map(c => c.document.entities.activities[secondCard.entityId].placeId)).size, 3);
    for (const candidate of batch.candidates) {
      assert.ok(candidate.patch.every(op => op.path === pathFor(secondCard.entityId, 'placeId')));
      assert.ok(candidate.patch.every(op => catalogIds.has(op.value)));
    }
    for (const letter of ['A', 'B', 'C']) { await page.getByTestId('phase2-candidate-' + letter).click(); await unchanged(placeBatchBefore, placeBatchRow); }
    await screenshot('real-place-candidates.png', 'phase2-candidates');
    const b = batch.candidates.find(c => c.strategy === 'efficiency_first');
    await page.getByTestId('phase2-candidate-B').click(); session = await action('POST', '/edits/commit', () => page.getByTestId('commit-edit-candidate').click());
    await applied(placeBatchBefore, session, ['/days/day2/stops/byId/' + secondCard.stopId + '/placeId'], 'Only real-place candidate B committed');
    const versionRecord = await current('/versions/' + session.version);
    assert.equal(versionRecord.candidateId, b.candidateId); assert.deepEqual(versionRecord.patch, b.patch); assert.equal(versionRecord.validationReport.valid, true);
    assert.deepEqual(session.tripDocument, { ...b.document, version: session.version });
    record('Natural-language place replacement produces three distinct real POIs; candidate B alone enters persistent history');

    await reject('A direct title patch cannot impersonate a server-derived place title', 'post', '/editor/preview', { baseVersion: session.version, selectedComponentId: secondCard.componentId, fields: ['placeId'], patch: [{ op: 'replace', path: pathFor(secondCard.entityId, 'placeId'), value: replacement.id }, { op: 'replace', path: pathFor(secondCard.entityId, 'title'), value: 'forged title' }] }, 403);
    await reject('Unknown POI is rejected', 'post', '/editor/preview', { baseVersion: session.version, selectedComponentId: secondCard.componentId, fields: ['placeId'], patch: [{ op: 'replace', path: pathFor(secondCard.entityId, 'placeId'), value: 'not-a-real-catalog-id' }] }, 400);
    session = await api('patch', '/editor/locks', { baseVersion: session.version, componentId: secondCard.componentId, locked: true });
    await reject('Locked real place cannot be replaced', 'post', '/editor/preview', { baseVersion: session.version, selectedComponentId: secondCard.componentId, fields: ['placeId'], patch: [{ op: 'replace', path: pathFor(secondCard.entityId, 'placeId'), value: replacement.id }] }, 423);
    await reject('Old version cannot override generalized edits', 'post', '/edits/commit', { baseVersion: placeBatchBefore.version, candidateId: b.candidateId }, 409);
    await reload(); const saved = await current(); assert.equal(saved.version, session.version); assert.deepEqual(saved.tripState, session.tripState); assert.deepEqual(saved.persistedConstraints, session.persistedConstraints); assert.deepEqual(Object.keys(saved.componentRegistry).sort(), idsAtStart);
    record('Unknown POIs, object locks and stale versions remain enforced; reload preserves generalized edits and constraints');

    // Keep map regression independent of the locked activity and without real keys.
    await selectDay('day3');
    const beforeMapPrompt = await current(), beforeMapPromptRow = stored();
    await page.getByTestId('refresh-day-routes').click();
    const mapSettings = page.getByRole('dialog', { name: '服务接入', exact: true }); await mapSettings.waitFor();
    assert.ok(await mapSettings.getByTestId('amap-integration-form').evaluate(node => node.classList.contains('is-target')));
    await unchanged(beforeMapPrompt, beforeMapPromptRow);
    await mapSettings.getByRole('button', { name: '关闭服务接入', exact: true }).click();
    await page.locator('a.nav-link').click(); await page.getByRole('button', { name: '操作记录', exact: true }).click(); await page.getByTestId('version-history').waitFor(); await page.getByRole('button', { name: '关闭操作记录', exact: true }).click();
    assert.equal(await page.locator('iframe').count(), 0); const metrics = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: innerWidth })); assert.ok(metrics.width <= metrics.viewport + 1);
    await page.setViewportSize({ width: 390, height: 844 }); await page.getByRole('button', { name: '查看操作记录', exact: true }).click(); await page.getByTestId('version-history').waitFor(); while (await page.getByRole('button', { name: '关闭提示', exact: true }).count()) await page.getByRole('button', { name: '关闭提示', exact: true }).first().click(); await page.screenshot({ path: path.join(shots, 'mobile-history.png'), animations: 'disabled' });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); assert.deepEqual(errors, []); assert.deepEqual(external, []);
    record('Missing map API opens service settings without mutation; navigation and persistent history remain usable on desktop and mobile');
    fs.writeFileSync(path.join(reports, suite+'-browser.json'), JSON.stringify({ passed: true, version: live.version, serverRevision: live.serverRevision, runId, tripId, baseURL: base, databaseIsolation: path.relative(root, dataDir).replace(/\\/g, '/'), realModel: false, realMap: false, browserErrors: errors, externalRequests: external, results, changes, rejections, screenshots: ['first-morning-candidates.png', 'third-evening-candidates.png', 'real-place-candidates.png', 'mobile-history.png'].map(n => 'docs/screenshots/edit-scope/' + n) }, null, 2));
    console.log('RESULT ' + JSON.stringify({ passed: true, checks: results.length, browserErrors: errors, externalRequests: external }));
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(shots, 'failure.png') }).catch(() => {});
    fs.writeFileSync(path.join(reports, suite+'-browser-failure.json'), JSON.stringify({ passed: false, runId, tripId, results, changes, rejections, browserErrors: errors, externalRequests: external, error: error.stack }, null, 2)); console.error(error.stack); process.exitCode = 1;
  } finally { if (browser) await browser.close(); await stopServer().catch(error => { console.error(error.stack); process.exitCode = 1; }); fs.closeSync(logFd); }
})();
