"""Explicit Scene transactions and passive refreshes."""
import ast
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock
from tests import test_blendcap_vta_scene_timing as timing_tests
bpy = timing_tests.bpy

@unittest.skipUnless(bpy is not None, 'Blender runtime required')
class SceneBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        timing_tests.SceneTimingTests.setUpClass.__func__(cls)
        cls.session = importlib.import_module(cls.ui.__package__+'.vta_scene_session')
        cls.ws = importlib.import_module(cls.ui.__package__+'.vta_workspace')

    cleanup = timing_tests.SceneTimingTests.cleanup
    prepare = timing_tests.SceneTimingTests.prepare
    owned = timing_tests.SceneTimingTests.owned

    def setUp(self):
        from _bpy_restrict_state import RestrictBlend
        timing_tests.SceneTimingTests.setUp(self)
        self.addon = sys.modules[self.ui.__package__.rsplit('.',1)[0]]
        with RestrictBlend(): self.addon.register()
        self.addCleanup(self.addon.unregister)
        self.workspace = bpy.context.workspace
        self.screen = bpy.context.screen
        self.pin = self.workspace.sequencer_scene
        self.tag = self.workspace.get('blendcap_vta_review_workspace')
        self.raw = self.screen.get('blendcap_vta_editor_modes_v1')
        self.workspace['blendcap_vta_review_workspace'] = 1
        self.preview = next(a for a in self.screen.areas if a.type=='VIEW_3D')
        self.preview.type = 'SEQUENCE_EDITOR'
        self.preview.spaces.active.view_type = 'SEQUENCER_PREVIEW'
        self.viewport = next(a for a in self.screen.areas if a.type=='PROPERTIES')
        self.viewport.type = 'VIEW_3D'
        self.timeline = next(a for a in self.screen.areas if a.type=='DOPESHEET_EDITOR')
        self.timeline_ui = self.timeline.ui_type
        self.timeline.ui_type = 'DOPESHEET'
        self.screen.pop('blendcap_vta_editor_modes_v1',None)
        self.addCleanup(self.restore_layout)
        from blendcap_vta_review import ReviewLocation, ReviewView
        from blendcap_vta_timeline import TimelineView
        self.backend = SimpleNamespace(
            _source_hash=lambda _: 'test-hash',
            _core=SimpleNamespace(load_project_state=lambda _: SimpleNamespace(to_dict=lambda:
                {'active_analysis_revision_by_source':{'s':'analysis'}})),
            query=lambda loc, **kw: ReviewView('analysis','s',loc.source_frame,16,16,
                'analyzed-with-candidates','Candidates',(),()),
            query_timeline=lambda _: TimelineView('s',(),()))
        def location(context, **kw):
            return ReviewLocation(self.fixture.root,self.path,context.scene.frame_current-1,
                                  context.scene.frame_current)
        def timing(context):
            return self.fixture.pid,self.source,location(context),context.scene.sequence_editor.active_strip
        patch=mock.patch.object(self.ui,'_get_backend',return_value=self.backend)
        patch.start();self.addCleanup(patch.stop)
        for field, fn in (('_location_from_context',location),('_timing_source',timing)):
            patch=mock.patch.object(self.ui,field,side_effect=fn);patch.start();self.addCleanup(patch.stop)

    def restore_layout(self):
        self.workspace.sequencer_scene = self.pin
        self.workspace.blendcap_vta_loaded_scene = None
        self.workspace.blendcap_vta_mode_enabled = False
        self.preview.type = 'VIEW_3D'
        self.viewport.type = 'PROPERTIES'
        self.timeline.ui_type = self.timeline_ui
        if self.raw is None: self.screen.pop('blendcap_vta_editor_modes_v1',None)
        else: self.screen['blendcap_vta_editor_modes_v1']=self.raw
        if self.tag is None: self.workspace.pop('blendcap_vta_review_workspace',None)
        else: self.workspace['blendcap_vta_review_workspace']=self.tag

    def tick(self):
        for _ in range(3):
            self.ui._scene_observer_timer()
            self.ui._presentation_timer()
            self.ui._analysis_timer()
            self.ui._reconstruction_timer()
            self.ui.refresh(bpy.context)

    def verify_loaded(self, scene):
        from blendcap_vta_area_mode import role_for,ROLE_PREVIEW,ROLE_TIMELINE
        self.assertIs(bpy.context.window.scene,scene)
        self.assertIs(self.workspace.blendcap_vta_loaded_scene,scene)
        self.assertIs(self.workspace.sequencer_scene,scene)
        self.assertTrue(self.session.matches(bpy.context))
        self.assertEqual(role_for(self.workspace,self.screen,self.preview),ROLE_PREVIEW)
        self.assertEqual(role_for(self.workspace,self.screen,self.timeline),ROLE_TIMELINE)
        self.assertIs(self.viewport.spaces.active.camera,scene.camera)
        self.assertEqual(self.viewport.spaces.active.region_3d.view_perspective,'CAMERA')
        self.assertIs(self.ui._presentation_scene,scene)
        self.assertIsNotNone(self.ui.overlay_state()[0])
        self.assertIsNotNone(self.ui.timeline_state()[0])
        outliner=next(a for a in self.screen.areas if a.type=='OUTLINER')
        with bpy.context.temp_override(area=outliner):
            self.assertEqual(set(bpy.context.view_layer.objects),set(scene.objects))

    def display_backend(self):
        """Counting read-only snapshot boundary, same adapter API as production."""
        from blendcap_vta_timeline import TimelineView
        project=SimpleNamespace(to_dict=lambda:{'sources':[dict(self.source)],
            'active_analysis_revision_by_source':{'s':'analysis'}})
        self.backend._core.load_project_state=mock.Mock(return_value=project)
        self.backend._core.load_analysis_cache=mock.Mock(return_value=object())
        self.backend.frame_from_context=mock.Mock(side_effect=lambda ctx,loc,**kw:self.backend.query(loc,**kw))
        self.backend.timeline_from_context=mock.Mock(return_value=TimelineView('s',(),()))
        return self.backend

    def test_unchanged_observer_ticks_have_zero_model_reloads(self):
        backend=self.display_backend();a=self.owned();self.session.load(bpy.context,a)
        self.ui._scene_observer_timer()
        backend._core.load_project_state.reset_mock();backend._core.load_analysis_cache.reset_mock()
        backend.frame_from_context.reset_mock();backend.timeline_from_context.reset_mock()
        for _ in range(40): self.ui._scene_observer_timer()
        backend._core.load_project_state.assert_not_called()
        backend._core.load_analysis_cache.assert_not_called()
        backend.frame_from_context.assert_not_called()
        backend.timeline_from_context.assert_not_called()

    def test_source_fingerprint_never_reads_media_derived_fps(self):
        presentation=importlib.import_module(self.ui.__package__+'.vta_presentation')
        class Strip(dict):
            type='MOVIE';filepath='source.mp4';retiming_keys=();modifiers=()
            @property
            def fps(self):raise AssertionError('expensive media getter in fingerprint')
        scene=SimpleNamespace(sequence_editor=SimpleNamespace(active_strip=Strip()),
            blendcap_props=SimpleNamespace(video_source='VSE'),get=lambda key:None)
        for _ in range(20):self.assertIsNotNone(presentation.source_identity(scene,'saved.blend'))

    def test_warm_ui_polls_draws_do_not_reload_or_hash(self):
        backend=self.display_backend();a=self.owned();self.session.load(bpy.context,a)
        backend._core.load_project_state.reset_mock();backend._core.load_analysis_cache.reset_mock()
        class Layout:
            def operator(self,*args,**kwargs): return SimpleNamespace()
            def __getattr__(self,_): return lambda *args,**kwargs:self
        with mock.patch.object(self.ui,'_timing_source',side_effect=AssertionError('validation in UI')), \
             mock.patch.object(backend,'_source_hash',side_effect=AssertionError('hash in UI')):
            for _ in range(30):
                self.assertTrue(self.ui._source_actions_available(bpy.context))
                self.assertTrue(self.ui.BLENDCAP_OT_vta_new_scene.poll(bpy.context))
                self.assertTrue(self.ui.BLENDCAP_OT_vta_match_source_fps.poll(bpy.context))
                self.ui.draw_scene_timing(Layout(),bpy.context)
                self.ui.draw_scene_workflow(Layout(),bpy.context)
                self.ui.VIEW3D_PT_blendcap_vta_review.draw(SimpleNamespace(layout=Layout()),bpy.context)
        backend._core.load_project_state.assert_not_called()
        backend._core.load_analysis_cache.assert_not_called()

    def test_rapid_frame_changes_publish_correct_overlay_before_timer_without_io(self):
        backend=self.display_backend();a=self.owned();self.session.load(bpy.context,a)
        backend._core.load_project_state.reset_mock();backend._core.load_analysis_cache.reset_mock()
        backend.frame_from_context.reset_mock()
        with mock.patch.object(self.ui,'_timing_source',side_effect=AssertionError('validation in frame handler')), \
             mock.patch.object(backend,'_source_hash',side_effect=AssertionError('hash in frame handler')):
            for frame in (1,2,3,4)*20:
                a.frame_set(frame)
                view=self.ui.overlay_state()[0]
                self.assertIsNotNone(view)
                self.assertEqual(view.source_frame,frame-1)
                self.assertIs(bpy.context.window.scene,a)
                self.assertIs(self.workspace.sequencer_scene,a)
        self.assertLessEqual(backend.frame_from_context.call_count,4)
        backend._core.load_project_state.assert_not_called()
        backend._core.load_analysis_cache.assert_not_called()

    def test_exit_resets_only_managed_native_suppression_filters(self):
        a=self.owned();self.session.load(bpy.context,a)
        filters=self.timeline.spaces.active.dopesheet
        filters.show_only_errors=True;filters.show_only_selected=True;filters.show_summary=False
        other=next(area for screen in bpy.data.workspaces['Animation'].screens for area in screen.areas
                   if area.type=='DOPESHEET_EDITOR').spaces.active.dopesheet
        before=other.show_only_errors,other.show_only_selected,other.show_summary
        frames=(a.frame_current,a.frame_start,a.frame_end,a.use_preview_range,a.frame_preview_start,a.frame_preview_end)
        self.session.exit_mode(bpy.context)
        self.assertEqual((filters.show_only_errors,filters.show_only_selected,filters.show_summary),(False,False,True))
        self.assertEqual(before,(other.show_only_errors,other.show_only_selected,other.show_summary))
        self.assertEqual(frames,(a.frame_current,a.frame_start,a.frame_end,a.use_preview_range,a.frame_preview_start,a.frame_preview_end))

    def test_multiple_valid_scenes_open_picker_even_when_active_valid(self):
        a=self.owned();self.session.load(bpy.context,a)
        b=self.timing.prepare_scene(a,self.fixture.pid,self.source,self.path,a.sequence_editor.active_strip,new=True)
        self.session.load(bpy.context,b)
        popup=mock.Mock(return_value={'RUNNING_MODAL'});execute=mock.Mock(return_value={'FINISHED'})
        ctx=self.session._Proxy(bpy.context,window_manager=SimpleNamespace(invoke_props_dialog=popup))
        operator=SimpleNamespace(report=mock.Mock(),execute=execute,scene_name='')
        self.assertEqual(self.ui.BLENDCAP_OT_vta_load_scene.invoke(operator,ctx,None),{'RUNNING_MODAL'})
        self.assertEqual(operator.scene_name,b.name)
        popup.assert_called_once_with(operator);execute.assert_not_called()
        with mock.patch.object(self.session,'validate',side_effect=AssertionError('validation during enum redraw')):
            for _ in range(30):
                self.assertEqual({x[0] for x in self.ui.load_scene_items(None,ctx)},{a.name,b.name})

    def test_single_valid_scene_retains_direct_load(self):
        a=self.owned();self.session.load(bpy.context,a)
        popup=mock.Mock();execute=mock.Mock(return_value={'FINISHED'})
        ctx=self.session._Proxy(bpy.context,window_manager=SimpleNamespace(invoke_props_dialog=popup))
        operator=SimpleNamespace(report=mock.Mock(),execute=execute,scene_name='')
        self.assertEqual(self.ui.BLENDCAP_OT_vta_load_scene.invoke(operator,ctx,None),{'FINISHED'})
        self.assertEqual(operator.scene_name,a.name)
        execute.assert_called_once_with(ctx);popup.assert_not_called()

    def test_new_creates_and_loads_once_then_idle_does_not_refresh_recursively(self):
        backend=self.display_backend();a=self.owned();self.session.load(bpy.context,a)
        backend._core.load_project_state.reset_mock()
        with mock.patch.object(self.session,'create_blank',wraps=self.session.create_blank) as create, \
             mock.patch.object(self.session,'load',wraps=self.session.load) as load:
            self.assertEqual(bpy.ops.blendcap.vta_new_scene(),{'FINISHED'})
            create.assert_called_once();load.assert_called_once()
            self.assertLessEqual(backend._core.load_project_state.call_count,3)
            backend._core.load_project_state.reset_mock();backend._core.load_analysis_cache.reset_mock()
            for _ in range(30):self.ui._scene_observer_timer()
            backend._core.load_project_state.assert_not_called()
            backend._core.load_analysis_cache.assert_not_called()

    def test_changed_evidence_file_invalidates_snapshot_once_not_on_draw(self):
        backend=self.display_backend();a=self.owned();self.session.load(bpy.context,a)
        self.ui._scene_observer_timer()
        path=self.fixture.root/'analysis'/'analysis.json';path.parent.mkdir(exist_ok=True)
        backend._core.load_project_state.reset_mock();backend._core.load_analysis_cache.reset_mock()
        path.write_text('changed derived evidence')
        self.ui._scene_observer_timer()
        self.assertEqual(backend._core.load_project_state.call_count,1)
        self.assertEqual(backend._core.load_analysis_cache.call_count,1)

        for _ in range(20):self.ui._scene_observer_timer()
        self.assertEqual(backend._core.load_project_state.call_count,1)
        self.assertEqual(backend._core.load_analysis_cache.call_count,1)

    def test_changed_project_or_video_invalidates_cached_presentation(self):
        backend=self.display_backend();a=self.owned();self.session.load(bpy.context,a)
        self.ui._scene_observer_timer()
        for path in (self.fixture.root/'project_state.json',self.path):
            before=backend._core.load_project_state.call_count
            # Test adapters supply canonical models; changing file signatures
            # must still cause a revalidation/refresh before display reuse.
            path.write_bytes(path.read_bytes()+b' ')
            self.ui._scene_observer_timer()
            self.assertGreater(backend._core.load_project_state.call_count,before)
            warm=backend._core.load_project_state.call_count
            for _ in range(10):self.ui._scene_observer_timer()
            self.assertEqual(backend._core.load_project_state.call_count,warm)

    def test_explicit_force_refresh_bypasses_display_cache(self):
        backend=self.display_backend();a=self.owned();self.session.load(bpy.context,a)
        backend._core.load_project_state.reset_mock();backend._core.load_analysis_cache.reset_mock()
        self.assertTrue(self.ui.refresh(bpy.context,force=True))
        self.assertEqual(backend._core.load_project_state.call_count,1)
        self.assertEqual(backend._core.load_analysis_cache.call_count,1)

    def test_failed_source_revalidation_disables_cached_actions(self):
        self.display_backend();a=self.owned();self.session.load(bpy.context,a)
        with mock.patch.object(self.ui,'_timing_source',side_effect=self.ui.VtaReviewUiError('changed source')):
            self.assertFalse(self.ui.refresh(bpy.context,force=True))
        self.assertIsNone(self.ui.cached_scene_source(bpy.context))
        self.assertFalse(self.ui._source_actions_available(bpy.context))
        self.assertIsNone(self.ui.overlay_state()[0])

    def test_failure_snapshot_retries_when_missing_evidence_appears(self):
        backend=self.display_backend();a=self.owned()
        location=self.ui._location_from_context(bpy.context)
        presentation=importlib.import_module(self.ui.__package__+'.vta_presentation').Presentation()
        backend._core.load_analysis_cache.side_effect=FileNotFoundError('missing evidence')
        presentation.observe(backend,location,self.source)
        self.assertIsNotNone(presentation.error)
        count=backend._core.load_project_state.call_count
        for _ in range(20):self.assertFalse(presentation.observe(backend,location,self.source))
        self.assertEqual(backend._core.load_project_state.call_count,count)
        path=self.fixture.root/'analysis'/'analysis.json';path.parent.mkdir(exist_ok=True);path.write_text('available')
        backend._core.load_analysis_cache.side_effect=None
        self.assertTrue(presentation.observe(backend,location,self.source))
        self.assertIsNone(presentation.error)

    def test_frame_memoization_is_bounded_and_outside_coverage_fails_closed(self):
        from dataclasses import replace
        backend=self.display_backend();self.source['end_frame']=1000
        a=self.owned();self.session.load(bpy.context,a)
        for frame in range(400):
            loc=replace(self.ui._location,source_frame=frame,blender_frame=frame+1)
            self.ui._display.frame(backend,loc)
        self.assertLessEqual(len(self.ui._display.frames),256)
        with self.assertRaisesRegex(self.ui.VtaReviewUiError,'outside VTA source coverage'):
            self.ui._display.frame(backend,replace(self.ui._location,source_frame=-1))

    def test_true_explicit_round_trip_preserves_user_choice_restores_on_load(self):
        a=self.owned();self.session.load(bpy.context,a);self.verify_loaded(a)
        signatures={o.name:self.fixture.curves(o) for o in a.objects if o.type=='ARMATURE'}
        empty=bpy.data.scenes.new('empty user Scene');empty.render.fps=60
        empty.use_preview_range=True;empty.frame_preview_start,empty.frame_preview_end=7,11
        config=self.screen['blendcap_vta_editor_modes_v1']
        def away():
            bpy.context.window.scene=empty;self.tick()
            self.assertIs(bpy.context.window.scene,empty)
            self.assertIsNot(self.workspace.sequencer_scene,empty)
            self.assertIsNone(self.ui.overlay_state()[0])
            self.assertIsNone(self.ui.timeline_state()[0])
            self.assertFalse(self.ui.BLENDCAP_OT_vta_analyze.poll(bpy.context))
            self.assertFalse(self.ui.BLENDCAP_OT_vta_generate_animation.poll(bpy.context))
            self.assertEqual(self.screen['blendcap_vta_editor_modes_v1'],config)
            self.assertEqual((empty.render.fps,empty.use_preview_range,tuple(empty.objects)),(60,True,()))
        away();self.session.load(bpy.context,a);self.verify_loaded(a)
        self.assertEqual(bpy.ops.blendcap.vta_new_scene(),{'FINISHED'})
        b=bpy.context.window.scene;self.assertIsNot(a,b)
        self.assertTrue(self.session.matches(bpy.context));self.assertEqual(tuple(b.objects),())
        self.assertIsNone(self.ui.overlay_state()[0])
        away();self.session.enter(bpy.context)
        self.assertIs(bpy.context.window.scene,b);self.assertTrue(self.session.matches(bpy.context))
        self.session.load(bpy.context,a);self.verify_loaded(a)
        self.assertEqual(signatures,{o.name:self.fixture.curves(o) for o in a.objects if o.type=='ARMATURE'})
        for rig in (o for o in b.objects if o.type=='ARMATURE'):
            old=next(o for o in a.objects if o.get('vta_character_id')==rig.get('vta_character_id'))
            self.assertIsNot(old.animation_data.action,rig.animation_data.action)

    def test_exact_select_a_then_all_refreshes_never_reassert_old_scene(self):
        a=self.owned();self.session.load(bpy.context,a)
        b=self.timing.prepare_scene(a,self.fixture.pid,self.source,self.path,a.sequence_editor.active_strip,new=True)
        self.session.load(bpy.context,b)
        bpy.context.window.scene=a;self.tick()
        self.assertIs(bpy.context.window.scene,a)
        self.assertIs(self.workspace.blendcap_vta_loaded_scene,b)
        self.assertIsNone(self.ui._view)

    def test_manual_pin_and_orbit_survive_timers_load_repairs_once(self):
        a=self.owned();self.session.load(bpy.context,a)
        empty=bpy.data.scenes.new('manual presentation choice')
        self.workspace.sequencer_scene=empty
        self.viewport.spaces.active.region_3d.view_perspective='PERSP'
        self.tick()
        self.assertIs(self.workspace.sequencer_scene,empty)
        self.assertIs(bpy.context.window.scene,a)
        self.assertEqual(self.viewport.spaces.active.region_3d.view_perspective,'PERSP')
        self.assertIsNone(self.ui.overlay_state()[0])
        self.session.load(bpy.context,a);self.verify_loaded(a)

    def test_exit_preserves_scene_pointer_roles_data_enter_restores(self):
        from blendcap_vta_area_mode import role_for
        a=self.owned();self.session.load(bpy.context,a)
        before=(dict(a.items()),tuple(a.objects),self.screen['blendcap_vta_editor_modes_v1'],a.render.fps)
        self.session.exit_mode(bpy.context);self.tick()
        self.assertIs(bpy.context.window.scene,a)
        self.assertIs(self.workspace.sequencer_scene,a)
        self.assertIs(self.workspace.blendcap_vta_loaded_scene,a)
        self.assertIsNone(role_for(self.workspace,self.screen,self.timeline))
        self.assertIsNone(self.ui._view)
        self.assertEqual(before,(dict(a.items()),tuple(a.objects),self.screen['blendcap_vta_editor_modes_v1'],a.render.fps))
        self.session.enter(bpy.context);self.verify_loaded(a)

    def test_enter_without_memory_never_guesses(self):
        self.owned()
        with self.assertRaisesRegex(ValueError,'No VTA Scene loaded'): self.session.enter(bpy.context)
        self.assertIsNone(self.workspace.blendcap_vta_loaded_scene)

    def test_failed_load_rolls_back_preserves_last_success(self):
        a=self.owned();self.session.load(bpy.context,a)
        b=self.timing.prepare_scene(a,self.fixture.pid,self.source,self.path,a.sequence_editor.active_strip,new=True)
        state=(b.render.fps,b.render.fps_base,b.blendcap_props.video_source)
        with mock.patch.object(self.ui,'refresh',return_value=False):
            with self.assertRaises(ValueError): self.session.load(bpy.context,b)
        self.assertIs(bpy.context.window.scene,a)
        self.assertIs(self.workspace.blendcap_vta_loaded_scene,a)
        self.assertIs(self.workspace.sequencer_scene,a)
        self.assertIs(self.viewport.spaces.active.camera,a.camera)
        self.assertEqual(state,(b.render.fps,b.render.fps_base,b.blendcap_props.video_source))

    def test_picker_metadata_validates_read_only_requires_confirmation(self):
        a=self.owned();self.session.load(bpy.context,a)
        b=self.timing.prepare_scene(a,self.fixture.pid,self.source,self.path,a.sequence_editor.active_strip,new=True)
        empty=bpy.data.scenes.new('VTA - fake name');bpy.context.window.scene=empty
        state=(bpy.context.window.scene,self.workspace.sequencer_scene,self.workspace.blendcap_vta_loaded_scene)
        items=self.ui.build_load_scene_choices(bpy.context)
        self.assertEqual({x[0] for x in items},{a.name,b.name})
        self.assertEqual(state,(bpy.context.window.scene,self.workspace.sequencer_scene,self.workspace.blendcap_vta_loaded_scene))
        popup=mock.Mock(return_value={'RUNNING_MODAL'})
        ctx=self.session._Proxy(bpy.context,window_manager=SimpleNamespace(invoke_props_dialog=popup))
        operator=SimpleNamespace(report=mock.Mock())
        self.assertEqual(self.ui.BLENDCAP_OT_vta_load_scene.invoke(operator,ctx,None),{'RUNNING_MODAL'})
        popup.assert_called_once_with(operator)

    def test_new_failure_removes_only_new_datablocks_keeps_remembered(self):
        a=self.owned();self.session.load(bpy.context,a)
        before={k:set(getattr(bpy.data,k)) for k in ('scenes','objects','armatures','cameras','actions')}
        with mock.patch.object(self.session,'load',side_effect=ValueError('injected failure')):
            self.assertEqual(self.ui.BLENDCAP_OT_vta_new_scene.execute(SimpleNamespace(report=mock.Mock()),bpy.context),{'CANCELLED'})
        self.assertEqual(before,{k:set(getattr(bpy.data,k)) for k in before})
        self.assertIs(self.workspace.blendcap_vta_loaded_scene,a)

    def test_mixed_ordinary_sequencer_not_forced(self):
        a=self.owned()
        with mock.patch.object(self.session,'configured_role_for',return_value=None):
            with self.assertRaisesRegex(ValueError,'ordinary Sequencer'): self.session.load(bpy.context,a)
        self.assertIsNone(self.workspace.blendcap_vta_loaded_scene)
        self.assertEqual(self.workspace.sequencer_scene,self.pin)

    def test_ui_workflow_available_when_review_off_preview_no_toggle(self):
        calls=[]
        class Layout:
            def operator(self,name,**kwargs): calls.append(name);return SimpleNamespace()
            def menu(self,name,**kwargs): calls.append(name)
            def label(self,**kwargs): pass
            def separator(self): pass
            def row(self): return self
        header=SimpleNamespace(layout=Layout())
        with bpy.context.temp_override(area=self.timeline):
            self.ws._draw_header(header,bpy.context)
            self.assertIn('blendcap.vta_toggle_editor_mode',calls)
            self.assertIn('BLENDCAP_MT_vta_timeline_commands',calls)
            self.ui.draw_scene_workflow(header.layout,bpy.context)
            self.assertTrue({'blendcap.vta_enter_mode','blendcap.vta_exit_mode','blendcap.vta_load_scene','blendcap.vta_new_scene'} <= set(calls))
        calls.clear()
        with bpy.context.temp_override(area=self.preview): self.ws._draw_preview_header(header,bpy.context)
        self.assertNotIn('blendcap.vta_toggle_editor_mode',calls)

    def test_no_analysis_no_camera_source_can_load_without_foreign_view(self):
        a=self.owned();self.session.load(bpy.context,a)
        source=dict(self.source,source_id='other')
        b=self.timing.prepare_scene(a,self.fixture.pid,source,self.path,a.sequence_editor.active_strip,new=True)
        self.assertIsNone(b.camera)
        with mock.patch.object(self.ui,'_timing_source',return_value=(self.fixture.pid,source,
                SimpleNamespace(companion_root=self.fixture.root,source_path=self.path,source_frame=0,blender_frame=1),b.sequence_editor.active_strip)), \
             mock.patch.object(self.backend._core,'load_project_state',return_value=SimpleNamespace(to_dict=lambda:{})):
            self.session.load(bpy.context,b)
        self.assertIs(self.workspace.blendcap_vta_loaded_scene,b)
        self.assertIsNone(self.viewport.spaces.active.camera)
        self.assertFalse(self.viewport.spaces.active.use_local_camera)
        self.assertIsNone(self.ui._view)

    def test_scene_strip_content_cannot_redefine_loaded_target(self):
        a=self.owned();self.session.load(bpy.context,a)
        empty=bpy.data.scenes.new('ordinary referenced content')
        strip=a.sequence_editor.strips.new_scene('Scene strip',empty,channel=25,frame_start=1)
        a.sequence_editor.active_strip=strip
        self.tick()
        self.assertIs(bpy.context.window.scene,a)
        self.assertIs(self.workspace.blendcap_vta_loaded_scene,a)
        self.assertIs(strip.scene,empty)
        self.assertIsNone(self.ui.overlay_state()[0])
        self.session.load(bpy.context,a)
        self.assertEqual(a.sequence_editor.active_strip.type,'MOVIE')

    def test_deleted_remembered_scene_does_not_guess_sibling(self):
        a=self.owned();self.session.load(bpy.context,a)
        bpy.context.window.scene=self.original
        bpy.data.scenes.remove(a,do_unlink=True)
        with self.assertRaisesRegex(ValueError,'No VTA Scene loaded'): self.session.enter(bpy.context)
        self.assertIs(bpy.context.window.scene,self.original)

    def test_different_sources_a_b_a_query_only_loaded_owner(self):
        from blendcap_vta_review import ReviewLocation, ReviewView
        from blendcap_vta_timeline import TimelineView
        a=self.owned();self.session.load(bpy.context,a)
        path=self.fixture.root/'boxing.mp4';path.write_bytes(self.path.read_bytes())
        source=dict(self.source,source_id='boxing',video_sha256='boxing-hash')
        b=self.timing.prepare_scene(a,self.fixture.pid,source,path,a.sequence_editor.active_strip,new=True)
        def timing(ctx):
            info=source if ctx.scene.get('vta_source_id')=='boxing' else self.source
            movie=ctx.scene.sequence_editor.active_strip
            loc=ReviewLocation(self.fixture.root,Path(movie.filepath),ctx.scene.frame_current-1,ctx.scene.frame_current)
            return self.fixture.pid,info,loc,movie
        def query(loc,**kw):
            return ReviewView('analysis',bpy.context.window.scene['vta_source_id'],loc.source_frame,16,16,
                             'analyzed-with-candidates','Candidates',(),())
        with mock.patch.object(self.ui,'_timing_source',side_effect=timing), \
             mock.patch.object(self.ui,'_location_from_context',side_effect=lambda ctx,**kw:timing(ctx)[2]), \
             mock.patch.object(self.backend,'_source_hash',side_effect=lambda p:'boxing-hash' if Path(p)==path else 'test-hash'), \
             mock.patch.object(self.backend,'query',side_effect=query), \
             mock.patch.object(self.backend,'query_timeline',side_effect=lambda loc:TimelineView(bpy.context.window.scene['vta_source_id'],(),())), \
             mock.patch.object(self.backend._core,'load_project_state',return_value=SimpleNamespace(to_dict=lambda:
                {'active_analysis_revision_by_source':{'s':'analysis','boxing':'analysis'}})):
            for scene in (b,a,b):
                self.session.load(bpy.context,scene)
                self.assertEqual(self.ui._view.source_id,scene['vta_source_id'])
                self.assertEqual(self.ui._timeline.source_id,scene['vta_source_id'])
                self.assertIs(self.workspace.blendcap_vta_loaded_scene,scene)
            bpy.context.window.scene=self.original;self.tick()
            self.session.enter(bpy.context)
            self.assertIs(bpy.context.window.scene,b)
            self.assertEqual(self.ui._view.source_id,'boxing')

class SceneWriterAuditTests(unittest.TestCase):
    def test_main_scene_assignments_only_in_explicit_transaction(self):
        root=Path(__file__).resolve().parents[1]/'blendcap'
        writers=[]
        for path in root.glob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8-sig'))
            for fn in ast.walk(tree):
                if not isinstance(fn,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
                for node in ast.walk(fn):
                    if not isinstance(node,ast.Assign): continue
                    for target in node.targets:
                        if isinstance(target,ast.Attribute) and target.attr=='scene' and ast.unparse(target).endswith('window.scene'):
                            writers.append((path.name,fn.name))
        self.assertEqual(writers,[('vta_scene_session.py','_activate_main_scene')])
