#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import {
  constraintRetentionByAge,
  goalRetentionByAge,
  normalizedRetentionAuc,
} from '../evaluation/metrics.mjs';

const args = process.argv.slice(2);
const input = args.shift();
let output = null;
let modelKey = null;
let iterations = 2000;
let seed = 20261104;
while (args.length) {
  const argument = args.shift();
  if (argument === '--model-key') modelKey = args.shift();
  else if (argument === '--iterations') iterations = Number(args.shift());
  else if (argument === '--seed') seed = Number(args.shift());
  else if (output === null) output = argument;
  else throw new TypeError(`unknown argument: ${argument}`);
}
if (!input) {
  console.error('Usage: node analysis/retention_curves.mjs dataset.json [output.json] --model-key KEY [--iterations N] [--seed N]');
  process.exit(2);
}
if (!Number.isInteger(iterations) || iterations < 100 || iterations > 100000) {
  throw new TypeError('--iterations must be an integer between 100 and 100000');
}
if (!Number.isInteger(seed)) throw new TypeError('--seed must be an integer');

const dataset = JSON.parse(await readFile(input, 'utf8'));
if (dataset?.schemaVersion !== '1.0' || !Array.isArray(dataset.episodes)) {
  throw new TypeError('dataset requires schemaVersion 1.0 and episodes[]');
}
const availableModels = [...new Set(dataset.episodes.map(item => item.modelKey).filter(item => typeof item === 'string' && item))].sort();
if (modelKey !== null && !availableModels.includes(modelKey)) {
  throw new TypeError(`--model-key must select one of: ${availableModels.join(', ')}`);
}
const selected = dataset.episodes.filter(item => modelKey === null || item.modelKey === modelKey);
if (!selected.length) throw new TypeError('selection contains no episodes');

const METHODS = ['A', 'B', 'C', 'D'];
const templateId = episode => typeof episode.templateId === 'string' && episode.templateId
  ? episode.templateId
  : episode.taskId.replace(/-L\d+$/, '');
for (const [index, episode] of selected.entries()) {
  if (!METHODS.includes(episode?.method)) throw new TypeError(`episodes[${index}].method is not registered`);
  if (typeof episode.taskId !== 'string' || !episode.taskId || !Array.isArray(episode.rounds)) {
    throw new TypeError(`episodes[${index}] lacks task/round data`);
  }
}
const templates = [...new Set(selected.map(templateId))].sort();
const byMethod = Object.fromEntries(METHODS.map(method => [method, selected.filter(item => item.method === method)]));
for (const method of METHODS) {
  const covered = [...new Set(byMethod[method].map(templateId))].sort();
  if (JSON.stringify(covered) !== JSON.stringify(templates)) throw new TypeError(`method ${method} task-template coverage differs`);
}

const canonicalize = value => {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  return value;
};
const sha256 = value => createHash('sha256').update(value).digest('hex');
const canonicalHash = value => sha256(JSON.stringify(canonicalize(value)));
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
const random = mulberry32(seed);
const percentile = (values, probability) => {
  if (!values.length) return null;
  const ordered = [...values].sort((left, right) => left - right);
  const position = (ordered.length - 1) * probability;
  const lower = Math.floor(position);
  const fraction = position - lower;
  return ordered[lower + 1] === undefined
    ? ordered[lower]
    : ordered[lower] + fraction * (ordered[lower + 1] - ordered[lower]);
};
const draws = Array.from({ length: iterations }, () => (
  Array.from({ length: templates.length }, () => templates[Math.floor(random() * templates.length)])
));
const sampleClusters = (items, draw) => draw.flatMap(id => items.filter(item => templateId(item) === id));

const aggregateCurve = (episodes, metric) => {
  const buckets = new Map();
  for (const episode of episodes) {
    for (const [age, value] of Object.entries(metric(episode))) {
      const bucket = buckets.get(age) ?? { satisfiedWeight: 0, totalWeight: 0, observations: 0 };
      bucket.satisfiedWeight += value.satisfiedWeight;
      bucket.totalWeight += value.totalWeight;
      bucket.observations += value.observations;
      buckets.set(age, bucket);
    }
  }
  return Object.fromEntries([...buckets.entries()].sort((left, right) => Number(left[0]) - Number(right[0])).map(([age, value]) => [age, {
    retention: value.totalWeight ? value.satisfiedWeight / value.totalWeight : null,
    satisfiedWeight: value.satisfiedWeight,
    totalWeight: value.totalWeight,
    observations: value.observations,
  }]));
};
const auc = curve => {
  const usable = Object.fromEntries(Object.entries(curve).filter(([, item]) => typeof item.retention === 'number'));
  return Object.keys(usable).length ? normalizedRetentionAuc(usable) : null;
};

const analyzeMetric = (id, metric) => {
  const methodDraws = Object.fromEntries(METHODS.map(method => [method,
    draws.map(draw => aggregateCurve(sampleClusters(byMethod[method], draw), metric)),
  ]));
  const methods = {};
  const ages = [...new Set(METHODS.flatMap(method => Object.keys(aggregateCurve(byMethod[method], metric))))].sort((left, right) => Number(left) - Number(right));
  for (const method of METHODS) {
    const point = aggregateCurve(byMethod[method], metric);
    const aucDraws = methodDraws[method].map(auc).filter(value => value !== null);
    methods[method] = {
      episodes: byMethod[method].length,
      taskTemplates: templates.length,
      auc: auc(point),
      aucClusterBootstrap95: aucDraws.length ? {
        lower: percentile(aucDraws, 0.025),
        upper: percentile(aucDraws, 0.975),
        usableDraws: aucDraws.length,
      } : null,
      curve: Object.fromEntries(ages.map(age => {
        const value = point[age] ?? { retention: null, satisfiedWeight: 0, totalWeight: 0, observations: 0 };
        const ageDraws = methodDraws[method]
          .map(curve => curve[age]?.retention)
          .filter(item => typeof item === 'number');
        return [age, {
          ...value,
          clusterBootstrap95: ageDraws.length ? {
            lower: percentile(ageDraws, 0.025), upper: percentile(ageDraws, 0.975), usableDraws: ageDraws.length,
          } : null,
        }];
      })),
    };
  }
  const comparisons = {};
  for (const comparator of ['A', 'B', 'C']) {
    comparisons[`D-${comparator}`] = {
      direction: `D minus ${comparator}`,
      aucDifference: methods.D.auc === null || methods[comparator].auc === null ? null : methods.D.auc - methods[comparator].auc,
      curveDifference: Object.fromEntries(ages.map(age => {
        const left = methods.D.curve[age].retention;
        const right = methods[comparator].curve[age].retention;
        const pairedDraws = draws.map((_, index) => {
          const d = methodDraws.D[index][age]?.retention;
          const other = methodDraws[comparator][index][age]?.retention;
          return typeof d === 'number' && typeof other === 'number' ? d - other : null;
        }).filter(value => value !== null);
        return [age, {
          difference: typeof left === 'number' && typeof right === 'number' ? left - right : null,
          clusterBootstrap95: pairedDraws.length ? {
            lower: percentile(pairedDraws, 0.025), upper: percentile(pairedDraws, 0.975), usableDraws: pairedDraws.length,
          } : null,
        }];
      })),
    };
  }
  return { id, ages: ages.map(Number), methods, comparisons };
};

const readinessBlockers = [];
if (dataset.formalExperiment !== true) readinessBlockers.push('dataset is not marked as a formal experiment');
if (dataset.formalExperiment === true && dataset.realModel !== true) readinessBlockers.push('formal dataset is not marked realModel:true');
if (dataset.formalExperiment === true && dataset.annotationStats?.formalReady !== true) readinessBlockers.push('formal dataset lacks formal-ready annotation provenance');
if (dataset.formalExperiment === true && dataset.generationMode !== 'real-model-formal-runner') readinessBlockers.push('formal dataset generationMode is not the registered runner');
if (dataset.formalExperiment === true && !/^[a-f0-9]{64}$/.test(dataset.manifestHash ?? '')) readinessBlockers.push('formal dataset lacks a valid manifestHash');
if (dataset.formalExperiment === true && (
  !dataset.analysisContract
  || dataset.analysisContractHash !== canonicalHash(dataset.analysisContract)
  || dataset.analysisContract.retentionScriptHash !== sha256(await readFile(import.meta.filename))
  || dataset.analysisContract.metricsModuleHash !== sha256(await readFile(new URL('../evaluation/metrics.mjs', import.meta.url)))
)) readinessBlockers.push('formal dataset analysis contract does not match the running retention script');
if (modelKey === null) readinessBlockers.push('formal retention analysis requires an explicit --model-key');
if (templates.length < 6) readinessBlockers.push('fewer than six independent task templates');

const result = {
  schemaVersion: '1.0',
  source: input,
  modelSelection: { selected: modelKey, available: availableModels },
  inferenceReady: readinessBlockers.length === 0,
  readinessBlockers,
  uncertainty: { unit: 'task-template-with-all-lengths-and-trials', method: 'percentile cluster bootstrap', iterations, seed },
  goalRetention: analyzeMetric('goal-retention-by-age', goalRetentionByAge),
  constraintRetention: analyzeMetric('constraint-retention-by-age', constraintRetentionByAge),
  notes: [
    'Goal age is current round minus introduced round; goal-update entries marked retentionEligible:false are excluded from goal retention.',
    'Each bootstrap draw resamples task templates and retains every registered length, trial and method pairing inside the sampled template.',
    'AUC uses the non-equidistant normalized trapezoid over observed ages, never a simple average of age points.',
    'Pointwise intervals are not simultaneous confidence bands; differences are secondary and unadjusted.',
  ],
};
const serialized = `${JSON.stringify(result, null, 2)}\n`;
if (output) await writeFile(output, serialized, { encoding: 'utf8', flag: 'wx' });
else process.stdout.write(serialized);
