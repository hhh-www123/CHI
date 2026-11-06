#!/usr/bin/env python3
"""Build an auditable A-D run manifest and enforce formal-run gates.

The script stores configuration metadata but never credentials. It deliberately
refuses --formal until tasks/methods/analysis are frozen, adapters are runnable,
the Git tree is clean, and a real provider/model/prompt are identified.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
from urllib.parse import urlsplit

try:
    from .prompt_bundle import DEFAULT_DIRECTORY as DEFAULT_PROMPTS, load_bundle
    from .task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, canonical_hash, compile_suite, read_json
except ImportError:  # direct script execution
    from prompt_bundle import DEFAULT_DIRECTORY as DEFAULT_PROMPTS, load_bundle
    from task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, canonical_hash, compile_suite, read_json


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_METHODS = ROOT / "evaluation" / "methods.json"
DEFAULT_MODELS = ROOT / "evaluation" / "models.json"
DEFAULT_ANALYSIS_SPEC = ROOT / "analysis" / "model-spec.json"
DEFAULT_ANALYSIS_PREPARER = ROOT / "analysis" / "prepare_mixed_effects.mjs"
DEFAULT_ANALYSIS_MODEL = ROOT / "analysis" / "mixed_effects.R"
DEFAULT_ANALYSIS_BOOTSTRAP = ROOT / "analysis" / "bootstrap.mjs"
DEFAULT_ANALYSIS_SURVIVAL = ROOT / "analysis" / "drift_survival.mjs"
DEFAULT_ANALYSIS_RETENTION = ROOT / "analysis" / "retention_curves.mjs"
DEFAULT_METRICS_MODULE = ROOT / "evaluation" / "metrics.mjs"
DEFAULT_SCORING_SCRIPT = ROOT / "evaluation" / "score.mjs"
DEFAULT_SCORING_ASSEMBLY = ROOT / "evaluation" / "assemble_scoring_dataset.py"
DEFAULT_ANNOTATION_PREPARATION = ROOT / "evaluation" / "prepare_blinded_annotations.py"
DEFAULT_ANNOTATION_RESOLUTION = ROOT / "evaluation" / "annotation_resolution.py"
DEFAULT_ANNOTATION_SCHEMA = ROOT / "evaluation" / "annotations" / "schema.json"
DEFAULT_ANNOTATION_PACKET_SCHEMA = ROOT / "evaluation" / "annotations" / "packet-schema.json"
DEFAULT_ANNOTATION_RUBRIC = ROOT / "research" / "annotation-rubric.md"
METHOD_IDS = ("A", "B", "C", "D")
PLACEHOLDERS = {"", "unset", "unknown", "todo", "placeholder"}


def git_metadata() -> dict:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
        )
        return result.stdout.strip()

    try:
        commit = run("rev-parse", "HEAD")
        status = run("status", "--porcelain")
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("a readable Git worktree is required") from error
    return {"commit": commit, "dirty": bool(status), "dirtyEntries": status.splitlines()}


def validate_methods(config: dict) -> dict[str, dict]:
    if config.get("schemaVersion") != "1.0" or not isinstance(config.get("methods"), list):
        raise ValueError("methods config requires schemaVersion 1.0 and methods[]")
    methods: dict[str, dict] = {}
    for item in config["methods"]:
        if not isinstance(item, dict) or item.get("id") not in METHOD_IDS:
            raise ValueError("method entries require one of ids A, B, C, D")
        if item["id"] in methods:
            raise ValueError(f"duplicate method {item['id']}")
        for key in ("outputContract", "candidateCount", "formalRunnable", "implementationStatus"):
            if key not in item:
                raise ValueError(f"method {item['id']} is missing {key}")
        methods[item["id"]] = item
    if set(methods) != set(METHOD_IDS):
        raise ValueError("methods config must define A, B, C and D exactly once")
    return methods


def is_configured(value) -> bool:
    return isinstance(value, str) and value.strip().lower() not in PLACEHOLDERS


def validate_models(config: dict) -> list[dict]:
    if config.get("schemaVersion") != "1.0" or config.get("design") != "two-model replication":
        raise ValueError("models config requires schemaVersion 1.0 and the accepted two-model design")
    models = config.get("models")
    if not isinstance(models, list) or len(models) != 2:
        raise ValueError("the accepted design requires exactly two model entries")
    keys = []
    required = {
        "key", "provider", "baseUrl", "model", "versionDate", "temperature",
        "maxOutputTokens", "timeoutSeconds", "seedSupport", "apiKeyEnv",
        "inputCostPerMillionUsd", "outputCostPerMillionUsd", "dataRetentionNotes",
    }
    for item in models:
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("each model entry must contain the exact registered metadata fields")
        if not isinstance(item["key"], str) or not item["key"]:
            raise ValueError("model key must be a non-empty string")
        keys.append(item["key"])
        if not isinstance(item["temperature"], (int, float)) or isinstance(item["temperature"], bool) or not 0 <= item["temperature"] <= 2:
            raise ValueError(f"invalid temperature for model {item['key']}")
        if not isinstance(item["maxOutputTokens"], int) or not 256 <= item["maxOutputTokens"] <= 100000:
            raise ValueError(f"invalid maxOutputTokens for model {item['key']}")
        if not isinstance(item["timeoutSeconds"], (int, float)) or not 5 <= item["timeoutSeconds"] <= 300:
            raise ValueError(f"invalid timeoutSeconds for model {item['key']}")
        if not isinstance(item["seedSupport"], bool):
            raise ValueError(f"seedSupport must be boolean for model {item['key']}")
        if not isinstance(item["apiKeyEnv"], str) or not item["apiKeyEnv"].replace("_", "").isalnum():
            raise ValueError(f"invalid apiKeyEnv for model {item['key']}")
    if len(set(keys)) != 2:
        raise ValueError("model keys must be unique")
    return models


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError(f"cannot read registered analysis asset: {path}") from error


def load_analysis_contract(
    spec_path: Path,
    preparer_path: Path,
    model_path: Path,
    bootstrap_path: Path = DEFAULT_ANALYSIS_BOOTSTRAP,
    survival_path: Path = DEFAULT_ANALYSIS_SURVIVAL,
    retention_path: Path = DEFAULT_ANALYSIS_RETENTION,
    metrics_path: Path = DEFAULT_METRICS_MODULE,
    scoring_path: Path = DEFAULT_SCORING_SCRIPT,
    assembly_path: Path = DEFAULT_SCORING_ASSEMBLY,
    annotation_preparation_path: Path = DEFAULT_ANNOTATION_PREPARATION,
    annotation_resolution_path: Path = DEFAULT_ANNOTATION_RESOLUTION,
    annotation_schema_path: Path = DEFAULT_ANNOTATION_SCHEMA,
    annotation_packet_schema_path: Path = DEFAULT_ANNOTATION_PACKET_SCHEMA,
    annotation_rubric_path: Path = DEFAULT_ANNOTATION_RUBRIC,
) -> tuple[dict, dict]:
    spec = read_json(spec_path)
    if spec.get("schemaVersion") != "1.0" or spec.get("analysisId") != "travel-long-horizon-primary-v1":
        raise ValueError("unsupported mixed-effects analysis specification")
    if spec.get("confirmatory", {}).get("primaryEndpoint", {}).get("targetInteractionLength") != 20:
        raise ValueError("analysis specification must register the 20-round primary endpoint")
    if spec.get("confirmatory", {}).get("primaryContrasts") != ["D-A", "D-B", "D-C"]:
        raise ValueError("analysis specification must register D-A, D-B and D-C primary contrasts")
    templates = spec.get("design", {}).get("taskTemplates")
    if not isinstance(templates, list) or len(templates) != 6 or len(set(templates)) != 6 or any(not isinstance(item, str) or not item for item in templates):
        raise ValueError("analysis specification must register six unique task templates")
    runtime = spec.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("analysis specification requires runtime metadata")
    locked_versions = runtime.get("lockedVersions")
    if locked_versions is not None and (
        not isinstance(locked_versions, dict)
        or not locked_versions
        or any(not isinstance(key, str) or not key or not isinstance(value, str) or not value for key, value in locked_versions.items())
    ):
        raise ValueError("analysis runtime lockedVersions must be a non-empty string mapping")
    contract = {
        "id": spec["analysisId"],
        "status": spec.get("status"),
        "specHash": canonical_hash(spec),
        "runtimeStatus": runtime.get("status"),
        "runtimeVersions": deepcopy(locked_versions),
        "verifiedRuntimeVersions": deepcopy(runtime.get("verifiedVersions")),
        "preparationScriptHash": file_sha256(preparer_path),
        "modelScriptHash": file_sha256(model_path),
        "bootstrapScriptHash": file_sha256(bootstrap_path),
        "survivalScriptHash": file_sha256(survival_path),
        "retentionScriptHash": file_sha256(retention_path),
        "metricsModuleHash": file_sha256(metrics_path),
        "scoringScriptHash": file_sha256(scoring_path),
        "scoringAssemblyHash": file_sha256(assembly_path),
        "annotationPreparationHash": file_sha256(annotation_preparation_path),
        "annotationResolutionHash": file_sha256(annotation_resolution_path),
        "annotationSchemaHash": file_sha256(annotation_schema_path),
        "annotationPacketSchemaHash": file_sha256(annotation_packet_schema_path),
        "annotationRubricHash": file_sha256(annotation_rubric_path),
    }
    return spec, contract


def build_blockers(
    args,
    suite: dict,
    methods_config: dict,
    selected: list[dict],
    git: dict,
    prompts: dict,
    models_config: dict,
    models: list[dict],
    analysis_spec: dict,
) -> list[str]:
    blockers: list[str] = []
    if suite["sourceStatus"] != "frozen":
        blockers.append("task suite is not frozen")
    if methods_config.get("status") != "frozen":
        blockers.append("method contract is not frozen")
    unavailable = [item["id"] for item in selected if item.get("formalRunnable") is not True]
    if unavailable:
        blockers.append("formal experiment adapters are not runnable: " + ", ".join(unavailable))
    if set(args.method) != set(METHOD_IDS) or len(args.method) != len(METHOD_IDS):
        blockers.append("core formal experiment must include methods A B C D exactly once")
    if git["dirty"]:
        blockers.append("Git worktree is dirty")
    if models_config.get("status") != "frozen":
        blockers.append("two-model configuration is not frozen")
    budget = models_config.get("budgetCapUsd")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0:
        blockers.append("positive experiment budgetCapUsd is not configured")
    identities = set()
    for model in models:
        key = model["key"]
        for field in ("provider", "model", "versionDate", "dataRetentionNotes"):
            if not is_configured(model[field]):
                blockers.append(f"model {key} {field} is not configured")
        parsed = urlsplit(model["baseUrl"])
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.hostname.endswith(".invalid"):
            blockers.append(f"model {key} baseUrl is not configured")
        for field in ("inputCostPerMillionUsd", "outputCostPerMillionUsd"):
            value = model[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                blockers.append(f"model {key} {field} is not configured")
        if not os.environ.get(model["apiKeyEnv"], "").strip():
            blockers.append(f"credential environment variable {model['apiKeyEnv']} is empty")
        identities.add((model["provider"], model["model"], model["versionDate"]))
    if len(identities) != len(models):
        blockers.append("the two replication model identities must be distinct")
    if prompts["status"] != "frozen":
        blockers.append("prompt bundle is not frozen")
    if analysis_spec.get("status") != "frozen":
        blockers.append("mixed-effects analysis specification is not frozen")
    runtime = analysis_spec.get("runtime", {})
    if runtime.get("status") != "frozen-tested":
        blockers.append(
            "mixed-effects R runtime is verified but not researcher-frozen"
            if runtime.get("status") == "verified-unlocked"
            else "mixed-effects R runtime is not frozen and tested"
        )
    if not runtime.get("lockedVersions"):
        blockers.append(
            "verified R/package versions are not locked for formal analysis"
            if runtime.get("verifiedVersions")
            else "mixed-effects R/package versions are not locked"
        )
    if args.trials < 3:
        blockers.append("formal reliability analysis requires at least 3 trials")
    cost_plan = build_cost_plan(models, suite["variants"], args.trials, list(args.method))
    output_allocation_cost = cost_plan["outputAllocationCostUsd"]
    if (
        isinstance(budget, (int, float)) and not isinstance(budget, bool) and budget > 0
        and isinstance(output_allocation_cost, (int, float))
        and budget < output_allocation_cost
    ):
        blockers.append(
            f"budgetCapUsd is below the output-token allocation ceiling ({output_allocation_cost} USD; input cost excluded)"
        )
    return blockers


def build_experiment_fingerprint(
    suite_hash: str,
    methods_config: dict,
    analysis_contract: dict,
    configuration: dict,
    git_commit: str,
) -> str:
    """Return the registered input fingerprint used by preparation and execution."""
    return canonical_hash({
        "tasks": suite_hash,
        "methods": canonical_hash(methods_config),
        "analysis": analysis_contract,
        "configuration": configuration,
        "gitCommit": git_commit,
    })


def build_blocks(
    experiment_fingerprint: str,
    models: list[dict],
    variants: list[dict],
    trials: int,
    methods: list[str],
    randomization_seed: int,
) -> list[dict]:
    """Build the deterministic blocked schedule shared with the formal runner."""
    blocks = []
    randomizer = random.Random(randomization_seed)
    method_count = len(methods)
    if method_count < 1:
        raise ValueError("at least one method is required")
    first_order = []
    for index in range(method_count):
        if index == 0:
            first_order.append(0)
        elif index % 2:
            first_order.append((index + 1) // 2)
        else:
            first_order.append(method_count - index // 2)
    base_orders = [
        [methods[(item + shift) % method_count] for item in first_order]
        for shift in range(method_count)
    ]
    if method_count % 2:
        base_orders.extend([list(reversed(item)) for item in base_orders])
    stratum_orders: dict[tuple[str, int], list[list[str]]] = {}
    stratum_counts: dict[tuple[str, int], int] = {}
    for model in models:
        for variant in variants:
            for trial in range(1, trials + 1):
                stratum = (model["key"], variant["interactionLength"])
                if stratum not in stratum_orders:
                    stratum_orders[stratum] = deepcopy(base_orders)
                    randomizer.shuffle(stratum_orders[stratum])
                    stratum_counts[stratum] = 0
                order_index = stratum_counts[stratum] % len(stratum_orders[stratum])
                order = list(stratum_orders[stratum][order_index])
                stratum_counts[stratum] += 1
                block_id = hashlib.sha256(f"{experiment_fingerprint}:{model['key']}:{variant['taskId']}:{trial}".encode()).hexdigest()[:20]
                blocks.append({
                    "blockId": block_id,
                    "modelKey": model["key"],
                    "taskId": variant["taskId"],
                    "family": variant["family"],
                    "interactionLength": variant["interactionLength"],
                    "trial": trial,
                    "methodOrder": order,
                    "runs": [
                        {
                            "runId": hashlib.sha256(f"{block_id}:{method}".encode()).hexdigest()[:24],
                            "method": method,
                            "status": "pending",
                        }
                        for method in order
                    ],
                })
    return blocks


def build_cost_plan(models: list[dict], variants: list[dict], trials: int, methods: list[str]) -> dict:
    calls_per_method_trial = 0
    semantic_units_per_method_trial = 0
    candidate_batches_per_d_trial = 0
    for variant in variants:
        calls_per_method_trial += sum(
            not (
                item["phase"] == "setup"
                and (variant["trustedSetupActions"] or item["target"]["kind"] == "conversation")
            )
            for item in variant["rounds"]
        )
        human_rounds = [
            item for item in variant["rounds"]
            if item["phase"] != "distractor"
            and not (
                item["phase"] == "setup"
                and (variant["trustedSetupActions"] or item["target"]["kind"] == "conversation")
            )
        ]
        semantic_units_per_method_trial += len(human_rounds)
        candidate_batches_per_d_trial += sum(item["candidateTargetCount"] is not None for item in human_rounds)
    method_count = len(methods)
    by_model = {}
    total_calls = 0
    total_output_tokens = 0
    total_output_cost = Decimal("0")
    output_cost_complete = True
    for model in models:
        calls = calls_per_method_trial * trials * method_count
        output_tokens = calls * model["maxOutputTokens"]
        price = model["outputCostPerMillionUsd"]
        cost = None
        if isinstance(price, (int, float)) and not isinstance(price, bool) and price >= 0:
            cost = float((Decimal(output_tokens) * Decimal(str(price)) / Decimal(1_000_000)).quantize(Decimal("0.000000000001")))
            total_output_cost += Decimal(str(cost))
        else:
            output_cost_complete = False
        by_model[model["key"]] = {
            "plannedProviderCalls": calls,
            "maximumAllocatedOutputTokens": output_tokens,
            "outputAllocationCostUsd": cost,
        }
        total_calls += calls
        total_output_tokens += output_tokens
    semantic_samples = semantic_units_per_method_trial * trials * method_count * len(models)
    candidate_samples = candidate_batches_per_d_trial * trials * len(models) if "D" in methods else 0
    unique_samples = semantic_samples + candidate_samples
    minimum_double_semantic = math.ceil(semantic_samples * 0.2)
    minimum_double_candidates = math.ceil(candidate_samples * 0.2)
    minimum_double_total = minimum_double_semantic + minimum_double_candidates
    return {
        "retryPolicy": "none",
        "callsPerMethodTrialAcrossTasks": calls_per_method_trial,
        "plannedProviderCalls": total_calls,
        "maximumAllocatedOutputTokens": total_output_tokens,
        "outputAllocationCostUsd": float(total_output_cost) if output_cost_complete else None,
        "inputCostIncluded": False,
        "plannedHumanSemanticUnits": semantic_samples,
        "maximumHumanCandidateBatches": candidate_samples,
        "maximumUniqueAnnotationSamples": unique_samples,
        "minimumDoubleCodedSemanticSamples": minimum_double_semantic,
        "minimumDoubleCodedCandidateBatches": minimum_double_candidates,
        "minimumDoubleCodedSamples": minimum_double_total,
        "minimumAnnotationAssignments": unique_samples + minimum_double_total,
        "planningUpperBoundWithTwoCodersAndAdjudicatorEverySample": unique_samples * 3,
        "notes": "Output allocation is a planning ceiling, not an expected bill; input cost depends on realized history. Annotation samples are unique packets, while assignments include the preregistered stratified 20% second coding; adjudication is additional and depends on observed disagreement.",
        "byModel": by_model,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--methods-config", type=Path, default=DEFAULT_METHODS)
    parser.add_argument("--models-config", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--analysis-spec", type=Path, default=DEFAULT_ANALYSIS_SPEC)
    parser.add_argument("--analysis-preparer", type=Path, default=DEFAULT_ANALYSIS_PREPARER)
    parser.add_argument("--analysis-model-script", type=Path, default=DEFAULT_ANALYSIS_MODEL)
    parser.add_argument("--analysis-bootstrap-script", type=Path, default=DEFAULT_ANALYSIS_BOOTSTRAP)
    parser.add_argument("--analysis-survival-script", type=Path, default=DEFAULT_ANALYSIS_SURVIVAL)
    parser.add_argument("--analysis-retention-script", type=Path, default=DEFAULT_ANALYSIS_RETENTION)
    parser.add_argument("--method", nargs="+", choices=METHOD_IDS, default=list(METHOD_IDS))
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--map-mode", choices=("offline", "real"), default="offline")
    parser.add_argument("--randomization-seed", type=int, default=20261103)
    parser.add_argument("--formal", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output must not already exist")
    if not 1 <= args.trials <= 20:
        parser.error("--trials must be between 1 and 20")
    if len(set(args.method)) != len(args.method):
        parser.error("--method cannot contain duplicates")

    source = read_json(args.tasks)
    schema = read_json(args.schema)
    suite = compile_suite(source, schema)
    methods_config = read_json(args.methods_config)
    methods = validate_methods(methods_config)
    selected = [methods[item] for item in args.method]
    models_config = read_json(args.models_config)
    models = validate_models(models_config)
    prompts = load_bundle(args.prompt_dir)
    analysis_spec, analysis_contract = load_analysis_contract(
        args.analysis_spec,
        args.analysis_preparer,
        args.analysis_model_script,
        args.analysis_bootstrap_script,
        args.analysis_survival_script,
        args.analysis_retention_script,
    )
    git = git_metadata()
    blockers = build_blockers(
        args, suite, methods_config, selected, git, prompts, models_config, models, analysis_spec,
    )

    if args.formal and blockers:
        print(json.dumps({"formalReady": False, "blockers": blockers}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(2)

    configuration = {
        "modelDesign": models_config["design"],
        "modelConfigStatus": models_config.get("status"),
        "models": deepcopy(models),
        "budgetCapUsd": models_config.get("budgetCapUsd"),
        "promptVersion": prompts["version"],
        "promptHash": prompts["hash"],
        "promptStatus": prompts["status"],
        "mapMode": args.map_mode,
        "trials": args.trials,
        "methods": args.method,
        "randomizationSeed": args.randomization_seed,
        "randomizationScheme": "williams-balanced-within-model-and-length-v1",
    }
    experiment_fingerprint = build_experiment_fingerprint(
        suite["compiledHash"], methods_config, analysis_contract, configuration, git["commit"],
    )
    blocks = build_blocks(
        experiment_fingerprint,
        models,
        suite["variants"],
        args.trials,
        list(args.method),
        args.randomization_seed,
    )
    cost_plan = build_cost_plan(models, suite["variants"], args.trials, list(args.method))

    manifest = {
        "schemaVersion": "1.0",
        "experimentId": experiment_fingerprint[:24],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "formalExperiment": bool(args.formal),
        "formalReady": not blockers,
        "readinessBlockers": blockers,
        "benchmark": {
            "id": suite["benchmarkId"],
            "status": suite["sourceStatus"],
            "hash": suite["sourceHash"],
            "compiledHash": suite["compiledHash"],
            "variantCount": suite["variantCount"],
        },
        "methodContract": {
            "status": methods_config.get("status"),
            "hash": canonical_hash(methods_config),
        },
        "modelContract": {
            "status": models_config.get("status"),
            "hash": canonical_hash(models_config),
            "modelCount": len(models),
            "budgetCapUsd": models_config.get("budgetCapUsd"),
        },
        "analysisContract": analysis_contract,
        "git": git,
        "configuration": configuration,
        "credentialRecorded": False,
        "credentialPresentAtPreparation": {
            model["key"]: bool(os.environ.get(model["apiKeyEnv"], "").strip()) for model in models
        },
        "costPlan": cost_plan,
        "blockCount": len(blocks),
        "runCount": sum(len(item["runs"]) for item in blocks),
        "blocks": blocks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "formalExperiment": manifest["formalExperiment"],
        "formalReady": manifest["formalReady"],
        "blockers": blockers,
        "blockCount": manifest["blockCount"],
        "runCount": manifest["runCount"],
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
