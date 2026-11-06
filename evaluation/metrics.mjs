const asArray = (value, name) => {
  if (!Array.isArray(value)) throw new TypeError(`${name} must be an array`);
  return value;
};

const finiteNumber = (value, name) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
};

const average = values => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;

const uniqueStrings = (values, name) => {
  const result = new Set();
  for (const value of asArray(values, name)) {
    if (typeof value !== 'string' || !value) throw new TypeError(`${name} entries must be non-empty strings`);
    result.add(value);
  }
  return result;
};

export function pathMetrics(round) {
  const desired = uniqueStrings(round.desiredPaths ?? [], 'desiredPaths');
  const actual = uniqueStrings(round.actualChangedPaths ?? [], 'actualChangedPaths');
  const unauthorized = uniqueStrings(round.unauthorizedChangedPaths ?? [], 'unauthorizedChangedPaths');
  for (const path of unauthorized) {
    if (!actual.has(path)) throw new TypeError('unauthorizedChangedPaths must be a subset of actualChangedPaths');
  }
  const truePositive = [...actual].filter(path => desired.has(path)).length;
  const precision = actual.size ? truePositive / actual.size : 1;
  const recall = desired.size ? truePositive / desired.size : 1;
  const f1 = precision + recall ? 2 * precision * recall / (precision + recall) : 0;
  return {
    desired: desired.size,
    actual: actual.size,
    truePositive,
    falsePositive: actual.size - truePositive,
    falseNegative: desired.size - truePositive,
    precision,
    recall,
    f1,
    unauthorizedCount: unauthorized.size,
    unauthorizedRateByChangedPaths: actual.size ? unauthorized.size / actual.size : 0,
  };
}

function weightedRetention(items, name) {
  asArray(items, name);
  if (!items.length) return null;
  let total = 0;
  let satisfied = 0;
  for (const item of items) {
    if (!item || typeof item.id !== 'string' || !item.id || typeof item.satisfied !== 'boolean') {
      throw new TypeError(`${name} entries require id and boolean satisfied`);
    }
    const weight = item.weight === undefined ? 1 : finiteNumber(item.weight, `${name}.weight`);
    if (weight <= 0) throw new TypeError(`${name}.weight must be positive`);
    total += weight;
    if (item.satisfied) satisfied += weight;
  }
  return satisfied / total;
}

export const goalRetention = round => weightedRetention(
  asArray(round.activeGoals ?? [], 'activeGoals').filter(item => item?.retentionEligible !== false),
  'activeGoals',
);
export const constraintRetention = round => weightedRetention(round.activeConstraints ?? [], 'activeConstraints');
export function turnGoalCompletion(round) {
  if (round.turnGoal == null) return null;
  if (!round.turnGoal || typeof round.turnGoal.id !== 'string' || !round.turnGoal.id || typeof round.turnGoal.satisfied !== 'boolean') {
    throw new TypeError('turnGoal requires id and boolean satisfied');
  }
  return round.turnGoal.satisfied ? 1 : 0;
}

export function normalizedRetentionAuc(curve) {
  if (curve == null || typeof curve !== 'object' || Array.isArray(curve)) {
    throw new TypeError('retention curve must be an object');
  }
  const points = Object.entries(curve).map(([age, item]) => {
    const numericAge = Number(age);
    if (!Number.isInteger(numericAge) || numericAge < 0 || !item || typeof item.retention !== 'number' || !Number.isFinite(item.retention) || item.retention < 0 || item.retention > 1) {
      throw new TypeError('retention curve requires non-negative integer ages and retention in [0, 1]');
    }
    return [numericAge, item.retention];
  }).sort((left, right) => left[0] - right[0]);
  if (!points.length) return null;
  if (points.length === 1) return points[0][1];
  let area = 0;
  for (let index = 1; index < points.length; index += 1) {
    const [leftAge, leftValue] = points[index - 1];
    const [rightAge, rightValue] = points[index];
    area += (rightAge - leftAge) * (leftValue + rightValue) / 2;
  }
  const span = points.at(-1)[0] - points[0][0];
  return span ? area / span : points[0][1];
}

export const retentionAuc = episode => normalizedRetentionAuc(goalRetentionByAge(episode));
export const constraintRetentionAuc = episode => normalizedRetentionAuc(constraintRetentionByAge(episode));

export function cumulativeGoalDrift(episode) {
  const auc = retentionAuc(episode);
  return auc === null ? null : 1 - auc;
}

function retentionByAge(episode, key, name, requireIntroducedRound, filter = () => true) {
  const buckets = new Map();
  for (const round of asArray(episode.rounds, 'rounds')) {
    const roundNumber = finiteNumber(round.round, 'round');
    for (const item of asArray(round[key] ?? [], name).filter(filter)) {
      if (!item || typeof item.id !== 'string' || !item.id || typeof item.satisfied !== 'boolean') {
        throw new TypeError(`${name} entries require id and boolean satisfied`);
      }
      if (item.introducedRound === undefined && !requireIntroducedRound) continue;
      const introduced = finiteNumber(item.introducedRound, `${name}.introducedRound`);
      const age = roundNumber - introduced;
      if (!Number.isInteger(age) || age < 0) throw new TypeError(`${name} age must be a non-negative integer`);
      const weight = item.weight === undefined ? 1 : finiteNumber(item.weight, `${name}.weight`);
      if (weight <= 0) throw new TypeError(`${name}.weight must be positive`);
      const bucket = buckets.get(age) ?? { satisfiedWeight: 0, totalWeight: 0, observations: 0 };
      bucket.totalWeight += weight;
      bucket.satisfiedWeight += item.satisfied ? weight : 0;
      bucket.observations += 1;
      buckets.set(age, bucket);
    }
  }
  return Object.fromEntries([...buckets.entries()].sort((a, b) => a[0] - b[0]).map(([age, value]) => [age, {
    retention: value.satisfiedWeight / value.totalWeight,
    observations: value.observations,
    satisfiedWeight: value.satisfiedWeight,
    totalWeight: value.totalWeight,
  }]));
}

export const goalRetentionByAge = episode => retentionByAge(episode, 'activeGoals', 'activeGoals', true, item => item?.retentionEligible !== false);
export const constraintRetentionByAge = episode => retentionByAge(episode, 'activeConstraints', 'activeConstraints', false);

export function staleGoalLeakage(episode) {
  let tested = 0;
  let leaked = 0;
  for (const round of asArray(episode.rounds, 'rounds')) {
    for (const goal of asArray(round.retiredGoals ?? [], 'retiredGoals')) {
      if (!goal || typeof goal.id !== 'string' || typeof goal.leaked !== 'boolean') {
        throw new TypeError('retiredGoals entries require id and boolean leaked');
      }
      tested += 1;
      leaked += goal.leaked ? 1 : 0;
    }
  }
  return { tested, leaked, rate: tested ? leaked / tested : null };
}

export function firstDriftRound(episode) {
  for (const round of asArray(episode.rounds, 'rounds')) {
    const goals = goalRetention(round);
    const constraints = constraintRetention(round);
    const turn = turnGoalCompletion(round);
    const paths = round.scopeApplicable === false ? null : pathMetrics(round);
    if ((goals !== null && goals < 1) || (constraints !== null && constraints < 1) || (turn !== null && turn < 1) || (paths !== null && paths.unauthorizedCount) || round.semanticFailure === true || (typeof round.componentIdSurvivalRate === 'number' && round.componentIdSurvivalRate < 1)) {
      return round.round;
    }
  }
  return null;
}

export function recoveryMetrics(recovery) {
  if (recovery == null) return null;
  const seeded = finiteNumber(recovery.seededAtRound, 'recovery.seededAtRound');
  const detected = recovery.detectedAtRound == null ? null : finiteNumber(recovery.detectedAtRound, 'recovery.detectedAtRound');
  const recovered = recovery.recoveredAtRound == null ? null : finiteNumber(recovery.recoveredAtRound, 'recovery.recoveredAtRound');
  const residualErrors = recovery.residualErrors == null ? 0 : finiteNumber(recovery.residualErrors, 'recovery.residualErrors');
  return {
    success: detected !== null && recovered !== null && residualErrors === 0,
    detectionDelayRounds: detected === null ? null : detected - seeded,
    recoveryRoundsFromInjection: recovered === null ? null : recovered - seeded,
    recoveryRoundsAfterDetection: recovered === null || detected === null ? null : recovered - detected,
    residualErrors,
    clicks: recovery.clicks ?? null,
    elapsedMs: recovery.elapsedMs ?? null,
  };
}

const choose = (n, k) => {
  if (k < 0 || n < k) return 0;
  const m = Math.min(k, n - k);
  let value = 1;
  for (let index = 1; index <= m; index += 1) value = value * (n - m + index) / index;
  return value;
};

export function passHatK(episodes, k) {
  asArray(episodes, 'episodes');
  if (!Number.isInteger(k) || k < 1) throw new TypeError('k must be a positive integer');
  const tasks = new Map();
  for (const episode of episodes) {
    if (!episode || typeof episode.taskId !== 'string' || typeof episode.success !== 'boolean') {
      throw new TypeError('episodes require taskId and boolean success');
    }
    const item = tasks.get(episode.taskId) ?? { trials: 0, successes: 0 };
    item.trials += 1;
    item.successes += episode.success ? 1 : 0;
    tasks.set(episode.taskId, item);
  }
  const eligible = [...tasks.entries()].filter(([, item]) => item.trials >= k);
  if (!eligible.length) return null;
  const byTask = Object.fromEntries(eligible.map(([taskId, item]) => [taskId, {
    ...item,
    value: choose(item.successes, k) / choose(item.trials, k),
  }]));
  return { k, value: average(Object.values(byTask).map(item => item.value)), taskCount: eligible.length, byTask };
}

const dcg = relevance => relevance.reduce((sum, value, index) => sum + (2 ** value - 1) / Math.log2(index + 2), 0);

export function candidateMetrics(candidates, candidateTargetCount = null) {
  asArray(candidates, 'candidates');
  if (candidateTargetCount !== null && (!Number.isInteger(candidateTargetCount) || candidateTargetCount < 1)) {
    throw new TypeError('candidateTargetCount must be a positive integer');
  }
  if (!candidates.length) return {
    proposed: 0,
    valid: 0,
    qualifiedRate: null,
    uniqueOutcomeRate: null,
    meanGoalCoverage: null,
    ndcg: null,
    top1Regret: null,
    top1HumanBest: null,
    candidateTargetCount,
    targetFulfillment: candidateTargetCount === null ? null : 0,
    targetMet: candidateTargetCount === null ? null : false,
  };
  const valid = candidates.filter(candidate => candidate?.valid === true);
  const signatures = new Set(valid.map(candidate => {
    if (typeof candidate.outcomeSignature !== 'string' || !candidate.outcomeSignature) {
      throw new TypeError('valid candidates require outcomeSignature');
    }
    return candidate.outcomeSignature;
  }));
  const coverage = valid.map(candidate => candidate.goalCoverage).filter(value => typeof value === 'number' && Number.isFinite(value));
  const ranked = valid.filter(candidate => Number.isInteger(candidate.systemRank) && typeof candidate.humanRelevance === 'number' && Number.isFinite(candidate.humanRelevance));
  let ndcg = null;
  let top1Regret = null;
  let top1HumanBest = null;
  if (ranked.length) {
    const ordered = [...ranked].sort((left, right) => left.systemRank - right.systemRank);
    const system = ordered.map(item => item.humanRelevance);
    const ideal = [...system].sort((left, right) => right - left);
    const denominator = dcg(ideal);
    ndcg = denominator ? dcg(system) / denominator : 1;
    if (ordered[0].systemRank === 1) {
      top1Regret = Math.max(...system) - ordered[0].humanRelevance;
      top1HumanBest = typeof ordered[0].humanBest === 'boolean' ? ordered[0].humanBest : null;
    }
  }
  return {
    proposed: candidates.length,
    valid: valid.length,
    qualifiedRate: valid.length / candidates.length,
    uniqueOutcomes: signatures.size,
    uniqueOutcomeRate: valid.length ? signatures.size / valid.length : null,
    meanGoalCoverage: average(coverage),
    ndcg,
    top1Regret,
    top1HumanBest,
    candidateTargetCount,
    targetFulfillment: candidateTargetCount === null ? null : Math.min(1, valid.length / candidateTargetCount),
    targetMet: candidateTargetCount === null ? null : valid.length >= candidateTargetCount,
  };
}

export function conflictMetrics(rounds) {
  asArray(rounds, 'rounds');
  const usable = rounds.filter(item => ['accept', 'clarify', 'reject'].includes(item?.groundTruthDecision));
  if (!usable.length) return null;
  let truePositive = 0;
  let falsePositive = 0;
  let falseNegative = 0;
  let trueNegative = 0;
  let exact = 0;
  const isConflict = value => value === 'clarify' || value === 'reject';
  for (const round of usable) {
    if (!['accept', 'clarify', 'reject', 'not_applicable'].includes(round.systemDecision)) {
      throw new TypeError('systemDecision is invalid');
    }
    const truth = isConflict(round.groundTruthDecision);
    const predicted = isConflict(round.systemDecision);
    if (truth && predicted) truePositive += 1;
    else if (!truth && predicted) falsePositive += 1;
    else if (truth) falseNegative += 1;
    else trueNegative += 1;
    exact += round.groundTruthDecision === round.systemDecision ? 1 : 0;
  }
  const precision = truePositive + falsePositive ? truePositive / (truePositive + falsePositive) : null;
  const recall = truePositive + falseNegative ? truePositive / (truePositive + falseNegative) : null;
  const f1 = precision === null || recall === null || precision + recall === 0 ? (precision === 0 && recall === 0 ? 0 : null) : 2 * precision * recall / (precision + recall);
  return {
    observations: usable.length,
    truePositive,
    falsePositive,
    falseNegative,
    trueNegative,
    precision,
    recall,
    f1,
    exactDecisionAccuracy: exact / usable.length,
    falseConflictRate: falsePositive + trueNegative ? falsePositive / (falsePositive + trueNegative) : null,
  };
}

export function summarizeEpisode(episode) {
  const rounds = asArray(episode.rounds, 'rounds');
  const scope = rounds.filter(item => item.scopeApplicable !== false).map(pathMetrics);
  const stale = staleGoalLeakage(episode);
  const firstDrift = firstDriftRound(episode);
  return {
    taskId: episode.taskId,
    trial: episode.trial ?? null,
    success: episode.success,
    rounds: rounds.length,
    episodeUnauthorized: scope.some(item => item.unauthorizedCount > 0),
    actualChangedPathCount: scope.reduce((sum, item) => sum + item.actual, 0),
    unauthorizedChangedPathCount: scope.reduce((sum, item) => sum + item.unauthorizedCount, 0),
    meanScopePrecision: average(scope.map(item => item.precision)),
    meanScopeRecall: average(scope.map(item => item.recall)),
    meanScopeF1: average(scope.map(item => item.f1)),
    meanTurnGoalCompletion: average(rounds.map(turnGoalCompletion).filter(value => value !== null)),
    meanComponentIdSurvivalRate: average(rounds.map(item => item.componentIdSurvivalRate).filter(value => typeof value === 'number' && Number.isFinite(value))),
    meanActivityIdSurvivalRate: average(rounds.map(item => item.activityIdSurvivalRate).filter(value => typeof value === 'number' && Number.isFinite(value))),
    systemIdentityOverrideAttemptCount: rounds.reduce((sum, item) => sum + (Array.isArray(item.attemptedSystemIdentityChanges) ? item.attemptedSystemIdentityChanges.length : 0), 0),
    goalRetentionAuc: retentionAuc(episode),
    constraintRetentionAuc: constraintRetentionAuc(episode),
    cumulativeGoalDrift: cumulativeGoalDrift(episode),
    staleGoalLeakageRate: stale.rate,
    staleGoalsTested: stale.tested,
    staleGoalsLeaked: stale.leaked,
    firstDriftRound: firstDrift,
    noDrift: firstDrift === null,
    recovery: recoveryMetrics(episode.recovery),
  };
}

export { average };
