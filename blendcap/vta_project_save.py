"""Native Save As companion-copy transaction. No automatic Scene selection."""
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import bpy

from ..blendcap_vta_review import companion_root_for_blend
from ..blendcap_vta_project_copy import (CopyPlan, file_hash, filesystem_path, exists,
    replacement_receipt, seal_receipt)

ERROR='vta_save_as_recovery_error'
_transaction=None


def error(scene):
    raw=getattr(scene,'get',lambda _key:None)(ERROR) if scene is not None else None
    if not raw:return None
    try:return json.loads(raw)
    except (ValueError,TypeError):return dict(details='Invalid VTA Save As recovery metadata. Reopen the original project.')


def require_usable(scene):
    record=error(scene)
    if record:
        raise ValueError('VTA Save As copy is incomplete; VTA edits are disabled. '+record['details'])


def draw_error(layout,context):
    from . import vta_review_ui as ui
    from .vta_scene_session import main_scene
    record=error(main_scene(context))
    if record:
        key=ui._feedback_key('save_as',context)
        ui._operation_feedback[key]=dict(primary='VTA Save As incomplete; edits disabled.',
            details=record['details'],error=True,disposition='Reopen the original or retry Save As to a new name.',
            log=Path(record['log']) if record.get('log') else None)
        ui.draw_operation_feedback(layout,context,'save_as')


def change(changes,owner,key,value,*,rna=False):
    previous=getattr(owner,key) if rna else owner.get(key)
    changes.append((owner,key,previous,rna))
    if rna:setattr(owner,key,value)
    elif value is None:owner.pop(key,None)
    else:owner[key]=value


def restore(changes):
    for owner,key,value,rna in reversed(changes):
        if rna:setattr(owner,key,value)
        elif value is None:owner.pop(key,None)
        else:owner[key]=value


def prepare(source,target,scenes):
    """Validate closure and stage host rebindings, without writing authority."""
    plan=CopyPlan(source,companion_root_for_blend(target))
    raw=plan.project()
    from . import vta_review_ui as ui
    # Core validates canonical schema; the transport does not reinterpret it.
    ui._get_backend(bpy.context)._core.load_project_state(source)
    sources={row['source_id']:row for row in raw['sources']}
    updates=[]
    for scene in scenes:
        if scene.get('vta_project_id')!=raw['project_id']:
            raise ValueError('Scene and canonical VTA project identity differ.')
        source_record=sources.get(scene.get('vta_source_id'))
        if not source_record or source_record['video_sha256']!=scene.get('vta_source_video_sha256'):
            raise ValueError('Scene and canonical VTA source identity differ.')
        found=False
        for strip in (tuple(scene.sequence_editor.strips_all) if scene.sequence_editor else ()):
            if strip.type!='MOVIE':continue
            path=Path(bpy.path.abspath(strip.filepath)).resolve()
            if strip.get('vta_source_strip_version') or file_hash(path)==source_record['video_sha256']:
                new=plan.video(path,source_record['video_sha256'])
                updates.append((strip,'filepath',str(new),True));found=True
        if not found:raise ValueError('VTA source movie is missing; no independent copy can be made.')
    # Generated objects/recovery Actions and Scene solves are serialized by Blender.
    # Only their live external output/reference paths need changing here.
    for obj in bpy.data.objects:
        if obj.get('vta_project_id')!=raw['project_id']:continue
        if obj.get('vta_output_file'):
            updates.append((obj,'vta_output_file',str(plan.output(obj['vta_output_file'])),False))
        data=getattr(obj,'data',None)
        if data is not None and data.get('vta_scene_reference'):
            reference=json.loads(data['vta_scene_reference'])
            reference['output_file']=str(plan.output(reference['output_file'],reference['output_sha256']))
            updates.append((data,'vta_scene_reference',json.dumps(reference,sort_keys=True),False))
    for block in (*bpy.data.images,*bpy.data.movieclips):
        if not block.filepath:continue
        path=Path(bpy.path.abspath(block.filepath)).resolve()
        if path in plan.media:updates.append((block,'filepath',str(plan.media[path]),True))
    return plan,updates


def _report(record):
    from . import vta_review_ui as ui
    ui._validated_sources.clear();ui._observer_tokens.clear();ui._display.clear()
    ui._activate_review_scene(None)
    ui._status='VTA Save As incomplete; reopen the original or retry Save As to a new name.'
    ui._status_is_error=True
    print('[VTA Save As] '+record['details'],flush=True)


@bpy.app.handlers.persistent
def save_pre(target,*_args):
    global _transaction
    if _transaction is not None:
        # Do not allow another writer to replace an in-flight transaction.
        return
    from .vta_scene_session import source_bound,owned
    scenes=[s for s in bpy.data.scenes if source_bound(s) or owned(s)]
    if not scenes:return
    target=Path(target).resolve()
    old=Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
    prior=next((error(s) for s in scenes if error(s)),None)
    if old==target and prior is None:
        # Ordinary Save may follow legitimate edits. Renew only a receipt whose
        # exact previous .blend was verified before Blender overwrites it.
        try:receipt=replacement_receipt(target)
        except (OSError,ValueError,KeyError):return
        _transaction=dict(receipt_only=True,target=target,receipt=receipt)
        return
    changes=[]
    job=dict(old=old,target=target,changes=changes,plan=None,published=False,backup=None,error=None)
    _transaction=job
    source=Path(prior['source_root']) if prior and prior.get('source_root') else (companion_root_for_blend(old) if old else target.with_name(target.stem+'_vta'))
    job['source']=source
    bound=[s for s in scenes if source_bound(s)]
    if not bound and not prior and not exists(source/'project_state.json'):
        _transaction=None
        return # Truly blank owner, no external history to preserve.
    try:
        # Handlers cannot cancel native Save. Keep an existing destination .blend
        # recoverable if our preflight fails and Blender nevertheless writes it.
        if exists(target):
            backup=target.parent/('.vta-save-'+uuid4().hex[:16]+'.bak')
            try:
                with open(filesystem_path(backup),'xb') as stream:
                    with open(filesystem_path(target),'rb') as original:shutil.copyfileobj(original,stream)
                    stream.flush();os.fsync(stream.fileno())
            except Exception:
                if exists(backup):os.unlink(filesystem_path(backup))
                raise
            job['backup']=backup
        from . import vta_review_ui as ui
        if ui._analysis_locked():raise ValueError('Wait for VTA analysis/generation/cleanup to finish before Save As.')
        plan,updates=prepare(source,target,bound)
        job['plan']=plan
        plan.authorize_replacement(target)
        plan.publish();job['published']=True
        for owner,key,value,rna in updates:change(changes,owner,key,value,rna=rna)
        for scene in scenes:change(changes,scene,ERROR,None)
    except Exception as exc:
        restore(changes);changes.clear()
        if job['published']:
            if not job['plan'].rollback():
                print('[VTA Save As] Staged companion changed; retained for recovery.',flush=True)
            job['published']=False
        record=dict(version=1,source_root=str(source),source_blend=str(old or ''),target_blend=str(target),
            details=str(exc)+' The original companion is unchanged. This new .blend is visual-only until recovery.',
            destination_backup=str(job['backup']) if job['backup'] else None,log=None)
        try:
            log=target.parent/('.vta-save-error-'+uuid4().hex[:16]+'.log')
            with open(filesystem_path(log),'x',encoding='utf-8') as stream:stream.write(record['details'])
            record['log']=str(log)
        except OSError:pass
        job['error']=record
        for scene in scenes:change(changes,scene,ERROR,json.dumps(record))
        _report(record)


def _refresh():
    from . import vta_review_ui as ui
    ui._validated_sources.clear();ui._observer_tokens.clear();ui._display.clear()
    ui._activate_review_scene(None)
    # Passive source read refresh only: no Scene/editor binding changes.
    if getattr(bpy.context,'window',None):ui.refresh(bpy.context,check_disk=True)


@bpy.app.handlers.persistent
def save_post(target,*_args):
    global _transaction
    job,_transaction=_transaction,None
    if job is None:return
    if Path(target).resolve()!=job['target']:
        _transaction=job;return
    if job.get('receipt_only'):
        try:seal_receipt(job['target'],job['receipt'])
        except (OSError,ValueError) as exc:print('[VTA Save As] Ownership receipt not renewed: '+str(exc),flush=True)
        return
    if job['error'] and job['backup'] is not None:
        # Preserve a pre-existing destination project rather than replace its
        # .blend with an intentionally blocked/failed copy of another project.
        try:
            os.replace(filesystem_path(job['backup']),filesystem_path(job['target']))
            job['error']['details']+=' Existing destination .blend was restored; the live unsaved copy is not bound to it.'
        except OSError as exc:
            job['error']['details']+=f" Destination restore failed: {exc}. Backup retained at {job['backup']}."
        job['backup']=None
        for scene in bpy.data.scenes:
            if scene.get(ERROR):scene[ERROR]=json.dumps(job['error'])
    copied=bpy.data.filepath and Path(bpy.data.filepath).resolve()!=job['target']
    retain_backup=False
    if not job['error']:
        try:job['plan'].finish()
        except (OSError,ValueError) as exc:
            # The new pair is already published. Never discard a recoverable
            # previous pair when receipt sealing/backup cleanup fails.
            retain_backup=True
            print('[VTA Save As] Save succeeded; recovery material retained: '+str(exc)
                +f"; previous .blend backup: {job['backup']}; companion backup: {job['plan'].replaced}",flush=True)
    if copied:restore(job['changes']) # Native Save Copy keeps the original live bindings.
    if job['backup'] is not None and not retain_backup:os.unlink(filesystem_path(job['backup']))
    if job['error'] and not copied:_report(job['error'])
    elif not copied:_refresh()


@bpy.app.handlers.persistent
def save_fail(*_args):
    global _transaction
    job,_transaction=_transaction,None
    if job is None:return
    if job.get('receipt_only'):return
    restore(job['changes'])
    if job['published'] and not job['plan'].rollback():
        print('[VTA Save As] Native save failed; changed companion retained for recovery.',flush=True)
    if job['backup'] is not None:
        # A failed native write may have touched its target before reporting
        # failure. Restore our exact pre-save destination snapshot as well.
        os.replace(filesystem_path(job['backup']),filesystem_path(job['target']))
    _refresh()


@bpy.app.handlers.persistent
def reset(*_args):
    global _transaction
    _transaction=None


def register():
    for handlers,callback in ((bpy.app.handlers.save_pre,save_pre),(bpy.app.handlers.save_post,save_post),
            (bpy.app.handlers.save_post_fail,save_fail),(bpy.app.handlers.load_post,reset)):
        if callback not in handlers:handlers.append(callback)


def unregister():
    for handlers,callback in ((bpy.app.handlers.save_pre,save_pre),(bpy.app.handlers.save_post,save_post),
            (bpy.app.handlers.save_post_fail,save_fail),(bpy.app.handlers.load_post,reset)):
        if callback in handlers:handlers.remove(callback)
    reset()
