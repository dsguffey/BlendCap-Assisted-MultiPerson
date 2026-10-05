"""Offline signal benchmark. No persistence, production matcher edits, or downloads.

Prediction APIs never accept semantic labels. Scoring is a separate pass. All
rules are experimental, fixed comparisons, NOT a calibrated tracking policy.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import math
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.vta_semantic_evaluation import prepare, validate_labels, sha256

CHARACTERS = ('wrestler-a', 'wrestler-b')
METHODS = ('geometry', 'velocity', 'flow', 'color-rank', 'color-veto',
           'color-exception', 'osnet-rank', 'osnet-veto', 'osnet-exception',
           'color-osnet-exception')
SPEC = {
    'scope': 'offline-research-only-not-production',
    'labels_enter_predictions': False,
    'coordinate_contract': 'source-pixels-xyxy-lower-left',
    'motion': 'two-box constant center/log-size velocity; reset at each anchor',
    'color': '2x2 spatial HSV histogram; 12x4x4 bins/cell; L2 normalize',
    'crops': {'whole': 'exact box enclosing integer pixels',
              'inner': 'central 60 percent width and 80 percent height'},
    'appearance_consensus': 'every crop and available bounding-anchor reference agrees; exact ties abstain',
    'rank': 'appearance chooses; unchanged geometry gates and selected minimum score remain',
    'veto': 'unchanged geometry match is rejected if appearance consensus disagrees/missing',
    'exception': 'only area-fold gate can be bypassed by unanimous appearance; IoU, center and minimum score remain',
    'flow': {'scale': 0.5, 'points': 40, 'quality': 0.01, 'minimum_distance': 5,
             'window': [21, 21], 'pyramid_levels': 3,
             'forward_backward_max_pixels': 1.0,
             'rule': 'median valid sparse LK translation; upstream gates on translated box'},
    'no_reacquisition_after_direction_stop': True,
    'human_anchor_plan': 'use only previously authorized frame-13 paired and frame-91 Yellow keys',
    'no_threshold_search': True,
}


def write_new(path, data):
    """Atomic no-clobber publication within ignored development output root."""
    path = Path(path).resolve()
    allowed = Path(__file__).resolve().parents[1] / 'checkpoints' / 'dev-preview'
    if not path.is_relative_to(allowed.resolve()):
        raise ValueError('Output must be under checkpoints/dev-preview')
    if path.exists():
        raise ValueError(f'Output exists: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.signal-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(data, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write('\n')
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def predict_box(history):
    if len(history) < 2:
        return tuple(history[-1][1])
    (fa, a), (fb, b) = history[-2:]
    step = abs(fb - fa)
    if step != 1:
        raise ValueError('Motion history may not bridge a missing frame')
    centers = [(box[0] / 2 + box[2] / 2, box[1] / 2 + box[3] / 2)
               for box in (a, b)]
    sizes = [(box[2] - box[0], box[3] - box[1]) for box in (a, b)]
    cx, cy = (2 * centers[1][i] - centers[0][i] for i in (0, 1))
    width, height = (sizes[1][i] ** 2 / sizes[0][i] for i in (0, 1))
    return (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)


def top_id(scores):
    """No ordering-based confidence: tied maximums are unavailable."""
    if not scores or any(not math.isfinite(v) for v in scores.values()):
        return None
    best = max(scores.values())
    ids = [key for key, value in scores.items() if value == best]
    return ids[0] if len(ids) == 1 else None


def cosine(a, b):
    # Stored normalized descriptors; accumulation is deterministic float64.
    return float(a @ b)


def appearance_choice(candidates, references, features, family):
    choices = []
    for ref in references:
        for view in ('whole', 'inner'):
            reference = features[family].get((ref.frame, ref.candidate_evidence.observation_id, view))
            if reference is None:
                return None
            scores = {}
            for candidate in candidates:
                descriptor = features[family].get((features['current_frame'], candidate.observation_id, view))
                if descriptor is None:
                    return None
                scores[candidate.observation_id] = cosine(reference, descriptor)
            choices.append(top_id(scores))
    if not choices or None in choices or len(set(choices)) != 1:
        return None
    return choices[0]


def gated_score(bt, previous, current, *, area_exception=False):
    iou, distance, fold = bt._metrics(previous, current)
    if (iou < bt.MIN_IOU or distance > bt.MAX_CENTER_DISPLACEMENT or
            (not area_exception and fold > bt.MAX_AREA_FOLD)):
        return None
    score = .55 * iou + .30 * (1 - distance / bt.MAX_CENTER_DISPLACEMENT) + .15 / fold
    return score if score >= bt.MIN_MATCH_SCORE else None


def direction(bt, cache, frames, anchor, references, method, features):
    """Walk strictly adjacent frames and stop permanently at first abstention."""
    history = [(anchor.frame, anchor.candidate_evidence.box_xyxy)]
    previous_id = anchor.candidate_evidence.observation_id
    found, trace = {}, []
    for frame in frames:
        if abs(frame - history[-1][0]) != 1:
            raise ValueError('Directional walk must be adjacent to anchor/history')
        row = cache.frame_result(frame)
        if row is None or not row.candidates:
            trace.append({'frame': frame, 'reason': 'missing-or-empty-candidates'})
            break
        previous = history[-1][1]
        candidate, score = None, None
        if method == 'geometry':
            match = bt._match(previous, row.candidates)
            if match:
                candidate, score = match
        elif method in ('velocity', 'flow'):
            predicted = predict_box(history) if method == 'velocity' else None
            if method == 'flow':
                shift = features['flow'].get((history[-1][0], previous_id, frame))
                if shift is not None:
                    predicted = tuple(v + shift[i % 2] for i, v in enumerate(previous))
            match = bt._match(predicted, row.candidates) if predicted is not None else None
            if match:
                candidate, score = match
        else:
            families = ('color', 'osnet') if method.startswith('color-osnet') else (method.split('-')[0],)
            frame_features = dict(features, current_frame=frame)
            choices = [appearance_choice(row.candidates, references, frame_features, family)
                       for family in families]
            choice = choices[0] if None not in choices and len(set(choices)) == 1 else None
            if method.endswith('veto'):
                match = bt._match(previous, row.candidates)
                if match and match[0].observation_id == choice:
                    candidate, score = match
            elif choice is not None:
                candidate = next(item for item in row.candidates if item.observation_id == choice)
                score = gated_score(bt, previous, candidate.box_xyxy,
                                    area_exception=method.endswith('exception'))
                if score is None:
                    candidate = None
        trace.append({'frame': frame, 'observation_id': candidate.observation_id if candidate else None,
                      'score': score, 'reason': 'match' if candidate else 'abstain',
                      'previous_box': list(previous)})
        if candidate is None:
            break
        found[frame] = (candidate, score)
        history.append((frame, candidate.box_xyxy))
        previous_id = candidate.observation_id
    return found, trace


def run_predictions(bt, project, cache, edits, method, features):
    """Read-only scopes, hard keys and occupancy; no labels or persistence."""
    source = cache.source.source_id
    results = {}
    keys = {(key.character_id, key.frame): key for key in project.identity_keyframes
            if key.source_id == source}
    for character in CHARACTERS:
        records, traces, scopes = {}, [], []
        for edit in edits[character]:
            effective_edit = edit + 1 if (character, edit) in keys else edit
            scope = project.compute_tracking_scope(character, source, effective_edit)
            left = next((key for key in keys.values() if key.keyframe_id == scope.left_boundary.keyframe_id), None)
            right = next((key for key in keys.values() if key.keyframe_id == scope.right_boundary.keyframe_id), None)
            frames = tuple(range(scope.start_frame, scope.end_frame + 1))
            references = [key for key in (left, right) if key and key.candidate_evidence]
            fwd, ftrace = direction(bt, cache, frames, left, references, method, features) if left and left.candidate_evidence else ({}, [])
            bwd, btrace = direction(bt, cache, frames[::-1], right, references, method, features) if right and right.candidate_evidence else ({}, [])
            two = bool(left and left.candidate_evidence and right and right.candidate_evidence)
            traces.append({'scope': scope.to_dict(), 'forward': ftrace, 'backward': btrace,
                           'publication': bool(fwd or bwd or (left and right))})
            # Match upstream failure semantics: a failed single-sided scope is
            # absent, not a fabricated published tracking revision.
            if not fwd and not bwd and not (left and right):
                continue
            scopes.append(scope.to_dict())
            for frame in frames:
                if cache.frame_result(frame) is None:
                    continue
                a, b = fwd.get(frame), bwd.get(frame)
                selected = (a if a and b and a[0].observation_id == b[0].observation_id else None) if two else a or b
                reason = 'geometry-continuity' if method == 'geometry' else 'experimental-' + method
                if not selected:
                    reason = 'directional-disagreement' if a and b else 'single-direction-only' if two and (a or b) else 'no-reliable-match'
                if selected:
                    for (other, at), key in keys.items():
                        if other != character and at == frame and key.candidate_evidence and (
                                key.candidate_evidence.observation_id == selected[0].observation_id or
                                key.candidate_evidence.box_xyxy == selected[0].box_xyxy):
                            selected, reason = None, 'reserved-by-other-character'
                records[frame] = {'frame': frame, 'state': 'visible' if selected else 'unresolved',
                                  'reason': reason,
                                  'observation_id': selected[0].observation_id if selected else None,
                                  'box_xyxy': list(selected[0].box_xyxy) if selected else None,
                                  'score': min(a[1], b[1]) if selected and two else selected[1] if selected else None}
        results[character] = {'records': records, 'scopes': scopes, 'traces': traces}
    # Final simultaneous reservations, independent of processing order. Colliding
    # derived claims are both withheld, rather than selecting an arbitrary owner.
    collisions = []
    for frame in sorted(set().union(*(set(v['records']) for v in results.values()))):
        rows = [results[ch]['records'].get(frame) for ch in CHARACTERS]
        if all(row and row['observation_id'] for row in rows) and (
                rows[0]['observation_id'] == rows[1]['observation_id'] or
                rows[0]['box_xyxy'] == rows[1]['box_xyxy']):
            collisions.append(frame)
            for row in rows:
                row.update(state='unresolved', reason='joint-candidate-conflict', observation_id=None, box_xyxy=None, score=None)
    return {'characters': results, 'withheld_collision_frames': collisions}


def score_predictions(prediction, labels, project):
    """Labels are consulted only here, after complete predictions exist."""
    anchors = {(key.character_id, key.frame) for key in project.identity_keyframes}
    totals = {ch: Counter() for ch in CHARACTERS}
    frames = []
    for row in labels['frames']:
        truths = {}
        for ch, label in row['labels'].items():
            positions = ([label['candidate_position']] if label['kind'] == 'candidate' else
                         label.get('candidate_positions') or [])
            truths[ch] = {row['candidates'][p - 1]['observation_id'] for p in positions}
        outcomes = {}
        for ch in CHARACTERS:
            label = row['labels'][ch]
            record = prediction['characters'][ch]['records'].get(row['source_frame'])
            selected = record['observation_id'] if record else None
            if (ch, row['source_frame']) in anchors:
                outcome = 'excluded-human-anchor'
            elif label['kind'] == 'skip':
                outcome = 'skipped'
            elif record is None:
                outcome = 'not-covered'
            elif truths[ch]:
                outcome = ('correct' if selected in truths[ch] else 'unresolved-valid' if selected is None else
                           'semantic-swap' if selected in set().union(*(v for k, v in truths.items() if k != ch)) else 'wrong-candidate')
            else:
                outcome = 'correct-conservative-unresolved' if selected is None else 'false-match'
            totals[ch][outcome] += 1
            if record and not any(s['start_frame'] <= row['source_frame'] <= s['end_frame'] for s in prediction['characters'][ch]['scopes']):
                totals[ch]['boundary-violation'] += 1
            outcomes[ch] = {'outcome': outcome, 'observation_id': selected,
                            'candidate_position': next((r['position'] for r in row['candidates'] if r['observation_id'] == selected), None)}
        frames.append({'blender_frame': row['blender_frame'], 'source_frame': row['source_frame'], 'characters': outcomes})
    metrics = {}
    for ch in CHARACTERS:
        counts = totals[ch]
        metrics[ch] = {
            'scored_cases': sum(v for k, v in counts.items() if k not in ('excluded-human-anchor', 'skipped', 'boundary-violation')),
            'correct_resolved': counts['correct'],
            'wrong_resolved': counts['semantic-swap'] + counts['wrong-candidate'] + counts['false-match'],
            'unresolved_valid': counts['unresolved-valid'],
            'outside_scope': counts['not-covered'],
            'conservative_unresolved': counts['correct-conservative-unresolved'],
            'scored_post_contact_matches': [r['blender_frame'] for r in frames
                                           if r['blender_frame'] > 81 and r['characters'][ch]['outcome'] == 'correct'],
        }
    return {'summary': {ch: dict(sorted(v.items())) for ch, v in totals.items()},
            'metrics': metrics, 'frames': frames}


def crop_bounds(box, width, height, view):
    x1, y1, x2, y2 = box
    if view == 'inner':
        dx, dy = .2 * (x2 - x1), .1 * (y2 - y1)
        x1, x2, y1, y2 = x1 + dx, x2 - dx, y1 + dy, y2 - dy
    return (max(0, math.floor(x1)), max(0, math.floor(height - y2)),
            min(width, math.ceil(x2)), min(height, math.ceil(height - y1)))


def independent_appearance(cache, state, features):
    """Read-only measurements over whole analyzed clip, not tracking decisions."""
    references = [key for key in state.identity_keyframes
                  if key.character_id in CHARACTERS and key.candidate_evidence]
    result = []
    for frame in cache.analyzed_frames:
        row = cache.frame_result(frame)
        for family in ('color', 'osnet'):
            for view in ('whole', 'inner'):
                for ref in references:
                    anchor = features[family][(ref.frame, ref.candidate_evidence.observation_id, view)]
                    scores = {candidate.observation_id: cosine(anchor, features[family][(frame, candidate.observation_id, view)])
                              for candidate in row.candidates}
                    result.append({'source_frame': frame, 'family': family, 'crop': view,
                                   'character': ref.character_id, 'anchor_frame': ref.frame,
                                   'scores': scores, 'top_observation_id': top_id(scores),
                                   'self_reference': frame == ref.frame})
    return result


def score_appearance(measurements, labels, state):
    """Independent rankings are diagnostic, not propagated or promoted tracks."""
    rows = {row['source_frame']: row for row in labels['frames']}
    anchors = {(key.character_id, key.frame) for key in state.identity_keyframes}
    totals, details = {}, []
    for item in measurements:
        row = rows.get(item['source_frame'])
        if row is None or (item['character'], item['source_frame']) in anchors:
            continue
        ch = item['character']
        label = row['labels'][ch]
        positions = ([label['candidate_position']] if label['kind'] == 'candidate' else label.get('candidate_positions') or [])
        ids = {row['candidates'][p - 1]['observation_id'] for p in positions}
        selected = item['top_observation_id']
        result = 'correct' if selected in ids else 'wrong' if ids and selected else 'unavailable' if ids else 'no-valid-person-label'
        name = f"{ch}/{item['family']}/{item['crop']}/anchor-{item['anchor_frame']}"
        totals.setdefault(name, Counter())[result] += 1
        details.append(dict(item, outcome=result, blender_frame=row['blender_frame']))
    return {'summary': {k: dict(v) for k, v in totals.items()}, 'frames': details}


def extract_features(cache, video, weights):
    """Offline CPU descriptors/flow and already-installed local OSNet, no SAM3 run."""
    import cv2
    import numpy as np
    from diagnose_preview_target_appearance import OSNetEmbedder
    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)
    features = {'color': {}, 'osnet': {}, 'flow': {}}
    timings = Counter()
    capture = cv2.VideoCapture(str(video))
    if (not capture.isOpened() or capture.get(cv2.CAP_PROP_FPS) != cache.source.fps or
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) != cache.source.width or
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) != cache.source.height):
        raise ValueError('Decoded source metadata differs from cache')
    frames = {}
    pending, pending_keys = [], []
    start = time.perf_counter()
    embedder = OSNetEmbedder(weights, 'cuda')
    import torch
    torch.manual_seed(0)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.cuda.reset_peak_memory_stats()
    timings['osnet_load_seconds'] = time.perf_counter() - start

    def batch():
        if not pending:
            return
        t = time.perf_counter()
        for key, vector in zip(pending_keys, embedder.embed_batch(pending)):
            features['osnet'][key] = np.asarray(vector, dtype=np.float64)
        timings['osnet_inference_seconds'] += time.perf_counter() - t
        pending.clear()
        pending_keys.clear()

    try:
        for frame in range(cache.source.start_frame, cache.source.end_frame + 1):
            t = time.perf_counter()
            success, bgr = capture.read()
            if not success:
                raise ValueError(f'Decode failed at source frame {frame}')
            timings['decode_seconds'] += time.perf_counter() - t
            frames[frame] = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), None, fx=.5, fy=.5)
            row = cache.frame_result(frame)
            if row is None:
                continue
            for candidate in row.candidates:
                for view in ('whole', 'inner'):
                    x1, y1, x2, y2 = crop_bounds(candidate.box_xyxy, bgr.shape[1], bgr.shape[0], view)
                    if x2 <= x1 or y2 <= y1:
                        raise ValueError('Empty candidate crop')
                    crop = bgr[y1:y2, x1:x2]
                    key = (frame, candidate.observation_id, view)
                    t = time.perf_counter()
                    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
                    cells = []
                    for ys in np.array_split(hsv, 2, axis=0):
                        for cell in np.array_split(ys, 2, axis=1):
                            histogram = cv2.calcHist([cell], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256]).flatten().astype(np.float64)
                            histogram /= max(np.linalg.norm(histogram), 1e-30)
                            cells.append(histogram)
                    descriptor = np.concatenate(cells)
                    features['color'][key] = descriptor / np.linalg.norm(descriptor)
                    timings['color_seconds'] += time.perf_counter() - t
                    pending.append(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                    pending_keys.append(key)
                    if len(pending) == 32:
                        batch()
        batch()
    finally:
        capture.release()
    t = time.perf_counter()
    for frame, gray in frames.items():
        row = cache.frame_result(frame)
        if row is None:
            continue
        for candidate in row.candidates:
            x1, y1, x2, y2 = crop_bounds(candidate.box_xyxy, cache.source.width, cache.source.height, 'inner')
            mask = np.zeros_like(gray)
            mask[y1 // 2:y2 // 2, x1 // 2:x2 // 2] = 255
            points = cv2.goodFeaturesToTrack(gray, maxCorners=40, qualityLevel=.01, minDistance=5, mask=mask)
            for nxt in (frame - 1, frame + 1):
                key = (frame, candidate.observation_id, nxt)
                features['flow'][key] = None
                if nxt not in frames or points is None:
                    continue
                moved, status, _ = cv2.calcOpticalFlowPyrLK(gray, frames[nxt], points, None, winSize=(21, 21), maxLevel=3)
                if moved is None:
                    continue
                returned, backstatus, _ = cv2.calcOpticalFlowPyrLK(frames[nxt], gray, moved, None, winSize=(21, 21), maxLevel=3)
                if returned is None:
                    continue
                valid = (status[:, 0] == 1) & (backstatus[:, 0] == 1) & (np.linalg.norm((returned - points)[:, 0], axis=1) <= 1)
                if valid.any():
                    # Grayscale image coordinates are top-left; canonical y is up.
                    delta = np.median((moved - points)[valid, 0], axis=0) * 2
                    features['flow'][key] = (float(delta[0]), -float(delta[1]))
    timings['flow_seconds'] = time.perf_counter() - t
    timings['peak_cuda_allocated_bytes'] = torch.cuda.max_memory_allocated()
    timings['feature_vectors_per_family'] = len(features['color'])
    timings['feature_candidates'] = len(features['color']) // 2
    timings['color_float32_payload_bytes'] = len(features['color']) * 768 * 4
    timings['osnet_float32_payload_bytes'] = sum(v.size * 4 for v in features['osnet'].values())
    timings['grayscale_working_bytes'] = sum(v.nbytes for v in frames.values())
    timings['cuda_device'] = torch.cuda.get_device_name()
    return features, dict(timings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-root', type=Path, required=True)
    parser.add_argument('--companion', type=Path, required=True)
    parser.add_argument('--corrected-companion', type=Path, required=True)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True, help='Scoring pass only')
    parser.add_argument('--baseline', type=Path, required=True, help='Frozen geometry comparison')
    parser.add_argument('--corrected-baseline', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if not root.is_relative_to((Path(__file__).resolve().parents[1] / 'checkpoints/dev-preview').resolve()) or root.exists():
        parser.error('Use a new directory under checkpoints/dev-preview')
    sys.path.insert(0, str(args.core_root.resolve()))
    from vta_core import bounded_tracking as bt
    from vta_core.project_persistence import load_project_state
    from vta_core.analysis_cache import load_analysis_cache
    inputs = [args.labels, args.video, args.weights, args.baseline, args.corrected_baseline,
              args.core_root / 'vta_core/bounded_tracking.py']
    inputs += [p for directory in (args.companion, args.corrected_companion) for p in directory.rglob('*') if p.is_file()]
    frozen = {str(p.resolve()): sha256(p) for p in inputs}
    project = load_project_state(args.companion)
    corrected = load_project_state(args.corrected_companion)
    cache = load_analysis_cache(args.companion, 'sam3-overlay-smoke')
    if sha256(args.video) != cache.source.video_sha256:
        raise ValueError('Source video hash differs from cache')
    raw = project.to_dict()
    raw_corrected = corrected.to_dict()
    raw_corrected['tracking_revisions'] = []
    corrected = type(project).from_dict(raw_corrected)
    scopes = {'baseline': (project, {ch: [0] for ch in CHARACTERS}),
              'human-anchors': (corrected, {'wrestler-a': [0, 12, 90], 'wrestler-b': [0, 12]})}
    write_new(root / 'plan.json', {'schema': 'vta-signal-research-plan', 'version': 1,
              'specification': SPEC, 'methods': list(METHODS), 'inputs_sha256': frozen,
              'thresholds': {name: getattr(bt, name) for name in ('MAX_AREA_FOLD', 'MAX_CENTER_DISPLACEMENT', 'MIN_IOU', 'MIN_MATCH_SCORE', 'MIN_RUNNER_MARGIN')},
              'private_policy': bt.POLICY_REVISION,
              'command': [sys.executable, *sys.argv], 'harness_sha256': sha256(Path(__file__)),
              'public_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'private_head': subprocess.check_output(['git', '-c', f'safe.directory={args.core_root.resolve().as_posix()}', '-C', str(args.core_root), 'rev-parse', 'HEAD'], text=True).strip()})
    features, timings = extract_features(cache, args.video, args.weights)
    # Cache in the disposable research directory, not in authoritative state.
    import numpy as np
    keys = sorted(features['color'])
    np.savez_compressed(root / 'features.npz',
                        keys=np.asarray(keys, dtype=str),
                        color=np.stack([features['color'][key] for key in keys]),
                        osnet=np.stack([features['osnet'][key] for key in keys]))
    predictions, elapsed = {}, {}
    for scheme, (state, edits) in scopes.items():
        for method in METHODS:
            name = scheme + '/' + method
            t = time.perf_counter()
            prediction = run_predictions(bt, state, cache, edits, method, features)
            elapsed[name] = time.perf_counter() - t
            repeated = run_predictions(bt, state, cache, edits, method, features)
            if repeated != prediction:
                raise ValueError(f'Non-deterministic matcher: {name}')
            predictions[name] = prediction
    for name, path in (('baseline/geometry', args.baseline), ('human-anchors/geometry', args.corrected_baseline)):
        frozen_baseline = json.loads(path.read_text(encoding='utf-8'))
        for ch in CHARACTERS:
            records = list(predictions[name]['characters'][ch]['records'].values())
            if records != frozen_baseline['characters'][ch]['records']:
                raise ValueError(f'Experimental geometry mirror differs from frozen core: {name}/{ch}')
    if project.to_dict() != raw or corrected.to_dict() != raw_corrected:
        raise ValueError('Input project mutated')
    write_new(root / 'predictions.json', {'schema': 'vta-signal-research-predictions', 'version': 1, 'predictions': predictions,
                                         'plan_sha256': sha256(root / 'plan.json'), 'labels_consulted': False})
    measurements = independent_appearance(cache, corrected, features)
    write_new(root / 'appearance_measurements.json', {'schema': 'vta-signal-research-appearance', 'version': 1,
              'labels_consulted': False, 'measurements': measurements})
    # Only now open and validate scoring labels. They were hashed, not passed
    # to extraction or prediction. Human-anchor plan comes from prior authority.
    labels = json.loads(args.labels.read_text(encoding='utf-8'))
    template = prepare(args.companion, tuple(labels['characters']),
                       tuple(row['blender_frame'] for row in labels['frames']), 1,
                       version=2, excluded_blender_frames=tuple(labels['excluded_from_independent_score_blender_frames']))
    validate_labels(labels, template)
    evaluations = {name: score_predictions(value, labels, scopes[name.split('/')[0]][0])
                   for name, value in predictions.items()}
    # Common-case comparisons exclude ALL supplied anchors in both schemes.
    common_labels = copy.deepcopy(labels)
    anchor_pairs = {(key.character_id, key.frame) for state, _ in scopes.values() for key in state.identity_keyframes}
    for row in common_labels['frames']:
        for ch in CHARACTERS:
            if (ch, row['source_frame']) in anchor_pairs:
                row['labels'][ch] = {'kind': 'skip', 'candidate_position': None, 'candidate_positions': None}
    common = {name: score_predictions(value, common_labels, scopes[name.split('/')[0]][0])
              for name, value in predictions.items()}
    before_equal = all(sha256(Path(p)) == h for p, h in frozen.items())
    if not before_equal:
        raise ValueError('Frozen input changed')
    report = {'schema': 'vta-signal-research-evaluation', 'version': 1, 'evaluations': evaluations,
              'common_unanchored_evaluations': common, 'timings': timings, 'matching_seconds': elapsed,
              'independent_appearance': score_appearance(measurements, labels, corrected),
              'geometry_mirrors_exact_frozen_records': True,
              'repeatability': 'exact repeated predictions for every variant', 'inputs_unchanged': True,
              'inputs_sha256': frozen, 'feature_cache_sha256': sha256(root / 'features.npz'),
              'predictions_sha256': sha256(root / 'predictions.json'),
              'warning': 'One clip; correlated frames; in-sample exploratory comparisons; no numerical threshold selected.'}
    write_new(root / 'evaluation.json', report)
    print(json.dumps({'output': str(root), 'timings': timings,
                      'summaries': {name: item['summary'] for name, item in evaluations.items()}}, sort_keys=True))


if __name__ == '__main__':
    main()
