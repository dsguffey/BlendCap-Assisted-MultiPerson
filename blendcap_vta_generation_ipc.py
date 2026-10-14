"""Request-scoped generation IPC and atomic publication; no Blender/model policy."""
import json
import os
from pathlib import Path
import re
from uuid import uuid4


class GenerationCancelled(Exception):
    """Normal cooperative cancellation, handled without a worker traceback."""


def preflight_generation_paths(root, *, windows=None):
    """Third-party BVH/model readers may still use legacy Windows paths.

    IPC supports extended paths independently. Don't assume every external
    reader opts in. Keep the generated run leaf compact and fail before models.
    """
    if windows is None:
        windows = os.name == 'nt'
    largest = Path(root).resolve() / 'reconstruction' / ('r-' + '0' * 16) / 'character-999999.bvh'
    if windows and len(str(largest)) >= 260:
        raise ValueError('Project path is too long for Windows generation/export readers. '
                         'Save the Blender project in a shorter folder, then configure its source again. '
                         f'Generated path would be {len(str(largest))} characters (limit 259).')


def new_output_directory(root):
    preflight_generation_paths(root)
    path = Path(root) / 'reconstruction' / ('r-' + uuid4().hex[:16])
    path.mkdir(parents=True, exist_ok=False)  # 64-bit random ID; exclusive creation never overwrites.
    return path


def filesystem_path(path):
    """Embedded Blender Python is not always Windows long-path-aware."""
    value = os.path.abspath(os.fspath(path))
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        value = '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    return value


def write_new_json(path, data):
    """Atomic no-replace publication. Only this writer removes its own temp."""
    path = Path(path)
    # Short independent leaf; don't append another UUID to a long target name.
    temporary = path.with_name('.vta-' + uuid4().hex + '.tmp')
    owned = False
    try:
        with open(filesystem_path(temporary), 'x', encoding='utf-8', newline='\n') as handle:
            owned = True
            json.dump(data, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.link(filesystem_path(temporary), filesystem_path(path))
    finally:
        if owned:
            try:
                os.unlink(filesystem_path(temporary))
            except FileNotFoundError:
                pass


def read_json(path):
    with open(filesystem_path(path), encoding='utf-8') as handle:
        return json.load(handle)


def requires_migration(previous, incoming):
    if not isinstance(previous, (tuple, list)) or any(not isinstance(p, str) or not p for p in previous):
        raise ValueError('Invalid prior generation ownership metadata')
    return any(p != incoming for p in previous)


def marker_path(root, generation_id, suffix):
    if not isinstance(generation_id, str) or not re.fullmatch('[0-9a-f]{32}', generation_id):
        raise ValueError('Invalid generation request ID')
    if suffix not in ('log', 'stop', 'decision'):
        raise ValueError('Invalid generation marker type')
    return Path(root).resolve()/'reconstruction'/('generation-' + generation_id + '.' + suffix)


def validate_marker(path, root, generation_id, suffix):
    if Path(path).resolve() != marker_path(root, generation_id, suffix):
        raise ValueError('Generation marker belongs to another request')


def new_request(root, *, decision_required=False):
    root = Path(root).resolve()
    for _ in range(8):
        generation_id = uuid4().hex
        paths = {suffix:marker_path(root, generation_id, suffix) for suffix in ('log','stop','decision')}
        if not any(os.path.exists(filesystem_path(p)) for p in paths.values()):
            return {'id':generation_id,'root':root,'log':paths['log'],'stop':paths['stop'],
                    'decision':paths['decision'] if decision_required else None,'process':None,
                    'cancelled':False,'decided':False,'ready':False,'migration':False}
    raise ValueError('Could not create a fresh generation request; previous files were retained')


def cancel_request(job):
    validate_marker(job['stop'], job['root'], job['id'], 'stop')
    try:
        with open(filesystem_path(job['stop']), 'x', encoding='utf-8') as handle:
            handle.write('Cancel generation ' + job['id'] + '\n')
    except FileExistsError:
        pass  # This request's repeated Cancel is idempotent.
    job['cancelled'] = True


def publish_decision(job, migration):
    if job.get('cancelled'):
        raise GenerationCancelled('This request was cancelled')
    if job['decision'] is None or migration is None:
        raise ValueError('Only an actual migration needs a user decision')
    process = job.get('process')
    if process is None or process.poll() is not None:
        raise ValueError('Generation is no longer waiting for a decision')
    validate_marker(job['decision'], job['root'], job['id'], 'decision')
    data = {'version':1,'generation_id':job['id'],
            'requested_range':list(job['plan']['source_range']),'migration_range':list(migration)}
    try:
        write_new_json(job['decision'], data)
    except FileExistsError:
        if read_json(job['decision']) != data:
            raise ValueError('A different decision already exists for this request')
    job['migration'] = True
    job['decided'] = True


def read_decision(path, root, generation_id, requested):
    validate_marker(path, root, generation_id, 'decision')
    choice = read_json(path)
    if (choice.get('version') != 1 or choice.get('generation_id') != generation_id
            or choice.get('requested_range') != list(requested)):
        raise ValueError('Generation decision belongs to another request or range')
    span = choice.get('migration_range')
    if (not isinstance(span, list) or len(span) != 2
            or any(type(f) is not int for f in span) or span[0] > span[1]):
        raise ValueError('Migration decision requires a valid frame interval')
    return span


def cleanup_request(job):
    """Host owns marker cleanup, but only after its worker has exited.

    No glob/temp scavenging: a publisher alone owns its temporary file. Retain
    the diagnostic log and immutable output runs. Never clean a different ID.
    """
    process = job.get('process')
    if process is None or process.poll() is None:
        return False
    for suffix in ('stop', 'decision'):
        path = job.get(suffix)
        if path is None:
            continue
        validate_marker(path, job['root'], job['id'], suffix)
        try:
            os.unlink(filesystem_path(path))
        except FileNotFoundError:
            pass
    return True
