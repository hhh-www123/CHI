/* Browser smoke coverage for the local, deterministic course prototype.
 * Run only after the frontend and backend are already running.
 * Uses an isolated browser context; never opens a personal browser profile.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { openLegacyTrip, resumeSavedTrip } = require('./browser-session.cjs');

function loadPlaywright() {
  try { return require('playwright'); }
  catch {
    const runtime = process.env.PLAYWRIGHT_MODULE_DIR || path.join(
      process.env.USERPROFILE || '', '.cache', 'codex-runtimes',
      'codex-primary-runtime', 'dependencies', 'node', 'node_modules', 'playwright',
    );
    return require(runtime);
  }
}

const { chromium } = loadPlaywright();
const baseURL = process.env.UI_BASE_URL || 'http://127.0.0.1:5173';
const artifacts = process.env.UI_TEST_ARTIFACT_DIR || path.resolve(__dirname, 'artifacts');
const candidates = [
  process.env.UI_BROWSER_PATH,
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
].filter(Boolean);
const executablePath = candidates.find(candidate => fs.existsSync(candidate));
const results = [];

async function check(name, run) {
  await run();
  results.push({ name, passed: true });
  console.log(`PASS ${name}`);
}

async function eventually(test, message) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (await test()) return;
    await new Promise(resolve => setTimeout(resolve, 60));
  }
  throw new Error(message);
}

(async () => {
  fs.mkdirSync(artifacts, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN', reducedMotion: 'reduce' });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.setDefaultTimeout(12000);
  let session;
  let baseline;

  async function readSession() {
    const id = await page.evaluate(() => localStorage.getItem('travel-genui-session-v1'));
    assert.ok(id, 'UI should save its own new session ID');
    const response = await context.request.get(`${baseURL}/api/sessions/${encodeURIComponent(id)}`);
    assert.ok(response.ok(), `Read session: ${response.status()}`);
    session = await response.json();
    return session;
  }

  async function responseFor(method, suffix, action) {
    const [response] = await Promise.all([
      page.waitForResponse(response => response.request().method() === method && new URL(response.url()).pathname.endsWith(suffix)),
      action(),
    ]);
    const body = await response.json();
    assert.ok(response.ok(), `${method} ${suffix}: ${response.status()} ${JSON.stringify(body)}`);
    if (body.tripState) session = body;
    return body;
  }

  async function previewAndApply(instruction) {
    await page.getByLabel('描述本次修改', { exact: true }).fill(instruction);
    const before = structuredClone(await readSession());
    await responseFor('POST', '/generate', () => page.getByRole('button', { name: '预览修改', exact: true }).click());
    const previewState = await readSession();
    assert.equal(previewState.version, before.version, 'Preview must not mutate the saved session');
    return responseFor('POST', '/apply', () => page.getByRole('button', { name: '应用这次修改', exact: true }).click());
  }

  try {
    await openLegacyTrip(page, context, baseURL);

    await check('Initial Hangzhou three-day itinerary loads with an explicit demo notice', async () => {
      await page.getByRole('tabpanel').waitFor();
      assert.match(await page.getByRole('heading', { level: 1 }).innerText(), /杭州/);
      assert.equal(await page.getByRole('tab').count(), 3);
      assert.equal(await page.getByRole('tab', { name: /第二天/ }).getAttribute('aria-selected'), 'true');
      assert.ok(await page.getByTestId('service-settings-button').isVisible());
      assert.match(await page.getByTestId('service-settings-button').getAttribute('title'), /模型未接入/);
      await page.getByTestId('service-settings-button').click();
      const settings = page.getByRole('dialog', { name: '服务接入', exact: true });
      await settings.waitFor();
      const modelForm = settings.getByTestId('model-integration-form');
      const amapForm = settings.getByTestId('amap-integration-form');
      assert.equal(await modelForm.getAttribute('data-active'), 'true');
      await settings.locator('#integration-amap-key').click();
      assert.equal(await amapForm.getAttribute('data-active'), 'true');
      assert.equal(await modelForm.getAttribute('data-active'), 'false');
      await settings.locator('#integration-model-url').click();
      assert.equal(await modelForm.getAttribute('data-active'), 'true');
      await settings.getByRole('button', { name: '关闭服务接入', exact: true }).click();
      baseline = structuredClone(await readSession());
      assert.equal(baseline.tripState.city, '杭州');
      assert.equal(Object.keys(baseline.tripState.days).length, 3);
      await page.screenshot({ path: path.join(artifacts, '01-desktop-initial.png'), fullPage: true });
    });

    const note = 'QA：下午留出喝茶休息时间；这个备注应在局部更新后保留。';
    await check('Saved note survives preview and local cards-to-timeline update; hotel and day one remain unchanged', async () => {
      await page.getByLabel('第二天行程备注', { exact: true }).fill(note);
      await responseFor('PATCH', '/direct', () => page.getByLabel('第二天行程备注', { exact: true }).press('Tab'));
      assert.equal(session.tripState.days.day2.note, note);
      const before = structuredClone(session);
      await responseFor('POST', '/generate', () => page.getByRole('button', { name: '预览时间轴展示', exact: true }).click());
      assert.equal((await readSession()).uiSpec.regions['day-2'].view, 'cards', 'Preview must not apply changes');
      await responseFor('POST', '/apply', () => page.getByRole('button', { name: '应用这次修改', exact: true }).click());
      assert.equal(session.uiSpec.regions['day-2'].view, 'timeline');
      assert.equal(session.tripState.days.day2.note, note);
      assert.deepEqual(session.tripState.days.day1, before.tripState.days.day1);
      assert.equal(session.tripState.hotelId, before.tripState.hotelId);
      assert.deepEqual(session.uiSpec.regions['day-1'], before.uiSpec.regions['day-1']);
      assert.equal(session.derived.total, before.derived.total, 'Layout-only change must not affect budget');
      await eventually(async () => (await page.getByLabel('第二天行程备注', { exact: true }).inputValue()) === note, 'Saved note not reflected in timeline');
      assert.ok(await page.locator('.stops-list.timeline').isVisible());
      await page.screenshot({ path: path.join(artifacts, '02-desktop-timeline.png'), fullPage: true });
    });

    await check('Locking day two disables generation and view-change entry points', async () => {
      await responseFor('PATCH', '/locks', () => page.getByRole('button', { name: '锁定第二天的生成修改', exact: true }).click());
      assert.ok(session.controlState.lockedRegions.includes('day-2'));
      await page.getByLabel('描述本次修改', { exact: true }).fill('改成卡片展示');
      await eventually(() => page.getByRole('button', { name: '预览修改', exact: true }).isDisabled(), 'Generate is not disabled for locked day');
      assert.ok(await page.getByRole('button', { name: '预览卡片展示', exact: true }).isDisabled());
      assert.ok(await page.getByLabel('第二天行程备注', { exact: true }).isEditable(), 'Lock protects generation; direct user notes remain editable');
    });

    await check('Unlock, switch back to cards, then undo restores the timeline and saved data', async () => {
      await responseFor('PATCH', '/locks', () => page.getByRole('button', { name: '解锁第二天的生成修改', exact: true }).click());
      await previewAndApply('改成卡片展示，保留景点、时间和备注');
      assert.equal(session.uiSpec.regions['day-2'].view, 'cards');
      await responseFor('POST', '/undo', () => page.getByRole('button', { name: '撤销上一步', exact: true }).click());
      assert.equal(session.uiSpec.regions['day-2'].view, 'timeline');
      assert.equal(session.tripState.days.day2.note, note);
      assert.equal(session.tripState.hotelId, baseline.tripState.hotelId);
      await eventually(() => page.locator('.stops-list.timeline').isVisible(), 'Undo did not render the previous timeline');
    });

    await check('Indoor alternatives can be displayed; replacing a stop recalculates the derived budget', async () => {
      await previewAndApply('显示室内备选景点对比表，保留当前行程');
      assert.equal(session.uiSpec.regions['day-2'].showAlternatives, true);
      await page.getByRole('table').waitFor();
      await page.getByLabel('选择要替换的行程', { exact: true }).selectOption('day2-stop-1');
      const before = structuredClone(await readSession());
      await responseFor('PATCH', '/direct', () => page.getByRole('button', { name: '用浙江省博物馆（孤山馆区）替换龙井村（牌坊）', exact: true }).click());
      assert.equal(session.tripState.days.day2.stops[0].placeId, 'city-museum');
      assert.equal(session.tripState.days.day2.stops[0].id, 'day2-stop-1', 'Replacement should retain the stable stop ID');
      assert.equal(session.derived.total, before.derived.total - 5, 'Estimated ticket + transport should change from 0+25 to 0+20');
      assert.equal(session.tripState.days.day2.note, note);
      assert.deepEqual(session.tripState.days.day1, before.tripState.days.day1);
      await eventually(() => page.getByRole('heading', { name: '浙江省博物馆（孤山馆区）', exact: true }).isVisible(), 'Replacement not reflected in itinerary');
      await eventually(async () => Number((await page.getByTestId('budget-total').innerText()).replace(/[^\d.-]/g, '')) === session.derived.total, 'Derived budget did not update on screen');
    });

    await check('Reload restores the same session, view, note, replacement, and budget', async () => {
      const before = structuredClone(await readSession());
      await page.reload({ waitUntil: 'networkidle' });
      await resumeSavedTrip(page);
      await page.getByRole('tabpanel').waitFor();
      const after = await readSession();
      assert.equal(after.id, before.id);
      assert.equal(after.version, before.version);
      assert.deepEqual(after.tripState, before.tripState);
      assert.deepEqual(after.uiSpec, before.uiSpec);
      assert.deepEqual(after.derived, before.derived);
      assert.equal(await page.getByLabel('第二天行程备注', { exact: true }).inputValue(), note);
    });

    await check('390px mobile layout has no page-level horizontal overflow and history remains accessible', async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByRole('heading', { level: 1 }).waitFor();
      const dimensions = await page.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        html: document.documentElement.scrollWidth,
        body: document.body.scrollWidth,
      }));
      assert.ok(dimensions.html <= dimensions.viewport + 1 && dimensions.body <= dimensions.viewport + 1, JSON.stringify(dimensions));
      await page.getByRole('button', { name: '查看操作记录', exact: true }).click();
      const historyDialog = page.getByRole('dialog', { name: '操作记录', exact: true });
      await historyDialog.waitFor({ state: 'visible' });
      assert.ok(await historyDialog.getByRole('heading', { name: '每一次修改，都有记录', exact: true }).isVisible());
      await historyDialog.getByRole('button', { name: '关闭操作记录', exact: true }).click();
      await historyDialog.waitFor({ state: 'hidden' });
      await page.screenshot({ path: path.join(artifacts, '03-mobile.png'), fullPage: true });
    });

    assert.deepEqual(pageErrors, [], `Browser runtime errors: ${pageErrors.join('\n')}`);
    fs.writeFileSync(path.join(artifacts, 'smoke-results.json'), JSON.stringify({ baseURL, passed: true, results, pageErrors }, null, 2));
    console.log(`All ${results.length} UI checks passed. Screenshots: ${artifacts}`);
  } catch (error) {
    await page.screenshot({ path: path.join(artifacts, 'failure.png'), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(artifacts, 'smoke-results.json'), JSON.stringify({ baseURL, passed: false, results, error: String(error), pageErrors }, null, 2));
    throw error;
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
