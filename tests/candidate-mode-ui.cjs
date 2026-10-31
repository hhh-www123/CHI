// Candidate checkbox contract against the isolated tests/fake_provider.py.
// No real LLM or map service is used; keep this separate from a quality trial.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { openLegacyTrip, resumeSavedTrip } = require('./browser-session.cjs');
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(process.env.PLAYWRIGHT_MODULE_DIR || path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8001';
const artifacts = process.env.UI_TEST_ARTIFACT_DIR || path.join(__dirname, 'artifacts');
const executablePath = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find(value => fs.existsSync(value));

(async () => {
  fs.mkdirSync(artifacts, { recursive: true });
  const browser = await playwright.chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN', reducedMotion: 'reduce' });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  const errors = [], results = [], generationRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/generate')) generationRequests.push(request.postDataJSON());
  });
  const toggle = page.getByRole('checkbox', { name: '是否生成候选', exact: true });
  const read = async () => {
    const id = await page.evaluate(() => localStorage.getItem('travel-genui-session-v1'));
    const response = await context.request.get(`${base}/api/sessions/${id}`);
    assert.equal(response.status(), 200);
    return response.json();
  };
  const action = async (method, suffix, callback) => {
    const responsePromise = page.waitForResponse(response => response.request().method() === method && new URL(response.url()).pathname.endsWith(suffix));
    await callback();
    const response = await responsePromise;
    const body = await response.json();
    assert.equal(response.status(), 200, JSON.stringify(body));
    return body;
  };
  let requestGate = null;
  await page.route('**/api/sessions/*/generate', async route => {
    const gate = requestGate;
    requestGate = null;
    if (gate) await gate;
    await route.continue();
  });
  const generate = async (text, expectedMode, hold = false) => {
    await page.getByLabel('描述本次修改', { exact: true }).fill(text);
    let release;
    if (hold) requestGate = new Promise(resolve => { release = resolve; });
    const requestPromise = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/generate'));
    const responsePromise = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith('/generate'));
    try {
      await page.getByRole('button', { name: '预览修改', exact: true }).click();
      const request = await requestPromise;
      assert.equal(request.postDataJSON().generateCandidates, expectedMode);
      if (hold) assert.equal(await toggle.isDisabled(), true, 'Mode is frozen while generation is in flight');
    } finally { release?.(); }
    const response = await responsePromise;
    const body = await response.json();
    assert.equal(response.status(), 200, JSON.stringify(body));
    assert.equal(body.generateCandidates, expectedMode);
    assert.equal(body.candidateCount, body.candidates.length);
    assert.equal(body.minimumCandidates, expectedMode ? 3 : 1);
    await page.getByTestId('candidate-comparison').waitFor();
    return body;
  };
  const assertNoOverflow = async () => {
    const size = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth }));
    assert.ok(size.content <= size.width + 1, JSON.stringify(size));
  };
  try {
    await openLegacyTrip(page, context, base);
    await page.getByRole('tabpanel').waitFor();
    assert.equal(await toggle.isChecked(), false, 'Candidates are opt-in by default');
    const placement = await page.evaluate(() => {
      const text = document.querySelector('#instruction');
      const checkbox = document.querySelector('[data-testid="generate-candidates-toggle"]');
      return { textBottom: text.getBoundingClientRect().bottom, checkboxTop: checkbox.getBoundingClientRect().top };
    });
    assert.ok(placement.checkboxTop >= placement.textBottom, 'Checkbox must sit below the idea input');
    const before = await read();
    const instruction = '只为第二天增加交通方式和地图，保留景点、时间、备注和其他日期';
    const single = await generate(instruction, false);
    assert.equal(single.candidates.length, 1);
    assert.equal(await page.getByTestId('candidate-option').count(), 1);
    assert.deepEqual(await read(), before);
    results.push('Unchecked checkbox sends false and returns exactly one validated read-only preview');

    await toggle.check();
    assert.equal(await page.getByRole('button', { name: '应用这次修改', exact: true }).count(), 0, 'Changing mode discards the old preview');
    assert.equal(await page.getByTestId('candidate-preview-banner').count(), 0);
    assert.deepEqual(await read(), before);
    const multiple = await generate(instruction, true, true);
    assert.ok(multiple.candidates.length >= 3);
    assert.equal(new Set(multiple.candidates.map(candidate => candidate.proposalId)).size, multiple.candidates.length);
    assert.equal(new Set(multiple.candidates.map(candidate => JSON.stringify(candidate.updates))).size, multiple.candidates.length, 'Displayed plans must differ in executed output');
    assert.equal(await page.getByTestId('candidate-option').count(), multiple.candidates.length);
    assert.deepEqual(await read(), before);
    results.push('Checked checkbox sends true, freezes during generation and returns at least three distinct valid previews');

    const chosen = multiple.candidates[2];
    await page.getByTestId('candidate-option').nth(2).click();
    assert.equal(await page.locator('[data-preview="true"]').getAttribute('data-candidate-id'), chosen.proposalId);
    assert.deepEqual(await read(), before, 'Selecting candidate three cannot write state');
    const applied = await action('POST', '/apply', () => page.getByRole('button', { name: '应用这次修改', exact: true }).click());
    assert.deepEqual(applied.tripState, before.tripState);
    assert.deepEqual(applied.uiSpec.regions['day-2'], chosen.updates[0].nextUI);
    for (const region of ['day-1', 'day-3']) assert.deepEqual(applied.uiSpec.regions[region], before.uiSpec.regions[region]);
    const undone = await action('POST', '/undo', () => page.getByRole('button', { name: '撤销上一步', exact: true }).click());
    for (const field of ['tripState', 'uiSpec', 'routeData', 'controlState']) assert.deepEqual(undone[field], before[field]);
    results.push('The third choice applies only its requested region; undo restores the complete prior state');

    await generate(instruction, true);
    await toggle.uncheck();
    assert.equal(await page.getByRole('button', { name: '应用这次修改', exact: true }).count(), 0);
    const singleAgain = await generate(instruction, false);
    assert.equal(singleAgain.candidates.length, 1);
    assert.equal(await page.getByTestId('candidate-option').count(), 1);
    assert.deepEqual((await read()).tripState, before.tripState);
    results.push('Switching back to one proposal discards multi-mode candidates and sends false again');
    await toggle.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(artifacts, 'candidate-mode-desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    await toggle.scrollIntoViewIfNeeded();
    await assertNoOverflow();
    await page.screenshot({ path: path.join(artifacts, 'candidate-mode-mobile.png') });
    assert.deepEqual(errors, []);
    results.push('Checkbox and help remain usable at 390px without horizontal overflow or browser errors');
    const report = { passed: true, fixture: true, realModel: false, realMap: false, results, generationModes: generationRequests.map(request => request.generateCandidates), browserErrors: errors };
    fs.writeFileSync(path.join(artifacts, 'candidate-mode-results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } catch (error) {
    await page.screenshot({ path: path.join(artifacts, 'candidate-mode-failure.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally { await context.close(); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });