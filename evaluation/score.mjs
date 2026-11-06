#!/usr/bin/env node
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import {
  average,
  candidateMetrics,
  conflictMetrics,
  constraintRetentionByAge,
  firstDriftRound,
  goalRetentionByAge,
  normalizedRetentionAuc,
  passHatK,
  summarizeEpisode,
} from './metrics.mjs';

const input = process.argv[2];
const output = process.argv[3] ?? null;
if (!input) {
  console.error('Usage: node evaluation/score.mjs path/to/dataset.json [output.json]');
  process.exit(2);
}

const dataset = JSON.parse(await readFile(input, 'utf8'));
if (dataset?.schemaVersion !== '1.0' || !Array.isArray(dataset.episodes)) {
  throw new TypeError('dataset requires schemaVersion 1.0 and episodes[]');
}
if (dataset.formalExperiment === true) {
  if (dataset.realModel !== true || dataset.annotationStats?.formalReady !== true) {
    throw new TypeError('formal datasets require realModel:true and formal-ready annotationStats');
  }
  if (dataset.episodes.some(item => typeof item.runId !== 'string' || !item.runId || typeof item.modelKey !== 'string' || !item.modelKey)) {
    throw new TypeError('formal episodes require runId and modelKey provenance');
  }
  const canonicalize = value => {
    if (Array.isArray(value)) return value.map(canonicalize);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
    return value;
  };
  const sha256 = value => createHash('sha256').update(value).digest('hex');
  const canonicalHash = value => sha256(JSON.stringify(canonicalize(value)));
  if (
    dataset.generationMode !== 'real-model-formal-runner'
    || !/^[a-f0-9]{64}$/.test(dataset.manifestHash ?? '')
    || !dataset.analysisContract
    || dataset.analysisContractHash !== canonicalHash(dataset.analysisContract)
    || dataset.analysisContract.scoringScriptHash !== sha256(await readFile(import.meta.filename))
    || dataset.analysisContract.metricsModuleHash !== sha256(await readFile(new URL('./metrics.mjs', import.meta.url)))
  ) {
    throw new TypeError('formal dataset scoring/metrics provenance does not match the running assets');
  }
}

const booleanOrNull = value => typeof value === 'boolean' ? value : null;
const runMetadata = {
  generatedAt: typeof dataset.generatedAt === 'string' ? dataset.generatedAt : null,
  illustrative: booleanOrNull(dataset.illustrative),
  pilot: booleanOrNull(dataset.pilot),
  formalExperiment: booleanOrNull(dataset.formalExperiment),
  generationMode: typeof dataset.generationMode === 'string' ? dataset.generationMode : null,
  realModel: booleanOrNull(dataset.realModel),
  realMap: booleanOrNull(dataset.realMap),
  annotationFormalReady: booleanOrNull(dataset.annotationStats?.formalReady),
  notes: Array.isArray(dataset.notes) ? dataset.notes : [],
};

const nullableAverage = values => average(values.filter(value => typeof value === 'number' && Number.isFinite(value)));
const retentionCurve = (episodes, metric) => {
  const result = new Map();
  for (const episode of episodes) {
    for (const [age, value] of Object.entries(metric(episode))) {
      const item = result.get(age) ?? { satisfiedWeight: 0, totalWeight: 0, observations: 0 };
      item.satisfiedWeight += value.satisfiedWeight;
      item.totalWeight += value.totalWeight;
      item.observations += value.observations;
      result.set(age, item);
    }
  }
  if (!result.size) return null;
  return Object.fromEntries([...result.entries()].sort((left, right) => Number(left[0]) - Number(right[0])).map(([age, item]) => [age, {
    retention: item.totalWeight ? item.satisfiedWeight / item.totalWeight : null,
    observations: item.observations,
  }]));
};
const driftSurvival = episodes => {
  const observations = episodes.map(episode => ({
    length: episode.rounds.length,
    event: firstDriftRound(episode),
  }));
  const maxRound = Math.max(0, ...observations.map(item => item.length));
  const result = {};
  let survival = 1;
  for (let round = 1; round <= maxRound; round += 1) {
    const atRisk = observations.filter(item => item.length >= round && (item.event === null || item.event >= round)).length;
    const events = observations.filter(item => item.event === round).length;
    if (atRisk) survival *= 1 - events / atRisk;
    result[round] = { survival, atRisk, events };
  }
  return result;
};
const groups = new Map();
for (const episode of dataset.episodes) {
  if (typeof episode.method !== 'string' || !episode.method) throw new TypeError('each episode requires method');
  const items = groups.get(episode.method) ?? [];
  items.push(episode);
  groups.set(episode.method, items);
}

const summarizeGroups = groupedEpisodes => {
const result = {};
for (const [method, episodes] of groupedEpisodes) {
  const summaries = episodes.map(summarizeEpisode);
  const recovery = summaries.map(item => item.recovery).filter(Boolean);
  const drifted = summaries.filter(item => !item.noDrift);
  const allRounds = episodes.flatMap(item => item.rounds);
  const candidateBatches = allRounds
    .filter(item => Array.isArray(item.candidates))
    .map(item => candidateMetrics(item.candidates, item.candidateTargetCount ?? null));
  const targetedCandidateBatches = candidateBatches.filter(item => item.candidateTargetCount !== null);
  const multiCandidateOpportunities = targetedCandidateBatches.filter(item => item.candidateTargetCount >= 3);
  const staleGoalsTested = summaries.reduce((sum, item) => sum + item.staleGoalsTested, 0);
  const staleGoalsLeaked = summaries.reduce((sum, item) => sum + item.staleGoalsLeaked, 0);
  const actualChangedPathCount = summaries.reduce((sum, item) => sum + item.actualChangedPathCount, 0);
  const unauthorizedChangedPathCount = summaries.reduce((sum, item) => sum + item.unauthorizedChangedPathCount, 0);
  const candidateProposals = candidateBatches.reduce((sum, item) => sum + item.proposed, 0);
  const qualifiedCandidates = candidateBatches.reduce((sum, item) => sum + item.valid, 0);
  const goalCurve = retentionCurve(episodes, goalRetentionByAge);
  const constraintCurve = retentionCurve(episodes, constraintRetentionByAge);
  const conflict = conflictMetrics(allRounds);
  const versionRecovery = episodes.map(item => item.versionRecovery).filter(Boolean);
  const goalUpdates = episodes.map(item => item.goalUpdate).filter(Boolean);
  const turnRate = assessment => nullableAverage(allRounds
    .filter(item => item.turnGoal?.assessment === assessment)
    .map(item => item.turnGoal.satisfied ? 1 : 0));
  result[method] = {
    episodes: episodes.length,
    taskCount: new Set(episodes.map(item => item.templateId ?? item.taskId.replace(/-L\d+$/, ''))).size,
    conditionCount: new Set(episodes.map(item => item.taskId)).size,
    rounds: allRounds.length,
    taskSuccessRate: average(episodes.map(item => item.success ? 1 : 0)),
    episodeUnauthorizedRate: average(summaries.map(item => item.episodeUnauthorized ? 1 : 0)),
    actualChangedPathCount,
    unauthorizedChangedPathCount,
    unauthorizedRateByChangedPaths: actualChangedPathCount ? unauthorizedChangedPathCount / actualChangedPathCount : 0,
    meanScopePrecision: nullableAverage(summaries.map(item => item.meanScopePrecision)),
    meanScopeRecall: nullableAverage(summaries.map(item => item.meanScopeRecall)),
    meanScopeF1: nullableAverage(summaries.map(item => item.meanScopeF1)),
    meanTurnGoalCompletion: nullableAverage(summaries.map(item => item.meanTurnGoalCompletion)),
    humanBlindedTurnGoalCompletion: turnRate('human_blinded'),
    structuralProxyTurnGoalCompletion: turnRate('structural_proxy'),
    trustedSetupTurnGoalCompletion: turnRate('trusted_setup'),
    meanComponentIdSurvivalRate: nullableAverage(summaries.map(item => item.meanComponentIdSurvivalRate)),
    meanActivityIdSurvivalRate: nullableAverage(summaries.map(item => item.meanActivityIdSurvivalRate)),
    systemIdentityOverrideAttemptCount: summaries.reduce((sum, item) => sum + item.systemIdentityOverrideAttemptCount, 0),
    episodeSystemIdentityOverrideAttemptRate: average(summaries.map(item => item.systemIdentityOverrideAttemptCount ? 1 : 0)),
    conflict,
    versionRecoveryEpisodeCount: versionRecovery.length,
    versionRecoverySuccessRate: versionRecovery.length ? average(versionRecovery.map(item => item.success ? 1 : 0)) : null,
    totalVersionRecoveryResidualErrors: versionRecovery.length ? versionRecovery.reduce((sum, item) => sum + item.residualErrors, 0) : null,
    goalUpdateEpisodeCount: goalUpdates.length,
    goalUpdateSuccessRate: goalUpdates.length ? average(goalUpdates.map(item => item.success ? 1 : 0)) : null,
    meanGoalRetentionAuc: nullableAverage(summaries.map(item => item.goalRetentionAuc)),
    meanConstraintRetentionAuc: nullableAverage(summaries.map(item => item.constraintRetentionAuc)),
    meanCumulativeGoalDrift: nullableAverage(summaries.map(item => item.cumulativeGoalDrift)),
    meanStaleGoalLeakageRate: nullableAverage(summaries.map(item => item.staleGoalLeakageRate)),
    staleGoalsTested,
    staleGoalsLeaked,
    staleGoalLeakageRate: staleGoalsTested ? staleGoalsLeaked / staleGoalsTested : null,
    goalRetentionByAge: goalCurve,
    constraintRetentionByAge: constraintCurve,
    goalRetentionCurveAuc: normalizedRetentionAuc(goalCurve ?? {}),
    constraintRetentionCurveAuc: normalizedRetentionAuc(constraintCurve ?? {}),
    noDriftRate: average(summaries.map(item => item.noDrift ? 1 : 0)),
    meanFirstDriftRoundAmongDrifted: nullableAverage(drifted.map(item => item.firstDriftRound)),
    driftSurvival: driftSurvival(episodes),
    passHat1: passHatK(episodes, 1),
    passHat3: passHatK(episodes, 3),
    passHat5: passHatK(episodes, 5),
    recoveryEpisodeCount: recovery.length,
    recoverySuccessRate: recovery.length ? average(recovery.map(item => item.success ? 1 : 0)) : null,
    meanRecoveryRounds: nullableAverage(recovery.map(item => item.recoveryRoundsFromInjection)),
    meanRecoveryDetectionDelayRounds: nullableAverage(recovery.map(item => item.detectionDelayRounds)),
    meanRecoveryRoundsAfterDetection: nullableAverage(recovery.map(item => item.recoveryRoundsAfterDetection)),
    meanRecoveryClicks: nullableAverage(recovery.map(item => item.clicks)),
    meanRecoveryElapsedMs: nullableAverage(recovery.map(item => item.elapsedMs)),
    totalRecoveryResidualErrors: recovery.length
      ? recovery.reduce((sum, item) => sum + item.residualErrors, 0)
      : null,
    candidateBatchCount: candidateBatches.length,
    candidateProposals,
    qualifiedCandidates,
    pooledCandidateQualifiedRate: candidateProposals ? qualifiedCandidates / candidateProposals : null,
    meanCandidateProposalsPerBatch: candidateBatches.length ? candidateProposals / candidateBatches.length : null,
    meanQualifiedCandidatesPerBatch: candidateBatches.length ? qualifiedCandidates / candidateBatches.length : null,
    threeQualifiedCandidateBatchRate: candidateBatches.length
      ? average(candidateBatches.map(item => item.valid >= 3 ? 1 : 0))
      : null,
    candidateTargetBatchCount: targetedCandidateBatches.length,
    meanCandidateTargetFulfillment: nullableAverage(targetedCandidateBatches.map(item => item.targetFulfillment)),
    candidateTargetMetRate: targetedCandidateBatches.length
      ? average(targetedCandidateBatches.map(item => item.targetMet ? 1 : 0))
      : null,
    multiCandidateOpportunityCount: multiCandidateOpportunities.length,
    multiCandidateTargetMetRate: multiCandidateOpportunities.length
      ? average(multiCandidateOpportunities.map(item => item.targetMet ? 1 : 0))
      : null,
    meanCandidateQualifiedRate: nullableAverage(candidateBatches.map(item => item.qualifiedRate)),
    meanCandidateUniqueOutcomeRate: nullableAverage(candidateBatches.map(item => item.uniqueOutcomeRate)),
    meanCandidateGoalCoverage: nullableAverage(candidateBatches.map(item => item.meanGoalCoverage)),
    meanCandidateNdcg: nullableAverage(candidateBatches.map(item => item.ndcg)),
    meanCandidateTop1Regret: nullableAverage(candidateBatches.map(item => item.top1Regret)),
    candidateTop1HumanBestRate: nullableAverage(candidateBatches.map(item => item.top1HumanBest === null ? null : item.top1HumanBest ? 1 : 0)),
    meanLatencyMs: nullableAverage(allRounds.map(item => item.latencyMs)),
    meanInputTokens: nullableAverage(allRounds.map(item => item.inputTokens)),
    meanOutputTokens: nullableAverage(allRounds.map(item => item.outputTokens)),
    totalApiCostUsd: allRounds.some(item => typeof item.apiCostUsd === 'number')
      ? allRounds.reduce((sum, item) => sum + (typeof item.apiCostUsd === 'number' ? item.apiCostUsd : 0), 0)
      : null,
  };
}
return result;
};

const methods = summarizeGroups(groups);
const episodesByModel = new Map();
for (const episode of dataset.episodes) {
  if (episode.modelKey == null) continue;
  if (typeof episode.modelKey !== 'string' || !episode.modelKey) throw new TypeError('modelKey must be a non-empty string when present');
  const modelEpisodes = episodesByModel.get(episode.modelKey) ?? [];
  modelEpisodes.push(episode);
  episodesByModel.set(episode.modelKey, modelEpisodes);
}
const byModel = Object.fromEntries([...episodesByModel.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([modelKey, episodes]) => {
  const modelGroups = new Map();
  for (const episode of episodes) {
    const items = modelGroups.get(episode.method) ?? [];
    items.push(episode);
    modelGroups.set(episode.method, items);
  }
  return [modelKey, summarizeGroups(modelGroups)];
}));

const provenance = {
  experimentId: typeof dataset.experimentId === 'string' ? dataset.experimentId : null,
  manifestHash: typeof dataset.manifestHash === 'string' ? dataset.manifestHash : null,
  analysisContractHash: typeof dataset.analysisContractHash === 'string' ? dataset.analysisContractHash : null,
  benchmarkHash: typeof dataset.benchmarkHash === 'string' ? dataset.benchmarkHash : null,
  benchmarkSourceHash: typeof dataset.benchmarkSourceHash === 'string' ? dataset.benchmarkSourceHash : null,
  promptHash: typeof dataset.promptHash === 'string' ? dataset.promptHash : null,
};
const serialized = `${JSON.stringify({ schemaVersion: '1.0', source: input, provenance, runMetadata, methods, byModel }, null, 2)}\n`;
if (output) await writeFile(output, serialized, { encoding: 'utf8', flag: 'wx' });
else process.stdout.write(serialized);
