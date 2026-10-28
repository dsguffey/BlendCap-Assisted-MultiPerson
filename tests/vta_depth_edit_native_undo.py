"""No event injection; actual Blender operator/Undo transaction proof on synthetic rigs."""
import sys,importlib.util,importlib,json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock
import bpy
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('depth_group_undo',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)]);addon=importlib.util.module_from_spec(spec);sys.modules[spec.name]=addon;spec.loader.exec_module(addon)
tool=importlib.import_module(spec.name+'.blendcap.vta_depth_tool');keys=importlib.import_module(spec.name+'.blendcap.vta_depth_keys');ws=importlib.import_module(spec.name+'.blendcap.vta_workspace')
tool.register();bpy.utils.register_class(ws.BLENDCAP_OT_vta_depth_delete)
bridge=importlib.import_module(spec.name+'.blendcap.vta_review_undo')
mode='mode-on' in sys.argv
bpy.context.workspace['blendcap_vta_mode_enabled']=mode
# Real production native callbacks are active. No engine history is allowed.
refresh=mock.patch.object(bridge,'_refresh');refresh.start()
forbidden=mock.patch.object(bridge,'_client',side_effect=AssertionError('Native edit requested engine history'));forbidden.start()
bridge.register()
scene=bpy.context.scene;scene['vta_project_id']='p';scene['vta_source_id']='s';scene.frame_set(12);scene.tool_settings.use_keyframe_insert_auto=True;scene.tool_settings.auto_keying_mode='ADD_REPLACE_KEYS'
for i in range(2):
    o=bpy.data.objects.new('group '+str(i),bpy.data.armatures.new('group data '+str(i)));scene.collection.objects.link(o)
    o['vta_character_id']=str(i);o['vta_project_id']='p';o['vta_source_id']='s';o.location.y=i*.4;o.keyframe_insert('location',frame=5)
    o.scale=(1.2,1.3,1.4);o.keyframe_insert('scale',frame=7) # Unrelated manual animation.
camera=bpy.data.objects.new('group camera',bpy.data.cameras.new('camera'));scene.collection.objects.link(camera)
def resolve(_ctx):return tuple(bpy.data.objects['group '+str(i)] for i in range(2)),bpy.data.objects['group camera']
def allframes():return tuple(keys.keyed_frames(o) for o in resolve(None)[0])
area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D');region=next(r for r in area.regions if r.type=='WINDOW')
with bpy.context.temp_override(area=area,region=region),mock.patch.object(tool,'targets',side_effect=resolve):
    bpy.ops.ed.undo_push(message='Before group depth')
    assert bpy.ops.blendcap.vta_depth_drag(distance=.3)=={'FINISHED'};assert allframes()==((5.,12.),(5.,12.))
    axis=tool.depth_axis(resolve(None)[1]);a,b=resolve(None)[0];assert abs((b.location-a.location).y-.4)<1e-6
    bpy.ops.ed.undo_push(message='One group depth and keys')
    for _ in range(3):
        bpy.ops.ed.undo();assert allframes()==((5.,),(5.,))
        assert all(abs(o.location.z)<1e-6 for o in resolve(None)[0])
        bpy.ops.ed.redo();assert allframes()==((5.,12.),(5.,12.))
area.type='DOPESHEET_EDITOR';region=next(r for r in area.regions if r.type=='WINDOW')
with bpy.context.temp_override(area=area,region=region),mock.patch.object(ws,'_scope',return_value=True),mock.patch.object(ws,'_state',return_value=(NS(characters=(NS(character_id='0'),NS(character_id='1'))),)):
    rigs=keys.scene_rigs(bpy.context.scene,['0','1']);keys.select_frame(rigs,'0',5);keys.select_frame(rigs,'1',12,extend=True)
    ws._depth_focus.add(ws._expansion_key(bpy.context));bpy.ops.ed.undo_push(message='Before selected depth delete')
    assert bpy.ops.blendcap.vta_depth_delete()=={'FINISHED'};assert allframes()==((12.,),(5.,))
    bpy.ops.ed.undo_push(message='One depth delete')
    for _ in range(3):
        bpy.ops.ed.undo();assert allframes()==((5.,12.),(5.,12.))
        bpy.ops.ed.redo();assert allframes()==((12.,),(5.,))
    rigs=keys.scene_rigs(bpy.context.scene,['0','1']);keys.select_frame(rigs,'0',12)
    bpy.ops.ed.undo_push(message='Before single delete')
    assert bpy.ops.blendcap.vta_depth_delete()=={'FINISHED'};assert allframes()==((),(5.,))
    bpy.ops.ed.undo_push(message='Single native delete')
    for _ in range(3):
        bpy.ops.ed.undo();assert allframes()==((12.,),(5.,))
        bpy.ops.ed.redo();assert allframes()==((),(5.,))
bridge.unregister();forbidden.stop();refresh.stop()
for obj in resolve(None)[0]:
    action=obj.animation_data.action
    curves=action.layers[0].strips[0].channelbag(obj.animation_data.action_slot).fcurves
    assert all(tuple(k.co.x for k in c.keyframe_points)==(7.,) for c in curves if c.data_path=='scale')
(Path(sys.argv[-1])/'verified.json').write_text(json.dumps(dict(group_drag=True,auto_key_both=True,relative_preserved=True,delete_xyz_character_isolation=True,undo_redo_both=True,single_delete=True,cycles_per_operation=3,vta_mode=mode,engine_history_calls=0)))
