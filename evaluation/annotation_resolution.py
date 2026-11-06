"""Validate blinded annotation coverage and resolve labels without unblinding."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

try:
    from .prepare_blinded_annotations import PACKET_SCHEMA_VERSION
    from .task_suite import canonical_hash
except ImportError:
    from prepare_blinded_annotations import PACKET_SCHEMA_VERSION
    from task_suite import canonical_hash


class AnnotationResolutionError(ValueError):
    pass


def validate_annotation_schema(data: dict, schema: dict) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda item: list(item.absolute_path))
    if errors:
        rendered = [f"/{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in errors[:20]]
        raise AnnotationResolutionError("annotation schema validation failed:\n" + "\n".join(rendered))


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    if not pairs:
        return None
    observed = sum(left == right for left, right in pairs) / len(pairs)
    left_true = sum(left for left, _ in pairs) / len(pairs)
    right_true = sum(right for _, right in pairs) / len(pairs)
    expected = left_true * right_true + (1 - left_true) * (1 - right_true)
    if expected == 1:
        return 1.0 if observed == 1 else None
    return (observed - expected) / (1 - expected)


def krippendorff_alpha_ordinal(units: list[list[int]]) -> float | None:
    """Krippendorff's alpha with the registered ordinal disagreement metric."""
    usable = [[value for value in unit if isinstance(value, int) and not isinstance(value, bool)] for unit in units]
    usable = [unit for unit in usable if len(unit) >= 2]
    if not usable:
        return None
    coincidence: dict[tuple[int, int], float] = defaultdict(float)
    for unit in usable:
        denominator = len(unit) - 1
        for left_index, left in enumerate(unit):
            for right_index, right in enumerate(unit):
                if left_index != right_index:
                    coincidence[(left, right)] += 1 / denominator
    categories = sorted({value for unit in usable for value in unit})
    marginals = {
        category: sum(coincidence[(category, other)] for other in categories)
        for category in categories
    }
    total = sum(marginals.values())
    if total <= 1:
        return None

    def distance(left: int, right: int) -> float:
        if left == right:
            return 0.0
        low, high = sorted((left, right))
        between = sum(marginals[item] for item in categories if low <= item <= high)
        return (between - (marginals[left] + marginals[right]) / 2) ** 2

    observed = sum(
        coincidence[(left, right)] * distance(left, right)
        for left in categories for right in categories
    ) / total
    expected = sum(
        marginals[left] * marginals[right] * distance(left, right)
        for left in categories for right in categories
    ) / (total * (total - 1))
    if expected == 0:
        return 1.0 if observed == 0 else None
    return 1 - observed / expected


def validate_packet_mapping(packet: dict, mapping: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    if packet.get("schemaVersion") != PACKET_SCHEMA_VERSION or packet.get("blinded") is not True or packet.get("formalExperiment") is not True:
        raise AnnotationResolutionError("invalid blinded packet provenance")
    if mapping.get("schemaVersion") != PACKET_SCHEMA_VERSION or mapping.get("private") is not True:
        raise AnnotationResolutionError("invalid private mapping provenance")
    mapping_payload = deepcopy(mapping)
    record_hash = mapping_payload.pop("recordHash", None)
    if not isinstance(record_hash, str) or record_hash != canonical_hash(mapping_payload):
        raise AnnotationResolutionError("private mapping recordHash mismatch")
    if mapping.get("packetHash") != canonical_hash(packet):
        raise AnnotationResolutionError("private mapping does not match the blinded packet")
    for key in (
        "experimentId", "manifestHash", "analysisContractHash",
        "benchmarkHash", "benchmarkSourceHash", "promptHash", "sampleCount",
    ):
        if mapping.get(key) != packet.get(key):
            raise AnnotationResolutionError(f"packet/private mapping mismatch: {key}")
    samples = packet.get("samples")
    private_records = mapping.get("records")
    if not isinstance(samples, list) or not isinstance(private_records, list):
        raise AnnotationResolutionError("packet samples and mapping records must be arrays")
    public_by_id = {}
    for sample in samples:
        sample_id = sample.get("sampleId") if isinstance(sample, dict) else None
        if not isinstance(sample_id, str) or not sample_id or sample_id in public_by_id:
            raise AnnotationResolutionError("packet sample IDs must be unique non-empty strings")
        public_by_id[sample_id] = sample
    private_by_id = {}
    for record in private_records:
        sample_id = record.get("sampleId") if isinstance(record, dict) else None
        if not isinstance(sample_id, str) or not sample_id or sample_id in private_by_id:
            raise AnnotationResolutionError("private sample IDs must be unique non-empty strings")
        private_by_id[sample_id] = record
    if set(public_by_id) != set(private_by_id) or len(public_by_id) != packet.get("sampleCount"):
        raise AnnotationResolutionError("packet and mapping sample sets differ")
    for sample_id, sample in public_by_id.items():
        private = private_by_id[sample_id]
        for key in ("sampleType", "taskId", "round"):
            if sample.get(key) != private.get(key):
                raise AnnotationResolutionError(f"sample metadata mismatch for {sample_id}: {key}")
    return public_by_id, private_by_id


def _unique_label_map(items: list[dict], id_key: str, sample_id: str, label_name: str) -> dict[str, dict]:
    result = {}
    for item in items:
        identifier = item[id_key]
        if identifier in result:
            raise AnnotationResolutionError(f"duplicate {label_name} {identifier} in {sample_id}")
        result[identifier] = item
    return result


def _record_label_maps(record: dict) -> dict:
    sample_id = record["sampleId"]
    return {
        "goals": _unique_label_map(record["goalLabels"], "goalId", sample_id, "goal label"),
        "stale": _unique_label_map(record["staleGoalLabels"], "goalId", sample_id, "stale-goal label"),
        "candidates": _unique_label_map(record["candidateLabels"], "candidateId", sample_id, "candidate label"),
    }


def _require_evidence(item: dict, value_key: str, sample_id: str) -> None:
    if item[value_key] is not None and not item.get("evidence", "").strip():
        raise AnnotationResolutionError(f"non-null {value_key} requires evidence in {sample_id}")


def _resolve_scalar(
    independent: list[dict],
    adjudicator: dict | None,
    *,
    collection: str,
    identifier: str,
    value_key: str,
    sample_id: str,
):
    independent_values = [item[collection][identifier][value_key] for item in independent]
    needs_adjudication = any(value is None for value in independent_values) or len(set(independent_values)) != 1
    if adjudicator is not None:
        value = adjudicator[collection][identifier][value_key]
        if value is None:
            raise AnnotationResolutionError(f"adjudicator left {identifier}/{value_key} null in {sample_id}")
        return value, needs_adjudication
    if needs_adjudication:
        raise AnnotationResolutionError(f"unresolved label disagreement or missing value for {identifier}/{value_key} in {sample_id}")
    return independent_values[0], False


def resolve_annotations(packet: dict, mapping: dict, annotations: dict, schema: dict) -> tuple[dict[str, dict], dict]:
    validate_annotation_schema(annotations, schema)
    public_by_id, private_by_id = validate_packet_mapping(packet, mapping)
    for key in ("benchmarkHash", "promptHash"):
        if annotations.get(key) != packet.get(key):
            raise AnnotationResolutionError(f"annotations {key} does not match the packet")

    records_by_sample: dict[str, list[dict]] = defaultdict(list)
    identities = set()
    for record in annotations["records"]:
        sample_id = record["sampleId"]
        if sample_id not in public_by_id:
            raise AnnotationResolutionError(f"annotation references unknown sample: {sample_id}")
        identity = (sample_id, record["annotatorId"])
        if identity in identities:
            raise AnnotationResolutionError(f"duplicate annotation record: {identity}")
        identities.add(identity)
        sample = public_by_id[sample_id]
        if record["taskId"] != sample["taskId"] or record["round"] != sample["round"]:
            raise AnnotationResolutionError(f"annotation task/round mismatch: {sample_id}")
        expected = sample["expectedLabels"]
        maps = _record_label_maps(record)
        if set(maps["goals"]) != set(expected["goalIds"]):
            raise AnnotationResolutionError(f"goal label IDs do not match packet: {sample_id}")
        if set(maps["stale"]) != set(expected["staleGoalIds"]):
            raise AnnotationResolutionError(f"stale-goal label IDs do not match packet: {sample_id}")
        if set(maps["candidates"]) != set(expected["candidateIds"]):
            raise AnnotationResolutionError(f"candidate label IDs do not match packet: {sample_id}")
        for item in maps["goals"].values():
            _require_evidence(item, "score", sample_id)
        for item in maps["stale"].values():
            _require_evidence(item, "leaked", sample_id)
        for item in maps["candidates"].values():
            _require_evidence(item, "relevance", sample_id)
            if item["humanBest"] is not None and not item.get("evidence", "").strip():
                raise AnnotationResolutionError(f"non-null humanBest requires evidence in {sample_id}")
        if expected["conflictLabel"] is False and record["conflictLabel"] != {"groundTruth": "not_applicable", "systemDecision": "not_applicable"}:
            raise AnnotationResolutionError(f"candidate-only sample conflict label must be not_applicable: {sample_id}")
        enriched = deepcopy(record)
        enriched["_maps"] = maps
        records_by_sample[sample_id].append(enriched)

    required_samples = [item for item in public_by_id.values() if item.get("requiresHumanJudgment") is True]
    resolved = {}
    double_coded = 0
    required_by_type: dict[str, int] = defaultdict(int)
    double_coded_by_type: dict[str, int] = defaultdict(int)
    comparable_goal_pairs: list[tuple[bool, bool]] = []
    ordinal_goal_units: list[list[int]] = []
    ordinal_candidate_units: list[list[int]] = []
    adjudicated_values = 0
    for sample in required_samples:
        sample_id = sample["sampleId"]
        sample_type = sample["sampleType"]
        required_by_type[sample_type] += 1
        records = records_by_sample.get(sample_id, [])
        independent_records = [item for item in records if item["role"] == "annotator"]
        adjudicators = [item for item in records if item["role"] == "adjudicator"]
        if not independent_records:
            raise AnnotationResolutionError(f"required sample has no independent annotation: {sample_id}")
        if len(adjudicators) > 1:
            raise AnnotationResolutionError(f"sample has multiple adjudicators: {sample_id}")
        if len(independent_records) >= 2:
            double_coded += 1
            double_coded_by_type[sample_type] += 1
            left, right = sorted(independent_records, key=lambda item: item["annotatorId"])[:2]
            for goal_id in sample["expectedLabels"]["goalIds"]:
                left_value = left["_maps"]["goals"][goal_id]["score"]
                right_value = right["_maps"]["goals"][goal_id]["score"]
                if left_value is not None and right_value is not None:
                    comparable_goal_pairs.append((left_value == 2, right_value == 2))
                values = [record["_maps"]["goals"][goal_id]["score"] for record in independent_records]
                values = [value for value in values if value is not None]
                if len(values) >= 2:
                    ordinal_goal_units.append(values)
            for candidate_id in sample["expectedLabels"]["candidateIds"]:
                values = [record["_maps"]["candidates"][candidate_id]["relevance"] for record in independent_records]
                values = [value for value in values if value is not None]
                if len(values) >= 2:
                    ordinal_candidate_units.append(values)
        independent_maps = [item["_maps"] for item in independent_records]
        adjudicator_map = adjudicators[0]["_maps"] if adjudicators else None
        item = {"goalScores": {}, "staleGoalLeaks": {}, "candidateLabels": {}, "conflictLabel": None}
        for goal_id in sample["expectedLabels"]["goalIds"]:
            value, adjudicated = _resolve_scalar(
                independent_maps, adjudicator_map,
                collection="goals", identifier=goal_id, value_key="score", sample_id=sample_id,
            )
            item["goalScores"][goal_id] = value
            adjudicated_values += int(adjudicated)
        for goal_id in sample["expectedLabels"]["staleGoalIds"]:
            value, adjudicated = _resolve_scalar(
                independent_maps, adjudicator_map,
                collection="stale", identifier=goal_id, value_key="leaked", sample_id=sample_id,
            )
            item["staleGoalLeaks"][goal_id] = value
            adjudicated_values += int(adjudicated)
        for candidate_id in sample["expectedLabels"]["candidateIds"]:
            relevance, adjudicated_relevance = _resolve_scalar(
                independent_maps, adjudicator_map,
                collection="candidates", identifier=candidate_id, value_key="relevance", sample_id=sample_id,
            )
            human_best, adjudicated_best = _resolve_scalar(
                independent_maps, adjudicator_map,
                collection="candidates", identifier=candidate_id, value_key="humanBest", sample_id=sample_id,
            )
            item["candidateLabels"][candidate_id] = {"relevance": relevance, "humanBest": human_best}
            adjudicated_values += int(adjudicated_relevance) + int(adjudicated_best)
        conflict_values = [
            (record["conflictLabel"]["groundTruth"], record["conflictLabel"]["systemDecision"])
            for record in independent_records
        ]
        if adjudicators:
            item["conflictLabel"] = deepcopy(adjudicators[0]["conflictLabel"])
            if any(value != conflict_values[0] for value in conflict_values[1:]):
                adjudicated_values += 1
        elif any(value != conflict_values[0] for value in conflict_values[1:]):
            raise AnnotationResolutionError(f"unresolved conflict-label disagreement in {sample_id}")
        else:
            item["conflictLabel"] = {
                "groundTruth": conflict_values[0][0],
                "systemDecision": conflict_values[0][1],
            }
        resolved[sample_id] = item

    human_count = len(required_samples)
    double_rate = double_coded / human_count if human_count else 0
    semantic_count = required_by_type["semantic_round"]
    candidate_count = required_by_type["candidate_batch"]
    semantic_double_rate = double_coded_by_type["semantic_round"] / semantic_count if semantic_count else 0
    candidate_double_rate = double_coded_by_type["candidate_batch"] / candidate_count if candidate_count else 0
    kappa = cohen_kappa(comparable_goal_pairs)
    goal_alpha = krippendorff_alpha_ordinal(ordinal_goal_units)
    candidate_alpha = krippendorff_alpha_ordinal(ordinal_candidate_units)
    candidate_rating_target = sum(
        len(sample["expectedLabels"]["candidateIds"])
        for sample in required_samples
        if sample["expectedLabels"]["candidateIds"]
    )
    blockers = []
    if annotations.get("status") != "formal":
        blockers.append("annotation set is not marked formal")
    if human_count == 0:
        blockers.append("packet contains no human-judgment samples")
    if double_rate < 0.2:
        blockers.append("fewer than 20% of required samples are double-coded")
    if semantic_count and semantic_double_rate < 0.2:
        blockers.append("fewer than 20% of semantic samples are double-coded")
    if candidate_count and candidate_double_rate < 0.2:
        blockers.append("fewer than 20% of candidate batches are double-coded")
    if len(comparable_goal_pairs) < 20:
        blockers.append("fewer than 20 comparable double-coded goal labels")
    if kappa is None or kappa < 0.7:
        blockers.append("binary complete-goal Cohen kappa is missing or below 0.70")
    if goal_alpha is None or goal_alpha < 0.67:
        blockers.append("ordinal goal Krippendorff alpha is missing or below 0.67")
    if candidate_rating_target:
        if len(ordinal_candidate_units) < 20:
            blockers.append("fewer than 20 double-coded candidate relevance labels")
        if candidate_alpha is None or candidate_alpha < 0.67:
            blockers.append("candidate relevance Krippendorff alpha is missing or below 0.67")
    stats = {
        "formalReady": not blockers,
        "blockers": blockers,
        "requiredSampleCount": human_count,
        "resolvedSampleCount": len(resolved),
        "doubleCodedSamples": double_coded,
        "doubleCodedRate": double_rate,
        "semanticSampleCount": semantic_count,
        "doubleCodedSemanticSamples": double_coded_by_type["semantic_round"],
        "doubleCodedSemanticRate": semantic_double_rate,
        "candidateBatchCount": candidate_count,
        "doubleCodedCandidateBatches": double_coded_by_type["candidate_batch"],
        "doubleCodedCandidateRate": candidate_double_rate,
        "comparableGoalLabels": len(comparable_goal_pairs),
        "binaryCompleteGoalKappa": kappa,
        "ordinalGoalKrippendorffAlpha": goal_alpha,
        "doubleCodedCandidateRatings": len(ordinal_candidate_units),
        "candidateRelevanceKrippendorffAlpha": candidate_alpha,
        "adjudicatedValueCount": adjudicated_values,
        "annotatorCount": len({record["annotatorId"] for record in annotations["records"] if record["role"] == "annotator"}),
        "adjudicatorCount": len({record["annotatorId"] for record in annotations["records"] if record["role"] == "adjudicator"}),
    }
    return resolved, stats
