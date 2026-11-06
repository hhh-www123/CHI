const assert = require('node:assert/strict');
const { execFileSync, spawnSync } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const python = process.platform === 'win32'
  ? path.join(root, '.venv', 'Scripts', 'python.exe')
  : path.join(root, '.venv', 'bin', 'python');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'travel-research-assets-'));
const hash = value => crypto.createHash('sha256').update(value).digest('hex');

try {
  execFileSync(python, ['-m', 'unittest', 'evaluation.test_research_contracts'], { cwd: root, encoding: 'utf8' });

  const taskCheck = JSON.parse(execFileSync(python, ['evaluation/task_suite.py', '--check'], { cwd: root, encoding: 'utf8' }));
  assert.equal(taskCheck.valid, true);
  assert.equal(taskCheck.variantCount, 24);
  assert.equal(taskCheck.sourceStatus, 'draft');
  assert.match(taskCheck.sourceHash, /^[a-f0-9]{64}$/);
  assert.match(taskCheck.compiledHash, /^[a-f0-9]{64}$/);
  assert.notEqual(taskCheck.compiledHash, taskCheck.sourceHash);

  const taskReviewPacketPath = path.join(temporary, 'task-review-packet.json');
  const taskReviewResult = JSON.parse(execFileSync(python, [
    'research/prepare_task_review.py', '--output', taskReviewPacketPath,
  ], { cwd: root, encoding: 'utf8' }));
  const taskReviewPacket = JSON.parse(fs.readFileSync(taskReviewPacketPath, 'utf8'));
  assert.equal(taskReviewResult.taskCount, 24);
  assert.equal(taskReviewPacket.status, 'unreviewed');
  assert.equal(taskReviewPacket.benchmarkHash, taskCheck.compiledHash);
  assert.equal(taskReviewPacket.tasks.length, 24);
  assert.ok(taskReviewPacket.tasks.every(task => task.rounds.length === task.interactionLength));
  assert.ok(!taskReviewPacket.tasks.some(task => Object.hasOwn(task, 'approved')));
  assert.match(taskReviewPacket.packetHash, /^[a-f0-9]{64}$/);

  const promptCheck = JSON.parse(execFileSync(python, ['evaluation/prompt_bundle.py'], { cwd: root, encoding: 'utf8' }));
  assert.equal(promptCheck.valid, true);
  assert.equal(promptCheck.status, 'draft');
  assert.match(promptCheck.hash, /^[a-f0-9]{64}$/);

  const manifestPath = path.join(temporary, 'draft-manifest.json');
  const manifestResult = JSON.parse(execFileSync(python, ['evaluation/prepare_experiment.py', '--output', manifestPath], { cwd: root, encoding: 'utf8' }));
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  assert.equal(manifestResult.formalReady, false);
  assert.equal(manifest.blockCount, 240);
  assert.equal(manifest.runCount, 960);
  assert.equal(manifest.costPlan.plannedProviderCalls, 8040);
  assert.equal(manifest.costPlan.byModel.primary.plannedProviderCalls, 4020);
  assert.equal(manifest.costPlan.maximumAllocatedOutputTokens, 48240000);
  assert.equal(manifest.costPlan.outputAllocationCostUsd, null);
  assert.equal(manifest.costPlan.plannedHumanSemanticUnits, 1080);
  assert.equal(manifest.costPlan.maximumHumanCandidateBatches, 150);
  assert.equal(manifest.costPlan.maximumUniqueAnnotationSamples, 1230);
  assert.equal(manifest.costPlan.minimumDoubleCodedSemanticSamples, 216);
  assert.equal(manifest.costPlan.minimumDoubleCodedCandidateBatches, 30);
  assert.equal(manifest.costPlan.minimumAnnotationAssignments, 1476);
  assert.equal(manifest.costPlan.planningUpperBoundWithTwoCodersAndAdjudicatorEverySample, 3690);
  assert.equal(manifest.configuration.randomizationScheme, 'williams-balanced-within-model-and-length-v1');
  assert.equal(manifest.analysisContract.status, 'frozen');
  assert.equal(manifest.analysisContract.runtimeStatus, 'frozen-tested');
  assert.deepEqual(manifest.analysisContract.runtimeVersions, {
    R: '4.6.2', lme4: '2.1.0', emmeans: '2.0.4', jsonlite: '2.0.0', digest: '0.6.39',
  });
  assert.deepEqual(manifest.analysisContract.verifiedRuntimeVersions, {
    R: '4.6.2', lme4: '2.1.0', emmeans: '2.0.4', jsonlite: '2.0.0', digest: '0.6.39',
  });
  assert.match(manifest.analysisContract.specHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.preparationScriptHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.modelScriptHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.bootstrapScriptHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.survivalScriptHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.retentionScriptHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.metricsModuleHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.scoringScriptHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.scoringAssemblyHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.annotationPreparationHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.annotationResolutionHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.annotationSchemaHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.annotationPacketSchemaHash, /^[a-f0-9]{64}$/);
  assert.match(manifest.analysisContract.annotationRubricHash, /^[a-f0-9]{64}$/);
  const positionCounts = new Map();
  for (const block of manifest.blocks) {
    const key = `${block.modelKey}:${block.interactionLength}`;
    const positions = positionCounts.get(key) ?? [new Map(), new Map(), new Map(), new Map()];
    block.methodOrder.forEach((method, index) => positions[index].set(method, (positions[index].get(method) ?? 0) + 1));
    positionCounts.set(key, positions);
  }
  for (const positions of positionCounts.values()) {
    for (const counts of positions) {
      assert.equal(counts.size, 4);
      assert.ok(Math.max(...counts.values()) - Math.min(...counts.values()) <= 1);
    }
  }
  assert.equal(manifest.credentialRecorded, false);
  assert.deepEqual(manifest.credentialPresentAtPreparation, { primary: false, replication: false });
  assert.ok(manifest.readinessBlockers.some(item => item.includes('not frozen')));
  assert.ok(!manifest.readinessBlockers.some(item => item.includes('analysis specification')));
  assert.ok(!manifest.readinessBlockers.some(item => item.includes('R runtime')));
  assert.ok(!manifest.readinessBlockers.some(item => item.includes('R/package versions')));
  const manifestBeforeOverwriteAttempt = fs.readFileSync(manifestPath, 'utf8');
  const overwriteManifest = spawnSync(python, ['evaluation/prepare_experiment.py', '--output', manifestPath], { cwd: root, encoding: 'utf8' });
  assert.equal(overwriteManifest.status, 2);
  assert.match(overwriteManifest.stderr, /must not already exist/);
  assert.equal(fs.readFileSync(manifestPath, 'utf8'), manifestBeforeOverwriteAttempt);
  assert.ok(!manifest.readinessBlockers.some(item => item.includes('adapters are not runnable')));

  const forbiddenFormalPath = path.join(temporary, 'must-not-exist.json');
  const formal = spawnSync(python, ['evaluation/prepare_experiment.py', '--formal', '--output', forbiddenFormalPath], { cwd: root, encoding: 'utf8' });
  assert.equal(formal.status, 2);
  assert.equal(fs.existsSync(forbiddenFormalPath), false);
  assert.match(formal.stderr, /formalReady/);

  const forbiddenRunDirectory = path.join(temporary, 'must-not-run');
  const realRunner = spawnSync(python, ['evaluation/run_real_experiment.py', '--manifest', manifestPath, '--output-dir', forbiddenRunDirectory], { cwd: root, encoding: 'utf8' });
  assert.equal(realRunner.status, 2);
  assert.equal(fs.existsSync(forbiddenRunDirectory), false);
  assert.match(realRunner.stderr, /formalExperiment:true and formalReady:true/);

  const bootstrapPath = path.join(temporary, 'bootstrap.json');
  execFileSync(process.execPath, ['analysis/bootstrap.mjs', 'evaluation/example-dataset.json', bootstrapPath, '--iterations', '100', '--seed', '7'], { cwd: root, encoding: 'utf8' });
  const bootstrap = JSON.parse(fs.readFileSync(bootstrapPath, 'utf8'));
  assert.equal(bootstrap.inferenceReady, false);
  assert.equal(bootstrap.methods.D.taskCount, 1);
  assert.equal(bootstrap.bootstrap.unit, 'task-template-with-all-lengths-and-trials');
  assert.equal(bootstrap.methods.D.point.taskSuccessRate, 2 / 3);
  assert.ok(bootstrap.readinessBlockers.includes('missing method A'));
  assert.deepEqual(bootstrap.modelSelection.available, ['illustrative-model']);

  const modelSpecPath = path.join(root, 'analysis', 'model-spec.json');
  const modelSpec = JSON.parse(fs.readFileSync(modelSpecPath, 'utf8'));
  assert.equal(modelSpec.status, 'frozen');
  assert.equal(modelSpec.confirmatory.primaryEndpoint.targetInteractionLength, 20);
  assert.deepEqual(modelSpec.confirmatory.primaryContrasts, ['D-A', 'D-B', 'D-C']);
  assert.equal(modelSpec.runtime.status, 'frozen-tested');
  assert.deepEqual(modelSpec.runtime.verifiedVersions, {
    R: '4.6.2', lme4: '2.1.0', emmeans: '2.0.4', jsonlite: '2.0.0', digest: '0.6.39',
  });
  assert.deepEqual(modelSpec.runtime.lockedVersions, modelSpec.runtime.verifiedVersions);
  const runtimeVerification = JSON.parse(fs.readFileSync(path.join(root, modelSpec.runtime.verificationEvidence), 'utf8'));
  assert.equal(runtimeVerification.status, 'passed');
  assert.equal(runtimeVerification.syntheticContractTest, true);
  assert.equal(runtimeVerification.formalAnalysisReady, false);
  assert.equal(runtimeVerification.modelFitsReady, true);
  assert.deepEqual(runtimeVerification.runtimeVersions, modelSpec.runtime.verifiedVersions);
  assert.equal(runtimeVerification.design.episodes, 960);
  assert.equal(runtimeVerification.design.blocks, 240);
  assert.equal(runtimeVerification.assetHashes.modelSpec, hash(fs.readFileSync(modelSpecPath)));
  assert.equal(runtimeVerification.assetHashes.modelScript, hash(fs.readFileSync(path.join(root, 'analysis', 'mixed_effects.R'))));
  assert.equal(runtimeVerification.assetHashes.verificationScript, hash(fs.readFileSync(path.join(root, 'analysis', 'verify_runtime.py'))));
  assert.ok(fs.existsSync(path.join(root, 'analysis', 'mixed_effects.R')));

  const methods = ['A', 'B', 'C', 'D'];
  const lengths = [1, 5, 10, 20];
  const modelKeys = ['synthetic-primary', 'synthetic-replication'];
  const templates = [
    ['retention-budget-hotel', 'instruction_retention'],
    ['implicit-indoor-preference', 'implicit_preference'],
    ['restore-before-place-change', 'versioned_editing'],
    ['self-coherence-budget', 'self_coherence'],
    ['budget-goal-update', 'goal_update'],
    ['locked-day-conflict', 'conflict_handling'],
  ];
  const syntheticEpisodes = [];
  for (const modelKey of modelKeys) {
    for (const length of lengths) {
      for (const [templateIndex, [templateId, family]] of templates.entries()) {
        for (let trial = 1; trial <= 5; trial += 1) {
          const blockOrdinal = templateIndex * 5 + trial - 1;
          const order = methods.map((_, index) => methods[(index + blockOrdinal) % methods.length]);
          const blockId = `${modelKey}-${templateId}-L${length}-T${trial}`;
          order.forEach((method, orderIndex) => {
            const success = method === 'D' || trial > 1;
            syntheticEpisodes.push({
              runId: `${blockId}-${method}`,
              blockId,
              executionPosition: orderIndex + 1,
              modelKey,
              method,
              taskId: `${templateId}-L${length}`,
              templateId,
              family,
              interactionLength: length,
              trial,
              success,
              rounds: Array.from({ length }, (_, roundIndex) => {
                const terminalFailure = !success && roundIndex === length - 1;
                return {
                  round: roundIndex + 1,
                  status: terminalFailure ? 'invalid_output' : 'applied',
                  turnGoal: { id: `turn-r${roundIndex + 1}`, satisfied: !terminalFailure },
                  activeGoals: roundIndex === length - 1 ? [{
                    id: 'synthetic-goal', introducedRound: 1, weight: 1,
                    satisfied: !terminalFailure, retentionEligible: family !== 'goal_update',
                  }] : [],
                  retiredGoals: [],
                  activeConstraints: [],
                  desiredPaths: [],
                  actualChangedPaths: [],
                  unauthorizedChangedPaths: [],
                  semanticFailure: terminalFailure,
                };
              }),
            });
          });
        }
      }
    }
  }
  const syntheticDatasetPath = path.join(temporary, 'synthetic-formal-dataset.json');
  fs.writeFileSync(syntheticDatasetPath, JSON.stringify({
    schemaVersion: '1.0',
    formalExperiment: true,
    realModel: true,
    experimentId: 'synthetic-contract-test-only',
    benchmarkHash: '1'.repeat(64),
    benchmarkSourceHash: '2'.repeat(64),
    promptHash: '3'.repeat(64),
    annotationStats: { formalReady: true },
    episodes: syntheticEpisodes,
  }));
  assert.equal(syntheticEpisodes.length, 960);

  const invalidAnalysisDirectory = path.join(temporary, 'must-not-prepare-analysis');
  const invalidAnalysis = spawnSync(process.execPath, [
    'analysis/prepare_mixed_effects.mjs', syntheticDatasetPath, invalidAnalysisDirectory,
  ], { cwd: root, encoding: 'utf8' });
  assert.equal(invalidAnalysis.status, 2);
  assert.equal(fs.existsSync(invalidAnalysisDirectory), false);
  assert.match(invalidAnalysis.stderr, /dataset is not explicitly marked|dataset analysisContract does not match/);

  const frozenTestSpec = structuredClone(modelSpec);
  frozenTestSpec.status = 'frozen';
  frozenTestSpec.runtime.status = 'frozen-tested';
  frozenTestSpec.runtime.lockedVersions = { R: 'synthetic', lme4: 'synthetic', emmeans: 'synthetic', jsonlite: 'synthetic', digest: 'synthetic' };
  const frozenTestSpecPath = path.join(temporary, 'synthetic-frozen-model-spec.json');
  fs.writeFileSync(frozenTestSpecPath, JSON.stringify(frozenTestSpec));
  const canonicalize = value => {
    if (Array.isArray(value)) return value.map(canonicalize);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
    return value;
  };
  const frozenAnalysisContract = {
    id: frozenTestSpec.analysisId,
    status: frozenTestSpec.status,
    specHash: hash(JSON.stringify(canonicalize(frozenTestSpec))),
    runtimeStatus: frozenTestSpec.runtime.status,
    runtimeVersions: frozenTestSpec.runtime.lockedVersions,
    verifiedRuntimeVersions: frozenTestSpec.runtime.verifiedVersions,
    preparationScriptHash: hash(fs.readFileSync(path.join(root, 'analysis', 'prepare_mixed_effects.mjs'))),
    modelScriptHash: hash(fs.readFileSync(path.join(root, 'analysis', 'mixed_effects.R'))),
    bootstrapScriptHash: hash(fs.readFileSync(path.join(root, 'analysis', 'bootstrap.mjs'))),
    survivalScriptHash: hash(fs.readFileSync(path.join(root, 'analysis', 'drift_survival.mjs'))),
    retentionScriptHash: hash(fs.readFileSync(path.join(root, 'analysis', 'retention_curves.mjs'))),
    metricsModuleHash: hash(fs.readFileSync(path.join(root, 'evaluation', 'metrics.mjs'))),
    scoringScriptHash: hash(fs.readFileSync(path.join(root, 'evaluation', 'score.mjs'))),
    scoringAssemblyHash: hash(fs.readFileSync(path.join(root, 'evaluation', 'assemble_scoring_dataset.py'))),
    annotationPreparationHash: hash(fs.readFileSync(path.join(root, 'evaluation', 'prepare_blinded_annotations.py'))),
    annotationResolutionHash: hash(fs.readFileSync(path.join(root, 'evaluation', 'annotation_resolution.py'))),
    annotationSchemaHash: hash(fs.readFileSync(path.join(root, 'evaluation', 'annotations', 'schema.json'))),
    annotationPacketSchemaHash: hash(fs.readFileSync(path.join(root, 'evaluation', 'annotations', 'packet-schema.json'))),
    annotationRubricHash: hash(fs.readFileSync(path.join(root, 'research', 'annotation-rubric.md'))),
  };
  const frozenSyntheticDataset = JSON.parse(fs.readFileSync(syntheticDatasetPath, 'utf8'));
  Object.assign(frozenSyntheticDataset, {
    illustrative: false,
    pilot: false,
    generationMode: 'real-model-formal-runner',
    manifestHash: '4'.repeat(64),
    analysisContract: frozenAnalysisContract,
    analysisContractHash: hash(JSON.stringify(canonicalize(frozenAnalysisContract))),
  });
  fs.writeFileSync(syntheticDatasetPath, JSON.stringify(frozenSyntheticDataset));
  const preparedAnalysisDirectory = path.join(temporary, 'prepared-analysis');
  execFileSync(process.execPath, [
    'analysis/prepare_mixed_effects.mjs', syntheticDatasetPath, preparedAnalysisDirectory, '--spec', frozenTestSpecPath,
  ], { cwd: root, encoding: 'utf8' });
  const analysisManifest = JSON.parse(fs.readFileSync(path.join(preparedAnalysisDirectory, 'analysis-manifest.json'), 'utf8'));
  assert.equal(analysisManifest.formalAnalysisReady, true);
  assert.equal(analysisManifest.designAudit.episodes, 960);
  assert.equal(analysisManifest.designAudit.blocks, 240);
  assert.deepEqual(analysisManifest.designAudit.models, modelKeys);
  assert.equal(analysisManifest.provenance.analysisContractHash, frozenSyntheticDataset.analysisContractHash);
  assert.deepEqual(analysisManifest.provenance.analysisContract, frozenAnalysisContract);
  assert.equal(fs.readFileSync(path.join(preparedAnalysisDirectory, 'episodes.csv'), 'utf8').trimEnd().split('\n').length, 961);
  const overwriteAnalysis = spawnSync(process.execPath, [
    'analysis/prepare_mixed_effects.mjs', syntheticDatasetPath, preparedAnalysisDirectory, '--spec', frozenTestSpecPath,
  ], { cwd: root, encoding: 'utf8' });
  assert.notEqual(overwriteAnalysis.status, 0);
  assert.match(overwriteAnalysis.stderr, /output directory already exists/);

  const mixedFixturePath = path.join(temporary, 'mixed-effects-contract-fixture.json');
  const mixedFixtureResult = JSON.parse(execFileSync(process.execPath, [
    'analysis/generate_mixed_effects_fixture.mjs', mixedFixturePath,
  ], { cwd: root, encoding: 'utf8' }));
  assert.equal(mixedFixtureResult.syntheticContractTest, true);
  assert.equal(mixedFixtureResult.formalExperiment, false);
  assert.equal(mixedFixtureResult.episodes, 960);
  const mixedFixture = JSON.parse(fs.readFileSync(mixedFixturePath, 'utf8'));
  assert.equal(mixedFixture.illustrative, true);
  assert.ok(mixedFixture.notes.some(item => item.includes('never be used')));
  const contractInputDirectory = path.join(temporary, 'mixed-effects-contract-input');
  const contractPrepare = JSON.parse(execFileSync(process.execPath, [
    'analysis/prepare_mixed_effects.mjs', mixedFixturePath, contractInputDirectory, '--contract-test',
  ], { cwd: root, encoding: 'utf8' }));
  const contractManifest = JSON.parse(fs.readFileSync(path.join(contractInputDirectory, 'analysis-manifest.json'), 'utf8'));
  assert.equal(contractPrepare.contractTest, true);
  assert.equal(contractPrepare.formalAnalysisReady, false);
  assert.equal(contractManifest.contractTest, true);
  assert.equal(contractManifest.formalAnalysisReady, false);

  const readiness = JSON.parse(execFileSync(python, ['research/readiness_audit.py'], { cwd: root, encoding: 'utf8' }));
  assert.equal(readiness.formalExperimentReady, false);
  assert.equal(readiness.paperReady, false);
  assert.equal(readiness.assetStatus.analysis.runtimeStatus, 'frozen-tested');
  assert.equal(readiness.assetStatus.analysis.runtimeVerification.ready, true);
  assert.equal(typeof readiness.assetStatus.analysis.runtimeVerification.currentRuntime.available, 'boolean');
  if (readiness.assetStatus.analysis.runtimeVerification.currentRuntime.versions) {
    assert.deepEqual(
      readiness.assetStatus.analysis.runtimeVerification.currentRuntime.versions,
      modelSpec.runtime.verifiedVersions,
    );
  }
  assert.equal(readiness.humanEvidence.taskReviews.expectedTaskCount, 24);
  assert.equal(readiness.humanEvidence.taskReviews.tasksWithTwoIndependentApprovals, 0);
  assert.deepEqual(readiness.humanEvidence.openDecisions.unsignedRequired, []);
  assert.equal(readiness.humanEvidence.openDecisions.ready, true);
  assert.ok(!readiness.blockers.some(item => item.id.startsWith('decision-')));
  assert.ok(!readiness.nextActions.some(item => item.startsWith('Sign open decisions')));
  assert.deepEqual(readiness.humanEvidence.literature.databasesSearched, ['OpenAlex']);
  assert.ok(readiness.blockers.some(item => item.id === 'annotation-pilot'));
  assert.ok(readiness.blockers.some(item => item.id === 'ethics-record'));
  assert.ok(readiness.blockers.some(item => item.id === 'formal-dataset'));
  assert.ok(readiness.blockers.some(item => item.id === 'primary-analysis-results'));
  assert.ok(readiness.blockers.some(item => item.id === 'user-study-results'));

  const staleRuntimeVerificationPath = path.join(temporary, 'stale-runtime-verification.json');
  const staleRuntimeVerification = structuredClone(runtimeVerification);
  staleRuntimeVerification.assetHashes.modelScript = '0'.repeat(64);
  fs.writeFileSync(staleRuntimeVerificationPath, JSON.stringify(staleRuntimeVerification));
  const staleReadiness = JSON.parse(execFileSync(python, [
    'research/readiness_audit.py', '--runtime-verification', staleRuntimeVerificationPath,
  ], { cwd: root, encoding: 'utf8' }));
  assert.equal(staleReadiness.assetStatus.analysis.runtimeVerification.ready, false);
  assert.ok(staleReadiness.assetStatus.analysis.runtimeVerification.issues.some(item => item.includes('stale for modelScript')));
  assert.ok(staleReadiness.blockers.some(item => item.id === 'runtime-verification'));
  const weakAnnotationEvidence = path.join(temporary, 'weak-annotation-evidence.json');
  const weakEthicsEvidence = path.join(temporary, 'weak-ethics-evidence.json');
  const weakUserPilotEvidence = path.join(temporary, 'weak-user-pilot-evidence.json');
  fs.writeFileSync(weakAnnotationEvidence, JSON.stringify({ valid: true }));
  fs.writeFileSync(weakEthicsEvidence, JSON.stringify({ status: 'approved' }));
  fs.writeFileSync(weakUserPilotEvidence, JSON.stringify({ status: 'complete', participantCount: 6 }));
  const weakEvidenceReadiness = JSON.parse(execFileSync(python, [
    'research/readiness_audit.py',
    '--annotation-pilot', weakAnnotationEvidence,
    '--ethics-record', weakEthicsEvidence,
    '--user-study-pilot', weakUserPilotEvidence,
  ], { cwd: root, encoding: 'utf8' }));
  assert.equal(weakEvidenceReadiness.humanEvidence.annotationPilot.ready, false);
  assert.equal(weakEvidenceReadiness.humanEvidence.ethicsRecord.ready, false);
  assert.equal(weakEvidenceReadiness.humanEvidence.userStudyPilot.ready, false);

  const annotations = JSON.parse(execFileSync(python, ['evaluation/validate_annotations.py', 'evaluation/annotations/example.json'], { cwd: root, encoding: 'utf8' }));
  assert.equal(annotations.valid, true);
  assert.equal(annotations.formalReady, false);
  assert.equal(annotations.doubleCodedRate, 1);
  assert.equal(annotations.binaryCompleteGoalKappa, 1);

  const contractPilotPath = path.join(temporary, 'ad-contract-pilot.json');
  const contractPilotResult = JSON.parse(execFileSync(python, ['evaluation/run_contract_pilot.py', '--output', contractPilotPath], { cwd: root, encoding: 'utf8' }));
  const contractPilot = JSON.parse(fs.readFileSync(contractPilotPath, 'utf8'));
  assert.equal(contractPilotResult.passed, true);
  assert.deepEqual(contractPilotResult.methods, ['A', 'B', 'C', 'D']);
  assert.equal(contractPilot.benchmarkHash, taskCheck.compiledHash);
  assert.equal(contractPilot.benchmarkSourceHash, taskCheck.sourceHash);
  assert.equal(contractPilotResult.rounds, 20);
  assert.equal(contractPilot.formalExperiment, false);
  assert.equal(contractPilot.fixture, true);
  assert.equal(contractPilot.episodes.length, 4);
  assert.deepEqual([...new Set(contractPilot.episodes.map(item => item.historyMessages))], [10]);
  assert.equal(contractPilot.episodes.find(item => item.method === 'D').rounds[0].guardsEnforced, true);

  const matrixPath = path.join(temporary, 'ad-contract-pilot-matrix.json');
  const matrixResult = JSON.parse(execFileSync(python, ['evaluation/run_contract_pilot.py', '--all-conditions', '--output', matrixPath], { cwd: root, encoding: 'utf8' }));
  const matrix = JSON.parse(fs.readFileSync(matrixPath, 'utf8'));
  assert.equal(matrixResult.passed, true);
  assert.equal(matrixResult.rounds, 864);
  assert.equal(matrix.taskIds.length, 24);
  assert.equal(matrix.episodes.length, 96);
  const dRounds = matrix.episodes.filter(item => item.method === 'D').flatMap(item => item.rounds);
  assert.ok(dRounds.some(item => item.status === 'restored'));
  assert.ok(dRounds.some(item => item.status === 'constraint_updated'));
  assert.ok(dRounds.some(item => item.status === 'clarify'));

  console.log(JSON.stringify({ passed: true, checks: 159, scope: 'paper protocol, balanced 24-condition A-D orchestration, action/prompt/model/state contracts, trusted controls, identity policy, immutable manifest and real-runner gates, atomic resume, budget guard, annotation agreement, template-level bootstrap, bound mixed-effects runtime verification, independent task review and paper-evidence readiness' }));
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
