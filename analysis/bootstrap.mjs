#!/usr/bin/env node
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import {
  average,
  candidateMetrics,
  conflictMetrics,
  goalRetentionByAge,
  normalizedRetentionAuc,
  passHatK,
  staleGoalLeakage,
  summarizeEpisode,
} from '../evaluation/metrics.mjs';

const args = process.argv.slice(2);
const input = args.shift();
let output = null;
let iterations = 2000;
let seed = 20261103;
let modelKey = null;
let interactionLength = null;
while (args.length) {
  const item = args.shift();
  if (item === '--iterations') iterations = Number(args.shift());
  else if (item === '--seed') seed = Number(args.shift());
  else if (item === '--model-key') modelKey = args.shift();
  else if (item === '--length') interactionLength = Number(args.shift());
  else if (output === null) output = item;
  else throw new TypeError(`unknown argument: ${item}`);
}
if (!input) {
  console.error('Usage: node analysis/bootstrap.mjs dataset.json [output.json] [--iterations N] [--seed N] [--model-key KEY] [--length N]');
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
const availableModelKeys = [...new Set(dataset.episodes.map(item => item.modelKey).filter(item => typeof item === 'string' && item))].sort();
const episodeLength = episode => Number.isInteger(episode?.interactionLength)
  ? episode.interactionLength
  : (() => {
      const match = typeof episode?.taskId === 'string' ? episode.taskId.match(/-L(\d+)$/) : null;
      return match ? Number(match[1]) : null;
    })();
const availableInteractionLengths = [...new Set(dataset.episodes.map(episodeLength).filter(Number.isInteger))].sort((left, right) => left - right);
if (modelKey !== null && (typeof modelKey !== 'string' || !modelKey || !availableModelKeys.includes(modelKey))) {
  throw new TypeError(`--model-key must select one of: ${availableModelKeys.join(', ')}`);
}
if (interactionLength !== null && !availableInteractionLengths.includes(interactionLength)) {
  throw new TypeError(`--length must select one of: ${availableInteractionLengths.join(', ')}`);
}
const selectedEpisodes = dataset.episodes.filter(item => (
  (modelKey === null || item.modelKey === modelKey)
  && (interactionLength === null || episodeLength(item) === interactionLength)
));
const canonicalize = value => {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  return value;
};
const sha256 = value => createHash('sha256').update(value).digest('hex');
const canonicalHash = value => sha256(JSON.stringify(canonicalize(value)));

const finiteAverage = values => {
  const usable = values.filter(value => typeof value === 'number' && Number.isFinite(value));
  return usable.length ? average(usable) : null;
};
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

const pairedSignFlip = differences => {
  if (!differences.length) return null;
  const observed = Math.abs(average(differences));
  if (differences.length <= 16) {
    const assignments = 2 ** differences.length;
    let extreme = 0;
    for (let mask = 0; mask < assignments; mask += 1) {
      const statistic = Math.abs(average(differences.map((value, index) => (
        mask & (2 ** index) ? value : -value
      ))));
      if (statistic >= observed - 1e-15) extreme += 1;
    }
    return {
      p: extreme / assignments,
      mode: 'exact-enumeration',
      assignments,
      assignmentResolution: 1 / assignments,
    };
  }
  let extreme = 0;
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const statistic = Math.abs(average(differences.map(value => random() < 0.5 ? value : -value)));
    if (statistic >= observed - 1e-15) extreme += 1;
  }
  return {
    p: (extreme + 1) / (iterations + 1),
    mode: 'monte-carlo',
    assignments: iterations,
    assignmentResolution: 1 / (iterations + 1),
  };
};

const METRICS = [
  'taskSuccessRate',
  'episodeUnauthorizedRate',
  'meanScopeF1',
  'meanTurnGoalCompletion',
  'meanComponentIdSurvivalRate',
  'meanActivityIdSurvivalRate',
  'meanGoalRetentionAuc',
  'staleGoalLeakageRate',
  'noDriftRate',
  'passHat3',
  'versionRecoverySuccessRate',
  'goalUpdateSuccessRate',
  'conflictF1',
  'conflictExactDecisionAccuracy',
  'meanCandidateNdcg',
  'meanCandidateTop1Regret',
  'candidateTop1HumanBestRate',
];

const aggregateGoalCurve = episodes => {
  const buckets = new Map();
  for (const episode of episodes) {
    for (const [age, item] of Object.entries(goalRetentionByAge(episode))) {
      const bucket = buckets.get(age) ?? { satisfiedWeight: 0, totalWeight: 0, observations: 0 };
      bucket.satisfiedWeight += item.satisfiedWeight;
      bucket.totalWeight += item.totalWeight;
      bucket.observations += item.observations;
      buckets.set(age, bucket);
    }
  }
  return Object.fromEntries([...buckets.entries()].map(([age, item]) => [age, {
    retention: item.totalWeight ? item.satisfiedWeight / item.totalWeight : null,
    observations: item.observations,
  }]));
};

const summarizeTask = episodes => {
  const summaries = episodes.map(summarizeEpisode);
  const rounds = episodes.flatMap(item => item.rounds);
  const candidateBatches = rounds
    .filter(item => Array.isArray(item.candidates))
    .map(item => candidateMetrics(item.candidates, item.candidateTargetCount ?? null));
  const conflict = conflictMetrics(rounds);
  const stale = episodes.map(staleGoalLeakage);
  const staleTested = stale.reduce((sum, item) => sum + item.tested, 0);
  const staleLeaked = stale.reduce((sum, item) => sum + item.leaked, 0);
  return {
    taskSuccessRate: average(episodes.map(item => item.success ? 1 : 0)),
    episodeUnauthorizedRate: average(summaries.map(item => item.episodeUnauthorized ? 1 : 0)),
    meanScopeF1: finiteAverage(summaries.map(item => item.meanScopeF1)),
    meanTurnGoalCompletion: finiteAverage(summaries.map(item => item.meanTurnGoalCompletion)),
    meanComponentIdSurvivalRate: finiteAverage(summaries.map(item => item.meanComponentIdSurvivalRate)),
    meanActivityIdSurvivalRate: finiteAverage(summaries.map(item => item.meanActivityIdSurvivalRate)),
    meanGoalRetentionAuc: normalizedRetentionAuc(aggregateGoalCurve(episodes)),
    staleGoalLeakageRate: staleTested ? staleLeaked / staleTested : null,
    noDriftRate: average(summaries.map(item => item.noDrift ? 1 : 0)),
    passHat3: passHatK(episodes, 3)?.value ?? null,
    versionRecoverySuccessRate: (() => {
      const values = episodes.map(item => item.versionRecovery).filter(Boolean);
      return values.length ? average(values.map(item => item.success ? 1 : 0)) : null;
    })(),
    goalUpdateSuccessRate: (() => {
      const values = episodes.map(item => item.goalUpdate).filter(Boolean);
      return values.length ? average(values.map(item => item.success ? 1 : 0)) : null;
    })(),
    conflictF1: conflict?.f1 ?? null,
    conflictExactDecisionAccuracy: conflict?.exactDecisionAccuracy ?? null,
    meanCandidateNdcg: finiteAverage(candidateBatches.map(item => item.ndcg)),
    meanCandidateTop1Regret: finiteAverage(candidateBatches.map(item => item.top1Regret)),
    candidateTop1HumanBestRate: finiteAverage(candidateBatches.map(item => item.top1HumanBest === null ? null : item.top1HumanBest ? 1 : 0)),
  };
};

const summarizeTasks = taskSummaries => Object.fromEntries(
  METRICS.map(metric => [metric, finiteAverage(taskSummaries.map(item => item[metric]))]),
);

const grouped = new Map();
for (const episode of selectedEpisodes) {
  if (typeof episode?.method !== 'string' || !episode.method || typeof episode.taskId !== 'string' || !episode.taskId) {
    throw new TypeError('episodes require non-empty method and taskId');
  }
  const method = grouped.get(episode.method) ?? new Map();
  const templateId = typeof episode.templateId === 'string' && episode.templateId
    ? episode.templateId
    : episode.taskId.replace(/-L\d+$/, '');
  const task = method.get(templateId) ?? [];
  task.push(episode);
  method.set(templateId, task);
  grouped.set(episode.method, method);
}

const byMethod = new Map();
for (const [method, tasks] of grouped) {
  byMethod.set(method, new Map([...tasks].map(([taskId, episodes]) => [taskId, summarizeTask(episodes)])));
}

const bootstrapMeans = taskSummaries => {
  const draws = Object.fromEntries(METRICS.map(metric => [metric, []]));
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const sample = Array.from({ length: taskSummaries.length }, () => taskSummaries[Math.floor(random() * taskSummaries.length)]);
    const summary = summarizeTasks(sample);
    for (const metric of METRICS) {
      if (summary[metric] !== null) draws[metric].push(summary[metric]);
    }
  }
  return Object.fromEntries(METRICS.map(metric => [metric, draws[metric].length ? {
    lower: percentile(draws[metric], 0.025),
    upper: percentile(draws[metric], 0.975),
  } : null]));
};

const methods = {};
for (const [method, tasks] of byMethod) {
  const summaries = [...tasks.values()];
  methods[method] = {
    taskCount: tasks.size,
    episodeCount: [...grouped.get(method).values()].reduce((sum, episodes) => sum + episodes.length, 0),
    point: summarizeTasks(summaries),
    ci95: bootstrapMeans(summaries),
  };
}

const comparisons = {};
const holmEntries = [];
const methodNames = [...byMethod.keys()].sort();
for (let leftIndex = 0; leftIndex < methodNames.length; leftIndex += 1) {
  for (let rightIndex = leftIndex + 1; rightIndex < methodNames.length; rightIndex += 1) {
    const leftName = methodNames[leftIndex];
    const rightName = methodNames[rightIndex];
    const left = byMethod.get(leftName);
    const right = byMethod.get(rightName);
    const matched = [...left.keys()].filter(taskId => right.has(taskId)).sort();
    if (!matched.length) continue;
    const pointLeft = summarizeTasks(matched.map(taskId => left.get(taskId)));
    const pointRight = summarizeTasks(matched.map(taskId => right.get(taskId)));
    const point = Object.fromEntries(METRICS.map(metric => [metric,
      pointLeft[metric] === null || pointRight[metric] === null ? null : pointRight[metric] - pointLeft[metric],
    ]));
    const draws = Object.fromEntries(METRICS.map(metric => [metric, []]));
    for (let iteration = 0; iteration < iterations; iteration += 1) {
      const sampledIds = Array.from({ length: matched.length }, () => matched[Math.floor(random() * matched.length)]);
      const sampledLeft = summarizeTasks(sampledIds.map(taskId => left.get(taskId)));
      const sampledRight = summarizeTasks(sampledIds.map(taskId => right.get(taskId)));
      for (const metric of METRICS) {
        if (sampledLeft[metric] !== null && sampledRight[metric] !== null) {
          draws[metric].push(sampledRight[metric] - sampledLeft[metric]);
        }
      }
    }
    const permutationP = {};
    const signFlip = {};
    for (const metric of METRICS) {
      const differences = matched
        .map(taskId => [left.get(taskId)[metric], right.get(taskId)[metric]])
        .filter(([leftValue, rightValue]) => leftValue !== null && rightValue !== null)
        .map(([leftValue, rightValue]) => rightValue - leftValue);
      if (!differences.length) {
        permutationP[metric] = null;
        signFlip[metric] = null;
        continue;
      }
      signFlip[metric] = pairedSignFlip(differences);
      permutationP[metric] = signFlip[metric].p;
      if ((leftName === 'D' || rightName === 'D') && metric === 'taskSuccessRate' && interactionLength === 20) {
        holmEntries.push({ comparison: `${rightName}-${leftName}`, metric, p: permutationP[metric] });
      }
    }
    comparisons[`${rightName}-${leftName}`] = {
      direction: `${rightName} minus ${leftName}`,
      matchedTaskCount: matched.length,
      point,
      pairedPermutationP: permutationP,
      pairedSignFlip: signFlip,
      ci95: Object.fromEntries(METRICS.map(metric => [metric, draws[metric].length ? {
        lower: percentile(draws[metric], 0.025),
        upper: percentile(draws[metric], 0.975),
      } : null])),
    };
  }
}

holmEntries.sort((left, right) => left.p - right.p);
let previousAdjusted = 0;
for (let index = 0; index < holmEntries.length; index += 1) {
  const item = holmEntries[index];
  const adjusted = Math.min(1, Math.max(previousAdjusted, item.p * (holmEntries.length - index)));
  previousAdjusted = adjusted;
  const comparison = comparisons[item.comparison];
  comparison.holmAdjustedP ??= {};
  comparison.holmAdjustedP[item.metric] = adjusted;
}

const readinessBlockers = [];
for (const method of methodNames) {
  if (byMethod.get(method).size < 6) readinessBlockers.push(`${method} has fewer than 6 independent tasks`);
}
for (const method of ['A', 'B', 'C', 'D']) {
  if (!byMethod.has(method)) readinessBlockers.push(`missing method ${method}`);
}
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
  || dataset.analysisContract.bootstrapScriptHash !== sha256(await readFile(import.meta.filename))
  || dataset.analysisContract.metricsModuleHash !== sha256(await readFile(new URL('../evaluation/metrics.mjs', import.meta.url)))
)) readinessBlockers.push('formal dataset analysis contract does not match the running bootstrap script');
if (modelKey === null && availableModelKeys.length > 1) {
  readinessBlockers.push('multiple models are pooled; run one bootstrap per --model-key for model-specific inference');
}
if (dataset.formalExperiment === true && interactionLength !== 20) {
  readinessBlockers.push('registered primary robustness inference requires --length 20');
}

const result = {
  schemaVersion: '1.0',
  source: input,
  modelSelection: { selected: modelKey, available: availableModelKeys },
  lengthSelection: { selected: interactionLength, available: availableInteractionLengths },
  bootstrap: {
    unit: interactionLength === null ? 'task-template-with-all-lengths-and-trials' : 'task-template-with-selected-length-and-all-trials',
    iterations,
    seed,
    interval: 'percentile-95',
  },
  registeredPrimarySensitivity: {
    endpoint: 'taskSuccessRate',
    interactionLength: 20,
    comparisons: ['D-A', 'D-B', 'D-C'],
    multiplicity: 'Holm within each separately selected model identity',
  },
  inferenceReady: readinessBlockers.length === 0,
  readinessBlockers,
  methods,
  comparisons,
  notes: [
    'Intervals resample the six task templates; all lengths and trials remain grouped within a draw.',
    'Two-sided paired sign-flip tests use exact enumeration for up to 16 matched task templates; larger sets use the registered Monte Carlo iteration count.',
    'Holm adjustment is emitted only for the registered length-20 task-success D-vs-A/B/C robustness family within one selected model identity.',
    'These intervals do not replace the preregistered mixed-effects models for the final paper.',
    'A difference interval containing zero is not evidence of equivalence.',
  ],
};
const serialized = `${JSON.stringify(result, null, 2)}\n`;
if (output) await writeFile(output, serialized, { encoding: 'utf8', flag: 'wx' });
else process.stdout.write(serialized);
