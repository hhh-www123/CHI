import { z } from 'zod';
import type { TripDocument } from './types';

const id = z.string().min(1).max(160);
const activity = z.object({
  id, dayId: id, period: z.enum(['am', 'pm', 'evening']), title: z.string().min(1),
  placeId: id, startTime: z.string().regex(/^(?:[01]\d|2[0-3]):[0-5]\d$/),
  description: z.string().max(2000),
}).strict();
const day = z.object({ id, label: z.string().min(1), date: z.iso.date(), activityIds: z.array(id) }).strict();
const component = z.object({
  id, kind: z.enum(['DayCard', 'PeriodGroup', 'ActivityCard']), entityId: id,
  parentId: id.optional(), props: z.object({}).strict(),
}).strict();

export const tripDocumentSchema = z.object({
  schemaVersion: z.literal('1.0'), tripId: id, version: z.number().int().min(1),
  title: z.string().min(1), budgetLimit: z.number().finite().min(100).max(100000),
  dayOrder: z.array(id).min(1).max(14),
  entities: z.object({ days: z.record(id, day), activities: z.record(id, activity) }).strict(),
  ui: z.object({ componentCatalogVersion: z.literal('1.0'), components: z.record(id, component) }).strict(),
}).strict().superRefine((document, context) => {
  const fail = (message: string) => context.addIssue({ code: 'custom', message });
  if (new Set(document.dayOrder).size !== document.dayOrder.length ||
      Object.keys(document.entities.days).length !== document.dayOrder.length) fail('日期顺序与实体不一致');
  const seen = new Set<string>();
  for (const dayId of document.dayOrder) {
    const value = document.entities.days[dayId];
    if (!value || value.id !== dayId) { fail('日期实体引用无效'); continue; }
    for (const activityId of value.activityIds) {
      const item = document.entities.activities[activityId];
      if (!item || item.id !== activityId || item.dayId !== dayId || seen.has(activityId)) fail('活动实体引用无效');
      seen.add(activityId);
    }
  }
  if (seen.size !== Object.keys(document.entities.activities).length) fail('存在未绑定的活动');
  for (const [key, value] of Object.entries(document.ui.components)) {
    if (value.id !== key || (value.parentId && !document.ui.components[value.parentId])) fail('组件绑定无效');
    const entities = value.kind === 'ActivityCard' ? document.entities.activities : document.entities.days;
    if (!entities[value.entityId]) fail('组件实体无效');
  }
});

// Client validation is an early rendering check, never a write permission.
// The server re-resolves registry paths and validates again before committing.
export function parseTripDocument(value: unknown): TripDocument {
  return tripDocumentSchema.parse(value);
}
