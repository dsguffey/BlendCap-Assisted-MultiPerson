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
KINDS = {"candidate", "occluded", "no-valid-candidate", "not-visible", "skip"}


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


def prepare(companion: Path, characters: tuple[str, ...], visible_frames: tuple[int, ...],
            blender_offset: int) -> dict:
    project_path, cache_path, project, cache, source = source_files(companion)
    if len(characters) != 2 or len(set(characters)) != 2:
        raise ValueError("Exactly two distinct Character IDs are required")
    known = {item["character_id"] for item in project["characters"]}
    if not set(characters) <= known:
        raise ValueError("Unknown Character ID")
    if not visible_frames or sorted(set(visible_frames)) != list(visible_frames):
        raise ValueError("Visible Blender frames must be unique and ascending")
    by_frame = {entry["frame"]: entry for entry in cache["frames"]}
    rows = []
    for visible in visible_frames:
        source_frame = visible - blender_offset
        if source_frame not in by_frame or by_frame[source_frame]["state"] != "analyzed":
            raise ValueError(f"Blender frame {visible} has no analyzed source frame")
        entry = by_frame[source_frame]
        rows.append({
            "blender_frame": visible, "source_frame": source_frame,
            "timestamp_seconds": entry["timestamp_seconds"],
            "candidates": [{"position": index, "observation_id": candidate["observation_id"],
                            "box_xyxy": candidate["box_xyxy"]}
                           for index, candidate in enumerate(entry["candidates"], 1)],
            "labels": {character: {"kind": None, "candidate_position": None}
                       for character in characters},
        })
    return {
        "schema": SCHEMA, "version": 1, "purpose": "scoring-only-not-authoritative",
        "ground_truth_used_as_tracker_input": False,
        "project_sha256": sha256(project_path), "analysis_cache_sha256": sha256(cache_path),
        "source_id": source["source_id"],
        "analysis_revision_id": cache["revision_id"],
        "blender_frame_offset": blender_offset,
        "characters": list(characters), "frames": rows,
    }


def validate_labels(labels: dict, template: dict) -> None:
    if labels.get("schema") != SCHEMA or labels.get("version") != 1:
        raise ValueError("Invalid semantic label schema/version")
    for name in ("purpose", "project_sha256", "analysis_cache_sha256", "source_id",
                 "analysis_revision_id", "blender_frame_offset", "characters"):
        if labels.get(name) != template[name]:
            raise ValueError(f"Semantic labels have changed or stale {name}")
    if labels.get("ground_truth_used_as_tracker_input") is not False:
        raise ValueError("Scoring labels cannot be tracker inputs")
    actual = labels.get("frames")
    if not isinstance(actual, list) or len(actual) != len(template["frames"]):
        raise ValueError("Semantic label frame set changed")
    for row, expected in zip(actual, template["frames"]):
        for name in ("blender_frame", "source_frame", "timestamp_seconds", "candidates"):
            if row.get(name) != expected[name]:
                raise ValueError(f"Semantic label {name} differs from analyzed cache")
        decisions = row.get("labels")
        if not isinstance(decisions, dict) or set(decisions) != set(template["characters"]):
            raise ValueError("Semantic label Character set differs")
        for character, decision in decisions.items():
            if not isinstance(decision, dict) or set(decision) != {"kind", "candidate_position"}:
                raise ValueError(f"Malformed decision for {character}")
            kind, position = decision["kind"], decision["candidate_position"]
            if kind not in KINDS:
                raise ValueError(f"Unfilled or invalid label for {character} at frame {row['blender_frame']}")
            if kind == "candidate":
                if type(position) is not int or not 1 <= position <= len(row["candidates"]):
                    raise ValueError("Candidate position is outside this frame's candidate list")
            elif position is not None:
                raise ValueError("Non-candidate label cannot carry a candidate position")


def score(companion: Path, baseline_path: Path, labels_path: Path) -> dict:
    labels = read_json(labels_path)
    frames = labels.get("frames")
    if not isinstance(frames, list):
        raise ValueError("Semantic label frames must be a list")
    visible = tuple(row["blender_frame"] for row in frames)
    characters = tuple(labels.get("characters", []))
    template = prepare(companion, characters, visible, labels.get("blender_frame_offset"))
    validate_labels(labels, template)
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
        "correct", "unresolved-with-candidate", "semantic-swap", "wrong-candidate",
        "correct-conservative-unresolved", "false-match", "not-covered", "skipped",
        "candidate-conflict", "boundary-violation", "reacquisition-success")}
        for character in characters}
    coverage = {character: {"candidate_label_cases": 0,
                            "covered_candidate_cases": 0,
                            "correct_candidate_cases": 0}
                for character in characters}
    last_non_candidate = {character: False for character in characters}
    for row in frames:
        frame = row["source_frame"]
        truths = {character: (row["candidates"][row["labels"][character]["candidate_position"] - 1]["observation_id"]
                              if row["labels"][character]["kind"] == "candidate" else None)
                  for character in characters}
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
            other_truths = {value for other, value in truths.items()
                            if other != character and value is not None}
            if label["kind"] == "skip":
                outcome = "skipped"
            elif record is None:
                outcome = "not-covered"
            elif truth is not None and predicted == truth:
                outcome = "correct"
            elif truth is not None and predicted is None:
                outcome = "unresolved-with-candidate"
            elif truth is not None and predicted in other_truths:
                outcome = "semantic-swap"
            elif truth is not None:
                outcome = "wrong-candidate"
            elif predicted is None:
                outcome = "correct-conservative-unresolved"
            else:
                outcome = "false-match"
            totals[character][outcome] += 1
            if label["kind"] == "candidate":
                coverage[character]["candidate_label_cases"] += 1
                if record is not None:
                    coverage[character]["covered_candidate_cases"] += 1
                if outcome == "correct":
                    coverage[character]["correct_candidate_cases"] += 1
            if record is not None and not baseline["characters"][character]["scope"]["start_frame"] <= frame <= baseline["characters"][character]["scope"]["end_frame"]:
                totals[character]["boundary-violation"] += 1
            if same_prediction:
                totals[character]["candidate-conflict"] += 1
            if outcome == "correct" and last_non_candidate[character]:
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
                        "characters": per_character, "candidate_conflict": bool(same_prediction)})
    return {
        "schema": "blendcap-vta-semantic-evaluation-diagnostic", "version": 1,
        "purpose": "scoring-only-not-authoritative",
        "ground_truth_used_as_tracker_input": False,
        "project_sha256": template["project_sha256"],
        "analysis_cache_sha256": template["analysis_cache_sha256"],
        "baseline_sha256": sha256(baseline_path), "labels_sha256": sha256(labels_path),
        "characters": list(characters), "frames": results, "summary": totals,
        "coverage_vs_correctness": coverage,
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
            result = prepare(args.companion, tuple(args.character), frames, args.blender_offset)
        else:
            result = score(args.companion, args.baseline, args.labels)
        write_new(args.output, result)
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "frames": len(result["frames"]),
                      "summary": result.get("summary")}, sort_keys=True))


if __name__ == "__main__":
    main()
