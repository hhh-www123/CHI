#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { access, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { basename, dirname, join, resolve } from 'node:path';
import {
  constraintRetentionAuc,
  firstDriftRound,
  retentionAuc,
  summarizeEpisode,
} from '../evaluation/metrics.mjs';

const root = resolve(import.meta.dirname, '..');
const defaultSpecPath = join(root, 'analysis', 'model-spec.json');
const FAILURE_STATUSES = new Set([
  'model_error',
  'invalid_output',
  'state_rejected',
  'guard_rejected',
  'version_conflict',
  'restore_target_missing',
  'post_restore_guard_rejected',
  'no_change',
]);

const argv = process.argv.slice(2);
const inputPath = argv.shift();
const outputDirectory = argv.shift();
let specPath = defaultSpecPath;
let contractTest = false;
while (argv.length) {
  const argument = argv.shift();
  if (argument === '--spec') specPath = resolve(argv.shift() ?? '');
  else if (argument === '--contract-test') contractTest = true;
  else throw new TypeError(`unknown argument: ${argument}`);
}
if (!inputPath || !outputDirectory) {
  console.error('Usage: node analysis/prepare_mixed_effects.mjs dataset.json output-directory [--spec model-spec.json] [--contract-test]');
  process.exit(2);
}

const sha256 = value => createHash('sha256').update(value).digest('hex');
const canonicalize = value => {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  }
  return value;
};
const canonicalHash = value => sha256(JSON.stringify(canonicalize(value)));
const hashPattern = /^[a-f0-9]{64}$/;
const unique = values => [...new Set(values)];
const asInteger = (value, name, minimum = Number.MIN_SAFE_INTEGER) => {
  if (!Number.isInteger(value) || value < minimum) throw new TypeError(`${name} must be an integer >= ${minimum}`);
  return value;
};
const requireString = (value, name) => {
  if (typeof value !== 'string' || !value) throw new TypeError(`${name} must be a non-empty string`);
  return value;
};
const sameMembers = (left, right) => left.length === right.length && [...left].sort().every((item, index) => item === [...right].sort()[index]);
const csvCell = value => {
  if (value === null || value === undefined) return '';
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};

const datasetBytes = await readFile(resolve(inputPath));
const specBytes = await readFile(resolve(specPath));
const dataset = JSON.parse(datasetBytes.toString('utf8'));
const spec = JSON.parse(specBytes.toString('utf8'));

if (spec?.schemaVersion !== '1.0' || spec?.analysisId !== 'travel-long-horizon-primary-v1') {
  throw new TypeError('unsupported mixed-effects model specification');
}
if (!Array.isArray(spec.design?.methods) || !Array.isArray(spec.design?.interactionLengths)) {
  throw new TypeError('model specification has no complete expected design');
}
if (dataset?.schemaVersion !== '1.0' || !Array.isArray(dataset.episodes)) {
  throw new TypeError('dataset requires schemaVersion 1.0 and episodes[]');
}

const methods = spec.design.methods;
const lengths = spec.design.interactionLengths;
const registeredTemplates = spec.design.taskTemplates;
if (!Array.isArray(registeredTemplates) || registeredTemplates.length !== spec.design.taskTemplateCount) {
  throw new TypeError('model specification does not enumerate the registered task templates');
}
const trialsPerCell = asInteger(spec.design.trialsPerCell, 'design.trialsPerCell', 1);
const rows = [];
const runIds = new Set();
const blocks = new Map();
const cellCounts = new Map();
const templateFamilies = new Map();

for (const [index, episode] of dataset.episodes.entries()) {
  const prefix = `episodes[${index}]`;
  const runId = requireString(episode?.runId, `${prefix}.runId`);
  if (runIds.has(runId)) throw new TypeError(`duplicate runId: ${runId}`);
  runIds.add(runId);
  const blockId = requireString(episode.blockId, `${prefix}.blockId`);
  const modelKey = requireString(episode.modelKey, `${prefix}.modelKey`);
  const method = requireString(episode.method, `${prefix}.method`);
  const taskId = requireString(episode.taskId, `${prefix}.taskId`);
  const templateId = requireString(episode.templateId, `${prefix}.templateId`);
  const family = requireString(episode.family, `${prefix}.family`);
  const interactionLength = asInteger(episode.interactionLength, `${prefix}.interactionLength`, 1);
  const trial = asInteger(episode.trial, `${prefix}.trial`, 1);
  const executionPosition = asInteger(episode.executionPosition, `${prefix}.executionPosition`, 1);
  if (!methods.includes(method)) throw new TypeError(`${prefix}.method is not registered`);
  if (!lengths.includes(interactionLength)) throw new TypeError(`${prefix}.interactionLength is not registered`);
  if (trial > trialsPerCell) throw new TypeError(`${prefix}.trial exceeds the registered trial count`);
  if (executionPosition > methods.length) throw new TypeError(`${prefix}.executionPosition is out of range`);
  if (typeof episode.success !== 'boolean') throw new TypeError(`${prefix}.success must be boolean`);
  if (!Array.isArray(episode.rounds) || episode.rounds.length !== interactionLength) {
    throw new TypeError(`${prefix}.rounds must match interactionLength`);
  }
  if (taskId !== `${templateId}-L${interactionLength}`) throw new TypeError(`${prefix}.taskId does not match templateId and interactionLength`);
  if (templateFamilies.has(templateId) && templateFamilies.get(templateId) !== family) {
    throw new TypeError(`template ${templateId} maps to multiple families`);
  }
  templateFamilies.set(templateId, family);

  const technicalFailure = episode.rounds.some(round => FAILURE_STATUSES.has(round?.status));
  if (technicalFailure && episode.success) throw new TypeError(`${prefix} marks a registered technical failure as success`);
  const summary = summarizeEpisode(episode);
  const blockRows = blocks.get(blockId) ?? [];
  blockRows.push({ modelKey, method, taskId, templateId, family, interactionLength, trial, executionPosition });
  blocks.set(blockId, blockRows);
  const cellKey = [modelKey, templateId, interactionLength, method].join('\u001f');
  const cell = cellCounts.get(cellKey) ?? { count: 0, trials: new Set() };
  cell.count += 1;
  cell.trials.add(trial);
  cellCounts.set(cellKey, cell);

  rows.push({
    run_id: runId,
    block_id: blockId,
    model_key: modelKey,
    method,
    task_id: taskId,
    template_id: templateId,
    family,
    interaction_length: interactionLength,
    trial,
    execution_position: executionPosition,
    success: episode.success ? 1 : 0,
    technical_failure: technicalFailure ? 1 : 0,
    episode_unauthorized_change: summary.episodeUnauthorized ? 1 : 0,
    no_drift: summary.noDrift ? 1 : 0,
    first_drift_round: firstDriftRound(episode),
    goal_retention_auc: retentionAuc(episode),
    constraint_retention_auc: constraintRetentionAuc(episode),
    mean_scope_f1: summary.meanScopeF1,
    mean_turn_goal_completion: summary.meanTurnGoalCompletion,
  });
}

const modelKeys = unique(rows.map(row => row.model_key)).sort();
const templateIds = unique(rows.map(row => row.template_id)).sort();
if (modelKeys.length !== spec.design.modelIdentityCount) {
  throw new TypeError(`expected ${spec.design.modelIdentityCount} model identities, found ${modelKeys.length}`);
}
if (templateIds.length !== spec.design.taskTemplateCount) {
  throw new TypeError(`expected ${spec.design.taskTemplateCount} task templates, found ${templateIds.length}`);
}
if (!sameMembers(templateIds, registeredTemplates)) {
  throw new TypeError('dataset task templates do not match the frozen analysis specification');
}
if (rows.length !== spec.design.expectedEpisodes) {
  throw new TypeError(`expected ${spec.design.expectedEpisodes} episodes, found ${rows.length}`);
}
if (blocks.size !== spec.design.expectedBlocks) {
  throw new TypeError(`expected ${spec.design.expectedBlocks} blocks, found ${blocks.size}`);
}

for (const [blockId, blockRows] of blocks) {
  if (blockRows.length !== methods.length) throw new TypeError(`block ${blockId} does not contain all methods`);
  if (!sameMembers(blockRows.map(row => row.method), methods)) throw new TypeError(`block ${blockId} method set is invalid`);
  if (!sameMembers(blockRows.map(row => String(row.executionPosition)), methods.map((_, index) => String(index + 1)))) {
    throw new TypeError(`block ${blockId} execution positions are invalid`);
  }
  for (const field of ['modelKey', 'taskId', 'templateId', 'family', 'interactionLength', 'trial']) {
    if (unique(blockRows.map(row => row[field])).length !== 1) throw new TypeError(`block ${blockId} mixes ${field}`);
  }
}

for (const modelKey of modelKeys) {
  for (const templateId of templateIds) {
    for (const interactionLength of lengths) {
      for (const method of methods) {
        const key = [modelKey, templateId, interactionLength, method].join('\u001f');
        const cell = cellCounts.get(key);
        if (!cell || cell.count !== trialsPerCell || !sameMembers([...cell.trials].map(String), Array.from({ length: trialsPerCell }, (_, index) => String(index + 1)))) {
          throw new TypeError(`incomplete cell: ${modelKey}/${templateId}/L${interactionLength}/${method}`);
        }
      }
    }
  }
}

const positionCounts = {};
for (const modelKey of modelKeys) {
  for (const interactionLength of lengths) {
    const stratumRows = rows.filter(row => row.model_key === modelKey && row.interaction_length === interactionLength);
    const key = `${modelKey}:L${interactionLength}`;
    positionCounts[key] = {};
    for (const position of methods.map((_, index) => index + 1)) {
      const counts = Object.fromEntries(methods.map(method => [method, stratumRows.filter(row => row.method === method && row.execution_position === position).length]));
      if (Math.max(...Object.values(counts)) - Math.min(...Object.values(counts)) > 1) {
        throw new TypeError(`execution order is not balanced in ${key}/position-${position}`);
      }
      positionCounts[key][position] = counts;
    }
  }
}

const readinessBlockers = [];
if (spec.status !== 'frozen') readinessBlockers.push('mixed-effects model specification is not frozen');
if (spec.runtime?.status !== 'frozen-tested') readinessBlockers.push('R runtime and package versions are not frozen and tested');
if (dataset.formalExperiment !== true) readinessBlockers.push('dataset is not marked formalExperiment:true');
if (dataset.realModel !== true) readinessBlockers.push('dataset is not marked realModel:true');
if (dataset.annotationStats?.formalReady !== true) readinessBlockers.push('dataset lacks formal-ready annotation provenance');
for (const field of ['experimentId', 'benchmarkHash', 'benchmarkSourceHash', 'promptHash']) {
  if (typeof dataset[field] !== 'string' || !dataset[field]) readinessBlockers.push(`dataset lacks ${field} provenance`);
}
if (dataset.illustrative !== false) readinessBlockers.push('dataset is not explicitly marked illustrative:false');
if (dataset.pilot !== false) readinessBlockers.push('dataset is not explicitly marked pilot:false');
if (dataset.generationMode !== 'real-model-formal-runner') readinessBlockers.push('dataset generationMode is not the formal runner');
if (!hashPattern.test(dataset.manifestHash ?? '')) readinessBlockers.push('dataset lacks a valid manifestHash');
const currentAnalysisContract = {
  id: spec.analysisId,
  status: spec.status,
  specHash: canonicalHash(spec),
  runtimeStatus: spec.runtime?.status,
  runtimeVersions: spec.runtime?.lockedVersions ?? null,
  verifiedRuntimeVersions: spec.runtime?.verifiedVersions ?? null,
  preparationScriptHash: sha256(await readFile(import.meta.filename)),
  modelScriptHash: sha256(await readFile(join(root, 'analysis', 'mixed_effects.R'))),
  bootstrapScriptHash: sha256(await readFile(join(root, 'analysis', 'bootstrap.mjs'))),
  survivalScriptHash: sha256(await readFile(join(root, 'analysis', 'drift_survival.mjs'))),
  retentionScriptHash: sha256(await readFile(join(root, 'analysis', 'retention_curves.mjs'))),
  metricsModuleHash: sha256(await readFile(join(root, 'evaluation', 'metrics.mjs'))),
  scoringScriptHash: sha256(await readFile(join(root, 'evaluation', 'score.mjs'))),
  scoringAssemblyHash: sha256(await readFile(join(root, 'evaluation', 'assemble_scoring_dataset.py'))),
  annotationPreparationHash: sha256(await readFile(join(root, 'evaluation', 'prepare_blinded_annotations.py'))),
  annotationResolutionHash: sha256(await readFile(join(root, 'evaluation', 'annotation_resolution.py'))),
  annotationSchemaHash: sha256(await readFile(join(root, 'evaluation', 'annotations', 'schema.json'))),
  annotationPacketSchemaHash: sha256(await readFile(join(root, 'evaluation', 'annotations', 'packet-schema.json'))),
  annotationRubricHash: sha256(await readFile(join(root, 'research', 'annotation-rubric.md'))),
};
const analysisContractMatches = JSON.stringify(canonicalize(dataset.analysisContract)) === JSON.stringify(canonicalize(currentAnalysisContract));
const analysisContractHashMatches = dataset.analysisContract && typeof dataset.analysisContract === 'object' && !Array.isArray(dataset.analysisContract)
  && dataset.analysisContractHash === canonicalHash(dataset.analysisContract);
if (!analysisContractMatches) {
  readinessBlockers.push('dataset analysisContract does not match the selected model specification and scripts');
}
if (!analysisContractHashMatches) {
  readinessBlockers.push('dataset analysisContractHash is missing or invalid');
}
for (const field of ['benchmarkHash', 'benchmarkSourceHash', 'promptHash']) {
  if (dataset[field] !== undefined && !hashPattern.test(dataset[field])) readinessBlockers.push(`dataset ${field} is not a SHA-256 hash`);
}
const contractTestBlockers = [];
if (contractTest) {
  if (dataset.syntheticContractTest !== true) contractTestBlockers.push('contract-test input must declare syntheticContractTest:true');
  if (dataset.formalExperiment !== false || dataset.realModel !== false || dataset.illustrative !== true || dataset.pilot !== true) {
    contractTestBlockers.push('contract-test input must be explicitly synthetic, illustrative, pilot, non-formal and non-model');
  }
  if (dataset.generationMode !== 'synthetic-mixed-effects-contract-fixture') {
    contractTestBlockers.push('contract-test input has an unregistered generationMode');
  }
  if (!analysisContractMatches || !analysisContractHashMatches) {
    contractTestBlockers.push('contract-test analysis contract does not match the selected local assets');
  }
}
if ((!contractTest && readinessBlockers.length) || (contractTest && contractTestBlockers.length)) {
  console.error(JSON.stringify({
    prepared: false,
    formalAnalysisReady: false,
    readinessBlockers: contractTest ? contractTestBlockers : readinessBlockers,
  }, null, 2));
  process.exit(2);
}

const headers = Object.keys(rows[0]);
const csv = `${headers.join(',')}\n${rows.map(row => headers.map(header => csvCell(row[header])).join(',')).join('\n')}\n`;
const target = resolve(outputDirectory);
try {
  await access(target);
  throw new TypeError(`output directory already exists: ${target}`);
} catch (error) {
  if (error instanceof TypeError) throw error;
  if (error?.code !== 'ENOENT') throw error;
}
await mkdir(dirname(target), { recursive: true });
const staging = `${target}.tmp-${process.pid}`;
await mkdir(staging);
await writeFile(join(staging, 'episodes.csv'), csv, { encoding: 'utf8', flag: 'wx' });
await writeFile(join(staging, 'model-spec.snapshot.json'), specBytes, { flag: 'wx' });

const outcomeCounts = Object.fromEntries(modelKeys.map(modelKey => [modelKey, Object.fromEntries(methods.map(method => [method,
  Object.fromEntries(lengths.map(length => {
    const selected = rows.filter(row => row.model_key === modelKey && row.method === method && row.interaction_length === length);
    return [length, {
      episodes: selected.length,
      successes: selected.reduce((sum, row) => sum + row.success, 0),
      technicalFailures: selected.reduce((sum, row) => sum + row.technical_failure, 0),
    }];
  })),
]))]));
const manifest = {
  schemaVersion: 'mixed-effects-input-1.0',
  generatedAt: new Date().toISOString(),
  formalAnalysisReady: !contractTest,
  contractTest,
  formalReadinessBlockers: contractTest ? readinessBlockers : [],
  analysisId: spec.analysisId,
  sourceDataset: basename(resolve(inputPath)),
  sourceDatasetSha256: sha256(datasetBytes),
  modelSpecSha256: sha256(specBytes),
  provenance: {
    experimentId: dataset.experimentId,
    manifestHash: dataset.manifestHash,
    analysisContractHash: dataset.analysisContractHash,
    analysisContract: dataset.analysisContract,
    benchmarkHash: dataset.benchmarkHash,
    benchmarkSourceHash: dataset.benchmarkSourceHash,
    promptHash: dataset.promptHash,
  },
  designAudit: {
    episodes: rows.length,
    blocks: blocks.size,
    models: modelKeys,
    templates: templateIds,
    methods,
    interactionLengths: lengths,
    trialsPerCell,
    positionCounts,
    outcomeCounts,
  },
  files: {
    episodesCsv: { path: 'episodes.csv', sha256: sha256(csv) },
    modelSpecSnapshot: { path: 'model-spec.snapshot.json', sha256: sha256(specBytes) },
  },
};
await writeFile(join(staging, 'analysis-manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
await rename(staging, target);
process.stdout.write(`${JSON.stringify({ prepared: true, formalAnalysisReady: manifest.formalAnalysisReady, contractTest, outputDirectory: target, ...manifest.designAudit }, null, 2)}\n`);
