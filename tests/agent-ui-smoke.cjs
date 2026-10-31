// Uses a local HTTP model fixture. It never evaluates DeepSeek's real quality.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { openLegacyTrip, resumeSavedTrip } = require('./browser-session.cjs');
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(process.env.PLAYWRIGHT_MODULE_DIR || path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const { chromium } = playwright;
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8001';
const artifacts = process.env.UI_TEST_ARTIFACT_DIR || path.join(__dirname, 'artifacts');
const executablePath = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find(p => fs.existsSync(p));

(async () => {
  const browser = await chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const results = [];
  const read = async () => {
    const id = await page.evaluate(() => localStorage.getItem('travel-genui-session-v1'));
    const response = await context.request.get(`${base}/api/sessions/${id}`);
    assert.equal(response.status(), 200);
    return response.json();
  };
  const action = async (suffix, callback) => {
    const [response] = await Promise.all([page.waitForResponse(r => r.request().method() !== 'GET' && new URL(r.url()).pathname.endsWith(suffix)), callback()]);
    assert.equal(response.status(), 200, await response.text());
    return response.json();
  };
  try {
    await openLegacyTrip(page, context, base);
    await page.getByRole('tabpanel').waitFor();
    await page.getByLabel('第二天行程备注', { exact: true }).fill('保留我的午餐安排');
    await action('/direct', () => page.getByLabel('第二天行程备注', { exact: true }).press('Tab'));
    const before = await read();
    await page.getByLabel('描述本次修改', { exact: true }).fill('在每天游玩的经典间添加交通方式以及地图');
    const plan = await action('/generate', () => page.getByRole('button', { name: '预览修改', exact: true }).click());
    assert.equal(plan.engine, 'llm');
    assert.deepEqual(plan.affectedRegionIds.sort(), ['day-1', 'day-2', 'day-3']);
    assert.deepEqual((await read()).tripState, before.tripState);
    await action('/apply', () => page.getByRole('button', { name: '应用这次修改', exact: true }).click());
    const applied = await read();
    assert.deepEqual(applied.tripState, before.tripState);
    for (const region of ['day-1', 'day-2', 'day-3']) {
      assert.equal(applied.uiSpec.regions[region].showTransport, true);
      assert.equal(applied.uiSpec.regions[region].showMap, true);
    }
    assert.ok(await page.getByText(/交通/).count());
    assert.equal(await page.getByLabel('第二天行程备注', { exact: true }).inputValue(), '保留我的午餐安排');
    results.push('LLM HTTP fixture → all three days → transport/maps; trip data and note preserved');
    fs.mkdirSync(artifacts, { recursive: true });
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: path.join(artifacts, 'agent-transport-maps.png'), fullPage: true });
    await action('/undo', () => page.getByRole('button', { name: '撤销上一步', exact: true }).click());
    for (const region of ['day-1', 'day-2', 'day-3']) assert.equal((await read()).uiSpec.regions[region].showMap, false);
    results.push('Atomic undo restores all three UI regions');
    await page.getByLabel('描述本次修改', { exact: true }).fill('给今天加一份出门前的准备清单');
    await action('/generate', () => page.getByRole('button', { name: '预览修改', exact: true }).click());
    await action('/apply', () => page.getByRole('button', { name: '应用这次修改', exact: true }).click());
    await page.getByRole('heading', { name: '出门前检查', exact: true }).waitFor();
    await page.locator('.generated-checklist li').filter({ hasText: '带好雨伞' }).waitFor();
    results.push('Model-generated content block renders outside the old fixed commands');
    const stranger = await browser.newContext();
    const denied = await stranger.request.get(`${base}/api/sessions/${applied.id}`);
    assert.equal(denied.status(), 404);
    await stranger.close();
    results.push('Separate visitor cannot read a known session ID');
    fs.mkdirSync(artifacts, { recursive: true });
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: path.join(artifacts, 'agent-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    const width = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
    assert.ok(width.content <= width.viewport + 1, JSON.stringify(width));
    await page.screenshot({ path: path.join(artifacts, 'agent-mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    results.push('Mobile layout and browser runtime');
    fs.writeFileSync(path.join(artifacts, 'agent-results.json'), JSON.stringify({ passed: true, fixture: true, results }, null, 2));
    console.log(JSON.stringify({ passed: true, fixture: true, results }));
  } catch (error) {
    await page.screenshot({ path: path.join(artifacts, 'agent-failure.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally { await context.close(); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
