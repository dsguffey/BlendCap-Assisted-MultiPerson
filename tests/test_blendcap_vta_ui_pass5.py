"""Bounded Coverage, Restore visibility and product-identity regressions."""
import importlib
import unittest
from types import SimpleNamespace as NS
from unittest import mock
try:
    import bpy
except ImportError:
    bpy = None


class Layout:
    enabled = True
    def __init__(self, events=None): self.events = [] if events is None else events
    def row(self, **kw): return Layout(self.events)
    column = box = row
    def label(self, **kw): self.events.append(('label', kw.get('text'), self.enabled))
    def prop(self, data, name, **kw): self.events.append(('prop', name, kw.get('text')))
    def operator(self, name, **kw):
        result = NS()
        self.events.append(('operator', name, kw.get('text'), self.enabled, result))
        return result


@unittest.skipUnless(bpy is not None, 'Blender required')
class UiPass5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_ui_polish import UiPolishTests
        UiPolishTests.setUpClass()
        cls.addon = UiPolishTests.addon
    def module(self, name):
        return importlib.import_module(self.addon.__name__+'.blendcap.'+name)

    def draw_animation(self, scope):
        ui = self.module('vta_review_ui')
        wm = NS(blendcap_vta_animation_scope=scope, blendcap_vta_animation_start=11,
                blendcap_vta_animation_end=20)
        context = NS(window_manager=wm, scene=NS(objects=()))
        layout = Layout()
        with mock.patch.object(ui, 'cached_scene_source', return_value=None), \
             mock.patch.object(ui, '_view', NS()), mock.patch.object(ui, '_analysis_locked', return_value=False), \
             mock.patch.object(ui, 'generation_unavailable_reason', return_value=''), \
             mock.patch.object(ui, '_feedback_key', return_value=None), \
             mock.patch.object(ui, '_reconstruction_process', None), \
             mock.patch.object(ui, 'draw_operation_feedback'), \
             mock.patch.object(self.module('vta_jobs_ui'), 'draw'):
            ui.VIEW3D_PT_blendcap_vta_animation.draw(NS(layout=layout), context)
        return layout.events

    def test_coverage_has_one_command_and_hides_bounds_only_for_whole(self):
        for scope in ('WHOLE', 'RANGE'):
            with self.subTest(scope=scope):
                events = self.draw_animation(scope)
                props = [e[1] for e in events if e[0]=='prop']
                self.assertEqual(props, ['blendcap_vta_animation_scope'] +
                    (['blendcap_vta_animation_start','blendcap_vta_animation_end'] if scope=='RANGE' else []))
                generate = [e for e in events if e[0]=='operator' and e[2]=='Generate Animation']
                self.assertEqual(len(generate), 1)
                self.assertEqual(generate[0][4].action, 'RANGE')  # Existing operator path.
                self.assertIn(('label','Characters: selected MoCap rows only',True), events)

    def test_current_frame_shortcuts_select_range_without_starting_generation(self):
        ui = self.module('vta_review_ui')
        for action, field in [('START_CURRENT','start'), ('END_CURRENT','end')]:
            wm = NS(blendcap_vta_animation_scope='WHOLE')
            context = NS(window_manager=wm,scene=NS(frame_current=42))
            with mock.patch.object(self.module('vta_jobs_ui'),'start_generation') as start:
                result=ui.BLENDCAP_OT_vta_generate_animation.execute(NS(action=action),context)
            self.assertEqual(result,{'FINISHED'})
            self.assertEqual(wm.blendcap_vta_animation_scope,'RANGE')
            self.assertEqual(getattr(wm,'blendcap_vta_animation_'+field),42)
            start.assert_not_called()

    def test_restore_always_visible_with_existing_action_and_availability(self):
        ui=self.module('vta_review_ui');cleanup=self.module('vta_camera_cleanup')
        for available in (False,True):
            layout=Layout()
            with mock.patch.object(cleanup,'availability',side_effect=lambda c,a='REMOVE':(available,'Select an eligible Character.')), \
                 mock.patch.object(cleanup,'busy',return_value=False),mock.patch.object(ui,'draw_operation_feedback'), \
                 mock.patch.object(cleanup,'targets',side_effect=AssertionError('No extra target query in draw')):
                cleanup.draw_cleanup(layout,NS())
            rows=[e for e in layout.events if e[0]=='operator' and e[2]=='Restore Source Alignment']
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0][3],available)
            self.assertEqual(rows[0][4].action,'RESTORE')

    def test_product_banner_is_unconditional_and_not_a_collapsible_section(self):
        ui=self.module('vta_review_ui');layout=Layout()
        ui.VIEW3D_PT_blendcap_vta_title.draw(NS(layout=layout),NS())
        self.assertEqual(layout.events,[('label','StudioFree MoCap',True)])
        self.assertIn('HIDE_HEADER',ui.VIEW3D_PT_blendcap_vta_title.bl_options)
        self.assertLess(ui.VIEW3D_PT_blendcap_vta_title.bl_order,ui.VIEW3D_PT_blendcap_vta_review.bl_order)

    def test_clear_assignment_is_retained_in_menu_but_absent_from_sidebar(self):
        import inspect
        ui=self.module('vta_review_ui')
        self.assertNotIn('clear-assignment',inspect.getsource(ui.VIEW3D_PT_blendcap_vta_current_frame.draw))
        self.assertIn('clear-assignment',inspect.getsource(ui.BLENDCAP_MT_vta_commands.draw))
        self.assertIn('clear-assignment',dict((a,b) for a,b,*_ in ui._MANAGEMENT_ACTIONS))
