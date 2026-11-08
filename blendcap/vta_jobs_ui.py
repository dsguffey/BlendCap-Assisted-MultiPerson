"""Main-thread native adoption of durable engine jobs. No provider/domain imports.

Background tasks carry only neutral inputs. Draw/poll uses this cached state;
reopening never replays inference or applies animation automatically.
"""
from dataclasses import replace
import hashlib,json,os,time
from pathlib import Path
from uuid import uuid4
import bpy
from .. import vta_protocol as wire
from ..blendcap_vta_jobs import ControlTask,JobHandle,EngineGenerationBackend,read_artifact
from ..blendcap_vta_engine_review import EngineReviewBackend
from ..blendcap_vta_review import companion_root_for_blend,map_visible_analysis_range

_active=None
_saved={}
_messages={}
_statuses={}
_pointer_error=''
BUSY={'prepared','starting','running','awaiting-decision'}

def _root():
    return companion_root_for_blend(bpy.data.filepath).resolve()

def _identity(scene):
    identity=scene.get('vta_scene_owner_id') or uuid4().hex;wire.identifier(identity)
    if any(s is not scene and s.get('vta_scene_owner_id')==identity for s in bpy.data.scenes):
        raise ValueError('Duplicate Scene identity; explicitly load a uniquely owned MoCap Scene.')
    scene['vta_scene_owner_id']=identity;return identity

def _key(scene,kind):return (scene.get('vta_scene_owner_id'),kind)

def _validate(value):
    wire.fields(value,('version','scene_owner_id','project_id','source_id','job_id','kind','settled',*(('native_sha256',) if 'native_sha256' in value else ())))
    wire.require(value['version']==1 and type(value['version']) is int,'Unknown host job pointer.')
    for field in ('scene_owner_id','job_id'):wire.identifier(value[field])
    for field in ('project_id','source_id'):wire.text(value[field])
    wire.require(value['kind'] in ('analysis','generation') and type(value['settled']) is bool,'Invalid host job pointer.')
    if value.get('native_sha256') is not None:wire.digest(value['native_sha256'])
    return value

def _path(root,value):return root/'.vta-host-jobs'/(value['scene_owner_id']+'-'+value['kind']+'.json')

def _persist(root,value):
    _validate(value);path=_path(root,value)
    if not path.resolve().is_relative_to(root):raise ValueError('Host job pointer escapes companion.')
    path.parent.mkdir(exist_ok=True);temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    _saved[(value['scene_owner_id'],value['kind'])]=value.copy()

def busy(root=None):
    return _active is not None and (root is None or Path(root).resolve()==_active['root'])

def analysis_presentation(root,source_id=None):
    """Compatibility formatting from cached job DTOs, never legacy status files."""
    return next((value for (saved_root,sid),value in _statuses.items()
        if saved_root==str(Path(root).resolve()) and (source_id is None or sid==source_id)),None)

def _context(job):
    from . import vta_review_ui as ui
    scene=job['scene']
    if scene.name not in bpy.data.scenes or bpy.data.scenes[scene.name] is not scene or _root()!=job['root']:
        raise ValueError('Job belongs to another open project; use explicit recovery in its original Scene.')
    context=ui._context_for_main_scene(bpy.context)
    if context.scene is not scene or not ui.loaded_context_matches(context):
        raise ValueError('Load the original MoCap Scene, then Recover Job to apply its result.')
    if (scene.get('vta_project_id'),scene.get('vta_source_id'))!=(job['pointer']['project_id'],job['pointer']['source_id']):
        raise ValueError('Job source/Scene identity changed; no native output was applied.')
    _identity(scene);return context

def _runtime(context):
    from . import vta_review_ui as ui
    from ..blendcap_vta_runtime import load_runtime
    from ..vta_protocol.runtime import provider_runtime
    runtime=load_runtime();engine=ui._engine_client(context).endpoint.launcher.parent
    if runtime['public_root']!=Path(__file__).resolve().parents[1] or runtime['engine_root']!=engine:
        raise ValueError('Runtime configuration does not match the loaded adapter/engine. Run setup/repair.')
    return provider_runtime(runtime)


def _launch(job,phase,fn):
    job['phase']=phase;job['task'].submit(fn);job['next']=time.monotonic()+.3

def _backend(client):return EngineGenerationBackend(client,EngineReviewBackend(client))

def _start(context,kind,*,recover=False):
    global _active
    from . import vta_review_ui as ui
    from . import vta_review_undo as recovery
    if _pointer_error:raise ValueError('Job recovery metadata is invalid; no new work was started. '+_pointer_error)
    recovery.require_ready();ui.require_loaded_context(context);context=ui._context_for_main_scene(context)
    if busy():raise ValueError('Wait for the current MoCap job or cancel it.')
    scene=context.scene;identity=_identity(scene);location=ui._location_from_context(context,allow_outside=True)
    root=Path(location.companion_root).resolve();client=ui._engine_client(context);backend=_backend(client)
    pointer=_saved.get((identity,kind))
    if recover:
        if pointer is None:raise ValueError('No saved job exists for this Scene.')
        if (pointer['project_id'],pointer['source_id'])!=(scene.get('vta_project_id'),scene.get('vta_source_id')):
            raise ValueError('Saved job does not belong to this Scene source.')
        handle=JobHandle(root,pointer['job_id'],kind,client)
    else:
        if pointer and not pointer['settled']:raise ValueError('Recover or discard the prior unresolved job before starting another.')
        pointer=dict(version=1,scene_owner_id=identity,project_id=scene['vta_project_id'],source_id=scene['vta_source_id'],job_id=uuid4().hex,kind=kind,settled=False,native_sha256=None)
        handle=JobHandle(root,pointer['job_id'],kind,client)
    job=dict(root=root,scene=scene,location=location,pointer=pointer,handle=handle,backend=backend,task=ControlTask(),phase='preparing',cancel=False,status=None,next=0)
    if recover:
        _active=job;_launch(job,'status',lambda:handle.call('job-status'))
    else:
        info=ui.cached_scene_source(context)
        if not info:raise ValueError('Load and validate a source before starting a job.')
        source=info[1];domain=(source['start_frame'],source['end_frame']);wm=context.window_manager
        runtime=_runtime(context)
        if kind=='generation':
            ids=tuple(ui.tracking_character_ids(context))
            if not ids:raise ValueError('Select at least one Character.')
            from .vta_scene_characters import stored
            members=stored(scene)
            if members is not None and not set(ids)<=members:raise ValueError('Select reviewed Characters belonging to this Scene.')
            offset=location.blender_frame-location.source_frame
            selected=(wm.blendcap_vta_animation_start-offset,wm.blendcap_vta_animation_end-offset)
            from .vta_generation import plan_options,owners,fingerprint
            plan_options(selected,domain)
            from .vta_camera_cleanup import require_source_aligned
            require_source_aligned(scene,ids,pointer['project_id'],pointer['source_id'])
            mode='AUTOMATIC' if scene.blendcap_vta_automatic_depth else 'FIXED'
            from .vta_depth_generation import preflight
            targets=owners(scene,pointer['project_id'],pointer['source_id'])
            preflight([o for o in targets if o.type=='ARMATURE' and o.get('vta_character_id') in ids],mode,source['video_sha256'],bounded=selected!=domain)
            stamps=sorted({fingerprint(o) for o in targets});fov=scene.blendcap_props.fov_mode
            def prepare():
                prepared,plan=backend.prepare(location,ids,selected,fov_mode=fov,depth_mode=mode,previous_fingerprints=stamps,request_id=pointer['job_id'])
                return prepared,plan
        else:
            selected=map_visible_analysis_range(location,*domain,wm.blendcap_vta_analyze_start,wm.blendcap_vta_analyze_end) if wm.blendcap_vta_analyze_scope=='RANGE' else domain
            def prepare():return backend.prepare_analysis(location,selected,request_id=pointer['job_id'])
        job['runtime']=runtime;_persist(root,pointer);_active=job;_launch(job,'prepare',prepare)
    _messages[_key(scene,kind)]='Preparing isolated '+kind+' job…'
    if kind=='generation':
        ui.clear_operation_feedback('animation',context);ui._generation_status='';ui._generation_details=''
    if not bpy.app.timers.is_registered(tick):bpy.app.timers.register(tick,first_interval=.05)

def start_generation(context):return _start(context,'generation')
def start_analysis(context):return _start(context,'analysis')

def request_cancel():
    if _active is None:return False
    _active['cancel']=True;return True

def _native_inputs(job):
    context=_context(job);scene=context.scene;pointer=job['pointer']
    from .vta_generation import owners,fingerprint
    targets=owners(scene,pointer['project_id'],pointer['source_id'])
    cameras=[o for o in targets if o.type=='CAMERA']
    if len(cameras)>1:raise ValueError('Multiple MoCap source cameras; previous animation retained.')
    saved=json.loads(cameras[0].data['vta_scene_reference']) if cameras and cameras[0].data.get('vta_scene_reference') else None
    from .vta_output_restore import fingerprint as native_fingerprint
    job['native_stamp']=[(o,native_fingerprint(o)) for o in targets]
    return sorted({fingerprint(o) for o in targets}),saved

def _accepted(job):
    stamps,saved=_native_inputs(job);backend=job['backend'];location=job['location'];handle=job['handle']
    # Bind mutable validation inputs locally in the worker, never read RNA there.
    def work():
        result=handle.result();paths=[job['root']/p for p in result['outputs']];values=[];current_saved=saved;current_stamps=stamps
        for index,path in enumerate(paths):
            value=backend.validate_output(location,path,previous_fingerprints=current_stamps,saved_reference=current_saved,migration_approved=len(paths)==2 and index==0)
            values.append(value)
            if len(paths)==2:current_saved=value['reference'];current_stamps=[value['report']['reconstruction']['fingerprint']]
        return paths,values
    _launch(job,'validating-native-output',work)

def _accept(job):
    from . import vta_review_undo as recovery
    _context(job);recovery.require_ready();client=job['handle'].client
    def prepare_accept():
        before=client.call('project-state',dict(companion_root=str(job['root'])))
        return before
    _launch(job,'accept-preflight',prepare_accept)

def _apply(job,paths,values):
    if job['cancel']:
        _settled(job,'Native application cancelled; prior animation retained. Accepted result remains recoverable.');return
    context=_context(job);scene=context.scene
    requested=values[-1]
    current_depth='AUTOMATIC' if scene.blendcap_vta_automatic_depth else 'FIXED'
    if current_depth!=requested['depth_mode'] or scene.blendcap_props.fov_mode!=requested['report']['reconstruction']['mode']:
        raise ValueError('Generation settings changed while the job ran; native animation retained. Restore the job settings before explicit recovery.')
    from .vta_scene_characters import stored
    members=stored(scene)
    if members is not None and not set(requested['report']['plan']['character_scope'])<=members:
        raise ValueError('Scene Character membership changed; result retained without native application.')
    from . import vta_output_restore as restore
    for obj,stamp in job['native_stamp']:
        if scene.objects.get(obj.name) is not obj or restore.fingerprint(obj)!=stamp:raise ValueError('Native animation changed during validation; prior animation retained. Recover explicitly.')
    result=job['status']['result']
    def stamp():
        from .vta_generation import owners
        return hashlib.sha256(repr(sorted((o.get('vta_character_id',''),o.type,restore.fingerprint(o))
            for o in owners(scene,job['pointer']['project_id'],job['pointer']['source_id']))).encode()).hexdigest()
    if job['pointer'].get('native_sha256')!=stamp():
        candidate=restore.capture(scene,job['pointer']['project_id'],job['pointer']['source_id'],companion_root=job['root'],job_id=job['handle'].job_id,result_sha256=result['sha256'])
        try:
            from .vta_generation import import_generation
            import_generation(paths,job['location'],job['backend'],migration=len(paths)==2,depth_mode=values[-1]['depth_mode'],validated=values)
        except Exception:
            restore.discard(candidate);raise
        restore.publish(scene,candidate)
    # The live output digest prevents explicit recovery from replacing it twice.
    # Engine application acknowledgement is observational, not a native Save.
    job['pointer'].update(settled=True,native_sha256=stamp());_persist(job['root'],job['pointer'])
    receipt=dict(application_id=uuid4().hex,state='applied',artifact_sha256=result['sha256'])
    _launch(job,'recording-native-application',lambda:job['handle'].call('job-application',**receipt))

def _settled(job,message):
    global _active
    job['pointer']['settled']=True;_persist(job['root'],job['pointer'])
    _messages[_key(job['scene'],job['pointer']['kind'])]=message;_active=None
    from . import vta_review_ui as ui
    ui._cache_signature=None;ui._display.clear();ui.vta_invalidation.request_redraw()

def _failure_feedback(job,details):
    if job['pointer']['kind']=='generation':
        from types import SimpleNamespace
        from . import vta_review_ui as ui
        ui.set_operation_feedback('animation','Couldn’t update the animation.',details,
            context=SimpleNamespace(scene=job['scene'],window_manager=bpy.context.window_manager),
            disposition='Prior animation retained. Recover or discard the job explicitly.',
            log=job['root']/'.vta-engine/jobs'/job['handle'].job_id/'job.log')

def _status(job,status):
    if (status['project_id'],status['source_id'])!=(job['pointer']['project_id'],job['pointer']['source_id']):
        raise ValueError('Job project/source differs from the intended Scene; no native output applied.')
    job['status']=status;state=status['state']
    if job['pointer']['kind']=='analysis':
        bounds=(job.get('plan') or {}).get('plan',{}).get('frame_range',[0,0]);progress=status['progress']
        _statuses[(str(job['root']),status['source_id'])]=dict(state='complete' if state=='ready' else state,
            error=(status['error'] or {}).get('message'),start_frame=bounds[0],end_frame=bounds[1],
            completed_in_requested_range=progress['completed'] if progress else bounds[1]-bounds[0]+1 if state=='ready' else 0)
    if job['cancel'] and state=='accepted':
        _settled(job,'Native application cancelled; accepted result retained for explicit recovery.');return
    if job.get('discard') and state in ('accepted','ready','failed','cancelled'):
        _settled(job,'Job dismissed; accepted artifacts and native animation retained.');return
    if job['cancel'] and state not in ('accepted','cancelled','failed'):
        job['cancel']=False;_launch(job,'cancel',lambda:job['handle'].call('job-cancel'));return
    if state=='prepared':
        # Recover never launches an unstarted job automatically.
        if 'runtime' not in job:raise ValueError('Prepared job never started. Discard it, then start a new explicit request.')
        _launch(job,'starting',lambda:job['handle'].call('job-start',runtime=job['runtime']));return
    if state in ('cancelled','failed'):
        if state=='failed':_failure_feedback(job,(status['error'] or {}).get('message','Job failed.'))
        from ..blendcap_vta_errors import primary
        _settled(job,'Cancelled; prior animation retained.' if state=='cancelled' else primary((status['error'] or {}).get('code')));return
    if state=='unknown':raise ValueError('Job outcome is unknown. No rerun or application was attempted. Inspect logs, then discard explicitly.')
    if state in ('ready','accepted'):
        if job['pointer']['kind']=='analysis':_settled(job,'Analysis complete; candidate checkpoints saved.');return
        if state=='ready':_accept(job)
        else:_accepted(job)
        return
    if state=='awaiting-decision':
        job['phase']='awaiting-decision';return
    job['phase']='waiting';job['next']=time.monotonic()+.5

def tick():
    global _active
    job=_active
    if job is None:return None
    try:
        if job['task'].thread is not None:
            if job['task'].thread.is_alive():return .1
            value=job['task'].take();phase=job['phase']
            if phase=='prepare':
                handle,plan=value;job['handle']=handle;job['plan']=plan;_status(job,handle.status)
            elif phase=='accept-preflight':
                from . import vta_review_undo as recovery
                _context(job);payload=dict(companion_root=str(job['root']),project_id=value['project_id'],expected_revision=value['revision'],job_id=job['handle'].job_id,artifact_sha256=job['status']['result']['sha256'])
                intent=recovery._intent(str(job['root']),value,'mutation','generation-accept',payload);recovery._mark(intent)
                job['intent']=intent
                def send():
                    try:return job['handle'].client.call('generation-accept',payload,request_id=intent['request_id'])
                    except Exception:
                        status=job['handle'].client.call('mutation-status',dict(companion_root=str(job['root']),mutation_id=intent['request_id']))
                        if status['state']!='applied':raise
                        return status['receipt']
                _launch(job,'accepting',send)
            elif phase=='accepting':
                from . import vta_review_undo as recovery
                recovery._unmark(str(job['root']));_launch(job,'status',lambda:job['handle'].call('job-status'))
            elif phase=='validating-native-output':_apply(job,*value)
            elif phase=='recording-native-application':_settled(job,'Animation updated; previous native output retained when compatible.')
            else:_status(job,value)
        elif job['cancel']:
            job['cancel']=False;_launch(job,'cancel',lambda:job['handle'].call('job-cancel'))
        elif job['phase'] in ('waiting','awaiting-decision') and time.monotonic()>=job['next']:
            _launch(job,'status',lambda:job['handle'].call('job-status'))
        if _active is not job:return None
        status=job['status'];progress=status.get('progress') if status else None
        label=(status['phase'] if status and job['phase']=='waiting' else job['phase']).replace('-',' ')
        if progress:label+=f" {progress['completed']}/{progress['total']}"
        _messages[_key(job['scene'],job['pointer']['kind'])]=label
        from . import vta_review_ui as ui
        ui.vta_invalidation.request_redraw()
    except Exception as exc:
        if getattr(exc,'code',None)=='JOB_NOT_FOUND':
            _settled(job,'Job did not publish a durable preparation record. Start a new explicit request.');return None
        _failure_feedback(job,str(exc))
        _messages[_key(job['scene'],job['pointer']['kind'])]='Job needs explicit recovery; prior animation retained.'
        print('[VTA job]',job['handle'].job_id,str(exc),flush=True);_active=None
    return .1 if _active is not None else None

def draw(layout,context,kind):
    if getattr(context,'scene',None) is None:return
    if _pointer_error:layout.label(text='Job recovery metadata needs attention; new jobs disabled.',icon='ERROR')
    key=_key(context.scene,kind);message=_messages.get(key)
    if message:layout.label(text=message,icon='INFO')
    if busy() and _active['scene'] is context.scene and _active['pointer']['kind']==kind:
        status = _active.get('status') or {}
        progress = status.get('progress') if _active.get('phase') == 'waiting' else None
        if progress and progress['total'] > 0:
            layout.progress(factor=min(1.0, progress['completed']/progress['total']), type='BAR',
                text=f"{progress['completed']} / {progress['total']}")
        else:
            layout.label(text='Working… ' + _active['phase'].replace('-', ' '), icon='TIME')
        if _active['phase']=='awaiting-decision':layout.operator('blendcap.vta_job_decide',text='Review Camera Calibration Update')
        cancel=layout.row()
        cancel.enabled=_active['phase']!='recording-native-application'
        cancel.operator('blendcap.vta_job_control',text='Cancel Job').action='CANCEL'
    elif key in _saved and not _saved[key].get('settled',False):
        row=layout.row(align=True)
        op=row.operator('blendcap.vta_job_control',text='Recover / Apply Job');op.action='RECOVER';op.kind=kind
        op=row.operator('blendcap.vta_job_control',text='Discard Job');op.action='DISCARD';op.kind=kind
    if kind=='generation':
        from .vta_output_restore import slot
        if slot(context.scene) is not None:layout.operator('blendcap.vta_restore_output')

class BLENDCAP_OT_vta_job_control(bpy.types.Operator):
    bl_idname='blendcap.vta_job_control';bl_label='MoCap Job';bl_options=set()
    action:bpy.props.EnumProperty(items=[(k,k,'') for k in ('RECOVER','CANCEL','DISCARD')])
    kind:bpy.props.EnumProperty(items=[(k,k,'') for k in ('generation','analysis')])
    def execute(self,context):
        try:
            if self.action=='CANCEL':request_cancel()
            elif self.action=='RECOVER':_start(context,self.kind,recover=True)
            else:
                _start(context,self.kind,recover=True);_active['discard']=True;request_cancel()
            return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}

class BLENDCAP_OT_vta_job_decide(bpy.types.Operator):
    bl_idname='blendcap.vta_job_decide';bl_label='Update Source Camera Calibration';bl_options=set()
    job_id:bpy.props.StringProperty(options={'HIDDEN','SKIP_SAVE'})
    def invoke(self,context,event):
        if not _active or _active['phase']!='awaiting-decision':return {'CANCELLED'}
        self.job_id=_active['handle'].job_id
        return context.window_manager.invoke_props_dialog(self,width=520)
    def draw(self,context):
        self.layout.label(text='Update the shared source camera and existing animation?')
        self.layout.label(text='All existing Characters must be selected. Native Depth keys are preserved.')
    def execute(self,context):
        try:
            job=_active
            if not job or job['handle'].job_id!=self.job_id:raise ValueError('Calibration request expired.')
            _context(job)
            # Recover obtains the immutable plan through the same bounded manifest.
            if 'plan' not in job:raise ValueError('Discard and retry this unconfirmed job after restart; no animation changed.')
            wrapper=job['plan'];source=wrapper['source'];plan=wrapper['plan']
            from .vta_generation import migration_range
            bounds=migration_range(job['scene'],plan,'pending-source-calibration',(source['start_frame'],source['end_frame']),job['location'].blender_frame-job['location'].source_frame)
            _launch(job,'decision',lambda:job['handle'].call('job-decision',accept=True,frame_range=list(bounds or plan['source_range'])))
            return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}
    def cancel(self,context):
        if _active and _active['handle'].job_id==self.job_id:request_cancel()

class BLENDCAP_OT_vta_restore_output(bpy.types.Operator):
    bl_idname='blendcap.vta_restore_output';bl_label='Restore Previous Output';bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        from .vta_output_restore import slot
        from . import vta_marks,vta_busy
        return not busy() and not vta_marks.busy() and not vta_busy.busy() and getattr(context,'scene',None) is not None and slot(context.scene) is not None
    def execute(self,context):
        try:
            from .vta_output_restore import restore
            restore(context.scene);return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}

@bpy.app.handlers.persistent
def clear(_unused=None):
    global _active,_pointer_error
    _active=None;_saved.clear();_messages.clear();_statuses.clear()
    _pointer_error=''
    if bpy.app.timers.is_registered(tick):bpy.app.timers.unregister(tick)

@bpy.app.handlers.persistent
def load(_unused=None):
    global _pointer_error
    clear()
    if not hasattr(bpy.data,'filepath') or not bpy.data.filepath:return
    try:
        root=_root();folder=root/'.vta-host-jobs'
        if not folder.exists():return
        paths=list(folder.iterdir())
        if len(paths)>256:raise ValueError('Too many host job pointers.')
        for path in paths:
            if path.suffix!='.json':continue
            if not path.resolve().is_relative_to(root) or path.stat().st_nlink!=1 or path.stat().st_size>16384:raise ValueError('Unsafe host job pointer.')
            value=_validate(wire.loads(path.read_bytes()))
            if path!=_path(root,value):raise ValueError('Host job pointer filename mismatch.')
            _saved[(value['scene_owner_id'],value['kind'])]=value
    except Exception as exc:
        _pointer_error=str(exc);print('[MoCap jobs recovery]',_pointer_error,flush=True)

CLASSES=(BLENDCAP_OT_vta_job_control,BLENDCAP_OT_vta_job_decide,BLENDCAP_OT_vta_restore_output)
def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.app.handlers.load_pre.append(clear);bpy.app.handlers.load_post.append(load);load()

def unregister():
    clear()
    for callbacks,fn in ((bpy.app.handlers.load_pre,clear),(bpy.app.handlers.load_post,load)):
        if fn in callbacks:callbacks.remove(fn)
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
