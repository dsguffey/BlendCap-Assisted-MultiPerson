"""Temporary marks and explicit batch submission; no identity inference."""
import time
import bpy
from ..blendcap_vta_marks import CandidateMarks, ASSIGN_ICON, SUPPRESS_ICON

marks=CandidateMarks()
_busy=False
_epoch=0
measurements=[]

def scope(context):
    from . import vta_review_ui as ui
    if ui._location is None or ui._view is None:return None
    return (context.scene.as_pointer(),str(ui._location.companion_root),ui._view.source_id)

def busy():return _busy

def apply_marked(context,key,kind,items,character,confirmed=False,expected_checkpoint=None):
    """Shared main-thread submission, also exercised by background Blender."""
    from . import vta_review_ui as ui
    if scope(context)!=key:raise ValueError('Source Scene changed; marks retained.')
    started=time.perf_counter();backend=ui._get_backend(context)
    observed,payload=backend._cache_payload(ui._location)
    if expected_checkpoint is not None and observed.value['checkpoint']!=expected_checkpoint:
        raise ValueError('Review changed while the action was pending; marks retained. Review and retry.')
    if any(r['analysis_revision_id']!=payload['analysis_revision_id'] or r['cache_sha256']!=payload['cache_sha256'] for r in items):
        raise ValueError('Analysis changed; clear stale marks and mark current candidates.')
    from .vta_camera_cleanup import require_source_aligned
    targets={(row['frame'],row['observation_id']) for row in items}
    affected={key.character_id for key in observed.timeline.keys
        if (key.source_frame,key.observation_id) in targets}
    if character:affected.add(character)
    if affected:require_source_aligned(context.scene,tuple(sorted(affected)))
    payload.pop('source_frame');payload.update(action=kind,character_id=character,
        items=[{k:r[k] for k in ('frame','observation_id')} for r in items],confirmed=confirmed,session_id=backend.session_id)
    result=backend._send(ui._location,'review-batch',payload,observed)
    action_end=time.perf_counter();ui._accept_mutated_review(context,backend.query(ui._location))
    marks.handled(key,kind,items)
    measurements.append(dict(action=kind,count=len(items),action_s=action_end-started,refresh_s=time.perf_counter()-action_end))
    ui._status=f'Applied {len(items)} marked candidates.';ui._status_is_error=False
    return result

def assignment_label(context):
    from . import vta_review_ui as ui
    character = ui.current_character_id(context)
    row = next((c for c in ui._view.characters if c.character_id == character), None) if ui._view else None
    return '(' + row.display_name + ')' if row else '(Choose Character)'


def overlay_labels(candidates,context):
    from dataclasses import replace
    from . import vta_review_ui as ui
    key=scope(context);symbols={}
    for kind,symbol in [('assign',assignment_label(context)),('suppress','[Suppress]')]:
        for row in marks.items(key,kind):
            if ui._view is not None and row['frame']==ui._view.source_frame:
                symbols[row['observation_id']]=(symbol,kind)
    return tuple(replace(item,label=symbols[item.observation_id][0]+' '+item.label,
                         mark_state=symbols[item.observation_id][1])
        if item.observation_id in symbols else item for item in candidates)


def draw(layout,context):
    from . import vta_review_ui as ui
    from .vta_review_undo import blocked
    key=scope(context)
    ready=not blocked()
    for kind,label,icon in [('assign','Mark for Assignment',ASSIGN_ICON),('suppress','Mark for Suppression',SUPPRESS_ICON)]:
        row=layout.row();row.enabled=bool(key and ui._selection.observation_id and not ui._analysis_locked())
        row.operator('blendcap.vta_mark_candidate',text=label,icon=icon).kind=kind
    for kind,label,icon in [('assign','Assign Marked',ASSIGN_ICON),('suppress','Suppress Marked',SUPPRESS_ICON)]:
        count=len(marks.items(key,kind));row=layout.row()
        row.enabled=bool(ready and count and not ui._analysis_locked() and (kind!='assign' or ui.current_character_id(context)))
        row.operator('blendcap.vta_apply_marks',text=f'{label} ({count})',icon=icon).kind=kind
    row=layout.row();row.enabled=bool(marks.items(key,'assign') or marks.items(key,'suppress')) and not _busy
    row.operator('blendcap.vta_clear_marks',text='Clear Marks',icon='X')
    layout.label(text='Assignment: one candidate per frame.')
    layout.label(text='Suppression: multiple on each frame.')
    layout.label(text='Marks are temporary until applied.')
    if _busy:layout.label(text='Working… applying marked candidates',icon='TIME')
    for kind,symbol in [('assign',assignment_label(context)),('suppress','[Suppress]')]:
        for item in marks.items(key,kind):
            if item['frame']==ui._view.source_frame:
                candidate=next((c for c in ui._view.candidates if c.observation_id==item['observation_id']),None)
                if candidate:layout.label(text=f'{symbol} Candidate {candidate.position}')

class BLENDCAP_OT_vta_mark_candidate(bpy.types.Operator):
    bl_idname='blendcap.vta_mark_candidate';bl_label='Mark Candidate'
    bl_description='Toggle a temporary mark; assignment replaces the previous mark on this frame, suppression allows several'
    kind:bpy.props.EnumProperty(items=[('assign','Assignment',''),('suppress','Suppression','')])
    @classmethod
    def poll(cls,context):
        from . import vta_review_ui as ui
        return ui._presentation_current(context) and bool(ui._selection.observation_id) and not ui._analysis_locked()
    def execute(self,context):
        from . import vta_review_ui as ui
        try:
            if not ui._presentation_current(context) or ui._analysis_locked():raise ValueError('Refresh the current source frame before marking.')
            backend=ui._get_backend(context);observed=backend.observation
            row=next((c for c in ui._view.candidates if c.observation_id==ui._selection.observation_id),None)
            if row is None or row.suppressed:raise ValueError('Choose an unsuppressed candidate.')
            marks.toggle(scope(context),self.kind,ui._view.source_frame,row.observation_id,
                ui._view.analysis_revision_id,observed.value['cache_sha256'])
            ui.vta_invalidation.request_redraw();return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}

class BLENDCAP_OT_vta_clear_marks(bpy.types.Operator):
    bl_idname='blendcap.vta_clear_marks';bl_label='Clear Marks'
    bl_description='Clear temporary assignment and suppression marks without changing review decisions'
    @classmethod
    def poll(cls,context):
        key=scope(context)
        return not _busy and bool(marks.items(key,'assign') or marks.items(key,'suppress'))
    def execute(self,context):
        if _busy:return {'CANCELLED'}
        marks.clear(scope(context))
        if context.area is not None:context.area.tag_redraw()
        return {'FINISHED'}

class BLENDCAP_OT_vta_apply_marks(bpy.types.Operator):
    bl_idname='blendcap.vta_apply_marks';bl_label='Apply Marked Candidates'
    bl_description='Apply all marks in one atomic edit; failure preserves all marks'
    kind:bpy.props.EnumProperty(items=[('assign','Assignment',''),('suppress','Suppression','')])
    confirmed:bpy.props.BoolProperty(default=False,options={'HIDDEN','SKIP_SAVE'})
    @classmethod
    def poll(cls,context):
        from . import vta_review_ui as ui
        key=scope(context)
        return not ui._analysis_locked() and bool(marks.items(key,'assign') or marks.items(key,'suppress'))
    def invoke(self,context,event):
        from . import vta_review_ui as ui
        if _busy or ui._analysis_locked():return {'CANCELLED'}
        self._scope=scope(context);self._items=marks.items(self._scope,self.kind);self._character=ui.current_character_id(context) if self.kind=='assign' else None
        self._checkpoint=ui._get_backend(context).observation.value['checkpoint']
        if not self._items or self.kind=='assign' and not self._character:return {'CANCELLED'}
        # Existing authoritative decisions are the only reason for confirmation.
        targets={(r['frame'],r['observation_id']) for r in self._items}
        conflicting=any((k.source_frame,k.observation_id) in targets or
            self.kind=='assign' and k.character_id==self._character and any(r['frame']==k.source_frame for r in self._items)
            for k in ui._timeline.keys)
        if conflicting:
            self.confirmed=True
            return context.window_manager.invoke_props_dialog(self,width=460)
        return self.execute(context)
    def draw(self,context):
        self.layout.label(text='Replace existing assignments affected by these marks?')
        self.layout.label(text='All changes apply together; other frames are preserved.')
    def execute(self,context):
        global _busy
        from . import vta_review_ui as ui
        if _busy or ui._analysis_locked():return {'CANCELLED'}
        self._scope=getattr(self,'_scope',scope(context));self._items=getattr(self,'_items',marks.items(self._scope,self.kind))
        self._character=getattr(self,'_character',ui.current_character_id(context) if self.kind=='assign' else None)
        if not self._items:return {'CANCELLED'}
        _busy=True;self._epoch=_epoch;self._timer=None
        if self.confirmed:
            # Complete the dialog's callback directly; don't start another
            # modal lifecycle on its operator. Validation still occurs in apply.
            context.window.cursor_modal_set('WAIT')
            try:return self._apply(context)
            finally:context.window.cursor_modal_restore()
        try:
            self._timer=context.window_manager.event_timer_add(.06,window=context.window)
            context.window_manager.modal_handler_add(self);ui.vta_invalidation.request_redraw()
        except Exception as exc:
            self._finish(context);self.report({'ERROR'},str(exc));return {'CANCELLED'}
        return {'RUNNING_MODAL'}
    def modal(self,context,event):
        if event.type=='ESC':self._finish(context);return {'CANCELLED'}
        if event.type!='TIMER':return {'PASS_THROUGH'}
        return self._apply(context)
    def _apply(self,context):
        from . import vta_review_ui as ui
        try:
            if self._epoch!=_epoch:raise ValueError('Mark session changed; no candidates applied.')
            apply_marked(context,self._scope,self.kind,self._items,self._character,self.confirmed,getattr(self,'_checkpoint',None))
            return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}
        finally:self._finish(context)
    def _finish(self,context):
        global _busy
        from . import vta_review_ui as ui
        _busy=False
        timer=getattr(self,'_timer',None);self._timer=None
        if timer is not None:
            try:context.window_manager.event_timer_remove(timer)
            except (RuntimeError,ReferenceError,ValueError):pass
        ui.vta_invalidation.request_redraw()
    def cancel(self,context):
        if hasattr(self,'_epoch'):self._finish(context)

@bpy.app.handlers.persistent
def clear(_unused=None):
    global _epoch,_busy
    _epoch+=1;_busy=False;marks.clear()

CLASSES=(BLENDCAP_OT_vta_mark_candidate,BLENDCAP_OT_vta_clear_marks,BLENDCAP_OT_vta_apply_marks)
def register():
    from . import vta_busy
    vta_busy.register()
    from . import vta_export
    vta_export.register()
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.app.handlers.load_pre.append(clear)
def unregister():
    from . import vta_busy
    vta_busy.unregister()
    from . import vta_export
    vta_export.unregister()
    clear()
    if clear in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.remove(clear)
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
