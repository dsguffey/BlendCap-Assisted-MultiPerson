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
    wire.fields(intent,('root','file','request_id','operation','payload','before','kind'))
    wire.path_form(intent['root']);wire.checkpoint(intent['before'])
    wire.require(intent['kind'] in ('capture','rename'),'Invalid pending intent.')
    wire.require(intent['operation']=={'capture':'project-state','rename':'character-rename'}[intent['kind']],'Invalid pending operation.')
    wire.request(intent['request_id'],intent['payload'],intent['operation'])
    wire.require(intent['payload']['companion_root']==intent['root'],'Pending root mismatch.')
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
    _error='VTA reconciliation pending. Use Retry Reconciliation before further domain edits.'

def _unmark(root):
    global _error
    _marker(root).unlink(missing_ok=True)
    text=_pending_text(root)
    if text is not None:bpy.data.texts.remove(text)
    _pending.pop(root,None);_error='' if not _pending else 'VTA reconciliation pending.'

def require_ready():
    if _pending or _error:raise RuntimeError(_error or 'VTA reconciliation pending.')

def _intent(root,before,kind,operation,payload):
    return dict(root=root,file=bpy.data.filepath,request_id=uuid.uuid4().hex,
                operation=operation,payload=payload,before=before,kind=kind)

def _send(intent,client):
    try:return client.call(intent['operation'],intent['payload'],request_id=intent['request_id'])
    except Exception:
        status=client.call('mutation-status',dict(companion_root=intent['root'],mutation_id=intent['request_id']))
        if status['state']=='applied':return status['receipt']
        if intent['kind']=='rename':_unmark(intent['root'])
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
    _error='VTA reconciliation pending: '+str(exc);print(_error)
    from . import vta_review_ui as ui
    ui.set_operation_feedback('tracking','VTA reconciliation pending.',_error,context=bpy.context,
        disposition='Domain edits disabled. Retry Reconciliation; saved files retain this warning.')

def _refresh():
    from . import vta_review_ui as ui
    ui._display.clear();ui._tracking_hints.clear()
    if ui.refresh(bpy.context,force=True) is False:
        raise RuntimeError(getattr(ui,'_status','VTA presentation unavailable.'))
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
            project=observed[0];valid={c.character_id for c in project.characters}
            for scene in bpy.data.scenes:
                if scene.get('vta_project_id')!=project.project_id:continue
                from .vta_scene_characters import stored
                members=stored(scene)
                changed |= bool(members is not None and not members<=valid)
                changed |= any(obj.get('vta_character_id') not in valid for obj in scene.objects
                               if obj.get('vta_project_id')==project.project_id and obj.get('vta_character_id'))
        _presentation_warning=('Native bindings/output changed; VTA decisions did not. Use Load VTA Scene or explicit generation if needed.' if changed else '')
    except Exception as exc:
        _presentation_warning='VTA presentation needs refresh after native Undo/Redo: '+str(exc)
        print(_presentation_warning)
    finally:_native_bindings=None

def retry(context):
    if not _pending:raise RuntimeError('No recoverable pending intent is available.')
    for root,intent in tuple(_pending.items()):
        client=_client(context)
        current=client.call('project-state',dict(companion_root=root))
        if current['project_id']!=intent['before']['project_id']:
            raise RuntimeError('Recovery belongs to another project; nothing was changed.')
        if intent['kind']=='rename':
            status=client.call('mutation-status',dict(companion_root=root,mutation_id=intent['request_id']))
            # No blind replay, including known-not-applied requests. A fresh Rename
            # is explicit correction. Applied-but-superseded remains review-required.
            if status['state']=='applied' and current['revision']!=status['receipt']['after']['revision']:
                raise RuntimeError('Project changed outside pending operation; reconciliation needs review.')
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
    def execute(self,context):
        try:retry(context);return {'FINISHED'}
        except Exception as exc:_feedback(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}

def draw_pending(layout):
    if _presentation_warning:
        box=layout.box();box.label(text='Check VTA bindings/output after native Undo.',icon='ERROR')
        box.label(text='Check bindings/output; refresh or Load VTA Scene if needed.')
    if _pending or _error:
        box=layout.box();box.label(text='VTA reconciliation pending',icon='ERROR')
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
