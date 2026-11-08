"""Non-animatable VTA bookkeeping and explicit, narrowly validated legacy repair."""
import math

import bpy

FIELDS = ('vta_output_stale', 'vta_camera_registration_version')


def register():
    # Native transforms + custom-properties keying skips RNA fields without
    # ANIMATABLE. Keep the same IDProperty keys and bracket read/write contract.
    bpy.types.Object.vta_output_stale = bpy.props.BoolProperty(
        name='MoCap Output Stale', default=False, options={'HIDDEN'})
    bpy.types.Object.vta_camera_registration_version = bpy.props.IntProperty(
        name='MoCap Camera Registration Version', default=1, options={'HIDDEN'})


def unregister():
    for name in reversed(FIELDS):
        delattr(bpy.types.Object, name)


def repair_plan(obj):
    """Read-only. Only constant known bookkeeping curves on a local owned rig."""
    from .retarget.fcurves import _iter_action_fcurves
    if (obj.type != 'ARMATURE' or not all(obj.get(k) for k in
            ('vta_project_id','vta_source_id','vta_character_id','vta_output_file'))
            or obj.get('vta_camera_registration_version') != 1):
        raise ValueError('Metadata repair requires a version-1 generated Character')
    data=obj.animation_data
    if (not obj.is_editable or len(obj.users_scene)!=1 or not data or not data.action
            or data.nla_tracks or data.drivers or not data.action.is_editable
            or data.action.users!=1 or len(data.action.slots)!=1):
        raise ValueError('Metadata repair requires local single-user animation without NLA/drivers')
    curves=[]
    for curve in _iter_action_fcurves(data.action):
        name=next((k for k in FIELDS if curve.data_path=='["'+k+'"]'),None)
        if name is None:continue
        expected=obj.get(name)
        if (type(expected) not in (bool,int) or (name=='vta_output_stale' and expected not in (0,1))
                or curve.array_index!=0 or curve.modifiers or curve.sampled_points
                or curve.mute or curve.lock or not curve.keyframe_points):
            raise ValueError('Metadata curve is not safe constant bookkeeping: '+curve.data_path)
        for key in curve.keyframe_points:
            values=(key.co.y,key.handle_left.y,key.handle_right.y)
            if any(not math.isfinite(v) or abs(v-float(expected))>1e-7 for v in values):
                raise ValueError('Metadata curve varies or disagrees with its value: '+curve.data_path)
        curves.append(dict(path=curve.data_path,index=curve.array_index,
                           frames=[float(k.co.x) for k in curve.keyframe_points],value=int(expected)))
    return curves


def repair(objects):
    """Explicit developer migration, never called by refresh/load/generation.

    Stage copied Actions, run the unchanged regeneration guard, then assign all
    together. No user transform/review channel or IDProperty value is rewritten.
    Original Actions remain available to native Undo and the safety copy.
    """
    from .vta_skeletal_output import _regenerable_curves
    objects=tuple(objects)
    plans=[(obj,repair_plan(obj)) for obj in objects]  # Validate all before copies.
    staged=[]
    try:
        for obj,rows in plans:
            if not rows:continue
            old=obj.animation_data.action;action=old.copy()
            staged.append((obj,old,obj.animation_data.action_slot,action,rows))
            paths={r['path'] for r in rows}
            for layer in action.layers:
                for strip in layer.strips:
                    bag=strip.channelbag(action.slots[0])
                    if bag:
                        for curve in tuple(bag.fcurves):
                            if curve.data_path in paths:bag.fcurves.remove(curve)
            probe=obj.copy()
            try:
                probe.animation_data.action=action;probe.animation_data.action_slot=action.slots[0]
                _regenerable_curves(probe,None,True)
            finally:bpy.data.objects.remove(probe,do_unlink=True)
        for obj,old,slot,action,rows in staged:
            obj.animation_data.action=action;obj.animation_data.action_slot=action.slots[0];obj.update_tag()
        return [dict(object=obj.name,old_action=old.name,new_action=action.name,removed=rows)
                for obj,old,slot,action,rows in staged]
    except Exception:
        for obj,old,slot,action,rows in reversed(staged):
            if obj.animation_data.action==action:
                obj.animation_data.action=old;obj.animation_data.action_slot=slot
            if action.users==0:bpy.data.actions.remove(action)
        raise
