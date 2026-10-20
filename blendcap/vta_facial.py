"""Selected-Character face capture and isolated, transactional shape-key NLA.

No inference here. User Actions/slots/curves are never edited. Conflicting
animation is rejected, not silently replaced. Core owns evidence/gap policy.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import bpy

from .retarget.fcurves import _iter_action_fcurves

BINDING='vta_face_binding'
OUTPUT='vta_face_output'
ACTION_ID='vta_face_action_id'
_job=None


def busy():return _job is not None


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def scene_id(scene):
    value=scene.get('vta_scene_owner_id') or scene.get('vta_face_scene_id')
    if not value:raise ValueError('Load a metadata-owned VTA Scene before facial capture.')
    if any(s!=scene and (s.get('vta_scene_owner_id') or s.get('vta_face_scene_id'))==value for s in bpy.data.scenes):
        raise ValueError('Duplicated Scene identity; create an independent New VTA Scene before facial binding.')
    return value


def scope(scene,project_id,source_id,cid):
    return dict(project_id=project_id,source_id=source_id,scene_id=scene_id(scene),character_id=cid)


def signature(action):
    if action is None:return None
    return hashlib.sha256(canonical([(fc.data_path,fc.array_index,fc.mute,fc.extrapolation,
        [(tuple(p.co),tuple(p.handle_left),tuple(p.handle_right),p.handle_left_type,p.handle_right_type,p.interpolation)
         for p in fc.keyframe_points],[(m.type,m.mute) for m in fc.modifiers]) for fc in _iter_action_fcurves(action)]).encode()).hexdigest()


def read_binding(obj):
    try:value=json.loads(obj.get(BINDING,''))
    except (ValueError,TypeError):raise ValueError('Choose an explicitly bound facial mesh.')
    if value.get('version')!=1:raise ValueError('Unsupported facial binding.')
    return value


def channel_map(obj,channels):
    keys=obj.data.shape_keys if obj.type=='MESH' else None
    if keys is None or keys.use_relative is False:raise ValueError('Bind a mesh with relative ARKit-style shape keys.')
    normalize=lambda name:name.casefold().replace('_','')
    available={}
    for key in list(keys.key_blocks)[1:]:available.setdefault(normalize(key.name),[]).append(key.name)
    mapping={}
    for channel in channels:
        # Bounded aliases for known canonical channels only. The persisted map
        # retains actual target names; evidence and mesh names never change.
        accepted={normalize(channel)}
        for suffix,short in (('Left','_L'),('Right','_R')):
            if channel.endswith(suffix):accepted.add(normalize(channel[:-len(suffix)]+short))
        matches=[name for token in accepted for name in available.get(token,[])]
        if len(matches)>1:raise ValueError('Duplicate/ambiguous facial shape-key names: '+channel)
        if matches:
            block=keys.key_blocks[matches[0]]
            if block.slider_min>0 or block.slider_max<1:raise ValueError('Facial target controls must support weights 0–1.')
            mapping[channel]=matches[0]
    if not mapping:raise ValueError('No compatible named facial shape keys found.')
    return mapping


def validate_mesh(scene,obj,binding=None):
    if (obj.type!='MESH' or obj.name not in scene.objects or len(obj.users_scene)!=1
            or obj.library or obj.data.library or obj.data.users!=1 or obj.data.shape_keys is None):
        raise ValueError('Facial mesh must be local, unshared and belong only to this Scene.')
    if any(other != obj and other.type=='MESH' and other.data.shape_keys == obj.data.shape_keys for other in bpy.data.objects):
        raise ValueError('Shared shape-key datablocks are not safe facial targets.')
    if binding:
        if any(other != obj and other.get(BINDING)==obj.get(BINDING) for other in bpy.data.objects):
            raise ValueError('Duplicated facial binding; explicitly bind an independent mesh.')
        if binding['scene_id']!=scene_id(scene):raise ValueError('Facial mesh is bound to another Scene.')


def owned_track(obj):
    raw=obj.get(OUTPUT)
    if not raw:return None,None,None
    meta=json.loads(raw);ad=obj.data.shape_keys.animation_data
    if meta.get('binding_id')!=read_binding(obj)['binding_id']:raise ValueError('Facial output and target binding differ.')
    tracks=[track for track in ad.nla_tracks if any(strip.action and strip.action.get(ACTION_ID)==meta['action_id'] for strip in track.strips)] if ad else []
    if len(tracks)!=1:raise ValueError('Facial output ownership is missing/ambiguous; no animation changed.')
    track=tracks[0];actions={s.action for s in track.strips}
    if len(actions)!=1:raise ValueError('User edits changed the generated facial track.')
    action=actions.pop()
    state=[(s.frame_start,s.frame_end,s.action_frame_start,s.action_frame_end,s.blend_type,s.extrapolation,
            s.influence,s.mute,s.scale,s.repeat,s.action_slot.handle if s.action_slot else None) for s in track.strips]
    if (signature(action)!=meta['signature'] or canonical(state)!=meta['strips'] or track.mute or track.is_solo
            or any(s.use_animated_time or s.use_animated_influence or s.modifiers for s in track.strips)
            or action.users!=len(track.strips)):
        raise ValueError('Generated facial animation was edited/shared; preserve it by using an independent target before regenerating.')
    return track,action,meta


def check_conflicts(obj,mapping,old_track=None):
    keys=obj.data.shape_keys;ad=keys.animation_data
    paths={keys.key_blocks[name].path_from_id('value') for name in mapping.values()}
    # Blender initializes NLA-driven properties to RNA defaults outside strips,
    # not their previous unkeyed pose. Refuse that lossy first adoption.
    if old_track is None and any(abs(keys.key_blocks[name].value)>1e-7 for name in mapping.values()):
        raise ValueError('Facial expression controls must start at neutral (zero). Preserve this manually posed face with an independent target before binding/generation.')
    if not ad:return
    if ad.use_tweak_mode:raise ValueError('Exit NLA tweak mode before facial generation.')
    if not ad.use_nla or any(t.is_solo for t in ad.nla_tracks):raise ValueError('Enable ordinary NLA playback and exit solo mode before facial generation.')
    if any(fc.data_path in paths for fc in ad.drivers):raise ValueError('Facial controls have drivers; no animation overwritten.')
    actions=[ad.action] if ad.action else []
    for track in ad.nla_tracks:
        if track != old_track:actions.extend(s.action for s in track.strips if s.action)
    if any(fc.data_path in paths for action in actions for fc in _iter_action_fcurves(action)):
        raise ValueError('User animation already drives these facial controls. Use an independent target; user curves are never overwritten.')


def bind(scene,obj,identity,channels):
    validate_mesh(scene,obj)
    if obj.get(OUTPUT):raise ValueError('This mesh already has generated facial animation; its binding cannot be reassigned.')
    mapping=channel_map(obj,channels);check_conflicts(obj,mapping)
    binding=dict(version=1,binding_id=uuid4().hex,mapping=mapping,**identity)
    for other in scene.objects:
        if other == obj or not other.get(BINDING):continue
        prior=read_binding(other)
        if all(prior.get(k)==identity[k] for k in identity):raise ValueError('Character already has a bound facial mesh in this Scene.')
    obj[BINDING]=canonical(binding)
    return binding


def targets(scene,identities,channels):
    result=[]
    for identity in identities:
        found=[]
        for obj in scene.objects:
            if not obj.get(BINDING):continue
            binding=read_binding(obj)
            if all(binding.get(k)==v for k,v in identity.items()):found.append((obj,binding))
        if len(found)!=1:raise ValueError('Bind exactly one compatible facial mesh for every selected Character.')
        obj,binding=found[0];validate_mesh(scene,obj,binding)
        if channel_map(obj,channels)!=binding['mapping']:raise ValueError('Facial target shape keys changed; binding must be reviewed.')
        old_track,_,_=owned_track(obj);check_conflicts(obj,binding['mapping'],old_track)
        result.append((obj,binding))
    return result


def target_stamp(obj):
    keys=obj.data.shape_keys;ad=keys.animation_data
    return canonical(dict(binding=obj.get(BINDING),output=obj.get(OUTPUT),data=keys.as_pointer(),
        active=ad.action.as_pointer() if ad and ad.action else None,
        slot=ad.action_slot.handle if ad and ad.action_slot else None,
        actions=[signature(a) for a in [ad.action] if a] if ad else [],
        tracks=[(t.name,t.mute,[(s.action.as_pointer() if s.action else None,s.frame_start,s.frame_end,
                  signature(s.action)) for s in t.strips]) for t in ad.nla_tracks] if ad else []))


def stage(obj,binding,samples,offset):
    """Build muted replacement layer; no user Action/slot or values are touched."""
    keys=obj.data.shape_keys;had_ad=keys.animation_data is not None
    old_track,old_action,old_meta=owned_track(obj)
    check_conflicts(obj,binding['mapping'],old_track)
    stored=json.loads(old_action.get('vta_face_samples','{}')) if old_action else {}
    provenance=json.loads(old_action.get('vta_face_derivation','{}')) if old_action else {}
    for row in samples:stored[str(row['source_frame']+offset)]={c:row['weights'][c] for c in binding['mapping']}
    for row in samples:
        provenance[str(row['source_frame']+offset)]={k:row[k] for k in ('origin','raw_state','input_origin','filter_policy') if k in row}
    numbers=sorted(map(int,stored))
    ranges=[]
    for frame in numbers:
        if ranges and frame==ranges[-1][1]+1:ranges[-1][1]=frame
        else:ranges.append([frame,frame])
    ad=keys.animation_data_create();action=bpy.data.actions.new('VTA facial expressions')
    track=None
    try:
        action[ACTION_ID]=uuid4().hex;action['vta_face_binding_id']=binding['binding_id']
        slot=action.slots.new(id_type='KEY',name=keys.name)
        layer=action.layers.new('VTA facial');strip=layer.strips.new(type='KEYFRAME');bag=strip.channelbag(slot,ensure=True)
        for channel,name in binding['mapping'].items():
            fc=bag.fcurves.new(keys.key_blocks[name].path_from_id('value'),index=0)
            points=[]
            for frame in numbers:points.extend((frame,stored[str(frame)][channel]))
            for first,last in ranges:points.extend((last+.001,stored[str(last)][channel]))
            pairs=sorted(zip(points[::2],points[1::2]));fc.keyframe_points.add(len(pairs))
            fc.keyframe_points.foreach_set('co',[v for pair in pairs for v in pair])
            for point in fc.keyframe_points:point.interpolation='LINEAR'
            fc.update()
        action['vta_face_samples']=canonical(stored)
        action['vta_face_derivation']=canonical(provenance)
        track=ad.nla_tracks.new();track.name='VTA Face '+binding['binding_id'][:8];track.mute=True
        for first,last in ranges:
            nla=track.strips.new('VTA face range',first,action);nla.action_slot=slot
            nla.action_frame_start=first;nla.action_frame_end=last+.001
            nla.frame_start=first;nla.frame_end=last+.001
            nla.extrapolation='NOTHING';nla.blend_type='REPLACE'
        states=[(s.frame_start,s.frame_end,s.action_frame_start,s.action_frame_end,s.blend_type,s.extrapolation,
                 s.influence,s.mute,s.scale,s.repeat,s.action_slot.handle if s.action_slot else None) for s in track.strips]
        meta=dict(version=1,action_id=action[ACTION_ID],signature=signature(action),strips=canonical(states),
                  binding_id=binding['binding_id'],offset=offset)
        if old_meta and old_meta['offset']!=offset:raise ValueError('Source/Blender timing changed; use a fresh facial target.')
        return dict(obj=obj,track=track,action=action,old_track=old_track,old_action=old_action,
                    old_output=obj.get(OUTPUT),new_output=canonical(meta),had_ad=had_ad)
    except Exception:
        if track:ad.nla_tracks.remove(track)
        bpy.data.actions.remove(action)
        if not had_ad:keys.animation_data_clear()
        raise


def rollback(stages):
    for item in reversed(stages):
        obj=item['obj'];keys=obj.data.shape_keys;ad=keys.animation_data
        if item['old_track']:item['old_track'].mute=False
        ad.nla_tracks.remove(item['track']);bpy.data.actions.remove(item['action'])
        if item['old_output'] is None:obj.pop(OUTPUT,None)
        else:obj[OUTPUT]=item['old_output']
        if not item['had_ad']:keys.animation_data_clear()


def publish(stages,commit):
    try:
        for item in stages:
            if item['old_track']:item['old_track'].mute=True
            item['track'].mute=False;item['obj'][OUTPUT]=item['new_output']
        commit()
    except Exception:
        rollback(stages);raise
    for item in stages:
        # Publication is already complete; cleanup is non-authoritative. Retain
        # muted recovery material if Blender cannot remove an unused datablock.
        try:
            if item['old_track']:item['obj'].data.shape_keys.animation_data.nla_tracks.remove(item['old_track'])
            if item['old_action'] and item['old_action'].users==0:bpy.data.actions.remove(item['old_action'])
        except RuntimeError:pass


def feedback(context,primary,details='',error=False,log=None):
    from . import vta_review_ui as ui
    ui._operation_feedback[ui._feedback_key('facial',context)]=dict(primary=primary,details=details,error=error,log=log)
    ui.vta_invalidation.request_redraw()


def cancel_job():
    global _job
    job=_job
    if not job:return
    job['cancelled']=True
    try:
        try:job['stop'].touch(exist_ok=True)
        except OSError:pass # External cleanup must not prevent cancellation.
        if job['process'].poll() is None:
            job['process'].terminate()
            try:job['process'].wait(timeout=2)
            except subprocess.TimeoutExpired:job['process'].kill();job['process'].wait(timeout=2)
    finally:
        finish(job);_job=None
        if bpy.app.timers.is_registered(poll_job):bpy.app.timers.unregister(poll_job)


def finish(job):
    job['lock'].__exit__(None,None,None)


def poll_job():
    global _job
    job=_job
    if job is None:return None
    if job['process'].poll() is None:return .1
    _job=None
    published=False
    try:
        if job['cancelled']:return None
        if bpy.data.filepath!=job['blend'] or bpy.context.window.scene != job['scene']:
            raise ValueError('Scene/file changed during facial capture; animation unchanged.')
        from . import vta_review_ui as ui
        ui.require_loaded_context(bpy.context)
        pid,source,location,_=ui._timing_source(bpy.context)
        if (pid!=job['request']['project_id'] or source!=job['request']['source']
                or scene_id(job['scene'])!=job['request']['scene_id']
                or location.blender_frame-location.source_frame!=job['offset']):
            raise ValueError('Facial Scene/source timing changed during capture; animation unchanged.')
        if sha(job['root']/'project_state.json')!=job['project_hash'] or sha(job['video'])!=job['video_hash']:
            raise ValueError('Assignments/source changed during facial capture; retry.')
        for obj,stamp in job['stamps']:
            if target_stamp(obj)!=stamp:raise ValueError('Facial target changed during capture; animation unchanged.')
        # Sharing/Scene membership can change without changing a shape-key
        # pointer or its curves. Recheck all target hazards at publication.
        targets(job['scene'],[{k:binding[k] for k in ('project_id','source_id','scene_id','character_id')}
                for _,binding in job['targets']],job['core'].CHANNELS)
        raw=json.loads(job['output'].read_text())
        if raw.get('status')=='cancelled':return None
        if raw.get('status')=='failed':raise ValueError(raw.get('details','Face provider failed'))
        core=job['core'];evidence=core.validate_evidence(raw,job['request'])
        store=core.FacialStore(job['root'],job['request']['project_id'])
        before,after,_=store.prepare(evidence,job['request'])
        stages=[]
        try:
            for obj,binding in job['targets']:
                frames=next(c['frames'] for c in evidence['characters'] if c['character_id']==binding['character_id'])
                stages.append(stage(obj,binding,core.derive_animation(frames,job['request']['source']['fps']),job['offset']))
        except Exception:
            rollback(stages);raise
        publish(stages,lambda:store.commit(before,after))
        published=True
        observed=sum(r['state']=='observed' for c in evidence['characters'] for r in c['frames'])
        total=sum(len(c['frames']) for c in evidence['characters'])
        job['scene'].frame_set(job['scene'].frame_current)
        feedback(bpy.context,f'Facial animation ready: {observed}/{total} observations.',
            'Approximate capture; gaps are derived animation, not measured neutral. tongueOut unsupported.\n'+job['target_report'])
    except Exception as exc:
        message=('Facial animation saved; viewport refresh failed.' if published
                 else 'Facial generation failed; prior animation retained.')
        feedback(bpy.context,message,str(exc),True,job['log'])
    finally:finish(job)
    return None


class BLENDCAP_OT_vta_face_bind(bpy.types.Operator):
    bl_idname='blendcap.vta_face_bind'
    bl_label='Bind Active Facial Mesh'
    bl_description='Bind the active compatible mesh to exactly one selected VTA Character'

    @classmethod
    def poll(cls,context):
        from . import vta_review_ui as ui
        return ui._source_actions_available(context) and len(ui.tracking_character_ids(context))==1 and context.active_object is not None

    def execute(self,context):
        from . import vta_review_ui as ui
        try:
            if ui._analysis_locked():raise ValueError('Wait for the running VTA operation.')
            ui.require_loaded_context(context)
            pid,source,_,_=ui._timing_source(context)
            cid,=ui.tracking_character_ids(context)
            core=ui._get_backend(context)._core.facial
            from .vta_scene_characters import allowed
            project=ui._get_backend(context)._core.load_project_state(ui._location_from_context(context,allow_outside=True).companion_root)
            if cid not in allowed(context.scene,project):raise ValueError('Character does not belong to this VTA Scene.')
            if not context.scene.get('vta_scene_owner_id') and not context.scene.get('vta_face_scene_id'):
                context.scene['vta_face_scene_id']=uuid4().hex
            result=bind(context.scene,context.active_object,scope(context.scene,pid,source['source_id'],cid),core.CHANNELS)
            details='Unmatched expressions: '+', '.join(sorted(set(core.CHANNELS)-set(result['mapping'])))+'; tongueOut unsupported.'
            unmatched=set(context.active_object.data.shape_keys.key_blocks.keys())-{'Basis',*result['mapping'].values()}
            details+='\nUnanimated target keys: '+', '.join(sorted(unmatched))
            feedback(context,'Facial mesh bound: '+context.active_object.name,details)
            return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}


class BLENDCAP_OT_vta_face_generate(bpy.types.Operator):
    bl_idname='blendcap.vta_face_generate'
    bl_label='Generate Facial Animation Range'
    bl_description='Same source, selected Character rows and normal animation range; approximate facial expressions'
    cancel: bpy.props.BoolProperty(default=False)

    @classmethod
    def poll(cls,context):
        from . import vta_review_ui as ui
        return busy() or (ui._source_actions_available(context) and bool(ui.tracking_character_ids(context)))

    def execute(self,context):
        global _job
        from . import vta_review_ui as ui
        lock=None;process=None
        if self.cancel:
            cancel_job();feedback(context,'Facial capture cancelled; prior animation retained.');return {'FINISHED'}
        try:
            if ui._analysis_locked():raise ValueError('Wait for the running VTA operation.')
            ui.require_loaded_context(context)
            pid,source,location,_=ui._timing_source(context)
            project_hash=sha(location.companion_root/'project_state.json')
            project=ui._get_backend(context)._core.load_project_state(location.companion_root)
            core=ui._get_backend(context)._core.facial
            ids=ui.tracking_character_ids(context)
            from .vta_scene_characters import allowed
            if not set(ids)<=allowed(context.scene,project):raise ValueError('Selected Characters do not belong to this Scene.')
            selected=targets(context.scene,[scope(context.scene,pid,source['source_id'],cid) for cid in ids],core.CHANNELS)
            offset=location.blender_frame-location.source_frame
            request=core.make_request(project,source['source_id'],ids,
                context.window_manager.blendcap_vta_animation_start-offset,
                context.window_manager.blendcap_vta_animation_end-offset,scene_id=scene_id(context.scene))
            bundle=Path(__file__).resolve().parents[1];python=Path(ui._python_exe())
            models=bundle/'checkpoints/mediapipe'
            # Validate presence/hash in the independent worker; no runtime downloads.
            if not python.is_file():raise ValueError('Install the existing BlendCap Python runtime first.')
            if os.name=='nt' and len(str(location.companion_root/'facial'/('0'*64+'.json')))>=260:
                raise ValueError('Facial project path is too long for this Windows runtime; Save As to a shorter location first.')
            from ..blendcap_vta_operation_lock import acquire
            lock=acquire(location.companion_root/'analysis/vta-analysis.lock',operation='facial',request_id=request['run_id'])
            lock.__enter__()
            if sha(location.companion_root/'project_state.json')!=project_hash:
                raise ValueError('Project changed while preparing facial capture; retry.')
            directory=location.companion_root/'facial'/'jobs'/request['run_id'];directory.mkdir(parents=True,exist_ok=False)
            core.atomic_json(directory/'request.json',request)
            output=directory/'result.json';stop=directory/'stop';log=directory/'worker.log'
            with log.open('x') as stream:
                process=subprocess.Popen([str(python),'-I','-B',str(bundle/'vta_face_worker.py'),'--request',str(directory/'request.json'),
                    '--output',str(output),'--video',str(location.source_path),'--models',str(models),'--stop',str(stop)],
                    stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            _job=dict(process=process,lock=lock,request=request,core=core,root=location.companion_root,
                video=location.source_path,video_hash=source['video_sha256'],project_hash=project_hash,
                targets=selected,stamps=[(obj,target_stamp(obj)) for obj,_ in selected],offset=offset,scene=context.scene,
                blend=bpy.data.filepath,output=output,stop=stop,log=log,cancelled=False,
                target_report='\n'.join(obj.name+': '+str(len(binding['mapping']))+' supported controls; missing '+
                    ', '.join(sorted(set(core.CHANNELS)-set(binding['mapping']))) for obj,binding in selected))
            bpy.app.timers.register(poll_job,first_interval=.1)
            feedback(context,'Capturing selected facial expressions…','Prior animation stays intact until complete.',log=log)
            return {'FINISHED'}
        except Exception as exc:
            if process and process.poll() is None:process.terminate();process.wait(timeout=2)
            if lock:lock.__exit__(None,None,None)
            _job=None
            feedback(context,'Facial capture could not start.',str(exc),True)
            return {'CANCELLED'}


def draw(layout,context):
    from . import vta_review_ui as ui
    box=layout.box();box.label(text='Facial Animation · approximate capture')
    box.operator('blendcap.vta_face_bind')
    if busy():box.operator('blendcap.vta_face_generate',text='Cancel Facial Capture').cancel=True
    else:box.operator('blendcap.vta_face_generate')
    box.label(text='Uses range above and selected Character rows.')
    ui.draw_operation_feedback(box,context,'facial')
