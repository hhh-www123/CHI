// Isolated open generated UI verification. Requires tests/open_provider.py on 18081.
// Reports fixture:true; this cannot substitute for a real-model quality trial.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { openLegacyTrip, resumeSavedTrip } = require('./browser-session.cjs');
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(process.env.PLAYWRIGHT_MODULE_DIR || path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8003';
const artifacts = process.env.UI_TEST_ARTIFACT_DIR || path.join(__dirname, 'artifacts');
const executablePath = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find(value => fs.existsSync(value));

(async () => {
  fs.mkdirSync(artifacts, { recursive: true });
  const browser = await playwright.chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN', reducedMotion: 'reduce' });
  const page = await context.newPage();
  page.setDefaultTimeout(25000);
  const errors = [], results = [];
  page.on('pageerror', error => errors.push(error.message));
  const read = async () => {
    const id = await page.evaluate(() => localStorage.getItem('travel-genui-session-v1'));
    const response = await context.request.get(`${base}/api/sessions/${id}`);
    assert.equal(response.status(), 200);
    return response.json();
  };
  const idle = () => page.waitForFunction(() => { const input = document.querySelector('#instruction'); return input && !input.disabled; });
  const action = async (method, suffix, callback) => {
    const [response] = await Promise.all([page.waitForResponse(value => value.request().method() === method && new URL(value.url()).pathname.endsWith(suffix)), callback()]);
    const body = await response.json();
    assert.equal(response.status(), 200, JSON.stringify(body));
    await idle();
    return body;
  };
  const generate = async (text, generateCandidates = false) => {
    await idle();
    await page.getByRole('checkbox', { name: '是否生成候选', exact: true }).setChecked(generateCandidates);
    await page.getByLabel('描述本次修改', { exact: true }).fill(text);
    return action('POST', '/generate', () => page.getByRole('button', { name: '预览修改', exact: true }).click());
  };
  const apply = () => action('POST', '/apply', () => page.getByRole('button', { name: '应用这次修改', exact: true }).click());
  const frame = () => page.frameLocator('[data-testid="generated-surface-frame"]');
  const waitSearch = async () => { await frame().locator('#place-results li').first().waitFor(); };
  try {
    await openLegacyTrip(page, context, base);
    await page.getByRole('tabpanel').waitFor();
    const catalogResponse = await context.request.get(`${base}/api/catalog`);
    assert.equal(catalogResponse.status(), 200);
    const catalog = await catalogResponse.json();
    const before = await read();
    const original = await generate('只在第二天增加一个可搜索真实景点、可勾选准备清单的面板，保留原行程和其他日期', true);
    assert.equal(original.candidates.length, 3);
    await waitSearch();
    assert.equal(await frame().locator('#place-results li').count(), catalog.places.length);
    const probe = catalog.places.find(place => place.name.includes('西湖')) || catalog.places[0];
    await frame().getByLabel('搜索真实景点', { exact: true }).fill(probe.name);
    const names = await frame().locator('#place-results li').allTextContents();
    assert.ok(names.length > 0 && names.every(name => name.includes(probe.name)));
    await frame().getByLabel('已准备雨伞', { exact: true }).check();
    assert.equal(await frame().getByLabel('已准备雨伞', { exact: true }).isChecked(), true);
    assert.deepEqual(await read(), before, 'Search and checkbox preview cannot write saved session data');
    assert.ok(await page.locator('.stops-list').count(), 'Augmentation must preserve native itinerary');
    assert.equal(await page.getByTestId('generated-surface').getAttribute('data-placement'), 'afterStops');
    assert.equal(await page.getByTestId('generated-surface-frame').getAttribute('sandbox'), 'allow-scripts');
    const hostAccess = await frame().locator('body').evaluate(() => { try { return { blocked: false, text: parent.document.title }; } catch { return { blocked: true }; } });
    assert.equal(hostAccess.blocked, true, 'Generated UI cannot access the host DOM');
    results.push('Three open HTML/CSS/JS candidates run real catalog search and local checkbox preview inside an opaque sandbox');

    await page.getByTestId('candidate-option').nth(1).click();
    await waitSearch();
    await apply();
    await waitSearch();
    assert.equal(await frame().getByLabel('已准备雨伞', { exact: true }).isChecked(), false, 'Unapplied preview state is not persisted');
    const saved = await action('PATCH', '/direct', () => frame().getByLabel('已准备雨伞', { exact: true }).check());
    assert.equal(saved.uiSpec.regions['day-2'].surfaceState.umbrella, true);
    assert.deepEqual(saved.tripState, before.tripState);
    for (const region of ['day-1', 'day-3']) assert.deepEqual(saved.uiSpec.regions[region], before.uiSpec.regions[region]);
    await page.reload({ waitUntil: 'networkidle' });
      await resumeSavedTrip(page);
    await waitSearch();
    assert.equal(await frame().getByLabel('已准备雨伞', { exact: true }).isChecked(), true);
    results.push('Chosen code persists, checkbox state saves through the host, survives reload, and leaves trip data and other days unchanged');

    const stateBeforeTheme = await read();
    await generate('只把第二天这个搜索面板的主题改成蓝色，保留已勾选状态、已有搜索功能和所有行程数据');
    await waitSearch();
    assert.equal(await frame().getByLabel('已准备雨伞', { exact: true }).isChecked(), true);
    assert.equal(await frame().locator('.custom-panel').evaluate(element => getComputedStyle(element).getPropertyValue('--accent').trim()), '#2563eb');
    assert.deepEqual((await read()).uiSpec, stateBeforeTheme.uiSpec, 'Theme preview must not persist');
    const themed = await apply();
    await waitSearch();
    assert.equal(themed.uiSpec.regions['day-2'].surfaceState.umbrella, true);
    assert.deepEqual(themed.tripState, before.tripState);
    assert.deepEqual(themed.uiSpec.regions['day-1'], before.uiSpec.regions['day-1']);
    assert.deepEqual(themed.uiSpec.regions['day-3'], before.uiSpec.regions['day-3']);
    results.push('A second natural-language edit changes generated source while preserving existing interaction state and unrelated contents');

    const priorReplacement = await read();
    await generate('只把第二天景点展示重新排成可折叠面板，不改变景点时间备注与其他日期');
    await frame().locator('#accordion details').first().waitFor();
    assert.equal(await frame().locator('#accordion details').count(), before.tripState.days.day2.stops.length);
    assert.equal(await page.locator('.stops-list').count(), 0, 'Explicit replacement layout renders the generated itinerary');
    assert.equal(await page.getByTestId('generated-surface').getAttribute('data-placement'), 'replaceStops');
    const firstDetail = frame().locator('#accordion details').first();
    assert.equal(await firstDetail.getAttribute('open'), null);
    await firstDetail.locator('summary').click();
    assert.notEqual(await firstDetail.getAttribute('open'), null);
    assert.deepEqual((await read()).uiSpec, priorReplacement.uiSpec);
    await apply();
    const restored = await action('POST', '/undo', () => page.getByRole('button', { name: '撤销上一步', exact: true }).click());
    assert.deepEqual(restored.uiSpec, priorReplacement.uiSpec);
    await waitSearch();
    assert.equal(await frame().getByLabel('已准备雨伞', { exact: true }).isChecked(), true);
    results.push('Explicit full presentation replacement supports working accordion interactions; undo restores generated source and saved checkbox state');

    const beforeTrip = await read();
    const budgetBatch = await generate('只把总预算改为3000，三天行程和界面都不要修改');
    assert.deepEqual(budgetBatch.candidates[0].updates, []);
    assert.deepEqual(budgetBatch.candidates[0].nextTrip, { budgetLimit: 3000 });
    assert.equal((await page.locator('.preview-budget-limit').textContent()).trim(), '3000');
    assert.deepEqual(await read(), beforeTrip);
    const budgetSaved = await apply();
    assert.equal(budgetSaved.tripState.budgetLimit, 3000);
    assert.deepEqual(budgetSaved.uiSpec, beforeTrip.uiSpec);
    assert.deepEqual(budgetSaved.tripState.days, beforeTrip.tripState.days);
    results.push('Trip-only global budget preview changes no day permissions or UI and only applies the explicitly requested field');

    const beforeRepair = await read();
    const repairedResponse = page.waitForResponse(value => value.request().method() === 'POST' && new URL(value.url()).pathname.endsWith('/repair'));
    await generate('只在第二天添加可搜索清单，模拟运行错误用于回归，保留其他内容');
    const repair = await repairedResponse;
    const repaired = await repair.json();
    assert.equal(repair.status(), 200, JSON.stringify(repaired));
    await idle();
    await waitSearch();
    assert.equal(await page.getByTestId('generated-surface-error').count(), 0);
    assert.deepEqual(await read(), beforeRepair, 'Automatic code repair is still only a preview');
    results.push('A real browser runtime error returns to the Agent and repairs the candidate without writing itinerary state');
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: path.join(artifacts, 'open-ui-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(async () => { await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    const sizes = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
    assert.ok(sizes.content <= sizes.viewport + 1, JSON.stringify(sizes));
    await page.screenshot({ path: path.join(artifacts, 'open-ui-mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    const report = { passed: true, fixture: true, realModel: false, realMap: false, results, browserErrors: errors };
    fs.writeFileSync(path.join(artifacts, 'open-ui-results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } catch (error) {
    await page.screenshot({ path: path.join(artifacts, 'open-ui-failure.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally { await context.close(); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
