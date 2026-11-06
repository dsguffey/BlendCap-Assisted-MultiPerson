"""Give explicit foreground edits a redraw and busy cursor before control I/O."""
import bpy
_active=None

def busy():return _active is not None

def _release(active):
    global _active
    if _active is active:_active=None
    timer=getattr(active,'_vta_timer',None)
    active._vta_timer=None
    if timer is not None:
        try:active._vta_wm.event_timer_remove(timer)
        except (RuntimeError,ReferenceError,ValueError):pass
    try:active._vta_window.cursor_modal_restore()
    except (RuntimeError,ReferenceError):pass

def install(cls, *, defer=True):
    if getattr(cls.execute,'_vta_deferred',False):return
    original=cls.execute
    if not defer:
        def confirmed_execute(self,context):
            # A props dialog owns the operator's modal lifecycle. Its execute
            # callback must finish the confirmed edit, not start a second modal
            # handler on the same operator and wait for a timer it may never see.
            if bpy.app.background:return original(self,context)
            if _active is not None:return {'CANCELLED'}
            context.window.cursor_modal_set('WAIT')
            try:return original(self,context)
            finally:context.window.cursor_modal_restore()
        confirmed_execute._vta_deferred=True
        cls.execute=confirmed_execute
        return
    def execute(self,context):
        global _active
        if bpy.app.background:return original(self,context)
        if _active is not None:return {'CANCELLED'}
        _active=self;self._vta_epoch=(bpy.data.filepath,context.scene.as_pointer(),context.scene.frame_current)
        self._vta_window=context.window;self._vta_wm=context.window_manager;self._vta_timer=None
        try:
            self._vta_timer=context.window_manager.event_timer_add(.06,window=context.window)
            context.window.cursor_modal_set('WAIT');context.window_manager.modal_handler_add(self)
            from . import vta_review_ui as ui
            ui.vta_invalidation.request_redraw()
        except Exception:
            _release(self);raise
        return {'RUNNING_MODAL'}
    def modal(self,context,event):
        global _active
        if _active is not self:return {'CANCELLED'}
        if event.type not in ('TIMER','ESC'):return {'RUNNING_MODAL'}
        try:
            if event.type=='ESC' or self._vta_epoch!=(bpy.data.filepath,context.scene.as_pointer(),context.scene.frame_current):return {'CANCELLED'}
            # Main-thread RNA/presentation stays on the main thread. The first
            # redraw and wait cursor have already happened before this I/O.
            _active=None
            return original(self,context)
        finally:
            _release(self)
            from . import vta_review_ui as ui
            ui.vta_invalidation.request_redraw()
    def cancel(self,context):_release(self)
    execute._vta_deferred=True;cls.execute=execute;cls.modal=modal;cls.cancel=cancel

def draw(layout):
    if _active is not None:layout.label(text='Working… '+_active.bl_label,icon='TIME')

@bpy.app.handlers.persistent
def clear(_unused=None):
    global _active
    active=_active
    if active is not None:_release(active)

def register():
    if clear not in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.append(clear)
def unregister():
    clear()
    if clear in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.remove(clear)
