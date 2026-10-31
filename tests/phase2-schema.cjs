// Deterministic client boundary checks; no network, API key or formal state writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const runtime = path.join(__dirname, '..', '.runtime');
fs.mkdirSync(runtime, { recursive: true });
const folder = fs.mkdtempSync(path.join(runtime, 'phase2-schema-unit-'));
for (const name of ['tripSchema', 'protocol', 'candidateSchema']) {
  const source = fs.readFileSync(path.join(__dirname, '../src/features/agentic-editor/' + name + '.ts'), 'utf8').replace("'./tripSchema'", "'./tripSchema.cjs'").replace("'./protocol'", "'./protocol.cjs'");
  fs.writeFileSync(path.join(folder, name + '.cjs'), ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText);
}
const { parseCandidateBatch } = require(path.join(folder, 'candidateSchema.cjs'));
const aid = 'activity-' + 'a'.repeat(32);
const doc = {
  schemaVersion: '1.0', tripId: 'trip-test', version: 3, title: '验证三日行程', budgetLimit: 2600,
  dayOrder: ['day-test'], entities: {
    days: { 'day-test': { id: 'day-test', label: '第二天', date: '2026-11-01', activityIds: [aid] } },
    activities: { [aid]: { id: aid, dayId: 'day-test', period: 'pm', title: '真实地点', placeId: 'place-test', startTime: '13:30', description: '正式描述' } },
  }, ui: { componentCatalogVersion: '1.0', components: {
    'card-day': { id: 'card-day', kind: 'DayCard', entityId: 'day-test', props: {} },
    'card-stop': { id: 'card-stop', kind: 'ActivityCard', entityId: aid, parentId: 'card-day', props: {} },
  } },
};
const pointer = '/entities/activities/' + aid + '/description';
const draft = structuredClone(doc); draft.entities.activities[aid].description = '只读草稿';
const batch = {
  requestId: 'request-test', baseVersion: 3, generationMode: 'mock', requestedCount: 3, candidateCount: 1,
  effectiveScope: { selectedComponentIds: ['card-stop'], allowedPaths: [pointer], lockedComponentIds: ['card-day-other'], eligibleActivityIds: [aid], editableFields: ['description'], labels: ['第二天下午'], selectedLocked: false, phase: 'Phase 2', restriction: '受限字段' },
  candidates: [{ candidateId: 'cand-test', baseVersion: 3, strategy: 'minimal_change', label: '最小改动', rationale: '真实字段差异',
    patch: [{ op: 'replace', path: pointer, value: '只读草稿' }], document: draft, changedPaths: [pointer], derivedChangedPaths: [],
    differences: [{ path: pointer, before: '正式描述', after: '只读草稿', activityId: aid, title: '真实地点', field: 'description' }],
    validationReport: { valid: true, schema: true, scope: true, locks: true, invariants: true, leafDiff: true, legacyBusinessDiff: true }, expiresAt: '2026-11-01T12:00:00Z',
  }], rejectedCandidates: [{ strategy: 'efficiency_first', code: 'INSUFFICIENT_DIVERSITY', message: '不以重复候选凑数' }], diagnostics: ['Mock明确标记'],
};
let count = 0;
try {
  assert.deepEqual(parseCandidateBatch(batch, doc), batch); count++;
  const stateBefore = JSON.stringify(doc), batchBefore = JSON.stringify(batch);
  const parsed = parseCandidateBatch(batch, doc); parsed.candidates[0].document.entities.activities[aid].description = '独立克隆';
  assert.equal(JSON.stringify(doc), stateBefore); assert.equal(JSON.stringify(batch), batchBefore); count++;
  const invalid = [
    b => { b.baseVersion = 2; }, b => { b.candidateCount = 3; }, b => { b.generationMode = 'real-looking-mock'; },
    b => { b.candidates[0].validationReport.locks = false; }, b => { b.candidates[0].validationReport.score = 1; },
    b => { b.candidates[0].document.version = 4; }, b => { b.candidates[0].document.title = '未授权全局变更'; },
    b => { b.candidates[0].document.entities.activities[aid].startTime = '25:00'; },
    b => { b.candidates[0].changedPaths.push(pointer); }, b => { b.candidates[0].patch[0].op = 'move'; },
    b => { b.candidates[0].patch[0].value = '与预览不符'; }, b => { b.candidates[0].patch[0].path = '/entities/activities'; },
    b => { b.candidates[0].patch[0].path = '/entities/activities/__proto__/description'; },
    b => { b.effectiveScope.allowedPaths = []; }, b => { b.candidates[0].differences[0].before = '错误基线'; },
    b => { b.candidates[0].differences[0].activityId = 'another-activity'; }, b => { b.candidates[0].differences[0].field = 'startTime'; },
    b => { b.candidates[0].document.ui.components['card-stop'].props.javascript = 'alert(1)'; },
    b => { b.candidates[0].baseVersion = 1; },
    b => { b.candidates.push(structuredClone(b.candidates[0])); b.candidateCount = 2; },
    b => { b.candidates[0].patch[0].score = 1; },
    b => { b.candidates[0].patch.push(structuredClone(b.candidates[0].patch[0])); },
    b => { b.candidates[0].differences.push(structuredClone(b.candidates[0].differences[0])); },
  ];
  for (const change of invalid) {
    const value = structuredClone(batch); change(value);
    assert.throws(() => parseCandidateBatch(value, doc));
    assert.equal(JSON.stringify(doc), stateBefore, 'Invalid candidates cannot mutate formal client state'); count++;
  }
  const rich = structuredClone(doc);
  for (const period of ['am', 'pm', 'evening']) rich.ui.components['period-day-test-' + period] = { id: 'period-day-test-' + period, kind: 'PeriodGroup', entityId: 'day-test', parentId: 'card-day', props: {} };
  rich.ui.components['card-stop'].parentId = 'period-day-test-pm';
  const context = { catalog: { places: [{ id: 'place-test', name: '真实地点', description: '正式描述' }, { id: 'place-next', name: '真实备用景点', description: '来自目录的新描述' }], hotels: [] }, componentRegistry: { 'card-stop': { componentId: 'card-stop', kind: 'ActivityCard', entityId: aid, dayKey: 'day1', stopId: 'stop-test' } }, tripState: { days: { day1: { stops: [{ id: 'stop-test', placeId: 'place-test', startTime: '13:30' }] } } } };
  function makeCandidate(operations, after, derived, ctx = context) {
    const result = structuredClone(batch);
    result.effectiveScope.labels = ['第一天'];
    result.effectiveScope.editableFields = [...new Set(operations.map(item => item.path.split('/').pop()))];
    result.effectiveScope.allowedPaths = operations.map(item => item.path);
    const candidate = result.candidates[0];
    candidate.patch = operations; candidate.document = after; candidate.derivedChangedPaths = derived;
    candidate.changedPaths = [...operations.filter(item => rich.entities.activities[aid][item.path.split('/').pop()] !== item.value).map(item => item.path), ...derived];
    candidate.differences = candidate.changedPaths.map(path => {
      const field = path.split('/').pop(), component = path.startsWith('/ui/');
      const before = component ? rich.ui.components['card-stop'].parentId : rich.entities.activities[aid][field];
      const value = component ? after.ui.components['card-stop'].parentId : after.entities.activities[aid][field];
      return { path, field, activityId: aid, title: rich.entities.activities[aid].title, before, after: value };
    });
    return result;
  }
  const placePath = '/entities/activities/' + aid + '/placeId', titlePath = '/entities/activities/' + aid + '/title';
  const placeDraft = structuredClone(rich); Object.assign(placeDraft.entities.activities[aid], { placeId: 'place-next', title: '真实备用景点', description: '来自目录的新描述' });
  const placeBatch = makeCandidate([{ op: 'replace', path: placePath, value: 'place-next' }], placeDraft, [titlePath, pointer]);
  assert.deepEqual(parseCandidateBatch(placeBatch, rich, context), placeBatch); count++;
  assert.throws(() => parseCandidateBatch(placeBatch, rich)); count++;
  const tamperedTitle = structuredClone(placeBatch); tamperedTitle.candidates[0].document.entities.activities[aid].title = '目录没有的虚构标题'; tamperedTitle.candidates[0].differences.find(item => item.field === 'title').after = '目录没有的虚构标题';
  assert.throws(() => parseCandidateBatch(tamperedTitle, rich, context)); count++;
  const hiddenMetadata = structuredClone(placeBatch); hiddenMetadata.candidates[0].document.budgetLimit = 7000;
  assert.throws(() => parseCandidateBatch(hiddenMetadata, rich, context)); count++;
  const extraDerived = structuredClone(placeBatch); extraDerived.candidates[0].derivedChangedPaths.push('/budgetLimit');
  assert.throws(() => parseCandidateBatch(extraDerived, rich, context)); count++;
  const fakeDescription = structuredClone(placeBatch); fakeDescription.candidates[0].document.entities.activities[aid].description = '不是目录的派生描述'; fakeDescription.candidates[0].differences.find(item => item.field === 'description').after = '不是目录的派生描述';
  assert.throws(() => parseCandidateBatch(fakeDescription, rich, context)); count++;
  const explicitContext = structuredClone(context); explicitContext.tripState.days.day1.stops[0].description = '正式描述';
  const explicitDraft = structuredClone(placeDraft); explicitDraft.entities.activities[aid].description = '正式描述';
  const explicitBatch = makeCandidate([{ op: 'replace', path: placePath, value: 'place-next' }], explicitDraft, [titlePath]);
  assert.deepEqual(parseCandidateBatch(explicitBatch, rich, explicitContext), explicitBatch); count++;
  const noOpBatch = makeCandidate([{ op: 'replace', path: placePath, value: 'place-next' }, { op: 'replace', path: pointer, value: '正式描述' }], explicitDraft, [titlePath]);
  assert.deepEqual(parseCandidateBatch(noOpBatch, rich, context), noOpBatch); count++;
  const timePath = '/entities/activities/' + aid + '/startTime', periodPath = '/entities/activities/' + aid + '/period', parentPath = '/ui/components/card-stop/parentId';
  const timeDraft = structuredClone(rich); timeDraft.entities.activities[aid].startTime = '18:30'; timeDraft.entities.activities[aid].period = 'evening'; timeDraft.ui.components['card-stop'].parentId = 'period-day-test-evening';
  const timeBatch = makeCandidate([{ op: 'replace', path: timePath, value: '18:30' }], timeDraft, [periodPath, parentPath]);
  assert.deepEqual(parseCandidateBatch(timeBatch, rich), timeBatch); count++;
  const wrongPeriod = structuredClone(timeBatch); wrongPeriod.candidates[0].document.entities.activities[aid].period = 'am'; wrongPeriod.candidates[0].differences.find(item => item.field === 'period').after = 'am';
  assert.throws(() => parseCandidateBatch(wrongPeriod, rich)); count++;
  const directTitle = structuredClone(placeBatch); directTitle.effectiveScope.allowedPaths.push(titlePath); directTitle.candidates[0].patch.push({ op: 'replace', path: titlePath, value: '真实备用景点' });
  assert.throws(() => parseCandidateBatch(directTitle, rich, context)); count++;
  const changedSourceTitle = structuredClone(timeBatch); changedSourceTitle.candidates[0].differences[0].title = '另一个地点';
  assert.throws(() => parseCandidateBatch(changedSourceTitle, rich)); count++;
  assert.equal(JSON.stringify(rich.entities.activities[aid]), JSON.stringify(doc.entities.activities[aid]), 'Replay checks must not mutate the authoritative document'); count++;
  console.log(JSON.stringify({ passed: true, checks: count, scope: 'Zod, exact authorized replay, real catalog derived title/description, cross-period binding, NOOP support and clone isolation; server separately enforces authority' }));
} finally {
  for (const name of ['tripSchema.cjs', 'protocol.cjs', 'candidateSchema.cjs']) fs.unlinkSync(path.join(folder, name));
  fs.rmdirSync(folder);
}
