"""VTA Mixamo FK adapter. Public target policy; private final-motion DTO only."""
import hashlib
import json
import math
import re
from types import SimpleNamespace

import bpy
from mathutils import Matrix, Vector
from .retarget.bake_fk_fast import _matrix_bake_pairs_iter_fast
from .retarget.fcurves import _iter_action_fcurves, _bulk_write_keys


PROFILE={'hips':'Hips','spine_lower':'Spine','spine_middle':'Spine1','chest':'Spine2',
         'neck':'Neck','head':'Head'}
for _side in ('left','right'):
    for _semantic,_bone in [('clavicle','Shoulder'),('upper_arm','Arm'),('forearm','ForeArm'),
            ('hand','Hand'),('thigh','UpLeg'),('shin','Leg'),('foot','Foot'),('toe','ToeBase')]:
        PROFILE[_side+'_'+_semantic]=_side.title()+_bone
PARENTS={'hips':None,'spine_lower':'hips','spine_middle':'spine_lower','chest':'spine_middle',
         'neck':'chest','head':'neck'}
for _side in ('left','right'):
    for _chain,_parent in [(('clavicle','upper_arm','forearm','hand'),'chest'),
                           (('thigh','shin','foot','toe'),'hips')]:
        for _semantic in _chain:
            PARENTS[_side+'_'+_semantic]=_parent;_parent=_side+'_'+_semantic
ENDPOINTS={'spine_lower':'spine_middle','spine_middle':'chest','chest':'neck','neck':'head'}
for _side in ('left','right'):
    for _a,_b in [('clavicle','upper_arm'),('upper_arm','forearm'),('forearm','hand'),
                  ('thigh','shin'),('shin','foot'),('foot','toe')]:ENDPOINTS[_side+'_'+_a]=_side+'_'+_b
_running=None


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


def validate_target(target,scene):
    if target is None or target.type!='ARMATURE' or scene.objects.get(target.name) is not target:
        raise ValueError('Choose a local Mixamo armature in this Scene.')
    if target.library or target.data.library or target.override_library or target.get('vta_character_id'):
        raise ValueError('Use a local, editable, non-VTA target armature.')
    if target.mode=='EDIT' or target.data.is_editmode:
        raise ValueError('Leave target Edit Mode before retargeting.')
    if target.data.pose_position!='POSE':
        raise ValueError('Show the target in Pose Position before retargeting.')
    if target.parent or target.constraints or any(b.constraints for b in target.pose.bones):
        raise ValueError('V1 requires an unparented FK target without constraints or a control rig.')
    anim=target.animation_data
    if anim and (anim.drivers or anim.nla_tracks or anim.use_tweak_mode):
        raise ValueError('Target drivers, NLA and tweak mode are unsupported in FK V1.')
    if anim and anim.action:
        if any(f.data_path in ('location','rotation_euler','rotation_quaternion','rotation_axis_angle','scale') or f.data_path.startswith('delta_') for f in _iter_action_fcurves(anim.action)):
            raise ValueError('Target Object Mode animation is unsupported; use a separate target copy.')
    if target.data.animation_data:
        raise ValueError('Animated/driven target armature data is unsupported in FK V1.')
    # Check authored values too: matrix_world can lag an RNA edit until the
    # next dependency-graph update. This preflight must also be safe in draw().
    for scale in (target.scale,target.delta_scale):
        if any(not math.isfinite(v) for v in scale) or min(scale)<=0 or max(scale)/min(scale)>1.0002:
            raise ValueError('Use positive uniform object scale; mirrored/nonuniform transforms are unsupported.')
    _uniform(target.matrix_world)
    names=set(target.data.bones.keys());prefixes=[]
    for prefix in ('','mixamorig','mixamorig:')+tuple(sorted({n[:-4] for n in names if n.endswith('Hips') and n[:-4].endswith((':','_','.','-'))})):
        if prefix not in prefixes and prefix+'Hips' in names:prefixes.append(prefix)
    if len(prefixes)!=1:raise ValueError('Target Mixamo namespace is missing or ambiguous.')
    prefix=prefixes[0];mapping={s:prefix+n for s,n in PROFILE.items() if prefix+n in names}
    missing=[PROFILE[s] for s in PROFILE if s not in mapping and not s.endswith('_toe')]
    if missing:raise ValueError('Missing required Mixamo bones: '+', '.join(missing))
    for s,name in mapping.items():
        bone=target.data.bones[name];parent=PARENTS[s]
        if (bone.parent.name if bone.parent else None)!=(mapping[parent] if parent else None):
            raise ValueError('Unsupported Mixamo hierarchy at '+name)
        if not bone.use_inherit_rotation or bone.inherit_scale!='FULL' or not bone.use_local_location:
            raise ValueError('Unsupported inheritance options on '+name)
        if any(abs(v-1)>1e-5 for v in target.pose.bones[name].scale):
            raise ValueError('Scaled target pose bones are unsupported; use a neutral target.')
        if target.pose.bones[name].rotation_mode!='QUATERNION':
            raise ValueError('V1 requires Mixamo quaternion FK bone rotation modes.')
    return mapping


def _swing(a,b):
    if a.length<1e-8 or b.length<1e-8:raise ValueError('Degenerate anatomical calibration direction')
    a=a.normalized();b=b.normalized()
    if a.dot(b)<-.99999:raise ValueError('Opposite-facing calibration is ambiguous; align target facing first.')
    return a.rotation_difference(b)


def calibration(target,motion,mapping):
    joints={j['id']:j for j in motion['joints']};world=target.matrix_world.copy()
    rest={s:world@target.data.bones[n].matrix_local for s,n in mapping.items()}
    offsets={};overrides={}
    for s in mapping:
        source=_matrix(joints[s]['rest']);q_source=source.to_quaternion().normalized();q_target=rest[s].to_quaternion().normalized()
        direction=joints[s]['direction'];endpoint=ENDPOINTS.get(s);target_direction=None
        if endpoint in rest:target_direction=rest[endpoint].translation-rest[s].translation
        if s.endswith('_hand'):
            prefix=mapping[s][:-len(PROFILE[s])];side=PROFILE[s][:-4]
            finger=next((target.data.bones.get(prefix+side+'Hand'+digit+'1') for digit in ('Middle','Index') if target.data.bones.get(prefix+side+'Hand'+digit+'1')),None)
            target_direction=(world@finger.head_local)-rest[s].translation if finger else rest[s].translation-rest[PARENTS[s]].translation
        swing=_swing(target_direction,Vector(direction)) if direction is not None and target_direction is not None else None
        q_cal=(swing@q_target) if swing else q_target
        offsets[s]=q_source.inverted()@q_cal
        # Existing engine hook: effective source rest, no bind-pose edits.
        q_override=q_target@offsets[s].inverted()
        overrides[s]=Matrix.LocRotScale(source.translation,q_override,(1,1,1))
    def leg(points):
        return sum((points[side+'_'+a].translation-points[side+'_'+b].translation).length for side in ('left','right') for a,b in (('thigh','shin'),('shin','foot')))/2
    source_rest={s:_matrix(joints[s]['rest']) for s in mapping}
    length=leg(source_rest)
    if length<1e-5:raise ValueError('Source leg reference is degenerate')
    scale=leg(rest)/length
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
    if start>end or end-start>100000:raise ValueError('Choose a valid bounded frame range.')
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
    def __init__(self,scene,target,motion,*,before_publish=None):
        self.scene=scene;self.target=target;self.motion=motion;self.before_publish=before_publish
        self.mapping=validate_target(target,scene);self.temporary=[];self.published=None
        _validate_motion(motion)
        ids={j['id'] for j in motion['joints']}
        for s in list(self.mapping):
            if s not in ids:
                if s.endswith('_toe'):del self.mapping[s]
                else:raise ValueError('Required semantic source joint missing: '+s)
        self.joints,self.rest,self.offsets,self.overrides,self.scale=calibration(target,motion,self.mapping)
        self.original=target.animation_data.action if target.animation_data else None
        self.slot=target.animation_data.action_slot if target.animation_data else None
        self.had_anim=target.animation_data is not None
        self.fingerprint=action_fingerprint(target);self.world=target.matrix_world.copy()
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
            source=self._source();stage=self.target.copy();stage.animation_data_clear();stage.name='.VTA retarget staging';self.scene.collection.objects.link(stage);self.temporary.append(stage)
            stage.hide_render=True;stage.hide_viewport=False;stage.hide_set(True)
            for b in stage.pose.bones:b.matrix_basis=Matrix.Identity(4);b.rotation_mode='QUATERNION'
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
            rows.append(SimpleNamespace(source='hips',target=self.mapping['hips'],channels='LOC',axes='XYZ',loc_scale=self.scale,loc_scale_residual=False))
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
                    if prop=='location' and name==self.mapping['hips']:continue
                    primed.add((name,prop));path=bone.path_from_id(prop)
                    for index in range(3):neutral[path,index]=[v for f in range(first,last+1) for v in (f,1. if prop=='scale' else 0.)]
            for _ in _bulk_write_keys(stage,neutral,primed,first):yield ('Writing neutral channels',None)
            action=stage.animation_data.action
            if action is None:raise ValueError('Retarget produced no Action')
            required={(stage.pose.bones[name].path_from_id('rotation_quaternion'),index) for name in self.mapping.values() for index in range(4)}
            required.update((stage.pose.bones[name].path_from_id(prop),index) for name in self.mapping.values() for prop in ('location','scale') for index in range(3))
            if {(f.data_path,f.array_index) for f in _iter_action_fcurves(action)}!=required:
                raise ValueError('Incomplete retarget channel set; no Action assigned.')
            for fc in _iter_action_fcurves(action):
                if len(fc.keyframe_points)!=len(self.motion['samples']):raise ValueError('Incomplete retarget curve')
                for key in fc.keyframe_points:
                    if not all(math.isfinite(v) for v in key.co):raise ValueError('Nonfinite retarget key')
                    key.interpolation='CONSTANT'
            for sample in self.motion['samples']:
                old=self.scene.frame_current;sub=self.scene.frame_subframe
                try:
                    self.scene.frame_set(sample['frame']);stage.hide_set(False);bpy.context.view_layer.update()
                    ev=stage.evaluated_get(bpy.context.evaluated_depsgraph_get())
                    for s,name in self.mapping.items():
                        got=ev.matrix_world@ev.pose.bones[name].matrix
                        q=_matrix(sample['poses'][s]).to_quaternion().normalized()@self.offsets[s]
                        if max(abs(a-b) for ra,rb in zip(got.to_quaternion().normalized().to_matrix(),q.to_matrix()) for a,b in zip(ra,rb))>1e-4:
                            raise ValueError('Retarget rotation validation failed: '+name)
                    expected=self.rest['hips'].translation+self.scale*(_matrix(sample['poses']['hips']).translation-_matrix(self.joints['hips']['rest']).translation)
                    if ((ev.matrix_world@ev.pose.bones[self.mapping['hips']].head)-expected).length>1e-4:raise ValueError('Retarget pelvis validation failed')
                finally:
                    self.scene.frame_set(old,subframe=sub);stage.hide_set(True);bpy.context.view_layer.update()
                yield ('Validating',sample['frame'])
            if bpy.context.scene is not self.scene or self.target.matrix_world!=self.world or self._rest_signature()!=self.rest_signature:
                raise ValueError('Target or Scene changed during retarget; no Action assigned.')
            if (self.target.animation_data.action if self.target.animation_data else None)!=self.original or action_fingerprint(self.target)!=self.fingerprint:
                raise ValueError('Target animation changed during retarget; no Action assigned.')
            validate_target(self.target,self.scene)
            if self.before_publish:self.before_publish()
            action.name='VTA Mixamo FK — '+self.target.name
            action['vta_retarget_version']=1;action['vta_motion_digest']=self.motion['provenance']['digest']
            # An ID reference keeps the previous Action saved/recoverable WITHOUT
            # modifying its curves, flags or adding NLA to the target.
            if self.original:action['vta_previous_action']=self.original
            self.target.animation_data_create()
            try:
                self.target.animation_data.action=action;self.target.animation_data.action_slot=action.slots[0]
                self.published=action
            except Exception:
                if not self.had_anim:self.target.animation_data_clear()
                else:
                    self.target.animation_data.action=self.original
                    if self.slot:self.target.animation_data.action_slot=self.slot
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
        _restore_properties(self.properties)
        for name,values in self.pose.items():
            if name in self.target.pose.bones and (not self.published or name not in self.mapping.values()):
                _restore_transform(self.target.pose.bones[name],values)


class BLENDCAP_OT_vta_retarget(bpy.types.Operator):
    bl_idname='blendcap.vta_retarget'
    bl_label='Retarget Animation to Mixamo'
    bl_description='Bake final VTA motion to a new Mixamo FK Action; keep previous animation'
    bl_options={'REGISTER','UNDO'}
    source_name:bpy.props.StringProperty(name='VTA Character')
    target_name:bpy.props.StringProperty(name='Mixamo target')
    start:bpy.props.IntProperty(name='Start Frame',default=1)
    end:bpy.props.IntProperty(name='End Frame',default=1)
    _stream=None
    _timer=None

    @classmethod
    def poll(cls,context):
        from . import vta_review_ui as ui
        return _running is None and ui.loaded_context_matches(context) and not ui._analysis_locked()

    def invoke(self,context,event):
        from . import vta_review_ui as ui
        candidates=[o for o in context.scene.objects if o.type=='ARMATURE' and o.get('vta_character_id')==ui.current_character_id(context)]
        active=context.active_object
        if candidates:self.source_name=candidates[0].name
        if active and active.type=='ARMATURE' and not active.get('vta_character_id'):self.target_name=active.name
        self.start=context.window_manager.blendcap_vta_animation_start;self.end=context.window_manager.blendcap_vta_animation_end
        return context.window_manager.invoke_props_dialog(self,width=460)

    def draw(self,context):
        layout=self.layout;layout.prop_search(self,'source_name',context.scene,'objects');layout.prop_search(self,'target_name',context.scene,'objects')
        row=layout.row(align=True);row.prop(self,'start');row.prop(self,'end')
        layout.label(text='New FK Action; previous Action retained. Esc cancels.')
        layout.label(text='Body only. No fingers, IK, foot locking or face.')
        try:
            mapping=validate_target(context.scene.objects.get(self.target_name),context.scene)
            layout.label(text=str(len(mapping))+' body bones matched; automatic rest calibration.',icon='CHECKMARK')
        except ValueError as exc:layout.label(text=str(exc),icon='INFO')

    def _run(self,context):
        from . import vta_review_ui as ui
        ui.require_loaded_context(context);scene=context.scene
        source=scene.objects.get(self.source_name);target=scene.objects.get(self.target_name)
        info=ui.cached_scene_source(context)
        if (source is None or source.type!='ARMATURE' or not info or source.get('vta_project_id')!=info[0]
                or source.get('vta_source_id')!=info[1]['source_id'] or not source.get('vta_character_id')):
            raise ValueError('Choose one generated VTA Character from the loaded source.')
        validate_target(target,scene)
        backend=ui._get_backend(context);location=ui._location_from_context(context,allow_outside=True)
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
        job=RetargetJob(scene,target,motion,before_publish=unchanged)
        source_state=_rig_state(source);source_properties=_animated_properties(source)
        try:yield from job.steps()
        finally:
            job.close();_restore_rig(source_state);_restore_properties(source_properties)
        self.report({'INFO'},'Created '+job.published.name+'; previous Action retained.')

    def execute(self,context):
        global _running
        if _running is not None:self.report({'ERROR'},'Retargeting is already running.');return {'CANCELLED'}
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
                phase,frame=next(self._stream);context.workspace.status_text_set('VTA Mixamo: '+phase+' '+str(frame if frame is not None else 'keys')+' — Esc cancels')
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
