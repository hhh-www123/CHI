const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

(async () => {
  const metrics = await import(pathToFileURL(path.resolve(__dirname, '../evaluation/metrics.mjs')).href);

  const paths = metrics.pathMetrics({
    desiredPaths: ['/a', '/b'],
    actualChangedPaths: ['/a', '/c'],
    unauthorizedChangedPaths: ['/c'],
  });
  assert.equal(paths.precision, 0.5);
  assert.equal(paths.recall, 0.5);
  assert.equal(paths.f1, 0.5);
  assert.equal(paths.unauthorizedRateByChangedPaths, 0.5);

  const episode = {
    taskId: 'retention-1',
    trial: 1,
    success: false,
    rounds: [
      {
        round: 1,
        activeGoals: [
          { id: 'g1', introducedRound: 1, weight: 2, satisfied: true },
          { id: 'g2', introducedRound: 1, weight: 1, satisfied: true },
        ],
        activeConstraints: [{ id: 'c1', introducedRound: 1, weight: 2, satisfied: true }],
        desiredPaths: ['/a'], actualChangedPaths: ['/a'], unauthorizedChangedPaths: [],
      },
      {
        round: 2,
        activeGoals: [
          { id: 'g1', introducedRound: 1, weight: 1, satisfied: true },
          { id: 'g2', introducedRound: 1, weight: 1, satisfied: false },
        ],
        retiredGoals: [{ id: 'old-subtask', leaked: true }, { id: 'old-budget', leaked: false }],
        activeConstraints: [{ id: 'c1', introducedRound: 1, weight: 2, satisfied: true }],
        desiredPaths: ['/b'], actualChangedPaths: [], unauthorizedChangedPaths: [],
      },
    ],
    recovery: { seededAtRound: 2, detectedAtRound: 3, recoveredAtRound: 5, residualErrors: 0, clicks: 2 },
  };
  assert.equal(metrics.retentionAuc(episode), 0.75);
  assert.equal(metrics.cumulativeGoalDrift(episode), 0.25);
  assert.equal(metrics.constraintRetentionAuc(episode), 1);
  assert.equal(metrics.firstDriftRound(episode), 2);
  assert.deepEqual(metrics.staleGoalLeakage(episode), { tested: 2, leaked: 1, rate: 0.5 });
  assert.equal(metrics.goalRetentionByAge(episode)[0].retention, 1);
  assert.equal(metrics.goalRetentionByAge(episode)[1].retention, 0.5);
  assert.equal(metrics.constraintRetentionByAge(episode)[0].retention, 1);
  assert.equal(metrics.constraintRetentionByAge(episode)[1].retention, 1);
  assert.equal(metrics.normalizedRetentionAuc({ 0: { retention: 1 }, 1: { retention: 0 }, 10: { retention: 0 } }), 0.05);
  assert.equal(metrics.turnGoalCompletion({ turnGoal: { id: 'turn-r1', satisfied: false } }), 0);
  assert.deepEqual(metrics.recoveryMetrics(episode.recovery), {
    success: true,
    detectionDelayRounds: 1,
    recoveryRoundsFromInjection: 3,
    recoveryRoundsAfterDetection: 2,
    residualErrors: 0,
    clicks: 2,
    elapsedMs: null,
  });

  const trials = [
    ...[1, 2, 3].map(trial => ({ taskId: 't1', trial, success: true })),
    { taskId: 't2', trial: 1, success: true },
    { taskId: 't2', trial: 2, success: true },
    { taskId: 't2', trial: 3, success: false },
  ];
  assert.equal(metrics.passHatK(trials, 3).value, 0.5);
  assert.ok(Math.abs(metrics.passHatK(trials, 2).value - 2 / 3) < 1e-12);

  const candidates = metrics.candidateMetrics([
    { valid: true, outcomeSignature: 'state-a', goalCoverage: 1, systemRank: 1, humanRelevance: 3, humanBest: true },
    { valid: true, outcomeSignature: 'state-b', goalCoverage: 0.5, systemRank: 2, humanRelevance: 2, humanBest: false },
    { valid: false },
  ], 3);
  assert.equal(candidates.qualifiedRate, 2 / 3);
  assert.equal(candidates.uniqueOutcomeRate, 1);
  assert.equal(candidates.meanGoalCoverage, 0.75);
  assert.equal(candidates.ndcg, 1);
  assert.equal(candidates.top1Regret, 0);
  assert.equal(candidates.top1HumanBest, true);
  assert.equal(candidates.targetFulfillment, 2 / 3);
  assert.equal(candidates.targetMet, false);
  assert.throws(() => metrics.candidateMetrics([], 0), /positive integer/);

  const conflicts = metrics.conflictMetrics([
    { groundTruthDecision: 'reject', systemDecision: 'reject' },
    { groundTruthDecision: 'clarify', systemDecision: 'accept' },
    { groundTruthDecision: 'accept', systemDecision: 'reject' },
    { groundTruthDecision: 'accept', systemDecision: 'accept' },
  ]);
  assert.equal(conflicts.precision, 0.5);
  assert.equal(conflicts.recall, 0.5);
  assert.equal(conflicts.f1, 0.5);
  assert.equal(conflicts.exactDecisionAccuracy, 0.5);

  const summary = metrics.summarizeEpisode(episode);
  assert.equal(summary.meanScopeF1, 0.5);
  assert.equal(summary.episodeUnauthorized, false);
  assert.equal(summary.noDrift, false);
  assert.equal(summary.actualChangedPathCount, 1);
  assert.equal(summary.unauthorizedChangedPathCount, 0);
  assert.equal(summary.staleGoalsTested, 2);
  assert.equal(summary.staleGoalsLeaked, 1);
  assert.equal(metrics.summarizeEpisode({ taskId: 'trusted-setup', success: true, rounds: [{ round: 1, scopeApplicable: false, desiredPaths: [], actualChangedPaths: ['/trusted-control'], unauthorizedChangedPaths: ['/trusted-control'] }] }).episodeUnauthorized, false);

  const cli = path.resolve(__dirname, '../evaluation/score.mjs');
  const example = path.resolve(__dirname, '../evaluation/example-dataset.json');
  const scored = JSON.parse(execFileSync(process.execPath, [cli, example], { encoding: 'utf8' }));
  assert.equal(scored.methods.D.episodes, 3);
  assert.equal(scored.methods.D.passHat3.value, 0);
  assert.equal(scored.methods.D.taskSuccessRate, 2 / 3);
  assert.equal(scored.methods.D.rounds, 3);
  assert.equal(scored.methods.D.meanConstraintRetentionAuc, 1);
  assert.equal(scored.byModel['illustrative-model'].D.episodes, 3);
  assert.equal(scored.runMetadata.illustrative, true);

  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'travel-analysis-'));
  try {
    const episodes = [];
    for (const method of ['A', 'B', 'C', 'D']) {
      for (let task = 1; task <= 6; task += 1) {
        for (let trial = 1; trial <= 3; trial += 1) {
          const success = method === 'D';
          episodes.push({
            method, modelKey: 'model-1', taskId: `task-${task}-L20`, interactionLength: 20, trial, success,
            rounds: Array.from({ length: 20 }, (_, roundIndex) => ({
              round: roundIndex + 1,
              turnGoal: { id: `turn-r${roundIndex + 1}`, satisfied: success, assessment: roundIndex === 19 ? 'human_blinded' : 'structural_proxy' },
              activeGoals: roundIndex === 19 ? [{ id: 'g', introducedRound: 1, satisfied: success }] : [],
              activeConstraints: [], retiredGoals: [],
              desiredPaths: ['/x'], actualChangedPaths: success ? ['/x'] : [], unauthorizedChangedPaths: [],
              ...(roundIndex === 19 ? {
                groundTruthDecision: 'reject',
                systemDecision: method === 'D' ? 'reject' : 'accept',
              } : {}),
              ...(method === 'D' && roundIndex === 19 ? {
                candidateTargetCount: 3,
                candidates: [
                  { valid: true, outcomeSignature: 'best', systemRank: 1, humanRelevance: 3, humanBest: true },
                  { valid: true, outcomeSignature: 'middle', systemRank: 2, humanRelevance: 2, humanBest: false },
                  { valid: true, outcomeSignature: 'last', systemRank: 3, humanRelevance: 1, humanBest: false },
                ],
              } : {}),
            })),
          });
        }
      }
    }
    const pairedPath = path.join(temporary, 'paired.json');
    const canonicalize = value => {
      if (Array.isArray(value)) return value.map(canonicalize);
      if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
      return value;
    };
    const hash = value => crypto.createHash('sha256').update(value).digest('hex');
    const analysisContract = {
      bootstrapScriptHash: hash(fs.readFileSync(path.resolve(__dirname, '../analysis/bootstrap.mjs'))),
      survivalScriptHash: hash(fs.readFileSync(path.resolve(__dirname, '../analysis/drift_survival.mjs'))),
      retentionScriptHash: hash(fs.readFileSync(path.resolve(__dirname, '../analysis/retention_curves.mjs'))),
      metricsModuleHash: hash(fs.readFileSync(path.resolve(__dirname, '../evaluation/metrics.mjs'))),
      scoringScriptHash: hash(fs.readFileSync(path.resolve(__dirname, '../evaluation/score.mjs'))),
    };
    fs.writeFileSync(pairedPath, JSON.stringify({
      schemaVersion: '1.0', formalExperiment: true, realModel: true,
      generationMode: 'real-model-formal-runner', manifestHash: 'a'.repeat(64),
      analysisContract, analysisContractHash: hash(JSON.stringify(canonicalize(analysisContract))),
      annotationStats: { formalReady: true }, episodes,
    }));
    const bootstrapCli = path.resolve(__dirname, '../analysis/bootstrap.mjs');
    const paired = JSON.parse(execFileSync(process.execPath, [bootstrapCli, pairedPath, '--iterations', '100', '--seed', '11', '--model-key', 'model-1', '--length', '20'], { encoding: 'utf8' }));
    assert.equal(paired.inferenceReady, true);
    assert.equal(paired.modelSelection.selected, 'model-1');
    assert.equal(paired.lengthSelection.selected, 20);
    assert.equal(typeof paired.comparisons['D-A'].pairedPermutationP.taskSuccessRate, 'number');
    assert.equal(paired.comparisons['D-A'].pairedSignFlip.taskSuccessRate.mode, 'exact-enumeration');
    assert.equal(paired.comparisons['D-A'].pairedSignFlip.taskSuccessRate.assignments, 64);
    assert.equal(paired.comparisons['D-A'].pairedPermutationP.taskSuccessRate, 2 / 64);
    assert.ok(paired.comparisons['D-A'].holmAdjustedP.taskSuccessRate >= paired.comparisons['D-A'].pairedPermutationP.taskSuccessRate);
    assert.equal(paired.methods.D.point.conflictF1, 1);
    assert.equal(paired.methods.D.point.meanCandidateNdcg, 1);
    assert.equal(paired.methods.D.point.meanCandidateTop1Regret, 0);
    assert.equal(paired.methods.D.point.candidateTop1HumanBestRate, 1);
    const survivalCli = path.resolve(__dirname, '../analysis/drift_survival.mjs');
    const survival = JSON.parse(execFileSync(process.execPath, [
      survivalCli, pairedPath, '--model-key', 'model-1', '--length', '20', '--iterations', '100', '--seed', '13',
    ], { encoding: 'utf8' }));
    assert.equal(survival.inferenceReady, true);
    assert.equal(survival.methods.D.noDriftRateAtHorizon, 1);
    assert.equal(survival.methods.A.curve['1'].survival, 0);
    assert.equal(survival.comparisons['D-A'].noDriftRateDifferenceAtHorizon, 1);
    assert.equal(survival.comparisons['D-A'].horizonExactTaskSignFlip.p, 2 / 64);
    assert.equal(survival.comparisons['D-A'].horizonExactTaskSignFlip.assignments, 64);
    const retentionCli = path.resolve(__dirname, '../analysis/retention_curves.mjs');
    const retention = JSON.parse(execFileSync(process.execPath, [
      retentionCli, pairedPath, '--model-key', 'model-1', '--iterations', '100', '--seed', '17',
    ], { encoding: 'utf8' }));
    assert.equal(retention.inferenceReady, true);
    assert.deepEqual(retention.goalRetention.ages, [19]);
    assert.equal(retention.goalRetention.methods.D.curve['19'].retention, 1);
    assert.equal(retention.goalRetention.methods.A.curve['19'].retention, 0);
    assert.equal(retention.goalRetention.comparisons['D-A'].curveDifference['19'].difference, 1);
    assert.equal(retention.goalRetention.methods.D.auc, 1);
    assert.equal(retention.constraintRetention.methods.D.auc, null);
    const tamperedPath = path.join(temporary, 'tampered-analysis-contract.json');
    const tamperedDataset = JSON.parse(fs.readFileSync(pairedPath, 'utf8'));
    tamperedDataset.analysisContract.bootstrapScriptHash = '0'.repeat(64);
    tamperedDataset.analysisContractHash = hash(JSON.stringify(canonicalize(tamperedDataset.analysisContract)));
    fs.writeFileSync(tamperedPath, JSON.stringify(tamperedDataset));
    const tamperedBootstrap = JSON.parse(execFileSync(process.execPath, [
      bootstrapCli, tamperedPath, '--iterations', '100', '--model-key', 'model-1', '--length', '20',
    ], { encoding: 'utf8' }));
    assert.equal(tamperedBootstrap.inferenceReady, false);
    assert.ok(tamperedBootstrap.readinessBlockers.some(item => item.includes('running bootstrap script')));
    const invalidFormalPath = path.join(temporary, 'invalid-formal.json');
    fs.writeFileSync(invalidFormalPath, JSON.stringify({ schemaVersion: '1.0', formalExperiment: true, realModel: true, episodes: [] }));
    assert.throws(() => execFileSync(process.execPath, [cli, invalidFormalPath], { encoding: 'utf8', stdio: 'pipe' }), /status 1|Command failed/);
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }

  console.log(JSON.stringify({ passed: true, checks: 75, scope: 'deterministic long-horizon evaluation metrics, exact paired inference, clustered drift survival, retention/candidate curves and CLI' }));
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
