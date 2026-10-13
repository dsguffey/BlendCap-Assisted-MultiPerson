"""Dedicated host Scene timing; never retime user Actions or authoritative frames."""
import base64
import importlib
from pathlib import Path
import unittest
from unittest import mock

try:
    import bpy
except ImportError:
    bpy = None

# Four black MPEG4 frames at 24 fps, generated once; no codec/model dependency
# beyond Blender's built-in movie decoder. A test fixture, not QA footage.
MOVIE = 'AAAAHGZ0eXBpc29tAAACAGlzb21pc28ybXA0MQAAAAhmcmVlAAAAMm1kYXQAAAGzABAHAAABthBgUYI9t+8AAAG2UPAjfwAAAbZRYCN/AAABtlHwI38AAANjbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAAKcAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAo50cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAAKcAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAACnAAAAAAABAAAAAAIGbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAwAAAACABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABsW1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAXFzdGJsAAAA6XN0c2QAAAAAAAAAAQAAANltcDR2AAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABEkxhdmM2My42LjEwMSBtcGVnNAAAAAAAAAAAAAAAAAAAGP//AAAAX2VzZHMAAAAAA4CAgE4AAQAEgICAQCARAAAAAAMNQAAAB+AFgICALgAAAbABAAABtYkTAAABAAAAASAAxI2IAMUAhAIUYwAAAbJMYXZjNjMuNi4xMDEGgICAAQIAAAAQcGFzcAAAAAEAAAABAAAAFGJ0cnQAAAAAAAMNQAAAB+AAAAAYc3R0cwAAAAAAAAABAAAABAAAAgAAAAAUc3RzcwAAAAAAAAABAAAAAQAAABxzdHNjAAAAAAAAAAEAAAABAAAABAAAAAEAAAAkc3RzegAAAAAAAAAAAAAABAAAABIAAAAIAAAACAAAAAgAAAAUc3RjbwAAAAAAAAABAAAALAAAAGF1ZHRhAAAAWW1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAG1kaXJhcHBsAAAAAAAAAAAAAAAALGlsc3QAAAAkqXRvbwAAABxkYXRhAAAAAQAAAABMYXZmNjMuNS4xMDA='


@unittest.skipUnless(bpy is not None, 'Blender runtime required')
class SceneTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_object_placement import ObjectPlacementTests
        ObjectPlacementTests.setUpClass()
        cls.fixture_class = ObjectPlacementTests
        cls.timing = importlib.import_module(ObjectPlacementTests.output.__package__ + '.vta_scene_timing')
        cls.ui = importlib.import_module(ObjectPlacementTests.output.__package__ + '.vta_review_ui')

    def setUp(self):
        self.fixture = self.fixture_class()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.original = bpy.context.scene
        self.scenes_before = set(bpy.data.scenes)
        self.fps_before = self.original.render.fps, self.original.render.fps_base
        self.seq = self.original.sequence_editor
        self.strips_before = set(self.seq.strips) if self.seq else set()
        self.path = self.fixture.root/'source.mp4'
        self.path.write_bytes(base64.b64decode(MOVIE))
        self.strip = self.original.sequence_editor_create().strips.new_movie('timing source', str(self.path), channel=20, frame_start=1)
        self.original.sequence_editor.active_strip = self.strip
        self.source = {'source_id':'s','video_sha256':'test-hash','fps':24.,'start_frame':0,'end_frame':3}
        self.addCleanup(self.cleanup)

    def cleanup(self):
        bpy.context.window.scene = self.original
        self.original.render.fps, self.original.render.fps_base = self.fps_before
        for scene in set(bpy.data.scenes) - self.scenes_before:
            bpy.data.scenes.remove(scene, do_unlink=True)
        if self.original.sequence_editor:
            for strip in set(self.original.sequence_editor.strips) - self.strips_before:
                self.original.sequence_editor.strips.remove(strip)

    def prepare(self):
        return self.timing.prepare_scene(self.original, self.fixture.pid, self.source, self.path, self.strip)

    def owned(self):
        s = self.prepare()
        bpy.context.window.scene = s
        return s

    def test_fresh_scene_matches_source_preserving_frame_identity(self):
        from blendcap_vta_review import map_vse_frame_to_source
        s = self.prepare()
        self.assertEqual(s.render.fps/s.render.fps_base,24)
        strip = s.sequence_editor.active_strip
        self.assertEqual(strip.frame_start,1)
        for f in range(1,5):
            self.assertEqual(map_vse_frame_to_source(blender_frame=f,visible_start=strip.frame_final_start,visible_end=strip.frame_final_end,source_offset=0), f-1)
        self.assertAlmostEqual((s.frame_end-s.frame_start+1)/(s.render.fps/s.render.fps_base),4/24)

    def test_user_scene_untouched_and_no_unrelated_animation_migrated(self):
        self.original.render.fps,self.original.render.fps_base=30,1
        user=bpy.data.objects.new('unrelated user animation',None)
        self.original.collection.objects.link(user)
        for f,x in ((1,2),(80,9)):
            user.location.x=x;user.keyframe_insert('location',frame=f)
        keys=self.fixture.curves(user)
        s=self.prepare()
        self.assertEqual(self.original.render.fps,30)
        self.assertNotIn(user.name,s.objects)
        self.assertEqual(self.fixture.curves(user),keys)
        self.assertEqual(set(s.objects),{o for o in s.objects if o.get('vta_project_id')==self.fixture.pid})

    def test_copy_preserves_rig_curves_and_isolates_actions(self):
        self.fixture.check_keys('location')
        before={o['vta_character_id']:self.fixture.curves(o) for o in self.fixture.rigs}
        s=self.owned()
        for original in self.fixture.rigs:
            copy=next(o for o in s.objects if o.get('vta_character_id')==original['vta_character_id'])
            self.assertEqual(self.fixture.curves(copy),before[copy['vta_character_id']])
            self.assertIsNot(copy.animation_data.action,original.animation_data.action)
            self.assertIsNot(copy.data,original.data)
            self.assertTrue(self.fixture.output.supported_parent(copy))
        roots=[o for o in s.objects if o.get('vta_scene_root_version')]
        self.assertEqual(len(roots),1)
        self.assertEqual(len(roots[0].children),3)

    def test_match_changes_only_owned_scene_rate_not_keys_projection_or_root(self):
        from bpy_extras.object_utils import world_to_camera_view
        s=self.owned()
        rigs=[o for o in s.objects if o.type=='ARMATURE']
        before=[self.fixture.curves(o) for o in rigs]
        matrices=[o.matrix_world.copy() for o in s.objects]
        point=rigs[0].matrix_world@rigs[0].pose.bones['Hips'].head
        projection=world_to_camera_view(s,s.camera,point)
        original_rate=(self.original.render.fps,self.original.render.fps_base)
        s.render.fps=30
        self.timing.match_source_rate(s,self.fixture.pid,self.source)
        self.assertEqual((self.original.render.fps,self.original.render.fps_base),original_rate)
        self.assertEqual([self.fixture.curves(o) for o in rigs],before)
        self.assertEqual([o.matrix_world for o in s.objects],matrices)
        self.assertEqual(world_to_camera_view(s,s.camera,point),projection)

    def test_multiple_scenes_independent_and_existing_owner_reused(self):
        a=self.prepare()
        other=dict(self.source,source_id='boxing',fps=60.,video_sha256='boxing-hash')
        b=self.timing.prepare_scene(self.original,self.fixture.pid,other,self.path,self.strip)
        self.assertIs(self.prepare(),a)
        self.assertEqual(a.render.fps,24)
        self.assertEqual(b.render.fps,60)
        bpy.context.window.scene=b
        self.assertEqual(bpy.context.scene.render.fps,60)
        bpy.context.window.scene=a
        self.assertEqual(bpy.context.scene.render.fps,24)

    def test_integer_and_fractional_rates(self):
        s=self.prepare()
        for rate in (24,25,30,60,23.976,29.97,24000/1001,30000/1001):
            with self.subTest(rate=rate):
                self.timing.match_source_rate(s,self.fixture.pid,dict(self.source,fps=rate))
                self.assertAlmostEqual(s.render.fps/s.render.fps_base,rate,places=5)

    def test_unowned_name_impostor_and_wrong_source_rejected(self):
        before=self.original.render.fps,self.original.render.fps_base
        self.original.name='VTA - name impostor'
        with self.assertRaisesRegex(ValueError,'unrelated Scenes'):
            self.timing.match_source_rate(self.original,self.fixture.pid,self.source)
        self.assertEqual((self.original.render.fps,self.original.render.fps_base),before)
        s=self.prepare()
        with self.assertRaisesRegex(ValueError,'matching VTA-owned'):
            self.timing.match_source_rate(s,self.fixture.pid,dict(self.source,source_id='wrong'))

    def test_invalid_and_variable_source_rate_fail_without_creation(self):
        before=set(bpy.data.scenes)
        for value in (0,-1,float('nan'),True):
            with self.assertRaises(ValueError):
                self.timing.prepare_scene(self.original,self.fixture.pid,dict(self.source,fps=value),self.path,self.strip)
        with self.assertRaisesRegex(ValueError,'Variable-frame-rate'):
            self.timing.prepare_scene(self.original,self.fixture.pid,dict(self.source,vfr=True),self.path,self.strip)
        self.assertEqual(set(bpy.data.scenes),before)

    def test_reverse_and_noncanonical_strip_refuse_remapping(self):
        self.strip.use_reverse_frames=True
        with self.assertRaisesRegex(ValueError,'unretimed'):
            self.prepare()
        self.strip.use_reverse_frames=False
        self.strip.frame_start=10
        with self.assertRaisesRegex(ValueError,'keys were not moved'):
            self.prepare()

    def test_failure_rolls_back_only_created_datablocks(self):
        before={k:set(getattr(bpy.data,k)) for k in ('scenes','objects','armatures','cameras','actions')}
        with mock.patch.object(self.timing,'_copy_action',side_effect=RuntimeError('injected')), self.assertRaisesRegex(RuntimeError,'injected'):
            self.prepare()
        self.assertEqual({k:set(getattr(bpy.data,k)) for k in before},before)

    def test_match_operator_poll_and_execute_defend_owner(self):
        with mock.patch.object(self.ui,'_timing_source',return_value=(self.fixture.pid,self.source,None,self.strip)),mock.patch.object(self.ui,'_analysis_locked',return_value=False):
            self.assertFalse(self.ui.BLENDCAP_OT_vta_match_source_fps.poll(bpy.context))
            self.owned()
            self.assertTrue(self.ui.BLENDCAP_OT_vta_match_source_fps.poll(bpy.context))
            from types import SimpleNamespace
            bpy.context.scene.render.fps=30
            self.assertEqual(self.ui.BLENDCAP_OT_vta_match_source_fps.execute(SimpleNamespace(report=mock.Mock()),bpy.context),{'FINISHED'})
            self.assertEqual(bpy.context.scene.render.fps,24)

    def test_library_save_reload_preserves_independent_scene_fps(self):
        s=self.prepare()
        self.original.render.fps,self.original.render.fps_base=30,1
        path=self.fixture.root/'scenes.blend'
        bpy.data.libraries.write(str(path),{s,self.original})
        with bpy.data.libraries.load(str(path)) as (src,dest):
            dest.scenes=[s.name,self.original.name]
        a,b=dest.scenes
        self.assertEqual(a.render.fps/a.render.fps_base,24)
        self.assertEqual(b.render.fps/b.render.fps_base,30)

    def test_same_rate_never_adopts_user_scene_by_content(self):
        self.original.render.fps,self.original.render.fps_base=24,1
        s=self.prepare()
        self.assertIsNot(s,self.original)
        self.assertNotIn(self.timing.SCENE_TAG,self.original)
        self.assertEqual(self.original.render.fps,24)

    def test_owned_scene_edited_rate_can_be_matched_without_new_scene(self):
        s=self.prepare();s.render.fps=60
        before=set(bpy.data.scenes)
        self.assertIs(self.timing.prepare_scene(s,self.fixture.pid,self.source,self.path,s.sequence_editor.active_strip),s)
        self.timing.match_source_rate(s,self.fixture.pid,self.source)
        self.assertEqual(s.render.fps,24)
        self.assertEqual(set(bpy.data.scenes),before)

    def test_regeneration_in_owned_copy_never_rewrites_original_scene(self):
        self.fixture.check_keys('location')
        before=[self.fixture.curves(o) for o in self.fixture.rigs]
        source_camera=self.original.camera
        camera_action=source_camera.data.animation_data.action
        self.original.render.fps,self.original.render.fps_base=30,1
        s=self.owned()
        for bounded in (True,False):
            self.fixture.regenerate(bounded)
            self.assertEqual([self.fixture.curves(o) for o in self.fixture.rigs],before)
            self.assertIs(self.original.camera,source_camera)
            self.assertIs(source_camera.data.animation_data.action,camera_action)
            self.assertEqual(self.original.render.fps,30)
            self.assertEqual(s.render.fps,24)

    def test_match_does_not_touch_project_or_repair_numbers(self):
        import json
        path=self.fixture.root/'project_state.json'
        state={'schema_version':'0.8','repair_ranges':[{'start_frame':15,'end_frame':18,'mode':'interpolate'}]}
        path.write_text(json.dumps(state))
        before=path.read_bytes()
        s=self.prepare();s.render.fps=30
        self.timing.match_source_rate(s,self.fixture.pid,self.source)
        self.assertEqual(path.read_bytes(),before)

    def test_duplicate_owned_scenes_rejected_before_creation(self):
        s=self.prepare()
        duplicate=s.copy()
        before=set(bpy.data.scenes)
        with self.assertRaisesRegex(ValueError,'Multiple VTA Scenes'):
            self.prepare()
        self.assertEqual(set(bpy.data.scenes),before)

    def test_known_source_validation_rejects_conflicting_movie_fps(self):
        from types import SimpleNamespace
        from blendcap_vta_review import ReviewLocation
        source=dict(self.source,fps=30)
        project=SimpleNamespace(to_dict=lambda:{'project_id':self.fixture.pid,'sources':[source]})
        backend=SimpleNamespace(_core=SimpleNamespace(load_project_state=lambda _:project),_source_hash=lambda _:source['video_sha256'])
        with mock.patch.object(self.ui,'_location_from_context',return_value=ReviewLocation(self.fixture.root,self.path,0,1)),mock.patch.object(self.ui,'_get_backend',return_value=backend):
            with self.assertRaisesRegex(self.ui.VtaReviewUiError,'Movie FPS differs'):
                self.ui._timing_source(bpy.context)
            self.assertFalse(self.ui.BLENDCAP_OT_vta_match_source_fps.poll(bpy.context))

    def test_new_scene_initializes_typed_source_mode_not_shadow_custom_property(self):
        existing=hasattr(bpy.types.Scene,'blendcap_props')
        if not existing:
            class TimingTestProps(bpy.types.PropertyGroup):
                video_source:bpy.props.EnumProperty(items=(('FILE','File',''),('VSE','Video','')))
            bpy.utils.register_class(TimingTestProps)
            bpy.types.Scene.blendcap_props=bpy.props.PointerProperty(type=TimingTestProps)
        try:
            s=self.prepare()
            self.assertEqual(s.blendcap_props.video_source,'VSE')
            self.assertNotIn('blendcap_props',s)
        finally:
            if not existing:
                del bpy.types.Scene.blendcap_props
                bpy.utils.unregister_class(TimingTestProps)

    def test_native_scene_switch_preserves_foreign_camera_without_enforcement(self):
        from types import SimpleNamespace
        workspace=importlib.import_module(self.fixture.output.__package__+'.vta_workspace')
        area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
        space=area.spaces.active
        before=(space.camera,space.use_local_camera,space.region_3d.view_perspective)
        try:
            space.use_local_camera=True;space.camera=self.original.camera
            space.region_3d.view_perspective='PERSP'
            owned=self.owned()
            native_camera = space.camera  # Blender itself may follow on window.scene assignment.
            fake=SimpleNamespace(get=lambda _tag:1,screens=[bpy.context.screen])
            self.ui._scene_observer_timer()
            self.assertIs(space.camera,native_camera)
            self.assertEqual(space.region_3d.view_perspective,'PERSP')
        finally:
            space.camera,space.use_local_camera,space.region_3d.view_perspective=before

    def test_current_scene_local_camera_choice_is_not_overridden(self):
        from types import SimpleNamespace
        workspace=importlib.import_module(self.fixture.output.__package__+'.vta_workspace')
        area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
        space=area.spaces.active
        before=(space.camera,space.use_local_camera)
        try:
            space.camera=self.original.camera;space.use_local_camera=True
            fake=SimpleNamespace(get=lambda _tag:1,screens=[bpy.context.screen])
            self.ui._scene_observer_timer()
            self.assertIs(space.camera,self.original.camera)
        finally:
            space.camera,space.use_local_camera=before
