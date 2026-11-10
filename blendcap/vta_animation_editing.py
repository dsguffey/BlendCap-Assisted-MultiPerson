"""Blender-owned pose-frame selection for native output application.

This never changes the engine's plan/evidence. One choice applies separately to
each selected Character. Any keyed pose channel protects its whole frame.
"""
import hashlib
import math

import bpy

from .retarget.fcurves import _iter_action_fcurves

CHOICES = (
    ('CANCEL', 'Cancel', 'Do nothing'),
    ('MISSING', 'Generate animation in frames with no keys', 'Preserve every currently keyed pose'),
    ('EXISTING', 'Replace animation in frames with keys', 'Preserve entirely unkeyed frames'),
    ('ALL', 'Replace all animation, generate new animation', 'Overwrite all poses and gaps in the requested range'),
)


def pose_curves(obj):
    action=obj.animation_data.action if obj and obj.animation_data else None
    return [fc for fc in _iter_action_fcurves(action) if fc.data_path.startswith('pose.bones[')] if action else []


def pose_stamp(obj):
    # Handles and interpolation are intentional edits too. Ignore user Object
    # Location/Depth, cameras and current evaluated transforms.
    rows=[(fc.data_path,fc.array_index,fc.mute,fc.extrapolation,
           [(tuple(p.co),tuple(p.handle_left),tuple(p.handle_right),p.interpolation,
             p.handle_left_type,p.handle_right_type) for p in fc.keyframe_points]) for fc in pose_curves(obj)]
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def inspect(scene, project_id, source_id, ids, bounds):
    first,last=bounds
    if first>last:raise ValueError('Animation Start must not exceed End.')
    frames={};stamps={};states={}
    for cid in sorted(ids):
        rigs=[o for o in scene.objects if o.type=='ARMATURE' and o.get('vta_project_id')==project_id
              and o.get('vta_source_id')==source_id and o.get('vta_character_id')==cid]
        if len(rigs)>1:raise ValueError('Multiple generated rigs own this Character.')
        obj=rigs[0] if rigs else None
        keyed=set()
        for fc in pose_curves(obj):
            for p in fc.keyframe_points:
                f=float(p.co.x)
                if first<=f<=last:
                    if not math.isfinite(f) or f!=int(f):
                        raise ValueError('Pose keys at subframes need manual resolution before regeneration; no animation was changed.')
                    keyed.add(int(f))
        frames[cid]=sorted(keyed);stamps[cid]=pose_stamp(obj)
        states[cid]='EMPTY' if not keyed else 'FULL' if len(keyed)==last-first+1 else 'MIXED'
    return dict(version=1,bounds=list(bounds),frames=frames,stamps=stamps,states=states)


def request(context):
    from . import vta_review_ui as ui
    ui.require_loaded_context(context);context=ui._context_for_main_scene(context)
    info=ui.cached_scene_source(context)
    if not info:raise ValueError('Load a configured MoCap Scene first.')
    ids=tuple(ui.tracking_character_ids(context))
    if not ids:raise ValueError('Select at least one Character.')
    location=ui._location_from_context(context,allow_outside=True)
    offset=location.blender_frame-location.source_frame
    source=info[1];domain=(source['start_frame'],source['end_frame'])
    wm=context.window_manager
    selected=domain if wm.blendcap_vta_animation_scope=='WHOLE' else (
        wm.blendcap_vta_animation_start-offset,wm.blendcap_vta_animation_end-offset)
    from .vta_generation import plan_options
    plan_options(selected,domain)
    result=inspect(context.scene,info[0],source['source_id'],ids,tuple(f+offset for f in selected))
    result.update(project_id=info[0],source_id=source['source_id'])
    return result


def mixed(value):
    states=set(value['states'].values())
    return 'MIXED' in states or len(states)>1


def validate(value):
    if not isinstance(value,dict) or set(value)!={'version','bounds','frames','stamps','states','project_id','source_id','mode'}:
        raise ValueError('Invalid native animation editing request.')
    if value['version']!=1 or value['mode'] not in {'MISSING','EXISTING','ALL'}:
        raise ValueError('Unknown native animation editing mode.')
    bounds=value['bounds']
    if not isinstance(bounds,list) or len(bounds)!=2 or any(type(f) is not int for f in bounds) or bounds[0]>bounds[1]:
        raise ValueError('Invalid native animation editing range.')
    if not value['frames'] or set(value['frames'])!=set(value['stamps']) or set(value['frames'])!=set(value['states']):
        raise ValueError('Invalid native Character editing scope.')
    for cid,frames in value['frames'].items():
        if not isinstance(cid,str) or not isinstance(frames,list) or frames!=sorted(set(frames)) or any(type(f) is not int or not bounds[0]<=f<=bounds[1] for f in frames):
            raise ValueError('Invalid keyed pose frames.')
        if value['states'][cid] not in ('EMPTY','FULL','MIXED') or len(value['stamps'][cid])!=64:
            raise ValueError('Invalid native pose snapshot.')
    return value


def require_unchanged(scene,value):
    validate(value)
    current=inspect(scene,value['project_id'],value['source_id'],value['frames'],value['bounds'])
    if current['stamps']!=value['stamps']:
        raise ValueError('Native poses changed after generation was requested; existing animation retained. Start a new generation request.')


def selected_frames(value,cid):
    first,last=value['bounds'];existing=set(value['frames'][cid])
    if value['mode']=='EXISTING':return existing
    all_frames=set(range(first,last+1))
    return all_frames-existing if value['mode']=='MISSING' else all_frames


def copy_point(dst,src):
    dst.co=src.co
    dst.handle_left_type,dst.handle_right_type=src.handle_left_type,src.handle_right_type
    dst.handle_left,dst.handle_right=src.handle_left,src.handle_right
    for prop in ('interpolation','type','easing','amplitude','back','period'):
        setattr(dst,prop,getattr(src,prop))


def merge(previous,segment,value,cid):
    """Copy then replace whole pose frames, without inserted boundary guards.

    Deliberately retains native interpolation between anchors. Changing adjacent
    anchors can change evaluated in-between motion; no exact curve-segment claim.
    """
    from .vta_skeletal_output import _regenerable_curves
    old,_=_regenerable_curves(previous,None,True,allow_pose_edits=True)
    incoming={(f.data_path,f.array_index):f for f in _iter_action_fcurves(segment)} if segment else {}
    generated={(f.data_path,f.array_index):f for f in _iter_action_fcurves(old)
               if f.data_path.startswith('pose.bones[') or f.data_path in ('hide_viewport','hide_render')}
    if any(key not in incoming for key in generated) and segment:
        raise ValueError('Generation skeletal channels differ from previous output.')
    allowed=selected_frames(value,cid)
    result=old.copy()
    try:
        bag=result.layers[0].strips[0].channelbag(result.slots[0])
        output={(f.data_path,f.array_index):f for f in _iter_action_fcurves(result)}
        for key in set(generated)|set(incoming):
            if key not in output:output[key]=bag.fcurves.new(key[0],index=key[1])
            fc=output[key]
            for i in range(len(fc.keyframe_points)-1,-1,-1):
                if fc.keyframe_points[i].co.x in allowed:fc.keyframe_points.remove(fc.keyframe_points[i],fast=True)
            new=incoming.get(key)
            if new:
                for p in new.keyframe_points:
                    if p.co.x in allowed:copy_point(fc.keyframe_points.insert(p.co.x,p.co.y,options={'FAST'}),p)
            fc.update()
            # Inserting a neighbor may recalculate AUTO handles. Preserve the
            # original keyed control points/handles on every untouched frame.
            original=generated.get(key)
            if original:
                saved={p.co.x:p for p in original.keyframe_points if p.co.x not in allowed}
                for p in fc.keyframe_points:
                    if p.co.x in saved:copy_point(p,saved[p.co.x])
        return result
    except Exception:
        bpy.data.actions.remove(result);raise


def limit_new(action,value,cid):
    allowed=selected_frames(value,cid)
    first,last=value['bounds']
    for fc in _iter_action_fcurves(action):
        for i in range(len(fc.keyframe_points)-1,-1,-1):
            f=fc.keyframe_points[i].co.x
            if first<=f<=last and f not in allowed:fc.keyframe_points.remove(fc.keyframe_points[i],fast=True)
        fc.update()
