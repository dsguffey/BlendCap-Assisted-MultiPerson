"""Explicit, selected-Character rotation finalization and root-only restoration.

Object transforms, limbs, visibility, review authority and the visible source
camera are never rewritten. No automatic Scene selection or inference.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import bpy
from mathutils import Matrix, Vector, Euler, Quaternion

from .retarget.fcurves import _iter_action_fcurves
from .vta_scene_root import supported_parent

FINAL = 'vta_camera_rotation_finalized'
BACKUP = 'vta_camera_rotation_backup'
TRACKS = 'vta_camera_rotation_tracks'
SCENE_ID = 'vta_camera_rotation_scene_id'
ROOT = 'pose.bones["Root"].'
_job = None
_output_modes = {} # Safe-observer snapshots; no file reads in draws or polls.


def observe_outputs(objects):
    """Existing output observer owns disk inspection, not UI eligibility polls."""
    from .vta_presentation import file_signature
    for obj in objects:
        path=Path(obj['vta_output_file']);key=str(path);stamp=file_signature(path)
        if key in _output_modes and _output_modes[key][0]==stamp:continue
        try:mode=(json.loads(path.read_text(encoding='utf-8')).get('reconstruction') or {}).get('mode')
        except (OSError,ValueError,AttributeError):mode=None
        _output_modes[key]=(stamp,mode)
    while len(_output_modes)>128:_output_modes.pop(next(iter(_output_modes)))


def observed_output_mode(obj):
    return _output_modes.get(str(obj.get('vta_output_file','')),(None,None))[1]


def busy():
    return _job is not None


def require_source_aligned(scene, character_ids, project_id=None, source_id=None):
    if isinstance(scene,bpy.types.Scene):
        project_id = project_id or scene.get('vta_project_id')
        source_id = source_id or scene.get('vta_source_id')
    ids = set(character_ids)
    matches = [o for o in getattr(scene,'objects',()) if o.type == 'ARMATURE' and o.get(FINAL)
        and o.get('vta_character_id') in ids
        and (project_id is None or o.get('vta_project_id') == project_id)
        and (source_id is None or o.get('vta_source_id') == source_id)]
    if matches:
        raise ValueError('Camera cleanup finalized '+', '.join(o.name for o in matches)+
            '. Use Animation Cleanup → Restore Source Alignment before tracking, generation or repair.')


def root_curves(action):
    return [fc for fc in _iter_action_fcurves(action) if fc.data_path in
            {ROOT+'location',ROOT+'rotation_euler',ROOT+'rotation_quaternion'}]


def action_signature(action, *, root_only=False):
    curves = root_curves(action) if root_only else _iter_action_fcurves(action)
    values = [(fc.data_path,fc.array_index,fc.mute,fc.extrapolation,
        [(tuple(p.co),tuple(p.handle_left),tuple(p.handle_right),p.handle_left_type,
          p.handle_right_type,p.interpolation,p.type,p.easing,p.amplitude,p.back,p.period)
         for p in fc.keyframe_points]) for fc in curves]
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


def targets(context, info=None, *, allow_partial=False):
    from . import vta_review_ui as ui
    ui.require_loaded_context(context)
    info = info or ui.cached_scene_source(context)
    if info is None:
        raise ValueError('Load a configured VTA Scene first.')
    ids = ui.tracking_character_ids(context)
    if not ids:
        raise ValueError('Select one or more Character rows in the VTA timeline.')
    result=[]
    for identity in ids:
        owners = [o for o in context.scene.objects if o.type == 'ARMATURE'
            and o.get('vta_character_id') == identity and o.get('vta_project_id') == info[0]
            and o.get('vta_source_id') == info[1]['source_id']]
        if len(owners) != 1:
            if allow_partial:continue
            raise ValueError('Generate exactly one owned rig for each selected Character first.')
        result.append(owners[0])
    if not result:raise ValueError('Generate animation for at least one selected Character first.')
    return tuple(result),info


def availability(context,action='REMOVE'):
    """Cheap read-only UI gate. Never prepare a solve or load project files.

    Canonical file/provenance checks still run before starting work.
    Metadata/rig/selection failures already visible to the UI disable the button.
    """
    from . import vta_review_ui as ui
    if not ui._source_actions_available(context):return False,'Load a configured VTA Scene; wait for any running operation.'
    try:objects,info=targets(context,allow_partial=True)
    except (ValueError,ui.VtaReviewUiError) as exc:return False,str(exc)
    cameras=[o for o in context.scene.objects if o.type=='CAMERA' and o.get('vta_project_id')==info[0]
        and o.get('vta_source_id')==info[1]['source_id']]
    reasons=[]
    for obj in objects:
        try:
            if len(obj.users_scene)!=1:raise ValueError('Selected rigs must belong to this Scene only.')
            if action=='RESTORE':
                if not obj.get(FINAL) or not isinstance(obj.get(BACKUP),bpy.types.Action):
                    raise ValueError('Select a finalized Character with source-alignment recovery data.')
                try:metadata=json.loads(obj[FINAL])
                except (ValueError,TypeError):metadata={}
                if not isinstance(metadata,dict) or metadata.get('version')!=1 or not metadata.get('original_root_sha256'):
                    raise ValueError('Source-alignment recovery metadata is unavailable.')
            else:
                if obj.get(FINAL):raise ValueError('Restore Source Alignment before another cleanup.')
                if obj.get('vta_output_stale'):raise ValueError('Generate current reviewed animation before cleanup.')
                if observed_output_mode(obj)!='FIRST':
                    raise ValueError('Cleanup needs Sample first frame animation; refresh or generate it first.')
                if len(cameras)!=1:raise ValueError('Exactly one calibrated source camera is required.')
                stamp=obj.get('vta_reconstruction_fingerprint')
                if not stamp or stamp=='legacy-per-frame-intrinsics' or cameras[0].data.get('vta_reconstruction_fingerprint')!=stamp:
                    raise ValueError('Generate compatible current camera-calibrated animation first.')
            validate_rig(obj)
            return True,''
        except (ValueError,AttributeError) as exc:reasons.append(str(exc))
    return False,next(iter(reasons),'Select an eligible generated Character.')


def target(context, info=None):
    """Single-rig helper retained for callers requiring one target."""
    objects,info=targets(context,info)
    if len(objects)!=1:raise ValueError('Select exactly one Character row in the VTA timeline.')
    return objects[0],info


def validate_rig(obj):
    from .vta_skeletal_output import _regenerable_curves
    _regenerable_curves(obj,None,True)
    if (obj.library or obj.override_library or obj.animation_data.action_blend_type != 'REPLACE'
            or obj.animation_data.action_influence != 1.):
        raise ValueError('Cleanup requires a local generated rig with ordinary replacement Action blending.')
    root = obj.pose.bones.get('Root')
    if root is None or root.parent is not None or not supported_parent(obj):
        raise ValueError('Cleanup requires the generated unparented Root bone and supported SceneRoot parent.')
    if root.rotation_mode not in {'QUATERNION','XYZ','XZY','YXZ','YZX','ZXY','ZYX'}:
        raise ValueError('Unsupported generated Root rotation mode.')
    suffix,size = ('rotation_quaternion',4) if root.rotation_mode == 'QUATERNION' else ('rotation_euler',3)
    curves = {(fc.data_path,fc.array_index):fc for fc in root_curves(obj.animation_data.action)}
    if (not root.bone.use_local_location or any(fc.mute or (fc.group and fc.group.mute)
            or fc.data_path not in {ROOT+'location',ROOT+suffix} for fc in curves.values())):
        raise ValueError('Cleanup requires active, ordinary generated Root channels.')
    if any((ROOT+field,axis) not in curves for field,count in (('location',3),(suffix,size)) for axis in range(count)):
        raise ValueError('Generated Root location/rotation channels are incomplete.')
    frames = {float(p.co.x) for fc in curves.values() for p in fc.keyframe_points}
    if not frames or any(not math.isfinite(f) or f != int(f) for f in frames):
        raise ValueError('Cleanup requires integer-frame generated Root keys.')
    return root,curves,(int(min(frames)),int(max(frames)))


def apply_solve(scene, obj, solve, core):
    """Stage an Action; commit root-only bake, recovery pointer and solve together."""
    if obj.get(FINAL):
        raise ValueError('Character already finalized. Restore Source Alignment first.')
    if len(obj.users_scene) != 1 or scene.objects.get(obj.name) != obj:
        raise ValueError('Cleanup cannot modify a rig shared with another Scene.')
    request = solve['request']
    if (request['scene_id'] != scene.get(SCENE_ID) or request['project_id'] != obj.get('vta_project_id')
            or request['source_id'] != obj.get('vta_source_id')):
        raise ValueError('Camera solve belongs to a different Scene/Character source.')
    rotations = core.validate_solve(solve,request)
    root,curves,(first,last) = validate_rig(obj)
    offset = request['offset']
    if first-offset < request['range'][0] or last-offset > request['range'][1]:
        raise ValueError('Camera solve does not cover all generated Root animation.')
    # The first source frame defines identity, not an estimated cleanup. If no
    # supported later frame overlaps this rig, leave both Action and state alone.
    if not any(solve['rotations'][frame-offset-request['range'][0]] is not None
               and frame-offset > request['range'][0] for frame in range(first,last+1)):
        return None
    old = obj.animation_data.action
    result = old.copy()
    original_slot = obj.animation_data.action_slot
    tracks_before = scene.get(TRACKS)
    root_matrix = root.bone.matrix_local.copy()
    # Canonical VTA/CV basis. Pure rotation about the fixed source-camera origin
    # in armature coordinates. User object transforms are applied AFTER this
    # generated pose, exactly as before; no object curve is conjugated/replaced.
    basis = Matrix(((1,0,0),(0,0,1),(0,-1,0)))
    output = {(fc.data_path,fc.array_index):fc for fc in root_curves(result)}
    rotation_field,size = ('rotation_quaternion',4) if root.rotation_mode == 'QUATERNION' else ('rotation_euler',3)
    scale_curves = {(fc.data_path,fc.array_index):fc for fc in _iter_action_fcurves(old)}
    values,preserved = [],[]
    compatible_euler,compatible_quaternion = None,None
    try:
        for frame in range(first,last+1):
            index = frame-offset-request['range'][0]
            if index == 0 or solve['rotations'][index] is None:
                # Preserve source channels verbatim, including raw Euler/quaternion
                # values; never decompose or normalize a skipped frame.
                preserved.append((frame,
                    tuple(curves[ROOT+'location',i].evaluate(frame) for i in range(3)),
                    tuple(curves[ROOT+rotation_field,i].evaluate(frame) for i in range(size))))
                continue
            location = Vector([curves[ROOT+'location',i].evaluate(frame) for i in range(3)])
            raw = [curves[ROOT+rotation_field,i].evaluate(frame) for i in range(size)]
            q = Quaternion(raw) if size == 4 else Euler(raw,root.rotation_mode).to_quaternion()
            scale = Vector([scale_curves[ROOT+'scale',i].evaluate(frame)
                            if (ROOT+'scale',i) in scale_curves else root.scale[i] for i in range(3)])
            r = Matrix(rotations[index].tolist())
            correction = (basis @ r.transposed() @ basis.inverted()).to_4x4()
            matrix = root_matrix.inverted() @ correction @ root_matrix @ Matrix.LocRotScale(location,q,scale)
            loc,rot,new_scale = matrix.decompose()
            if max(abs(a-b) for a,b in zip(scale,new_scale)) > 1e-5:
                raise ValueError('Root scale/shear cannot be preserved safely.')
            if size == 3:
                euler = rot.to_euler(root.rotation_mode,compatible_euler) if compatible_euler else rot.to_euler(root.rotation_mode)
                angles = tuple(euler); compatible_euler = euler
            else:
                if compatible_quaternion and rot.dot(compatible_quaternion) < 0:
                    rot.negate()
                angles = tuple(rot); compatible_quaternion = rot
            values.append((frame,tuple(loc),angles))
        # The staged Action already contains every original source key. Replace
        # only supported frames; gaps retain their original CONSTANT animation.
        for frame,loc,angles in values+preserved:
            for field,components in (('location',loc),(rotation_field,angles)):
                for axis,value in enumerate(components):
                    output[ROOT+field,axis].keyframe_points.insert(frame,value,options={'FAST'}).interpolation = 'CONSTANT'
        for fc in output.values():
            fc.update()
        tracks = json.loads(tracks_before or '{}')
        tracks[solve['request_sha256']] = solve
        metadata = dict(version=1,solve_sha256=solve['sha256'],scene_id=request['scene_id'],
            character_id=obj.get('vta_character_id'),
            original_root_sha256=action_signature(old,root_only=True),
            corrected_frames=len(values),skipped_frames=[row['frame']+offset for row in solve['skipped']
                if first <= row['frame']+offset <= last])
        metadata['component_review']=[dict(frame=row['frame']+offset,source_frame=row['frame'],
            uncorrected=[name for name,passed in row['accepted'].items() if not passed])
            for row in solve['components'] if first<=row['frame']+offset<=last and not all(row['accepted'].values())]
        # The old Action is a source-aligned recovery snapshot, not a second rig.
        obj.animation_data.action = result
        obj.animation_data.action_slot = result.slots[0]
        obj[BACKUP] = old
        # The ID-property Action pointer is itself a persistent user. Do not
        # change fake-user flags on an Action possibly shared by another rig.
        obj[FINAL] = json.dumps(metadata)
        scene[TRACKS] = json.dumps(tracks)
        return result
    except Exception:
        obj.animation_data.action = old
        obj.animation_data.action_slot = original_slot
        obj.pop(BACKUP,None);obj.pop(FINAL,None)
        if tracks_before is None: scene.pop(TRACKS,None)
        else: scene[TRACKS] = tracks_before
        if result.users == 0: bpy.data.actions.remove(result)
        raise


def restore_source(obj):
    """Restore only saved generated Root channels; keep later object/limb edits."""
    if len(obj.users_scene) != 1:
        raise ValueError('Restore cannot modify a rig shared with another Scene.')
    metadata = json.loads(obj.get(FINAL,'null'))
    backup = obj.get(BACKUP)
    if not metadata or metadata.get('version') != 1 or not isinstance(backup,bpy.types.Action):
        raise ValueError('Source-aligned recovery Action is missing; restore the saved pre-cleanup file.')
    if action_signature(backup,root_only=True) != metadata['original_root_sha256']:
        raise ValueError('Recovery Root animation changed. Restore the saved pre-cleanup file.')
    validate_rig(obj)
    old = obj.animation_data.action
    result = old.copy()
    original_slot = obj.animation_data.action_slot
    try:
        source = {(fc.data_path,fc.array_index):fc for fc in root_curves(backup)}
        current = {(fc.data_path,fc.array_index):fc for fc in root_curves(result)}
        if set(source) != set(current):
            raise ValueError('Root channels changed after cleanup; cannot restore safely.')
        for key,fc in current.items():
            original = source[key]
            while fc.keyframe_points:
                fc.keyframe_points.remove(fc.keyframe_points[-1],fast=True)
            fc.keyframe_points.add(len(original.keyframe_points))
            for a,b in zip(fc.keyframe_points,original.keyframe_points):
                a.co = b.co
                a.handle_left_type,a.handle_right_type = b.handle_left_type,b.handle_right_type
                a.handle_left,a.handle_right = b.handle_left,b.handle_right
                for prop in ('interpolation','type','easing','amplitude','back','period'):
                    setattr(a,prop,getattr(b,prop))
            fc.update()
        obj.animation_data.action = result
        obj.animation_data.action_slot = result.slots[0]
        obj.pop(FINAL,None);obj.pop(BACKUP,None)
    except Exception:
        obj.animation_data.action = old;obj.animation_data.action_slot = original_slot
        if result.users == 0: bpy.data.actions.remove(result)
        raise


def _rotation_core(backend):
    import importlib
    try:
        return importlib.import_module(backend._core.__name__+'.camera_rotation')
    except ImportError as exc:
        raise ValueError('Update the private VTA Core to use rotation cleanup.') from exc


def prepare(context, obj=None):
    from . import vta_review_ui as ui
    from .vta_skeletal_output import _hash
    from ..blendcap_vta_generation_ipc import preflight_generation_paths
    if obj is None:obj,info = target(context)
    else:info=ui.cached_scene_source(context)
    if len(obj.users_scene)!=1:
        raise ValueError('This rig is shared with another Scene. Cleanup requires an independent VTA rig.')
    if obj.get(FINAL):
        raise ValueError('Character already finalized; Restore Source Alignment first.')
    if obj.get('vta_output_stale'):
        raise ValueError('Generate the current reviewed animation before camera cleanup.')
    _,_,bounds = validate_rig(obj)
    backend = ui._get_backend(context)
    project,cache,revision = backend._load_context(info[2])
    if (obj.get('vta_project_file_hash') != _hash(info[2].companion_root/'project_state.json')
            or obj.get('vta_cache_file_hash') != _hash(info[2].companion_root/'analysis'/(revision+'.json'))):
        raise ValueError('Review or analysis changed since this animation was generated. Generate it before cleanup.')
    if not context.scene.get(SCENE_ID):context.scene[SCENE_ID]=uuid4().hex
    scene_id=context.scene[SCENE_ID]
    if sum(s.get(SCENE_ID) == scene_id for s in bpy.data.scenes) != 1:
        raise ValueError('Copied Scene camera-cleanup identity is ambiguous; use an independent VTA Scene.')
    cameras = [o for o in context.scene.objects if o.type == 'CAMERA'
        and o.get('vta_project_id') == info[0] and o.get('vta_source_id') == info[1]['source_id']]
    if len(cameras) != 1:
        raise ValueError('Exactly one calibrated source camera is required for cleanup.')
    camera = cameras[0]
    output = json.loads(Path(obj['vta_output_file']).read_text(encoding='utf-8'))
    reconstruction = output.get('reconstruction') or {}
    if reconstruction.get('mode') != 'FIRST':
        raise ValueError('Rotation cleanup requires current fixed-initial (Sample first frame) animation. Generate it first.')
    calibration = backend._core.reconstruction_fingerprint(reconstruction,info[1]['video_sha256'])
    if (obj.get('vta_reconstruction_fingerprint') != calibration
            or camera.data.get('vta_reconstruction_fingerprint') != calibration):
        raise ValueError('Character/source-camera calibration differs; regenerate before cleanup.')
    focal = float(reconstruction['calibration']['intrinsics'][0][0])
    offset = info[2].blender_frame-info[2].source_frame
    first,last = cache.source.start_frame,bounds[1]-offset
    if bounds[0]-offset < first or last > cache.source.end_frame:
        raise ValueError('Generated Root coverage is outside the source.')
    preflight_generation_paths(info[2].companion_root)
    core = _rotation_core(backend)
    request = dict(algorithm=core.ALGORITHM,scene_id=scene_id,project_id=info[0],
        source_id=info[1]['source_id'],video_sha256=info[1]['video_sha256'],
        cache_sha256=_hash(info[2].companion_root/'analysis'/(revision+'.json')),
        calibration=calibration,width=info[1]['width'],height=info[1]['height'],focal_px=focal,
        range=[first,last],offset=offset,fps=info[1]['fps'],
        source_camera_matrix=[list(row) for row in camera.matrix_world])
    return obj,info,backend,core,request,revision


def prepare_selection(context,objects):
    """Per-Character preflight, one union-range Scene solve, no partial fit math."""
    ready=[];issues=[]
    for obj in objects:
        try:ready.append(prepare(context,obj))
        except Exception as exc:issues.append((obj.name,str(exc)))
    if not ready:raise ValueError('\n'.join(name+': '+reason for name,reason in issues))
    _,info,backend,core,request,revision=ready[0]
    request={**request,'range':list(request['range'])}
    for _,_,_,_,other,_ in ready[1:]:
        if {k:v for k,v in request.items() if k!='range'}!={k:v for k,v in other.items() if k!='range'}:
            raise ValueError('Selected Characters do not share one Scene/source calibration.')
        request['range'][1]=max(request['range'][1],other['range'][1])
    return tuple(row[0] for row in ready),info,backend,core,request,revision,issues


def apply_selection(scene,objects,solve,core,issues=(),log=None):
    """Independent rig transactions: a refusal never prevents another rig."""
    issues=list(issues);done=[]
    for obj in objects:
        try:
            apply_solve(scene,obj,solve,core)
            done.append(obj)
        except Exception as exc:issues.append((obj.name,str(exc)))
    scene.frame_set(scene.frame_current)
    if len(done)==1 and not issues:
        completion_feedback(scene,done[0],solve,log)
    else:
        details=[]
        for obj in done:
            metadata=json.loads(obj.get(FINAL,'null'))
            details.append(obj.name+': '+(f"{metadata['corrected_frames']} corrected; "
                f"{len(metadata['component_review'])} frames need component review. Restore Source Alignment before VTA work."
                if metadata else 'No reliable frames; unchanged.'))
            if metadata:
                details.extend(f"  Blender {row['frame']} / source {row['source_frame']}: uncorrected "
                    +', '.join(row['uncorrected']) for row in metadata['component_review'])
        details.extend(name+': unchanged: '+reason for name,reason in issues)
        _finish_feedback(f'Camera cleanup: {sum(bool(o.get(FINAL)) for o in done)} Characters finalized; '
            f'{len(issues)} not processed. Review per-Character uncertain frames.',
            '\n'.join(details),error=not done,log=log,scene=scene)
    return bool(done)


def _finish_feedback(message,details='',error=False,log=None,scene=None):
    from . import vta_review_ui as ui
    context=bpy.context
    if scene is not None:
        try: scene.name
        except ReferenceError: return # Target was deleted; never pollute another Scene.
        context=SimpleNamespace(scene=scene,window=SimpleNamespace(scene=scene),window_manager=bpy.context.window_manager)
    ui.set_operation_feedback('camera_cleanup',message,details,context=context,error=error,
        log=log or (_job['log'] if _job else None))
    from . import vta_invalidation
    vta_invalidation.request_redraw()


def completion_feedback(scene,obj,solve,log=None):
    """Same non-blocking outcome for cached and freshly computed trajectories."""
    _,_,(first,last) = validate_rig(obj)
    offset = solve['request']['offset']
    skipped = [row for row in solve['skipped'] if first <= row['frame']+offset <= last]
    finalized = bool(obj.get(FINAL))
    partial=[row for row in solve['components'] if first<=row['frame']+offset<=last
        and any(row['accepted'].values()) and not all(row['accepted'].values())]
    if not finalized:
        primary = 'Warning: no reliable cleanup frames; animation unchanged.'
    elif skipped or partial:
        count = json.loads(obj[FINAL])['corrected_frames']
        primary = f'Warning: cleanup on {count} frames; {len(partial)} roll-only; skipped {len(skipped)}. Character finalized.'
    else:
        primary = 'Camera rotation removed. Character finalized.'
    details = 'Rotation only; translation/depth remain. '
    if finalized:
        details += 'Restore Source Alignment before further VTA work. '
    if partial:
        details+=('Pan/tilt were not corrected on roll-only frames. Changing accepted components may jump; inspect boundaries.\n'
            'Roll-only Blender frames: '+', '.join(str(row['frame']+offset) for row in partial)+'\n')
    if skipped:
        details += ('Skipped frames retain their original source-aligned animation. '
            'Corrected/uncorrected boundaries may jump; inspect them during QA.\n'
            'Skipped Blender frames (source frame):\n'+ '\n'.join(
                f"{row['frame']+offset} ({row['frame']}): {row['reason']}" for row in skipped))
    _finish_feedback(primary,details,log=log,scene=scene)


def cancel_job():
    global _job
    if _job is not None:
        job,_job = _job,None
        job['process'].terminate() if job['process'].poll() is None else None
        # Unique request directory is retained as diagnostic output. No other
        # request's markers or files are consumed/deleted.
    if bpy.app.timers.is_registered(poll_job): bpy.app.timers.unregister(poll_job)


def poll_job():
    global _job
    job = _job
    if job is None: return None
    if job['process'].poll() is None: return .2
    _job = None
    try:
        if job['cancelled']:
            _finish_feedback('Camera cleanup cancelled; animation unchanged.',scene=job.get('scene'))
            return None
        if job['process'].returncode != 0:
            lines=job['log'].read_text(encoding='utf-8',errors='replace').splitlines()
            # Progress is not a rejection reason. Keep the actual worker failure
            # prominent; the full request log remains available via Go to Log.
            failure=next((line for line in reversed(lines) if line.startswith('Rotation cleanup failed:')),None)
            raise ValueError(failure or f"Camera cleanup worker exited with code {job['process'].returncode}. See Go to Log.")
        scene = job['scene']
        if bpy.context.scene != scene or not bpy.context.window or bpy.context.window.scene != scene:
            raise ValueError('Active Scene changed during cleanup; no animation was modified. Retry in the loaded VTA Scene.')
        for obj,action,signature in job['snapshots']:
            if obj.animation_data.action != action or action_signature(action) != signature:
                raise ValueError('Animation changed during cleanup; no compensation was published.')
        selected,_=targets(bpy.context,allow_partial=True)
        objects,info,backend,core,request,_,issues=prepare_selection(bpy.context,selected)
        if selected!=job['selected'] or objects!=job['objects'] or request != job['request']:
            raise ValueError('Selection, source or calibration changed during cleanup. Retry.')
        solve = json.loads((job['directory']/'solve.json').read_text(encoding='utf-8'))
        apply_selection(scene,objects,solve,core,issues,job['log'])
    except Exception as exc:
        _finish_feedback('Couldn’t remove camera rotation; animation unchanged.',str(exc),True,job['log'],job.get('scene'))
    return None


class BLENDCAP_OT_vta_camera_cleanup(bpy.types.Operator):
    bl_idname = 'blendcap.vta_camera_cleanup'
    bl_label = 'Remove Camera Movement'
    bl_description = 'Finalize selected Characters by removing the same estimated Scene camera ROTATION; depth/translation remain'
    action: bpy.props.EnumProperty(items=(('REMOVE','Remove Camera Movement',''),
        ('RESTORE','Restore Source Alignment',''),('CANCEL','Cancel Camera Cleanup','')),default='REMOVE')

    @classmethod
    def poll(cls,context):
        if busy():return True # Cooperative Cancel remains available.
        remove,reason=availability(context)
        restore,_=availability(context,'RESTORE') if not remove else (False,'')
        if not (remove or restore):cls.poll_message_set(reason)
        return remove or restore

    def invoke(self,context,event):
        from . import vta_review_ui as ui
        try:
            if self.action == 'CANCEL': return self.execute(context)
            enabled,reason=availability(context,self.action)
            if not enabled:
                self.report({'WARNING'},reason);return {'CANCELLED'}
            if ui._analysis_locked(): raise ValueError('Wait for the running VTA operation.')
            objects,_ = targets(context,allow_partial=True)
            self._confirmed_objects = objects
            if self.action=='REMOVE':return self.execute(context)
            return context.window_manager.invoke_props_dialog(self,width=540,confirm_text='Restore Source Alignment')
        except Exception as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}

    def draw(self,context):
        self.layout.label(text='Restore the saved source-aligned Root animation?')
        self.layout.label(text='Current generated Root edits will be replaced.')
        self.layout.label(text='Object Mode edits and limb animation are retained.')

    def execute(self,context):
        global _job
        from . import vta_review_ui as ui
        from ..blendcap_vta_generation_ipc import write_new_json,filesystem_path
        if self.action == 'CANCEL':
            if _job:
                ( _job['directory']/'cancel').touch(exist_ok=True)
                _job['cancelled'] = True
            return {'FINISHED'}
        try:
            enabled,reason=availability(context,self.action)
            if not enabled:raise ValueError(reason)
            if ui._analysis_locked(): raise ValueError('Wait for the running VTA operation.')
            selected,info = targets(context,allow_partial=True)
            confirmed=getattr(self,'_confirmed_objects',None)
            if self.action=='RESTORE' and confirmed!=selected:
                raise ValueError('Use Restore Source Alignment confirmation; select the same Characters.')
            if self.action=='REMOVE' and confirmed is not None and confirmed!=selected:
                raise ValueError('Character selection changed; retry cleanup.')
            if self.action == 'RESTORE':
                restored=[];issues=[]
                for obj in selected:
                    try:restore_source(obj);restored.append(obj)
                    except Exception as exc:issues.append(obj.name+': '+str(exc))
                context.scene.frame_set(context.scene.frame_current)
                _finish_feedback(f'Source alignment restored for {len(restored)} Characters; '
                    f'{len(issues)} unchanged.','\n'.join(issues),error=not restored)
                return {'FINISHED'} if restored else {'CANCELLED'}
            objects,info,backend,core,request,revision,issues = prepare_selection(context,selected)
            tracks = json.loads(context.scene.get(TRACKS,'{}'))
            cached = tracks.get(core.digest(request))
            if cached is not None:
                return ({'FINISHED'} if apply_selection(context.scene,objects,cached,core,issues) else {'CANCELLED'})
            directory = info[2].companion_root/'reconstruction'/('rotation-'+uuid4().hex[:16])
            directory.mkdir(parents=True,exist_ok=False)
            write_new_json(directory/'request.json',dict(identity=request,revision=revision,
                companion_root=str(info[2].companion_root),video=str(info[2].source_path)))
            log = directory/'worker.log'
            bundle = Path(__file__).resolve().parents[1]
            python = Path(ui._python_exe())
            if not python.is_file(): raise ValueError('Install the BlendCap runtime before camera cleanup.')
            command = [str(python),str(bundle/'blendcap_vta_rotation_worker.py'),
                       '--core-root',str(ui._backend_root),'--request',str(directory/'request.json')]
            with open(filesystem_path(log),'x',encoding='utf-8') as stream:
                process = subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,
                    cwd=str(bundle),env={**os.environ,'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'},
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            _job = dict(process=process,directory=directory,log=log,objects=objects,selected=selected,scene=context.scene,
                snapshots=tuple((obj,obj.animation_data.action,action_signature(obj.animation_data.action)) for obj in objects),
                request=request,cancelled=False)
            bpy.app.timers.register(poll_job,first_interval=.2)
            _finish_feedback('Solving background camera rotation…','Source-aligned animation remains unchanged until successful publication.')
            return {'FINISHED'}
        except Exception as exc:
            _finish_feedback('Couldn’t remove camera rotation; animation unchanged.',str(exc),True)
            self.report({'ERROR'},str(exc));return {'CANCELLED'}


def draw_cleanup(layout,context):
    from . import vta_review_ui as ui
    layout.label(text='Animation Cleanup')
    layout.label(text='Rotation only; translation/depth remain unchanged.')
    layout.label(text='Roll may pass alone; uncertain components need review.')
    layout.label(text='Finalizes selected Characters; Restore to resume VTA work.')
    enabled,reason=availability(context)
    controls=layout.column();controls.enabled=enabled
    controls.operator('blendcap.vta_camera_cleanup',text='Remove Camera Movement').action='REMOVE'
    if not enabled:layout.label(text=reason,icon='INFO')
    try:
        objects,_ = targets(context,allow_partial=True)
        if any(obj.get(FINAL) for obj in objects):
            layout.label(text='Selection includes finalized Characters',icon='CHECKMARK')
            restore=layout.row();restore.enabled=availability(context,'RESTORE')[0]
            restore.operator('blendcap.vta_camera_cleanup',text='Restore Source Alignment').action = 'RESTORE'
    except (ValueError,ui.VtaReviewUiError):
        pass
    if busy(): layout.operator('blendcap.vta_camera_cleanup',text='Cancel Camera Cleanup').action = 'CANCEL'
    ui.draw_operation_feedback(layout,context,'camera_cleanup')
