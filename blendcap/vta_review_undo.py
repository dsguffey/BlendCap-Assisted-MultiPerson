"""Small native-Blender-Undo bridge for explicit review/tracking commands.

Only an opaque token lives in an owned Text datablock. Compressed JSON snapshots
are session-only; cache/media/output files are never part of this bridge. All
restores use private-core validation, an operation lock and compare-and-swap.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import uuid
import zlib
import bpy

_entries = {}
_session = uuid.uuid4().hex
_error = ''
MAX_SNAPSHOTS = 64
MAX_COMPRESSED_BYTES = 128*1024*1024


def _pack(data):
    return hashlib.sha256(data).hexdigest(), zlib.compress(data, 1)


def _text(entry):
    return next((t for t in bpy.data.texts if t.get('vta_review_undo') == 1
        and t.get('vta_review_undo_session') == _session
        and t.get('vta_review_undo_root') == entry['root']), None)


@bpy.app.handlers.persistent
def clear(_unused=None):
    global _session, _error
    _entries.clear()
    _session = uuid.uuid4().hex
    _error = ''


@contextmanager
def transaction(context, location):
    global _error
    path = Path(location.companion_root)/'project_state.json'
    if not isinstance(getattr(context, 'scene', None), bpy.types.Scene) or not path.is_file():
        yield {}  # Host-independent test adapters retain their existing contract.
        return
    root = str(path.parent.resolve())
    before = path.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    entry = _entries.get(root)
    if entry is None or entry['digest'] != digest or entry['file'] != bpy.data.filepath:
        # External writes create an explicit history barrier, never overwritten
        # by replaying an old native token against a newer canonical document.
        entry = dict(root=root, file=bpy.data.filepath,
                     current='base', digest=digest, states={'base':(digest,zlib.compress(before,1))})
        _entries[root] = entry
        token = _text(entry)
        if token is not None:
            token.clear(); token.write('base')
    yield {'expected_document_sha256': digest}
    try:
        after = path.read_bytes()
    except OSError as exc:
        # The backend has already committed. Losing the optional Undo capture
        # must not turn a successful authoritative edit into a reported failure.
        _entries.pop(root, None)
        _error = 'Review saved, but Undo capture is unavailable: '+str(exc)
        print('VTA review Undo:', _error)
        return
    if after == before:
        return
    token = _text(entry)
    if token is None:
        token = bpy.data.texts.new('.VTA Review Undo')
        token.use_fake_user = True
        token['vta_review_undo'] = 1
        token['vta_review_undo_session'] = _session
        token['vta_review_undo_root'] = root
    identity = uuid.uuid4().hex
    digest, packed = _pack(after)
    entry['states'][identity] = digest, packed
    entry['current'], entry['digest'] = identity, digest
    token.clear(); token.write(identity)
    while (len(entry['states']) > MAX_SNAPSHOTS or
           sum(len(v[1]) for v in entry['states'].values()) > MAX_COMPRESSED_BYTES):
        victim = next((k for k in entry['states'] if k not in {'base',identity}), None)
        if victim is None:
            break
        del entry['states'][victim]
    _error = ''


@bpy.app.handlers.persistent
def restore(_unused=None, _depsgraph=None):
    global _error
    from . import vta_review_ui as ui
    changed = False
    for entry in tuple(_entries.values()):
        if entry['file'] != bpy.data.filepath:
            continue
        token = _text(entry)
        desired = token.as_string() if token is not None else 'base'
        if desired == entry['current']:
            continue
        try:
            if desired not in entry['states']:
                raise ValueError('This review Undo snapshot is no longer in session memory; project was not overwritten')
            digest, packed = entry['states'][desired]
            ui._get_backend(bpy.context).restore_review_snapshot(
                Path(entry['root']), entry['digest'], zlib.decompress(packed).decode('utf-8'))
            entry['current'], entry['digest'] = desired, digest
            changed = True
            _error = ''
        except Exception as exc:
            _error = str(exc)
            print('VTA review Undo:', _error)
            ui.set_operation_feedback('tracking','Review Undo was not applied.',_error,context=bpy.context,
                disposition='External project data was not overwritten.')
    if changed:
        ui._display.clear()
        ui._tracking_hints.clear()
        ui.refresh(bpy.context, force=True)
        ui.vta_invalidation.request_redraw()


def register():
    clear()
    for handlers, callback in ((bpy.app.handlers.undo_post,restore),
            (bpy.app.handlers.redo_post,restore),(bpy.app.handlers.load_pre,clear)):
        if callback not in handlers:handlers.append(callback)


def unregister():
    for handlers, callback in ((bpy.app.handlers.undo_post,restore),
            (bpy.app.handlers.redo_post,restore),(bpy.app.handlers.load_pre,clear)):
        if callback in handlers:handlers.remove(callback)
    clear()
