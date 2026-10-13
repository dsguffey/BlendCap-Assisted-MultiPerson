"""Explicit host Scene loading. Never called to repair choices from a timer."""
import bpy
import uuid
from ..blendcap_vta_area_mode import MODE_KEY, ROLE_PREVIEW, ROLE_TIMELINE, configured_role_for, initialize_workspace_preset
from .vta_scene_timing import SCENE_TAG, SCENE_VERSION, owns_scene, match_source_rate

MODE = 'blendcap_vta_mode_enabled'
LAST = 'blendcap_vta_loaded_scene'
OWNER_TAG = 'vta_scene_owner_version'
OWNER_ID = 'vta_scene_owner_id'


def source_bound(scene):
    return bool(scene and any(key in scene for key in
        (SCENE_TAG, 'vta_project_id', 'vta_source_id', 'vta_source_video_sha256')))


def create_blank():
    """Host ownership only; never borrow a project/source identity."""
    scene = bpy.data.scenes.new('VTA - Untitled')
    scene[OWNER_TAG] = 1
    scene[OWNER_ID] = uuid.uuid4().hex
    return scene


def main_scene(context):
    return getattr(getattr(context, 'window', None), 'scene', None) or getattr(context, 'scene', None)


def owned(scene):
    try:
        host_owned = (scene is not None and type(scene.get(OWNER_TAG)) is int
                      and scene.get(OWNER_TAG) == 1 and isinstance(scene.get(OWNER_ID), str)
                      and bool(scene.get(OWNER_ID)))
        return host_owned or (scene is not None and type(scene.get(SCENE_TAG)) is int
                and scene.get(SCENE_TAG) == SCENE_VERSION
                and all(isinstance(scene.get(key), str) and scene.get(key)
                        for key in ('vta_project_id','vta_source_id','vta_source_video_sha256')))
    except ReferenceError:
        return False


def enabled(workspace):
    return bool(getattr(workspace, MODE, False))


def remembered(workspace):
    scene = getattr(workspace, LAST, None)
    return scene if owned(scene) else None


def matches(context, target=None):
    workspace = getattr(context, 'workspace', None)
    scene = target or remembered(workspace)
    if not enabled(workspace) or scene is None or main_scene(context) is not scene:
        return False
    previews = [(s,a) for s in getattr(workspace, 'screens', ()) for a in s.areas
                if configured_role_for(workspace,s,a) == ROLE_PREVIEW]
    if previews and workspace.sequencer_scene not in (None,scene):
        return False
    for screen in getattr(workspace, 'screens', ()):
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                if space.use_local_camera and space.camera != scene.camera:
                    return False
    return True


def warning(context):
    workspace = getattr(context, 'workspace', None)
    scene = remembered(workspace)
    if scene is None:
        return 'No VTA Scene loaded. Use Load VTA Scene / New VTA Scene.'
    if not enabled(workspace):
        return f'VTA Mode is off. Enter VTA Mode to restore {scene.name}.'
    active = main_scene(context)
    if active is not scene:
        return f'Loaded VTA Scene: {scene.name} · Active Scene: {active.name if active else "None"}. Load VTA Scene to restore.'
    if not matches(context):
        return 'VTA presentation differs from the loaded Scene. Load VTA Scene to restore.'
    return ''


class _Proxy:
    def __init__(self, owner, **values):
        self._owner = owner
        self.__dict__.update(values)
    def __getattr__(self, key):
        return getattr(self._owner, key)


def scene_context(context, scene, strip=None):
    if strip is None:
        return _Proxy(context, scene=scene)
    # Read-only validation does not change active strips or Scene properties.
    seq = _Proxy(scene.sequence_editor, active_strip=strip)
    props = _Proxy(scene.blendcap_props, video_source='VSE')
    return _Proxy(context, scene=_Proxy(scene, sequence_editor=seq, blendcap_props=props))


def validate(context, scene):
    from . import vta_review_ui as ui
    if not owned(scene):
        raise ValueError('Choose a metadata-owned VTA Scene; names are not ownership.')
    if not source_bound(scene):
        return None  # A blank owner needs no project, backend, clip or camera.
    backend = ui._get_backend(context)
    movies = []
    strips = tuple(scene.sequence_editor.strips) if scene.sequence_editor else ()
    tagged = [s for s in strips if 'vta_source_strip_version' in s
              and s.get('vta_project_id') == scene.get('vta_project_id')
              and s.get('vta_source_id') == scene.get('vta_source_id')]
    if tagged and (len(tagged) != 1 or tagged[0].get('vta_source_strip_version') != 1):
        raise ValueError('VTA source strip ownership is unsupported or ambiguous.')
    for strip in tagged or strips:
        if strip.type != 'MOVIE':
            continue
        path = ui._vse_source_path(strip)
        if path is not None and path.is_file() and backend._source_hash(path) == scene['vta_source_video_sha256']:
            movies.append(strip)
    if len(movies) != 1:
        raise ValueError('VTA source movie is missing or ambiguous; no Scene was loaded.')
    info = ui._timing_source(scene_context(context,scene,movies[0]))
    if not owns_scene(scene, info[0], info[1]):
        raise ValueError('VTA Scene metadata differs from the validated project/source.')
    return info


def workspace_for(context):
    from .vta_workspace import get_vta_workspace
    return get_vta_workspace(context)


def _activate_main_scene(context, scene):
    """The sole active-Scene write; explicit load transaction/rollback only."""
    context.window.scene = scene


def load(context, scene, *, source_info=None, project=None, commit=None):
    """Validate, configure once, then remember. Failed setup rolls back."""
    from . import vta_review_ui as ui, vta_workspace as ws, properties
    if ui._analysis_locked():
        raise ValueError('Unavailable while VTA analysis or generation is running.')
    # Prepared inputs are supplied only by the explicit Configure transaction.
    info = source_info if source_info is not None else validate(context,scene)
    if info is not None:
        pid, source, location, strip = info
    workspace = workspace_for(context)
    # Workspace-wide VSE selection cannot safely force ordinary VSE siblings.
    seqs = [(s,a) for s in workspace.screens for a in s.areas if a.type=='SEQUENCE_EDITOR']
    if any(configured_role_for(workspace,s,a) != ROLE_PREVIEW for s,a in seqs):
        raise ValueError('Use the dedicated VTA workspace; ordinary Sequencer editors will not be overridden.')
    if not seqs:
        raise ValueError('VTA workspace has no configured Preview editor.')
    if sum(a.type == 'VIEW_3D' for s in workspace.screens for a in s.areas) > 1:
        raise ValueError('Use the dedicated single-viewport VTA preset; additional ordinary 3D editors will not be overridden.')
    before = (context.window.scene,context.window.workspace,workspace.sequencer_scene,
              getattr(workspace,LAST),getattr(workspace,MODE),scene.sequence_editor.active_strip if scene.sequence_editor else None,
              scene.blendcap_props.video_source,scene.render.fps,scene.render.fps_base,scene.render.use_sequencer)
    maps = [(s,s.get(MODE_KEY)) for s in workspace.screens]
    view = ws.source_camera_view_state()
    displays = [(a.spaces.active,a.spaces.active.display_channel) for _,a in seqs]
    try:
        if info is not None:
            match_source_rate(scene,pid,source)
        previous = properties._SETTING_DEFAULT_SOURCE
        properties._SETTING_DEFAULT_SOURCE = True
        try:
            if info is not None:
                scene.blendcap_props.video_source = 'VSE'
        finally:
            properties._SETTING_DEFAULT_SOURCE = previous
        scene.render.use_sequencer = before[-1]
        if info is not None:
            scene.sequence_editor.active_strip = strip
        context.window.workspace = workspace
        _activate_main_scene(context,scene)
        setattr(workspace,MODE,True)
        for screen in workspace.screens:
            initialize_workspace_preset(workspace,screen)
        ui.ensure_vta_preview_context(context,scene)
        for _, area in seqs:
            area.spaces.active.display_channel = 0
        if info is None or scene.camera is None:
            for screen in workspace.screens:
                for area in screen.areas:
                    if area.type == 'VIEW_3D':
                        # With local-camera off, SpaceView3D.camera aliases
                        # Scene.camera. Clear only its local slot, never a user camera.
                        area.spaces.active.use_local_camera = True
                        area.spaces.active.camera = None
                        area.spaces.active.use_local_camera = False
        if info is not None:
            ws.configure_source_camera_views(scene,[workspace])
        # A loaded source can legitimately have no analysis yet. Its controls
        # remain available for Analyze; this is not an empty/malformed Scene.
        if info is not None:
            raw = (project or ui._get_backend(context)._core.load_project_state(location.companion_root)).to_dict()
            analyzed = bool(raw.get('active_analysis_revision_by_source',{}).get(source['source_id']))
            ui.record_scene_source(scene, info)
        else:
            analyzed = False
        if analyzed and source['start_frame'] <= location.source_frame <= source['end_frame']:
            if not ui.refresh(context,force=True,target_scene=scene, validated_info=(pid, source, location, strip)):
                raise ValueError(ui._status)
        else:
            ui._activate_review_scene(None)
            ui._status = ('VTA Scene loaded; analyze candidates or move inside source coverage.'
                          if info is not None else 'Blank VTA Scene; choose a clip in Video Source.')
            ui._status_is_error = False
        if commit is not None:
            commit()  # Last fallible step; source history is published only now.
        setattr(workspace,LAST,scene)  # Only after successful validation/setup.
        return scene
    except Exception:
        old_scene,old_workspace,pin,last,mode,active,source_mode,fps,base,sequencer = before
        workspace.sequencer_scene = pin  # Explicit command rollback, not enforcement.
        setattr(workspace,LAST,last);setattr(workspace,MODE,mode)
        if scene.sequence_editor:
            scene.sequence_editor.active_strip = active
        previous = properties._SETTING_DEFAULT_SOURCE
        properties._SETTING_DEFAULT_SOURCE = True
        try:
            scene.blendcap_props.video_source = source_mode
        finally:
            properties._SETTING_DEFAULT_SOURCE = previous
        scene.render.fps,scene.render.fps_base,scene.render.use_sequencer = fps,base,sequencer
        for screen,raw in maps:
            if raw is None: screen.pop(MODE_KEY,None)
            else: screen[MODE_KEY] = raw
        for owner,field,value in view: setattr(owner,field,value)
        for owner,value in displays: owner.display_channel=value
        context.window.workspace = old_workspace
        _activate_main_scene(context,old_scene)
        ui._activate_review_scene(None)
        raise


def enter(context):
    scene = remembered(workspace_for(context))
    if scene is None:
        raise ValueError('No VTA Scene loaded. Use Load VTA Scene / New VTA Scene.')
    return load(context,scene)


def exit_mode(context):
    from . import vta_review_ui as ui
    workspace = workspace_for(context)
    if enabled(workspace):
        # The dedicated canvas hides native channels using preset filters.
        # Exit supplies normal defaults, not an editor-history restoration.
        for screen in workspace.screens:
            for area in screen.areas:
                if configured_role_for(workspace, screen, area) == ROLE_TIMELINE:
                    filters = area.spaces.active.dopesheet
                    filters.show_only_errors = False
                    filters.show_only_selected = False
                    filters.show_summary = True
    setattr(workspace,MODE,False)
    ui._activate_review_scene(None)
    # No Scene/editor pointer, roles, animation or FPS restoration.
