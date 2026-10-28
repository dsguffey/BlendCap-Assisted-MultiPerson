"""Native Undo tokens backed by engine-owned checkpoints and durable recovery."""
from contextlib import contextmanager
from pathlib import Path
import json, os, uuid
import bpy
from .. import vta_protocol as wire
_entries={};_pending={};_session=uuid.uuid4().hex;_error=''
PENDING_FILE='.vta-host-pending.json'

def _client(context=None):
    from . import vta_review_ui as ui
    return ui._engine_client(context or bpy.context)

def _text(entry):
    return next((t for t in bpy.data.texts if t.get('vta_review_undo')==2 and
        t.get('vta_review_undo_session')==_session and t.get('vta_review_undo_root')==entry['root']
        and t.get('vta_review_undo_epoch')==entry['epoch']),None)

def _pending_text(root):
    return next((t for t in bpy.data.texts if t.get('vta_reconciliation_pending')==root),None)

def _validate(intent):
    wire.fields(intent,('root','file','request_id','operation','payload','before','kind','desired_snapshot'))
    wire.path_form(intent['root']);wire.checkpoint(intent['before'])
    wire.require(intent['kind'] in ('capture','rename','restore'),'Invalid pending intent.')
    wire.require(intent['operation']=={'capture':'review-checkpoint','rename':'character-rename','restore':'review-restore'}[intent['kind']],'Invalid pending operation.')
    wire.request(intent['request_id'],intent['payload'],intent['operation'])
    wire.require(intent['payload']['companion_root']==intent['root'],'Pending root mismatch.')
    if intent['desired_snapshot'] is not None:wire.digest(intent['desired_snapshot'])
    return intent

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

def _entry(root,checkpoint):
    entry=_entries.get(root)
    if entry is None or entry['file']!=bpy.data.filepath or entry['current']['revision']!=checkpoint['revision']:
        barrier=entry is not None
        entry=dict(root=root,file=bpy.data.filepath,base=checkpoint,current=checkpoint,
                   epoch=uuid.uuid4().hex,barrier=barrier);_entries[root]=entry
    return entry

def _adopt(entry,checkpoint):
    wire.checkpoint(checkpoint);token=_text(entry)
    if token is None:
        token=bpy.data.texts.new('.VTA Review Undo');token.use_fake_user=True
        token['vta_review_undo']=2;token['vta_review_undo_session']=_session;token['vta_review_undo_root']=entry['root']
        token['vta_review_undo_epoch']=entry['epoch']
    token.clear();token.write(checkpoint['snapshot_id']);entry['current']=checkpoint

def _intent(root,before,kind,operation,payload):
    return dict(root=root,file=bpy.data.filepath,request_id=uuid.uuid4().hex,operation=operation,payload=payload,before=before,kind=kind,desired_snapshot=None)

def _send(intent,client):
    try:return client.call(intent['operation'],intent['payload'],request_id=intent['request_id'])
    except Exception:
        status=client.call('mutation-status',dict(companion_root=intent['root'],mutation_id=intent['request_id']))
        if status['state']=='applied':return status['receipt']
        if intent['kind']=='rename':_unmark(intent['root'])
        raise

def rename(context,root,observed,name):
    require_ready();root=str(Path(root).resolve());client=_client(context);before=observed['checkpoint'];entry=_entry(root,before)
    payload=dict(companion_root=root,project_id=before['project_id'],character_id=observed['character_id'],display_name=name,expected_revision=before['revision'])
    intent=_intent(root,before,'rename','character-rename',payload);_mark(intent)
    result=_send(intent,client)
    if result['changed']:_adopt(entry,result['after'])
    _unmark(root);return result

@contextmanager
def transaction(context,location):
    # Legacy commands remain in-process. Only their shared Undo storage moves.
    if not isinstance(getattr(context,'scene',None),bpy.types.Scene):yield {};return
    require_ready();root=str(Path(location.companion_root).resolve());client=_client(context)
    before=client.call('review-checkpoint',dict(companion_root=root));entry=_entry(root,before)
    _mark(_intent(root,before,'capture','review-checkpoint',dict(companion_root=root)))
    try:yield {'expected_document_sha256':before['revision']['sha256']}
    finally:
        try:
            after=client.call('review-checkpoint',dict(companion_root=root))
            if after['snapshot_id']!=before['snapshot_id']:_adopt(entry,after)
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
    ui._display.clear();ui._tracking_hints.clear();ui.refresh(bpy.context,force=True);ui.vta_invalidation.request_redraw()

@bpy.app.handlers.persistent
def restore(_unused=None,_depsgraph=None):
    if _pending:
        # Native Undo cannot be disabled. Remember its latest requested token
        # without changing the identity/payload of an uncertain prior request.
        for root,intent in tuple(_pending.items()):
            entry=_entries.get(root)
            if entry is not None and entry['file']==bpy.data.filepath:
                token=_text(entry);desired=token.as_string() if token is not None else entry['base']['snapshot_id']
                try:_mark({**intent,'desired_snapshot':desired})
                except Exception as exc:_feedback(exc)
        return
    changed=False
    for entry in tuple(_entries.values()):
        if entry['file']!=bpy.data.filepath:continue
        token=_text(entry);desired=token.as_string() if token is not None else entry['base']['snapshot_id']
        # Earlier native history cannot rewind an externally changed domain
        # document after a fresh explicit edit established a new baseline.
        if token is None and entry['barrier']:changed=True
        if desired==entry['current']['snapshot_id']:continue
        try:
            wire.digest(desired);before=entry['current'];root=entry['root']
            payload=dict(companion_root=root,project_id=before['project_id'],snapshot_id=desired,expected_revision=before['revision'])
            intent=_intent(root,before,'restore','review-restore',payload);_mark(intent)
            result=_send(intent,_client());entry['current']=result['after'];_unmark(root);changed=True
        except Exception as exc:_feedback(exc)
    if changed:_refresh()

def retry(context):
    if not _pending:raise RuntimeError('No recoverable pending intent is available.')
    for root,intent in tuple(_pending.items()):
        client=_client(context);entry=_entries.get(root) or _entry(root,intent['before'])
        if intent['kind']=='capture':after=client.call('review-checkpoint',dict(companion_root=root))
        else:
            status=client.call('mutation-status',dict(companion_root=root,mutation_id=intent['request_id']))
            if status['state']=='applied':after=status['receipt']['after']
            elif intent['kind']=='rename':after=client.call('review-checkpoint',dict(companion_root=root)) # Reopen Rename afresh; no blind replay.
            else:
                intent={**intent,'request_id':uuid.uuid4().hex};_mark(intent);after=_send(intent,client)['after']
        current=client.call('review-checkpoint',dict(companion_root=root))
        if current['revision']!=after['revision']:raise RuntimeError('Project changed outside pending operation; reconciliation needs review.')
        desired=intent.get('desired_snapshot')
        if desired is not None and desired!=after['snapshot_id']:
            payload=dict(companion_root=root,project_id=after['project_id'],expected_revision=after['revision'],snapshot_id=desired)
            followup=_intent(root,after,'restore','review-restore',payload);_mark(followup)
            after=_send(followup,client)['after']
        _adopt(entry,after);_unmark(root)
    _refresh()

@bpy.app.handlers.persistent
def clear(_unused=None):
    global _session,_error
    _entries.clear();_pending.clear();_session=uuid.uuid4().hex;_error=''

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
                _validate(intent)
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
        for intent in candidates:_validate(intent);_pending[intent['root']]=intent
        if _pending:_feedback('Saved project has an unresolved operation. Retry explicitly.')
    except Exception as exc:_feedback(exc)

@bpy.app.handlers.persistent
def save_pre(_unused=None,*_args):
    # Native Save cannot be cancelled; unresolved intent must be IN the .blend.
    for intent in tuple(_pending.values()):_mark(intent)

class BLENDCAP_OT_vta_reconcile(bpy.types.Operator):
    bl_idname='blendcap.vta_reconcile';bl_label='Retry Reconciliation';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        try:retry(context);return {'FINISHED'}
        except Exception as exc:_feedback(exc);self.report({'ERROR'},str(exc));return {'CANCELLED'}

def draw_pending(layout):
    if _pending or _error:
        box=layout.box();box.label(text='VTA reconciliation pending',icon='ERROR')
        box.label(text='Domain edits paused; saved files retain this state.');box.operator('blendcap.vta_reconcile')

def register():
    bpy.utils.register_class(BLENDCAP_OT_vta_reconcile);load()
    for handlers,callback in ((bpy.app.handlers.undo_post,restore),(bpy.app.handlers.redo_post,restore),
            (bpy.app.handlers.load_pre,clear),(bpy.app.handlers.load_post,load),(bpy.app.handlers.save_pre,save_pre)):
        if callback not in handlers:handlers.append(callback)

def unregister():
    for handlers,callback in ((bpy.app.handlers.undo_post,restore),(bpy.app.handlers.redo_post,restore),
            (bpy.app.handlers.load_pre,clear),(bpy.app.handlers.load_post,load),(bpy.app.handlers.save_pre,save_pre)):
        if callback in handlers:handlers.remove(callback)
    bpy.utils.unregister_class(BLENDCAP_OT_vta_reconcile);clear()
