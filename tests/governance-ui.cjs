// Isolated, no-key acceptance for requirements, scope visualization and fidelity.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const playwright = require(
  process.env.PLAYWRIGHT_MODULE_DIR ||
    path.join(
      process.env.USERPROFILE,
      '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright',
    ),
);
const root = path.resolve(__dirname, '..');
const base = 'http://127.0.0.1:8016';
const runId = crypto.randomUUID();
const output = path.resolve(
  process.env.UI_OUTPUT_DIR ||
    path.join(root, 'tests/artifacts/governance-' + runId),
);
const data = path.join(root, '.runtime/governance-ui-' + runId);
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(data, { recursive: true });
const log = fs.openSync(path.join(output, 'server.log'), 'a');
const env = {
  ...process.env,
  TRAVEL_ENV_FILE: path.join(data, 'absent.env'),
  TRAVEL_GENUI_DATA_DIR: data,
  AGENT_MODE: 'demo',
  CANDIDATE_AGENT_MODE: 'mock',
  LLM_API_KEY: '',
  DEEPSEEK_API_KEY: '',
  AMAP_KEY: '',
  AGENT_REQUESTS_PER_MINUTE: '1000',
  PYTHONUTF8: '1',
};
let child, ownedPid, browser, page, context, session;
const results = [],
  errors = [],
  external = [];
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function record(name) {
  results.push({ name, passed: true });
  console.log('PASS ' + name);
}
async function health() {
  const response = await fetch(base + '/api/health');
  assert.ok(response.ok);
  return response.json();
}
async function start() {
  const busy = await new Promise((resolve) => {
    const socket = net.connect(8016, '127.0.0.1');
    socket.once('connect', () => {
      socket.destroy();
      resolve(true);
    });
    socket.once('error', () => resolve(false));
  });
  assert.equal(busy, false, 'Port 8016 must be free');
  child = spawn(
    path.join(root, '.venv/Scripts/python.exe'),
    [
      '-m',
      'uvicorn',
      'backend.app:app',
      '--host',
      '127.0.0.1',
      '--port',
      '8016',
    ],
    { cwd: root, env, windowsHide: true, stdio: ['ignore', log, log] },
  );
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const status = await health();
      assert.equal(path.resolve(status.projectRoot), root);
      ownedPid = status.processId;
      return;
    } catch (error) {
      if (attempt === 99) throw error;
      await delay(100);
    }
  }
}
async function stop() {
  if (!ownedPid) return;
  const status = await health();
  assert.equal(status.processId, ownedPid);
  assert.equal(path.resolve(status.projectRoot), root);
  process.kill(ownedPid);
  if (child) child.kill();
  ownedPid = null;
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      await health();
      await delay(100);
    } catch {
      return;
    }
  }
  throw Error('Owned test port did not close');
}
async function current() {
  const response = await context.request.get(
    base + '/api/sessions/' + session.id,
  );
  assert.equal(response.status(), 200);
  return response.json();
}
async function mutation(suffix, action) {
  const responsePromise = page.waitForResponse(
    (response) =>
      ['POST', 'PATCH'].includes(response.request().method()) &&
      new URL(response.url()).pathname.endsWith(suffix),
  );
  await action();
  const response = await responsePromise;
  const body = await response.json();
  assert.equal(response.status(), 200, JSON.stringify(body));
  session = body;
  return body;
}
async function addGoal(text, kind) {
  await page.getByLabel('需求内容', { exact: true }).fill(text);
  await page.getByLabel('需求校验方式', { exact: true }).selectOption(kind);
  if (kind === 'cost_ceiling')
    await page.getByLabel('需求估算费用上限', { exact: true }).fill('3000');
  await page
    .getByRole('checkbox', {
      name: '我确认此需求、范围和校验方式，从下一版本生效。',
      exact: true,
    })
    .check();
  await mutation('/requirements', () =>
    page.getByRole('button', { name: '保存持续需求', exact: true }).click(),
  );
  await page
    .getByTestId('active-requirement')
    .filter({ hasText: text })
    .waitFor();
}
async function screenshot(name) {
  await page.waitForFunction(
    () => document.querySelectorAll('.toast-stack .toast').length === 0,
  );
  await page.screenshot({
    path: path.join(output, name),
    fullPage: false,
    animations: 'disabled',
  });
}

(async () => {
  try {
    await start();
    browser = await playwright.chromium.launch({
      headless: true,
      executablePath:
        process.env.UI_BROWSER_PATH ||
        'C:/Program Files/Google/Chrome/Application/chrome.exe',
    });
    context = await browser.newContext({
      viewport: { width: 1600, height: 1100 },
      locale: 'zh-CN',
      reducedMotion: 'reduce',
    });
    page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => {
      const hostname = new URL(request.url()).hostname;
      if (!['127.0.0.1', 'localhost'].includes(hostname))
        external.push(hostname);
    });
    await page.goto(base);
    await mutation('/demo-sessions', () =>
      page.getByTestId('open-three-day-demo').click(),
    );
    await page.getByRole('tabpanel').waitFor();
    await page.getByTestId('open-requirements').click();
    await addGoal('保持已确认酒店', 'keep_hotel');
    await addGoal('每天安排一次休息', 'preference');
    await addGoal('费用估算控制在3000元', 'cost_ceiling');
    const previousGoal = session.requirements.at(-1);
    await page
      .getByTestId('active-requirement')
      .filter({ hasText: previousGoal.text })
      .getByRole('button', { name: '替换此需求', exact: true })
      .click();
    await page
      .getByLabel('需求内容', { exact: true })
      .fill('费用估算控制在3500元');
    await page.getByLabel('需求估算费用上限', { exact: true }).fill('3500');
    await page
      .getByRole('checkbox', {
        name: '我确认此需求、范围和校验方式，从下一版本生效。',
        exact: true,
      })
      .check();
    await mutation('/requirements', () =>
      page.getByRole('button', { name: '确认替换需求', exact: true }).click(),
    );
    assert.equal(
      session.requirements.find((item) => item.id === previousGoal.id).status,
      'superseded',
    );
    const newGoal = session.requirements.at(-1);
    await page
      .getByTestId('active-requirement')
      .filter({ hasText: newGoal.text })
      .getByRole('button', { name: '停用此需求', exact: true })
      .click();
    await mutation('/requirements', () =>
      page.getByRole('button', { name: '确认停用', exact: true }).click(),
    );
    assert.equal(session.requirements.at(-1).status, 'retired');
    await screenshot('01-requirements.png');
    record(
      'User-confirmed requirements add, replace and retire with versioned source links',
    );
    await page
      .getByRole('button', { name: '关闭历史需求', exact: true })
      .click();
    await page.getByTestId('edit-mode-toggle').click();
    await mutation('/editor/locks', () =>
      page.getByRole('button', { name: '锁定第一天', exact: true }).click(),
    );
    await mutation('/editor/locks', () =>
      page.getByRole('button', { name: '锁定第二天', exact: true }).click(),
    );
    await page.getByRole('tab').filter({ hasText: '第三天' }).click();
    await page
      .getByRole('button', { name: '选择第三天下午', exact: true })
      .click();
    await page.getByRole('checkbox', { name: '景点安排', exact: true }).check();
    await page
      .getByRole('textbox', { name: '描述本次修改', exact: true })
      .fill('换一个景点，保留时间和其他内容');
    const before = await current();
    await page.getByTestId('generate-edit-candidates').click();
    await page.getByTestId('phase2-candidates').waitFor();
    await page.getByTestId('phase2-candidate-B').click();
    await page.getByTestId('scope-change-map').waitFor();
    assert.ok((await page.locator('.card-change-summary').count()) > 0);
    assert.ok(
      (await page
        .locator('.grouped-differences [data-derived="true"]')
        .count()) > 0,
    );
    assert.equal(
      await page
        .getByTestId('scope-change-map')
        .getByText('已锁定', { exact: true })
        .count(),
      2,
    );
    assert.deepEqual(
      await current(),
      before,
      'Preview and comparison must not write saved state',
    );
    await page.getByTestId('scope-change-map').scrollIntoViewIfNeeded();
    await screenshot('02-scope-and-diff.png');
    await page.locator('.card-change-summary').first().scrollIntoViewIfNeeded();
    await page.locator('.grouped-differences').scrollIntoViewIfNeeded();
    await screenshot('02b-card-differences.png');
    record(
      'Day map, card annotations and grouped direct/derived differences preserve preview isolation',
    );
    await mutation('/edits/commit', () =>
      page.getByTestId('commit-edit-candidate').click(),
    );
    assert.deepEqual(session.tripState.days.day1, before.tripState.days.day1);
    assert.deepEqual(session.tripState.days.day2, before.tripState.days.day2);
    assert.equal(session.tripState.hotelId, before.tripState.hotelId);
    await page.getByTestId('open-fidelity').click();
    await page
      .getByTestId('fidelity-panel')
      .getByText('状态保留率', { exact: true })
      .waitFor();
    const response = await context.request.get(
      base + '/api/sessions/' + session.id + '/fidelity',
    );
    const report = await response.json();
    assert.equal(report.summary.unauthorizedPathCount, 0);
    assert.equal(report.summary.stateRetentionRate, 1);
    await screenshot('03-fidelity.png');
    const reviewedVersion = session.version;
    await mutation('/fidelity/reviews', () =>
      page.getByRole('button', { name: '确认满足', exact: true }).click(),
    );
    await page.getByText('人工确认满足', { exact: true }).waitFor();
    await page
      .getByRole('region', { name: '当前版本验证详情', exact: true })
      .scrollIntoViewIfNeeded();
    await screenshot('05-manual-review.png');
    const downloadPromise = page.waitForEvent('download');
    await page
      .getByRole('button', { name: '导出验证 JSON', exact: true })
      .click();
    const download = await downloadPromise;
    await download.saveAs(path.join(output, 'exported-fidelity.json'));
    const exported = JSON.parse(
      fs.readFileSync(path.join(output, 'exported-fidelity.json'), 'utf8'),
    );
    assert.equal(exported.formalExperiment, false);
    assert.ok(
      exported.rows
        .find((row) => row.version === reviewedVersion)
        .requirements.some((item) => item.review?.verdict === 'satisfied'),
    );
    record(
      'Persisted fidelity metrics, version-specific manual review and exported JSON',
    );
    await page
      .getByRole('button', { name: '关闭保真验证', exact: true })
      .click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByTestId('open-requirements').click();
    await page.getByTestId('requirements-panel').waitFor();
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    );
    await screenshot('04-mobile-requirements.png');
    await page
      .getByRole('button', { name: '关闭历史需求', exact: true })
      .click();
    const saved = await current();
    await stop();
    await start();
    await page.reload();
    await page
      .getByRole('button', { name: '继续上次行程', exact: true })
      .click();
    await page.getByRole('tabpanel').waitFor();
    assert.deepEqual((await current()).requirements, saved.requirements);
    assert.deepEqual(
      (await current()).requirementReviews,
      saved.requirementReviews,
    );
    assert.equal((await current()).version, saved.version);
    record(
      '390px UI and real server restart retain requirement ledger, reviews and itinerary',
    );
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    fs.writeFileSync(
      path.join(output, 'result.json'),
      JSON.stringify(
        {
          passed: true,
          runId,
          tripId: session.id,
          results,
          errors,
          external,
          realModel: false,
          realMap: false,
        },
        null,
        2,
      ),
    );
    console.log(
      JSON.stringify({
        passed: true,
        groups: results.length,
        errors,
        external,
      }),
    );
  } catch (error) {
    if (page) await screenshot('failure.png').catch(() => {});
    fs.writeFileSync(
      path.join(output, 'failure.json'),
      JSON.stringify(
        { passed: false, results, errors, external, error: error.stack },
        null,
        2,
      ),
    );
    console.error(error.stack);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    await stop().catch((error) => {
      console.error(error.stack);
      process.exitCode = 1;
    });
    fs.closeSync(log);
  }
})();
