"""Real Blender + real ProjectState 0.8 lifecycle, no inference/clock thresholds."""
import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import test_blendcap_vta_scene_binding as binding_tests
bpy = binding_tests.bpy


@unittest.skipUnless(bpy is not None, 'Blender runtime required')
class SourceLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binding_tests.SceneBindingTests.setUpClass.__func__(cls)
        cls.lifecycle = importlib.import_module(cls.ui.__package__+'.vta_source_lifecycle')
        import vta_core
        cls.core = vta_core

    cleanup = binding_tests.SceneBindingTests.cleanup
    prepare = binding_tests.SceneBindingTests.prepare
    owned = binding_tests.SceneBindingTests.owned
    restore_layout = binding_tests.SceneBindingTests.restore_layout
    tick = binding_tests.SceneBindingTests.tick

    def setUp(self):
        binding_tests.SceneBindingTests.setUp(self)
        self.root = self.fixture.root/'new-project'
        self.backend._core = self.core
        self.backend._source_hash = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        patch = mock.patch.object(self.lifecycle, 'companion_root_for_blend', return_value=self.root)
        patch.start(); self.addCleanup(patch.stop)
        def timing(context):
            scene = context.scene
            project = self.core.load_project_state(self.root)
            source = project.source_reference(scene.get('vta_source_id')).to_dict()
            from blendcap_vta_review import ReviewLocation
            return project.project_id, source, ReviewLocation(self.root,self.path,
                    scene.frame_current-1,scene.frame_current),scene.sequence_editor.active_strip
        patch = mock.patch.object(self.ui, '_timing_source', side_effect=timing)
        patch.start(); self.addCleanup(patch.stop)
        self.scene = self.session.create_blank()
        self.session.load(bpy.context,self.scene)

    def configure(self):
        return self.lifecycle.configure(bpy.context,str(self.path))

    def assert_blank(self):
        s=self.scene
        self.assertTrue(self.session.owned(s));self.assertFalse(self.session.source_bound(s))
        self.assertIsNone(s.camera);self.assertEqual(tuple(s.objects),())
        self.assertFalse(s.sequence_editor and len(s.sequence_editor.strips))
        self.assertIsNone(s.animation_data)
        self.assertIs(bpy.context.window.scene,s)
        self.assertIs(self.workspace.blendcap_vta_loaded_scene,s)
        self.assertTrue(self.session.matches(bpy.context))
        self.assertIsNone(self.ui.overlay_state()[0]);self.assertIsNone(self.ui.timeline_state()[0])
        self.assertFalse(self.ui._source_actions_available(bpy.context))
        self.assertFalse(self.ui.BLENDCAP_OT_vta_analyze.poll(bpy.context))
        self.assertFalse(self.ui.BLENDCAP_OT_vta_generate_animation.poll(bpy.context))
        self.assertTrue(self.ui.BLENDCAP_OT_vta_choose_clip.poll(bpy.context))
        self.assertFalse(self.ui.BLENDCAP_OT_vta_clear_source.poll(bpy.context))

    def test_new_copies_no_content_state_or_actions_from_source_backed_scene(self):
        self.configure()
        before={kind:set(getattr(bpy.data,kind)) for kind in ('objects','cameras','armatures','actions','collections')}
        self.assertEqual(bpy.ops.blendcap.vta_new_scene(),{'FINISHED'})
        old=self.scene;self.scene=bpy.context.scene
        self.assert_blank()
        self.assertNotEqual(old[self.session.OWNER_ID],self.scene[self.session.OWNER_ID])
        self.assertEqual(set(self.scene.keys()),{self.session.OWNER_TAG,self.session.OWNER_ID})
        for kind, values in before.items():self.assertEqual(set(getattr(bpy.data,kind)),values)
        self.assertFalse(self.scene.collection.children)

    def test_blank_exit_enter_and_load_picker_with_backed_scene(self):
        a=self.configure();b=self.session.create_blank();self.session.load(bpy.context,b)
        self.scene=b;self.session.exit_mode(bpy.context)
        bpy.context.window.scene=self.original;self.tick()
        self.session.enter(bpy.context);self.assert_blank()
        self.assertEqual({i[0] for i in self.ui.build_load_scene_choices(bpy.context)},{a.name,b.name})
        self.session.load(bpy.context,a);self.session.load(bpy.context,b);self.assert_blank()

    def test_configure_registers_real_source_metadata_fps_and_strip_without_inference(self):
        self.scene.render.fps=60  # Source frame count must not inherit Scene playback FPS.
        with mock.patch.object(self.ui,'_configured_analysis_paths',side_effect=AssertionError('inference')):
            self.configure()
        project=self.core.load_project_state(self.root).to_dict()
        self.assertEqual(project['version'],'0.8')
        self.assertEqual(len(project['sources']),1)
        source=project['sources'][0]
        self.assertEqual((source['fps'],source['start_frame'],source['end_frame'],source['width'],source['height']),
                         (24.,0,3,16,16))
        self.assertEqual(source['video_sha256'],hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(self.scene.render.fps/self.scene.render.fps_base,24.)
        self.assertEqual((self.scene.frame_start,self.scene.frame_end),(1,4))
        self.assertEqual(len(self.scene.sequence_editor.strips),1)
        self.assertEqual(self.scene.sequence_editor.active_strip.frame_start,1)
        self.assertTrue(self.ui._source_actions_available(bpy.context))
        self.assertFalse(self.ui.BLENDCAP_OT_vta_choose_clip.poll(bpy.context))
        self.assertTrue(self.ui.BLENDCAP_OT_vta_clear_source.poll(bpy.context))
        self.assertTrue(self.ui.BLENDCAP_OT_vta_analyze.poll(bpy.context))
        self.assertEqual(project['characters'],[]);self.assertEqual(project['repair_ranges'],[])
        self.assertEqual(project['active_analysis_revision_by_source'],{})

    def test_no_in_place_replacement_even_execute_bypass(self):
        self.configure();before=(self.root/'project_state.json').read_bytes()
        strip=self.scene.sequence_editor.active_strip
        with self.assertRaisesRegex(ValueError,'Clear Source'):
            self.configure()
        self.assertEqual(self.scene.sequence_editor.active_strip,strip)
        self.assertEqual((self.root/'project_state.json').read_bytes(),before)

    def test_clear_detaches_only_scoped_owned_content_preserves_history_and_actions(self):
        self.configure();s=self.scene;pid=s['vta_project_id'];sid=s['vta_source_id']
        root=bpy.data.objects.new('owned root',None)
        root['vta_scene_root_version']=1
        camera=bpy.data.objects.new('owned camera',bpy.data.cameras.new('owned camera data'))
        camera.data['vta_scene_reference']='{}';s.camera=camera
        rig=bpy.data.objects.new('owned rig',bpy.data.armatures.new('owned rig data'))
        rig['vta_character_id']='human';rig.location.x=2;rig.keyframe_insert('location',frame=1)
        action=rig.animation_data.action
        for obj in (root,camera,rig):
            obj['vta_project_id'],obj['vta_source_id']=pid,sid;s.collection.objects.link(obj)
        user=bpy.data.objects.new('user',None);s.collection.objects.link(user)
        user['vta_project_id'],user['vta_source_id']=pid,sid  # Not a sufficient ownership marker.
        ad=s.sequence_editor.strips.new_movie('user movie',str(self.path),channel=10,frame_start=20)
        history=(self.root/'project_state.json').read_bytes()
        cache=self.root/'analysis'/'history.bin';cache.parent.mkdir();cache.write_bytes(b'preserve')
        self.lifecycle.clear(bpy.context)
        self.assertEqual(tuple(s.objects),(user,));self.assertEqual(tuple(s.sequence_editor.strips),(ad,))
        for obj in (root,camera,rig):self.assertIn(obj.name,bpy.data.objects);self.assertTrue(obj.use_fake_user)
        self.assertIs(rig.animation_data.action,action);self.assertTrue(action.use_fake_user)
        self.assertEqual((self.root/'project_state.json').read_bytes(),history);self.assertEqual(cache.read_bytes(),b'preserve')
        self.assertTrue(self.session.owned(s));self.assertFalse(self.session.source_bound(s))
        self.assertFalse(self.ui._source_actions_available(bpy.context))

    def test_clear_then_configure_reuses_history_without_duplicate_registration(self):
        self.configure();before=(self.root/'project_state.json').read_bytes();sid=self.scene['vta_source_id']
        self.lifecycle.clear(bpy.context);self.assert_blank()
        self.configure();self.assertEqual(self.scene['vta_source_id'],sid)
        self.assertEqual((self.root/'project_state.json').read_bytes(),before)

    def test_tagged_source_can_detach_if_media_or_private_backend_unavailable(self):
        self.configure();before=(self.root/'project_state.json').read_bytes()
        self.scene.sequence_editor.active_strip.filepath=str(self.path.with_name('offline.mp4'))
        with mock.patch.object(self.ui,'_get_backend',side_effect=AssertionError('detach needs no backend')):
            self.lifecycle.clear(bpy.context)
        self.assert_blank();self.assertEqual((self.root/'project_state.json').read_bytes(),before)

    def test_different_clip_can_register_only_after_clear(self):
        self.configure();first=self.scene['vta_source_id']
        other=self.path.with_name('other.mp4');other.write_bytes(self.path.read_bytes()+b'\0')
        with self.assertRaisesRegex(ValueError,'Clear Source'):
            self.lifecycle.configure(bpy.context,str(other))
        self.lifecycle.clear(bpy.context)
        self.lifecycle.configure(bpy.context,str(other))
        self.assertNotEqual(first,self.scene['vta_source_id'])
        self.assertEqual(len(self.core.load_project_state(self.root).to_dict()['sources']),2)

    def test_invalid_movie_and_missing_path_leave_original_blank_scene(self):
        with self.assertRaises(ValueError):self.lifecycle.configure(bpy.context,str(self.path.with_name('missing')))
        self.assert_blank()
        bad=self.path.with_name('invalid.mp4');bad.write_bytes(b'not a movie')
        with self.assertRaises(Exception):self.lifecycle.configure(bpy.context,str(bad))
        self.assert_blank();self.assertFalse((self.root/'project_state.json').exists())

    def test_configure_presentation_failure_does_not_publish_project(self):
        with mock.patch.object(self.ws,'configure_source_camera_views',side_effect=ValueError('injected')):
            with self.assertRaisesRegex(ValueError,'injected'):self.configure()
        self.assert_blank();self.assertFalse((self.root/'project_state.json').exists())

    def test_configure_atomic_save_failure_restores_host_and_prior_project_bytes(self):
        self.configure();self.lifecycle.clear(bpy.context)
        old=(self.root/'project_state.json').read_bytes()
        other=self.path.with_name('another.mp4');other.write_bytes(self.path.read_bytes()+b'\0')
        with mock.patch.object(self.core,'save_project_state',side_effect=OSError('injected publish failure')):
            with self.assertRaisesRegex(OSError,'publish failure'):
                self.lifecycle.configure(bpy.context,str(other))
        self.assert_blank();self.assertEqual((self.root/'project_state.json').read_bytes(),old)

    def test_clear_failure_rolls_back_source_objects_camera_presentation_and_fake_users(self):
        self.configure();s=self.scene
        obj=bpy.data.objects.new('owned root',None);s.collection.objects.link(obj)
        obj['vta_scene_root_version']=1;obj['vta_project_id']=s['vta_project_id'];obj['vta_source_id']=s['vta_source_id']
        strip=s.sequence_editor.active_strip;metadata=dict(s.items());mode=self.workspace.blendcap_vta_mode_enabled
        before=(self.root/'project_state.json').read_bytes()
        with mock.patch.object(self.lifecycle,'_remove_strip',side_effect=RuntimeError('injected remove failure')):
            with self.assertRaisesRegex(RuntimeError,'remove failure'):self.lifecycle.clear(bpy.context)
        self.assertEqual(dict(s.items()),metadata);self.assertIn(obj.name,s.objects)
        self.assertFalse(obj.use_fake_user);self.assertEqual(s.sequence_editor.active_strip,strip)
        self.assertEqual(self.workspace.blendcap_vta_mode_enabled,mode)
        self.assertTrue(self.ui._source_actions_available(bpy.context))
        self.assertEqual((self.root/'project_state.json').read_bytes(),before)

    def test_blank_ticks_polls_draws_require_no_project_or_backend(self):
        class Layout:
            def operator(self,*args,**kw):return SimpleNamespace()
            def __getattr__(self,key):return lambda *args,**kw:self
        with mock.patch.object(self.ui,'_get_backend',side_effect=AssertionError('blank backend reload')):
            for _ in range(30):
                self.ui._scene_observer_timer();self.ui.refresh(bpy.context)
                self.ui.draw_vta_source(Layout(),bpy.context)
                self.assertTrue(self.ui.BLENDCAP_OT_vta_choose_clip.poll(bpy.context))
                self.assertFalse(self.ui.BLENDCAP_OT_vta_analyze.poll(bpy.context))
        self.assert_blank()

    def test_clear_keeps_real_assignment_repair_and_cache_history_byte_exact(self):
        self.configure()
        from vta_core.state_model import PresenceState
        project=self.core.load_project_state(self.root)
        character=project.create_character('human')
        source=self.scene['vta_source_id']
        project.mark_repair_range(character.character_id,source,1,2,mode='freeze')
        project.add_identity_keyframe(character.character_id,source,0,PresenceState.NOT_VISIBLE)
        self.core.save_project_state(project,self.root)
        before=(self.root/'project_state.json').read_bytes()
        self.lifecycle.clear(bpy.context)
        self.assertEqual((self.root/'project_state.json').read_bytes(),before)
        self.assert_blank()

    def test_missing_or_corrupt_existing_project_is_not_silently_replaced(self):
        self.root.mkdir();(self.root/'analysis-history').write_bytes(b'protected')
        with self.assertRaisesRegex(ValueError,'Companion data'):self.configure()
        self.assert_blank()
        (self.root/'project_state.json').write_bytes(b'bad JSON')
        with self.assertRaises(Exception):self.configure()
        self.assertEqual((self.root/'project_state.json').read_bytes(),b'bad JSON')
        self.assertEqual((self.root/'analysis-history').read_bytes(),b'protected');self.assert_blank()

    def test_unowned_scene_cannot_choose_clip_even_direct_command(self):
        bpy.context.window.scene=self.original
        self.assertFalse(self.ui.BLENDCAP_OT_vta_choose_clip.poll(bpy.context))
        with self.assertRaisesRegex(ValueError,'Load this VTA Scene'):self.configure()

    def test_clear_preserves_unrelated_user_camera_without_stale_local_camera(self):
        self.configure()
        camera=bpy.data.objects.new('user camera',bpy.data.cameras.new('user camera data'))
        self.scene.collection.objects.link(camera);self.scene.camera=camera
        self.session.load(bpy.context,self.scene)
        self.lifecycle.clear(bpy.context)
        self.assertIs(self.scene.camera,camera);self.assertIn(camera.name,self.scene.objects)
        self.assertFalse(self.viewport.spaces.active.use_local_camera)
        self.assertIs(self.viewport.spaces.active.camera,camera)  # Non-local slot reads Scene.camera.
        self.assertTrue(self.session.matches(bpy.context))

    def test_shared_collection_clear_is_rejected_before_any_detach(self):
        self.configure();s=self.scene
        shared=bpy.data.collections.new('shared user collection');s.collection.children.link(shared)
        self.original.collection.children.link(shared)
        obj=bpy.data.objects.new('owned root',None);shared.objects.link(obj)
        obj['vta_scene_root_version']=1;obj['vta_project_id']=s['vta_project_id'];obj['vta_source_id']=s['vta_source_id']
        with self.assertRaisesRegex(ValueError,'shared'):self.lifecycle.clear(bpy.context)
        self.assertTrue(self.session.source_bound(s));self.assertIn(obj.name,s.objects)
        self.assertIn(obj.name,self.original.objects)
        self.original.collection.children.unlink(shared)

    def test_source_ui_shows_choose_only_blank_and_x_only_bound(self):
        calls=[]
        class Layout:
            def row(self,**kw):return self
            def label(self,**kw):pass
            def operator(self,name,**kw):calls.append(name)
        self.ui.draw_vta_source(Layout(),bpy.context)
        self.assertEqual(calls,['blendcap.vta_choose_clip'])
        calls.clear();self.configure();self.ui.draw_vta_source(Layout(),bpy.context)
        self.assertEqual(calls,['blendcap.vta_clear_source'])
