const previewResponse=require('./preview-response.cjs');
// Phase 0 + Phase 1 regression; only run against the explicitly isolated demo server.
// UI_BASE_URL defaults to 8013. PHASE1_TEST_DATA_DIR must point to this copy's
// .runtime/phase1-browser directory. No real LLM or map request is allowed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
let playwright;
try { playwright = require('playwright'); }
catch {
  playwright = require(process.env.PLAYWRIGHT_MODULE_DIR || path.join(
    process.env.USERPROFILE || process.env.HOME || '', '.cache', 'codex-runtimes',
    'codex-primary-runtime', 'dependencies', 'node', 'node_modules', 'playwright',
  ));
}
const root = path.resolve(__dirname, '..');
const artifactPrefix = process.env.UI_ARTIFACT_PREFIX || 'phase1';
assert.match(artifactPrefix, /^[a-z0-9-]{1,64}$/, 'Artifact prefix must be a simple local filename prefix');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8013';
const dataDir = path.resolve(process.env.PHASE1_TEST_DATA_DIR || path.join(root, '.runtime', 'phase1-browser'));
assert.equal(new URL(base).hostname, '127.0.0.1', 'Use the local isolated test server');
assert.equal(new URL(base).port, '8013', 'Never point this regression at a live user service');
assert.equal(dataDir, path.join(root, '.runtime', 'phase1-browser'), 'Database fixture path must be the isolated browser-test directory');
const database = path.join(dataDir, 'travel.sqlite3');
const python = process.env.UI_PYTHON_PATH || path.join(root, '.venv', 'Scripts', 'python.exe');
const shots = path.join(root, 'docs', 'screenshots');
const reportDir = path.join(root, 'docs', 'verification');
fs.mkdirSync(shots, { recursive: true });
fs.mkdirSync(reportDir, { recursive: true });
const executablePath = [
  process.env.UI_BROWSER_PATH,
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
].filter(Boolean).find(value => fs.existsSync(value));
const STORAGE_KEY = 'travel-genui-phase1-session-v1';
function fixturePython(source, args) {
  const output = spawnSync(python, ['-X', 'utf8', '-c', source, database, ...args], { cwd: root, encoding: 'utf8' });
  assert.equal(output.status, 0, output.stderr);
  return output.stdout.trim();
}
const readStored = id => fixturePython(
  'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); r=c.execute("SELECT document FROM sessions WHERE id=?",(sys.argv[2],)).fetchone(); assert r; print(r[0]); c.close()',
  [id],
);
const injectLegacyFixture = id => fixturePython(
  'import sqlite3,sys,json; c=sqlite3.connect(sys.argv[1]); r=c.execute("SELECT document FROM sessions WHERE id=?",(sys.argv[2],)).fetchone(); d=json.loads(r[0]); d["uiSpec"]["regions"]["day-1"]["surface"]={"title":"Untrusted legacy fixture","html":"<button id=legacy-code-button>Legacy executable UI</button>","css":"body{background:red}","javascript":"window.__phase1LegacyExecuted=true; parent.postMessage({type:\\"unsafe\\"},\\"*\\")","placement":"replaceStops"}; c.execute("UPDATE sessions SET document=? WHERE id=?",(json.dumps(d,ensure_ascii=False),sys.argv[2])); c.commit(); c.close(); print("isolated-fixture-only")',
  [id],
);
(async () => {
  const browser = await playwright.chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', reducedMotion: 'reduce' });
  const page = await context.newPage();
  page.setDefaultTimeout(18000);
  const browserErrors = [];
  const results = [];
  let tripId = '';
  const record = name => { results.push({ name, passed: true }); console.log('PASS ' + name); };
  page.on('pageerror', error => browserErrors.push(error.message));
  async function current() {
    assert.ok(tripId);
    const response = await context.request.get(base + '/api/sessions/' + tripId);
    assert.equal(response.status(), 200);
    return response.json();
  }
  async function action(method, suffix, callback, expectedStatus = 200) {
    const responsePromise = page.waitForResponse(response => response.request().method() === method && new URL(response.url()).pathname.endsWith(suffix));
    await callback();
    const response = await responsePromise;
    const body = await previewResponse(response);
    assert.equal(response.status(), expectedStatus, JSON.stringify(body));
    await page.waitForTimeout(100);
    return body;
  }
  async function maliciousPreview(body, expectedStatus = [400, 423]) {
    const response = await context.request.post(base + '/api/sessions/' + tripId + '/editor/preview', { data: body });
    const result = await response.json();
    assert.ok((Array.isArray(expectedStatus) ? expectedStatus : [expectedStatus]).includes(response.status()), JSON.stringify(result));
    return result;
  }
  const selection = () => page.locator('.local-editor-panel');
  const entityPath = (activityId, field) => '/entities/activities/' + activityId + '/' + field;
  const metrics = () => page.evaluate(() => ({
    left: document.querySelector('.itinerary-column').scrollTop,
    right: document.querySelector('.control-panel').scrollTop,
    window: window.scrollY,
    width: document.documentElement.scrollWidth,
    viewport: innerWidth,
  }));
  try {
    const status = await (await context.request.get(base + '/api/agent/status')).json();
    const health = await (await context.request.get(base + '/api/health')).json();
    assert.equal(status.mode, 'demo', 'Server must explicitly use demo mode');
    assert.equal(status.mapConfigured, false, 'No live map key is permitted');
    assert.equal(status.runtimeCodeExecution, false);
    assert.ok(['Phase 0 + Phase 1', 'Phase 2', 'Phase 3', 'Phase 4'].includes(status.phase), 'Server must retain the Phase 1 capability boundary');
    assert.ok(fs.existsSync(database), 'Server must create the isolated SQLite fixture before running');
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.getByTestId('trip-setup').waitFor();
    assert.equal(await page.getByRole('tabpanel').count(), 0);
    assert.equal(await page.locator('#setup-city').inputValue(), '');
    let session = await action('POST', '/api/demo-sessions', () => page.getByTestId('open-three-day-demo').click());
    tripId = session.id;
    await page.getByRole('tabpanel').waitFor();
    assert.equal(await page.getByRole('tab').count(), 3);
    assert.equal(await page.locator('.stops-list .stop-card').count(), 3);
    assert.equal(await page.locator('iframe').count(), 0);
    const originalRegistry = session.componentRegistry;
    const registry = Object.values(originalRegistry);
    const dayBinding = key => registry.find(item => item.dayKey === key && !item.stopId && !item.period);
    const pmBinding = registry.find(item => item.dayKey === 'day2' && item.period === 'pm' && !item.stopId);
    const pmCards = registry.filter(item => item.dayKey === 'day2' && item.period === 'pm' && item.stopId);
    assert.ok(pmBinding && pmCards.length === 2);
    record('Explicit fixed three-day demo opens trusted original cards, without model generation');

    await page.getByTestId('edit-mode-toggle').click();
    assert.equal(await page.getByTestId('edit-mode-toggle').getAttribute('aria-pressed'), 'true');
    const dayPanel = page.getByRole('tabpanel');
    await dayPanel.focus();
    await page.keyboard.press('Enter');
    assert.equal(await dayPanel.getAttribute('data-editor-selected'), 'true');
    const firstCard = page.locator('.stops-list .stop-card').first();
    await firstCard.focus();
    await page.keyboard.press(' ');
    assert.equal(await firstCard.getAttribute('data-editor-selected'), 'true');
    const selectedBeforeInput = await firstCard.getAttribute('data-component-id');
    await firstCard.locator('input[type=time]').focus();
    assert.equal(await firstCard.getAttribute('data-editor-selected'), 'true');
    assert.equal(await firstCard.getAttribute('data-component-id'), selectedBeforeInput);
    record('Edit mode supports keyboard selection and preserves existing interactive inputs');

    session = await action('PATCH', '/editor/locks', () => selection().getByRole('button', { name: '锁定' + dayBinding('day1').label, exact: true }).click());
    session = await action('PATCH', '/editor/locks', () => selection().getByRole('button', { name: '锁定' + dayBinding('day3').label, exact: true }).click());
    assert.ok(session.persistedConstraints.lockedComponentIds.includes(dayBinding('day1').componentId));
    assert.ok(session.persistedConstraints.lockedComponentIds.includes(dayBinding('day3').componentId));
    await page.getByRole('tab', { name: /第二天/ }).click();
    await page.getByTestId('editor-period-pm').click();
    await page.locator('#editor-value').waitFor();
    assert.equal(await page.getByTestId('editor-period-pm').getAttribute('data-editor-selected'), 'true');
    assert.equal(await page.locator('[data-editor-in-scope=true]').count(), 2);
    const baseline = await current();
    const originalStored = readStored(tripId);
    const scopeResponse = await context.request.post(base + '/api/sessions/' + tripId + '/editor/scope', {
      data: { baseVersion: baseline.version, selectedComponentId: pmBinding.componentId, fields: ['description'] },
    });
    assert.equal(scopeResponse.status(), 200);
    const scope = await scopeResponse.json();
    assert.equal(scope.allowedPaths.length, 2);
    assert.ok(scope.allowedPaths.every(item => pmCards.some(card => item === entityPath(card.entityId, 'description'))));
    record('Day 1 and day 3 locks persist; day 2 afternoon has a server-derived field whitelist and highlight');

    const activityId = await page.locator('#editor-activity').inputValue();
    const targetBinding = pmCards.find(item => item.entityId === activityId);
    assert.ok(targetBinding);
    await selection().getByRole('button', { name: '填入固定演示文本', exact: true }).click();
    const description = await page.locator('#editor-value').inputValue();
    const preview = await action('POST', '/editor/preview', () => page.getByTestId('validate-local-patch').click());
    await page.getByTestId('local-patch-preview').waitFor();
    assert.equal(preview.generationMode, 'mock');
    assert.equal(preview.document.entities.activities[activityId].description, description);
    assert.deepEqual(preview.changedPaths, [entityPath(activityId, 'description')]);
    assert.deepEqual(await current(), baseline, 'Preview must not modify any public state');
    assert.equal(readStored(tripId), originalStored, 'Preview must not change the SQLite session record');
    assert.notEqual(await page.locator('[data-stop-id="' + targetBinding.stopId + '"] .stop-description').innerText(), description);
    while (await page.getByRole('button', { name: '关闭提示', exact: true }).count()) await page.getByRole('button', { name: '关闭提示', exact: true }).first().click();
    await page.locator('[data-stop-id="' + targetBinding.stopId + '"]').scrollIntoViewIfNeeded();
    await page.getByTestId('apply-local-patch').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(shots, artifactPrefix + '-preview.png') });
    session = await action('POST', '/editor/apply', () => page.getByTestId('apply-local-patch').click());
    const expectedDays = structuredClone(baseline.tripState.days);
    expectedDays.day2.stops.find(item => item.id === targetBinding.stopId).description = description;
    assert.deepEqual(session.tripState.days, expectedDays);
    assert.equal(session.version, baseline.version + 1);
    assert.deepEqual(session.tripState.days.day1, baseline.tripState.days.day1);
    assert.deepEqual(session.tripState.days.day3, baseline.tripState.days.day3);
    assert.deepEqual(session.persistedConstraints, baseline.persistedConstraints);
    assert.equal(await page.locator('[data-stop-id="' + targetBinding.stopId + '"] .stop-description').innerText(), description);
    record('Fixed Patch preview leaves GET and SQLite unchanged; explicit apply changes only the selected description');

    // Scope and lock attempts are API-level adversarial checks against the same user-owned test trip.
    const outsideBefore = await current();
    const outsideStored = readStored(tripId);
    const day1Card = registry.find(item => item.dayKey === 'day1' && item.stopId);
    const bad = await maliciousPreview({
      baseVersion: outsideBefore.version, selectedComponentId: pmBinding.componentId, fields: ['description'],
      patch: [
        { op: 'replace', path: entityPath(activityId, 'description'), value: 'must not be partially applied' },
        { op: 'replace', path: entityPath(day1Card.entityId, 'description'), value: 'unauthorized' },
      ],
    });
    assert.ok(bad.detail);
    assert.deepEqual(await current(), outsideBefore);
    assert.equal(readStored(tripId), outsideStored);
    record('Mixed authorized and unauthorized Patch is rejected atomically; protected days remain byte-identical');

    session = await action('PATCH', '/editor/locks', () => selection().getByRole('button', { name: '锁定此组件', exact: true }).click());
    assert.ok(session.persistedConstraints.lockedComponentIds.includes(pmBinding.componentId));
    assert.equal(await page.locator('[data-stop-id="' + targetBinding.stopId + '"] input[type=time]').isDisabled(), true);
    const lockedBefore = await current();
    const lockedStored = readStored(tripId);
    await maliciousPreview({
      baseVersion: lockedBefore.version, selectedComponentId: pmBinding.componentId, fields: ['description'],
      patch: [{ op: 'replace', path: entityPath(activityId, 'description'), value: 'blocked by period lock' }],
    });
    assert.deepEqual(await current(), lockedBefore);
    assert.equal(readStored(tripId), lockedStored);
    session = await action('PATCH', '/editor/locks', () => selection().getByRole('button', { name: '解除本组件锁定', exact: true }).click());
    const targetCard = page.locator('[data-stop-id="' + targetBinding.stopId + '"]');
    await targetCard.locator('h4').click();
    assert.equal(await targetCard.getAttribute('data-editor-selected'), 'true');
    session = await action('PATCH', '/editor/locks', () => selection().getByRole('button', { name: '锁定此组件', exact: true }).click());
    assert.equal(await targetCard.locator('input[type=time]').isDisabled(), true);
    assert.equal(await targetCard.locator('select').isDisabled(), true);
    session = await action('PATCH', '/editor/locks', () => selection().getByRole('button', { name: '解除本组件锁定', exact: true }).click());
    record('Period and activity locks have visible UI state, disable matching controls, and reject malicious writes');

    await page.getByTestId('editor-period-pm').click();
    await page.locator('#editor-field').selectOption('startTime');
    await page.locator('#editor-value').waitFor();
    await page.locator('#editor-activity').selectOption(activityId);
    await page.locator('#editor-value').fill('14:00');
    await action('POST', '/editor/preview', () => page.getByTestId('validate-local-patch').click());
    session = await action('POST', '/editor/apply', () => page.getByTestId('apply-local-patch').click());
    assert.equal(session.tripDocument.entities.activities[activityId].startTime, '14:00');
    const timeBaseline = await current();
    const timeStored = readStored(tripId);
    await page.locator('#editor-value').fill('17:00');
    await action('POST', '/editor/preview', () => page.getByTestId('validate-local-patch').click(), 400);
    assert.deepEqual(await current(), timeBaseline);
    assert.equal(readStored(tripId), timeStored);
    assert.equal(await page.getByTestId('local-patch-preview').count(), 0);
    record('Authorized time Patch applies; invalid time ordering is rejected without changing formal state');

    await page.getByTestId('browse-mode-toggle').click();
    const note = page.getByLabel('第二天行程备注', { exact: true });
    await note.fill('Phase 1 isolated regression note');
    session = await action('PATCH', '/direct', () => note.blur());
    assert.equal(session.tripState.days.day2.note, 'Phase 1 isolated regression note');
    const stop = session.tripState.days.day2.stops[0];
    const alt = page.getByTestId('alternative-' + stop.id);
    const otherPlaceId = await alt.locator('option').evaluateAll(options => options.map(item => item.value).find(value => value !== options[0].value));
    assert.ok(otherPlaceId);
    session = await action('PATCH', '/direct', () => alt.selectOption(otherPlaceId));
    assert.equal(session.tripState.days.day2.stops[0].placeId, otherPlaceId);
    session = await action('POST', '/editor/presentation', () => page.getByRole('button', { name: '预览时间轴展示', exact: true }).click());
    assert.equal(session.uiSpec.regions['day-2'].view, 'timeline');
    assert.ok(await page.locator('.stops-list.timeline').count());
    session = await action('POST', '/editor/presentation', () => page.getByRole('button', { name: '预览卡片展示', exact: true }).click());
    assert.equal(session.uiSpec.regions['day-2'].view, 'cards');
    record('Existing note saving, real catalog alternatives, and trusted cards/timeline controls remain usable');

    const beforeMapPrompt = structuredClone(session);
    const beforeMapPromptRow = readStored(tripId);
    await page.getByTestId('refresh-day-routes').click();
    const mapSettings = page.getByRole('dialog', { name: '服务接入', exact: true });
    await mapSettings.waitFor();
    assert.ok(await mapSettings.getByTestId('amap-integration-form').evaluate(node => node.classList.contains('is-target')));
    assert.match(await mapSettings.getByTestId('amap-integration-form').innerText(), /高德地图 API|未接入/);
    assert.deepEqual(await current(), beforeMapPrompt);
    assert.equal(readStored(tripId), beforeMapPromptRow);
    await mapSettings.getByRole('button', { name: '关闭服务接入', exact: true }).click();
    const sourceUrls = await page.locator('.place-source-link').evaluateAll(links => links.map(item => item.href));
    assert.ok(sourceUrls.length > 0 && sourceUrls.every(value => value.startsWith('https://')));
    record('Safe place links remain available; a no-key route click prompts for the high-de map API without mutating the trip');

    const idsBeforeUndo = Object.keys(session.componentRegistry).sort();
    const locksBeforeUndo = session.persistedConstraints;
    session = await action('POST', '/undo', () => page.getByRole('button', { name: '撤销上一步', exact: true }).click());
    assert.deepEqual(Object.keys(session.componentRegistry).sort(), idsBeforeUndo);
    assert.deepEqual(session.persistedConstraints, locksBeforeUndo);
    const savedVersion = session.version;
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '继续上次行程', exact: true }).click();
    await page.getByRole('tabpanel').waitFor();
    session = await current();
    assert.equal(session.version, savedVersion);
    assert.deepEqual(Object.keys(session.componentRegistry).sort(), idsBeforeUndo);
    assert.deepEqual(session.persistedConstraints, locksBeforeUndo);
    assert.equal(await page.getByTestId('edit-mode-toggle').getAttribute('aria-pressed'), 'false');
    record('Undo and refresh/continue preserve stable IDs, data and persisted locks; ephemeral selection resets');

    // A separate fixture-only session simulates an old stored generated surface.
    const legacyResponse = await context.request.post(base + '/api/sessions', { data: {} });
    assert.equal(legacyResponse.status(), 200);
    const legacySession = await legacyResponse.json();
    injectLegacyFixture(legacySession.id);
    await page.evaluate(({ key, id }) => localStorage.setItem(key, id), { key: STORAGE_KEY, id: legacySession.id });
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '继续上次行程', exact: true }).click();
    await page.getByRole('tabpanel').waitFor();
    await page.locator('.editor-legacy-notice').waitFor();
    assert.equal(await page.locator('iframe').count(), 0);
    assert.equal(await page.locator('#legacy-code-button').count(), 0);
    assert.equal(await page.evaluate(() => window.__phase1LegacyExecuted), undefined);
    assert.equal(await page.locator('.stops-list .stop-card').count(), 3);
    await page.evaluate(({ key, id }) => localStorage.setItem(key, id), { key: STORAGE_KEY, id: tripId });
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '继续上次行程', exact: true }).click();
    await page.getByRole('tab', { name: /第二天/ }).click();
    record('Stored untrusted HTML/JavaScript fixture stays inert and falls back to original trusted cards');

    await page.getByTestId('edit-mode-toggle').click();
    await page.getByTestId('editor-period-pm').click();
    await page.locator('#editor-value').waitFor();
    await page.screenshot({ path: path.join(shots, artifactPrefix + '-desktop.png') });
    await page.evaluate(() => { document.querySelector('.itinerary-column').scrollTop = 0; document.querySelector('.control-panel').scrollTop = 0; });
    const initialScroll = await metrics();
    assert.equal(initialScroll.window, 0);
    const right = await page.locator('.control-panel').boundingBox();
    await page.mouse.move(right.x + right.width - 20, right.y + right.height / 2);
    await page.mouse.wheel(0, 360); await page.waitForTimeout(180);
    const rightMoved = await metrics();
    assert.ok(rightMoved.right > 30);
    assert.equal(rightMoved.left, initialScroll.left);
    const left = await page.locator('.itinerary-column').boundingBox();
    await page.mouse.move(left.x + left.width - 20, left.y + left.height / 2);
    await page.mouse.wheel(0, 360); await page.waitForTimeout(180);
    const leftMoved = await metrics();
    assert.ok(leftMoved.left > 30);
    assert.equal(leftMoved.right, rightMoved.right);
    assert.equal(leftMoved.window, 0);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(250);
    await page.evaluate(() => { document.querySelector('.itinerary-column').scrollTop = 0; document.querySelector('.control-panel').scrollTop = 0; });
    await page.getByTestId('local-editor-panel').evaluate(node => window.scrollTo(0, node.getBoundingClientRect().top + window.scrollY));
    await page.waitForTimeout(150);
    const mobile = await metrics();
    assert.ok(mobile.width <= mobile.viewport + 1, JSON.stringify(mobile));
    await page.screenshot({ path: path.join(shots, artifactPrefix + '-mobile.png') });
    assert.equal(await page.locator('iframe').count(), 0);
    record('Desktop panes scroll independently; 390px mobile retains editor controls without horizontal overflow');
    assert.deepEqual(browserErrors, []);
    const report = {
      passed: true, scope: 'Phase 1 inherited regression', phase: status.phase, version: health.version, baseURL: base,
      generationMode: 'mock', realModel: false, realMap: false,
      databaseIsolation: '.runtime/phase1-browser/travel.sqlite3',
      browserErrors, results, screenshots: [artifactPrefix + '-desktop.png', artifactPrefix + '-preview.png', artifactPrefix + '-mobile.png'],
      fixtures: ['fresh per-run browser context and sessions', 'legacy surface seeded only in isolated SQLite test session'],
    };
    fs.writeFileSync(path.join(reportDir, artifactPrefix + '-browser.json'), JSON.stringify(report, null, 2));
    const failureShot = path.join(shots, artifactPrefix + '-failure.png');
    if (fs.existsSync(failureShot)) fs.unlinkSync(failureShot);
    console.log('RESULT ' + JSON.stringify({ passed: true, checks: results.length, browserErrors }));
  } catch (error) {
    await page.screenshot({ path: path.join(shots, artifactPrefix + '-failure.png') }).catch(() => {});
    fs.writeFileSync(path.join(reportDir, artifactPrefix + '-browser.json'), JSON.stringify({ passed: false, phase: 'Phase 0 + Phase 1', baseURL: base, realModel: false, realMap: false, results, browserErrors, error: error.stack }, null, 2));
    console.error(error.stack); process.exitCode = 1;
  } finally { await context.close(); await browser.close(); }
})();
