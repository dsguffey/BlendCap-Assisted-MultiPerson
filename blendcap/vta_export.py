"""Export current evaluated VTA animation, including native object/Depth motion."""
import os
from pathlib import Path
from uuid import uuid4
import bpy
from bpy_extras.io_utils import ExportHelper, axis_conversion
from mathutils import Matrix


def eligible(context):
    obj=context.active_object
    return bool(obj and obj.type=='ARMATURE' and obj.get('vta_character_id') and obj.animation_data and obj.animation_data.action)


def export_current(context,source,destination,start,end):
    from io_anim_bvh.export_bvh import write_armature
    destination=Path(destination)
    if destination.suffix.lower()!='.bvh':raise ValueError('Choose a .bvh export destination.')
    if bpy.data.filepath:
        from ..blendcap_vta_review import companion_root_for_blend
        if destination.resolve().is_relative_to(companion_root_for_blend(bpy.data.filepath).resolve()):
            raise ValueError('Export outside the VTA companion folder to preserve its accepted results.')
    if start>end:raise ValueError('Export start must not exceed end.')
    scene=context.scene;frame=scene.frame_current;subframe=scene.frame_subframe
    active=context.view_layer.objects.active;selected=tuple(context.selected_objects)
    temporary=Path(destination).with_name('.'+Path(destination).name+'.'+uuid4().hex+'.bvh')
    rig=None;data=None;action=None
    try:
        scene.frame_set(start);context.view_layer.update()
        # The stock writer ignores object transforms. Bake evaluated world
        # matrices on an isolated temporary skeleton before invoking it.
        conversion=axis_conversion(from_forward='-Z',from_up='Y').to_4x4().inverted()
        reference_scale=source.matrix_world.to_scale()
        if min(reference_scale)<=0 or max(reference_scale)-min(reference_scale)>1e-4:
            raise ValueError('BVH export requires a positive uniform Character object scale.')
        data=source.data.copy();data.transform(conversion @ source.matrix_world)
        rig=bpy.data.objects.new('VTA export temporary',data);scene.collection.objects.link(rig)
        rig.matrix_world=Matrix.Identity(4)
        for obj in selected:obj.select_set(False)
        rig.select_set(True);context.view_layer.objects.active=rig
        for number in range(start,end+1):
            scene.frame_set(number);context.view_layer.update()
            evaluated=source.evaluated_get(context.evaluated_depsgraph_get())
            for bone in rig.pose.bones:
                bone.rotation_mode='QUATERNION'
                matrix=conversion @ evaluated.matrix_world @ evaluated.pose.bones[bone.name].matrix
                location,rotation,scale=matrix.decompose()
                if any(abs(v-reference_scale[0])>1e-4 for v in scale):
                    raise ValueError('BVH cannot preserve animated bone scale; use another interchange format.')
                # Static object scale is already represented by rest lengths.
                bone.matrix=Matrix.LocRotScale(location,rotation,(1,1,1))
                context.view_layer.update()
                if any(abs(v-1)>1e-4 for v in bone.scale):
                    raise ValueError('BVH cannot preserve animated bone scale; use another interchange format.')
                for path in ('location','rotation_quaternion'):bone.keyframe_insert(path,frame=number)
        action=rig.animation_data.action
        with context.temp_override(object=rig,active_object=rig,selected_objects=[rig],selected_editable_objects=[rig]):
            write_armature(context,str(temporary),start,end,rotate_mode='XYZ',root_transform_only=False)
        os.replace(temporary,destination)
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass  # A locked scratch file must not prevent native cleanup.
        if rig is not None:
            if rig.animation_data:action=rig.animation_data.action
            bpy.data.objects.remove(rig,do_unlink=True)
        if action is not None and action.users==0:bpy.data.actions.remove(action)
        if data is not None and data.users==0:bpy.data.armatures.remove(data)
        for obj in selected:
            if context.view_layer.objects.get(obj.name) is obj:obj.select_set(True)
        context.view_layer.objects.active=active
        scene.frame_set(frame,subframe=subframe)


class BLENDCAP_OT_vta_export_bvh(bpy.types.Operator,ExportHelper):
    bl_idname='blendcap.vta_export_bvh';bl_label='Export BVH'
    bl_description='Export the selected VTA Character’s current animation, including manual Depth and cleanup, over the Scene frame range'
    filename_ext='.bvh'
    filter_glob:bpy.props.StringProperty(default='*.bvh',options={'HIDDEN'})
    @classmethod
    def poll(cls,context):
        from .vta_jobs_ui import busy
        from .vta_marks import busy as marks_busy
        return eligible(context) and not busy() and not marks_busy() and context.mode=='OBJECT'
    def execute(self,context):
        if not self.poll(context):return {'CANCELLED'}
        try:
            export_current(context,context.active_object,self.filepath,context.scene.frame_start,context.scene.frame_end)
            self.report({'INFO'},'Current Character animation exported.');return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}


class VIEW3D_PT_vta_export(bpy.types.Panel):
    bl_idname='VIEW3D_PT_vta_export';bl_label='Export'
    bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='VTA'
    bl_options={'DEFAULT_CLOSED'};bl_order=900
    def draw(self,context):
        self.layout.label(text='Selected VTA Character • Scene frame range')
        self.layout.operator('blendcap.vta_export_bvh',icon='EXPORT')
        self.layout.label(text='Includes current Depth and animation edits.')

def register():
    from .vta_busy import install
    install(BLENDCAP_OT_vta_export_bvh)
    for cls in (BLENDCAP_OT_vta_export_bvh,):bpy.utils.register_class(cls)
def unregister():
    for cls in (BLENDCAP_OT_vta_export_bvh,):bpy.utils.unregister_class(cls)
