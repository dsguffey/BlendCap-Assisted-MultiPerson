"""Actual native Undo/Redo transaction; synthetic objects only, no UI automation."""
import sys,types,importlib,json
from pathlib import Path
import bpy
P=Path(__file__).resolve().parents[1]
for name,path in [('metadata_undo',P),('metadata_undo.blendcap',P/'blendcap'),('metadata_undo.blendcap.retarget',P/'blendcap/retarget')]:
    m=types.ModuleType(name);m.__path__=[str(path)];sys.modules[name]=m
meta=importlib.import_module('metadata_undo.blendcap.vta_metadata')
output=importlib.import_module('metadata_undo.blendcap.vta_skeletal_output')
obj=bpy.data.objects.new('metadata undo rig',bpy.data.armatures.new('metadata undo data'));bpy.context.scene.collection.objects.link(obj)
for key in ('vta_project_id','vta_source_id','vta_character_id','vta_output_file'):obj[key]='synthetic'
obj['vta_output_stale']=True;obj['vta_camera_registration_version']=1
obj.keyframe_insert('location',frame=1)
for key in meta.FIELDS:obj.keyframe_insert('["'+key+'"]',frame=1)
class Repair(bpy.types.Operator):
    bl_idname='test.vta_metadata_repair';bl_label='Synthetic metadata migration';bl_options={'REGISTER','UNDO'}
    def execute(self,context):meta.repair([bpy.data.objects['metadata undo rig']]);return {'FINISHED'}
bpy.utils.register_class(Repair)
def paths():return {c.data_path for c in output._iter_action_fcurves(bpy.data.objects['metadata undo rig'].animation_data.action)}
area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D');region=next(r for r in area.regions if r.type=='WINDOW')
with bpy.context.temp_override(area=area,region=region):
    bpy.ops.ed.undo_push(message='Before metadata migration');before=paths()
    assert bpy.ops.test.vta_metadata_repair()=={'FINISHED'};assert paths()=={'location'}
    bpy.ops.ed.undo_push(message='After metadata migration');bpy.ops.ed.undo();assert paths()==before
    bpy.ops.ed.redo();assert paths()=={'location'}
(Path(sys.argv[-1])/'verified.json').write_text(json.dumps(dict(undo_redo=True)))
