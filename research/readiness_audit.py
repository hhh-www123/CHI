#!/usr/bin/env python3
"""Report research and paper readiness without inventing human evidence."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.prepare_experiment import (
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_ANALYSIS_PREPARER,
    DEFAULT_ANALYSIS_SPEC,
    DEFAULT_METHODS,
    DEFAULT_MODELS,
    METHOD_IDS,
    build_blockers,
    git_metadata,
    load_analysis_contract,
    validate_methods,
    validate_models,
)
from evaluation.prompt_bundle import DEFAULT_DIRECTORY as DEFAULT_PROMPTS, load_bundle
from evaluation.task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, canonical_hash, compile_suite, read_json

DEFAULT_TASK_REVIEW = ROOT / "research" / "task-review-template.csv"
DEFAULT_OPEN_DECISIONS = ROOT / "research" / "open-decisions.md"
DEFAULT_LITERATURE_QUERIES = ROOT / "research" / "literature-query-log.csv"
DEFAULT_LITERATURE_RECORDS = ROOT / "research" / "literature-log.csv"
DEFAULT_RUNTIME_VERIFICATION = ROOT / "analysis" / "runtime-verification.json"
DEFAULT_R_LIBRARY = ROOT / ".r-library"
RUNTIME_ASSETS = {
    "modelSpec": DEFAULT_ANALYSIS_SPEC,
    "preparationScript": DEFAULT_ANALYSIS_PREPARER,
    "modelScript": DEFAULT_ANALYSIS_MODEL,
    "fixtureGenerator": ROOT / "analysis" / "generate_mixed_effects_fixture.mjs",
    "verificationScript": ROOT / "analysis" / "verify_runtime.py",
}
REVIEW_FIELDS = (
    "approved", "setup_clear", "probe_clear", "oracle_decidable", "distractors_neutral",
    "method_symmetric", "no_hidden_external_fact", "severity_balanced",
)
TRUE_VALUES = {"1", "true", "yes", "y", "x", "approved"}
PRIMARY_DATABASES = {"ACM Digital Library", "IEEE Xplore", "ACL Anthology", "arXiv"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError as error:
        raise ValueError(f"cannot read CSV: {path}") from error


def _is_true(value: str | None) -> bool:
    return isinstance(value, str) and value.strip().lower() in TRUE_VALUES


def _read_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read JSON object: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON evidence must contain an object: {path}")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_current_r_runtime(library: Path, expected: dict) -> dict:
    rscript = shutil.which("Rscript")
    if rscript is None:
        return {"available": False, "matchesVerifiedVersions": False, "versions": None, "error": "Rscript is unavailable"}
    packages = ("lme4", "emmeans", "jsonlite", "digest")
    expression = (
        'packages <- c("lme4", "emmeans", "jsonlite", "digest"); '
        'cat(paste(c(R=paste(R.version$major,R.version$minor,sep="."), '
        'vapply(packages, function(x) as.character(packageVersion(x)), character(1))), collapse="\\n"))'
    )
    environment = os.environ.copy()
    environment["R_LIBS_USER"] = str(library.resolve())
    try:
        result = subprocess.run(
            [rscript, "-e", expression], cwd=ROOT, env=environment,
            text=True, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": True, "matchesVerifiedVersions": False, "versions": None, "error": str(error)}
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return {"available": True, "matchesVerifiedVersions": False, "versions": None, "error": detail[-1] if detail else "runtime inspection failed"}
    values = result.stdout.strip().splitlines()
    if len(values) != 5:
        return {"available": True, "matchesVerifiedVersions": False, "versions": None, "error": "runtime inspection returned an invalid version set"}
    versions = dict(zip(("R", *packages), values, strict=True))
    return {
        "available": True,
        "matchesVerifiedVersions": versions == expected,
        "versions": versions,
        "error": None,
    }


def audit_runtime_verification(path: Path, library: Path, analysis_spec: dict, analysis_contract: dict) -> dict:
    if not path.is_file():
        return {"supplied": False, "ready": False, "issues": ["no mixed-effects runtime verification supplied"]}
    data = _read_json_object(path)
    issues = []
    required_flags = {
        "schemaVersion": "mixed-effects-runtime-verification-1.0",
        "status": "passed",
        "syntheticContractTest": True,
        "formalAnalysisReady": False,
        "modelFitsReady": True,
    }
    for field, expected in required_flags.items():
        if data.get(field) != expected:
            issues.append(f"{field} must equal {expected!r}")
    design = data.get("design")
    if not isinstance(design, dict) or design.get("episodes") != 960 or design.get("blocks") != 240 or design.get("modelCount") != 2:
        issues.append("runtime verification must cover the 960-episode, 240-block, two-model fixture")
    runtime = analysis_spec.get("runtime", {})
    verified_versions = runtime.get("verifiedVersions")
    if runtime.get("status") not in {"verified-unlocked", "frozen-tested"}:
        issues.append("model specification runtime status is not verified")
    if not isinstance(verified_versions, dict) or data.get("runtimeVersions") != verified_versions:
        issues.append("runtime verification versions do not match model-spec.json")
        verified_versions = data.get("runtimeVersions") if isinstance(data.get("runtimeVersions"), dict) else {}
    if runtime.get("verificationEvidence") != "analysis/runtime-verification.json":
        issues.append("model specification does not reference the registered runtime evidence")
    asset_hashes = data.get("assetHashes")
    if not isinstance(asset_hashes, dict):
        issues.append("runtime verification assetHashes are missing")
    else:
        for name, asset_path in RUNTIME_ASSETS.items():
            if asset_hashes.get(name) != _file_sha256(asset_path):
                issues.append(f"runtime verification is stale for {name}")
    if data.get("modelSpecCanonicalHash") != analysis_contract.get("specHash"):
        issues.append("runtime verification canonical model-spec hash is stale")
    if data.get("analysisContractHash") != canonical_hash(analysis_contract):
        issues.append("runtime verification analysis-contract hash is stale")
    models = data.get("models")
    if (
        not isinstance(models, dict)
        or len(models) != 2
        or any(
            not isinstance(model, dict)
            or model.get("confirmatoryInferenceReady") is not True
            or model.get("templateFixedEffectSensitivityEstimable") is not True
            for model in models.values()
        )
    ):
        issues.append("runtime verification lacks two ready primary and sensitivity fits")
    current = inspect_current_r_runtime(library, verified_versions)
    return {
        "supplied": True,
        "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "ready": not issues,
        "issues": issues,
        "verifiedAt": data.get("verifiedAt"),
        "verifiedVersions": data.get("runtimeVersions"),
        "currentRuntime": current,
        "note": "Passing evidence is synthetic and only verifies the registered analysis implementation; it is not a formal result.",
    }


def audit_annotation_pilot(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"supplied": False, "ready": False, "issues": ["no pilot summary supplied"]}
    data = _read_json_object(path)
    issues = []
    if data.get("valid") is not True:
        issues.append("validator output must contain valid:true")
    thresholds = {
        "doubleCodedRate": 0.20,
        "doubleCodedSemanticRate": 0.20,
        "doubleCodedCandidateRate": 0.20,
        "comparableGoalLabels": 20,
        "binaryCompleteGoalKappa": 0.70,
        "ordinalGoalKrippendorffAlpha": 0.67,
        "doubleCodedCandidateRatings": 20,
        "candidateRelevanceKrippendorffAlpha": 0.67,
    }
    for field, threshold in thresholds.items():
        value = data.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < threshold:
            issues.append(f"{field} is missing or below {threshold}")
    if not isinstance(data.get("requiredSampleCount"), int) or data["requiredSampleCount"] < 1:
        issues.append("requiredSampleCount must be positive")
    if not isinstance(data.get("annotatorCount"), int) or data["annotatorCount"] < 2:
        issues.append("at least two independent annotators are required")
    return {
        "supplied": True,
        "path": str(path),
        "ready": not issues,
        "issues": issues,
        "observed": {field: data.get(field) for field in (*thresholds, "requiredSampleCount", "annotatorCount", "adjudicatorCount")},
        "note": "Pilot readiness uses agreement thresholds and does not require annotations.status=formal.",
    }


def audit_ethics_record(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"supplied": False, "ready": False, "issues": ["no ethics determination supplied"]}
    data = _read_json_object(path)
    issues = []
    if data.get("status") not in {"approved", "exempt", "not-required-documented"}:
        issues.append("status must be approved, exempt, or not-required-documented")
    for field in ("authority", "determinationDate", "reference", "dataRetentionPolicy"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            issues.append(f"{field} is required")
    if data.get("consentMaterialsReviewed") is not True:
        issues.append("consentMaterialsReviewed must be true")
    return {"supplied": True, "path": str(path), "ready": not issues, "issues": issues, "status": data.get("status")}


def audit_user_study_pilot(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"supplied": False, "ready": False, "issues": ["no user-study pilot record supplied"]}
    data = _read_json_object(path)
    issues = []
    count = data.get("participantCount")
    if not isinstance(count, int) or isinstance(count, bool) or not 6 <= count <= 8:
        issues.append("participantCount must be between 6 and 8 for the registered pilot")
    if data.get("status") != "complete":
        issues.append("status must be complete")
    for field in ("primaryBehavioralEndpoint", "primarySubjectiveEndpoint", "usabilityScale", "sampleSizePlan", "stoppingRule"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            issues.append(f"{field} is required")
    if data.get("usabilityScale") not in {"UMUX-Lite", "SUS"}:
        issues.append("usabilityScale must be UMUX-Lite or SUS")
    if data.get("formalRecruitmentStarted") is not False:
        issues.append("formalRecruitmentStarted must be false at pilot freeze")
    return {"supplied": True, "path": str(path), "ready": not issues, "issues": issues, "participantCount": count}


def audit_formal_dataset(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"supplied": False, "ready": False, "issues": ["no formal scoring dataset supplied"]}
    data = _read_json_object(path)
    issues = []
    required_flags = {
        "schemaVersion": "1.0",
        "formalExperiment": True,
        "illustrative": False,
        "pilot": False,
        "realModel": True,
        "generationMode": "real-model-formal-runner",
    }
    for field, expected in required_flags.items():
        if data.get(field) != expected:
            issues.append(f"{field} must equal {expected!r}")
    episodes = data.get("episodes")
    if not isinstance(episodes, list) or len(episodes) != 960:
        issues.append("formal dataset must contain exactly 960 episodes")
    annotation_stats = data.get("annotationStats")
    if not isinstance(annotation_stats, dict) or annotation_stats.get("formalReady") is not True:
        issues.append("annotationStats.formalReady must be true")
    for field in ("manifestHash", "analysisContractHash", "benchmarkHash", "benchmarkSourceHash", "promptHash"):
        if not isinstance(data.get(field), str) or not re.fullmatch(r"[a-f0-9]{64}", data[field]):
            issues.append(f"{field} must be a SHA-256 hash")
    return {"supplied": True, "path": str(path), "ready": not issues, "issues": issues, "episodeCount": len(episodes) if isinstance(episodes, list) else None}


def audit_primary_analysis(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"supplied": False, "ready": False, "issues": ["no mixed-effects result supplied"]}
    data = _read_json_object(path)
    issues = []
    if data.get("schemaVersion") != "mixed-effects-results-1.0":
        issues.append("mixed-effects result schemaVersion is invalid")
    if data.get("formalAnalysisReady") is not True or data.get("contractTest") is not False:
        issues.append("result must be formal-analysis ready and not a contract test")
    models = data.get("models")
    if not isinstance(models, dict) or len(models) != 2 or any(not isinstance(item, dict) for item in models.values()):
        issues.append("result must contain two separately fitted model identities")
    elif any(item.get("confirmatoryInferenceReady") is not True for item in models.values()):
        issues.append("one or more model identities lack confirmatory-ready diagnostics")
    return {"supplied": True, "path": str(path), "ready": not issues, "issues": issues, "modelCount": len(models) if isinstance(models, dict) else None}


def audit_user_study_results(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"supplied": False, "ready": False, "issues": ["no final user-study result summary supplied"]}
    data = _read_json_object(path)
    issues = []
    if data.get("status") != "complete":
        issues.append("status must be complete")
    if data.get("analysisComplete") is not True or data.get("deidentified") is not True:
        issues.append("analysisComplete and deidentified must both be true")
    count = data.get("participantCount")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        issues.append("participantCount must be positive")
    conditions = data.get("conditions")
    if not isinstance(conditions, list) or set(conditions) != {"C", "D"}:
        issues.append("conditions must contain C and D")
    for field in ("ethicsReference", "datasetHash", "analysisHash"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            issues.append(f"{field} is required")
    for field in ("datasetHash", "analysisHash"):
        if isinstance(data.get(field), str) and data[field].strip() and not re.fullmatch(r"[a-f0-9]{64}", data[field]):
            issues.append(f"{field} must be a SHA-256 hash")
    return {"supplied": True, "path": str(path), "ready": not issues, "issues": issues, "participantCount": count}


def audit_task_reviews(path: Path, task_ids: list[str], benchmark_hash: str) -> dict:
    rows = _read_csv(path)
    expected = set(task_ids)
    unknown = sorted({row.get("task_id", "") for row in rows if row.get("task_id") not in expected})
    invalid_rows = []
    approved_reviewers: dict[str, set[str]] = {task_id: set() for task_id in task_ids}
    seen = set()
    for index, row in enumerate(rows, 2):
        task_id = row.get("task_id", "")
        reviewer = row.get("reviewer_id", "").strip()
        review_round = row.get("review_round", "").strip()
        identity = (task_id, reviewer, review_round)
        complete = (
            task_id in expected
            and row.get("benchmark_hash") == benchmark_hash
            and bool(reviewer)
            and bool(review_round)
            and all(_is_true(row.get(field)) for field in REVIEW_FIELDS)
        )
        if identity in seen or not complete:
            invalid_rows.append(index)
        seen.add(identity)
        if complete:
            approved_reviewers[task_id].add(reviewer)
    approved = sorted(task_id for task_id, reviewers in approved_reviewers.items() if len(reviewers) >= 2)
    return {
        "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "expectedTaskCount": len(task_ids),
        "benchmarkHash": benchmark_hash,
        "rowCount": len(rows),
        "tasksWithTwoIndependentApprovals": len(approved),
        "approvedTaskIds": approved,
        "missingTaskIds": sorted(expected - set(approved)),
        "unknownTaskIds": unknown,
        "invalidRowNumbers": invalid_rows,
        "ready": len(approved) == len(task_ids) and not unknown and not invalid_rows,
    }


def audit_open_decisions(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    headings = list(re.finditer(r"^##\s+(\d+)\.\s+(.+)$", text, flags=re.MULTILINE))
    decisions = []
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[match.end():end]
        decisions.append({
            "number": int(match.group(1)),
            "title": match.group(2).strip(),
            "hasCheckedChoice": bool(re.search(r"^-\s*\[x\]", block, flags=re.MULTILINE | re.IGNORECASE)),
        })
    required = {8, 9, 10, 11}
    signed = {item["number"] for item in decisions if item["hasCheckedChoice"]}
    return {
        "decisionCount": len(decisions),
        "decisions": decisions,
        "requiredBeforeFreeze": sorted(required),
        "unsignedRequired": sorted(required - signed),
        "ready": required <= signed,
    }


def audit_literature(query_path: Path, records_path: Path) -> dict:
    queries = _read_csv(query_path)
    records = _read_csv(records_path)
    databases = sorted({row.get("database", "").strip() for row in queries if row.get("database", "").strip()})
    missing = sorted(PRIMARY_DATABASES - set(databases))
    double_screen_evidence = [
        row for row in queries
        if "double" in row.get("screening_rule", "").lower() or "two" in row.get("screening_rule", "").lower()
    ]
    return {
        "queryCount": len(queries),
        "recordCount": len(records),
        "databasesSearched": databases,
        "missingRegisteredPrimaryDatabases": missing,
        "doubleScreeningLogged": bool(double_screen_evidence),
        "ready": not missing and bool(double_screen_evidence),
        "note": "OpenAlex is supplemental discovery and never satisfies the registered primary-database requirement.",
    }


def _owner_for_formal_blocker(message: str) -> str:
    if "credential" in message or "budget" in message or "model " in message or "dataRetention" in message:
        return "researcher/configuration"
    if "Git worktree" in message:
        return "engineering/release"
    if "analysis" in message or "mixed-effects" in message or "R/package" in message:
        return "researcher/statistics"
    return "researcher/freeze"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-review", type=Path, default=DEFAULT_TASK_REVIEW)
    parser.add_argument("--open-decisions", type=Path, default=DEFAULT_OPEN_DECISIONS)
    parser.add_argument("--literature-queries", type=Path, default=DEFAULT_LITERATURE_QUERIES)
    parser.add_argument("--literature-records", type=Path, default=DEFAULT_LITERATURE_RECORDS)
    parser.add_argument("--runtime-verification", type=Path, default=DEFAULT_RUNTIME_VERIFICATION)
    parser.add_argument("--r-library", type=Path, default=DEFAULT_R_LIBRARY)
    parser.add_argument("--annotation-pilot", type=Path)
    parser.add_argument("--ethics-record", type=Path)
    parser.add_argument("--user-study-pilot", type=Path)
    parser.add_argument("--formal-dataset", type=Path)
    parser.add_argument("--primary-analysis", type=Path)
    parser.add_argument("--user-study-results", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is not None and args.output.exists():
        parser.error("--output must not already exist")

    suite = compile_suite(read_json(DEFAULT_SOURCE), read_json(DEFAULT_SCHEMA))
    methods_config = read_json(DEFAULT_METHODS)
    methods = validate_methods(methods_config)
    models_config = read_json(DEFAULT_MODELS)
    models = validate_models(models_config)
    prompts = load_bundle(DEFAULT_PROMPTS)
    analysis_spec, analysis_contract = load_analysis_contract(
        DEFAULT_ANALYSIS_SPEC, DEFAULT_ANALYSIS_PREPARER, DEFAULT_ANALYSIS_MODEL,
    )
    runtime_verification = audit_runtime_verification(
        args.runtime_verification, args.r_library, analysis_spec, analysis_contract,
    )
    git = git_metadata()
    formal_blockers = build_blockers(
        SimpleNamespace(method=list(METHOD_IDS), trials=5),
        suite,
        methods_config,
        [methods[item] for item in METHOD_IDS],
        git,
        prompts,
        models_config,
        models,
        analysis_spec,
    )
    task_reviews = audit_task_reviews(
        args.task_review,
        [item["taskId"] for item in suite["variants"]],
        suite["compiledHash"],
    )
    decisions = audit_open_decisions(args.open_decisions)
    literature = audit_literature(args.literature_queries, args.literature_records)
    annotation_pilot = audit_annotation_pilot(args.annotation_pilot)
    ethics_record = audit_ethics_record(args.ethics_record)
    user_study_pilot = audit_user_study_pilot(args.user_study_pilot)
    formal_dataset = audit_formal_dataset(args.formal_dataset)
    primary_analysis = audit_primary_analysis(args.primary_analysis)
    user_study_results = audit_user_study_results(args.user_study_results)

    blockers = [
        {
            "id": f"formal-{index + 1:02d}",
            "stage": "formal-experiment",
            "owner": _owner_for_formal_blocker(message),
            "detail": message,
        }
        for index, message in enumerate(formal_blockers)
    ]
    if not task_reviews["ready"]:
        blockers.append({
            "id": "human-task-review",
            "stage": "benchmark-freeze",
            "owner": "two independent reviewers",
            "detail": f"{len(task_reviews['missingTaskIds'])} of 24 task conditions still lack two complete independent approvals",
        })
    if not runtime_verification["ready"]:
        blockers.append({
            "id": "runtime-verification",
            "stage": "analysis-freeze",
            "owner": "statistics lead",
            "detail": "mixed-effects runtime evidence is absent, stale or failed its synthetic contract",
        })
    for number in decisions["unsignedRequired"]:
        blockers.append({
            "id": f"decision-{number}",
            "stage": "preregistration",
            "owner": "research lead",
            "detail": f"open-decisions.md item {number} has no checked choice",
        })
    if not annotation_pilot["ready"]:
        blockers.append({
            "id": "annotation-pilot",
            "stage": "annotation-freeze",
            "owner": "two annotators and adjudicator",
            "detail": "annotation pilot evidence is absent or fails the registered coverage/agreement contract",
        })
    if not literature["ready"]:
        blockers.append({
            "id": "literature-search",
            "stage": "related-work",
            "owner": "two literature reviewers",
            "detail": "registered primary-database searches and double-screening evidence are incomplete",
        })
    if not ethics_record["ready"]:
        blockers.append({
            "id": "ethics-record",
            "stage": "user-study",
            "owner": "course/institution and research lead",
            "detail": "ethics evidence is absent or lacks a valid determination and reviewed consent/data-retention fields",
        })
    if not user_study_pilot["ready"]:
        blockers.append({
            "id": "user-study-pilot",
            "stage": "user-study",
            "owner": "research team",
            "detail": "user-study pilot evidence is absent or lacks 6-8 participants and frozen endpoint/scale/sample-size decisions",
        })
    if not formal_dataset["ready"]:
        blockers.append({
            "id": "formal-dataset",
            "stage": "paper-results",
            "owner": "formal experiment and annotation pipeline",
            "detail": "no valid 960-episode formal scoring dataset was supplied",
        })
    if not primary_analysis["ready"]:
        blockers.append({
            "id": "primary-analysis-results",
            "stage": "paper-results",
            "owner": "statistics lead",
            "detail": "no formal two-model mixed-effects result with passing diagnostics was supplied",
        })
    if not user_study_results["ready"]:
        blockers.append({
            "id": "user-study-results",
            "stage": "paper-results",
            "owner": "user-study team",
            "detail": "no complete deidentified C-D user-study result summary was supplied",
        })

    formal_manual_ids = {"human-task-review", "decision-8", "decision-10", "decision-11", "annotation-pilot"}
    formal_experiment_ready = not formal_blockers and runtime_verification["ready"] and not any(item["id"] in formal_manual_ids for item in blockers)
    result_evidence_ready = formal_dataset["ready"] and primary_analysis["ready"] and user_study_results["ready"]
    next_actions = []
    if decisions["unsignedRequired"]:
        next_actions.append(
            "Sign open decisions " + ", ".join(str(number) for number in decisions["unsignedRequired"])
            + " before freezing outcomes, annotation scope or user-study claims."
        )
    if not task_reviews["ready"]:
        next_actions.append("Complete two independent reviews for all 24 compiled task conditions.")
    if analysis_spec.get("status") != "frozen" or analysis_spec.get("runtime", {}).get("status") != "frozen-tested":
        next_actions.append(
            "Review the verified R/package versions and synthetic diagnostics, then freeze the exact runtime with the analysis contract."
            if runtime_verification["ready"]
            else "Install and synthetic-test the registered R mixed-effects runtime, then record exact versions."
        )
    if any(
        token in message
        for message in formal_blockers
        for token in ("model ", "two-model", "budget", "credential", "dataRetention")
    ):
        next_actions.append("Freeze two exact model identities, prices, retention policies and an authorized budget; keep credentials only in environment variables.")
    if not annotation_pilot["ready"]:
        next_actions.append("Run the blinded annotation pilot and retain agreement/adjudication output.")
    if not literature["ready"]:
        next_actions.append("Complete registered primary-database search and double screening.")
    if not ethics_record["ready"] or not user_study_pilot["ready"]:
        next_actions.append("Obtain ethics determination and run the 6-8 participant user-study pilot before recruitment freeze.")
    report = {
        "schemaVersion": "research-readiness-1.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "formalExperimentReady": formal_experiment_ready,
        "paperReady": formal_experiment_ready and result_evidence_ready and not blockers,
        "assetStatus": {
            "taskSuite": {"status": suite["sourceStatus"], "variants": suite["variantCount"], "compiledHash": suite["compiledHash"]},
            "methods": {"status": methods_config.get("status"), "count": len(methods)},
            "prompts": {"status": prompts["status"], "hash": prompts["hash"]},
            "models": {
                "status": models_config.get("status"),
                "count": len(models),
                "budgetConfigured": isinstance(models_config.get("budgetCapUsd"), (int, float)) and not isinstance(models_config.get("budgetCapUsd"), bool),
            },
            "analysis": {
                **analysis_contract,
                "rscriptAvailable": shutil.which("Rscript") is not None,
                "runtimeVerification": runtime_verification,
            },
            "git": {"commit": git["commit"], "dirty": git["dirty"], "dirtyEntryCount": len(git["dirtyEntries"])},
        },
        "humanEvidence": {
            "taskReviews": task_reviews,
            "openDecisions": decisions,
            "literature": literature,
            "annotationPilot": annotation_pilot,
            "ethicsRecord": ethics_record,
            "userStudyPilot": user_study_pilot,
            "formalDataset": formal_dataset,
            "primaryAnalysis": primary_analysis,
            "userStudyResults": user_study_results,
        },
        "blockerCount": len(blockers),
        "blockers": blockers,
        "nextActions": next_actions,
        "notes": [
            "This audit reports evidence presence; it does not approve human review, ethics, labels or study quality.",
            "Credentials are checked only for presence by the formal manifest gate and are never written here.",
            "Mock, fixture and engineering regression outputs cannot satisfy paper-result blockers.",
        ],
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
    print(serialized, end="")


if __name__ == "__main__":
    main()
