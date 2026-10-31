// Isolated two-phase model fixture. No real model or map service is called.
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
  const errors = [], results = [];
  page.on('pageerror', error => errors.push(error.message));
  const read = async () => {
    const id = await page.evaluate(() => localStorage.getItem('travel-genui-session-v1'));
    const response = await context.request.get(`${base}/api/sessions/${id}`);
    assert.equal(response.status(), 200);
    return response.json();
  };
  const action = async (method, suffix, callback, expected = 200) => {
    const [response] = await Promise.all([page.waitForResponse(response => response.request().method() === method && new URL(response.url()).pathname.endsWith(suffix)), callback()]);
    const body = await response.json();
    assert.equal(response.status(), expected, JSON.stringify(body));
    return body;
  };
  const generate = async (text, generateCandidates = true) => {
    await page.getByRole('checkbox', { name: '是否生成候选', exact: true }).setChecked(generateCandidates);
    await page.getByLabel('描述本次修改', { exact: true }).fill(text);
    return action('POST', '/generate', () => page.getByRole('button', { name: '预览修改', exact: true }).click());
  };
  const mobileWidth = async () => {
    await page.evaluate(async () => { await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    const sizes = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
    assert.ok(sizes.content <= sizes.viewport + 1, JSON.stringify(sizes));
  };
  try {
    await openLegacyTrip(page, context, base);
    await page.getByRole('tabpanel').waitFor();
    const note = page.getByLabel('第二天行程备注', { exact: true });
    await note.fill('午餐安排保持不变，第二天结束后回酒店。');
    await action('PATCH', '/direct', () => note.press('Tab'));
    const before = await read();
    const batch = await generate('在每天游玩的景点间添加交通方式与地图，保留景点、时间、备注、费用和其他界面设置');
    assert.equal(batch.candidates.length, 3);
    assert.equal(new Set(batch.candidates.map(item => item.proposalId)).size, 3);
    assert.equal(await page.getByTestId('candidate-option-1').count(), 1);
    await page.getByTestId('candidate-preview-banner').waitFor();
    assert.deepEqual(await read(), before, 'Generation and initial preview must not mutate the saved session');
    const candidateRegion = page.locator('[data-preview="true"]');
    assert.equal(await candidateRegion.getAttribute('data-candidate-id'), batch.candidates[0].proposalId);
    assert.equal(await candidateRegion.locator('input:not([disabled]),select:not([disabled]),textarea:not([disabled])').count(), 0, 'Candidate must be read-only');
    await page.getByTestId('candidate-option').nth(1).click();
    assert.equal(await candidateRegion.getAttribute('data-candidate-id'), batch.candidates[1].proposalId);
    const mapAboveStops = await candidateRegion.evaluate(element => {
      const map = element.querySelector('.route-map-section');
      const stop = element.querySelector('.stops-list');
      if (!map || !stop) return { available: false, classes: [...element.querySelectorAll('article')].map(item => item.className) };
      return { available: true, before: !!(map.compareDocumentPosition(stop) & Node.DOCUMENT_POSITION_FOLLOWING) };
    });
    assert.ok(mapAboveStops.available && mapAboveStops.before, JSON.stringify(mapAboveStops));
    await page.getByRole('tab', { name: /第一天/ }).click();
    await page.getByRole('tab', { name: /第二天/ }).click();
    assert.equal(await page.getByRole('button', { name: '应用这次修改', exact: true }).isDisabled(), false, 'Browsing preview dates must not invalidate a candidate');
    assert.ok((await page.locator('.preview-day-note').textContent()).includes(before.tripState.days.day2.note));
    await page.getByTestId('candidate-option').nth(2).click();
    await page.getByTestId('transport-summary').waitFor();
    assert.equal(await candidateRegion.getAttribute('data-candidate-id'), batch.candidates[2].proposalId);
    assert.deepEqual(await read(), before, 'Switching alternatives must not write any session state');
    results.push('Three distinct real UI previews; date navigation and candidate switching are read-only');
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: path.join(artifacts, 'candidates-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await mobileWidth();
    await page.screenshot({ path: path.join(artifacts, 'candidates-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(await page.locator('[data-preview="true"]').count(), 0);
    assert.deepEqual(await read(), before);
    assert.equal(await page.getByLabel('第二天行程备注', { exact: true }).isDisabled(), false);
    results.push('Cancel restores current UI and original date; responsive preview has no overflow');

    const secondBatch = await generate('在每天游玩的景点间添加交通方式与地图，保留景点、时间、备注、费用和其他界面设置');
    await page.getByTestId('candidate-option').nth(1).click();
    const applied = await action('POST', '/apply', () => page.getByRole('button', { name: '应用这次修改', exact: true }).click());
    assert.deepEqual(applied.tripState, before.tripState);
    assert.deepEqual(applied.controlState, before.controlState);
    for (const region of ['day-1', 'day-2', 'day-3']) {
      assert.equal(applied.uiSpec.regions[region].showMap, true);
      assert.equal(applied.uiSpec.regions[region].showTransport, true);
      assert.equal(applied.uiSpec.regions[region].mapPlacement, 'beforeStops');
      for (const field of ['view', 'showCost', 'compact', 'showAlternatives']) assert.equal(applied.uiSpec.regions[region][field], before.uiSpec.regions[region][field]);
    }
    assert.equal(await page.locator('[data-preview="true"]').count(), 0);
    const stale = await context.request.post(`${base}/api/sessions/${before.id}/apply`, { data: { baseVersion: applied.version, proposalId: secondBatch.candidates[0].proposalId } });
    assert.ok([404, 409].includes(stale.status()));
    const undone = await action('POST', '/undo', () => page.getByRole('button', { name: '撤销上一步', exact: true }).click());
    assert.deepEqual(undone.tripState, before.tripState);
    assert.deepEqual(undone.uiSpec, before.uiSpec);
    assert.deepEqual(undone.routeData, before.routeData);
    results.push('Chosen candidate applies once, protects unrelated fields, invalidates siblings, and undoes atomically');

    const fixed = await generate('仅将第二天改成时间轴，保留其他内容', false);
    assert.equal(fixed.candidates.length, 1, 'Do not invent differences for an exact fixed request');
    assert.equal(await page.getByTestId('candidate-option-2').count(), 0);
    await page.getByLabel('描述本次修改', { exact: true }).fill('第二天增加清单');
    assert.equal(await page.getByRole('button', { name: '应用这次修改', exact: true }).isDisabled(), true);
    const unsupportedText = '帮我支付门票';
    await page.getByLabel('描述本次修改', { exact: true }).fill(unsupportedText);
    await action('POST', '/generate', () => page.getByRole('button', { name: '预览修改', exact: true }).click(), 400);
    assert.equal(await page.getByRole('button', { name: '应用这次修改', exact: true }).count(), 0);
    assert.equal(await page.locator('[data-preview="true"]').count(), 0);
    assert.deepEqual((await read()).tripState, before.tripState);
    results.push('Single valid candidate is honest; edits or failed regeneration cannot apply an old candidate');

    const stranger = await browser.newContext();
    try {
      const denied = await stranger.request.get(`${base}/api/sessions/${before.id}/proposals/${batch.candidates[0].proposalId}/map/day-1?v=${before.version}`);
      assert.equal(denied.status(), 404);
    } finally { await stranger.close(); }
    assert.deepEqual(errors, []);
    const report = { passed: true, fixture: true, realModel: false, realMap: false, results, browserErrors: errors };
    fs.writeFileSync(path.join(artifacts, 'candidates-results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } catch (error) {
    await page.screenshot({ path: path.join(artifacts, 'candidates-failure.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally { await context.close(); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
