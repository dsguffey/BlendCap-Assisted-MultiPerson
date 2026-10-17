"""Explicit blank/configure/detach transactions. No inference or Scene observer writes."""
import math
from pathlib import Path
import uuid

import bpy

from . import vta_scene_session as session
from .vta_scene_timing import SCENE_TAG, SCENE_VERSION, source_rate
from ..blendcap_vta_review import ReviewLocation, companion_root_for_blend

from .vta_scene_characters import KEY as CHARACTER_MEMBERSHIP
BINDING = (SCENE_TAG, 'vta_project_id', 'vta_source_id', 'vta_source_video_sha256')
STRIP_TAG = 'vta_source_strip_version'


def _require_session(context):
    from . import vta_review_ui as ui
    scene = session.main_scene(context)
    from .vta_project_save import require_usable
    require_usable(scene)
    if ui._analysis_locked() or not session.owned(scene) or not session.matches(context):
        raise ValueError('Load this VTA Scene first; unavailable during analysis/generation.')
    return scene


def _presentation_snapshot():
    from . import vta_review_ui as ui
    names = ('_view', '_location', '_timeline', '_timeline_identity', '_project_signature',
             '_cache_signature', '_presentation_scene', '_active_review_scene', '_status',
             '_status_is_error', '_selection', '_review_key_selection', '_character_selections',
             '_repair_selections', '_last_assignment_target', '_display_backend','_scene_character_ids')
    values = {name: getattr(ui, name) for name in names}
    maps = {name: dict(getattr(ui, name)) for name in
            ('_validated_sources', '_scene_sessions', '_observer_tokens','_object_selection_tokens')}
    display = dict(ui._display.__dict__)
    def restore():
        for name, value in values.items(): setattr(ui, name, value)
        for name, value in maps.items():
            getattr(ui, name).clear(); getattr(ui, name).update(value)
        ui._display.__dict__.clear(); ui._display.__dict__.update(display)
        ui._rebuild_enum_items()
    return restore


def _forget(scene):
    from . import vta_review_ui as ui
    from .vta_presentation import pointer
    ui._activate_review_scene(None)
    ui._scene_sessions.pop(pointer(scene), None)
    ui._validated_sources.pop(pointer(scene), None)
    ui._display.clear()
    ui._observer_tokens.clear()


def configure(context, filename):
    """Stage host data + private-core state; publish state as the last fallible step."""
    from . import vta_review_ui as ui, properties
    scene = _require_session(context)
    if session.source_bound(scene):
        raise ValueError('Clear Source before choosing another clip; in-place replacement is disabled.')
    path = Path(bpy.path.abspath(filename)).resolve()
    if not path.is_file():
        raise ValueError('Choose an existing video file.')
    root = companion_root_for_blend(bpy.data.filepath)
    backend = ui._get_backend(context)
    core = backend._core
    state_path = root / 'project_state.json'
    if not state_path.exists() and root.exists() and any(root.iterdir()):
        raise ValueError('Companion data exists without its project state; restore it before configuring a source.')
    # Never reinterpret a corrupt/unsupported existing project as a fresh one.
    project = core.load_project_state(root) if state_path.exists() else core.ProjectState()
    original_bytes = state_path.read_bytes() if state_path.exists() else None
    had_editor = scene.sequence_editor is not None
    seq = scene.sequence_editor_create()
    before = (seq.active_strip, scene.render.fps, scene.render.fps_base,
              scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage,
              scene.frame_start, scene.frame_end, scene.frame_current, scene.blendcap_props.video_source,
              scene.render.use_sequencer)
    restore_presentation = _presentation_snapshot()
    membership_before=scene.get(CHARACTER_MEMBERSHIP)
    strip = None
    try:
        channel = max((s.channel for s in seq.strips), default=0) + 1
        strip = seq.strips.new_movie(path.stem, str(path), channel=channel, frame_start=1)
        fps, count = float(strip.fps), int(strip.frame_duration)
        element = strip.elements[0]
        width, height = int(element.orig_width), int(element.orig_height)
        if not math.isfinite(fps) or fps <= 0 or min(count, width, height) <= 0:
            raise ValueError('Video metadata is unavailable or invalid.')
        digest = backend._source_hash(path)
        matches = [s for s in project.to_dict()['sources'] if s.get('video_sha256') == digest]
        if len(matches) > 1:
            raise ValueError('Video matches multiple project sources; cannot bind ambiguously.')
        if matches:
            source = matches[0]
            if (not math.isclose(source['fps'], fps, rel_tol=1e-6, abs_tol=1e-3)
                    or (source['width'], source['height']) != (width, height)
                    or not 0 <= source['start_frame'] <= source['end_frame'] < count):
                raise ValueError('Video metadata differs from its existing source record.')
        else:
            source = core.SourceReference('source-' + uuid.uuid4().hex, fps, 0, count - 1,
                                          digest, width, height).to_dict()
            project.add_source(core.SourceReference(source['source_id'], fps, 0, count - 1,
                                                    digest, width, height))
        source_rate(source)  # Reject unsupported timing before publishing anything.
        scene[SCENE_TAG] = SCENE_VERSION
        scene['vta_project_id'] = project.project_id
        scene['vta_source_id'] = source['source_id']
        scene['vta_source_video_sha256'] = digest
        from . import vta_scene_characters
        vta_scene_characters.bind(scene,project,fresh=True)
        strip[STRIP_TAG] = 1
        strip['vta_project_id'], strip['vta_source_id'] = project.project_id, source['source_id']
        seq.active_strip = strip
        scene.render.resolution_x, scene.render.resolution_y = width, height
        scene.render.resolution_percentage = 100
        scene.frame_start, scene.frame_end = 1, count
        scene.frame_set(1)
        previous = properties._SETTING_DEFAULT_SOURCE
        properties._SETTING_DEFAULT_SOURCE = True
        try: scene.blendcap_props.video_source = 'VSE'
        finally: properties._SETTING_DEFAULT_SOURCE = previous
        scene.render.use_sequencer = before[-1]
        info = (project.project_id, source, ReviewLocation(root, path, 0, 1), strip)
        def publish():
            current = state_path.read_bytes() if state_path.exists() else None
            if current != original_bytes:
                raise ValueError('Project changed during source setup; retry without concurrent edits.')
            if not matches:
                core.save_project_state(project, root)
        session.load(context, scene, source_info=info, project=project, commit=publish)
        return scene
    except Exception:
        for key in BINDING: scene.pop(key, None)
        if membership_before is None:scene.pop(CHARACTER_MEMBERSHIP,None)
        else:scene[CHARACTER_MEMBERSHIP]=membership_before
        if strip is not None: seq.strips.remove(strip)
        (active, scene.render.fps, scene.render.fps_base,
         scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage,
         scene.frame_start, scene.frame_end, frame, mode, scene.render.use_sequencer) = before
        seq.active_strip = active
        previous = properties._SETTING_DEFAULT_SOURCE
        properties._SETTING_DEFAULT_SOURCE = True
        try: scene.blendcap_props.video_source = mode
        finally: properties._SETTING_DEFAULT_SOURCE = previous
        if not had_editor: scene.sequence_editor_clear()
        scene.frame_set(frame)
        restore_presentation()
        raise


def _remove_strip(scene, strip):
    scene.sequence_editor.strips.remove(strip)


def clear(context):
    """Unlink scoped objects, retain fake-user data/history; remove only the owned movie."""
    scene = _require_session(context)
    if not session.source_bound(scene):
        raise ValueError('This VTA Scene is already blank.')
    pid, sid = scene.get('vta_project_id'), scene.get('vta_source_id')
    if (type(scene.get(SCENE_TAG)) is not int or scene.get(SCENE_TAG) != SCENE_VERSION
            or not all(isinstance(scene.get(key), str) and scene.get(key) for key in BINDING[1:])):
        raise ValueError('Source binding metadata is incomplete or unsupported.')
    # New strips have explicit ownership: detach does not need the file/backend
    # to remain available. Legacy untagged strips require the original validator.
    tagged = [s for s in scene.sequence_editor.strips if STRIP_TAG in s
              and s.get('vta_project_id') == pid and s.get('vta_source_id') == sid] if scene.sequence_editor else []
    if tagged:
        if len(tagged) != 1 or tagged[0].get(STRIP_TAG) != 1 or tagged[0].type != 'MOVIE':
            raise ValueError('Source movie ownership is unsupported or ambiguous.')
        strip = tagged[0]
    else:
        strip = session.validate(context, scene)[3]
    targets = [o for o in scene.objects if o.get('vta_project_id') == pid
               and o.get('vta_source_id') == sid and
               ((o.type == 'EMPTY' and 'vta_scene_root_version' in o)
                or o.type == 'CAMERA'
                or (o.type == 'ARMATURE' and o.get('vta_character_id')))]
    if any(child not in targets for obj in targets for child in obj.children):
        raise ValueError('User content is parented to VTA output; detach it before Clear Source.')
    def collections(collection):
        return {collection} | set().union(*(collections(c) for c in collection.children))
    own_collections = collections(scene.collection)
    links = [(c, obj) for obj in targets for c in obj.users_collection if c in own_collections]
    other_collections = set().union(*(collections(s.collection) for s in bpy.data.scenes if s != scene))
    if any(c in other_collections for c, _ in links):
        raise ValueError('VTA content uses a collection shared with another Scene; clear would affect that Scene.')
    restore_presentation = _presentation_snapshot()
    metadata = {key: scene[key] for key in (*BINDING, session.OWNER_TAG, session.OWNER_ID) if key in scene}
    camera = scene.camera
    retained = set(targets)
    for obj in targets:
        if obj.data: retained.add(obj.data)
        for owner in (obj, obj.data) if obj.data else (obj,):
            ad = owner.animation_data
            if ad:
                if ad.action: retained.add(ad.action)
                retained.update(track_strip.action for track in ad.nla_tracks for track_strip in track.strips
                                if track_strip.action)
    fake = [(data, data.use_fake_user) for data in retained]
    removed = []
    try:
        # Upgrade legacy ownership only in this explicit command, not on Load/ticks.
        if session.OWNER_TAG not in scene:
            scene[session.OWNER_TAG], scene[session.OWNER_ID] = 1, uuid.uuid4().hex
        for data, _ in fake: data.use_fake_user = True
        for collection, obj in links:
            collection.objects.unlink(obj); removed.append((collection, obj))
        if camera in targets: scene.camera = None
        for key in BINDING: scene.pop(key, None)
        _forget(scene)
        # The final RNA removal is atomic. Nothing fallible follows it in Load.
        session.load(context, scene, commit=lambda: _remove_strip(scene, strip))
        return scene
    except Exception:
        for collection, obj in removed: collection.objects.link(obj)
        for data, value in fake: data.use_fake_user = value
        for key in (*BINDING, session.OWNER_TAG, session.OWNER_ID): scene.pop(key, None)
        for key, value in metadata.items(): scene[key] = value
        scene.camera = camera
        restore_presentation()
        raise
