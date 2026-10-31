import { parseSurface } from './protocol';
import { z } from 'zod';
import { tripDocumentSchema } from './tripSchema';
import type { Session } from '../../types';
import type { CandidateBatch, CandidateDifferenceField } from './candidateTypes';
import type { EditorField, TripDocument } from './types';

const text = z.string().max(4000);
const editableField = z.enum(['description', 'startTime', 'placeId']);
const operation = z.object({ op: z.literal('replace'), path: z.string().max(512), value: z.string().max(2000) }).strict();
const difference = z.object({ path: z.string(), before: z.string(), after: z.string(), activityId: z.string(), title: z.string(), field: z.enum(['description', 'startTime', 'placeId', 'title', 'period', 'parentId']) }).strict();
const validation = z.object({ valid: z.literal(true), schema: z.literal(true), scope: z.literal(true), locks: z.literal(true), invariants: z.literal(true), leafDiff: z.literal(true), legacyBusinessDiff: z.literal(true) }).strict();
export const candidateBatchSchema = z.object({
  requestId: z.string().min(1), baseVersion: z.number().int().positive(), generationMode: z.enum(['mock', 'llm']),
  requestedCount: z.literal(3), candidateCount: z.number().int().min(1).max(3),
  effectiveScope: z.object({
    selectedComponentIds: z.array(z.string()).length(1), allowedPaths: z.array(z.string()), lockedComponentIds: z.array(z.string()),
    eligibleActivityIds: z.array(z.string()), editableFields: z.array(editableField), labels: z.array(z.string()),
    selectedLocked: z.boolean(), phase: z.string(), restriction: z.string(),
  }).strict(),
  candidates: z.array(z.object({
    candidateId: z.string().min(1), baseVersion: z.number().int().positive(), strategy: z.enum(['minimal_change', 'efficiency_first', 'experience_first']),
    label: text, rationale: text, patch: z.array(operation).min(1).max(16), document: tripDocumentSchema,
    changedPaths: z.array(z.string()).min(1), derivedChangedPaths: z.array(z.string()), differences: z.array(difference).min(1), validationReport: validation,
    evaluation:z.object({method:z.literal('rules-v1'),metrics:z.record(z.string(),z.number().min(0).max(1)),changeCost:z.number().min(0).max(1),score:z.number().min(0).max(1),weights:z.record(z.string(),z.number().min(0).max(1)),rank:z.number().int().min(1).max(3),evidence:z.array(z.string())}).strict().optional(),
    a2ui:z.unknown().optional(),
    expiresAt: z.iso.datetime({ offset: true }),
  }).strict()).min(1).max(3),
  metrics:z.object({generationMs:z.number().min(0),proposed:z.number().int().min(1),accepted:z.number().int().min(1),qualifiedRate:z.number().min(0).max(1),attempts:z.number().int().min(1).max(2)}).strict().optional(),
  diversity:z.array(z.object({pathJaccard:z.number().min(0).max(1),sameFinalState:z.boolean()}).strict()).optional(),
  rejectedCandidates: z.array(z.object({ strategy: z.string(), code: z.string(), message: text }).strict()), diagnostics: z.array(text),
}).strict();

function leafDiff(a: unknown, b: unknown, path = ''): string[] {
  if (a === b) return [];
  if (a && b && typeof a === 'object' && typeof b === 'object' && !Array.isArray(a) && !Array.isArray(b)) {
    const left = a as Record<string, unknown>, right = b as Record<string, unknown>;
    return [...new Set([...Object.keys(left), ...Object.keys(right)])].sort().flatMap(key => leafDiff(left[key], right[key], path + '/' + key.replace(/~/g, '~0').replace(/\//g, '~1')));
  }
  return JSON.stringify(a) === JSON.stringify(b) ? [] : [path];
}
const equalPaths = (a: string[], b: string[]) => new Set(a).size === a.length && new Set(b).size === b.length && JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
type BeforeContext = Pick<Session, 'catalog' | 'componentRegistry' | 'tripState'>;

// Client replay is an extra rendering boundary. The server remains responsible
// for authorisation, locks, invariants and commit. Derived leaves are calculated
// from the saved session/catalog; response metadata cannot grant more writes.
export function parseCandidateBatch(value: unknown, current: TripDocument, context?: BeforeContext): CandidateBatch {
  const batch = candidateBatchSchema.parse(value);
  if (batch.baseVersion !== current.version || batch.candidateCount !== batch.candidates.length) throw new Error('候选版本或数量不一致');
  const ids = new Set<string>();
  const strategies = new Set<string>();
  for (const candidate of batch.candidates) {
    if (ids.has(candidate.candidateId) || strategies.has(candidate.strategy) || candidate.baseVersion !== current.version) throw new Error('候选身份或版本无效');
    ids.add(candidate.candidateId); strategies.add(candidate.strategy);
    const actual = leafDiff(current, candidate.document).sort();
    if (!actual.length || !equalPaths(actual, candidate.changedPaths) || !equalPaths(candidate.differences.map(item => item.path), actual)) throw new Error('候选真实差异与声明不符');
    const expected = structuredClone(current);
    const paths = new Set<string>();
    const edited = new Map<string, Set<EditorField>>();
    for (const op of candidate.patch) {
      const match = /^\/entities\/activities\/(activity-[a-f0-9]+)\/(description|startTime|placeId)$/.exec(op.path);
      if (!match || paths.has(op.path) || !batch.effectiveScope.allowedPaths.includes(op.path)) throw new Error('补丁超出服务端范围');
      const aid = match[1], field = match[2] as EditorField;
      const before = current.entities.activities[aid], after = candidate.document.entities.activities[aid];
      if (!before || !after || !batch.effectiveScope.eligibleActivityIds.includes(aid) || op.value !== after[field]) throw new Error('候选字段绑定不一致');
      paths.add(op.path); expected.entities.activities[aid][field] = op.value;
      if (!edited.has(aid)) edited.set(aid, new Set());
      edited.get(aid)!.add(field);
    }
    for (const [aid, fields] of edited) {
      const before = current.entities.activities[aid], after = expected.entities.activities[aid];
      if (fields.has('placeId')) {
        const place = context?.catalog?.places.find(item => item.id === after.placeId);
        const binding = Object.values(context?.componentRegistry ?? {}).find(item => item.kind === 'ActivityCard' && item.entityId === aid);
        const stop = binding?.stopId ? context?.tripState.days[binding.dayKey]?.stops.find(item => item.id === binding.stopId) : undefined;
        if (!place || !stop || stop.placeId !== before.placeId) throw new Error('景点变更缺少可信地点目录或原始绑定');
        after.title = place.name;
        if (!fields.has('description') && !Object.prototype.hasOwnProperty.call(stop, 'description')) after.description = place.description || '';
      }
      if (fields.has('startTime')) {
        const match = /^(?:([01]\d|2[0-3])):([0-5]\d)$/.exec(after.startTime);
        if (!match) throw new Error('开始时间无效');
        const hour = Number(match[1]);
        const period = hour < 12 ? 'am' : hour < 18 ? 'pm' : 'evening';
        after.period = period;
        if (period !== before.period) {
          const card = Object.values(expected.ui.components).find(item => item.kind === 'ActivityCard' && item.entityId === aid);
          const parentId = 'period-' + after.dayId + '-' + period;
          if (!card || !expected.ui.components[parentId]) throw new Error('目标时段不在当前组件注册表中');
          card.parentId = parentId;
        }
      }
    }
    tripDocumentSchema.parse(expected);
    if (leafDiff(expected, candidate.document).length) throw new Error('候选包含无法从授权补丁及可信目录重建的派生变化');
    const derived = actual.filter(path => !paths.has(path));
    if (!equalPaths(derived, candidate.derivedChangedPaths)) throw new Error('候选自动同步字段与实际派生变化不一致');
    for (const entry of candidate.differences) {
      let aid: string, field: CandidateDifferenceField, before: string | undefined, after: string | undefined;
      const activityMatch = /^\/entities\/activities\/(activity-[a-f0-9]+)\/(description|startTime|placeId|title|period)$/.exec(entry.path);
      if (activityMatch) {
        aid = activityMatch[1]; field = activityMatch[2] as CandidateDifferenceField;
        const key = field as keyof TripDocument['entities']['activities'][string];
        before = current.entities.activities[aid]?.[key]; after = candidate.document.entities.activities[aid]?.[key];
      } else {
        const componentMatch = /^\/ui\/components\/([^/]+)\/parentId$/.exec(entry.path);
        const oldCard = componentMatch ? current.ui.components[componentMatch[1]] : undefined;
        const newCard = componentMatch ? candidate.document.ui.components[componentMatch[1]] : undefined;
        if (!oldCard || oldCard.kind !== 'ActivityCard' || !oldCard.entityId || !newCard) throw new Error('候选派生组件差异无效');
        aid = oldCard.entityId; field = 'parentId'; before = oldCard.parentId; after = newCard.parentId;
      }
      if (entry.activityId !== aid || entry.field !== field || entry.before !== before || entry.after !== after || entry.title !== current.entities.activities[aid]?.title) throw new Error('候选差异条目与正式绑定不一致');
    }
  }
  for(const candidate of batch.candidates)if(candidate.a2ui!==undefined)parseSurface(candidate.a2ui,candidate,batch.effectiveScope.eligibleActivityIds);
  return batch;
}
