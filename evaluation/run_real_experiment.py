#!/usr/bin/env python3
"""Execute a frozen formal manifest with auditable, resumable model calls.

The CLI has no fixture or permissive mode: it invokes a provider only after the
manifest and every frozen local input pass the formal gates. Results are written
one run at a time. A completed run is immutable, while an interrupted attempt
must be explicitly acknowledged before it can be retried.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

try:  # POSIX and Windows use different advisory-lock APIs.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None
    import msvcrt


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

try:
    from .model_client import ModelCallError, ModelConfig, invoke_json_prompt
    from .prepare_experiment import (
        DEFAULT_METHODS,
        DEFAULT_MODELS,
        DEFAULT_ANALYSIS_MODEL,
        DEFAULT_ANALYSIS_PREPARER,
        DEFAULT_ANALYSIS_SPEC,
        DEFAULT_ANALYSIS_BOOTSTRAP,
        DEFAULT_ANALYSIS_SURVIVAL,
        DEFAULT_ANALYSIS_RETENTION,
        METHOD_IDS,
        build_blocks,
        build_cost_plan,
        build_experiment_fingerprint,
        git_metadata,
        is_configured,
        load_analysis_contract,
        validate_methods,
        validate_models,
    )
    from .prompt_bundle import DEFAULT_DIRECTORY as DEFAULT_PROMPTS, load_bundle
    from .run_contract_pilot import run_method
    from .task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, canonical_hash, compile_suite, read_json
except ImportError:  # direct script execution
    from model_client import ModelCallError, ModelConfig, invoke_json_prompt
    from prepare_experiment import (
        DEFAULT_METHODS,
        DEFAULT_MODELS,
        DEFAULT_ANALYSIS_MODEL,
        DEFAULT_ANALYSIS_PREPARER,
        DEFAULT_ANALYSIS_SPEC,
        DEFAULT_ANALYSIS_BOOTSTRAP,
        DEFAULT_ANALYSIS_SURVIVAL,
        DEFAULT_ANALYSIS_RETENTION,
        METHOD_IDS,
        build_blocks,
        build_cost_plan,
        build_experiment_fingerprint,
        git_metadata,
        is_configured,
        load_analysis_contract,
        validate_methods,
        validate_models,
    )
    from prompt_bundle import DEFAULT_DIRECTORY as DEFAULT_PROMPTS, load_bundle
    from run_contract_pilot import run_method
    from task_suite import DEFAULT_SCHEMA, DEFAULT_SOURCE, canonical_hash, compile_suite, read_json


ROOT = REPOSITORY_ROOT
SCHEMA_VERSION = "formal-runner-1.0"
INPUT_TOKEN_OVERHEAD = 16_384
MAX_PROMPT_BYTES = 2_000_000


class RunnerValidationError(ValueError):
    """A manifest or frozen local input is not safe to execute."""


class RunnerStateError(RuntimeError):
    """The result directory cannot be safely created or resumed."""


@dataclass(frozen=True)
class ValidatedExperiment:
    manifest: dict
    manifest_hash: str
    suite: dict
    variants: dict[str, dict]
    models: dict[str, dict]
    prompt_directory: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000000000001")))


def _decimal(value, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise RunnerStateError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except Exception as error:  # Decimal raises multiple concrete subclasses
        raise RunnerStateError(f"{name} must be numeric") from error
    if not result.is_finite() or result < 0:
        raise RunnerStateError(f"{name} must be finite and non-negative")
    return result


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunnerStateError(f"cannot read JSON object: {path}") from error
    if not isinstance(value, dict):
        raise RunnerStateError(f"JSON file must contain an object: {path}")
    return value


def _encoded_json(value: dict) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RunnerStateError("result contains a non-JSON value") from error


def _sync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        # File contents are already fsynced; directory fsync is unavailable on
        # some supported filesystems.
        pass


def _atomic_json(path: Path, value: dict, *, create_only: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_encoded_json(value))
            handle.flush()
            os.fsync(handle.fileno())
        if create_only:
            try:
                os.link(temporary, path)
            except FileExistsError as error:
                raise RunnerStateError(f"refusing to overwrite immutable result: {path}") from error
            temporary.unlink()
        else:
            os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_create_json(path: Path, value: dict) -> None:
    _atomic_json(path, value, create_only=True)


def _atomic_replace_json(path: Path, value: dict) -> None:
    _atomic_json(path, value, create_only=False)


@contextmanager
def _exclusive_runner_lock(output_directory: Path):
    output_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = output_directory / ".runner.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RunnerStateError("another runner already holds this result directory") from error
        else:  # pragma: no cover - exercised on Windows
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write("0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RunnerStateError("another runner already holds this result directory") from error
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - exercised on Windows
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _validation_error(message: str) -> None:
    raise RunnerValidationError(message)


def validate_formal_manifest(
    manifest_path: Path,
    *,
    tasks_path: Path = DEFAULT_SOURCE,
    schema_path: Path = DEFAULT_SCHEMA,
    methods_path: Path = DEFAULT_METHODS,
    models_path: Path = DEFAULT_MODELS,
    prompt_directory: Path = DEFAULT_PROMPTS,
    analysis_spec_path: Path = DEFAULT_ANALYSIS_SPEC,
    analysis_preparer_path: Path = DEFAULT_ANALYSIS_PREPARER,
    analysis_model_path: Path = DEFAULT_ANALYSIS_MODEL,
    analysis_bootstrap_path: Path = DEFAULT_ANALYSIS_BOOTSTRAP,
    analysis_survival_path: Path = DEFAULT_ANALYSIS_SURVIVAL,
    analysis_retention_path: Path = DEFAULT_ANALYSIS_RETENTION,
) -> ValidatedExperiment:
    """Validate the manifest against current frozen inputs without any writes."""
    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RunnerValidationError("manifest must be a readable JSON object") from error

    if manifest.get("schemaVersion") != "1.0":
        _validation_error("manifest schemaVersion must be 1.0")
    if manifest.get("formalExperiment") is not True or manifest.get("formalReady") is not True:
        _validation_error("runner requires formalExperiment:true and formalReady:true")
    if manifest.get("readinessBlockers") != []:
        _validation_error("formal manifest must contain no readiness blockers")
    if manifest.get("credentialRecorded") is not False:
        _validation_error("manifest must explicitly state that credentials were not recorded")

    try:
        suite = compile_suite(read_json(tasks_path), read_json(schema_path))
        methods_config = read_json(methods_path)
        methods = validate_methods(methods_config)
        models_config = read_json(models_path)
        models = validate_models(models_config)
        prompts = load_bundle(prompt_directory)
        analysis_spec, analysis_contract = load_analysis_contract(
            analysis_spec_path,
            analysis_preparer_path,
            analysis_model_path,
            analysis_bootstrap_path,
            analysis_survival_path,
            analysis_retention_path,
        )
        git = git_metadata()
    except (OSError, RuntimeError, ValueError) as error:
        raise RunnerValidationError(f"cannot validate frozen experiment inputs: {error}") from error

    if suite["sourceStatus"] != "frozen":
        _validation_error("current task suite is not frozen")
    if methods_config.get("status") != "frozen":
        _validation_error("current method contract is not frozen")
    if any(methods[item].get("formalRunnable") is not True for item in METHOD_IDS):
        _validation_error("all A-D method adapters must be formally runnable")
    if models_config.get("status") != "frozen":
        _validation_error("current two-model configuration is not frozen")
    if prompts["status"] != "frozen":
        _validation_error("current prompt bundle is not frozen")
    if analysis_spec.get("status") != "frozen":
        _validation_error("current mixed-effects analysis specification is not frozen")
    if analysis_spec.get("runtime", {}).get("status") != "frozen-tested" or not analysis_spec.get("runtime", {}).get("lockedVersions"):
        _validation_error("current mixed-effects R runtime is not frozen and tested")
    if git["dirty"]:
        _validation_error("Git worktree must remain clean while starting a formal run")

    benchmark = manifest.get("benchmark")
    method_contract = manifest.get("methodContract")
    model_contract = manifest.get("modelContract")
    registered_analysis = manifest.get("analysisContract")
    configuration = manifest.get("configuration")
    if not all(isinstance(item, dict) for item in (benchmark, method_contract, model_contract, registered_analysis, configuration)):
        _validation_error("manifest contracts and configuration must be objects")
    if benchmark != {
        "id": suite["benchmarkId"],
        "status": suite["sourceStatus"],
        "hash": suite["sourceHash"],
        "compiledHash": suite["compiledHash"],
        "variantCount": suite["variantCount"],
    }:
        _validation_error("manifest benchmark does not match the current frozen task suite")
    if method_contract != {"status": methods_config.get("status"), "hash": canonical_hash(methods_config)}:
        _validation_error("manifest method contract does not match methods.json")
    expected_model_contract = {
        "status": models_config.get("status"),
        "hash": canonical_hash(models_config),
        "modelCount": len(models),
        "budgetCapUsd": models_config.get("budgetCapUsd"),
    }
    if model_contract != expected_model_contract:
        _validation_error("manifest model contract does not match models.json")
    if registered_analysis != analysis_contract:
        _validation_error("manifest analysis contract does not match the frozen mixed-effects assets")

    required_configuration = {
        "modelDesign", "modelConfigStatus", "models", "budgetCapUsd",
        "promptVersion", "promptHash", "promptStatus", "mapMode", "trials",
        "methods", "randomizationSeed", "randomizationScheme",
    }
    if set(configuration) != required_configuration:
        _validation_error("manifest configuration fields are incomplete or unexpected")
    if configuration["modelDesign"] != models_config["design"] or configuration["modelConfigStatus"] != models_config["status"]:
        _validation_error("manifest model design/status mismatch")
    if configuration["models"] != models:
        _validation_error("manifest model entries do not match models.json")
    if configuration["budgetCapUsd"] != models_config.get("budgetCapUsd"):
        _validation_error("manifest budget cap does not match models.json")
    if (configuration["promptVersion"], configuration["promptHash"], configuration["promptStatus"]) != (
        prompts["version"], prompts["hash"], prompts["status"],
    ):
        _validation_error("manifest prompt bundle does not match the current frozen prompts")
    if configuration["mapMode"] not in {"offline", "real"}:
        _validation_error("manifest mapMode is invalid")
    configured_methods = configuration["methods"]
    if not isinstance(configured_methods, list) or len(configured_methods) != len(METHOD_IDS) or set(configured_methods) != set(METHOD_IDS):
        _validation_error("formal schedule must register methods A, B, C and D exactly once")
    trials = configuration["trials"]
    randomization_seed = configuration["randomizationSeed"]
    if isinstance(trials, bool) or not isinstance(trials, int) or not 3 <= trials <= 20:
        _validation_error("formal trials must be an integer between 3 and 20")
    if isinstance(randomization_seed, bool) or not isinstance(randomization_seed, int):
        _validation_error("randomizationSeed must be an integer")
    if configuration["randomizationScheme"] != "williams-balanced-within-model-and-length-v1":
        _validation_error("formal randomization scheme is not the registered balanced design")

    cap = models_config.get("budgetCapUsd")
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
        _validation_error("a positive budgetCapUsd is required")
    for model in models:
        for field in ("provider", "model", "versionDate", "dataRetentionNotes"):
            if not is_configured(model[field]):
                _validation_error(f"model {model['key']} {field} is not configured")
        for field in ("inputCostPerMillionUsd", "outputCostPerMillionUsd"):
            value = model[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                _validation_error(f"model {model['key']} {field} is not configured")

    manifest_git = manifest.get("git")
    if not isinstance(manifest_git, dict) or manifest_git.get("commit") != git["commit"] or manifest_git.get("dirty") is not False:
        _validation_error("manifest Git commit does not match the current clean checkout")
    if manifest_git.get("dirtyEntries") != []:
        _validation_error("formal manifest must have been prepared from a clean checkout")

    fingerprint = build_experiment_fingerprint(
        suite["compiledHash"], methods_config, analysis_contract, configuration, git["commit"],
    )
    if manifest.get("experimentId") != fingerprint[:24]:
        _validation_error("manifest experimentId does not match its frozen inputs")
    expected_blocks = build_blocks(
        fingerprint,
        models,
        suite["variants"],
        trials,
        configured_methods,
        randomization_seed,
    )
    if manifest.get("blocks") != expected_blocks:
        _validation_error("manifest block schedule or run IDs were modified")
    if manifest.get("blockCount") != len(expected_blocks):
        _validation_error("manifest blockCount is inconsistent")
    run_count = sum(len(item["runs"]) for item in expected_blocks)
    if manifest.get("runCount") != run_count:
        _validation_error("manifest runCount is inconsistent")
    expected_cost_plan = build_cost_plan(models, suite["variants"], trials, configured_methods)
    if manifest.get("costPlan") != expected_cost_plan:
        _validation_error("manifest costPlan is inconsistent with the registered schedule")
    credential_presence = manifest.get("credentialPresentAtPreparation")
    if credential_presence != {model["key"]: True for model in models}:
        _validation_error("formal manifest must record both credentials as present at preparation")

    return ValidatedExperiment(
        manifest=manifest,
        manifest_hash=canonical_hash(manifest),
        suite=suite,
        variants={item["taskId"]: item for item in suite["variants"]},
        models={item["key"]: item for item in models},
        prompt_directory=prompt_directory.resolve(),
    )


def _scheduled_runs(experiment: ValidatedExperiment) -> list[dict]:
    result = []
    for block_index, block in enumerate(experiment.manifest["blocks"]):
        for order_index, run in enumerate(block["runs"]):
            result.append({
                "blockIndex": block_index,
                "orderIndex": order_index,
                "blockId": block["blockId"],
                "modelKey": block["modelKey"],
                "taskId": block["taskId"],
                "family": block["family"],
                "interactionLength": block["interactionLength"],
                "trial": block["trial"],
                "runId": run["runId"],
                "method": run["method"],
            })
    return result


def _seed_for_round(randomization_seed: int, block_id: str, round_number: int) -> int:
    digest = hashlib.sha256(f"{randomization_seed}:{block_id}:{round_number}".encode()).hexdigest()
    return int(digest[:8], 16) & 0x7FFFFFFF


def _event_totals(records: list[dict]) -> dict:
    debit = Decimal("0")
    observed = Decimal("0")
    input_tokens = 0
    output_tokens = 0
    provider_calls = 0
    blocked_calls = 0
    for record in records:
        events = record.get("callEvents", [])
        if not isinstance(events, list):
            raise RunnerStateError("saved callEvents must be a list")
        for event in events:
            if not isinstance(event, dict):
                raise RunnerStateError("saved call event must be an object")
            debit += _decimal(event.get("budgetDebitUsd", 0), "budgetDebitUsd")
            if event.get("observedCostUsd") is not None:
                observed += _decimal(event["observedCostUsd"], "observedCostUsd")
            usage = event.get("usage") or {}
            if usage.get("inputTokens") is not None:
                value = usage["inputTokens"]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise RunnerStateError("saved input token count is invalid")
                input_tokens += value
            if usage.get("outputTokens") is not None:
                value = usage["outputTokens"]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise RunnerStateError("saved output token count is invalid")
                output_tokens += value
            if event.get("status") == "blocked_budget":
                blocked_calls += 1
            else:
                provider_calls += 1
    return {
        "debit": debit,
        "observed": observed,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "providerCalls": provider_calls,
        "blockedCalls": blocked_calls,
    }


class BudgetLedger:
    """Reserve a conservative call ceiling before any network request."""

    def __init__(self, cap, initial: dict):
        self.cap = _decimal(cap, "budgetCapUsd")
        self.debit = initial["debit"]
        self.observed = initial["observed"]
        self.input_tokens = initial["inputTokens"]
        self.output_tokens = initial["outputTokens"]
        self.provider_calls = initial["providerCalls"]
        self.blocked_calls = initial["blockedCalls"]
        self.halted = False
        if self.debit > self.cap:
            raise RunnerStateError("saved budget debit exceeds the registered cap")

    def invoke(
        self,
        *,
        prompt: str,
        config: ModelConfig,
        model_entry: dict,
        run: dict,
        round_item: dict,
        events: list[dict],
        persist,
        invoke_fn,
    ) -> dict:
        input_upper = len(prompt.encode("utf-8")) + INPUT_TOKEN_OVERHEAD
        if input_upper - INPUT_TOKEN_OVERHEAD > MAX_PROMPT_BYTES:
            raise ModelCallError(
                "EXPERIMENT_PROMPT_TOO_LARGE",
                "rendered prompt exceeds the registered 2 MB limit",
                details={"promptBytes": input_upper - INPUT_TOKEN_OVERHEAD, "limitBytes": MAX_PROMPT_BYTES},
            )
        output_upper = config.max_output_tokens
        input_rate = Decimal(str(model_entry["inputCostPerMillionUsd"]))
        output_rate = Decimal(str(model_entry["outputCostPerMillionUsd"]))
        reservation = (
            Decimal(input_upper) * input_rate + Decimal(output_upper) * output_rate
        ) / Decimal(1_000_000)
        event = {
            "callId": f"{run['runId']}:round:{round_item['round']}",
            "runId": run["runId"],
            "blockId": run["blockId"],
            "modelKey": run["modelKey"],
            "method": run["method"],
            "round": round_item["round"],
            "promptHash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "requestedAt": utc_now(),
            "inputTokenUpperBound": input_upper,
            "outputTokenUpperBound": output_upper,
            "reservedCostUsd": _money(reservation),
            "observedCostUsd": None,
            "budgetDebitUsd": 0.0,
            "status": "reserving",
        }
        events.append(event)
        if self.debit + reservation > self.cap:
            self.halted = True
            self.blocked_calls += 1
            event.update({
                "status": "blocked_budget",
                "finishedAt": utc_now(),
                "remainingBudgetUsd": _money(self.cap - self.debit),
            })
            persist()
            raise ModelCallError(
                "EXPERIMENT_BUDGET_GUARD",
                "registered budget cannot cover the conservative upper bound for this call",
                details={
                    "requiredUpperBoundUsd": _money(reservation),
                    "remainingBudgetUsd": _money(self.cap - self.debit),
                },
            )

        self.debit += reservation
        self.provider_calls += 1
        event["status"] = "reserved"
        event["budgetDebitUsd"] = _money(reservation)
        persist()
        try:
            result = invoke_fn(prompt, config)
        except ModelCallError as error:
            event.update({
                "status": "model_error",
                "finishedAt": utc_now(),
                "error": error.metadata(),
            })
            persist()
            raise
        except Exception as error:
            event.update({
                "status": "client_error",
                "finishedAt": utc_now(),
                "error": {"type": type(error).__name__, "message": str(error)},
            })
            persist()
            raise

        if not isinstance(result, dict) or not isinstance(result.get("text"), str) or not isinstance(result.get("metadata"), dict):
            event.update({
                "status": "client_error",
                "finishedAt": utc_now(),
                "error": {"type": "InvalidClientResult", "message": "model client returned an invalid result"},
            })
            persist()
            raise RunnerStateError("model client returned an invalid result")
        metadata = deepcopy(result["metadata"])
        input_tokens = metadata.get("inputTokens")
        output_tokens = metadata.get("outputTokens")
        actual = None
        if isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and input_tokens >= 0 and isinstance(output_tokens, int) and not isinstance(output_tokens, bool) and output_tokens >= 0:
            actual = (
                Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate
            ) / Decimal(1_000_000)
            if input_tokens > input_upper or output_tokens > output_upper:
                self.debit += actual - reservation
                self.observed += actual
                self.input_tokens += input_tokens
                self.output_tokens += output_tokens
                event.update({
                    "status": "usage_out_of_bounds",
                    "finishedAt": utc_now(),
                    "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
                    "observedCostUsd": _money(actual),
                    "budgetDebitUsd": _money(actual),
                    "rawResponseHash": hashlib.sha256(result["text"].encode("utf-8")).hexdigest(),
                    "rawResponse": result["text"],
                    "modelMetadata": metadata,
                })
                self.halted = True
                persist()
                raise ModelCallError(
                    "MODEL_USAGE_OUT_OF_BOUNDS",
                    "provider-reported usage exceeded the pre-call conservative bound",
                    details={"inputUpper": input_upper, "outputUpper": output_upper},
                )
            self.debit += actual - reservation
            self.observed += actual
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            event_status = "succeeded"
            budget_debit = actual
        else:
            event_status = "succeeded_usage_missing"
            budget_debit = reservation

        event.update({
            "status": event_status,
            "finishedAt": utc_now(),
            "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
            "observedCostUsd": _money(actual) if actual is not None else None,
            "budgetDebitUsd": _money(budget_debit),
            "rawResponseHash": hashlib.sha256(result["text"].encode("utf-8")).hexdigest(),
            "rawResponse": result["text"],
            "modelMetadata": metadata,
        })
        persist()
        metadata["observedCostUsd"] = event["observedCostUsd"]
        metadata["budgetDebitUsd"] = event["budgetDebitUsd"]
        metadata["usageReported"] = actual is not None
        return {"text": result["text"], "metadata": metadata}


def _record_with_hash(record: dict) -> dict:
    result = deepcopy(record)
    result["recordHash"] = canonical_hash(record)
    return result


def _validate_record_hash(record: dict, path: Path) -> None:
    expected = record.get("recordHash")
    payload = deepcopy(record)
    payload.pop("recordHash", None)
    if not isinstance(expected, str) or expected != canonical_hash(payload):
        raise RunnerStateError(f"saved record hash mismatch: {path}")


def _load_saved_records(directory: Path) -> list[tuple[Path, dict]]:
    if not directory.exists():
        return []
    result = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith("."):
            continue
        if not path.is_file() or path.suffix != ".json":
            raise RunnerStateError(f"unexpected file in runner state directory: {path}")
        result.append((path, _read_object(path)))
    return result


def _archive_attempt(path: Path, record: dict, archive_directory: Path, reason: str) -> dict:
    archived = deepcopy(record)
    archived.pop("recordHash", None)
    archived["archivedAt"] = utc_now()
    archived["archiveReason"] = reason
    fingerprint = canonical_hash(archived)[:16]
    destination = archive_directory / f"{record.get('runId', 'unknown')}-{fingerprint}.json"
    archived = _record_with_hash(archived)
    _atomic_create_json(destination, archived)
    path.unlink()
    _sync_directory(path.parent)
    return archived


def _prepare_result_directory(
    output_directory: Path,
    experiment: ValidatedExperiment,
    *,
    resume: bool,
    retry_interrupted: bool,
) -> tuple[dict[str, dict], list[dict]]:
    snapshot_path = output_directory / "manifest.snapshot.json"
    state_directories = ["runs", "in_progress", "failures", "interruptions"]
    if resume:
        if not snapshot_path.exists():
            raise RunnerStateError("--resume requires an existing manifest.snapshot.json")
        snapshot = _read_object(snapshot_path)
        if snapshot.get("manifestHash") != experiment.manifest_hash or snapshot.get("manifest") != experiment.manifest:
            raise RunnerStateError("resume manifest does not match the result directory snapshot")
    else:
        existing = [item for item in output_directory.iterdir() if item.name != ".runner.lock"]
        if existing:
            raise RunnerStateError("new execution requires an empty output directory; use --resume for an existing run")
        for name in state_directories:
            (output_directory / name).mkdir(parents=True, exist_ok=False, mode=0o700)
        _atomic_create_json(snapshot_path, {
            "schemaVersion": SCHEMA_VERSION,
            "createdAt": utc_now(),
            "manifestHash": experiment.manifest_hash,
            "manifest": experiment.manifest,
        })

    for name in state_directories:
        (output_directory / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    schedule = {item["runId"]: item for item in _scheduled_runs(experiment)}
    completed: dict[str, dict] = {}
    for path, record in _load_saved_records(output_directory / "runs"):
        _validate_record_hash(record, path)
        run_id = record.get("runId")
        if path.stem != run_id or run_id not in schedule:
            raise RunnerStateError(f"saved completed run is not in the manifest: {path}")
        if record.get("status") != "completed" or record.get("experimentId") != experiment.manifest["experimentId"] or record.get("manifestHash") != experiment.manifest_hash:
            raise RunnerStateError(f"saved completed run metadata mismatch: {path}")
        expected = schedule[run_id]
        for key in ("blockId", "modelKey", "taskId", "family", "interactionLength", "trial", "method"):
            if record.get(key) != expected[key]:
                raise RunnerStateError(f"saved completed run schedule mismatch: {path}")
        completed[run_id] = record

    interrupted_pairs = _load_saved_records(output_directory / "in_progress")
    failed_pairs = _load_saved_records(output_directory / "failures")
    unresolved = []
    for path, record in [*interrupted_pairs, *failed_pairs]:
        if path.parent.name == "failures":
            _validate_record_hash(record, path)
        run_id = record.get("runId")
        if path.stem != run_id or run_id not in schedule or record.get("manifestHash") != experiment.manifest_hash:
            raise RunnerStateError(f"saved interrupted run metadata mismatch: {path}")
        if run_id in completed:
            # The immutable run was committed before a crash removed its marker.
            path.unlink()
            _sync_directory(path.parent)
            continue
        unresolved.append((path, record))
    if unresolved and not retry_interrupted:
        ids = ", ".join(sorted({record["runId"] for _, record in unresolved}))
        raise RunnerStateError(
            "interrupted/failed attempts require explicit --retry-interrupted acknowledgement: " + ids
        )
    for path, record in unresolved:
        _archive_attempt(path, record, output_directory / "interruptions", "explicit_retry_acknowledgement")

    archived = []
    for path, record in _load_saved_records(output_directory / "interruptions"):
        _validate_record_hash(record, path)
        run_id = record.get("runId")
        if run_id not in schedule or record.get("manifestHash") != experiment.manifest_hash:
            raise RunnerStateError(f"archived attempt metadata mismatch: {path}")
        expected = schedule[run_id]
        for key in ("blockId", "modelKey", "taskId", "trial", "method"):
            if record.get(key) != expected[key]:
                raise RunnerStateError(f"archived attempt schedule mismatch: {path}")
        archived.append(record)
    return completed, archived


def _credentials(experiment: ValidatedExperiment, supplied: dict[str, str] | None) -> dict[str, str]:
    result = {}
    for key, model in experiment.models.items():
        secret = supplied.get(key, "") if supplied is not None else os.environ.get(model["apiKeyEnv"], "")
        if not isinstance(secret, str) or not secret.strip():
            raise RunnerStateError(f"credential environment variable {model['apiKeyEnv']} is empty")
        result[key] = secret
        ModelConfig(
            provider=model["provider"],
            base_url=model["baseUrl"],
            model=model["model"],
            api_key=secret,
            temperature=model["temperature"],
            max_output_tokens=model["maxOutputTokens"],
            timeout_seconds=model["timeoutSeconds"],
        ).validate()
    return result


def _progress(
    experiment: ValidatedExperiment,
    *,
    status: str,
    completed_count: int,
    archived_count: int,
    ledger: BudgetLedger,
    current_run_id: str | None = None,
) -> dict:
    scheduled = experiment.manifest["runCount"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "experimentId": experiment.manifest["experimentId"],
        "manifestHash": experiment.manifest_hash,
        "formalExperiment": True,
        "fixture": False,
        "updatedAt": utc_now(),
        "status": status,
        "currentRunId": current_run_id,
        "scheduledRunCount": scheduled,
        "completedRunCount": completed_count,
        "pendingRunCount": scheduled - completed_count,
        "archivedInterruptedAttemptCount": archived_count,
        "providerCallCount": ledger.provider_calls,
        "budgetBlockedCallCount": ledger.blocked_calls,
        "usage": {
            "inputTokens": ledger.input_tokens,
            "outputTokens": ledger.output_tokens,
        },
        "observedCostUsd": _money(ledger.observed),
        "budgetDebitUsd": _money(ledger.debit),
        "budgetCapUsd": _money(ledger.cap),
        "rawResponsesStored": True,
    }


def execute_validated_experiment(
    experiment: ValidatedExperiment,
    output_directory: Path,
    *,
    resume: bool = False,
    retry_interrupted: bool = False,
    max_runs: int | None = None,
    invoke_fn=invoke_json_prompt,
    supplied_credentials: dict[str, str] | None = None,
) -> dict:
    """Execute only an already validated experiment (dependency-injectable for tests)."""
    if max_runs is not None and (isinstance(max_runs, bool) or not isinstance(max_runs, int) or max_runs < 1):
        raise RunnerStateError("max_runs must be a positive integer")
    if retry_interrupted and not resume:
        raise RunnerStateError("retry_interrupted requires resume")
    output_directory = output_directory.resolve()
    with _exclusive_runner_lock(output_directory):
        completed, archived = _prepare_result_directory(
            output_directory,
            experiment,
            resume=resume,
            retry_interrupted=retry_interrupted,
        )
        history = [*completed.values(), *archived]
        ledger = BudgetLedger(experiment.manifest["configuration"]["budgetCapUsd"], _event_totals(history))
        progress_path = output_directory / "progress.json"
        previous_progress = _read_object(progress_path) if resume and progress_path.exists() else None
        if previous_progress and previous_progress.get("status") == "budget_stopped":
            ledger.halted = True
        if len(completed) == experiment.manifest["runCount"]:
            progress = _progress(
                experiment,
                status="complete",
                completed_count=len(completed),
                archived_count=len(archived),
                ledger=ledger,
            )
            _atomic_replace_json(progress_path, progress)
            return progress
        if ledger.halted:
            progress = _progress(
                experiment,
                status="budget_stopped",
                completed_count=len(completed),
                archived_count=len(archived),
                ledger=ledger,
            )
            _atomic_replace_json(progress_path, progress)
            return progress

        credentials = _credentials(experiment, supplied_credentials)
        progress = _progress(
            experiment,
            status="running",
            completed_count=len(completed),
            archived_count=len(archived),
            ledger=ledger,
        )
        _atomic_replace_json(progress_path, progress)
        new_runs = 0

        for run in _scheduled_runs(experiment):
            if run["runId"] in completed:
                continue
            if max_runs is not None and new_runs >= max_runs:
                progress = _progress(
                    experiment,
                    status="paused_run_limit",
                    completed_count=len(completed),
                    archived_count=len(archived),
                    ledger=ledger,
                )
                _atomic_replace_json(progress_path, progress)
                return progress

            model = experiment.models[run["modelKey"]]
            variant = experiment.variants[run["taskId"]]
            marker_path = output_directory / "in_progress" / f"{run['runId']}.json"
            marker = {
                "schemaVersion": SCHEMA_VERSION,
                "experimentId": experiment.manifest["experimentId"],
                "manifestHash": experiment.manifest_hash,
                "runId": run["runId"],
                "blockId": run["blockId"],
                "modelKey": run["modelKey"],
                "taskId": run["taskId"],
                "method": run["method"],
                "trial": run["trial"],
                "status": "running",
                "startedAt": utc_now(),
                "updatedAt": utc_now(),
                "callEvents": [],
            }
            _atomic_create_json(marker_path, marker)

            def persist_marker() -> None:
                marker["updatedAt"] = utc_now()
                marker["budgetDebitUsd"] = _money(sum(
                    (_decimal(item.get("budgetDebitUsd", 0), "budgetDebitUsd") for item in marker["callEvents"]),
                    Decimal("0"),
                ))
                _atomic_replace_json(marker_path, marker)

            def response_provider(*, method: str, prompt: str, round_item: dict) -> dict:
                if method != run["method"]:
                    raise RunnerStateError("orchestrator method changed inside a scheduled run")
                seed = None
                if model["seedSupport"]:
                    seed = _seed_for_round(
                        experiment.manifest["configuration"]["randomizationSeed"],
                        run["blockId"],
                        round_item["round"],
                    )
                config = ModelConfig(
                    provider=model["provider"],
                    base_url=model["baseUrl"],
                    model=model["model"],
                    api_key=credentials[run["modelKey"]],
                    temperature=model["temperature"],
                    max_output_tokens=model["maxOutputTokens"],
                    timeout_seconds=model["timeoutSeconds"],
                    seed=seed,
                )
                return ledger.invoke(
                    prompt=prompt,
                    config=config,
                    model_entry=model,
                    run=run,
                    round_item=round_item,
                    events=marker["callEvents"],
                    persist=persist_marker,
                    invoke_fn=invoke_fn,
                )

            try:
                episode = run_method(
                    variant,
                    run["method"],
                    experiment.prompt_directory,
                    episode_key=run["runId"],
                    response_provider=response_provider,
                    strict=False,
                    include_raw_response=True,
                    formal_experiment=True,
                    include_state_snapshots=True,
                    state_seed=variant["initialFixture"],
                )
                statuses = Counter(item["status"] for item in episode["rounds"])
                budget_stopped = any(
                    item.get("error", {}).get("code") == "EXPERIMENT_BUDGET_GUARD"
                    for item in episode["rounds"]
                )
                if budget_stopped:
                    outcome = "budget_stopped"
                elif any(status in statuses for status in ("model_error", "invalid_output", "state_rejected", "guard_rejected", "version_conflict", "restore_target_missing")):
                    outcome = "completed_with_recorded_errors"
                else:
                    outcome = "completed"
                run_totals = _event_totals([marker])
                record_payload = {
                    "schemaVersion": SCHEMA_VERSION,
                    "experimentId": experiment.manifest["experimentId"],
                    "manifestHash": experiment.manifest_hash,
                    **run,
                    "status": "completed",
                    "outcome": outcome,
                    "startedAt": marker["startedAt"],
                    "completedAt": utc_now(),
                    "formalExperiment": True,
                    "fixture": False,
                    "model": {
                        "key": model["key"],
                        "provider": model["provider"],
                        "model": model["model"],
                        "versionDate": model["versionDate"],
                        "temperature": model["temperature"],
                        "maxOutputTokens": model["maxOutputTokens"],
                        "seedSupport": model["seedSupport"],
                    },
                    "roundStatusCounts": dict(sorted(statuses.items())),
                    "callEvents": deepcopy(marker["callEvents"]),
                    "cost": {
                        "observedCostUsd": _money(run_totals["observed"]),
                        "budgetDebitUsd": _money(run_totals["debit"]),
                        "inputTokens": run_totals["inputTokens"],
                        "outputTokens": run_totals["outputTokens"],
                        "providerCallCount": run_totals["providerCalls"],
                        "budgetBlockedCallCount": run_totals["blockedCalls"],
                    },
                    "episode": episode,
                }
                record = _record_with_hash(record_payload)
                result_path = output_directory / "runs" / f"{run['runId']}.json"
                _atomic_create_json(result_path, record)
                marker_path.unlink()
                _sync_directory(marker_path.parent)
                completed[run["runId"]] = record
                new_runs += 1
                status = "budget_stopped" if ledger.halted or budget_stopped else "running"
                progress = _progress(
                    experiment,
                    status=status,
                    completed_count=len(completed),
                    archived_count=len(archived),
                    ledger=ledger,
                    current_run_id=None,
                )
                _atomic_replace_json(progress_path, progress)
                if status == "budget_stopped":
                    return progress
            except Exception as error:
                marker.update({
                    "status": "runner_error",
                    "updatedAt": utc_now(),
                    "error": {"type": type(error).__name__, "message": str(error)},
                })
                _atomic_replace_json(marker_path, marker)
                failure = _record_with_hash(marker)
                _atomic_create_json(output_directory / "failures" / f"{run['runId']}.json", failure)
                marker_path.unlink()
                _sync_directory(marker_path.parent)
                progress = _progress(
                    experiment,
                    status="runner_error",
                    completed_count=len(completed),
                    archived_count=len(archived),
                    ledger=ledger,
                    current_run_id=run["runId"],
                )
                _atomic_replace_json(progress_path, progress)
                raise RunnerStateError(f"runner failed during {run['runId']}: {type(error).__name__}") from error

        progress = _progress(
            experiment,
            status="complete",
            completed_count=len(completed),
            archived_count=len(archived),
            ledger=ledger,
        )
        _atomic_replace_json(progress_path, progress)
        return progress


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--methods-config", type=Path, default=DEFAULT_METHODS)
    parser.add_argument("--models-config", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-interrupted", action="store_true")
    parser.add_argument("--max-runs", type=int)
    args = parser.parse_args()
    if args.retry_interrupted and not args.resume:
        parser.error("--retry-interrupted requires --resume")
    if args.max_runs is not None and args.max_runs < 1:
        parser.error("--max-runs must be positive")

    try:
        experiment = validate_formal_manifest(
            args.manifest,
            tasks_path=args.tasks,
            schema_path=args.schema,
            methods_path=args.methods_config,
            models_path=args.models_config,
            prompt_directory=args.prompt_dir,
        )
        output_directory = args.output_dir or ROOT / "results" / "raw" / experiment.manifest["experimentId"]
        progress = execute_validated_experiment(
            experiment,
            output_directory,
            resume=args.resume,
            retry_interrupted=args.retry_interrupted,
            max_runs=args.max_runs,
        )
    except (RunnerValidationError, RunnerStateError) as error:
        print(json.dumps({"started": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps({
        "started": True,
        "status": progress["status"],
        "completedRunCount": progress["completedRunCount"],
        "pendingRunCount": progress["pendingRunCount"],
        "budgetDebitUsd": progress["budgetDebitUsd"],
        "outputDirectory": str(output_directory),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
