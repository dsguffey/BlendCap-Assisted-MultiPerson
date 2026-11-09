"""Foreground callback completion and resource cleanup, without GUI event injection."""
import importlib,unittest
from types import SimpleNamespace as NS
from unittest import mock
from tests import test_blendcap_vta_ui_polish as fixtures
bpy=fixtures.bpy

@unittest.skipUnless(bpy is not None,'Blender required')
class DialogLifecycleTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  fixtures.UiPolishTests.setUpClass();cls.addon=fixtures.UiPolishTests.addon
 def setUp(self):
  self.ui=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_ui')
  self.busy=importlib.import_module(self.addon.__name__+'.blendcap.vta_busy')
  self.marks=importlib.import_module(self.addon.__name__+'.blendcap.vta_marks')
  self.context=NS(scene=NS(as_pointer=lambda:1,frame_current=125),window=mock.Mock(),window_manager=mock.Mock())
  self.addCleanup(setattr,self.busy,'_active',None);self.addCleanup(setattr,self.marks,'_busy',False)
 def test_repeat_unavailable_has_no_assignment_or_second_modal_handoff(self):
  cls=self.ui.BLENDCAP_OT_vta_repeat_assignment;self.busy.install(cls,defer=False)
  op=NS(report=mock.Mock())
  with mock.patch.object(self.busy,'bpy',NS(app=NS(background=False))),mock.patch.object(self.ui,'_edit_available',return_value=False),mock.patch.object(self.ui.BLENDCAP_OT_vta_review_assign,'execute') as apply:
   self.assertEqual(cls.execute(op,self.context),{'CANCELLED'});apply.assert_not_called()
   self.context.window_manager.modal_handler_add.assert_not_called();self.context.window_manager.event_timer_add.assert_not_called();self.assertFalse(self.busy.busy())
 def marked_op(self,kind='assign'):
  cls=self.marks.BLENDCAP_OT_vta_apply_marks
  class Op:
   execute=cls.execute;_apply=cls._apply;_finish=cls._finish
   kind='assign';confirmed=False
   report=mock.Mock()
  op=Op();op.kind=kind;return op
 def test_confirmed_mark_replacements_complete_without_second_modal_handler(self):
  for kind in ('assign','suppress'):
   with self.subTest(kind=kind):
    op=self.marked_op(kind);rows=({'frame':1,'observation_id':'o'},);checkpoint={'revision':1}
    backend=NS(observation=NS(value={'checkpoint':checkpoint}))
    with mock.patch.object(self.ui,'_analysis_locked',return_value=False),mock.patch.object(self.ui,'current_character_id',return_value='c'),mock.patch.object(self.ui,'_get_backend',return_value=backend),mock.patch.object(self.ui,'_timeline',NS(keys=(NS(source_frame=1,observation_id='o',character_id='c'),))),mock.patch.object(self.marks,'scope',return_value='scope'),mock.patch.object(self.marks.marks,'items',return_value=rows),mock.patch.object(self.marks,'apply_marked') as apply,mock.patch.object(self.ui.vta_invalidation,'request_redraw'):
     self.context.window_manager.invoke_props_dialog.return_value={'RUNNING_MODAL'}
     self.assertEqual(self.marks.BLENDCAP_OT_vta_apply_marks.invoke(op,self.context,None),{'RUNNING_MODAL'})
     apply.assert_not_called()
     self.assertEqual(op.execute(self.context),{'FINISHED'})
     apply.assert_called_once_with(self.context,'scope',kind,rows,'c' if kind=='assign' else None,True,checkpoint)
     self.context.window_manager.event_timer_add.assert_not_called();self.context.window_manager.modal_handler_add.assert_not_called();self.assertFalse(self.marks.busy())
 def test_mark_failure_and_escape_keep_marks_and_release_busy_state(self):
  for confirmed in (False,True):
   op=self.marked_op();op.confirmed=confirmed
   with mock.patch.object(self.ui,'_analysis_locked',return_value=False),mock.patch.object(self.ui,'current_character_id',return_value='c'),mock.patch.object(self.marks,'scope',return_value='scope'),mock.patch.object(self.marks.marks,'items',return_value=({'frame':1,'observation_id':'o'},)),mock.patch.object(self.marks,'apply_marked',side_effect=ValueError('stale checkpoint')) as apply,mock.patch.object(self.marks.marks,'handled') as handled,mock.patch.object(self.ui.vta_invalidation,'request_redraw'):
    if confirmed:self.assertEqual(op.execute(self.context),{'CANCELLED'})
    else:
     self.assertEqual(op.execute(self.context),{'RUNNING_MODAL'})
     self.assertEqual(self.marks.BLENDCAP_OT_vta_apply_marks.modal(op,self.context,NS(type='ESC')),{'CANCELLED'})
     apply.assert_not_called()
    handled.assert_not_called();self.assertFalse(self.marks.busy());self.assertIsNone(op._timer)
  # Cancelling a dialog before execute has no pending work to clean up.
  self.marks.BLENDCAP_OT_vta_apply_marks.cancel(self.marked_op(),self.context)
 def test_mark_timer_setup_failure_does_not_leave_busy(self):
  op=self.marked_op();self.context.window_manager.modal_handler_add.side_effect=RuntimeError('handler unavailable')
  with mock.patch.object(self.ui,'_analysis_locked',return_value=False),mock.patch.object(self.ui,'current_character_id',return_value='c'),mock.patch.object(self.marks,'scope',return_value='scope'),mock.patch.object(self.marks.marks,'items',return_value=({'frame':1},)),mock.patch.object(self.ui.vta_invalidation,'request_redraw'):
   self.assertEqual(op.execute(self.context),{'CANCELLED'});self.assertFalse(self.marks.busy());self.assertIsNone(op._timer)
   self.context.window_manager.event_timer_remove.assert_called_once()
 def test_deferred_cancel_load_and_setup_failure_release_resources(self):
  module=self.busy
  class Operation:
   def execute(self,context):raise AssertionError('must not mutate')
  module.install(Operation)
  fake=NS(app=NS(background=False),data=NS(filepath='snapshot.blend'))
  with mock.patch.object(module,'bpy',fake),mock.patch.object(self.ui.vta_invalidation,'request_redraw'):
   for action in ('cancel','load','changed_frame'):
    op=Operation();self.assertEqual(op.execute(self.context),{'RUNNING_MODAL'})
    if action=='cancel':op.cancel(self.context)
    elif action=='load':module.clear()
    else:
     self.context.scene.frame_current+=1
     self.assertEqual(op.modal(self.context,NS(type='TIMER')),{'CANCELLED'})
    self.assertFalse(module.busy());self.assertIsNone(op._vta_timer)
   self.context.window_manager.modal_handler_add.side_effect=RuntimeError('handler unavailable')
   with self.assertRaisesRegex(RuntimeError,'handler unavailable'):Operation().execute(self.context)
   self.assertFalse(module.busy())
 def retarget_op(self):
  module=importlib.import_module(self.addon.__name__+'.blendcap.vta_retarget')
  cls=module.BLENDCAP_OT_vta_retarget;steps=[]
  def run(context):steps.append('work');yield ('testing',1)
  op=NS(use_scene_selection=False,_run=run,_timer=None,_stream=None,report=mock.Mock())
  op._finish=lambda context:cls._finish(op,context)
  self.context.workspace=mock.Mock()
  self.addCleanup(setattr,module,'_running',None)
  return module,cls,op,steps
 def test_incremental_retarget_cancel_and_completion_release_timer(self):
  module,cls,op,steps=self.retarget_op()
  with mock.patch.object(module,'bpy',NS(app=NS(background=False))):
   self.assertEqual(cls.execute(op,self.context),{'RUNNING_MODAL'});self.assertFalse(steps)
   self.assertEqual(cls.modal(op,self.context,NS(type='ESC')),{'CANCELLED'});self.assertFalse(steps)
   self.assertIsNone(module._running);self.assertIsNone(op._timer)
   self.assertEqual(cls.execute(op,self.context),{'RUNNING_MODAL'})
   self.assertEqual(cls.modal(op,self.context,NS(type='TIMER')),{'FINISHED'})
   self.assertEqual(steps,['work']);self.assertIsNone(module._running)
 def test_incremental_retarget_setup_failure_releases_running_guard(self):
  module,cls,op,steps=self.retarget_op()
  self.context.window_manager.modal_handler_add.side_effect=RuntimeError('handler unavailable')
  with mock.patch.object(module,'bpy',NS(app=NS(background=False))):
   self.assertEqual(cls.execute(op,self.context),{'CANCELLED'})
   self.assertIsNone(module._running);self.assertIsNone(op._timer);self.assertFalse(steps)
   self.context.window_manager.event_timer_remove.assert_called_once()
