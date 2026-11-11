"""One-shot .blend-open and explicit Scene-load reconciliation.

No Scene is recreated or reassigned. Canonical Character deletion goes through
the private engine's existing guarded mutation, never a public project writer.
"""
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import bpy

from ..blendcap_vta_review import companion_root_for_blend
from ..blendcap_vta_scene_reconcile import PROJECT_FIELDS,plan


def _membership(scene, project_id):
    raw=scene.get('vta_scene_characters')
    if raw is None:return None,(),None
    value=json.loads(raw)
    if value.get('version')!=1 or not isinstance(value.get('history',{}),dict):
        raise ValueError('Unsupported Scene Character membership; cleanup was skipped.')
    history=[];ids=set()
    for key,rows in value.get('history',{}).items():
        scope=json.loads(key)
        if not isinstance(scope,list) or len(scope)!=2 or not isinstance(rows,list) or any(not isinstance(x,str) for x in rows):
            raise ValueError('Invalid Scene membership history; cleanup was skipped.')
        if scope[0]==project_id:history.append(key);ids.update(rows)
    if value.get('project_id')==project_id:
        rows=value.get('ids')
        if not isinstance(rows,list) or any(not isinstance(x,str) for x in rows):
            raise ValueError('Invalid Scene membership; cleanup was skipped.')
        ids.update(rows)
    return ids,tuple(history),value


def _native_ids(project_id):
    ids=set()
    for collection in (bpy.data.objects,bpy.data.armatures,bpy.data.actions):
        for block in collection:
            identity=block.get('vta_character_id')
            if identity and block.get('vta_project_id') in (None,project_id):ids.add(identity)
    return ids


def _clear_missing_remembered():
    from .vta_scene_session import LAST
    surviving={scene.as_pointer() for scene in bpy.data.scenes}
    for workspace in bpy.data.workspaces:
        try:
            target=getattr(workspace,LAST,None)
            if target is not None and target.as_pointer() not in surviving:setattr(workspace,LAST,None)
        except ReferenceError:
            setattr(workspace,LAST,None)


def reconcile(context, root=None):
    """Run once for one validated companion; ambiguity preserves authority."""
    from . import vta_review_ui as ui, vta_character_bindings, vta_review_undo
    _clear_missing_remembered()
    if vta_review_undo.blocked() or ui._analysis_locked():return None
    root=Path(root or companion_root_for_blend(bpy.data.filepath)).resolve()
    path=root/'project_state.json'
    if not path.is_file():return None
    client=ui._engine_client(context)
    checkpoint=client.call('project-state',dict(companion_root=str(root)))
    if path.stat().st_size>64*1024*1024:raise ValueError('MoCap project is too large for reconciliation.')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=checkpoint['revision']['sha256']:
        raise ValueError('MoCap project changed during reconciliation; no cleanup was applied.')
    project=json.loads(raw)
    if project.get('project_id')!=checkpoint['project_id'] or not isinstance(project.get('characters'),list):
        raise ValueError('MoCap project identity changed; no cleanup was applied.')
    pid=project['project_id'];scenes=[];owners=[]
    for scene in bpy.data.scenes:
        membership,history,value=_membership(scene,pid)
        row=dict(project_id=scene.get('vta_project_id'),source_id=scene.get('vta_source_id'),
            owner_id=scene.get('vta_scene_owner_id'),membership=membership,
            history_projects=(pid,) if history else ())
        scenes.append(row);owners.append((scene,value,history))
    receipts=vta_character_bindings.read(root,with_ids=True)
    result=plan(project,scenes,receipts,_native_ids(pid))
    live=[entry for entry in owners if entry[0].get('vta_project_id')==pid or entry[2]]
    # Unknown future schema or missing membership can never justify deletion.
    if set(project)!=PROJECT_FIELDS:
        print('[MoCap] Unknown project fields; Character cleanup deferred.',flush=True)
    for identity in result.orphans:
        payload=dict(companion_root=str(root),project_id=pid,expected_revision=checkpoint['revision'],
            action='delete-character',character_id=identity,source_id=None,frame=None,
            keyframe_ids=[],confirmed=True,session_id=uuid4().hex)
        receipt=client.call('review-management',payload)
        checkpoint=receipt['after']
    # Stale host receipts are historical adoption intent, not Character authority.
    # Remove only after successful canonical mutations; a failed unlink is harmless.
    folder=vta_character_bindings.path(root)
    for request_id in result.stale_receipts:
        candidate=folder/(request_id+'.json')
        if candidate.is_file() and not candidate.is_symlink():candidate.unlink()
    valid={row['character_id'] for row in project['characters']}-set(result.orphans)
    for (index,unknown) in result.stale_memberships:
        scene,value,history=live[index]
        if value is None:continue
        if value.get('project_id')==pid:value['ids']=[cid for cid in value['ids'] if cid in valid]
        for key in history:value['history'][key]=[cid for cid in value['history'][key] if cid in valid]
        scene['vta_scene_characters']=json.dumps(value,sort_keys=True)
    if result.orphans or result.stale_receipts or result.stale_memberships:
        ui._display.clear();ui._observer_tokens.clear()
    uncertain=[cid for cid,reason in result.preserved if reason.startswith('uncertain')]
    if uncertain:print('[MoCap] Preserved uncertain Character identities:',len(uncertain),flush=True)
    return result


def _run_after_open():
    try:reconcile(bpy.context)
    except Exception as exc:print('[MoCap] File-open reconciliation deferred: '+str(exc),flush=True)
    return None


@bpy.app.handlers.persistent
def file_opened(_unused=None):
    try:filename=bpy.data.filepath
    except (AttributeError,RuntimeError):return  # RestrictBlend during registration.
    if filename and not bpy.app.timers.is_registered(_run_after_open):
        bpy.app.timers.register(_run_after_open,first_interval=0.0)


def register():
    if file_opened not in bpy.app.handlers.load_post:bpy.app.handlers.load_post.append(file_opened)
    file_opened()  # Add-on may register after Blender already opened the file.


def unregister():
    if file_opened in bpy.app.handlers.load_post:bpy.app.handlers.load_post.remove(file_opened)
    if bpy.app.timers.is_registered(_run_after_open):bpy.app.timers.unregister(_run_after_open)
