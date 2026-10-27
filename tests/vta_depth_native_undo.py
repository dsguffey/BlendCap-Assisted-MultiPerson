"""Child-process fixture for native undo; deliberately not a discovery module."""
import importlib.util
from pathlib import Path
import sys
from unittest import mock
import bpy

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('depth_undo_addon',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
addon=importlib.util.module_from_spec(spec);sys.modules[spec.name]=addon;spec.loader.exec_module(addon)
from importlib import import_module
tool=import_module(spec.name+'.blendcap.vta_depth_tool')
keys=import_module(spec.name+'.blendcap.vta_depth_keys')
workspace_ui=import_module(spec.name+'.blendcap.vta_workspace')
layout=import_module(spec.name+'.blendcap_vta_workspace')
tool.register()
area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
region=next(r for r in area.regions if r.type=='WINDOW')
obj=bpy.data.objects.new('depth undo',bpy.data.armatures.new('depth undo'))
bpy.context.collection.objects.link(obj)
camera=bpy.data.objects.new('depth camera',bpy.data.cameras.new('depth camera'))
bpy.context.collection.objects.link(camera)
scene=bpy.context.scene
scene['vta_project_id']='depth undo';scene['vta_source_id']='source'
obj['vta_project_id']='depth undo';obj['vta_source_id']='source';obj['vta_character_id']='character'
scene.frame_set(12)
scene.tool_settings.use_keyframe_insert_auto=True
scene.tool_settings.auto_keying_mode='ADD_REPLACE_KEYS'
def target(_):return bpy.data.objects['depth undo'],bpy.data.objects['depth camera']
def displayed_frames():
    from types import SimpleNamespace as NS
    view=NS(characters=(NS(character_id='character'),))
    rows=layout.channel_rows(view,500,expanded=('character',))
    lanes={r.character_id:r.y for r in rows if r.kind=='depth'}
    ctx=NS(scene=bpy.context.scene,region=NS(view2d=NS(view_to_region=lambda f,y,**kw:(f*10,y))))
    return tuple(mark[1] for mark in workspace_ui.depth_key_markers(ctx,lanes,1))
with bpy.context.temp_override(area=area,region=region),mock.patch.object(tool,'target',side_effect=target):
    bpy.ops.ed.undo_push(message='Before depth')
    assert bpy.ops.blendcap.vta_depth_drag(distance=1)=={'FINISHED'}
    obj,camera=target(None)
    assert keys.keyed_frames(obj)==(12.,)
    assert displayed_frames()==(12.,)
    bpy.ops.ed.undo_push(message='Depth transform and key')
    assert bpy.ops.ed.undo()=={'FINISHED'}
    obj,camera=target(None)
    assert obj.location.length<1e-7 and keys.keyed_frames(obj)==()
    assert displayed_frames()==()
    assert bpy.ops.ed.redo()=={'FINISHED'}
    obj,camera=target(None)
    assert (obj.location-tool.depth_axis(camera)).length<1e-7 and keys.keyed_frames(obj)==(12.,)
    assert displayed_frames()==(12.,)
    for c in keys.location_curves(obj):
        c.keyframe_points[0].co.x=18
        c.update()
    bpy.ops.ed.undo_push(message='Moved native key')
    assert keys.keyed_frames(obj)==(18.,)
    assert displayed_frames()==(18.,)
    bpy.ops.ed.undo();obj,camera=target(None)
    assert keys.keyed_frames(obj)==(12.,)
    assert displayed_frames()==(12.,)
    bpy.ops.ed.redo();obj,camera=target(None)
    assert keys.keyed_frames(obj)==(18.,)
    assert displayed_frames()==(18.,)
    for c in keys.location_curves(obj):c.keyframe_points.remove(c.keyframe_points[0])
    bpy.ops.ed.undo_push(message='Deleted native key')
    assert keys.keyed_frames(obj)==()
    assert displayed_frames()==()
    bpy.ops.ed.undo();obj,camera=target(None)
    assert keys.keyed_frames(obj)==(18.,)
    assert displayed_frames()==(18.,)
    bpy.ops.ed.redo();obj,camera=target(None)
    assert keys.keyed_frames(obj)==()
    assert displayed_frames()==()
tool.unregister()
(Path(sys.argv[-1])/'verified.txt').write_text('Native transform/key/move/delete undo-redo verified')
