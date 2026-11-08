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
        self.assertEqual(ui.VIEW3D_PT_blendcap_vta_review.bl_label,'Workspace')
        names={c.__name__ for c in registration.classes}
        self.assertNotIn('VIEW3D_PT_blendcap_vta_characters',names)
        self.assertNotIn('VIEW3D_PT_blendcap_vta_generation_camera',names)
        self.assertNotIn('VIEW3D_PT_vta_credits',names)
        self.assertEqual(self.module('vta_retarget').VIEW3D_PT_vta_retarget.bl_label,'Export / Retarget')
        self.assertGreater(ui.VIEW3D_PT_blendcap_vta_advanced.bl_order,self.module('vta_retarget').VIEW3D_PT_vta_retarget.bl_order)
    def test_workspace_sections_and_advanced_order(self):
        ui=self.module('vta_review_ui');layout=Layout()
        with mock.patch.object(ui,'refresh',return_value=True),mock.patch.object(ui,'_view',None),mock.patch.object(ui,'cached_scene_source',return_value=None),mock.patch.object(ui,'draw_scene_workflow'),mock.patch.object(self.module('vta_workspace'),'blank_animation_guidance',return_value=[]):
            ui.VIEW3D_PT_blendcap_vta_review.draw(NS(layout=layout),NS(scene=NS(blendcap_props=NS())))
        panels=[v for k,v in layout.events if k=='panel']
        self.assertEqual(panels,['vta_workspace_scenes','vta_workspace_camera','vta_workspace_camera_advanced','vta_workspace_details'])
        self.assertEqual(layout.events.count(('operator','blendcap.open_vta_workspace')),1)
        self.assertIn(('operator','blendcap.vta_camera_depth'),layout.events)
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
