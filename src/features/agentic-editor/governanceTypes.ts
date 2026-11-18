export type RequirementKind =
  | 'preference'
  | 'keep_hotel'
  | 'keep_component'
  | 'cost_ceiling';
export interface Requirement {
  id: string;
  text: string;
  kind: RequirementKind;
  status: 'active' | 'superseded' | 'retired';
  introducedVersion: number;
  endedVersion?: number;
  createdAt: string;
  sourceInstruction: string;
  scopeLabel: string;
  componentId?: string | null;
  maxCost?: number;
  replaces?: string | null;
  supersededBy?: string;
}
export interface RequirementChange {
  action: 'add' | 'replace' | 'retire';
  confirm: true;
  requirementId?: string;
  text?: string;
  kind?: RequirementKind;
  componentId?: string | null;
  maxCost?: number | null;
  sourceInstruction?: string;
}
export interface RequirementReview {
  requirementId: string;
  targetVersion: number;
  verdict: 'satisfied' | 'unsatisfied';
  note: string;
  reviewedAt: string;
  recordedVersion: number;
}
export interface RequirementCheck {
  id: string;
  text: string;
  kind: RequirementKind;
  scopeLabel: string;
  introducedVersion: number;
  status: 'passed' | 'failed' | 'manual';
  detail: string;
  review?: RequirementReview | null;
}
export interface FidelityRow {
  version: number;
  baseVersion: number;
  kind: string;
  summary: string;
  userRequest: string;
  createdAt: string;
  changedPaths: string[];
  checks: { name: string; passed: boolean }[];
  requirements: RequirementCheck[];
  unauthorizedPaths: string[] | null;
  protectedLeafCount: number | null;
  preservedLeafCount: number | null;
  stateRetentionRate: number | null;
  scopeEvidence: string;
  lockedStatePreserved: boolean | null;
  identityPreserved: boolean | null;
}
export interface FidelityReport {
  tripId: string;
  currentVersion: number;
  rows: FidelityRow[];
  summary: {
    versionCount: number;
    verifiedScopedEdits: number;
    unauthorizedPathCount: number | null;
    stateRetentionRate: number | null;
    functionalChecksPassed: number;
    functionalChecksTotal: number;
  };
}
