"""Independent UI management selection, existing mutation routes and mark-only Repeat."""
import importlib,unittest
from types import SimpleNamespace as NS,MethodType
from pathlib import Path
from unittest import mock
from contextlib import ExitStack
from dataclasses import replace
from blendcap_vta_review import SelectionState,ReviewLocation,CharacterRow
try:import bpy
except ImportError:bpy=None

@unittest.skipUnless(bpy is not None,'Blender required')
class UiPass4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_ui_pass2 import UiPass2Tests
        UiPass2Tests.setUpClass();cls.addon=UiPass2Tests.addon
    def module(self,name):return importlib.import_module(self.addon.__name__+'.blendcap.'+name)
    def setUp(self):
        from tests.test_blendcap_vta_repeat_assignment import view
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.ui=self.module('vta_review_ui');self.management=self.module('vta_management_selection')
        selection=SelectionState();selection.character_id='white';selection.observation_id='obs-0'
        for name,value in [('_view',view()),('_location',ReviewLocation(Path('test_vta'),Path('video.mp4'),0,1)),('_presentation_scene',None),('_selection',selection),('_character_enum_items',[('none','None',''),('white','White',''),('yellow','Yellow','')])]:self.stack.enter_context(mock.patch.object(self.ui,name,value))
        self.stack.enter_context(mock.patch.object(self.management,'_targets',{}))
        self.stack.enter_context(mock.patch.object(self.ui.vta_invalidation,'request_redraw'))
    def test_management_selection_does_not_touch_other_workflow_state(self):
        before=(self.ui._selection.character_id,self.ui._selection.observation_id,tuple(bpy.context.selected_objects),dict(self.ui._character_selections))
        self.management.set_index(None,2)
        self.assertEqual(self.management.selected(),'yellow')
        self.assertEqual(before,(self.ui._selection.character_id,self.ui._selection.observation_id,tuple(bpy.context.selected_objects),dict(self.ui._character_selections)))
        self.ui._selection.character_id='white';self.assertEqual(self.management.selected(),'yellow')
        self.assertEqual(self.management.get_index(None),2)
        self.ui._view=replace(self.ui._view,characters=(CharacterRow('white','White',None),))
        self.assertIsNone(self.management.selected())
        self.assertEqual(self.management.get_index(None),0)
    def test_management_selection_is_source_scoped_and_session_only(self):
        self.management.select('yellow');original=self.ui._location
        self.ui._location=replace(original,companion_root=Path('other_vta'))
        self.assertIsNone(self.management.selected())
        self.ui._location=original;self.assertEqual(self.management.selected(),'yellow')
        self.management.clear();self.assertIsNone(self.management.selected())
    def operation(self,action):
        cls=self.ui.BLENDCAP_OT_vta_review_manage;op=NS(action=action,new_name='Renamed',report=mock.Mock())
        for method in ('_target','_arguments'):setattr(op,method,MethodType(getattr(cls,method),op))
        return op
    def test_lifecycle_request_targets_management_without_replacing_assignment(self):
        self.management.select('yellow')
        for action in ('rename-character','delete-character'):
            op=self.operation(action);self.assertEqual(op._arguments()['character_id'],'yellow')
        op=self.operation('clear-assignment');self.assertEqual(op._arguments()['character_id'],'white')
        self.assertEqual(self.ui._selection.observation_id,'obs-0')
    def test_rename_uses_existing_bridge_and_checks_confirmation_target(self):
        self.management.select('yellow');op=self.operation('rename-character')
        op._pending_request={'character_id':'yellow'};op._pending_location=self.ui._location
        with mock.patch.object(self.ui,'_edit_available',return_value=True),mock.patch.object(self.ui,'refresh',return_value=True),mock.patch.object(self.module('vta_review_undo'),'rename') as rename:
            result=self.ui.BLENDCAP_OT_vta_review_manage.execute(op,bpy.context)
            self.assertEqual(result,{'FINISHED'});self.assertEqual(rename.call_args.args[2]['character_id'],'yellow')
            rename.reset_mock();self.management.select('white')
            self.assertEqual(self.ui.BLENDCAP_OT_vta_review_manage.execute(op,bpy.context),{'CANCELLED'});rename.assert_not_called()
        self.assertEqual(self.ui._selection.character_id,'white')
    def test_delete_uses_existing_backend_and_rejects_changed_management_selection(self):
        self.management.select('yellow');op=self.operation('delete-character');op._pending_request=NS(character_id='yellow');op._pending_location=self.ui._location;backend=mock.Mock()
        with mock.patch.object(self.ui,'_edit_available',return_value=True),mock.patch.object(self.ui,'refresh',return_value=True),mock.patch.object(self.ui,'_get_backend',return_value=backend):
            self.assertEqual(self.ui.BLENDCAP_OT_vta_review_manage.execute(op,bpy.context),{'FINISHED'})
            backend.apply_management.assert_called_once_with(self.ui._location,op._pending_request,confirmed=True)
            backend.reset_mock();self.management.select('white')
            self.assertEqual(self.ui.BLENDCAP_OT_vta_review_manage.execute(op,bpy.context),{'CANCELLED'});backend.apply_management.assert_not_called()
    def test_create_selects_management_only(self):
        from tests.test_blendcap_vta_repeat_assignment import view
        new=CharacterRow('new','New',None);refreshed=replace(view(),characters=(*view().characters,new));backend=mock.Mock();backend.create_character.return_value=(new,refreshed)
        context=NS(scene=NS(),window_manager=NS(blendcap_vta_new_character_name='New'))
        with mock.patch.object(self.ui,'_edit_available',return_value=True),mock.patch.object(self.ui,'refresh',return_value=True),mock.patch.object(self.ui,'_get_backend',return_value=backend),mock.patch.object(self.ui,'_accept_mutated_review'):
            self.assertEqual(self.ui.BLENDCAP_OT_vta_review_create_character.execute(NS(report=mock.Mock()),context),{'FINISHED'})
        self.assertEqual(self.management.selected(),'new');self.assertEqual(self.ui._selection.character_id,'white');self.assertEqual(self.ui._selection.observation_id,'obs-0')
    def test_assignment_dropdown_adjacent_to_candidate_and_no_repeat_button(self):
        from tests.test_blendcap_vta_ui_pass2 import Layout
        events=[]
        class Draw(Layout):
            def prop(self,data,name,**kw):events.append(name)
        with mock.patch.object(self.ui,'_analysis_locked',return_value=False),mock.patch.object(self.ui,'_action_availability',return_value={name:False for name,_,_ in self.ui._MANAGEMENT_ACTIONS}):
            layout=Draw();self.ui.VIEW3D_PT_blendcap_vta_current_frame.draw(NS(layout=layout),bpy.context)
        index=events.index('blendcap_vta_character');self.assertEqual(events[index+1],'blendcap_vta_candidate')
        self.assertNotIn('blendcap_vta_new_character_name',events)
        self.assertNotIn(('operator','blendcap.vta_repeat_assignment'),layout.events)
    def test_sidebar_normal_rna_failure_does_not_break_creation(self):
        workspace=self.module('vta_workspace')
        class Region:
            type='UI'
            @property
            def active_panel_category(self):return ''
            @active_panel_category.setter
            def active_panel_category(self,v):raise RuntimeError('not initialized yet')
        space=NS(show_region_ui=False);created=NS(screens=[NS(areas=[NS(type='VIEW_3D',spaces=NS(active=space),regions=[Region()])])]);context=NS(window=NS(workspace=None))
        with mock.patch.object(workspace,'create_vta_workspace',return_value=created):
            result=workspace.BLENDCAP_OT_open_vta_workspace.execute(NS(report=mock.Mock()),context)
        self.assertEqual(result,{'FINISHED'});self.assertIs(context.window.workspace,created);self.assertTrue(space.show_region_ui)

    def test_sidebar_setup_exception_never_cancels_new_workspace(self):
        workspace=self.module('vta_workspace');created=object();context=NS(window=NS(workspace=None))
        with mock.patch.object(workspace,'create_vta_workspace',return_value=created),mock.patch.object(workspace,'_open_workspace_sidebar',side_effect=RuntimeError('sidebar not ready')):
            self.assertEqual(workspace.BLENDCAP_OT_open_vta_workspace.execute(NS(report=mock.Mock()),context),{'FINISHED'})
        self.assertIs(context.window.workspace,created)
