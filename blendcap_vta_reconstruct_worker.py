"""Offline, explicit reviewed VTA boxes -> existing Fast-SAM-3D-Body -> BVH.

Never invokes legacy person selection, detector matching, or review mutations.
Every run lives in a fresh immutable directory; failed runs remain diagnostic.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
if __package__:
    from .blendcap_vta_generation_ipc import (
        GenerationCancelled, filesystem_path, read_decision, requires_migration,
        validate_marker, write_new_json, preflight_generation_paths, new_output_directory,
    )
else:
    from blendcap_vta_generation_ipc import (
        GenerationCancelled, filesystem_path, read_decision, requires_migration,
        validate_marker, write_new_json, preflight_generation_paths, new_output_directory,
    )


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def bbox_for_backend(box, height):
    x1, y1, x2, y2 = box
    return (x1, height-y2, x2, height-y1)


def export_command(python, bundle, pose_path, bvh_path):
    return [str(python), str(bundle / 'npz_to_bvh.py'), '--input', str(pose_path),
            '--output', str(bvh_path), '--mhr-model',
            str(bundle / 'checkpoints/sam-3d-body-dinov3/assets/mhr_model.pt'),
            '--export', 'body', '--rest-pose', 'tpose', '--root-motion', '--no-center',
            '--no-foot-lock', '--smooth', '0', '--smooth-root', '0', '--smooth-vertical', '0']


def await_generation_decision(path, root, plan, reconstruction, check_stop, generation_id):
    """Consent handshake before pose inference; one job, no Blender mutation."""
    path = Path(path).resolve()
    validate_marker(path, root, generation_id, 'decision')
    print('VTA_READY=' + json.dumps({'generation_id':generation_id,'plan': plan,
                                    'reconstruction': reconstruction}), flush=True)
    deadline = time.monotonic() + 900  # Release the lock if the host closes while waiting.
    while not os.path.exists(filesystem_path(path)):
        check_stop()
        if time.monotonic() >= deadline:
            raise RuntimeError('Animation update confirmation expired; retry generation')
        time.sleep(.1)
    check_stop()
    return read_decision(path, root, generation_id, plan['source_range'])


def infer_reviewed_pose(estimator, np, torch, rgb, evidence, height, cam_int=None):
    """One exact reviewed box, one aligned pose; no detector or continuity fallback."""
    box = np.asarray([bbox_for_backend(evidence['box_xyxy'], height)], dtype=np.float32)
    with torch.inference_mode():
        options = {} if cam_int is None else {'cam_int': cam_int}
        result = estimator.process_one_image(rgb, bboxes=box, inference_type='full', **options)
    if not isinstance(result, list) or len(result) != 1:
        raise RuntimeError('Explicit one-box reconstruction did not return exactly one pose')
    person = result[0]
    if not np.array_equal(np.asarray(person['bbox']), box[0]):
        raise RuntimeError('Reconstruction returned a different candidate box')
    arrays = {key: np.asarray(person[key]) for key in
              ('pred_global_rots', 'pred_joint_coords', 'pred_cam_t')}
    rots, joints, cam = arrays.values()
    if (rots.ndim != 3 or rots.shape[1:] != (3,3) or joints.shape != (rots.shape[0],3)
            or cam.shape != (3,)):
        raise RuntimeError('Reconstruction returned malformed skeletal channels')
    if not all(np.isfinite(value).all() for value in arrays.values()):
        return None
    focal = np.asarray(person.get('focal_length'))
    if focal.size != 1 or not np.issubdtype(focal.dtype, np.number) or not np.isfinite(focal).all() or focal.item() <= 0:
        raise RuntimeError('Reconstruction requires a finite positive source focal_length')
    arrays['focal_length'] = float(focal.item())
    if cam_int is not None and abs(arrays['focal_length'] - float(cam_int[0, 0, 0])) > 1e-3:
        raise RuntimeError('Reconstruction did not use the fixed source calibration')
    return arrays


def runtime_identity(bundle, provenance, estimator, torch):
    """Actual offline model/code/config identity, never a path or a UI label alone."""
    from importlib.metadata import version, PackageNotFoundError
    def package(name):
        try:
            return version(name)
        except PackageNotFoundError:
            return None
    trees = {}
    for name in ('models/fast-sam-3d-body', 'models/dinov3_hub'):
        folder = bundle / name
        if name == 'models/dinov3_hub':
            override = os.environ.get('DINOV3_HUB_DIR')
            default = folder / 'facebookresearch_dinov3_main'
            if override and Path(override).resolve() != default.resolve():
                folder = Path(override)
        files = {str(p.relative_to(folder)): digest(p) for p in sorted(folder.rglob('*.py'))}
        if not files:
            raise RuntimeError('Installed reconstruction provider code is missing: ' + name)
        trees[name] = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    if os.environ.get('FOV_TRT', '0') == '1':
        engine = bundle/'models/fast-sam-3d-body/checkpoints/moge_trt/moge_dinov2_encoder_fp16.engine'
        trees['fov_trt_engine'] = digest(engine) if engine.is_file() else None
        trees['tensorrt_version'] = package('tensorrt')
    size = os.environ.get('FOV_MODEL', 's')
    short = {'s': 'vits', 'b': 'vitb', 'l': 'vitl'}.get(size, 'vitl')
    fov = Path(os.environ.get('BLENDCAP_MOGE_BASE', str(bundle/'checkpoints'))) / ('moge-2-'+short+'-normal/model.pt')
    settings = {key: os.environ.get(key, default) for key, default in {
        'FOV_MODEL': 's', 'FOV_FAST': '1', 'FOV_LEVEL': '0', 'FOV_SIZE': '0', 'FOV_TRT': '0',
        'SKIP_KEYPOINT_PROMPT': '1', 'BODY_INTERM_PRED_LAYERS': '0,1,2',
        'HAND_INTERM_PRED_LAYERS': '0,1', 'MHR_NO_CORRECTIVES': '1', 'GPU_HAND_PREP': '1',
        'LAYER_DTYPE': 'fp32', 'USE_ORT': '0', 'USE_COMPILE': '0',
        'USE_COMPILE_BACKBONE': '0', 'DECODER_COMPILE': '0'}.items()}
    return {'backend': 'Fast-SAM-3D-Body', 'inference_type': 'full', 'settings': settings,
        'asset_hashes': {key: provenance[key] for key in ('checkpoint_sha256', 'mhr_sha256',
            'config_sha256', 'worker_sha256', 'capture_source_sha256', 'export_source_sha256',
            'private_plan_source_sha256', 'registration_source_sha256', 'calibration_source_sha256')},
        'provider_code': trees, 'fov_checkpoint_sha256': digest(fov) if fov.is_file() else None,
        'fov_available': estimator.fov_estimator is not None, 'device': str(estimator.device),
        'packages': {name: package(name) for name in ('torch', 'numpy', 'opencv-python', 'moge')},
        'torch_cuda': getattr(getattr(torch, 'version', None), 'cuda', None),
        'reconstruction_algorithm': 'reviewed-box-full-raw-v1'}


def fixed_source_calibration(root, source, provider, estimator, np, torch, decode_rgb):
    from vta_core.source_calibration import calibration_identity, load_or_compute_calibration
    identity = calibration_identity(source.video_sha256, *source.require_dimensions(), provider)
    def compute():
        if estimator.fov_estimator is None:
            raise RuntimeError('FIRST source-frame-0 calibration unavailable: install/repair MoGe; '
                               'no default or per-frame fallback is permitted')
        rgb = decode_rgb(0)
        if rgb.shape[:2] != (identity['height'], identity['width']):
            raise RuntimeError('Source-frame-0 calibration image dimensions changed')
        with torch.inference_mode():
            value = estimator.fov_estimator.get_cam_intrinsics(rgb)
        if hasattr(value, 'detach'):
            value = value.detach().cpu().numpy()
        matrix = np.asarray(value)
        if matrix.shape == (1, 3, 3):
            matrix = matrix[0]
        return matrix.tolist()
    record, computed = load_or_compute_calibration(root, identity, compute)
    matrix = torch.as_tensor([record['intrinsics']], dtype=torch.float32)
    return identity, record, matrix, computed


def run(args):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', YOLO_OFFLINE='true',
                      HF_HUB_DISABLE_TELEMETRY='1', LAYER_DTYPE='fp32', USE_COMPILE='0',
                      USE_COMPILE_BACKBONE='0', DECODER_COMPILE='0', USE_ORT='0')
    core = Path(args.core_root).resolve()
    bundle = Path(args.blendcap_root).resolve()
    root = Path(args.companion_root).resolve()
    preflight_generation_paths(root)
    video = Path(args.video).resolve()
    stop = Path(args.stop_file).resolve() if getattr(args, 'stop_file', None) else None
    if stop is not None and (stop.parent != (root/'reconstruction').resolve()
            or not stop.name.startswith('generation-') or stop.suffix != '.stop'):
        raise RuntimeError('Animation stop request must belong to this generation directory')
    generation_id = getattr(args, 'generation_id', None)
    if generation_id is not None and stop is not None:
        validate_marker(stop, root, generation_id, 'stop')
    def check_stop():
        if stop is not None and os.path.exists(filesystem_path(stop)):
            raise GenerationCancelled('Animation generation cancelled; prior output retained')
    sys.path.insert(0, str(core))
    from vta_core import load_project_state, load_analysis_cache, build_skeletal_plan, resolve_pose_schedule
    from vta_core.skeletal_output import repair_anchor_requests, validate_skeletal_repairs
    from vta_core.source_preflight import preflight_source
    from vta_core.source_decode import decode_source_frame
    from vta_core.provider_contract import ProviderFrameRequest
    from vta_core.camera_registration import source_camera_sample
    from vta_core.source_calibration import calibration_identity, reconstruction_record
    from blendcap_vta_analyze_worker import _source_for_video
    lock = root / 'analysis/vta-analysis.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    if __package__:
        from .blendcap_vta_operation_lock import acquire
    else:
        from blendcap_vta_operation_lock import acquire
    ownership=acquire(lock,request_id=generation_id)
    ownership.__enter__()
    started = time.monotonic()
    try:
        project = load_project_state(root)
        source = _source_for_video(project, video)
        preflight_source(source, video)
        revision = project.to_dict()['active_analysis_revision_by_source'].get(source.source_id)
        cache = load_analysis_cache(root, revision)
        start, end = getattr(args, 'start_frame', None), getattr(args, 'end_frame', None)
        if (start is None) != (end is None):
            raise RuntimeError('Animation range requires both start and end frames')
        options = {'frame_range': (start, end)} if start is not None else {}
        if getattr(args, 'character_id', None):
            options['character_ids'] = args.character_id
        plan = build_skeletal_plan(project, cache, **options)
        validate_skeletal_repairs(project, plan, frame_offset=1)
        check_stop()
        checkpoint = bundle / 'checkpoints/sam-3d-body-dinov3/model.ckpt'
        mhr = bundle / 'checkpoints/sam-3d-body-dinov3/assets/mhr_model.pt'
        config = bundle / 'checkpoints/sam-3d-body-dinov3/model_config.yaml'
        for required in (checkpoint, mhr, config, bundle / 'save_mhr_data.py', bundle / 'npz_to_bvh.py'):
            if not required.is_file():
                raise RuntimeError('Installed skeletal runtime is incomplete: ' + str(required))
        output = new_output_directory(root)
        write_new_json(output / 'plan.json', plan)
        provenance = {'backend': 'Fast-SAM-3D-Body', 'precision': 'fp32',
            'checkpoint_sha256': digest(checkpoint), 'mhr_sha256': digest(mhr),
            'config_sha256': digest(config), 'worker_sha256': digest(Path(__file__)),
            'capture_source_sha256': digest(bundle / 'save_mhr_data.py'),
            'export_source_sha256': digest(bundle / 'npz_to_bvh.py'),
            'repair_stage': 'registered-raw-BVH-to-final-animation; private schedule v3; output v4',
            'private_plan_source_sha256': digest(core / 'vta_core/skeletal_output.py'),
            'registration_source_sha256': digest(core / 'vta_core/camera_registration.py'),
            'calibration_source_sha256': digest(core / 'vta_core/source_calibration.py'),
            'private_state_source_sha256': digest(core / 'vta_core/state_model.py'),
            'environment_python': str(Path(sys.executable).resolve()),
            'command': sys.argv, 'plan_sha256': digest(output / 'plan.json')}
        write_new_json(output / 'request.json', provenance)
        sys.path.insert(0, str(bundle))
        import numpy as np
        import cv2
        import torch
        # Existing estimator, offline local models. Explicit boxes bypass YOLO inference.
        from save_mhr_data import load_estimator_fast
        estimator = load_estimator_fast()
        mode = getattr(args, 'fov_mode', 'FIRST')
        if mode not in ('FIRST', 'EVERY'):
            raise RuntimeError('Reviewed VTA reconstruction supports FIRST or EVERY, not MANUAL focal entry')
        provider = runtime_identity(bundle, provenance, estimator, torch)
        def decode_rgb(frame):
            decoded = decode_source_frame(video, ProviderFrameRequest(source, frame))
            return cv2.cvtColor(decoded.pixels_bgr, cv2.COLOR_BGR2RGB)
        cam_int, calibration = None, None
        identity = calibration_identity(source.video_sha256, plan['width'], plan['height'], provider)
        if mode == 'FIRST':
            if provider.get('fov_checkpoint_sha256') is None:
                raise RuntimeError('FIRST calibration requires an identifiable offline MoGe checkpoint; prior output retained')
            identity, calibration, cam_int, computed = fixed_source_calibration(
                root, source, provider, estimator, np, torch, decode_rgb)
            print('VTA source-frame-0 calibration ' + ('computed' if computed else 'reused'), flush=True)
        reconstruction = reconstruction_record(mode, identity, calibration)
        provenance['device'] = str(estimator.device)
        provenance['fov_backend'] = ('MoGe-'+provider.get('settings', {}).get('FOV_MODEL', 's')
            if estimator.fov_estimator is not None else 'default-fov-fallback')
        provenance['fov_checkpoint_sha256'] = provider.get('fov_checkpoint_sha256')
        provenance['yolo_checkpoint_sha256'] = digest(bundle / 'checkpoints/yolo/yolo11m-pose.pt')
        provenance['detector_inference_used'] = False
        provenance['warnings'] = ([] if estimator.fov_estimator is not None else
                                  ['MoGe unavailable; existing backend default FOV used'])
        provenance['reconstruction'] = reconstruction
        decision = Path(args.decision_file).resolve() if getattr(args, 'decision_file', None) else None
        migration = None
        previous = getattr(args, 'previous_fingerprint', ()) or ()
        if requires_migration(previous, reconstruction['fingerprint']):
            if decision is None or generation_id is None:
                raise ValueError('Existing animation needs explicit migration approval; retry from the VTA generation panel')
            migration = await_generation_decision(decision, root, plan, reconstruction, check_stop, generation_id)
            scene_ids=options.get('character_ids') or getattr(args,'scene_character_id',None)
            migration_plan = build_skeletal_plan(project, cache, frame_range=migration,
                **({'character_ids':scene_ids} if scene_ids else {}))
            validate_skeletal_repairs(project, migration_plan, frame_offset=1)
        else:
            # No old output, or compatible existing output: no file read/write/wait.
            print('VTA_CALIBRATION_READY=' + str(generation_id or 'standalone'), flush=True)

        def generate(plan, options, output, provenance):
            records = {c['character_id']: {'frames': [], 'poses': [], 'failures': []}
                       for c in plan['characters']}
            attempted = {cid: set() for cid in records}
            by_frame = {}
            for character in plan['characters']:
                for sample in character['samples']:
                    if sample['mode'] == 'infer':
                        by_frame.setdefault(sample['frame'], []).append((character['character_id'], sample))
            for completed, (frame, requests) in enumerate(sorted(by_frame.items()), 1):
                check_stop()  # Cooperative stop; never kill an in-flight model call.
                rgb = decode_rgb(frame)
                for character_id, sample in requests:
                    attempted[character_id].add(frame)
                    arrays = infer_reviewed_pose(estimator, np, torch, rgb, sample['evidence'], plan['height'],
                                                 **({} if cam_int is None else {'cam_int': cam_int}))
                    if arrays is None:
                        records[character_id]['failures'].append({'frame': frame, 'reason': 'nonfinite-pose'})
                        continue
                    records[character_id]['frames'].append(frame)
                    records[character_id]['poses'].append(arrays)
                print(f'VTA reconstruction source frame {frame} complete', flush=True)
                print(f'VTA_PROGRESS={completed}/{len(by_frame)} requested inference frames complete', flush=True)
            # Repair anchors use current raw inference, never previously repaired output.
            for character in plan['characters']:
                cid = character['character_id']
                record = records[cid]
                while True:
                    extra = repair_anchor_requests(character['samples'], record['frames'], attempted[cid],
                                                   repair_context=character.get('repair_context', ()))
                    if not extra:
                        break
                    for sample in extra:
                        check_stop()
                        frame = sample['frame']
                        attempted[cid].add(frame)
                        rgb = decode_rgb(frame)
                        pose = infer_reviewed_pose(estimator, np, torch, rgb, sample['evidence'], plan['height'],
                                                  **({} if cam_int is None else {'cam_int': cam_int}))
                        if pose is None:
                            record['failures'].append({'frame': frame, 'reason': 'nonfinite-repair-anchor'})
                        else:
                            record['frames'].append(frame)
                            record['poses'].append(pose)
                ordered = sorted(zip(record['frames'], record['poses']))
                record['frames'] = [f for f, _ in ordered]
                record['poses'] = [p for _, p in ordered]
            # Validate every Character before exporting or publishing final output.
            schedules = {c['character_id']: resolve_pose_schedule(c['samples'], records[c['character_id']]['frames'],
                          repair_context=c.get('repair_context', ())) for c in plan['characters']}
            exported = []
            for index, character in enumerate(plan['characters']):
                check_stop()
                record = records[character['character_id']]
                frames = record['frames']
                item = {'character_id': character['character_id'], 'display_name': character['display_name'],
                        'source_frames': frames, 'schedule': schedules[character['character_id']],
                        'failures': record['failures'], 'bvh': None}
                if frames:
                    pose_path, bvh_path = output / f'character-{index}.npz', output / f'character-{index}.bvh'
                    arrays = {name: np.asarray([p[key] for p in record['poses']])
                        for name, key in [('global_rots','pred_global_rots'), ('joint_coords','pred_joint_coords'), ('cam_t','pred_cam_t')]}
                    arrays.update(frame_indices=np.asarray(frames), fps=np.asarray(plan['fps']), blendcap_npz_version=np.asarray(1))
                    arrays['focal_length'] = np.asarray([p['focal_length'] for p in record['poses']])
                    arrays['reconstruction_fingerprint'] = np.asarray(reconstruction['fingerprint'])
                    with pose_path.open('xb') as handle:
                        np.savez_compressed(handle, **arrays)
                    command = export_command(sys.executable, bundle, pose_path, bvh_path)
                    subprocess.run(command, cwd=bundle, check=True)
                    item.update(bvh=bvh_path.name, bvh_sha256=digest(bvh_path),
                                npz=pose_path.name, npz_sha256=digest(pose_path), export_command=command)
                    item['camera_registration'] = {'version': 1,
                        'convention': 'camera-centred-metres; world=(cv-x,cv-z,-cv-y); centred-square-pixels',
                        'samples': [source_camera_sample(f, (p['pred_joint_coords'][1] + p['pred_cam_t']).tolist(),
                            p['focal_length'], plan['width'], plan['height'])
                            for f, p in zip(frames, record['poses'])]}
                exported.append(item)
            if not any(s['state'] != 'unavailable' for c in exported for s in c['schedule']):
                raise RuntimeError('Animation range has no successful trustworthy or repaired pose; prior output retained')
            result = {'schema': 'vta-reviewed-skeletal-output', 'version': 4, 'plan': plan,
                      'provenance': provenance, 'reconstruction': reconstruction, 'characters': exported,
                      'runtime_seconds': time.monotonic()-started}
            # Reject evidence edits even from writers not honoring the common lock.
            current = build_skeletal_plan(load_project_state(root), load_analysis_cache(root, revision), **options)
            if current != plan:
                raise RuntimeError('Review/evidence changed during reconstruction; output was not published')
            check_stop()
            write_new_json(output / 'output.json', result)
            print('VTA_OUTPUT=' + str(output / 'output.json'), flush=True)
            return output / 'output.json'
        if migration is not None:
            migration_options = {'frame_range': migration}
            migration_ids=options.get('character_ids') or getattr(args,'scene_character_id',None)
            if migration_ids:migration_options['character_ids']=migration_ids
            migration_plan = build_skeletal_plan(project, cache, **migration_options)
            migration_output = new_output_directory(root)
            write_new_json(migration_output/'plan.json', migration_plan)
            migration_provenance = {**provenance, 'plan_sha256': digest(migration_output/'plan.json'),
                                    'stage': 'compatibility-migration'}
            write_new_json(migration_output/'request.json', migration_provenance)
            generate(migration_plan, migration_options, migration_output, migration_provenance)
        return generate(plan, options, output, provenance)
    except GenerationCancelled:
        print('VTA_CANCELLED=' + str(generation_id or 'standalone'), flush=True)
        return None
    finally:
        ownership.__exit__(None,None,None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('core-root', 'blendcap-root', 'companion-root', 'video'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--start-frame', type=int)
    parser.add_argument('--end-frame', type=int)
    parser.add_argument('--stop-file')
    parser.add_argument('--decision-file')
    parser.add_argument('--generation-id')
    parser.add_argument('--previous-fingerprint', action='append', default=[])
    parser.add_argument('--character-id', action='append')
    parser.add_argument('--scene-character-id', action='append')
    parser.add_argument('--fov-mode', choices=('FIRST', 'EVERY'), default='FIRST')
    run(parser.parse_args())


if __name__ == '__main__':
    import faulthandler
    faulthandler.enable()
    main()
