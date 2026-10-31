// Node built-ins + existing TypeScript; no additional test framework.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const folder = path.join(__dirname, '..', '.runtime', 'schema-unit');
fs.mkdirSync(folder, { recursive: true });
const output = path.join(folder, 'tripSchema.cjs');
const source = fs.readFileSync(path.join(__dirname, '../src/features/agentic-editor/tripSchema.ts'), 'utf8');
fs.writeFileSync(output, ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText);
const { parseTripDocument } = require(output);
const document = {
  schemaVersion: '1.0', tripId: 'trip-test', version: 1, title: '验证行程', budgetLimit: 2600,
  dayOrder: ['day-test'], entities: {
    days: { 'day-test': { id: 'day-test', label: '第一天', date: '2026-11-01', activityIds: ['activity-test'] } },
    activities: { 'activity-test': { id: 'activity-test', dayId: 'day-test', period: 'pm', title: '真实地点', placeId: 'place-test', startTime: '13:30', description: '描述' } },
  }, ui: { componentCatalogVersion: '1.0', components: {
    'card-day': { id: 'card-day', kind: 'DayCard', entityId: 'day-test', props: {} },
    'card-stop': { id: 'card-stop', kind: 'ActivityCard', entityId: 'activity-test', parentId: 'card-day', props: {} },
  } },
};
let count = 0;
try {
  assert.deepEqual(parseTripDocument(document), document); count++;
  const cases = [
    d => { d.version = 0; }, d => { d.version = '1'; },
    d => { d.entities.activities['activity-test'].startTime = '26:80'; },
    d => { d.entities.days['day-test'].date = '2026-02-30'; },
    d => { d.entities.activities['activity-test'].dayId = 'missing'; },
    d => { d.entities.days['day-test'].activityIds.push('activity-test'); },
    d => { d.dayOrder.push('day-test'); },
    d => { d.ui.components['card-stop'].kind = 'arbitrary-html'; },
    d => { d.ui.components['card-stop'].props.javascript = 'alert(1)'; },
    d => { d.ui.components['card-stop'].parentId = 'missing'; },
    d => { d.internals = {}; },
  ];
  for (const change of cases) { const d = structuredClone(document); change(d); assert.throws(() => parseTripDocument(d)); count++; }
  console.log(JSON.stringify({ passed: true, checks: count, scope: 'client schema only; server permissions validated separately' }));
} finally { fs.unlinkSync(output); fs.rmdirSync(folder); }
