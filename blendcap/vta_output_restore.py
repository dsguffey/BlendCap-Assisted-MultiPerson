"""One native previous-output slot; no domain history or private objects.

Actions/data are held by native ID references, including across Save/Reopen.
Restore refuses later edits rather than discarding manual animation.
"""
import hashlib,json
from pathlib import Path
from uuid import uuid4
import bpy
from mathutils import Matrix
from .retarget.fcurves import _iter_action_fcurves

SCHEMA=1

def _encode(value,text):
    if isinstance(value,bpy.types.ID):
        key='ref-'+uuid4().hex;text[key]=value;return {'native_id':key}
    if hasattr(value,'to_dict'):value=value.to_dict()
    if isinstance(value,dict):return {k:_encode(v,text) for k,v in value.items()}
    if isinstance(value,(list,tuple)) or hasattr(value,'to_list'):return [_encode(v,text) for v in value]
    if value is None or type(value) in (str,int,float,bool):return value
    raise ValueError('Unsupported native output metadata; previous animation retained.')

def _decode(value,text):
    if isinstance(value,dict):
        if set(value)=={'native_id'}:return text[value['native_id']]
        return {k:_decode(v,text) for k,v in value.items()}
    if isinstance(value,list):return [_decode(v,text) for v in value]
    return value

def _portable(value):
    if isinstance(value,dict):return {k:_portable(v) for k,v in value.items()}
    if isinstance(value,list):return [_portable(v) for v in value]
    if isinstance(value,str):
        try:
            if value.startswith(('{','[')):return _portable(json.loads(value))
        except ValueError:pass
        path=Path(value)
        if path.is_absolute() and 'reconstruction' in path.parts:
            return Path(*path.parts[path.parts.index('reconstruction'):]).as_posix()
    return value

def _metadata(owner):
    return {k:owner[k] for k in owner.keys() if k.startswith('vta_') and k!='vta_output_stale'}

def fingerprint(obj):
    action=obj.animation_data.action if obj.animation_data else None
    curves=list(_iter_action_fcurves(action)) if action else []
    animated={fc.data_path for fc in curves}
    camera_curves=list(_iter_action_fcurves(obj.data.animation_data.action)) if obj.type=='CAMERA' and obj.data.animation_data and obj.data.animation_data.action else []
    camera_animated={f.data_path for f in camera_curves}
    values=dict(slot=obj.animation_data.action_slot.handle if obj.animation_data and obj.animation_data.action_slot else None,
        curves=[(f.data_path,f.array_index,f.mute,f.extrapolation,[(tuple(p.co),tuple(p.handle_left),tuple(p.handle_right),p.interpolation) for p in f.keyframe_points],[(m.type) for m in f.modifiers]) for f in curves],
        static={k:list(getattr(obj,k)) for k in ('location','rotation_euler','rotation_quaternion','scale') if k not in animated},
        metadata=_portable(_metadata(obj)),data_metadata=_portable(_metadata(obj.data)),
        bones=[(b.name,b.parent.name if b.parent else None,[list(r) for r in b.matrix_local]) for b in obj.data.bones] if obj.type=='ARMATURE' else None,
        camera_settings={key:getattr(obj.data,key) for key in ('type','lens','sensor_width','sensor_height','shift_x','shift_y','clip_start','clip_end') if key not in camera_animated} if obj.type=='CAMERA' else None,
        camera_action=[(f.data_path,f.array_index,[(tuple(p.co),p.interpolation) for p in f.keyframe_points]) for f in camera_curves])
    return hashlib.sha256(repr(values).encode('utf-8')).hexdigest()

def slot(scene):
    identity=scene.get('vta_scene_owner_id')
    if not identity or sum(s.get('vta_scene_owner_id')==identity for s in bpy.data.scenes)!=1:return None
    matches=[t for t in bpy.data.texts if t.get('vta_output_restore_scene')==identity and t.get('vta_output_restore_version')==SCHEMA]
    return matches[0] if len(matches)==1 else None

def capture(scene,project_id,source_id,*,companion_root,job_id=None,result_sha256=None):
    bpy.context.view_layer.update()
    bpy.context.view_layer.update()
    targets=[o for o in scene.objects if o.get('vta_project_id')==project_id and o.get('vta_source_id')==source_id and o.type in ('ARMATURE','CAMERA')]
    if not any(o.type=='ARMATURE' and o.animation_data and o.animation_data.action for o in targets):return None
    identity=scene.get('vta_scene_owner_id') or uuid4().hex;scene['vta_scene_owner_id']=identity
    if scene.library or any(s is not scene and s.get('vta_scene_owner_id')==identity for s in bpy.data.scenes):
        raise ValueError('Previous output requires one local, uniquely owned Scene.')
    text=bpy.data.texts.new('.VTA Previous Output Candidate');text.use_fake_user=True
    text['vta_output_restore_version']=SCHEMA;text['vta_output_restore_candidate']=True
    text['vta_output_restore_project']=project_id;text['vta_output_restore_source']=source_id
    rows=[]
    try:
        for index,obj in enumerate(targets):
            for name,value in (('object',obj),('data',obj.data),('action',obj.animation_data.action if obj.animation_data else None),('parent',obj.parent)):
                if value is not None:text[name+str(index)]=value
            rows.append(dict(index=index,slot=obj.animation_data.action_slot.handle if obj.animation_data and obj.animation_data.action_slot else None,
                metadata=_encode(_metadata(obj),text),data_metadata=_encode(_metadata(obj.data),text),
                matrix=[list(r) for r in obj.matrix_basis],parent_inverse=[list(r) for r in obj.matrix_parent_inverse],
                parent_type=obj.parent_type,parent_bone=obj.parent_bone))
        value=dict(schema=SCHEMA,scene_owner_id=identity,project_id=project_id,source_id=source_id,origin_root=str(Path(companion_root).resolve()),job_id=job_id,result_sha256=result_sha256,rows=rows,ready=False)
        raw=json.dumps(value,sort_keys=True)
        if len(raw)>8*1024*1024:raise ValueError('Previous native output metadata exceeds the bounded slot.')
        text.write(raw);return text
    except Exception:
        discard(text);raise

def discard(candidate):
    if candidate is not None and candidate.name in bpy.data.texts:bpy.data.texts.remove(candidate)


def publish(scene,candidate):
    if candidate is None:return
    value=json.loads(candidate.as_string())
    current=[o for o in scene.objects if o.get('vta_project_id')==value['project_id'] and o.get('vta_source_id')==value['source_id'] and o.type in ('ARMATURE','CAMERA')]
    if len(current)!=len(value['rows']):
        previous=slot(scene)
        if previous is not None:discard(previous)
        discard(candidate)
        return False  # A structural expansion cannot be undone by an Action slot.
    for row in value['rows']:
        index=row['index'];obj=candidate.get('object'+str(index))
        if obj not in current:raise ValueError('Native output ownership changed during generation.')
        candidate['after-data'+str(index)]=obj.data
        if obj.animation_data and obj.animation_data.action:candidate['after-action'+str(index)]=obj.animation_data.action
        if obj.parent:candidate['after-parent'+str(index)]=obj.parent
        row['after_slot']=obj.animation_data.action_slot.handle if obj.animation_data and obj.animation_data.action_slot else None
        row['fingerprint']=fingerprint(obj)
    # Include newly created source-owned objects so a later structural change
    # invalidates this slot. Never remove those objects automatically on Restore.
    value['scope']=[(o.get('vta_character_id'),o.type) for o in current]
    value['ready']=True;previous=slot(scene)
    candidate.clear();candidate.write(json.dumps(value,sort_keys=True));candidate['vta_output_restore_scene']=value['scene_owner_id'];candidate['vta_output_restore_candidate']=False
    if previous is not None and previous!=candidate:discard(previous)
    return True

def _rebind(value,source,destination):
    if isinstance(value,dict):return {k:_rebind(v,source,destination) for k,v in value.items()}
    if isinstance(value,list):return [_rebind(v,source,destination) for v in value]
    if isinstance(value,str):
        try:
            if value.startswith(('{','[')):
                return json.dumps(_rebind(json.loads(value),source,destination),sort_keys=True)
        except ValueError:pass
        path=Path(value)
        if path.is_absolute() and path.is_relative_to(source):return str(destination/path.relative_to(source))
    return value

def copy_dependencies(plan):
    """Include the one retained native slot's external output closure in Save As."""
    for text in bpy.data.texts:
        if not text.get('vta_output_restore_scene'):continue
        value=json.loads(text.as_string())
        for row in value['rows']:
            metadata=_rebind(_decode(row['metadata'],text),Path(value['origin_root']),plan.source)
            data_metadata=_rebind(_decode(row['data_metadata'],text),Path(value['origin_root']),plan.source)
            if metadata.get('vta_output_file'):plan.output(metadata['vta_output_file'])
            if data_metadata.get('vta_scene_reference'):
                reference=json.loads(data_metadata['vta_scene_reference'])
                plan.output(reference['output_file'],reference['output_sha256'])

def _guard(scene,text,value):
    if not value['ready'] or scene.get('vta_scene_owner_id')!=value['scene_owner_id']:raise ValueError('Previous output belongs to another Scene.')
    if (scene.get('vta_project_id'),scene.get('vta_source_id'))!=(value['project_id'],value['source_id']):
        raise ValueError('Scene source changed; previous output was retained without application.')
    current=[o for o in scene.objects if o.get('vta_project_id')==value['project_id'] and o.get('vta_source_id')==value['source_id'] and o.type in ('ARMATURE','CAMERA')]
    if sorted((o.get('vta_character_id') or '',o.type) for o in current)!=sorted((cid or '',kind) for cid,kind in value['scope']):raise ValueError('Output structure changed; previous output cannot safely replace it.')
    for row in value['rows']:
        key=str(row['index']);obj=text.get('object'+key)
        if (obj is None or scene.objects.get(obj.name) is not obj or obj.data is not text.get('after-data'+key)
                or obj.library or obj.data.library or text.library
                or obj.parent is not text.get('after-parent'+key)
                or (obj.animation_data.action if obj.animation_data else None) is not text.get('after-action'+key)
                or (obj.animation_data.action_slot.handle if obj.animation_data and obj.animation_data.action_slot else None)!=row['after_slot']
                or obj.constraints or (obj.animation_data and (obj.animation_data.nla_tracks or obj.animation_data.drivers))
                or (obj.data.animation_data and (obj.data.animation_data.nla_tracks or obj.data.animation_data.drivers))
                or (obj.type=='ARMATURE' and any(b.constraints for b in obj.pose.bones)) or fingerprint(obj)!=row['fingerprint']):
            raise ValueError('Animation, Depth keys or rig structure changed after generation. Manual work was retained; Restore is unavailable.')

def restore(scene):
    text=slot(scene)
    if text is None:raise ValueError('No compatible previous native output is retained.')
    value=json.loads(text.as_string());_guard(scene,text,value)
    # All references and slot identities validate before any native mutation.
    prepared=[];rollback=[]
    current_root=Path(bpy.data.filepath).with_name(Path(bpy.data.filepath).stem+'_vta').resolve()
    for row in value['rows']:
        key=str(row['index']);action=text.get('action'+key)
        obj=text.get('object'+key);data=text.get('data'+key)
        if data is None or data.library or (action and action.library):raise ValueError('Previous native data is missing or linked.')
        if action and not any(s.handle==row['slot'] for s in action.slots):raise ValueError('Previous Action slot is unavailable.')
        metadata=[_rebind(_decode(row[name],text),Path(value['origin_root']),current_root) for name in ('metadata','data_metadata')]
        prepared.append((row,obj,data,action,metadata))
        anim=obj.animation_data
        rollback.append((obj,obj.data,anim.action if anim else None,anim.action_slot if anim else None,
            _metadata(obj),_metadata(obj.data),obj.parent,obj.parent_type,obj.parent_bone,obj.matrix_parent_inverse.copy(),obj.matrix_basis.copy()))
    frame=scene.frame_current
    bpy.context.view_layer.update() # Populate native layered-Action owner caches before reassignment.
    bpy.context.view_layer.update() # Populate native layered-Action owner caches before reassignment.
    def put(owner,metadata):
        for name in tuple(owner.keys()):
            if name.startswith('vta_') and name!='vta_output_stale':del owner[name]
        for name,item in metadata.items():owner[name]=item
    try:
        for row,obj,data,action,metadata in prepared:
            key=str(row['index']);obj.data=data
            if action:
                obj.animation_data_create();obj.animation_data.action=action;obj.animation_data.action_slot=next(s for s in action.slots if s.handle==row['slot'])
            elif obj.animation_data:obj.animation_data.action=None
            put(obj,metadata[0]);put(obj.data,metadata[1])
            obj.parent=text.get('parent'+key);obj.parent_type=row['parent_type'];obj.parent_bone=row['parent_bone']
            obj.matrix_parent_inverse=Matrix(row['parent_inverse']);obj.matrix_basis=Matrix(row['matrix'])
        scene.frame_set(frame);bpy.context.view_layer.update()
    except Exception:
        for obj,data,action,action_slot,meta,data_meta,parent,parent_type,parent_bone,inverse,basis in rollback:
            obj.data=data
            if obj.animation_data:
                obj.animation_data.action=action
                if action:obj.animation_data.action_slot=action_slot
            put(obj,meta);put(obj.data,data_meta)
            obj.parent=parent;obj.parent_type=parent_type;obj.parent_bone=parent_bone
            obj.matrix_parent_inverse=inverse;obj.matrix_basis=basis
        scene.frame_set(frame);bpy.context.view_layer.update();raise
    discard(text)
    # The engine's accepted artifact is retained. This restores native output,
    # not review/domain authority; the current result can be explicitly reapplied.
    return value
