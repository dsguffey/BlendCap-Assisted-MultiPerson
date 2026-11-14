"""Minimal transient public controls; private engine owns every decision."""
import bpy
import textwrap

from ..blendcap_vta_engine import EngineClient, EngineError, resolve_engine

_status = 'Select Check License Status.'


def _call(operation, payload=None):
    return EngineClient(resolve_engine(), timeout=25).call(operation, payload or {})


def _show(result, action):
    global _status
    state = result['state']
    quantity = result.get('quantity')
    if state in ('activated', 'activated_offline') and type(quantity) is int:
        kind = 'Individual license' if quantity == 1 else f'{quantity}-seat license'
        if state == 'activated_offline':
            if action == 'revalidate':
                _status = f'Online check unavailable; {kind.lower()} active under offline grace.'
            elif action == 'activate':
                _status = f'Online activation unconfirmed; existing {kind.lower()} active offline.'
            else:
                _status = f'Local status: {kind.lower()} active under offline grace.'
        elif action == 'revalidate':
            _status = f'Verified online; {kind.lower()} active.'
        elif action == 'activate':
            _status = f'License key accepted; {kind.lower()} active.'
        else:
            _status = f'Local status: {kind.lower()} active.'
    else:
        prefix = ('Activation failed: ' if action == 'activate' else
                  'Validation failed: ' if action == 'revalidate' else '')
        _status = prefix + result['message']


def _announce(operator, context, message, *, action, active):
    operator.report({'INFO'} if active else {'WARNING'}, message)
    area = getattr(context, 'area', None)
    if area is not None:
        area.tag_redraw()
    title = {'status': 'License status', 'activate': 'License activation',
             'revalidate': 'License validation'}[action]

    def draw(menu, _context):
        for line in textwrap.wrap(message, width=58, break_long_words=False):
            menu.layout.label(text=line)

    try:
        context.window_manager.popup_menu(draw, title=title,
                                          icon='INFO' if active else 'ERROR')
    except RuntimeError:
        pass  # The panel label and Blender report still carry the result.


def _notify(operator, context, result, action):
    _show(result, action)
    active = result['state'] in ('activated', 'activated_offline')
    _announce(operator, context, _status, action=action, active=active)


class BLENDCAP_OT_vta_license_status(bpy.types.Operator):
    bl_idname = 'blendcap.vta_license_status'
    bl_label = 'Check License Status'
    bl_description = 'Read locally stored entitlement status without contacting Gumroad or increasing activation uses'

    def execute(self, context):
        global _status
        try:
            _notify(self, context, _call('license-status'), 'status')
            return {'FINISHED'}
        except (EngineError, OSError, ValueError):
            _status = 'License status unavailable. Check the StudioFree runtime.'
            _announce(self, context, _status, action='status', active=False)
            return {'CANCELLED'}


class BLENDCAP_OT_vta_license_activate(bpy.types.Operator):
    bl_idname = 'blendcap.vta_license_activate'
    bl_label = 'Activate StudioFree MoCap'
    bl_description = 'Verify a key online for this Windows user; a new activation records one Gumroad use, and the key is not saved in the Blender project'
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
            _notify(self, context, result, 'activate')
            return {'FINISHED'} if result['state'] in ('activated', 'activated_offline') else {'CANCELLED'}
        except (EngineError, OSError, ValueError):
            _status = 'Activation unavailable. Check the key and StudioFree runtime.'
            _announce(self, context, _status, action='activate', active=False)
            return {'CANCELLED'}
        finally:
            self.key = ''


class BLENDCAP_OT_vta_license_revalidate(bpy.types.Operator):
    bl_idname = 'blendcap.vta_license_revalidate'
    bl_label = 'Retry License Validation'
    bl_description = 'Verify the stored key online without increasing Gumroad activation uses'

    def execute(self, context):
        global _status
        try:
            result = _call('license-revalidate')
            _notify(self, context, result, 'revalidate')
            return {'FINISHED'} if result['state'] in ('activated', 'activated_offline') else {'CANCELLED'}
        except (EngineError, OSError, ValueError):
            _status = 'Validation unavailable. Check the StudioFree runtime.'
            _announce(self, context, _status, action='revalidate', active=False)
            return {'CANCELLED'}


def draw(layout):
    layout.label(text=_status, icon='INFO')
    row = layout.row(align=True)
    row.operator('blendcap.vta_license_status', text='Check Status', icon='FILE_REFRESH')
    row.operator('blendcap.vta_license_revalidate', text='Retry Validation')
    layout.operator('blendcap.vta_license_activate', text='Enter License Key', icon='KEYINGSET')
