import type { PersistedConstraints, TripDocument } from './types';

export interface VersionSummary {
  tripId: string; version: number; parentVersion: number | null; baseVersion: number | null;
  createdAt: string; kind: string; summary: string; userRequest: string;
  selectedComponentIds: string[]; candidateId: string | null; changedPaths: string[]; allowedPaths: string[];
  validationReport: Record<string, unknown>; constraintsSnapshot: PersistedConstraints;
  restoreFromVersion?: number | null;
}
export interface VersionList { tripId: string; currentVersion: number; versions: VersionSummary[] }
export interface VersionRecord extends VersionSummary {
  documentSnapshot: TripDocument;
  payloadSnapshot: Record<string, unknown>;
}
export interface VersionDiff {
  fromVersion: number; toVersion: number; changedPaths: string[];
  differences: { path: string; before?: unknown; after?: unknown }[];
}
export type DisplayChange = { view?: 'cards' | 'timeline'; compact?: boolean };
