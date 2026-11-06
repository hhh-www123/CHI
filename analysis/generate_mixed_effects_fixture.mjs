#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { access, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = process.argv[2];
if (!output || process.argv.length !== 3) {
  console.error('Usage: node analysis/generate_mixed_effects_fixture.mjs output.json');
  process.exit(2);
}
const target = resolve(output);
try {
  await access(target);
  throw new TypeError(`refusing to overwrite ${target}`);
} catch (error) {
  if (error instanceof TypeError) throw error;
  if (error?.code !== 'ENOENT') throw error;
}

const root = resolve(import.meta.dirname, '..');
const sha256 = value => createHash('sha256').update(value).digest('hex');
const canonicalize = value => {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  return value;
};
const canonicalHash = value => sha256(JSON.stringify(canonicalize(value)));
const spec = JSON.parse(await readFile(resolve(root, 'analysis', 'model-spec.json'), 'utf8'));
const analysisContract = {
  id: spec.analysisId,
  status: spec.status,
  specHash: canonicalHash(spec),
  runtimeStatus: spec.runtime?.status,
  runtimeVersions: spec.runtime?.lockedVersions ?? null,
  verifiedRuntimeVersions: spec.runtime?.verifiedVersions ?? null,
  preparationScriptHash: sha256(await readFile(resolve(root, 'analysis', 'prepare_mixed_effects.mjs'))),
  modelScriptHash: sha256(await readFile(resolve(root, 'analysis', 'mixed_effects.R'))),
  bootstrapScriptHash: sha256(await readFile(resolve(root, 'analysis', 'bootstrap.mjs'))),
  survivalScriptHash: sha256(await readFile(resolve(root, 'analysis', 'drift_survival.mjs'))),
  retentionScriptHash: sha256(await readFile(resolve(root, 'analysis', 'retention_curves.mjs'))),
  metricsModuleHash: sha256(await readFile(resolve(root, 'evaluation', 'metrics.mjs'))),
  scoringScriptHash: sha256(await readFile(resolve(root, 'evaluation', 'score.mjs'))),
  scoringAssemblyHash: sha256(await readFile(resolve(root, 'evaluation', 'assemble_scoring_dataset.py'))),
  annotationPreparationHash: sha256(await readFile(resolve(root, 'evaluation', 'prepare_blinded_annotations.py'))),
  annotationResolutionHash: sha256(await readFile(resolve(root, 'evaluation', 'annotation_resolution.py'))),
  annotationSchemaHash: sha256(await readFile(resolve(root, 'evaluation', 'annotations', 'schema.json'))),
  annotationPacketSchemaHash: sha256(await readFile(resolve(root, 'evaluation', 'annotations', 'packet-schema.json'))),
  annotationRubricHash: sha256(await readFile(resolve(root, 'research', 'annotation-rubric.md'))),
};

const methods = ['A', 'B', 'C', 'D'];
const lengths = [1, 5, 10, 20];
const models = ['synthetic-primary', 'synthetic-replication'];
const families = [
  'instruction_retention', 'implicit_preference', 'versioned_editing',
  'self_coherence', 'goal_update', 'conflict_handling',
];
const templates = spec.design.taskTemplates;
if (!Array.isArray(templates) || templates.length !== 6) throw new TypeError('model spec must register six task templates');

const mulberry32 = initial => {
  let state = initial >>> 0;
  return () => {
    state += 0x6D2B79F5;
    let value = state;
    value = Math.imul(value ^ value >>> 15, value | 1);
    value ^= value + Math.imul(value ^ value >>> 7, value | 61);
    return ((value ^ value >>> 14) >>> 0) / 4294967296;
  };
};
const random = mulberry32(20261104);
const normal = () => Math.sqrt(-2 * Math.log(Math.max(random(), 1e-12))) * Math.cos(2 * Math.PI * random());
const logistic = value => 1 / (1 + Math.exp(-value));
const methodEffect = { A: -0.65, B: -0.2, C: 0.25, D: 0.8 };
const lengthEffect = { 1: 0.8, 5: 0.25, 10: -0.3, 20: -0.9 };
const templateEffect = [-0.65, -0.35, -0.1, 0.15, 0.4, 0.65];
const episodes = [];
for (const [modelIndex, modelKey] of models.entries()) {
  for (const length of lengths) {
    for (const [templateIndex, templateId] of templates.entries()) {
      for (let trial = 1; trial <= 5; trial += 1) {
        const blockOrdinal = templateIndex * 5 + trial - 1;
        const order = methods.map((_, index) => methods[(index + blockOrdinal) % methods.length]);
        const blockId = `${modelKey}-${templateId}-L${length}-T${trial}`;
        const blockEffect = normal() * 0.45;
        for (const [orderIndex, method] of order.entries()) {
          const longGuardInteraction = method === 'D' ? Math.log2(length) * 0.22 : method === 'C' ? Math.log2(length) * 0.08 : 0;
          const linear = -0.15 + methodEffect[method] + lengthEffect[length] + templateEffect[templateIndex]
            + (modelIndex ? -0.15 : 0.1) + blockEffect + longGuardInteraction;
          const success = random() < logistic(linear);
          episodes.push({
            runId: `${blockId}-${method}`,
            blockId,
            executionPosition: orderIndex + 1,
            modelKey,
            method,
            taskId: `${templateId}-L${length}`,
            templateId,
            family: families[templateIndex],
            interactionLength: length,
            trial,
            success,
            rounds: Array.from({ length }, (_, index) => {
              const terminalFailure = !success && index === length - 1;
              return {
                round: index + 1,
                phase: length === 1 ? 'combined' : index === 0 ? 'setup' : index === length - 1 ? 'probe' : 'distractor',
                status: 'applied',
                turnGoal: { id: `turn-r${index + 1}`, satisfied: !terminalFailure, assessment: index === length - 1 ? 'human_blinded' : 'structural_proxy' },
                activeGoals: index === length - 1 ? [{
                  id: 'synthetic-goal', introducedRound: 1, weight: 1,
                  satisfied: !terminalFailure, retentionEligible: families[templateIndex] !== 'goal_update',
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
        }
      }
    }
  }
}

const fixture = {
  schemaVersion: '1.0',
  generatedAt: new Date().toISOString(),
  illustrative: true,
  pilot: true,
  formalExperiment: false,
  syntheticContractTest: true,
  generationMode: 'synthetic-mixed-effects-contract-fixture',
  realModel: false,
  experimentId: 'synthetic-contract-test-only',
  manifestHash: sha256('synthetic-contract-test-manifest'),
  analysisContract,
  analysisContractHash: canonicalHash(analysisContract),
  benchmarkHash: sha256('synthetic-compiled-benchmark'),
  benchmarkSourceHash: sha256('synthetic-source-benchmark'),
  promptHash: sha256('synthetic-prompt-bundle'),
  annotationStats: { formalReady: false, syntheticContractTest: true },
  notes: [
    'Generated outcomes test data and model contracts only.',
    'This fixture contains no model calls, human labels, participants or empirical evidence.',
    'It must never be used in a paper result table or converted to formalExperiment:true.',
  ],
  episodes,
};
await writeFile(target, `${JSON.stringify(fixture, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
process.stdout.write(`${JSON.stringify({ created: true, syntheticContractTest: true, formalExperiment: false, episodes: episodes.length, output: target })}\n`);
