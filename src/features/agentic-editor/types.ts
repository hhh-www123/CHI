import type { DayKey, RegionId } from '../../types';

export type EditorField = 'description' | 'startTime' | 'placeId';
export type PeriodKey = 'am' | 'pm' | 'evening';
export interface DocumentActivity {
  id: string;
  dayId: string;
  period: PeriodKey;
  title: string;
  placeId: string;
  startTime: string;
  description: string;
}
export interface TripDocument {
  schemaVersion: '1.0';
  tripId: string;
  version: number;
  title: string;
  budgetLimit: number;
  dayOrder: string[];
  entities: {
    days: Record<string, { id: string; label: string; date: string; activityIds: string[] }>;
    activities: Record<string, DocumentActivity>;
  };
  ui: {
    componentCatalogVersion: string;
    components: Record<string, { id: string; kind: string; entityId?: string; parentId?: string; props: Record<string, unknown> }>;
  };
}
export interface ComponentBinding {
  componentId: string;
  kind: string;
  label: string;
  entityId: string;
  parentId?: string;
  regionId: RegionId;
  dayKey: DayKey;
  stopId?: string;
  period?: PeriodKey;
  editablePaths: string[];
}
export interface PersistedConstraints {
  lockedComponentIds: string[];
  hardConstraints: { id: string; kind: string; active: boolean; targetId?: string; value?: unknown }[];
}
export interface EditorScope {
  selectedComponentId: string;
  selectedComponentIds?: string[];
  label: string;
  allowedPaths: string[];
  lockedComponentIds: string[];
  editableFields: EditorField[];
  eligibleActivityIds: string[];
  labels?: string[];
  locked: boolean;
  blockedReason?: string;
  phase: string;
}
export interface ReplaceOperation { op: 'replace'; path: string; value: string }
export interface PatchPreview {
  previewId: string;
  baseVersion: number;
  document: TripDocument;
  changedPaths: string[];
  derivedChangedPaths?: string[];
  generationMode?: 'mock';
}
