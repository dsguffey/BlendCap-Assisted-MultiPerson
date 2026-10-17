"""Background rotation adapter; same installed private-core boundary as generation."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from blendcap_vta_generation_ipc import filesystem_path, write_new_json


def file_hash(path):
    with open(filesystem_path(path), 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(args):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(args.core_root).resolve()))
    from vta_core.camera_rotation import solve_frames, validate_request
    from vta_core import load_analysis_cache
    import cv2
    cv2.setNumThreads(1)
    path = Path(args.request).resolve()
    # All paths belong to one exclusively created request directory.
    if path.name != 'request.json' or not path.parent.name.startswith('rotation-'):
        raise ValueError('Invalid camera-rotation request path.')
    data = json.loads(path.read_text(encoding='utf-8'))
    request = validate_request(data['identity'])
    root = Path(data['companion_root']).resolve()
    if path.parent.parent != root/'reconstruction':
        raise ValueError('Rotation request is outside the project companion.')
    video = Path(data['video']).resolve()
    cache_path = root/'analysis'/(data['revision']+'.json')
    if cache_path.parent != root/'analysis' or cache_path.name != data['revision']+'.json':
        raise ValueError('Invalid analysis revision path.')
    if file_hash(video) != request['video_sha256'] or file_hash(cache_path) != request['cache_sha256']:
        raise ValueError('Source video or candidate analysis changed; retry cleanup.')
    cache = load_analysis_cache(root,data['revision'])
    if cache.source.source_id != request['source_id'] or cache.source.video_sha256 != request['video_sha256']:
        raise ValueError('Analysis belongs to a different source.')
    first,last = request['range']
    boxes = {}
    for frame in range(first,last+1):
        result = cache.frame_result(frame)
        if result is not None:
            # Every detected person, even suppressed/unassigned ones. Core boxes
            # are lower-left; OpenCV image masks are top-left source pixels.
            boxes[frame] = [(c.box_xyxy[0],request['height']-c.box_xyxy[3],
                             c.box_xyxy[2],request['height']-c.box_xyxy[1]) for c in result.candidates]
    stop = path.parent/'cancel'
    capture = cv2.VideoCapture(str(video))
    try:
        if not capture.isOpened():
            raise ValueError('Could not open source video for rotation cleanup.')
        def frames():
            # Sequential decoding preserves exact source-frame identity. A seek
            # can land on a keyframe instead of the requested source frame.
            for frame in range(last+1):
                ok,image = capture.read()
                if not ok:
                    raise ValueError(f'Could not decode source frame {frame}.')
                if stop.exists():
                    raise InterruptedError('Camera cleanup cancelled; animation unchanged.')
                if frame >= first:
                    yield cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        solve = solve_frames(frames(),boxes,request,
            progress=lambda n,total: print(f'Rotation {n}/{total}',flush=True),cancelled=stop.exists)
        for skipped in solve['skipped']:
            print(f"Skipped source frame {skipped['frame']}: {skipped['reason']}",flush=True)
        counts={name:sum(row['accepted'][name] for row in solve['components'][1:]) for name in ('pan','tilt','roll')}
        print(f"Rotation solve: {len(solve['diagnostics'])} fitted frames, "
              f"{len(solve['skipped'])} skipped, one reference frame. Accepted components: {counts}",flush=True)
        if stop.exists():
            raise InterruptedError('Camera cleanup cancelled; animation unchanged.')
        if file_hash(video) != request['video_sha256'] or file_hash(cache_path) != request['cache_sha256']:
            raise ValueError('Source video or candidate analysis changed during the solve; retry cleanup.')
        write_new_json(path.parent/'solve.json',solve)
    finally:
        capture.release()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--core-root',required=True)
    parser.add_argument('--request',required=True)
    args = parser.parse_args()
    try:
        run(args)
    except InterruptedError as exc:
        print(str(exc),flush=True)
        sys.exit(2)
    except Exception as exc:
        print('Rotation cleanup failed: '+str(exc),flush=True)
        sys.exit(1)
