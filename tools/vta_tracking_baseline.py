"""Read-only geometry-tracker baseline against a completed VTA analysis cache.

This diagnostic never persists the derived revisions or accepts identity labels
as tracker input. It is intentionally limited to existing user-authored keys.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate(core_root: Path, companion: Path, character_ids: tuple[str, ...],
             edited_frame: int) -> dict:
    sys.path.insert(0, str(core_root.resolve()))
    from vta_core.analysis_cache import load_analysis_cache
    from vta_core.bounded_tracking import (
        MAX_AREA_FOLD, MAX_CENTER_DISPLACEMENT, MIN_IOU, MIN_MATCH_SCORE,
        MIN_RUNNER_MARGIN, POLICY_REVISION, derive_bounded_tracking,
    )
    from vta_core.project_persistence import load_project_state
    from vta_core.state_model import ProjectState

    project_file = companion / "project_state.json"
    project = load_project_state(companion)
    raw = project.to_dict()
    if len(raw["sources"]) != 1:
        raise ValueError("Baseline requires one unambiguous project source")
    source_id = raw["sources"][0]["source_id"]
    revision = raw["active_analysis_revision_by_source"][source_id]
    cache_path = companion / "analysis" / f"{revision}.json"
    cache = load_analysis_cache(companion, revision)
    if set(character_ids) - {item.character_id for item in project.characters}:
        raise ValueError("Requested Character ID is not in this project")
    snapshots = {}
    for character_id in character_ids:
        # Clone in memory: each Character gets an untouched baseline, without
        # cross-order effects from prior derived revisions.
        clone = ProjectState.from_dict(raw)
        update = derive_bounded_tracking(clone, cache, character_id, source_id,
                                         edited_frame)
        tracking = next(item for item in clone.tracking_revisions
                        if item.revision_id == update.revision_id)
        records = [
            {"frame": record.frame, "state": record.state.value,
             "reason": record.reason,
             "observation_id": (record.candidate_evidence.observation_id
                                if record.candidate_evidence else None),
             "box_xyxy": (list(record.candidate_evidence.box_xyxy)
                          if record.candidate_evidence else None),
             "score": record.match_score}
            for record in tracking.records
        ]
        snapshots[character_id] = {
            "scope": update.scope.to_dict(),
            "matched": update.matched, "unresolved": update.unresolved,
            "conflicts": update.conflicts, "records": records,
        }
    return {
        "schema": "blendcap-vta-geometry-baseline-diagnostic", "version": 1,
        "project_sha256": file_hash(project_file),
        "analysis_cache_sha256": file_hash(cache_path),
        "source_id": source_id, "analysis_revision_id": revision,
        "analyzed_frames": len(cache.analyzed_frames),
        "policy_revision": POLICY_REVISION,
        "thresholds_unchanged": {
            "max_center_displacement": MAX_CENTER_DISPLACEMENT,
            "max_area_fold": MAX_AREA_FOLD, "min_iou": MIN_IOU,
            "min_match_score": MIN_MATCH_SCORE,
            "min_runner_margin": MIN_RUNNER_MARGIN,
        },
        "edited_frame": edited_frame, "characters": snapshots,
        "ground_truth_used_as_tracker_input": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-root", type=Path, required=True)
    parser.add_argument("--companion", type=Path, required=True)
    parser.add_argument("--character", action="append", required=True)
    parser.add_argument("--edited-frame", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Output exists; a baseline diagnostic must not be overwritten")
    report = evaluate(args.core_root, args.companion, tuple(args.character),
                      args.edited_frame)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({
        "output": str(output), "analyzed_frames": report["analyzed_frames"],
        "characters": {key: {name: value[name] for name in ("matched", "unresolved", "conflicts")}
                       for key, value in report["characters"].items()},
    }, sort_keys=True))


if __name__ == "__main__":
    main()
