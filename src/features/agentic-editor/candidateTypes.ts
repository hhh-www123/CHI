import type { EditorField, ReplaceOperation, TripDocument } from './types';
export type CandidateStrategy = 'minimal_change' | 'efficiency_first' | 'experience_first';
export type CandidateAgentMode = 'mock' | 'llm';
export interface CandidateAgentStatus {
  defaultMode: CandidateAgentMode; llmConfigured: boolean; model: string; provider: string;
  timeoutSeconds: number; candidateTtlSeconds: number; phase: string;
}
export type CandidateDifferenceField = EditorField | 'title' | 'period' | 'parentId';
export interface CandidateDifference { path: string; before: string; after: string; activityId: string; title: string; field: CandidateDifferenceField }
export interface CandidateValidation {
  valid: true; schema: true; scope: true; locks: true; invariants: true; leafDiff: true; legacyBusinessDiff: true;
}
export interface CandidateEvaluation {method:'rules-v1';metrics:Record<string,number>;changeCost:number;score:number;weights:Record<string,number>;rank:number;evidence:string[]}
export interface EditCandidate {
  evaluation?: CandidateEvaluation; a2ui?: unknown;
  candidateId: string; baseVersion: number; strategy: CandidateStrategy; label: string; rationale: string;
  patch: ReplaceOperation[]; document: TripDocument; changedPaths: string[]; derivedChangedPaths: string[]; differences: CandidateDifference[];
  validationReport: CandidateValidation; expiresAt: string;
}
export interface CandidateBatch {
  requestId: string; baseVersion: number; generationMode: CandidateAgentMode; requestedCount: number; candidateCount: number;
  effectiveScope: {
    selectedComponentIds: string[]; allowedPaths: string[]; lockedComponentIds: string[];
    eligibleActivityIds: string[]; editableFields: EditorField[]; labels: string[];
    selectedLocked: boolean; phase: string; restriction: string;
  };
  candidates: EditCandidate[];
  rejectedCandidates: { strategy: string; code: string; message: string }[];
  diagnostics: string[];
}
