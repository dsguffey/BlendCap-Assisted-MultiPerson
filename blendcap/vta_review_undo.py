"""Durable mutation recovery, independent of Blender native Undo history."""
from contextlib import contextmanager
from pathlib import Path
import json, os, uuid
import bpy
from .. import vta_protocol as wire
_pending={};_error='';_presentation_warning='';_native_bindings=None
PENDING_FILE='.vta-host-pending.json'

class AppliedMutationError(RuntimeError):
    """Authority committed; presentation/recovery still needs explicit retry."""

def _client(context=None):
    from . import vta_review_ui as ui
    return ui._engine_client(context or bpy.context)

def _pending_text(root):
    return next((t for t in bpy.data.texts if t.get('vta_reconciliation_pending')==root),None)

def _validate(intent):
    wire.fields(intent,('root','file','request_id','operation','payload','before','kind',*(('binding',) if 'binding' in intent else ())))
    wire.path_form(intent['root']);wire.checkpoint(intent['before'])
    wire.require(intent['kind'] in ('capture','rename','mutation'),'Invalid pending intent.')
    expected={'capture':('project-state',),'rename':('character-rename',),'mutation':wire.MUTATIONS}[intent['kind']]
    wire.require(intent['operation'] in expected,'Invalid pending operation.')
    wire.request(intent['request_id'],intent['payload'],intent['operation'])
    wire.require(intent['payload']['companion_root']==intent['root'],'Pending root mismatch.')
    if intent['kind'] in ('rename','mutation'):
        wire.require(intent['payload']['project_id']==intent['before']['project_id']
            and intent['payload']['expected_revision']==intent['before']['revision'],'Pending intent revision mismatch.')
    if 'binding' in intent:
        wire.require(intent['operation']=='character-create','Unexpected native binding intent.')
        binding=intent['binding'];wire.fields(binding,('scene_owner_id','project_id','source_id','video_sha256'))
        wire.identifier(binding['scene_owner_id']);wire.text(binding['project_id']);wire.text(binding['source_id']);wire.digest(binding['video_sha256'])
        wire.require(binding['project_id']==intent['before']['project_id'],'Binding project mismatch.')
    return intent

def _read_intent(intent):
    # Preserve unresolved v2 recovery, but never reinterpret an old Undo request
    # as a new mutation. Such requests need explicit resolution in the old build.
    if 'desired_snapshot' in intent:
        if intent.get('kind')=='restore' or intent['desired_snapshot'] is not None:
            raise RuntimeError('Legacy Undo recovery is unresolved; use the prior M2 build to resolve it before upgrading.')
        wire.fields(intent,('root','file','request_id','operation','payload','before','kind','desired_snapshot'))
        before=intent['before'];wire.fields(before,('project_id','revision','snapshot_id'))
        wire.require(before['snapshot_id']==before['revision']['sha256'],'Invalid legacy recovery checksum.')
        intent={**intent,'before':{k:v for k,v in before.items() if k!='snapshot_id'}}
        del intent['desired_snapshot']
        if intent['kind']=='capture':intent['operation']='project-state'
    return _validate(intent)

def _marker(root):
    path=Path(root)/PENDING_FILE
    if not path.resolve().is_relative_to(Path(root).resolve()):raise ValueError('Recovery marker escapes project.')
    return path

def _mark(intent):
    global _error
    _validate(intent);raw=wire.dumps(intent).decode();root=intent['root'];_pending[root]=intent
    text=_pending_text(root)
    if text is None:
        text=bpy.data.texts.new('.VTA Reconciliation Pending');text.use_fake_user=True
        text['vta_reconciliation_pending']=root
    text.clear();text.write(raw)
    path=_marker(root);temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8',newline='\n') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    _error='MoCap reconciliation pending. Use Retry Reconciliation before further domain edits.'

def _unmark(root):
    global _error
    _marker(root).unlink(missing_ok=True)
    text=_pending_text(root)
    if text is not None:bpy.data.texts.remove(text)
    _pending.pop(root,None);_error='' if not _pending else 'MoCap reconciliation pending.'

def require_ready():
    if blocked():raise RuntimeError(_error or 'MoCap reconciliation pending.')


def blocked():
    """Cheap presentation predicate; never reads or changes authoritative state."""
    return bool(_pending or _error)

def _intent(root,before,kind,operation,payload):
    return dict(root=root,file=bpy.data.filepath,request_id=uuid.uuid4().hex,
                operation=operation,payload=payload,before=before,kind=kind)

def _send(intent,client):
    try:return client.call(intent['operation'],intent['payload'],request_id=intent['request_id'])
    except Exception:
        status=client.call('mutation-status',dict(companion_root=intent['root'],mutation_id=intent['request_id']))
        if status['state']=='applied':
            result=status['receipt']
            wire.validate_response(wire.response(intent['request_id'],intent['operation'],result=result),intent['request_id'],intent['operation'])
            wire.validate_target(intent['operation'],intent['payload'],result)
            return result
        if intent['kind'] in ('rename','mutation'):_unmark(intent['root'])
        raise

def rename(context,root,observed,name):
    require_ready();root=str(Path(root).resolve());client=_client(context);before=observed['checkpoint']
    payload=dict(companion_root=root,project_id=before['project_id'],character_id=observed['character_id'],display_name=name,expected_revision=before['revision'])
    intent=_intent(root,before,'rename','character-rename',payload);_mark(intent)
    result=_send(intent,client)
    try:_refresh()
    except Exception as exc:
        # Failure retains durable intent. The operator must not report an abort.
        raise AppliedMutationError('Character renamed; presentation reconciliation pending: '+str(exc)) from exc
    _unmark(root);return result

def _adopt_creation(intent,result,context):
    if 'binding' not in intent:return
    binding=intent['binding']
    from . import vta_character_bindings
    vta_character_bindings.record(intent['root'],binding,result['character']['character_id'],result['request_id'])
    candidates=[s for s in bpy.data.scenes if (s.get('vta_project_id'),s.get('vta_source_id'),s.get('vta_source_video_sha256'))==
        (binding['project_id'],binding['source_id'],binding['video_sha256'])]
    exact=[s for s in candidates if s.get('vta_scene_owner_id')==binding['scene_owner_id']]
    if len(exact)==1:scene=exact[0]
    elif not exact and len(candidates)==1 and not candidates[0].get('vta_scene_owner_id'):
        # Explicit Retry after native Undo may have removed only the host ID.
        # A unique matching owned source is sufficient; never choose among Scenes.
        scene=candidates[0];scene['vta_scene_owner_id']=binding['scene_owner_id']
    else:raise RuntimeError('Character was saved; its intended Scene binding is missing or ambiguous. Restore the original Scene before Retry.')
    from . import vta_scene_characters as characters
    ids=characters.stored(scene)
    if ids is None:
        from . import vta_review_ui as ui
        location=ui._location_from_context(context,allow_outside=True)
        observed=ui._get_backend(context).observe(location,force=True)
        if observed.project_id!=binding['project_id'] or observed.source['source_id']!=binding['source_id']:
            raise RuntimeError('Load the original source Scene before reconciling Character membership.')
        ids=observed.members(scene)
    characters._write(scene,ids|{result['character']['character_id']})

def submit(context,root,before,operation,payload):
    require_ready();root=str(Path(root).resolve())
    intent=_intent(root,before,'mutation',operation,payload)
    if operation=='character-create':
        scene=context.scene
        if scene.get('vta_project_id')!=before['project_id'] or not scene.get('vta_source_id'):
            raise RuntimeError('Character creation needs the matching loaded MoCap Scene.')
        identity=scene.get('vta_scene_owner_id') or uuid.uuid4().hex;wire.identifier(identity)
        scene['vta_scene_owner_id']=identity
        intent['binding']=dict(scene_owner_id=identity,project_id=before['project_id'],source_id=scene['vta_source_id'],video_sha256=scene['vta_source_video_sha256'])
    _mark(intent)
    client=_client(context);result=_send(intent,client)
    try:
        if 'presentation' in payload:
            from . import vta_review_ui as ui
            location=ui._location_from_context(context,allow_outside=True)
            ui._get_backend(context).offer_presentation(location,operation,payload,result,client.last_presentation)
        _adopt_creation(intent,result,context)
        _refresh()
    except Exception as exc:
        raise AppliedMutationError('MoCap edit saved; presentation reconciliation pending: '+str(exc)) from exc
    _unmark(root);return result

@contextmanager
def transaction(context,location):
    # Legacy commands remain in-process with their existing guarded writes.
    if not isinstance(getattr(context,'scene',None),bpy.types.Scene):yield {};return
    require_ready();root=str(Path(location.companion_root).resolve());client=_client(context)
    before=client.call('project-state',dict(companion_root=root))
    _mark(_intent(root,before,'capture','project-state',dict(companion_root=root)))
    try:yield {'expected_document_sha256':before['revision']['sha256']}
    finally:
        try:
            client.call('project-state',dict(companion_root=root))
            _unmark(root)
        except Exception as exc:_feedback(exc) # May already be saved; retain intent.

def _feedback(exc):
    global _error
    _error='MoCap reconciliation pending: '+str(exc);print(_error)
    from . import vta_review_ui as ui
    ui.set_operation_feedback('tracking','MoCap reconciliation pending.',_error,context=bpy.context,
        disposition='Domain edits disabled. Retry Reconciliation; saved files retain this warning.')

def _refresh():
    from . import vta_review_ui as ui
    ui._display.clear();ui._tracking_hints.clear()
    if ui.refresh(bpy.context,force=True) is False:
        raise RuntimeError(getattr(ui,'_status','MoCap presentation unavailable.'))
    ui.vta_invalidation.request_redraw()

def _bindings():
    # Diagnostic identities only, never animation snapshots or restore targets.
    scopes={(scene.name,scene.get('vta_project_id'),scene.get('vta_source_id'),
             scene.get('vta_scene_characters'),scene.camera.name if scene.camera else None)
            for scene in bpy.data.scenes if scene.get('vta_project_id')}
    return frozenset(scopes | {(scene.name,scene.get('vta_project_id'),scene.get('vta_source_id'),
                      obj.name,obj.get('vta_character_id'),obj.get('vta_output_file'))
        for scene in bpy.data.scenes for obj in scene.objects
        if obj.get('vta_character_id') and obj.get('vta_project_id')})

@bpy.app.handlers.persistent
def native_before(_unused=None,_depsgraph=None):
    global _native_bindings
    _native_bindings=_bindings()

@bpy.app.handlers.persistent
def native_changed(_unused=None,_depsgraph=None):
    # Native history owns keys/objects. It must never replay a domain mutation.
    global _presentation_warning,_native_bindings
    try:
        _refresh()
        from . import vta_review_ui as ui
        changed=_native_bindings is not None and _bindings()!=_native_bindings
        observed=getattr(ui._display,'context',None)
        if observed is not None:
            project=observed if hasattr(observed,'members') else observed[0];valid={c.character_id for c in project.characters}
            for scene in bpy.data.scenes:
                if scene.get('vta_project_id')!=project.project_id:continue
                from .vta_scene_characters import stored
                members=stored(scene)
                changed |= bool(members is not None and not members<=valid)
                changed |= any(obj.get('vta_character_id') not in valid for obj in scene.objects
                               if obj.get('vta_project_id')==project.project_id and obj.get('vta_character_id'))
        _presentation_warning=('Native bindings/output changed; MoCap decisions did not. Use Load MoCap Scene or explicit generation if needed.' if changed else '')
    except Exception as exc:
        _presentation_warning='MoCap presentation needs refresh after native Undo/Redo: '+str(exc)
        print(_presentation_warning)
    finally:_native_bindings=None

def retry(context):
    if not _pending:raise RuntimeError('No recoverable pending intent is available.')
    for root,intent in tuple(_pending.items()):
        client=_client(context)
        current=client.call('project-state',dict(companion_root=root))
        if current['project_id']!=intent['before']['project_id']:
            raise RuntimeError('Recovery belongs to another project; nothing was changed.')
        if intent['kind'] in ('rename','mutation'):
            status=client.call('mutation-status',dict(companion_root=root,mutation_id=intent['request_id']))
            if status['state']=='applied':
                wire.validate_response(wire.response(intent['request_id'],intent['operation'],result=status['receipt']),intent['request_id'],intent['operation'])
                wire.validate_target(intent['operation'],intent['payload'],status['receipt'])
            # No blind replay, including known-not-applied requests. A fresh Rename
            # is explicit correction. Applied-but-superseded remains review-required.
            if status['state']=='applied' and current['revision']!=status['receipt']['after']['revision']:
                raise RuntimeError('Project changed outside pending operation; reconciliation needs review.')
            if status['state']=='applied':_adopt_creation(intent,status['receipt'],context)
        _refresh()
        _unmark(root)

@bpy.app.handlers.persistent
def clear(_unused=None):
    global _error,_presentation_warning,_native_bindings
    _pending.clear();_error='';_presentation_warning='';_native_bindings=None

@bpy.app.handlers.persistent
def load(_unused=None):
    clear()
    if not hasattr(bpy.data,'texts'):return  # Registration under RestrictBlend.
    try:
        candidates=[wire.loads(t.as_string().encode()) for t in bpy.data.texts if t.get('vta_reconciliation_pending')]
        if bpy.data.filepath:
            file=Path(bpy.data.filepath);root=file.with_name(file.stem+'_vta');path=_marker(root)
            if path.is_file():
                if path.stat().st_size>wire.MAX_BYTES:raise ValueError('Recovery marker exceeds limit.')
                intent=wire.loads(path.read_bytes());intent['root']=str(root.resolve());intent['payload']['companion_root']=intent['root'];intent['file']=bpy.data.filepath
                intent=_read_intent(intent)
                # Save As copied this durable intent. Rebind its native warning
                # too, so successful recovery removes it from the copied .blend.
                for text in bpy.data.texts:
                    if text.get('vta_reconciliation_pending'):
                        saved=wire.loads(text.as_string().encode())
                        if saved.get('request_id')==intent['request_id']:
                            text['vta_reconciliation_pending']=intent['root'];text.clear();text.write(wire.dumps(intent).decode())
                candidates=[intent]
            elif any(Path(i['root']).resolve()!=root.resolve() for i in candidates):
                raise RuntimeError('Saved recovery references another companion; inspect the copy before retrying.')
        for intent in candidates:
            intent=_read_intent(intent);_pending[intent['root']]=intent
        if _pending:_feedback('Saved project has an unresolved operation. Retry explicitly.')
    except Exception as exc:_feedback(exc)

@bpy.app.handlers.persistent
def save_pre(_unused=None,*_args):
    # Native Save cannot be cancelled; unresolved intent must be IN the .blend.
    for intent in tuple(_pending.values()):_mark(intent)

class BLENDCAP_OT_vta_reconcile(bpy.types.Operator):
    bl_idname='blendcap.vta_reconcile';bl_label='Retry Reconciliation';bl_options={'REGISTER'}
    bl_description='Verify the pending operation and refresh MoCap without replaying the edit; available when reconciliation is pending'
    @classmethod
    def poll(cls,context):return bool(_pending)
    def execute(self,context):
        try:retry(context);return {'FINISHED'}
        except Exception as exc:_feedback(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}

def draw_pending(layout):
    if _presentation_warning:
        box=layout.box();box.label(text='Check MoCap bindings/output after native Undo.',icon='ERROR')
        box.label(text='Check bindings/output; refresh or Load MoCap Scene if needed.')
    if blocked():
        box=layout.box();box.label(text='MoCap reconciliation pending',icon='ERROR')
        box.label(text='Domain edits paused; saved files retain this state.');box.operator('blendcap.vta_reconcile')

def register():
    bpy.utils.register_class(BLENDCAP_OT_vta_reconcile);load()
    for handlers,callback in ((bpy.app.handlers.undo_pre,native_before),(bpy.app.handlers.redo_pre,native_before),
            (bpy.app.handlers.undo_post,native_changed),(bpy.app.handlers.redo_post,native_changed),
            (bpy.app.handlers.load_pre,clear),(bpy.app.handlers.load_post,load),(bpy.app.handlers.save_pre,save_pre)):
        if callback not in handlers:handlers.append(callback)

def unregister():
    for handlers,callback in ((bpy.app.handlers.undo_pre,native_before),(bpy.app.handlers.redo_pre,native_before),
            (bpy.app.handlers.undo_post,native_changed),(bpy.app.handlers.redo_post,native_changed),
            (bpy.app.handlers.load_pre,clear),(bpy.app.handlers.load_post,load),(bpy.app.handlers.save_pre,save_pre)):
        if callback in handlers:handlers.remove(callback)
    bpy.utils.unregister_class(BLENDCAP_OT_vta_reconcile);clear()
