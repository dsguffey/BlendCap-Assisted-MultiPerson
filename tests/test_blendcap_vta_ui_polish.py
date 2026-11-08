import importlib,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock
try:import bpy
except ImportError:bpy=None

@unittest.skipUnless(bpy is not None,'Blender required')
class UiPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass();cls.addon=BlenderOutputTests.addon
        cls.export=importlib.import_module(cls.addon.__name__+'.blendcap.vta_export')
    def test_pending_recovery_is_exposed_even_without_a_review_view(self):
        ui=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_ui')
        recovery=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_undo')
        calls=[]
        class Layout:
            def box(self):return self
            def row(self):return self
            def label(self,**kw):pass
            def operator(self,name,**kw):calls.append(name);return NS()
        with mock.patch.object(recovery,'_pending',{'root':object()}), \
             mock.patch.object(recovery,'_error',''), \
             mock.patch.object(recovery,'_client') as client, \
             mock.patch.object(ui,'_view',None), \
             mock.patch.object(ui,'refresh',return_value=False), \
             mock.patch.object(ui,'draw_scene_timing'), \
             mock.patch.object(ui,'_source_actions_available',return_value=False):
            for panel in (ui.VIEW3D_PT_blendcap_vta_current_frame,ui.BLENDCAP_MT_vta_commands):
                calls.clear();panel.draw(NS(layout=Layout()),bpy.context)
                self.assertEqual(calls[0],'blendcap.vta_reconcile')
            client.assert_not_called()
            with self.assertRaisesRegex(RuntimeError,'pending'):recovery.require_ready()
            # Recovery itself must remain executable while domain edits are blocked.
            with mock.patch.object(recovery,'retry') as retry:
                self.assertEqual(recovery.BLENDCAP_OT_vta_reconcile.execute(NS(report=mock.Mock()),bpy.context),{'FINISHED'})
                retry.assert_called_once_with(bpy.context)
    def test_pending_recovery_disables_batch_submission_without_clearing_marks(self):
        ui=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_ui')
        recovery=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_undo')
        marks=importlib.import_module(self.addon.__name__+'.blendcap.vta_marks')
        rows=[]
        class Layout:
            enabled=True
            def row(self):return Layout()
            def label(self,**kw):pass
            def operator(self,name,**kw):rows.append((name,kw.get('text'),self.enabled));return NS()
        items=[dict(frame=1,observation_id='a'),dict(frame=2,observation_id='b')]
        with mock.patch.object(recovery,'_pending',{'root':object()}), \
             mock.patch.object(marks,'scope',return_value='scope'), \
             mock.patch.object(marks.marks,'items',return_value=items), \
             mock.patch.object(marks.marks,'handled') as handled, \
             mock.patch.object(ui,'_view',NS(source_frame=0,candidates=())), \
             mock.patch.object(ui,'_selection',NS(observation_id='a')), \
             mock.patch.object(ui,'_analysis_locked',return_value=False), \
             mock.patch.object(ui,'current_character_id',return_value='white'):
            marks.draw(Layout(),bpy.context)
            submissions=[row for row in rows if row[0]=='blendcap.vta_apply_marks']
            self.assertEqual(len(submissions),2)
            self.assertTrue(all(not enabled for _,_,enabled in submissions))
            self.assertIn('Assign Marked (2)',[text for _,text,_ in submissions])
            handled.assert_not_called()
    def test_resolved_recovery_draw_is_inert(self):
        recovery=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_undo')
        layout=mock.Mock()
        with mock.patch.object(recovery,'_pending',{}),mock.patch.object(recovery,'_error',''), \
             mock.patch.object(recovery,'_presentation_warning',''):
            self.assertFalse(recovery.blocked());recovery.require_ready();recovery.draw_pending(layout)
            self.assertEqual(layout.mock_calls,[])
    def test_preferences_expose_only_persistent_choice_and_diagnostics(self):
        calls=[]
        class Layout:
            def label(self,**kw):calls.append(('label',kw))
            def prop(self,data,name,**kw):calls.append(('prop',name))
            def box(self):return self
        self.addon.BLENDCAP_AddonPreferences.draw(NS(layout=Layout()),bpy.context)
        self.assertEqual([v for k,v in calls if k=='prop'],['hide_setup_section'])
        self.assertFalse(any('BlendCap' in str(v) or 'Install Dependencies' in str(v) for _,v in calls))
        operators=importlib.import_module(self.addon.__name__+'.blendcap.operators_pipeline')
        show=mock.Mock();workspace=mock.Mock(return_value={'FINISHED'})
        fake=NS(ops=NS(preferences=NS(addon_show=show),blendcap=NS(open_vta_workspace=workspace)))
        with mock.patch.object(operators,'bpy',fake):
            self.assertEqual(operators.BLENDCAP_OT_install_open_prefs.execute(NS(),bpy.context),{'FINISHED'})
            self.assertEqual(operators.BLENDCAP_OT_append_workspace.execute(NS(),bpy.context),{'FINISHED'})
        show.assert_called_once_with(module=self.addon.__package__);workspace.assert_called_once_with()
        names={cls.__name__ for cls in self.addon._registration.classes}
        self.assertNotIn('BLENDCAP_OT_import_lib_bvh',names)
        self.assertNotIn('BLENDCAP_OT_generate_bvh',names)
    def test_foreground_edit_redraws_before_io_and_prevents_duplicate_submit(self):
        module=importlib.import_module(self.addon.__name__+'.blendcap.vta_busy')
        ui=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_ui')
        calls=[]
        class Operation:
            def execute(self,context):calls.append('mutation');return {'FINISHED'}
        module.install(Operation)
        fake=NS(app=NS(background=False),data=NS(filepath='test.blend'))
        context=NS(scene=NS(as_pointer=lambda:1,frame_current=5),window=mock.Mock(),window_manager=mock.Mock())
        with mock.patch.object(module,'bpy',fake),mock.patch.object(ui.vta_invalidation,'request_redraw') as redraw:
            a=Operation();self.assertEqual(a.execute(context),{'RUNNING_MODAL'})
            self.assertFalse(calls);redraw.assert_called_once()
            self.assertEqual(Operation().execute(context),{'CANCELLED'})
            self.assertEqual(a.modal(context,NS(type='TIMER')),{'FINISHED'})
            self.assertEqual(calls,['mutation']);self.assertFalse(module.busy())
            a=Operation();a.execute(context)
            self.assertEqual(a.modal(context,NS(type='ESC')),{'CANCELLED'})
            self.assertEqual(calls,['mutation'])
    def test_confirmed_edit_finishes_without_a_second_modal_handoff(self):
        module=importlib.import_module(self.addon.__name__+'.blendcap.vta_busy')
        ui=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_ui')
        # Exercise the installed management wrapper, including its actual body,
        # as a props-dialog execute callback with no subsequent modal dispatch.
        module.install(ui.BLENDCAP_OT_vta_review_manage,defer=False)
        context=NS(window=mock.Mock(),window_manager=mock.Mock())
        request=NS(character_id=None,keyframe_ids=('key-a','key-b'))
        operator=NS(action='clear-selected-review-keys',_pending_request=request,
                    _pending_location='location',report=mock.Mock())
        backend=mock.Mock()
        with mock.patch.object(module,'bpy',NS(app=NS(background=False))), \
             mock.patch.object(ui,'_edit_available',return_value=True), \
             mock.patch.object(ui,'refresh',return_value=True), \
             mock.patch.object(ui,'_use_timeline_active'), \
             mock.patch.object(ui,'_location','location'), \
             mock.patch.object(ui,'_review_key_selection',NS(keyframe_ids=request.keyframe_ids)), \
             mock.patch.object(ui,'_get_backend',return_value=backend):
            try:
                self.assertEqual(ui.BLENDCAP_OT_vta_review_manage.execute(operator,context),{'FINISHED'})
                backend.apply_management.assert_called_once_with('location',request,confirmed=True)
                context.window_manager.event_timer_add.assert_not_called()
                context.window_manager.modal_handler_add.assert_not_called()
                context.window.cursor_modal_restore.assert_called_once()
                self.assertFalse(module.busy())
            finally:module._active=None

    def test_confirmed_edit_restores_cursor_on_failure_and_keeps_duplicate_guard(self):
        module=importlib.import_module(self.addon.__name__+'.blendcap.vta_busy')
        class Operation:
            def execute(self,context):raise ValueError('test failure')
        module.install(Operation,defer=False)
        context=NS(window=mock.Mock(),window_manager=mock.Mock())
        with mock.patch.object(module,'bpy',NS(app=NS(background=False))):
            with self.assertRaisesRegex(ValueError,'test failure'):Operation().execute(context)
            context.window.cursor_modal_restore.assert_called_once()
            with mock.patch.object(module,'_active',object()):
                self.assertEqual(Operation().execute(context),{'CANCELLED'})
            context.window_manager.event_timer_add.assert_not_called()
            self.assertFalse(module.busy())

    def test_generation_progress_uses_cached_counts_without_engine_calls(self):
        module=importlib.import_module(self.addon.__name__+'.blendcap.vta_jobs_ui');calls=[]
        class Layout:
            def label(self,**kw):calls.append(kw)
            def operator(self,*a,**kw):calls.append(dict(operator=a[0],**kw));return NS()
            def progress(self,**kw):calls.append(kw)
            def row(self,**kw):return self
        job=dict(scene=bpy.context.scene,pointer=dict(kind='analysis'),phase='waiting',status=dict(progress=dict(completed=3,total=10)))
        with mock.patch.object(module,'_active',job),mock.patch.object(module,'_messages',{}):
            module.draw(Layout(),bpy.context,'analysis')
        self.assertEqual(next(row for row in calls if 'factor' in row)['factor'],.3)
        key=module._key(bpy.context.scene,'analysis');calls.clear()
        with mock.patch.object(module,'_active',None),mock.patch.object(module,'_messages',{}),mock.patch.object(module,'_saved',{key:dict(settled=True)}):
            module.draw(Layout(),bpy.context,'analysis')
        self.assertFalse(any(row.get('operator')=='blendcap.vta_job_control' for row in calls))
    def test_suppression_preserves_source_alignment_guard_for_assigned_characters(self):
        module=importlib.import_module(self.addon.__name__+'.blendcap.vta_marks')
        ui=importlib.import_module(self.addon.__name__+'.blendcap.vta_review_ui')
        camera=importlib.import_module(self.addon.__name__+'.blendcap.vta_camera_cleanup')
        observed=NS(value=dict(checkpoint={'revision':2}),timeline=NS(keys=(NS(source_frame=5,observation_id='a',character_id='white'),)))
        payload=dict(analysis_revision_id='r',cache_sha256='a'*64,source_frame=5)
        backend=NS(_cache_payload=lambda location:(observed,dict(payload)),_send=mock.Mock())
        rows=(dict(frame=5,observation_id='a',analysis_revision_id='r',cache_sha256='a'*64),)
        with mock.patch.object(module,'scope',return_value='key'),mock.patch.object(ui,'_get_backend',return_value=backend),mock.patch.object(camera,'require_source_aligned',side_effect=ValueError('Restore camera first')) as guard:
            with self.assertRaisesRegex(ValueError,'Restore camera'):module.apply_marked(bpy.context,'key','suppress',rows,None)
            guard.assert_called_once_with(bpy.context.scene,('white',));backend._send.assert_not_called()
            with self.assertRaisesRegex(ValueError,'Review changed'):module.apply_marked(bpy.context,'key','assign',rows,'white',True,{'revision':1})
            backend._send.assert_not_called()
    def test_export_bakes_native_object_depth_and_preserves_source(self):
        from mathutils import Vector
        scene=bpy.context.scene;oldframe=scene.frame_current
        active=bpy.context.view_layer.objects.active;selected=tuple(bpy.context.selected_objects)
        objects=set(bpy.data.objects);actions=set(bpy.data.actions);armatures=set(bpy.data.armatures)
        try:
            data=bpy.data.armatures.new('export-regression');obj=bpy.data.objects.new('export-regression',data)
            scene.collection.objects.link(obj);bpy.context.view_layer.objects.active=obj;obj.select_set(True)
            bpy.ops.object.mode_set(mode='EDIT')
            root=data.edit_bones.new('Root');root.head=(0,0,0);root.tail=(0,0,.2)
            child=data.edit_bones.new('Hips');child.head=(0,0,1);child.tail=(0,0,1.4);child.parent=root
            bpy.ops.object.mode_set(mode='OBJECT');obj['vta_character_id']='export-test'
            obj.scale=(2,2,2)
            for number,depth in [(1,0),(2,1)]:
                obj.location=(0,depth,0);obj.keyframe_insert('location',frame=number)
                obj.pose.bones['Root'].location.x=.1*number;obj.pose.bones['Root'].keyframe_insert('location',frame=number)
            action=obj.animation_data.action
            expected={}
            for number in (1,2):
                scene.frame_set(number);expected[number]=[obj.matrix_world @ p.head for p in obj.pose.bones]
            scene.frame_set(1);before=set(bpy.data.objects)
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'edited.bvh'
                with self.assertRaisesRegex(ValueError,'.bvh export'):self.export.export_current(bpy.context,obj,path.with_suffix('.blend'),1,2)
                if bpy.data.filepath:
                    from blendcap_vta_review import companion_root_for_blend
                    protected=companion_root_for_blend(bpy.data.filepath)/'protected.bvh'
                    with self.assertRaisesRegex(ValueError,'companion folder'):self.export.export_current(bpy.context,obj,protected,1,2)
                self.export.export_current(bpy.context,obj,path,1,2)
                self.assertEqual(set(bpy.data.objects),before);self.assertIs(obj.animation_data.action,action)
                previous=path.read_bytes()
                with mock.patch('io_anim_bvh.export_bvh.write_armature',side_effect=RuntimeError('writer failed')),mock.patch.object(Path,'unlink',side_effect=PermissionError('scratch locked')):
                    with self.assertRaisesRegex(RuntimeError,'writer failed'):
                        self.export.export_current(bpy.context,obj,path,1,2)
                self.assertEqual(path.read_bytes(),previous)
                self.assertEqual(set(bpy.data.objects),before);self.assertIs(obj.animation_data.action,action)
                bpy.ops.import_anim.bvh(filepath=str(path),axis_forward='-Z',axis_up='Y',use_fps_scale=False,update_scene_fps=False,update_scene_duration=False)
                imported=bpy.context.active_object
                for number in (1,2):
                    scene.frame_set(number)
                    for p,wanted in zip(imported.pose.bones,expected[number]):
                        self.assertLess((imported.matrix_world @ p.head-wanted).length,2e-4)
        finally:
            if bpy.context.object and bpy.context.object.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
            for obj in set(bpy.data.objects)-objects:bpy.data.objects.remove(obj,do_unlink=True)
            for action in set(bpy.data.actions)-actions:
                if action.users==0:bpy.data.actions.remove(action)
            for data in set(bpy.data.armatures)-armatures:
                if data.users==0:bpy.data.armatures.remove(data)
            bpy.context.view_layer.objects.active=active
            for obj in selected:obj.select_set(True)
            scene.frame_set(oldframe)
