"""Prepare scoring-only VTA labels and compare them with a frozen geometry baseline.

This tool never imports the tracker, mutates ProjectState, or promotes labels to
review keys. Candidate positions are frame-local, one-based UI positions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SCHEMA = "blendcap-vta-semantic-labels"
KINDS = {"candidate", "accepted-set", "occluded", "no-valid-candidate", "not-visible", "skip"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return result


def source_files(companion: Path) -> tuple[Path, Path, dict, dict, dict]:
    project_path = companion / "project_state.json"
    project = read_json(project_path)
    sources = project.get("sources")
    if not isinstance(sources, list) or len(sources) != 1:
        raise ValueError("Expected exactly one project source")
    source = sources[0]
    source_id = source["source_id"]
    revision = project["active_analysis_revision_by_source"][source_id]
    cache_path = companion / "analysis" / f"{revision}.json"
    cache = read_json(cache_path)
    if cache.get("source", {}).get("source_id") != source_id:
        raise ValueError("Analysis cache does not belong to the project source")
    return project_path, cache_path, project, cache, source


def resolve_anchor_character_ids(project: dict, cache: dict, source_frame: int,
                                 positions_by_identity: dict[str, int]) -> dict[str, str]:
    """Bind human names to stable Character IDs through authoritative keys."""
    frame = next((row for row in cache["frames"] if row["frame"] == source_frame), None)
    if frame is None:
        raise ValueError("Identity-mapping anchor frame is not analyzed")
    result = {}
    for identity, position in positions_by_identity.items():
        if not isinstance(identity, str) or not identity or type(position) is not int:
            raise ValueError("Identity mapping requires names and one-based candidate positions")
        if not 1 <= position <= len(frame["candidates"]):
            raise ValueError("Identity-mapping candidate is outside the anchor frame")
        observation = frame["candidates"][position - 1]["observation_id"]
        owners = {key["character_id"] for key in project["identity_keyframes"]
                  if key["frame"] == source_frame and
                  (key.get("candidate_evidence") or {}).get("observation_id") == observation}
        if len(owners) != 1:
            raise ValueError(f"Candidate {position} lacks one authoritative Character anchor")
        result[identity] = owners.pop()
    if len(set(result.values())) != len(result):
        raise ValueError("Semantic identities map to the same Character")
    return result


def prepare(companion: Path, characters: tuple[str, ...], visible_frames: tuple[int, ...],
            blender_offset: int, *, version: int = 1,
            excluded_blender_frames: tuple[int, ...] = ()) -> dict:
    project_path, cache_path, project, cache, source = source_files(companion)
    if len(characters) != 2 or len(set(characters)) != 2:
        raise ValueError("Exactly two distinct Character IDs are required")
    known = {item["character_id"] for item in project["characters"]}
    if not set(characters) <= known:
        raise ValueError("Unknown Character ID")
    if not visible_frames or sorted(set(visible_frames)) != list(visible_frames):
        raise ValueError("Visible Blender frames must be unique and ascending")
    if version not in (1, 2):
        raise ValueError("Unsupported semantic label version")
    if version == 1 and excluded_blender_frames:
        raise ValueError("Anchor exclusions require semantic label version 2")
    if len(set(excluded_blender_frames)) != len(excluded_blender_frames) or not set(
        excluded_blender_frames
    ) <= set(visible_frames):
        raise ValueError("Excluded Blender frames must be unique selected frames")
    by_frame = {entry["frame"]: entry for entry in cache["frames"]}
    rows = []
    for visible in visible_frames:
        source_frame = visible - blender_offset
        if source_frame not in by_frame or by_frame[source_frame]["state"] != "analyzed":
            raise ValueError(f"Blender frame {visible} has no analyzed source frame")
        entry = by_frame[source_frame]
        row = {
            "blender_frame": visible, "source_frame": source_frame,
            "timestamp_seconds": entry["timestamp_seconds"],
            "candidates": [{"position": index, "observation_id": candidate["observation_id"],
                            "box_xyxy": candidate["box_xyxy"]}
                           for index, candidate in enumerate(entry["candidates"], 1)],
            "labels": {character: {"kind": None, "candidate_position": None}
                       for character in characters},
        }
        if version == 2:
            row["note"] = ""
            row["labels"] = {
                character: {"kind": None, "candidate_position": None,
                            "candidate_positions": None} for character in characters
            }
        rows.append(row)
    document = {
        "schema": SCHEMA, "version": version, "purpose": "scoring-only-not-authoritative",
        "ground_truth_used_as_tracker_input": False,
        "project_sha256": sha256(project_path), "analysis_cache_sha256": sha256(cache_path),
        "source_id": source["source_id"],
        "analysis_revision_id": cache["revision_id"],
        "blender_frame_offset": blender_offset,
        "characters": list(characters), "frames": rows,
    }
    if version == 2:
        document["excluded_from_independent_score_blender_frames"] = list(excluded_blender_frames)
        document["semantic_identity_map"] = {}
    return document


def validate_labels(labels: dict, template: dict) -> None:
    if labels.get("schema") != SCHEMA or labels.get("version") != template["version"]:
        raise ValueError("Invalid semantic label schema/version")
    for name in ("purpose", "project_sha256", "analysis_cache_sha256", "source_id",
                 "analysis_revision_id", "blender_frame_offset", "characters"):
        if labels.get(name) != template[name]:
            raise ValueError(f"Semantic labels have changed or stale {name}")
    if labels.get("ground_truth_used_as_tracker_input") is not False:
        raise ValueError("Scoring labels cannot be tracker inputs")
    version = template["version"]
    if version == 2:
        if labels.get("excluded_from_independent_score_blender_frames") != template[
            "excluded_from_independent_score_blender_frames"
        ]:
            raise ValueError("Semantic anchor-exclusion frame set changed")
        identity_map = labels.get("semantic_identity_map")
        if (not isinstance(identity_map, dict) or
                any(not isinstance(name, str) or not name or
                    not isinstance(character, str) for name, character in identity_map.items()) or
                set(identity_map.values()) - set(template["characters"]) or
                len(set(identity_map.values())) != len(identity_map)):
            raise ValueError("Invalid stable Character identity mapping")
    actual = labels.get("frames")
    if not isinstance(actual, list) or len(actual) != len(template["frames"]):
        raise ValueError("Semantic label frame set changed")
    for row, expected in zip(actual, template["frames"]):
        for name in ("blender_frame", "source_frame", "timestamp_seconds", "candidates"):
            if row.get(name) != expected[name]:
                raise ValueError(f"Semantic label {name} differs from analyzed cache")
        if version == 2 and not isinstance(row.get("note"), str):
            raise ValueError("Semantic frame note must be text")
        decisions = row.get("labels")
        if not isinstance(decisions, dict) or set(decisions) != set(template["characters"]):
            raise ValueError("Semantic label Character set differs")
        for character, decision in decisions.items():
            expected_fields = ({"kind", "candidate_position"} if version == 1 else
                               {"kind", "candidate_position", "candidate_positions"})
            if not isinstance(decision, dict) or set(decision) != expected_fields:
                raise ValueError(f"Malformed decision for {character}")
            kind, position = decision["kind"], decision["candidate_position"]
            if kind not in KINDS or (kind == "accepted-set" and version != 2):
                raise ValueError(f"Unfilled or invalid label for {character} at frame {row['blender_frame']}")
            positions = decision.get("candidate_positions")
            if kind == "candidate":
                if type(position) is not int or not 1 <= position <= len(row["candidates"]):
                    raise ValueError("Candidate position is outside this frame's candidate list")
                if positions is not None:
                    raise ValueError("Single-candidate truth cannot carry an accepted set")
            elif kind == "accepted-set":
                if (position is not None or not isinstance(positions, list) or
                        len(positions) < 2 or any(type(item) is not int or not
                        1 <= item <= len(row["candidates"]) for item in positions) or
                        sorted(set(positions)) != positions):
                    raise ValueError("Accepted-set truth requires distinct ascending candidates")
            elif position is not None or positions is not None:
                raise ValueError("Non-candidate label cannot carry candidate positions")


def score(companion: Path, baseline_path: Path, labels_path: Path) -> dict:
    labels = read_json(labels_path)
    frames = labels.get("frames")
    if not isinstance(frames, list):
        raise ValueError("Semantic label frames must be a list")
    visible = tuple(row["blender_frame"] for row in frames)
    characters = tuple(labels.get("characters", []))
    version = labels.get("version")
    exclusions = labels.get("excluded_from_independent_score_blender_frames", [])
    if version == 2 and (not isinstance(exclusions, list) or
                         any(type(frame) is not int for frame in exclusions)):
        raise ValueError("Anchor-exclusion frame set must be integer Blender frames")
    template = prepare(companion, characters, visible, labels.get("blender_frame_offset"),
                       version=version, excluded_blender_frames=tuple(exclusions))
    validate_labels(labels, template)
    if version == 2 and exclusions:
        _project_path, _cache_path, project, cache, _source = source_files(companion)
        identity_map = labels["semantic_identity_map"]
        for excluded in exclusions:
            anchor_row = next(row for row in frames if row["blender_frame"] == excluded)
            if any(anchor_row["labels"][character]["kind"] != "candidate"
                   for character in characters):
                raise ValueError("Excluded anchor frame must retain candidate labels")
            positions = {name: anchor_row["labels"][character]["candidate_position"]
                         for name, character in identity_map.items()}
            if (set(positions.values()) != {
                    anchor_row["labels"][character]["candidate_position"]
                    for character in characters
            } or resolve_anchor_character_ids(project, cache,
                  anchor_row["source_frame"], positions) != identity_map):
                raise ValueError("Excluded frame identity map does not match authoritative anchors")
    baseline = read_json(baseline_path)
    if (baseline.get("schema") != "blendcap-vta-geometry-baseline-diagnostic"
            or baseline.get("version") != 1):
        raise ValueError("Invalid geometry baseline schema/version")
    for name in ("project_sha256", "analysis_cache_sha256", "source_id"):
        if baseline.get(name) != template[name]:
            raise ValueError(f"Geometry baseline has stale {name}")
    if not set(characters) <= set(baseline.get("characters", {})):
        raise ValueError("Baseline lacks a labeled Character")
    records = {character: {record["frame"]: record
                           for record in baseline["characters"][character]["records"]}
               for character in characters}
    results = []
    totals = {character: {name: 0 for name in (
        "correct", "correct-accepted-set", "unresolved-with-candidate", "semantic-swap", "wrong-candidate",
        "correct-conservative-unresolved", "false-match", "not-covered", "skipped",
        "excluded-authoritative-anchor", "candidate-conflict", "boundary-violation",
        "reacquisition-success")}
        for character in characters}
    coverage = {character: {"candidate_label_cases": 0,
                            "covered_candidate_cases": 0,
                            "correct_candidate_cases": 0}
                for character in characters}
    last_non_candidate = {character: False for character in characters}
    for row in frames:
        frame = row["source_frame"]
        truths = {}
        for character in characters:
            label = row["labels"][character]
            positions = ([label["candidate_position"]] if label["kind"] == "candidate" else
                         label["candidate_positions"] if label["kind"] == "accepted-set" else [])
            truths[character] = {
                row["candidates"][position - 1]["observation_id"] for position in positions
            }
        predictions = {character: records[character].get(frame) for character in characters}
        same_prediction = (all(predictions.values()) and
                           predictions[characters[0]].get("observation_id") is not None and
                           predictions[characters[0]].get("observation_id") ==
                           predictions[characters[1]].get("observation_id"))
        per_character = {}
        for character in characters:
            label = row["labels"][character]
            record = predictions[character]
            predicted = record.get("observation_id") if record else None
            truth = truths[character]
            other_truths = set().union(*(value for other, value in truths.items()
                                         if other != character))
            if row["blender_frame"] in exclusions:
                outcome = "excluded-authoritative-anchor"
            elif label["kind"] == "skip":
                outcome = "skipped"
            elif record is None:
                outcome = "not-covered"
            elif truth and predicted in truth:
                outcome = "correct-accepted-set" if label["kind"] == "accepted-set" else "correct"
            elif truth and predicted is None:
                outcome = "unresolved-with-candidate"
            elif truth and predicted in other_truths:
                outcome = "semantic-swap"
            elif truth:
                outcome = "wrong-candidate"
            elif predicted is None:
                outcome = "correct-conservative-unresolved"
            else:
                outcome = "false-match"
            totals[character][outcome] += 1
            if label["kind"] in {"candidate", "accepted-set"} and row["blender_frame"] not in exclusions:
                coverage[character]["candidate_label_cases"] += 1
                if record is not None:
                    coverage[character]["covered_candidate_cases"] += 1
                if outcome in {"correct", "correct-accepted-set"}:
                    coverage[character]["correct_candidate_cases"] += 1
            if record is not None and not baseline["characters"][character]["scope"]["start_frame"] <= frame <= baseline["characters"][character]["scope"]["end_frame"]:
                totals[character]["boundary-violation"] += 1
            if same_prediction and row["blender_frame"] not in exclusions:
                totals[character]["candidate-conflict"] += 1
            if outcome in {"correct", "correct-accepted-set"} and last_non_candidate[character]:
                totals[character]["reacquisition-success"] += 1
            last_non_candidate[character] = label["kind"] in {
                "occluded", "no-valid-candidate", "not-visible",
            }
            per_character[character] = {
                "label": label, "outcome": outcome,
                "predicted_observation_id": predicted,
                "predicted_state": record["state"] if record else "outside-derived-scope",
            }
        results.append({"blender_frame": row["blender_frame"], "source_frame": frame,
                        "independently_scored": row["blender_frame"] not in exclusions,
                        "note": row.get("note", ""), "characters": per_character,
                        "candidate_conflict": bool(same_prediction)})
    correct_outcomes = {"correct", "correct-accepted-set", "correct-conservative-unresolved"}
    evaluated = [row for row in results if row["independently_scored"] and
                 all(row["characters"][character]["outcome"] != "skipped"
                     for character in characters)]
    joint_summary = {
        "independently_scored_frames": len(evaluated),
        "all_characters_correct": sum(all(row["characters"][character]["outcome"] in correct_outcomes
                                      for character in characters) for row in evaluated),
        "candidate_conflicts": sum(row["candidate_conflict"] for row in evaluated),
    }
    per_character_summary = {}
    for character in characters:
        case_count = len(evaluated)
        correct_count = sum(row["characters"][character]["outcome"] in correct_outcomes
                            for row in evaluated)
        resolved_count = sum(row["characters"][character]["predicted_observation_id"] is not None
                             for row in evaluated)
        resolved_correct = sum(row["characters"][character]["outcome"] in
                               {"correct", "correct-accepted-set"} for row in evaluated)
        per_character_summary[character] = {
            "independently_scored_cases": case_count,
            "correct_cases": correct_count,
            "resolved_cases": resolved_count,
            "correct_among_resolved": resolved_correct,
        }
    return {
        "schema": "blendcap-vta-semantic-evaluation-diagnostic", "version": 1,
        "purpose": "scoring-only-not-authoritative",
        "ground_truth_used_as_tracker_input": False,
        "project_sha256": template["project_sha256"],
        "analysis_cache_sha256": template["analysis_cache_sha256"],
        "baseline_sha256": sha256(baseline_path), "labels_sha256": sha256(labels_path),
        "characters": list(characters), "frames": results, "summary": totals,
        "coverage_vs_correctness": coverage,
        "joint_summary": joint_summary, "independent_summary": per_character_summary,
        "semantic_identity_map": labels.get("semantic_identity_map", {}),
        "warning": "Correlated sampled frames are not independent statistical samples.",
    }


def write_new(output: Path, document: dict) -> None:
    if output.exists():
        raise ValueError("Output already exists; refusing to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="Write a blank, scoring-only label sheet")
    prep.add_argument("--companion", type=Path, required=True)
    prep.add_argument("--character", action="append", required=True)
    prep.add_argument("--blender-offset", type=int, default=1)
    prep.add_argument("--frames", required=True, help="Comma-separated Blender-visible frames")
    prep.add_argument("--version", type=int, choices=(1, 2), default=1)
    prep.add_argument("--exclude-anchor-frame", type=int, action="append", default=[])
    evaluate = sub.add_parser("score", help="Score completed labels against a frozen baseline")
    evaluate.add_argument("--companion", type=Path, required=True)
    evaluate.add_argument("--baseline", type=Path, required=True)
    evaluate.add_argument("--labels", type=Path, required=True)
    for command in (prep, evaluate):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            frames = tuple(int(item.strip()) for item in args.frames.split(","))
            result = prepare(
                args.companion, tuple(args.character), frames, args.blender_offset,
                version=args.version,
                excluded_blender_frames=tuple(args.exclude_anchor_frame),
            )
        else:
            result = score(args.companion, args.baseline, args.labels)
        write_new(args.output, result)
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "frames": len(result["frames"]),
                      "summary": result.get("summary")}, sort_keys=True))


if __name__ == "__main__":
    main()
