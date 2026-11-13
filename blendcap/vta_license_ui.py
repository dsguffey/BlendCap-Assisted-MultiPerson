"""Minimal transient public controls; private engine owns every decision."""
import bpy

from ..blendcap_vta_engine import EngineClient, EngineError, resolve_engine

_status = 'Select Check License Status.'


def _call(operation, payload=None):
    return EngineClient(resolve_engine(), timeout=25).call(operation, payload or {})


def _show(result):
    global _status
    _status = result['message']


class BLENDCAP_OT_vta_license_status(bpy.types.Operator):
    bl_idname = 'blendcap.vta_license_status'
    bl_label = 'Check License Status'

    def execute(self, context):
        global _status
        try:
            _show(_call('license-status'))
            return {'FINISHED'}
        except (EngineError, OSError, ValueError):
            _status = 'License status unavailable. Check the StudioFree runtime.'
            return {'CANCELLED'}


class BLENDCAP_OT_vta_license_activate(bpy.types.Operator):
    bl_idname = 'blendcap.vta_license_activate'
    bl_label = 'Activate StudioFree MoCap'
    bl_options = {'INTERNAL'}

    key: bpy.props.StringProperty(name='License key', subtype='PASSWORD',
                                  options={'SKIP_SAVE'}, maxlen=256)

    def invoke(self, context, event):
        self.key = ''
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        self.layout.prop(self, 'key', text='License key')

    def execute(self, context):
        global _status
        try:
            result = _call('license-activate', {'license_key': self.key.strip()})
            _show(result)
            return {'FINISHED'} if result['state'] == 'activated' else {'CANCELLED'}
        except (EngineError, OSError, ValueError):
            _status = 'Activation unavailable. Check the key and StudioFree runtime.'
            return {'CANCELLED'}
        finally:
            self.key = ''


class BLENDCAP_OT_vta_license_revalidate(bpy.types.Operator):
    bl_idname = 'blendcap.vta_license_revalidate'
    bl_label = 'Retry License Validation'

    def execute(self, context):
        global _status
        try:
            result = _call('license-revalidate')
            _show(result)
            return {'FINISHED'} if result['state'] in ('activated', 'activated_offline') else {'CANCELLED'}
        except (EngineError, OSError, ValueError):
            _status = 'Validation unavailable. Check the StudioFree runtime.'
            return {'CANCELLED'}


def draw(layout):
    layout.label(text=_status, icon='INFO')
    row = layout.row(align=True)
    row.operator('blendcap.vta_license_status', text='Check Status', icon='FILE_REFRESH')
    row.operator('blendcap.vta_license_revalidate', text='Retry Validation')
    layout.operator('blendcap.vta_license_activate', text='Enter License Key', icon='KEYINGSET')
