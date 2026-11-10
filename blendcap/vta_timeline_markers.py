"""Cheap Scene timeline-marker presentation; Blender owns all marker data."""
import bpy

HEIGHT=28
_focus=set()


def scope(context):
    return (context.area.as_pointer(),context.scene.as_pointer())


def focused(context):
    return scope(context) in _focus


def focus(context,active=True):
    if active:_focus.add(scope(context))
    else:_focus.discard(scope(context))


def positions(context,scale):
    return tuple((m,context.region.view2d.view_to_region(m.frame,0,clip=False)[0])
                 for m in context.scene.timeline_markers)


def select(context,x,*,extend=False,scale=1):
    hit=min(positions(context,scale),key=lambda row:abs(row[1]-x),default=None)
    marker=hit[0] if hit and abs(hit[1]-x)<=9*scale else None
    if not extend:
        for m in context.scene.timeline_markers:m.select=False
    if marker:marker.select=not marker.select if extend else True
    focus(context)


def draw(context,scale,shader,batch,fill,line):
    """Mirror the native marker strip only; no project/engine query or cache IO."""
    import blf
    height=HEIGHT*scale
    fill(shader,batch,0,0,context.region.width,height,(.14,.14,.14,1))
    line(shader,batch,((0,height),(context.region.width,height)),(.3,.3,.3,1))
    for marker,x in positions(context,scale):
        if not 0<=x<context.region.width:continue
        color=(1,.8,.35,1) if marker.select else (.7,.7,.7,1)
        line(shader,batch,((x-5*scale,6*scale),(x,13*scale),(x+5*scale,6*scale),(x-5*scale,6*scale)),color)
        blf.position(0,x+8*scale,8*scale,0);blf.size(0,11*scale)
        blf.color(0,*color);blf.draw(0,marker.name)


def command(context,action):
    """Delegate data mutation and Undo to Blender's existing marker operators."""
    focus(context)
    if action=='ADD':return bpy.ops.marker.add('EXEC_DEFAULT',True)
    if not any(m.select for m in context.scene.timeline_markers):return {'CANCELLED'}
    if action=='RENAME':return bpy.ops.marker.rename('INVOKE_DEFAULT',True)
    if action=='DELETE':return bpy.ops.marker.delete('INVOKE_DEFAULT',True)
    if action=='MOVE':return bpy.ops.marker.move('INVOKE_DEFAULT',True)
    return {'CANCELLED'}
