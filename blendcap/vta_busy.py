"""Give explicit foreground edits a redraw and busy cursor before control I/O."""
import bpy
_active=None

def busy():return _active is not None

def install(cls):
    if getattr(cls.execute,'_vta_deferred',False):return
    original=cls.execute
    def execute(self,context):
        global _active
        if bpy.app.background:return original(self,context)
        if _active is not None:return {'CANCELLED'}
        _active=self;self._vta_epoch=(bpy.data.filepath,context.scene.as_pointer(),context.scene.frame_current)
        self._vta_window=context.window
        self._vta_timer=context.window_manager.event_timer_add(.06,window=context.window)
        context.window.cursor_modal_set('WAIT');context.window_manager.modal_handler_add(self)
        from . import vta_review_ui as ui
        ui.vta_invalidation.request_redraw()
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
            _active=None;context.window_manager.event_timer_remove(self._vta_timer)
            context.window.cursor_modal_restore()
            from . import vta_review_ui as ui
            ui.vta_invalidation.request_redraw()
    execute._vta_deferred=True;cls.execute=execute;cls.modal=modal

def draw(layout):
    if _active is not None:layout.label(text='Working… '+_active.bl_label,icon='TIME')

@bpy.app.handlers.persistent
def clear(_unused=None):
    global _active
    active=_active;_active=None
    if active is not None:
        try:bpy.context.window_manager.event_timer_remove(active._vta_timer)
        except (RuntimeError,ReferenceError,ValueError):pass
        try:active._vta_window.cursor_modal_restore()
        except (RuntimeError,ReferenceError):pass

def register():
    if clear not in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.append(clear)
def unregister():
    clear()
    if clear in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.remove(clear)
