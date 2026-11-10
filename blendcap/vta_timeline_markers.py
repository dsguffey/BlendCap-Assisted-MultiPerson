"""Thin presentation/gesture bridge to Scene.timeline_markers; no marker store."""
import bpy
from ..blendcap_vta_workspace import SCROLLBAR_HEIGHT, MARKER_HEIGHT
from . import vta_invalidation

_focus=set()
_drag=None


def scene(context):
    return getattr(getattr(context,'window',None),'scene',None) or getattr(context,'scene',None)


def scope(context):
    pointer=lambda value:value.as_pointer() if hasattr(value,'as_pointer') else id(value)
    return tuple(pointer(value) for value in (scene(context),getattr(context,'workspace',None),getattr(context,'area',None)))


def focused(context):return scope(context) in _focus


def focus(context,active=True):
    if active:_focus.add(scope(context))
    else:_focus.discard(scope(context))


def clear():
    global _drag
    _focus.clear();_drag=None


def contains(context,event,scale):
    return (context.region.type=='WINDOW' and
            SCROLLBAR_HEIGHT*scale <= event.mouse_region_y < (SCROLLBAR_HEIGHT+MARKER_HEIGHT)*scale)


def positions(context):
    # Read live native data every time, including edits made in another editor.
    drag=_drag if _drag and _drag['scope']==scope(context) else None
    return tuple((m,context.region.view2d.view_to_region(
        m.frame+(drag['delta'] if drag and m.as_pointer() in drag['ids'] else 0),0,clip=False)[0])
        for m in scene(context).timeline_markers)


def hit(context,x,scale):
    import blf
    blf.size(0,11*scale)
    hits=[(abs(px-x),m) for m,px in positions(context)
          if px-9*scale <= x <= px+max(9*scale,8*scale+blf.dimensions(0,m.name)[0])]
    return min(hits,key=lambda row:row[0])[1] if hits else None


def selected(context):
    return tuple(m for m in scene(context).timeline_markers if m.select)


def signature(context):
    return tuple((m.as_pointer(),m.name,m.frame) for m in selected(context))


def draw(context,scale,shader,batch,fill,line):
    import blf
    low=SCROLLBAR_HEIGHT*scale;high=(SCROLLBAR_HEIGHT+MARKER_HEIGHT)*scale
    fill(shader,batch,0,low,context.region.width,high,(.14,.14,.14,1))
    line(shader,batch,((0,high),(context.region.width,high)),(.3,.3,.3,1))
    for marker,x in positions(context):
        if not -9*scale <= x <= context.region.width:continue
        color=(1,.8,.35,1) if marker.select else (.7,.7,.7,1)
        line(shader,batch,((x-5*scale,low+6*scale),(x,low+13*scale),
                          (x+5*scale,low+6*scale),(x-5*scale,low+6*scale)),color)
        blf.position(0,x+8*scale,low+8*scale,0);blf.size(0,11*scale)
        blf.color(0,*color);blf.draw(0,marker.name)


def command(context,action):
    focus(context)
    if action=='MOVE':
        return bpy.ops.blendcap.vta_marker_drag('INVOKE_DEFAULT',grab=True)
    return bpy.ops.blendcap.vta_marker_edit(
        'INVOKE_DEFAULT' if action=='RENAME' else 'EXEC_DEFAULT',True,action=action)


class BLENDCAP_OT_vta_marker_edit(bpy.types.Operator):
    bl_idname='blendcap.vta_marker_edit'
    bl_label='Edit Timeline Marker'
    bl_options={'REGISTER','UNDO'}
    __annotations__={
        'action':bpy.props.EnumProperty(items=[(v,v.title(),'') for v in ('ADD','RENAME','DELETE','MOVE')]),
        'marker_name':bpy.props.StringProperty(name='Name'),
        'frames':bpy.props.IntProperty(name='Move by Frames')}

    @classmethod
    def poll(cls,context):
        from .vta_workspace import _scope
        return _scope(context) and context.region.type=='WINDOW'

    def invoke(self,context,_event):
        if self.action=='RENAME':
            markers=selected(context)
            if not markers:return {'CANCELLED'}
            self.marker_name=markers[0].name
            self._target=markers[0].as_pointer();self._scope=scope(context)
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self,_context):self.layout.prop(self,'marker_name')

    def execute(self,context):
        owner=scene(context)
        if owner.tool_settings.lock_markers:return {'CANCELLED'}
        markers=selected(context)
        if self.action=='ADD':
            frame=owner.frame_current
            if any(m.frame==frame for m in owner.timeline_markers):return {'CANCELLED'}
            for m in owner.timeline_markers:m.select=False
            owner.timeline_markers.new(f'F_{frame:02}',frame=frame).select=True
        elif self.action=='RENAME':
            if getattr(self,'_scope',scope(context))!=scope(context):return {'CANCELLED'}
            target=next((m for m in markers if m.as_pointer()==getattr(self,'_target',m.as_pointer())),None)
            if target is None or not self.marker_name.strip():return {'CANCELLED'}
            target.name=self.marker_name.strip()
        elif self.action=='DELETE':
            if not markers:return {'CANCELLED'}
            for m in markers:owner.timeline_markers.remove(m)
        elif self.action=='MOVE':
            if not markers or not self.frames:return {'CANCELLED'}
            for m in markers:m.frame+=self.frames
        else:return {'CANCELLED'}
        vta_invalidation.request_redraw()
        return {'FINISHED'}


class BLENDCAP_OT_vta_marker_drag(bpy.types.Operator):
    bl_idname='blendcap.vta_marker_drag'
    bl_label='Move Timeline Marker'
    bl_options={'INTERNAL'}
    __annotations__={'grab':bpy.props.BoolProperty(default=False)}

    @classmethod
    def poll(cls,context):return BLENDCAP_OT_vta_marker_edit.poll(context)

    def invoke(self,context,event):
        global _drag
        from .vta_workspace import _metrics
        if _drag is not None:return {'CANCELLED'}
        focus(context)
        if not self.grab:
            marker=hit(context,event.mouse_region_x,_metrics(context).ui_scale)
            extend=bool(event.shift or event.ctrl)
            if not extend:
                for m in scene(context).timeline_markers:m.select=False
            if marker:marker.select=not marker.select if extend else True
            vta_invalidation.request_redraw()
            if marker is None or not marker.select:return {'FINISHED'}
        if not selected(context):return {'CANCELLED'}
        self._start=signature(context)
        self._scope=scope(context);self._region=context.region.as_pointer()
        self._x=event.mouse_region_x
        self._frame=context.region.view2d.region_to_view(self._x,0)[0]
        _drag=dict(scope=self._scope,ids={p for p,_,_ in self._start},delta=0)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self,_context):
        global _drag
        _drag=None;vta_invalidation.request_redraw()

    def modal(self,context,event):
        global _drag
        from .vta_workspace import _scope
        if (_drag is None or not _scope(context) or scope(context)!=self._scope or
                context.region.as_pointer()!=self._region or signature(context)!=self._start):
            self.cancel(context);return {'CANCELLED'}
        if event.type in {'ESC','RIGHTMOUSE','WINDOW_DEACTIVATE'}:
            self.cancel(context);return {'CANCELLED'}
        if event.type=='MOUSEMOVE' or (event.type=='LEFTMOUSE' and event.value=='RELEASE'):
            _drag['delta']=round(context.region.view2d.region_to_view(event.mouse_region_x,0)[0]-self._frame)
            vta_invalidation.request_redraw()
        if event.type=='LEFTMOUSE' and event.value=='RELEASE':
            delta=_drag['delta'];self.cancel(context)
            if not delta:return {'FINISHED'}
            # Exactly one undoable native-data edit on release; no frame writes
            # while previewing, no custom history, no marker synchronization.
            return bpy.ops.blendcap.vta_marker_edit('EXEC_DEFAULT',True,action='MOVE',frames=delta)
        return {'RUNNING_MODAL'}
