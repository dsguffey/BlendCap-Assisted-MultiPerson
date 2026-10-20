"""Retired capture cannot be selected, scheduled or required by active VTA."""
import importlib
import importlib.abc
import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

try:import bpy
except ImportError:bpy=None

ROOT=Path(__file__).resolve().parents[1]

class BlockMediaPipe(importlib.abc.MetaPathFinder):
    def __init__(self):self.attempts=[]
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0]=='mediapipe':
            self.attempts.append(fullname);raise ImportError('MediaPipe deliberately unavailable')


class DependencyTests(unittest.TestCase):
    def test_requirements_and_worker_no_longer_enable_face_capture(self):
        requirements=[line.strip().lower() for line in (ROOT/'requirements.txt').read_text().splitlines() if not line.lstrip().startswith('#')]
        self.assertFalse(any(line.startswith('mediapipe') for line in requirements))
        self.assertTrue(any(line.startswith('opencv-python==') for line in requirements))
        self.assertFalse((ROOT/'save_face_data.py').exists())


@unittest.skipUnless(bpy is not None,'Blender required')
class LegacyFaceDisabledTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass();cls.addon=BlenderOutputTests.addon
        package=BlenderOutputTests.output.__package__
        cls.capture=importlib.import_module(package+'.operators_pipeline')
        cls.panels=importlib.import_module(package+'.panels')
        cls.registration=importlib.import_module(package+'.registration')

    def test_capture_planner_has_no_worker_or_cached_face_overlay_route(self):
        source=inspect.getsource(self.capture.BLENDCAP_OT_generate_mocap)
        for retired in ('save_face_data','props.track_face','"--face"','face_npz'):
            self.assertNotIn(retired,source)
        self.assertIn('save_mhr_data.py',source)

    def test_saved_face_only_request_fails_before_any_io(self):
        props=SimpleNamespace(track_body=False,track_face=True)
        context=SimpleNamespace(scene=SimpleNamespace(blendcap_props=props))
        operator=SimpleNamespace(report=mock.Mock())
        with mock.patch.dict(self.capture.blendcap_state,running=False), mock.patch.object(self.capture,'_resolve_video_path') as resolve:
            self.assertEqual(self.capture.BLENDCAP_OT_generate_mocap.execute(operator,context),{'CANCELLED'})
            resolve.assert_not_called()
        self.assertIn('not supported',operator.report.call_args.args[1])
        self.assertTrue(props.track_face) # Never rewrite saved owner decisions.

    def test_saved_face_export_flags_refused_before_source_io(self):
        props=SimpleNamespace(export_face=True,use_separate_face=False)
        context=SimpleNamespace(scene=SimpleNamespace(blendcap_props=props))
        operator=SimpleNamespace(report=mock.Mock())
        with mock.patch.dict(self.capture.blendcap_state,running=False),mock.patch.dict(self.capture.bvh_state,running=False):
            for method,args in ((self.capture.BLENDCAP_OT_generate_bvh.invoke,(context,None)),
                                (self.capture.BLENDCAP_OT_generate_bvh.execute,(context,))):
                self.assertEqual(method(operator,*args),{'CANCELLED'})
        self.assertTrue(props.export_face)

    def test_face_capture_and_application_controls_not_drawn(self):
        for function in (self.panels._draw_capture_section,self.panels._draw_bvh_settings_section):
            source=inspect.getsource(function)
            for prop in ('"track_face"','"export_face"','"use_separate_face"','"separate_face_data"'):
                self.assertNotIn(prop,source)
        self.assertNotIn('_draw_arkit_section',inspect.getsource(self.panels.draw_blendcap_ui))

    def test_blocked_mediapipe_registration_lifecycle_has_no_face_entrypoints(self):
        import sys
        blocker=BlockMediaPipe();sys.meta_path.insert(0,blocker)
        try:
            for _ in range(2):
                self.addon.register()
                try:
                    props=bpy.context.scene.blendcap_props
                    self.assertNotIn('track_face',props.bl_rna.properties.keys())
                    self.assertTrue(hasattr(bpy.types,'BLENDCAP_OT_vta_update_tracking'))
                    self.assertTrue(hasattr(bpy.types,'BLENDCAP_OT_vta_generate_animation'))
                    for retired in ('BLENDCAP_OT_apply_arkit','BLENDCAP_OT_cancel_arkit_apply','BLENDCAP_OT_save_arkit_csv'):
                        self.assertFalse(hasattr(bpy.types,retired))
                finally:self.addon.unregister()
            self.assertEqual(blocker.attempts,[])
        finally:sys.meta_path.remove(blocker)
