import importlib,unittest
from types import SimpleNamespace as NS
from unittest import mock
try:import bpy
except ImportError:bpy=None

class Layout:
    enabled=True
    def __init__(self,events=None):self.events=events if events is not None else []
    def panel(self,key,**kw):self.events.append(('panel',key));return self,self
    def label(self,**kw):self.events.append(('label',kw.get('text')))
    def operator(self,name,**kw):self.events.append(('operator',name));return NS()
    def row(self,**kw):return self
    def column(self,**kw):return self
    def box(self):return self
    def separator(self):pass
    def prop(self,*a,**kw):pass
    def prop_enum(self,*a,**kw):pass

@unittest.skipUnless(bpy is not None,'Blender required')
class UiPass2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_ui_polish import UiPolishTests
        UiPolishTests.setUpClass();cls.addon=UiPolishTests.addon
    def module(self,name):return importlib.import_module(self.addon.__name__+'.blendcap.'+name)
    def test_registered_hierarchy(self):
        ui=self.module('vta_review_ui');registration=self.addon._registration
        self.assertEqual(ui.VIEW3D_PT_blendcap_vta_current_frame.bl_label,'Character Assignment')
        self.assertEqual(ui.VIEW3D_PT_blendcap_vta_review.bl_label,'MoCap Setup')
        names={c.__name__ for c in registration.classes}
        self.assertIn('VIEW3D_PT_blendcap_vta_characters',names)
        self.assertNotIn('VIEW3D_PT_blendcap_vta_generation_camera',names)
        self.assertNotIn('VIEW3D_PT_vta_credits',names)
        self.assertEqual(self.module('vta_retarget').VIEW3D_PT_vta_retarget.bl_label,'Export / Retarget')
        self.assertGreater(ui.VIEW3D_PT_blendcap_vta_advanced.bl_order,self.module('vta_retarget').VIEW3D_PT_vta_retarget.bl_order)
    def test_single_workspace_top_level_order_and_preferences_location(self):
        ui=self.module('vta_review_ui');panels=self.module('panels')
        root_panels=[c for c in self.addon._registration.classes
                     if isinstance(c,type) and issubclass(c,bpy.types.Panel)
                     and getattr(c,'bl_space_type','')=='VIEW_3D'
                     and getattr(c,'bl_category','')=='MoCap' and not getattr(c,'bl_parent_id','') and 'HIDE_HEADER' not in getattr(c,'bl_options',set())]
        ordered=sorted(root_panels,key=lambda c:getattr(c,'bl_order',0))
        self.assertEqual([c.bl_label for c in ordered],['MoCap Setup','Analyze','Character Management','Character Assignment','Update Tracking','Animation','Overview','Export / Retarget','Resources'])
        self.assertEqual(sum(c.bl_label=='MoCap Setup' for c in root_panels),1)
        self.assertIn('DEFAULT_CLOSED',ui.VIEW3D_PT_blendcap_vta_review.bl_options)
        layout=Layout()
        with mock.patch.object(panels,'addon_prefs',return_value=None),mock.patch.object(panels,'_draw_source_section'):
            panels.draw_blendcap_ui(NS(layout=layout),NS(scene=NS(blendcap_props=NS())))
        self.assertNotIn(('label','Workspace'),layout.events)
        self.assertNotIn(('operator','blendcap.install_open_prefs'),layout.events)
        layout=Layout()
        with mock.patch.object(ui,'addon_prefs',return_value=None),mock.patch.object(panels,'addon_prefs',return_value=None):
            ui.VIEW3D_PT_blendcap_vta_advanced.draw(NS(layout=layout),bpy.context)
        self.assertIn(('operator','blendcap.install_open_prefs'),layout.events)

    def test_product_names_do_not_change_existing_operator_ids(self):
        ui=self.module('vta_review_ui');workspace=self.module('vta_workspace')
        self.assertEqual(importlib.import_module(self.addon.__name__+'.blendcap_vta_product').PRODUCT_NAME,'StudioFree MoCap')
        for cls,label in [(ui.BLENDCAP_OT_vta_enter_mode,'Enter MoCap Mode'),(ui.BLENDCAP_OT_vta_exit_mode,'Exit MoCap Mode'),(ui.BLENDCAP_OT_vta_load_scene,'Load MoCap Scene'),(ui.BLENDCAP_MT_vta_commands,'MoCap Commands'),(workspace.BLENDCAP_MT_vta_timeline_commands,'MoCap Commands')]:
            self.assertEqual(cls.bl_label,label)
            self.assertIn('vta_',cls.bl_idname)
        self.assertEqual(ui.BLENDCAP_OT_vta_enter_mode.bl_idname,'blendcap.vta_enter_mode')
        self.assertEqual(ui.BLENDCAP_OT_vta_exit_mode.bl_idname,'blendcap.vta_exit_mode')
    def test_visible_recovery_and_preferences_use_mocap(self):
        bridge=self.module('vta_review_undo');layout=Layout()
        with mock.patch.object(bridge,'_pending',{'project':object()}):bridge.draw_pending(layout)
        labels=[value for kind,value in layout.events if kind=='label']
        self.assertIn('MoCap reconciliation pending',labels)
        self.assertFalse(any('VTA' in text for text in labels))

    def test_runtime_config_location_keeps_vta_compatibility_path(self):
        runtime=importlib.import_module(self.addon.__name__+'.blendcap_vta_runtime')
        from pathlib import Path
        self.assertEqual(runtime.configuration_path(environ={'LOCALAPPDATA':str(Path.cwd())}),Path.cwd()/'VTA/config/runtime.json')

    def test_overview_uses_cached_state_without_io_or_nested_sections(self):
        from pathlib import Path
        ui=self.module('vta_review_ui');marks=self.module('vta_marks');layout=Layout()
        context=NS(scene=NS(frame_current=21,render=NS(fps=24,fps_base=1)),window_manager=NS(blendcap_vta_animation_start=1,blendcap_vta_animation_end=90))
        info=('project',{'fps':24},NS(source_path=Path('talk.mp4')),None)
        with mock.patch.object(ui,'cached_scene_source',return_value=info),mock.patch.object(ui,'_view',NS(candidates=(),characters=(),analysis_revision_id='cached')),mock.patch.object(ui,'_display',NS(context=NS(coverage=(0,40)))),mock.patch.object(ui,'tracking_character_ids',return_value=('white',)),mock.patch.object(ui,'_feedback_key',return_value='key'),mock.patch.object(ui,'_operation_feedback',{}),mock.patch.object(marks,'scope',return_value='scope'),mock.patch.object(marks.marks,'items',return_value=[]),mock.patch.object(ui,'refresh',side_effect=AssertionError('Overview must not refresh')),mock.patch.object(ui,'_engine_client',side_effect=AssertionError('Overview must not start IPC')):
            for _ in range(20):ui.VIEW3D_PT_blendcap_vta_display.draw(NS(layout=layout),context)
        self.assertFalse(any(kind=='panel' for kind,_ in layout.events))
        self.assertIn(('label','Source: talk.mp4'),layout.events)
        self.assertFalse(any(kind=='label' and 'Generation selection:' in (value or '') for kind,value in layout.events))
        self.assertFalse(any('Update needed' in str(value) for _,value in layout.events))
    def test_apply_tooltip_explains_unavailable_without_changing_action(self):
        marks=self.module('vta_marks');ui=self.module('vta_review_ui');bridge=self.module('vta_review_undo')
        with mock.patch.object(bridge,'blocked',return_value=False),mock.patch.object(ui,'_analysis_locked',return_value=False),mock.patch.object(marks,'scope',return_value='scope'),mock.patch.object(marks.marks,'items',return_value=[]):
            text=marks.BLENDCAP_OT_vta_apply_marks.description(bpy.context,NS(kind='assign'))
        self.assertIn('Mark candidates to enable',text)
        with mock.patch.object(ui,'generation_unavailable_reason',return_value='Select at least one Character.'):
            text=ui.BLENDCAP_OT_vta_generate_animation.description(bpy.context,NS(action='RANGE'))
        self.assertIn('Unavailable: Select at least one Character.',text)

    def test_create_operator_focuses_exact_new_workspace(self):
        workspace=self.module('vta_workspace');created=object();context=NS(window=NS(workspace=object()))
        with mock.patch.object(workspace,'create_vta_workspace',return_value=created) as create:
            result=workspace.BLENDCAP_OT_open_vta_workspace.execute(NS(report=mock.Mock()),context)
        self.assertEqual(result,{'FINISHED'});create.assert_called_once_with(context)
        self.assertIs(context.window.workspace,created)

    def test_workspace_sections_and_advanced_order(self):
        ui=self.module('vta_review_ui');layout=Layout()
        with mock.patch.object(ui,'refresh',return_value=True),mock.patch.object(ui,'_view',None),mock.patch.object(ui,'cached_scene_source',return_value=None),mock.patch.object(ui,'draw_scene_workflow'),mock.patch.object(self.module('vta_workspace'),'blank_animation_guidance',return_value=[]),mock.patch.object(self.module('panels'),'draw_blendcap_ui'):
            ui.VIEW3D_PT_blendcap_vta_review.draw(NS(layout=layout),NS(scene=NS(blendcap_props=NS())))
        panels=[v for k,v in layout.events if k=='panel']
        self.assertEqual(panels,['vta_workspace_scenes','vta_workspace_camera','vta_workspace_camera_advanced','vta_setup_display'])
        self.assertEqual(layout.events.count(('operator','blendcap.open_vta_workspace')),1)
        self.assertNotIn(('operator','blendcap.vta_camera_depth'),layout.events)
        self.assertIn(('label','Video Source'),layout.events)
        self.assertNotIn(('label','Details'),layout.events)
    def test_marks_use_current_destination_without_changing_mark_data(self):
        marks=self.module('vta_marks');ui=self.module('vta_review_ui');overlay=self.module('vta_overlay')
        candidates=tuple(overlay.OverlayRegionCandidate(v,v,(0,0,100,100),None,False) for v in ('a','b','c'))
        def items(scope,kind):return [dict(frame=10,observation_id='a' if kind=='assign' else 'b')]
        view=NS(source_frame=10,characters=(NS(character_id='white',display_name='White'),))
        with mock.patch.object(ui,'_view',view),mock.patch.object(ui,'current_character_id',return_value='white'),mock.patch.object(marks,'scope',return_value='scope'),mock.patch.object(marks.marks,'items',side_effect=items):
            result=marks.overlay_labels(candidates,bpy.context)
        self.assertEqual([c.mark_state for c in result],['assign','suppress',''])
        self.assertEqual([c.label for c in result],['(White) a','[Suppress] b','c'])
        self.assertEqual(candidates[0].label,'a')
    def test_timeline_recovery_precedes_unavailable_view(self):
        ui=self.module('vta_review_ui');workspace=self.module('vta_workspace');bridge=self.module('vta_review_undo');layout=Layout()
        with mock.patch.object(bridge,'_pending',{'root':object()}),mock.patch.object(ui,'draw_scene_workflow'),mock.patch.object(ui,'draw_scene_timing'),mock.patch.object(ui,'refresh',return_value=False):
            workspace.BLENDCAP_MT_vta_timeline_commands.draw(NS(layout=layout),bpy.context)
        self.assertEqual(next(v for k,v in layout.events if k=='operator'),'blendcap.vta_reconcile')
    def test_resources_render_known_version_and_credits(self):
        ui=self.module('vta_review_ui');sections=self.module('vta_ui_sections');layout=Layout()
        sections.version_lines.cache_clear()
        lines=sections.version_lines();self.assertTrue(any('1.0.0' in line for line in lines),lines)
        with mock.patch.object(ui,'addon_prefs',return_value=None),mock.patch.object(self.module('panels'),'addon_prefs',return_value=None):
            ui.VIEW3D_PT_blendcap_vta_advanced.draw(NS(layout=layout),bpy.context)
        self.assertIn(('panel','vta_resource_version'),layout.events)
        self.assertTrue(any(k=='label' and 'BlendCap' in (v or '') for k,v in layout.events))
