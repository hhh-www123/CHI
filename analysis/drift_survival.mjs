#!/usr/bin/env node
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { firstDriftRound } from '../evaluation/metrics.mjs';

const args = process.argv.slice(2);
const input = args.shift();
let output = null;
let modelKey = null;
let interactionLength = null;
let iterations = 2000;
let seed = 20261104;
while (args.length) {
  const argument = args.shift();
  if (argument === '--model-key') modelKey = args.shift();
  else if (argument === '--length') interactionLength = Number(args.shift());
  else if (argument === '--iterations') iterations = Number(args.shift());
  else if (argument === '--seed') seed = Number(args.shift());
  else if (output === null) output = argument;
  else throw new TypeError(`unknown argument: ${argument}`);
}
if (!input) {
  console.error('Usage: node analysis/drift_survival.mjs dataset.json [output.json] --model-key KEY --length N [--iterations N] [--seed N]');
  process.exit(2);
}
if (!Number.isInteger(iterations) || iterations < 100 || iterations > 100000) {
  throw new TypeError('--iterations must be an integer between 100 and 100000');
}
if (!Number.isInteger(seed)) throw new TypeError('--seed must be an integer');
if (interactionLength !== null && (!Number.isInteger(interactionLength) || interactionLength < 1)) {
  throw new TypeError('--length must be a positive integer');
}

const dataset = JSON.parse(await readFile(input, 'utf8'));
if (dataset?.schemaVersion !== '1.0' || !Array.isArray(dataset.episodes)) {
  throw new TypeError('dataset requires schemaVersion 1.0 and episodes[]');
}
const availableModels = [...new Set(dataset.episodes.map(item => item.modelKey).filter(item => typeof item === 'string' && item))].sort();
const episodeLength = episode => Number.isInteger(episode?.interactionLength)
  ? episode.interactionLength
  : (() => {
      const match = typeof episode?.taskId === 'string' ? episode.taskId.match(/-L(\d+)$/) : null;
      return match ? Number(match[1]) : null;
    })();
const availableLengths = [...new Set(dataset.episodes.map(episodeLength).filter(Number.isInteger))].sort((left, right) => left - right);
if (modelKey !== null && !availableModels.includes(modelKey)) {
  throw new TypeError(`--model-key must select one of: ${availableModels.join(', ')}`);
}
if (interactionLength !== null && !availableLengths.includes(interactionLength)) {
  throw new TypeError(`--length must select one of: ${availableLengths.join(', ')}`);
}
const selected = dataset.episodes.filter(episode => (
  (modelKey === null || episode.modelKey === modelKey)
  && (interactionLength === null || episodeLength(episode) === interactionLength)
));
if (!selected.length) throw new TypeError('selection contains no episodes');
const canonicalize = value => {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  return value;
};
const sha256 = value => createHash('sha256').update(value).digest('hex');
const canonicalHash = value => sha256(JSON.stringify(canonicalize(value)));

const METHODS = ['A', 'B', 'C', 'D'];
const average = values => values.reduce((sum, value) => sum + value, 0) / values.length;
const templateId = episode => typeof episode.templateId === 'string' && episode.templateId
  ? episode.templateId
  : episode.taskId.replace(/-L\d+$/, '');
const observations = selected.map((episode, index) => {
  if (!METHODS.includes(episode?.method)) throw new TypeError(`episodes[${index}].method is not registered`);
  const length = episodeLength(episode);
  if (!Number.isInteger(length) || !Array.isArray(episode.rounds) || episode.rounds.length !== length) {
    throw new TypeError(`episodes[${index}] has inconsistent interaction length`);
  }
  const event = firstDriftRound(episode);
  return {
    method: episode.method,
    templateId: templateId(episode),
    length,
    event,
    noDrift: event === null,
  };
});
const selectedLengths = [...new Set(observations.map(item => item.length))];
if (selectedLengths.length !== 1) {
  throw new TypeError('drift survival must select exactly one interaction length; provide --length');
}
const horizon = selectedLengths[0];
const templates = [...new Set(observations.map(item => item.templateId))].sort();

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
  const ordered = [...values].sort((left, right) => left - right);
  const position = (ordered.length - 1) * probability;
  const lower = Math.floor(position);
  const fraction = position - lower;
  return ordered[lower + 1] === undefined
    ? ordered[lower]
    : ordered[lower] + fraction * (ordered[lower + 1] - ordered[lower]);
};

const kaplanMeier = items => {
  let survival = 1;
  const curve = {};
  for (let round = 1; round <= horizon; round += 1) {
    const atRisk = items.filter(item => item.event === null || item.event >= round).length;
    const events = items.filter(item => item.event === round).length;
    const censored = round === horizon ? items.filter(item => item.event === null).length : 0;
    if (atRisk) survival *= 1 - events / atRisk;
    curve[round] = { survival, atRisk, events, censored };
  }
  return curve;
};
const sampledTemplateDraws = Array.from({ length: iterations }, () => (
  Array.from({ length: templates.length }, () => templates[Math.floor(random() * templates.length)])
));
const sampleClusters = (items, draw) => draw.flatMap(id => items.filter(item => item.templateId === id));
const byMethod = Object.fromEntries(METHODS.map(method => [method, observations.filter(item => item.method === method)]));
for (const method of METHODS) {
  const methodTemplates = [...new Set(byMethod[method].map(item => item.templateId))].sort();
  if (JSON.stringify(methodTemplates) !== JSON.stringify(templates)) {
    throw new TypeError(`method ${method} does not cover the same task templates`);
  }
}

const drawsByMethod = Object.fromEntries(METHODS.map(method => [method,
  sampledTemplateDraws.map(draw => kaplanMeier(sampleClusters(byMethod[method], draw))),
]));
const methods = {};
for (const method of METHODS) {
  const items = byMethod[method];
  const point = kaplanMeier(items);
  methods[method] = {
    episodes: items.length,
    taskTemplates: templates.length,
    events: items.filter(item => item.event !== null).length,
    administrativelyCensored: items.filter(item => item.event === null).length,
    noDriftRateAtHorizon: point[horizon].survival,
    meanDistortionFreeRounds: average(items.map(item => item.event === null ? horizon : item.event - 1)),
    normalizedMeanDistortionFreeRounds: average(items.map(item => item.event === null ? 1 : (item.event - 1) / horizon)),
    medianFirstDriftRound: Number(Object.keys(point).find(round => point[round].survival <= 0.5)) || null,
    curve: Object.fromEntries(Object.entries(point).map(([round, value]) => {
      const draws = drawsByMethod[method].map(curve => curve[round].survival);
      return [round, {
        ...value,
        clusterBootstrap95: { lower: percentile(draws, 0.025), upper: percentile(draws, 0.975) },
      }];
    })),
  };
}

const exactSignFlip = differences => {
  if (!differences.length || differences.length > 16) return null;
  const observed = Math.abs(average(differences));
  const assignments = 2 ** differences.length;
  let extreme = 0;
  for (let mask = 0; mask < assignments; mask += 1) {
    const statistic = Math.abs(average(differences.map((value, index) => mask & (2 ** index) ? value : -value)));
    if (statistic >= observed - 1e-15) extreme += 1;
  }
  return { p: extreme / assignments, assignments, mode: 'exact-enumeration' };
};
const comparisons = {};
for (const comparator of ['A', 'B', 'C']) {
  const key = `D-${comparator}`;
  const point = Object.fromEntries(Array.from({ length: horizon }, (_, index) => {
    const round = index + 1;
    return [round, methods.D.curve[round].survival - methods[comparator].curve[round].survival];
  }));
  const taskDifferences = templates.map(id => {
    const d = byMethod.D.filter(item => item.templateId === id);
    const other = byMethod[comparator].filter(item => item.templateId === id);
    return average(d.map(item => item.noDrift ? 1 : 0)) - average(other.map(item => item.noDrift ? 1 : 0));
  });
  comparisons[key] = {
    direction: `D minus ${comparator}`,
    noDriftRateDifferenceAtHorizon: point[horizon],
    horizonExactTaskSignFlip: exactSignFlip(taskDifferences),
    curveDifference: Object.fromEntries(Object.entries(point).map(([round, difference]) => {
      const draws = sampledTemplateDraws.map((_, drawIndex) => (
        drawsByMethod.D[drawIndex][round].survival - drawsByMethod[comparator][drawIndex][round].survival
      ));
      return [round, {
        difference,
        clusterBootstrap95: { lower: percentile(draws, 0.025), upper: percentile(draws, 0.975) },
      }];
    })),
  };
}

const readinessBlockers = [];
if (dataset.formalExperiment !== true) readinessBlockers.push('dataset is not marked as a formal experiment');
if (dataset.formalExperiment === true && dataset.annotationStats?.formalReady !== true) {
  readinessBlockers.push('formal dataset does not carry formal-ready annotation provenance');
}
if (dataset.formalExperiment === true && dataset.realModel !== true) readinessBlockers.push('formal dataset is not marked realModel:true');
if (dataset.formalExperiment === true && dataset.generationMode !== 'real-model-formal-runner') readinessBlockers.push('formal dataset generationMode is not the registered runner');
if (dataset.formalExperiment === true && !/^[a-f0-9]{64}$/.test(dataset.manifestHash ?? '')) readinessBlockers.push('formal dataset lacks a valid manifestHash');
if (dataset.formalExperiment === true && (
  !dataset.analysisContract
  || dataset.analysisContractHash !== canonicalHash(dataset.analysisContract)
  || dataset.analysisContract.survivalScriptHash !== sha256(await readFile(import.meta.filename))
  || dataset.analysisContract.metricsModuleHash !== sha256(await readFile(new URL('../evaluation/metrics.mjs', import.meta.url)))
)) readinessBlockers.push('formal dataset analysis contract does not match the running survival script');
if (modelKey === null) readinessBlockers.push('formal survival analysis requires an explicit --model-key');
if (interactionLength === null) readinessBlockers.push('formal survival analysis requires an explicit --length');
if (templates.length < 6) readinessBlockers.push('fewer than six independent task templates');

const result = {
  schemaVersion: '1.0',
  source: input,
  modelSelection: { selected: modelKey, available: availableModels },
  lengthSelection: { selected: interactionLength, available: availableLengths },
  estimand: {
    event: 'first registered distortion round',
    timeOrigin: 'round 1',
    administrativeCensoring: 'no registered distortion through the selected interaction length',
    stratification: 'separate model identity and interaction length',
  },
  uncertainty: {
    unit: 'task-template-with-all-trials-and-method-pairing',
    method: 'percentile cluster bootstrap',
    iterations,
    seed,
  },
  inferenceReady: readinessBlockers.length === 0,
  readinessBlockers,
  methods,
  comparisons,
  notes: [
    'Interaction lengths are never pooled in one risk set because shorter registered conditions are design censoring, not random loss to follow-up.',
    'Pointwise curve intervals resample task templates and retain all trials; they are not simultaneous confidence bands.',
    'Horizon sign-flip tests are secondary and unadjusted; they do not replace the registered length-20 task-success mixed model.',
    'A confidence interval containing zero is not evidence of equivalence.',
  ],
};
const serialized = `${JSON.stringify(result, null, 2)}\n`;
if (output) await writeFile(output, serialized, { encoding: 'utf8', flag: 'wx' });
else process.stdout.write(serialized);
