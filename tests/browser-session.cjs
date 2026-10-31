// Existing smoke suites use an explicit legacy fixture session; the personalized
// onboarding suite separately verifies that fresh visitors never create one.
const assert = require('node:assert/strict');
const STORAGE_KEY = 'travel-genui-session-v1';

async function resumeSavedTrip(page, dayName = '第二天') {
  await page.getByTestId('trip-setup').waitFor();
  await page.getByRole('button', { name: '继续上次行程', exact: true }).click();
  await page.getByRole('tabpanel').waitFor();
  if (dayName) await page.getByRole('tab', { name: new RegExp(dayName) }).click();
}

async function openLegacyTrip(page, context, baseURL, dayName = '第二天') {
  await page.goto(baseURL, { waitUntil: 'networkidle' });
  const response = await context.request.post(new URL('/api/sessions', baseURL).href, { data: {} });
  const session = await response.json();
  assert.equal(response.status(), 200, JSON.stringify(session));
  assert.ok(session.id && session.tripState, 'Legacy fixture session must be created');
  await page.evaluate(({ key, id }) => localStorage.setItem(key, id), { key: STORAGE_KEY, id: session.id });
  await page.reload({ waitUntil: 'networkidle' });
  await resumeSavedTrip(page, dayName);
  return session;
}

module.exports = { openLegacyTrip, resumeSavedTrip };
