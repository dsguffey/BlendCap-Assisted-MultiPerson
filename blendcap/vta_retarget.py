"""VTA FK adapter. Public target profiles; private final-motion DTO only."""
import hashlib
import json
import math
import re
from dataclasses import replace
from types import SimpleNamespace

import bpy
from mathutils import Matrix, Vector
from .retarget.bake_fk_fast import _matrix_bake_pairs_iter_fast
from .retarget.fcurves import _iter_action_fcurves, _bulk_write_keys
from .vta_target_profiles import MIXAMO, PARENTS, PROFILES, PROFILE_ITEMS, resolve_target


# Compatibility name for existing scripts/tests; semantic keys are unchanged.
PROFILE=MIXAMO.bones
ENDPOINTS={'spine_lower':'spine_middle','spine_middle':'chest','chest':'neck','neck':'head'}
for _side in ('left','right'):
    for _a,_b in [('clavicle','upper_arm'),('upper_arm','forearm'),('forearm','hand'),
                  ('thigh','shin'),('shin','foot'),('foot','toe')]:ENDPOINTS[_side+'_'+_a]=_side+'_'+_b
_running=None
OBJECT_CHANNELS=('location','rotation_euler','rotation_quaternion','rotation_axis_angle','scale',
                 'delta_location','delta_rotation_euler','delta_rotation_quaternion','delta_scale')


def _rotation_property(bone):
    return 'rotation_quaternion' if bone.rotation_mode=='QUATERNION' else 'rotation_axis_angle' if bone.rotation_mode=='AXIS_ANGLE' else 'rotation_euler'


def _object_curve_digest(action):
    return hashlib.sha256(repr([(f.data_path,f.array_index,f.mute,f.extrapolation,len(f.modifiers),
        [(tuple(p.co),tuple(p.handle_left),tuple(p.handle_right),p.interpolation) for p in f.keyframe_points])
        for f in _iter_action_fcurves(action) if f.data_path in OBJECT_CHANNELS]).encode()).hexdigest()


def _leg_length(points):
    return sum((points[side+'_'+a].translation-points[side+'_'+b].translation).length
        for side in ('left','right') for a,b in (('thigh','shin'),('shin','foot')))/2


def _import_reference(target):
    """Only our intact object tracks may carry an imported frame-zero reference.

    This is not permission to adopt arbitrary Object Mode animation. User edits
    to generated placement tracks invalidate the receipt and require a copy.
    """
    action=target.animation_data.action if target.animation_data else None
    has_object=action and any(f.data_path in OBJECT_CHANNELS for f in _iter_action_fcurves(action))
    if not has_object:return _transform_state(target),target.matrix_world.copy()
    try:
        if (action.get('vta_alignment_version')!=1 or action.get('vta_object_curve_digest')!=_object_curve_digest(action)
                or any(f.modifiers for f in _iter_action_fcurves(action) if f.data_path in OBJECT_CHANNELS)):raise ValueError()
        reference=json.loads(action['vta_import_reference'])
        state=reference['channels'];world=_matrix(reference['world']);_uniform(world)
        if state['rotation_mode']!=target.rotation_mode:raise ValueError()
        if set(state)!=set(_transform_state(target)):raise ValueError()
        for key,values in state.items():
            if key!='rotation_mode' and (len(values)!=len(getattr(target,key)) or not all(math.isfinite(v) for v in values)):raise ValueError()
        return state,world
    except (KeyError,TypeError,ValueError):
        raise ValueError('Target Object Mode animation is unsupported or edited; use a separate target copy.') from None


def _matrix(value):
    m=Matrix(value)
    if len(m)!=4 or any(not math.isfinite(x) for row in m for x in row):raise ValueError('Nonfinite retarget transform')
    return m


def _uniform(matrix):
    if any(not math.isfinite(v) for row in matrix for v in row):
        raise ValueError('Nonfinite object transform is unsupported.')
    scales=matrix.to_scale()
    if matrix.to_3x3().determinant()<=0 or min(scales)<1e-8 or max(scales)/min(scales)>1.0002:
        raise ValueError('Use positive uniform object scale; mirrored/nonuniform transforms are unsupported.')
    basis=matrix.to_3x3();axes=[basis.col[i].normalized() for i in range(3)]
    if max(abs(axes[a].dot(axes[b])) for a,b in ((0,1),(0,2),(1,2)))>2e-4:
        raise ValueError('Sheared object transforms are unsupported.')


def action_fingerprint(obj):
    action=obj.animation_data.action if obj.animation_data else None
    if action is None:return None
    return hashlib.sha256(repr([(f.data_path,f.array_index,[(tuple(p.co),tuple(p.handle_left),tuple(p.handle_right),p.interpolation) for p in f.keyframe_points]) for f in _iter_action_fcurves(action)]).encode()).hexdigest()


def target_profile(target,profile_id='AUTO'):
    markers=[]
    for profile in PROFILES:
        if any(owner.get(key) is not None for owner in (target,target.data) for key in profile.marker_keys):markers.append(profile.id)
    return resolve_target(target.data.bones.keys(),profile_id,control_markers=markers)


def validate_target(target,scene,profile_id='AUTO'):
    if target is None or target.type!='ARMATURE' or scene.objects.get(target.name) is not target:
        raise ValueError('Choose a local target armature in this Scene.')
    if target.library or target.data.library or target.override_library or target.get('vta_character_id'):
        raise ValueError('Use a local, editable, non-VTA target armature.')
    if target.mode=='EDIT' or target.data.is_editmode:
        raise ValueError('Leave target Edit Mode before retargeting.')
    if target.data.pose_position!='POSE':
        raise ValueError('Show the target in Pose Position before retargeting.')
    match=target_profile(target,profile_id);mapping=match.mapping;profile=match.profile
    rigify=profile.id=='RIGIFY'
    if target.parent or target.constraints or (not rigify and any(b.constraints for b in target.pose.bones)):
        raise ValueError('V1 requires an unparented FK target without constraints or a control rig.')
    anim=target.animation_data
    if anim and ((anim.drivers and not rigify) or anim.nla_tracks or anim.use_tweak_mode):
        raise ValueError('Target drivers, NLA and tweak mode are unsupported in FK V1.')
    _import_reference(target)
    if target.data.animation_data and not rigify:
        raise ValueError('Animated/driven target armature data is unsupported in FK V1.')
    # Check authored values too: matrix_world can lag an RNA edit until the
    # next dependency-graph update. This preflight must also be safe in draw().
    for scale in (target.scale,target.delta_scale):
        if any(not math.isfinite(v) for v in scale) or min(scale)<=0 or max(scale)/min(scale)>1.0002:
            raise ValueError('Use positive uniform object scale; mirrored/nonuniform transforms are unsupported.')
    _uniform(target.matrix_world)
    if rigify:
        from .vta_rigify_fk import validate
        validate(target,match)
        return mapping
    if profile.root:
        root=target.data.bones[match.prefix+profile.root]
        if root.parent is not None:raise ValueError('Unsupported '+profile.label+' root hierarchy: '+root.name)
    for s,name in mapping.items():
        bone=target.data.bones[name];parent=PARENTS[s]
        expected_parent=mapping[parent] if parent else match.prefix+profile.root if profile.root else None
        if (bone.parent.name if bone.parent else None)!=expected_parent:
            raise ValueError('Unsupported '+profile.label+' hierarchy at '+name)
        if not bone.use_inherit_rotation or bone.inherit_scale!='FULL' or not bone.use_local_location:
            raise ValueError('Unsupported inheritance options on '+name)
        if any(abs(v-1)>1e-5 for v in target.pose.bones[name].scale):
            raise ValueError('Scaled target pose bones are unsupported; use a neutral target.')
        if target.pose.bones[name].rotation_mode!='QUATERNION':
            raise ValueError('V1 requires quaternion FK body bone rotation modes.')
    return mapping


def _swing(a,b):
    if a.length<1e-8 or b.length<1e-8:raise ValueError('Degenerate anatomical calibration direction')
    a=a.normalized();b=b.normalized()
    if a.dot(b)<-.99999:raise ValueError('Opposite-facing calibration is ambiguous; align target facing first.')
    return a.rotation_difference(b)


def calibration(target,motion,mapping,*,world=None,match=None):
    match=match or target_profile(target)
    joints={j['id']:j for j in motion['joints']};world=target.matrix_world.copy() if world is None else world
    references={s:match.prefix+match.profile.observations[s] if match.profile.observations else n for s,n in mapping.items()}
    rest={s:world@target.data.bones[n].matrix_local for s,n in references.items()}
    offsets={};overrides={}
    for s in mapping:
        source=_matrix(joints[s]['rest']);q_source=source.to_quaternion().normalized()
        q_target=(world@target.data.bones[mapping[s]].matrix_local).to_quaternion().normalized()
        direction=joints[s]['direction'];endpoint=ENDPOINTS.get(s);target_direction=None
        if endpoint in rest:target_direction=rest[endpoint].translation-rest[s].translation
        if s.endswith('_hand'):
            references=match.profile.hand_references[0 if s.startswith('left_') else 1]
            finger=next((target.data.bones.get(match.prefix+n) for n in references if target.data.bones.get(match.prefix+n)),None)
            target_direction=(world@finger.head_local)-rest[s].translation if finger else rest[s].translation-rest[PARENTS[s]].translation
        swing=_swing(target_direction,Vector(direction)) if direction is not None and target_direction is not None else None
        q_cal=(swing@q_target) if swing else q_target
        offsets[s]=q_source.inverted()@q_cal
        # Existing engine hook: effective source rest, no bind-pose edits.
        q_override=q_target@offsets[s].inverted()
        overrides[s]=Matrix.LocRotScale(source.translation,q_override,(1,1,1))
    source_rest={s:_matrix(joints[s]['rest']) for s in mapping}
    length=_leg_length(source_rest)
    if length<1e-5:raise ValueError('Source leg reference is degenerate')
    scale=_leg_length(rest)/length
    if not math.isfinite(scale) or not .001<scale<1000:raise ValueError('Incompatible source/target proportions')
    return joints,rest,offsets,overrides,scale


def _transform_state(owner):
    # Raw channels preserve Euler turns/quaternion signs as well as matrices.
    fields=('location','rotation_euler','rotation_axis_angle','rotation_quaternion','scale',
            'delta_location','delta_rotation_euler','delta_rotation_quaternion','delta_scale')
    return dict(rotation_mode=owner.rotation_mode,**{key:tuple(getattr(owner,key)) for key in fields if hasattr(owner,key)})


def _restore_transform(owner,values):
    for key,value in values.items():setattr(owner,key,value)


def _rig_state(obj):
    return obj,_transform_state(obj),{b.name:_transform_state(b) for b in obj.pose.bones},obj.hide_viewport,obj.hide_render,obj.hide_get()


def _restore_rig(state):
    obj,transform,pose,viewport,render,hidden=state
    _restore_transform(obj,transform);obj.hide_viewport=viewport;obj.hide_render=render;obj.hide_set(hidden)
    for name,values in pose.items():_restore_transform(obj.pose.bones[name],values)


def _animated_properties(obj):
    """Preserve non-transform values touched by sampling the old Action.

    Pose transforms are handled by raw channel snapshots. Never eval a data
    path: resolve its owner through RNA and decode only a final property key.
    """
    action=obj.animation_data.action if obj.animation_data else None
    result=[];seen=set()
    for curve in _iter_action_fcurves(action) if action else ():
        path=curve.data_path
        if path in seen:continue
        seen.add(path)
        if path in ('location','rotation_euler','rotation_quaternion','rotation_axis_angle','scale') or path.startswith('delta_'):continue
        attr=re.fullmatch(r'(.*)\.([A-Za-z_][A-Za-z0-9_]*)',path)
        key=re.fullmatch(r'(.*)\[("(?:[^"\\]|\\.)*")\]',path)
        if attr:
            owner=obj.path_resolve(attr[1]);name=attr[2]
            if isinstance(owner,bpy.types.PoseBone) and name in ('location','rotation_euler','rotation_quaternion','rotation_axis_angle','scale'):continue
            value=getattr(owner,name);custom=False
        elif key:
            owner=obj.path_resolve(key[1]) if key[1] else obj;name=json.loads(key[2]);value=owner[name];custom=True
        elif path.isidentifier():owner=obj;name=path;value=getattr(obj,path);custom=False
        else:raise ValueError('Unsupported animation property: '+path)
        result.append((owner,name,tuple(value) if hasattr(value,'__len__') and not isinstance(value,str) else value,custom))
    return result


def _restore_properties(properties):
    for owner,name,value,custom in properties:
        if custom:owner[name]=value
        else:setattr(owner,name,value)


def _validate_motion(motion):
    """Validate the public input envelope before allocating any Blender IDs."""
    if not isinstance(motion,dict) or any(motion.get(k)!=v for k,v in (
            ('schema','vta-final-motion'),('version',1),('root_policy','composite-pelvis-once'),
            ('transform_space','global'),('coordinates','right-handed-z-up-metres'))):
        raise ValueError('Unsupported final-motion contract')
    try:
        fps=motion['fps'];samples=motion['samples'];joints=motion['joints']
        if type(fps) not in (int,float) or not math.isfinite(fps) or not 0<fps<=1000:raise ValueError()
        if not samples or len(samples)>100001:raise ValueError()
        ids=[j['id'] for j in joints]
        if len(ids)!=len(set(ids)) or set(ids)-set(PROFILE):raise ValueError()
        if any(j['parent']!=PARENTS[j['id']] for j in joints):raise ValueError()
        for joint in joints:
            _uniform(_matrix(joint['rest']))
            direction=joint['direction']
            if direction is not None and (len(direction)!=3 or not all(math.isfinite(v) for v in direction)):raise ValueError()
        first=samples[0]['frame']
        if type(first) is not int or motion['frame_range']!=[first,first+len(samples)-1]:raise ValueError()
        for index,sample in enumerate(samples):
            if type(sample['frame']) is not int or sample['frame']!=first+index:raise ValueError()
            if type(sample['available']) is not bool:raise ValueError()
            if not sample['available']:raise ValueError('Range includes unavailable motion; choose a continuously available frame range.')
            if set(sample['poses'])!=set(ids):raise ValueError()
            for matrix in sample['poses'].values():_uniform(_matrix(matrix))
        if not isinstance(motion['provenance']['digest'],str) or not motion['provenance']['digest']:raise ValueError()
    except (KeyError,TypeError,AttributeError,ValueError) as exc:
        if 'unavailable' in str(exc):raise
        raise ValueError('Malformed final-motion contract: '+str(exc)) from exc


def capture_motion(source,bindings,start,end,*,preserve=()):
    """Host sample only: private code validates/normalizes the final numeric DTO."""
    scene=bpy.context.scene
    if start<1 or start>end or end-start>100000:raise ValueError('Choose animation frames starting at 1 or later; frame 0 is reserved for imported rest.')
    missing=[b['bone'] for b in bindings if not b['optional'] and b['bone'] not in source.data.bones]
    if missing:raise ValueError('Missing VTA source bones: '+', '.join(missing))
    bindings=[b for b in bindings if b['bone'] in source.data.bones]
    anim=source.animation_data
    if not anim or not anim.action or anim.nla_tracks or anim.drivers:raise ValueError('Use a generated Character Action without NLA or object drivers.')
    visibility=[f for f in _iter_action_fcurves(anim.action) if f.data_path in ('hide_viewport','hide_render')]
    properties=[_animated_properties(o) for o in (source,*preserve)]
    properties=[_animated_properties(o) for o in (source,*preserve)]
    rest=None;samples=[]
    for frame in range(start,end+1):
        if bpy.context.scene is not scene:raise ValueError('Scene changed; retarget cancelled.')
        old_frame=scene.frame_current;sub=scene.frame_subframe
        states=[_rig_state(o) for o in (source,*preserve)]
        try:
            scene.frame_set(frame)
            if scene.frame_current!=frame:raise ValueError('Requested frame is outside Blender frame limits.')
            available=not any(f.evaluate(frame)>.5 for f in visibility)
            source.hide_viewport=False;source.hide_set(False);bpy.context.view_layer.update()
            ev=source.evaluated_get(bpy.context.evaluated_depsgraph_get());_uniform(ev.matrix_world)
            if rest is None:
                rest={}
                for b in bindings:
                    bone=source.data.bones[b['bone']];m=ev.matrix_world@bone.matrix_local;direction=None
                    endpoint=source.data.bones.get(b['endpoint']) if b['endpoint'] else None
                    if endpoint:direction=(ev.matrix_world@endpoint.head_local)-m.translation
                    elif b['id'].endswith('_hand') and bone.parent:direction=m.translation-(ev.matrix_world@bone.parent.head_local)
                    rest[b['id']]=dict(matrix=[list(r) for r in m],direction=list(direction) if direction is not None else None)
            samples.append(dict(frame=frame,available=available,poses={b['id']:[list(r) for r in ev.matrix_world@ev.pose.bones[b['bone']].matrix] for b in bindings} if available else None))
        finally:
            scene.frame_set(old_frame,subframe=sub)
            for state in states:_restore_rig(state)
            bpy.context.view_layer.update()
            for values in properties:_restore_properties(values)
            for values in properties:_restore_properties(values)
        yield ('Sampling',frame)
    return rest,samples


class RetargetJob:
    """Bake on disposable objects. Real target receives one new Action on success."""
    def __init__(self,scene,target,motion,*,before_publish=None,profile_id='AUTO'):
        self.scene=scene;self.target=target;self.motion=motion;self.before_publish=before_publish
        self.mapping=validate_target(target,scene,profile_id);self.match=target_profile(target,profile_id)
        self.adapter=None
        if self.match.profile.id=='RIGIFY':
            from .vta_rigify_fk import RigifyFk
            self.adapter=RigifyFk(target,self.match)
        self.temporary=[];self.published=None
        _validate_motion(motion)
        if motion['frame_range'][0]<1:raise ValueError('Frame 0 is reserved for imported rest; choose animation frames starting at 1 or later.')
        ids={j['id'] for j in motion['joints']}
        for s in list(self.mapping):
            if s not in ids:
                if s.endswith('_toe'):del self.mapping[s]
                else:raise ValueError('Required semantic source joint missing: '+s)
        self.import_state,self.import_world=_import_reference(target)
        joints,rest,_,_,ratio=calibration(target,motion,self.mapping,world=self.import_world,match=self.match)
        # Arbitrary global scale is retained from the source, not the FBX units.
        # Uniform scaling preserves target proportions; a world-space translation
        # aligns the two reference pelvises without double-applying Root motion.
        source_hips=_matrix(joints['hips']['rest']).translation
        self.aligned_world=Matrix.Translation(source_hips)@Matrix.Scale(1/ratio,4)@Matrix.Translation(-rest['hips'].translation)@self.import_world
        self.joints,self.rest,self.offsets,self.overrides,self.scale=calibration(target,motion,self.mapping,world=self.aligned_world,match=self.match)
        self.frame_world={}
        reference_length=_leg_length({s:_matrix(j['rest']) for s,j in self.joints.items()})
        for sample in motion['samples']:
            poses={s:_matrix(m) for s,m in sample['poses'].items()}
            factor=_leg_length(poses)/reference_length
            if not math.isfinite(factor) or not .001<factor<1000:raise ValueError('Source scale reference is degenerate.')
            pelvis=poses['hips'].translation
            self.frame_world[sample['frame']]=Matrix.Translation(pelvis)@Matrix.Scale(factor,4)@Matrix.Translation(-pelvis)@self.aligned_world
        self.original=target.animation_data.action if target.animation_data else None
        self.slot=target.animation_data.action_slot if target.animation_data else None
        self.had_anim=target.animation_data is not None
        self.fingerprint=action_fingerprint(target);self.world=target.matrix_world.copy()
        self.object_state=_transform_state(target)
        self.rest_signature=self._rest_signature();self.pose={b.name:_transform_state(b) for b in target.pose.bones}
        self.properties=_animated_properties(target)

    def _rest_signature(self):return repr([(b.name,b.parent.name if b.parent else None,tuple(map(tuple,b.matrix_local))) for b in self.target.data.bones])

    def _source(self):
        data=bpy.data.armatures.new('.VTA motion snapshot');obj=bpy.data.objects.new('.VTA motion snapshot',data);self.scene.collection.objects.link(obj);self.temporary.append(obj)
        active=bpy.context.view_layer.objects.active;selected=list(bpy.context.selected_objects);mode=active.mode if active else 'OBJECT'
        try:
            if bpy.context.object and bpy.context.object.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
            for o in bpy.context.selected_objects:o.select_set(False)
            obj.select_set(True);bpy.context.view_layer.objects.active=obj;bpy.ops.object.mode_set(mode='EDIT')
            for s in PROFILE:
                if s not in self.mapping:continue
                j=self.joints[s];m=_matrix(j['rest']);b=data.edit_bones.new(s)
                b.head=m.translation;b.tail=m.translation+m.to_3x3().col[1]*.1;b.matrix=m;b.length=.1
                if j['parent']:b.parent=data.edit_bones[j['parent']]
            bpy.ops.object.mode_set(mode='OBJECT')
        finally:
            if obj.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
            obj.select_set(False)
            for o in selected:o.select_set(True)
            bpy.context.view_layer.objects.active=active
            if active and mode!='OBJECT':bpy.ops.object.mode_set(mode=mode)
        obj.hide_render=True;obj.hide_set(True)
        return obj

    def steps(self):
        try:
            source=self._source();stage=self.target.copy()
            if self.adapter:
                stage.data=self.target.data.copy()
                stage.animation_data.action=None # Preserve generated drivers; Blender remaps self IDs on copy.
                self.adapter.isolate_data_drivers(stage,self.target)
            else:stage.animation_data_clear()
            stage.name='.VTA retarget staging';self.scene.collection.objects.link(stage);self.temporary.append(stage)
            stage.hide_render=True;stage.hide_viewport=False;stage.hide_set(True)
            _restore_transform(stage,self.import_state)
            stage.delta_location=(0,0,0);stage.delta_rotation_euler=(0,0,0);stage.delta_rotation_quaternion=(1,0,0,0);stage.delta_scale=(1,1,1)
            stage.matrix_world=self.aligned_world
            bpy.context.view_layer.update()
            owned=self.adapter.owned if self.adapter else set(stage.pose.bones.keys())
            for name in owned:stage.pose.bones[name].matrix_basis=Matrix.Identity(4)
            if self.adapter:self.adapter.prepare(stage)
            for sample in self.motion['samples']:
                computed={}
                for b in source.pose.bones:
                    bone=b.bone;parent=computed.get(bone.parent.name) if bone.parent else None
                    base=parent@bone.parent.matrix_local.inverted()@bone.matrix_local if parent is not None else bone.matrix_local.copy()
                    pose=_matrix(sample['poses'][b.name]);p=pose.translation if b.name=='hips' else base.translation
                    desired=Matrix.LocRotScale(p,pose.to_quaternion().normalized(),(1,1,1));computed[b.name]=desired
                    kwargs=dict(parent_matrix=parent,parent_matrix_local=bone.parent.matrix_local) if parent is not None else {}
                    b.rotation_mode='QUATERNION';b.matrix_basis=bone.convert_local_to_pose(desired,bone.matrix_local,invert=True,**kwargs)
                    b.keyframe_insert('rotation_quaternion',frame=sample['frame'])
                    if b.name=='hips':b.keyframe_insert('location',frame=sample['frame'])
                yield ('Preparing',sample['frame'])
            rows=[SimpleNamespace(source=s,target=n,channels='ROT',axes='XYZ',loc_scale=1.,loc_scale_residual=False) for s,n in self.mapping.items()]
            if not self.adapter:rows.append(SimpleNamespace(source='hips',target=self.mapping['hips'],channels='LOC',axes='XYZ',loc_scale=self.scale,loc_scale_residual=False))
            first,last=self.motion['frame_range']
            for frame in _matrix_bake_pairs_iter_fast(rows,source,stage,first,last,set(),self.overrides,settings=dict(world_location=True,leg_compensation=False,face_compensation=False,face_denoise=False)):
                yield ('Baking',frame)
            # A new Action must not inherit unkeyed location/scale remnants
            # from the previous Action on the real object. Materialize neutral
            # body channels with the SAME existing bulk writer, only on stage.
            neutral={};primed=set()
            for name in self.mapping.values():
                bone=stage.pose.bones[name]
                for prop in ('location','scale'):
                    if prop=='location' and name==self.mapping['hips'] and not self.adapter:continue
                    primed.add((name,prop));path=bone.path_from_id(prop)
                    for index in range(3):neutral[path,index]=[v for f in range(first,last+1) for v in (f,1. if prop=='scale' else 0.)]
            for _ in _bulk_write_keys(stage,neutral,primed,first):yield ('Writing neutral channels',None)
            # Plain FK owns every pose bone, including unmapped fingers. Rigify
            # owns only its enumerated body controls, not unrelated mechanisms.
            for bone in stage.pose.bones:
                if bone.name in owned and bone.name not in self.mapping.values():
                    for frame in range(first,last+1):
                        for prop in ('location',_rotation_property(bone),'scale'):bone.keyframe_insert(prop,frame=frame)
            for frame in range(first,last+1):
                stage.matrix_world=self.frame_world[frame]
                for prop in OBJECT_CHANNELS:stage.keyframe_insert(prop,frame=frame)
            if self.adapter:
                self.adapter.property_keys(stage,[0,*range(first,last+1)])
                saved_frame=self.scene.frame_current;saved_subframe=self.scene.frame_subframe
                try:
                    for sample in self.motion['samples']:
                        self.scene.frame_set(sample['frame']);stage.hide_set(False);bpy.context.view_layer.update()
                        self.adapter.correct_pelvis(stage,sample)
                        stage.hide_set(True)
                        yield ('Aligning Rigify pelvis',sample['frame'])
                finally:self.scene.frame_set(saved_frame,subframe=saved_subframe)
            _restore_transform(stage,self.import_state)
            for prop in OBJECT_CHANNELS:stage.keyframe_insert(prop,frame=0)
            for name in owned:
                bone=stage.pose.bones[name]
                bone.matrix_basis=Matrix.Identity(4)
                for prop in ('location',_rotation_property(bone),'scale'):bone.keyframe_insert(prop,frame=0)
            action=stage.animation_data.action
            if action is None:raise ValueError('Retarget produced no Action')
            required={(stage.pose.bones[name].path_from_id(_rotation_property(stage.pose.bones[name])),index) for name in owned for index in range(len(getattr(stage.pose.bones[name],_rotation_property(stage.pose.bones[name]))))}
            required.update((stage.pose.bones[name].path_from_id(prop),index) for name in owned for prop in ('location','scale') for index in range(3))
            required.update((prop,index) for prop in OBJECT_CHANNELS for index in range(len(getattr(stage,prop))))
            if self.adapter:
                required.update((stage.pose.bones[name].path_from_id('['+json.dumps(key)+']'),0) for name,props in self.adapter.values.items() for key in props)
            if {(f.data_path,f.array_index) for f in _iter_action_fcurves(action)}!=required:
                raise ValueError('Incomplete retarget channel set; no Action assigned.')
            for fc in _iter_action_fcurves(action):
                if len(fc.keyframe_points)!=len(self.motion['samples'])+1:raise ValueError('Incomplete retarget curve')
                for key in fc.keyframe_points:
                    if not all(math.isfinite(v) for v in key.co):raise ValueError('Nonfinite retarget key')
                    key.interpolation='CONSTANT'
            # Validate the neutral reference with the same evaluated graph as
            # animated frames; the bind data and imported object are unchanged.
            old=self.scene.frame_current;sub=self.scene.frame_subframe
            try:
                self.scene.frame_set(0);stage.hide_set(False);bpy.context.view_layer.update()
                ev=stage.evaluated_get(bpy.context.evaluated_depsgraph_get())
                if max(abs(v-w) for a,b in zip(ev.matrix_world,self.import_world) for v,w in zip(a,b))>1e-4:
                    raise ValueError('Imported frame-zero placement validation failed')
                for name in (self.adapter.observations.values() if self.adapter else ev.pose.bones.keys()):
                    bone=ev.pose.bones[name]
                    if max(abs(v-w) for a,b in zip(bone.matrix,bone.bone.matrix_local) for v,w in zip(a,b))>1e-4:
                        raise ValueError('Frame-zero rest pose validation failed: '+bone.name)
            finally:
                self.scene.frame_set(old,subframe=sub);stage.hide_set(True);bpy.context.view_layer.update()
            for sample in self.motion['samples']:
                old=self.scene.frame_current;sub=self.scene.frame_subframe
                try:
                    self.scene.frame_set(sample['frame']);stage.hide_set(False);bpy.context.view_layer.update()
                    ev=stage.evaluated_get(bpy.context.evaluated_depsgraph_get())
                    if max(abs(v-w) for a,b in zip(ev.matrix_world,self.frame_world[sample['frame']]) for v,w in zip(a,b))>1e-4:
                        raise ValueError('Retarget world scale/placement validation failed')
                    for s,name in self.mapping.items():
                        got=ev.matrix_world@ev.pose.bones[name].matrix
                        q=_matrix(sample['poses'][s]).to_quaternion().normalized()@self.offsets[s]
                        if max(abs(a-b) for ra,rb in zip(got.to_quaternion().normalized().to_matrix(),q.to_matrix()) for a,b in zip(ra,rb))>1e-4:
                            raise ValueError('Retarget rotation validation failed: '+name)
                    if self.adapter:self.adapter.validate_deform(ev,sample,self.offsets)
                    else:
                        expected=_matrix(sample['poses']['hips']).translation
                        if ((ev.matrix_world@ev.pose.bones[self.mapping['hips']].head)-expected).length>1e-4:raise ValueError('Retarget pelvis validation failed')
                finally:
                    self.scene.frame_set(old,subframe=sub);stage.hide_set(True);bpy.context.view_layer.update()
                yield ('Validating',sample['frame'])
            if (bpy.context.scene is not self.scene or self.target.matrix_world!=self.world
                    or _transform_state(self.target)!=self.object_state or self._rest_signature()!=self.rest_signature):
                raise ValueError('Target or Scene changed during retarget; no Action assigned.')
            if (self.target.animation_data.action if self.target.animation_data else None)!=self.original or action_fingerprint(self.target)!=self.fingerprint:
                raise ValueError('Target animation changed during retarget; no Action assigned.')
            validate_target(self.target,self.scene,self.match.profile.id)
            if self.adapter:
                from .vta_rigify_fk import graph_signature
                if graph_signature(self.target)!=self.adapter.graph:raise ValueError('Rigify graph changed; no Action assigned.')
            if self.before_publish:self.before_publish()
            action.name='VTA '+self.match.profile.label+' — '+self.target.name
            action['vta_target_profile']=self.match.profile.id
            action['vta_retarget_version']=1;action['vta_motion_digest']=self.motion['provenance']['digest']
            action['vta_alignment_version']=1
            action['vta_import_reference']=json.dumps(dict(channels=self.import_state,world=[list(r) for r in self.import_world]))
            action['vta_object_curve_digest']=_object_curve_digest(action)
            if self.adapter:self.adapter.finish_action(action)
            # An ID reference keeps the previous Action saved/recoverable WITHOUT
            # modifying its curves, flags or adding NLA to the target.
            if self.original:action['vta_previous_action']=self.original
            self.target.animation_data_create()
            try:
                self.target.animation_data.action=action;self.target.animation_data.action_slot=action.slots[0]
                if self.adapter:self.adapter.prepare(self.target)
                self.scene.frame_set(self.scene.frame_current,subframe=self.scene.frame_subframe)
                self.published=action
            except Exception:
                if not self.had_anim:self.target.animation_data_clear()
                else:
                    self.target.animation_data.action=self.original
                    if self.slot:self.target.animation_data.action_slot=self.slot
                _restore_transform(self.target,self.object_state)
                raise
        finally:
            self.close()

    def close(self):
        for obj in reversed(self.temporary):
            if bpy.data.objects.get(obj.name) is obj:
                data=obj.data;action=obj.animation_data.action if obj.animation_data else None
                bpy.data.objects.remove(obj,do_unlink=True)
                if action and action is not self.published and action.users==0:bpy.data.actions.remove(action)
                if data and data.users==0:bpy.data.armatures.remove(data)
        self.temporary=[]
        _restore_properties([p for p in self.properties if not (self.published and self.adapter and self.adapter.owns_property(p[0],p[1]))])
        if self.adapter and not self.published:self.adapter.restore(self.target)
        for name,values in self.pose.items():
            if name in self.target.pose.bones and (not self.published or (self.adapter and name not in self.adapter.owned)):
                _restore_transform(self.target.pose.bones[name],values)


def source_picker_poll(settings, obj):
    scene = settings.id_data
    return (obj.type == 'ARMATURE' and scene.objects.get(obj.name) is obj
            and bool(obj.get('vta_character_id'))
            and obj.get('vta_project_id') == scene.get('vta_project_id')
            and obj.get('vta_source_id') == scene.get('vta_source_id'))


def target_picker_poll(settings, obj):
    return (obj.type == 'ARMATURE' and settings.id_data.objects.get(obj.name) is obj
            and not obj.get('vta_character_id'))


class VtaRetargetSettings(bpy.types.PropertyGroup):
    """Blender presentation choices only; no private project/schema changes."""
    source: bpy.props.PointerProperty(name='VTA Character', type=bpy.types.Object,
        poll=source_picker_poll, description='Pick the generated VTA armature, not its mesh')
    target: bpy.props.PointerProperty(name='Target armature', type=bpy.types.Object,
        poll=target_picker_poll, description='Pick the target armature in the viewport or Outliner, not a mesh part')
    show_mapping: bpy.props.BoolProperty(name='Bone mapping (read-only)', default=False)
    target_profile: bpy.props.EnumProperty(name='Target profile',items=PROFILE_ITEMS,default='AUTO')


def mapping_preview(context, settings):
    """Read-only name/rest preflight; never load project/evidence or bake in draw."""
    from . import vta_review_ui as ui
    source, target = settings.source, settings.target
    if source is None or not source_picker_poll(settings, source):
        raise ValueError('Choose a generated VTA Character armature from this Scene.')
    if target is None or not target_picker_poll(settings, target):
        raise ValueError('Choose a target armature, not a mesh part.')
    mapping = validate_target(target, context.scene,settings.target_profile)
    bindings = ui._get_backend(context)._core.retarget_source_bindings()
    rows = []
    for binding in bindings:
        semantic, name = binding['id'], binding['bone']
        if name not in source.data.bones:
            if binding['optional']:continue
            raise ValueError('Missing required VTA source bone: ' + name)
        if semantic in mapping:rows.append((name, mapping[semantic]))
    return tuple(rows)


def draw_rig_choices(layout, context):
    settings = context.scene.blendcap_vta_retarget
    # Native Object PointerProperty supplies Blender's filtered picker AND
    # eyedropper. No name dropdown over scene.objects (mesh parts) is used.
    layout.prop(settings, 'source', text='VTA Character')
    layout.prop(settings, 'target', text='Target armature')
    layout.prop(settings, 'target_profile')
    layout.label(text='Pick armatures, not body/clothing meshes.', icon='INFO')
    try:
        rows = mapping_preview(context, settings)
    except (ValueError, AttributeError) as exc:
        layout.label(text=str(exc), icon='INFO')
        return False
    layout.label(text=f'{len(rows)} body bones matched; automatic calibration.', icon='CHECKMARK')
    layout.label(text='Resolved: '+target_profile(settings.target,settings.target_profile).profile.label)
    layout.prop(settings, 'show_mapping', icon='TRIA_DOWN' if settings.show_mapping else 'TRIA_RIGHT', emboss=False)
    if settings.show_mapping:
        table = layout.box()
        header = table.row();header.label(text='VTA source');header.label(text='Target control')
        for source_name, target_name in rows:
            row = table.row();row.label(text=source_name);row.label(text=target_name)
        table.label(text='Composite pelvis motion applied once.')
        table.label(text='Toes optional; fingers/IK/face not solved.')
    action = settings.target.animation_data.action if settings.target.animation_data else None
    layout.label(text='Previous Action: ' + (action.name if action else 'None'))
    layout.label(text='New Action; previous animation retained. Esc cancels.')
    layout.label(text='Frame 0: imported rest. Motion: source size and placement.')
    return True


class VIEW3D_PT_vta_retarget(bpy.types.Panel):
    bl_idname = 'VIEW3D_PT_vta_retarget'
    bl_label = 'Retargeting'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'VTA'
    bl_parent_id = 'VIEW3D_PT_blendcap_ui'
    bl_order = -10

    def draw(self, context):
        from . import vta_review_ui as ui
        layout = self.layout
        if context.mode != 'OBJECT':
            layout.label(text='Switch to Object Mode to select armatures.', icon='INFO')
        if not ui.loaded_context_matches(context):
            layout.label(text='Load a VTA Scene and Enter VTA Mode first.', icon='INFO')
        controls = layout.column()
        ready = draw_rig_choices(controls, context) if ui.loaded_context_matches(context) else False
        wm = context.window_manager
        row = controls.row(align=True)
        row.prop(wm, 'blendcap_vta_animation_start', text='Start')
        row.prop(wm, 'blendcap_vta_animation_end', text='End')
        button = controls.row()
        button.enabled = ready and BLENDCAP_OT_vta_retarget.poll(context)
        button.operator_context = 'EXEC_DEFAULT'
        op = button.operator('blendcap.vta_retarget', text='Retarget Animation', icon='ARMATURE_DATA')
        op.use_scene_selection = True
        op.start, op.end = wm.blendcap_vta_animation_start, wm.blendcap_vta_animation_end


class BLENDCAP_OT_vta_retarget(bpy.types.Operator):
    bl_idname='blendcap.vta_retarget'
    bl_label='Retarget Animation'
    bl_description='Bake final VTA motion to a new supported FK Action; keep previous animation'
    bl_options={'REGISTER','UNDO'}
    source_name:bpy.props.StringProperty(name='VTA Character')
    target_name:bpy.props.StringProperty(name='Target armature')
    start:bpy.props.IntProperty(name='Start Frame',default=1)
    end:bpy.props.IntProperty(name='End Frame',default=1)
    use_scene_selection:bpy.props.BoolProperty(default=False,options={'HIDDEN','SKIP_SAVE'})
    target_profile:bpy.props.EnumProperty(name='Target profile',items=PROFILE_ITEMS,default='AUTO')
    _stream=None
    _timer=None

    @classmethod
    def poll(cls,context):
        from . import vta_review_ui as ui
        area = getattr(context, 'area', None)
        return (_running is None and context.mode == 'OBJECT'
                and (bpy.app.background or (area and area.type == 'VIEW_3D'))
                and ui.loaded_context_matches(context) and not ui._analysis_locked())

    def invoke(self,context,event):
        from . import vta_review_ui as ui
        candidates=[o for o in context.scene.objects if o.type=='ARMATURE' and o.get('vta_character_id')==ui.current_character_id(context)]
        active=context.active_object
        settings = context.scene.blendcap_vta_retarget
        if candidates and settings.source is None:settings.source=candidates[0]
        if active and target_picker_poll(settings,active):settings.target=active
        self.use_scene_selection = True
        self.start=context.window_manager.blendcap_vta_animation_start;self.end=context.window_manager.blendcap_vta_animation_end
        return context.window_manager.invoke_props_dialog(self,width=620)

    def draw(self,context):
        layout=self.layout
        draw_rig_choices(layout, context)
        row=layout.row(align=True);row.prop(self,'start');row.prop(self,'end')

    def _run(self,context):
        from . import vta_review_ui as ui
        ui.require_loaded_context(context);scene=context.scene
        source=scene.objects.get(self.source_name);target=scene.objects.get(self.target_name)
        info=ui.cached_scene_source(context)
        if (source is None or source.type!='ARMATURE' or not info or source.get('vta_project_id')!=info[0]
                or source.get('vta_source_id')!=info[1]['source_id'] or not source.get('vta_character_id')):
            raise ValueError('Choose one generated VTA Character from the loaded source.')
        validate_target(target,scene,self.target_profile)
        backend=ui._get_backend(context);location=ui._location_from_context(context,allow_outside=True)
        # Export authorizes the requested motion, not the current playhead.
        # In particular frame 0 is a valid place to inspect the imported model
        # and launch a bake whose motion starts at 1, without moving the Scene.
        location=replace(location,source_frame=location.source_frame+self.start-location.blender_frame,blender_frame=self.start)
        if not (info[1]['start_frame']<=location.source_frame
                <=location.source_frame+self.end-self.start<=info[1]['end_frame']):
            raise ValueError('Choose a retarget range inside the VTA source coverage.')
        fingerprint=action_fingerprint(source)
        properties=_animated_properties(target)
        try:
            rest,samples=yield from capture_motion(source,backend._core.retarget_source_bindings(),self.start,self.end,preserve=(target,))
        finally:_restore_properties(properties)
        motion=backend.export_retarget_motion(location,character_id=source['vta_character_id'],fps=scene.render.fps/scene.render.fps_base,rest=rest,samples=samples)
        token=motion['provenance']['state_token']
        def unchanged():
            if action_fingerprint(source)!=fingerprint:raise ValueError('Source animation changed; retarget cancelled.')
            project,_=backend._load_project_source(location)
            if hashlib.sha256(project.to_json().encode()).hexdigest()!=token:raise ValueError('VTA state changed; retarget cancelled.')
        job=RetargetJob(scene,target,motion,before_publish=unchanged,profile_id=self.target_profile)
        source_state=_rig_state(source);source_properties=_animated_properties(source)
        try:yield from job.steps()
        finally:
            job.close();_restore_rig(source_state);_restore_properties(source_properties)
        self.report({'INFO'},'Created '+job.published.name+'; previous Action retained.')

    def execute(self,context):
        global _running
        if _running is not None:self.report({'ERROR'},'Retargeting is already running.');return {'CANCELLED'}
        if self.use_scene_selection:
            settings = context.scene.blendcap_vta_retarget
            self.source_name = settings.source.name if settings.source else ''
            self.target_name = settings.target.name if settings.target else ''
            self.target_profile = settings.target_profile
        _running=self;self._stream=self._run(context)
        if bpy.app.background:
            try:
                for _ in self._stream:pass
                return {'FINISHED'}
            except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}
            finally:self._finish(context)
        self._timer=context.window_manager.event_timer_add(.02,window=context.window);context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self,context,event):
        if event.type=='ESC':self._finish(context);self.report({'INFO'},'Retarget cancelled; target animation unchanged.');return {'CANCELLED'}
        if event.type!='TIMER':return {'RUNNING_MODAL'}
        try:
            for _ in range(4):
                phase,frame=next(self._stream);context.workspace.status_text_set('VTA Retarget: '+phase+' '+str(frame if frame is not None else 'keys')+' — Esc cancels')
        except StopIteration:self._finish(context);return {'FINISHED'}
        except Exception as exc:self._finish(context);self.report({'ERROR'},str(exc));return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _finish(self,context):
        global _running
        try:
            if self._stream:self._stream.close()
        finally:
            self._stream=None
            if self._timer:context.window_manager.event_timer_remove(self._timer);self._timer=None
            context.workspace.status_text_set(None);_running=None


def cancel_running(context):
    if _running is not None:_running._finish(context)
