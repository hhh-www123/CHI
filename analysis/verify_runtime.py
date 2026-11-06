#!/usr/bin/env python3
"""Run the isolated mixed-effects contract test and emit bound evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "analysis" / "runtime-verification.json"
DEFAULT_LIBRARY = ROOT / ".r-library"
ASSETS = {
    "modelSpec": ROOT / "analysis" / "model-spec.json",
    "preparationScript": ROOT / "analysis" / "prepare_mixed_effects.mjs",
    "modelScript": ROOT / "analysis" / "mixed_effects.R",
    "fixtureGenerator": ROOT / "analysis" / "generate_mixed_effects_fixture.mjs",
    "verificationScript": Path(__file__).resolve(),
}
REQUIRED_RUNTIME_KEYS = {"R", "lme4", "emmeans", "jsonlite", "digest"}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    if result.returncode:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        raise RuntimeError(f"contract command failed ({result.returncode}): {' '.join(command)}")
    return result


def model_summary(models: object) -> dict:
    if not isinstance(models, dict) or len(models) != 2:
        raise ValueError("contract result must contain two model identities")
    summary = {}
    for key, value in models.items():
        if not isinstance(value, dict):
            raise ValueError(f"invalid model result: {key}")
        diagnostics = value.get("diagnostics")
        sensitivity = value.get("templateFixedEffectSensitivity")
        if (
            value.get("estimable") is not True
            or value.get("confirmatoryInferenceReady") is not True
            or not isinstance(diagnostics, dict)
            or diagnostics.get("singular") is not False
            or diagnostics.get("optimizerCode") != 0
            or diagnostics.get("warnings") != []
            or diagnostics.get("convergenceMessages") != []
            or not isinstance(sensitivity, dict)
            or sensitivity.get("estimable") is not True
        ):
            raise ValueError(f"mixed-effects contract diagnostics failed for {key}")
        summary[key] = {
            "estimable": True,
            "confirmatoryInferenceReady": True,
            "primaryDiagnostics": {
                "optimizerCode": diagnostics["optimizerCode"],
                "singular": diagnostics["singular"],
                "maxAbsoluteGradient": diagnostics.get("maxAbsoluteGradient"),
                "warningCount": 0,
                "convergenceMessageCount": 0,
            },
            "templateFixedEffectSensitivityEstimable": True,
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--r-library", type=Path, default=DEFAULT_LIBRARY)
    args = parser.parse_args()

    rscript = shutil.which("Rscript")
    node = shutil.which("node")
    if rscript is None or node is None:
        raise SystemExit("Rscript and node must both be available")
    if not args.r_library.is_dir():
        raise SystemExit(f"R library does not exist: {args.r_library}")

    environment = os.environ.copy()
    environment["R_LIBS_USER"] = str(args.r_library.resolve())
    with tempfile.TemporaryDirectory(prefix="travel-mixed-runtime-") as temporary:
        temporary_path = Path(temporary)
        fixture_path = temporary_path / "fixture.json"
        input_directory = temporary_path / "input"
        result_path = temporary_path / "result.json"
        run([node, "analysis/generate_mixed_effects_fixture.mjs", str(fixture_path)])
        run([node, "analysis/prepare_mixed_effects.mjs", str(fixture_path), str(input_directory), "--contract-test"])
        run([rscript, "analysis/mixed_effects.R", str(input_directory), str(result_path), "--contract-test"], environment)

        manifest = json.loads((input_directory / "analysis-manifest.json").read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        design = manifest.get("designAudit", {})
        if (
            manifest.get("contractTest") is not True
            or manifest.get("formalAnalysisReady") is not False
            or design.get("episodes") != 960
            or design.get("blocks") != 240
            or result.get("contractTest") is not True
            or result.get("formalAnalysisReady") is not False
            or result.get("modelFitsReady") is not True
        ):
            raise ValueError("mixed-effects contract flags or design counts are invalid")
        runtime = result.get("runtime")
        if not isinstance(runtime, dict) or set(runtime) != REQUIRED_RUNTIME_KEYS or any(not isinstance(value, str) or not value for value in runtime.values()):
            raise ValueError("contract result did not report the required R/package versions")
        models = model_summary(result.get("models"))
        analysis_contract = manifest.get("provenance", {}).get("analysisContract", {})
        if analysis_contract.get("runtimeStatus") not in {"verified-unlocked", "frozen-tested"}:
            raise ValueError("model specification runtime must be verified before evidence generation")
        if (
            analysis_contract.get("runtimeStatus") == "frozen-tested"
            and analysis_contract.get("runtimeVersions") != runtime
        ):
            raise ValueError("frozen runtime versions do not match the executing R environment")
        if analysis_contract.get("specHash") is None:
            raise ValueError("contract result is missing the model specification hash")

        evidence = {
            "schemaVersion": "mixed-effects-runtime-verification-1.0",
            "status": "passed",
            "verifiedAt": datetime.now(timezone.utc).isoformat(),
            "syntheticContractTest": True,
            "formalAnalysisReady": False,
            "modelFitsReady": True,
            "design": {
                "episodes": design["episodes"],
                "blocks": design["blocks"],
                "modelCount": len(design.get("models", [])),
                "templateCount": len(design.get("templates", [])),
                "methods": design.get("methods"),
                "interactionLengths": design.get("interactionLengths"),
                "trialsPerCell": design.get("trialsPerCell"),
            },
            "runtimeVersions": runtime,
            "platform": result.get("sessionInfo", [])[:3],
            "assetHashes": {key: file_sha256(path) for key, path in ASSETS.items()},
            "modelSpecCanonicalHash": analysis_contract["specHash"],
            "analysisContractHash": manifest.get("provenance", {}).get("analysisContractHash"),
            "preparedEpisodesSha256": manifest.get("files", {}).get("episodesCsv", {}).get("sha256"),
            "models": models,
            "note": "Synthetic contract evidence only; it is not a formal experiment or an empirical result.",
        }

    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "verified": True,
        "formalAnalysisReady": False,
        "runtimeVersions": evidence["runtimeVersions"],
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
