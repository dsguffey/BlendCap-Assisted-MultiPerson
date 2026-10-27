"""Retired capture has no UI/registration/import fallback into detection."""
import ast
import builtins
import importlib
import importlib.abc
import inspect
from pathlib import Path
import sys
import unittest
from unittest import mock

try:
    import bpy
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]
RETIRED = {'blendcap.generate_mocap', 'blendcap.cancel_cleanup'}


class RetirementSourceTests(unittest.TestCase):
    def test_no_detector_module_dependency_or_silent_box_fallback(self):
        self.assertFalse((ROOT/'models/fast-sam-3d-body/tools/build_detector.py').exists())
        for relative in ('save_mhr_data.py', 'models/fast-sam-3d-body/sam_3d_body/sam_3d_body_estimator.py',
                         'models/fast-sam-3d-body/sam_3d_body/models/meta_arch/sam3d_body.py'):
            text=(ROOT/relative).read_text(encoding='utf-8')
            tree=ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node,(ast.Import,ast.ImportFrom)):
                    self.assertNotIn('ultralytics',ast.unparse(node))
                if isinstance(node,ast.Name):
                    self.assertNotIn(node.id,{'HumanDetector','load_detector_only','run_preview'})
            self.assertNotIn('yolo',text.lower())
        requirements=(ROOT/'requirements.txt').read_text(encoding='utf-8')
        packages=[line.lower() for line in requirements.splitlines() if line and not line.startswith('#')]
        self.assertFalse(any('ultralytics' in p or 'thop' in p for p in packages))

    def test_source_ui_and_registration_have_no_retired_operator_ids(self):
        for relative in ('blendcap/panels.py','blendcap/registration.py','blendcap/operators_pipeline.py',
                         'blendcap/vta_review_ui.py','blendcap/vta_workspace.py'):
            text=(ROOT/relative).read_text(encoding='utf-8')
            strings={n.value for n in ast.walk(ast.parse(text)) if isinstance(n,ast.Constant) and isinstance(n.value,str)}
            self.assertFalse(RETIRED.intersection(strings))
        entry=ast.parse((ROOT/'__init__.py').read_text(encoding='utf-8'))
        prefs=next(n for n in entry.body if isinstance(n,ast.ClassDef) and n.name=='BLENDCAP_AddonPreferences')
        fields={n.target.id for n in prefs.body if isinstance(n,ast.AnnAssign)}
        self.assertFalse(fields.intersection({'capture_backend','refine_hands','disable_timeline_autosnap'}))


@unittest.skipUnless(bpy is not None,'Blender required')
class RetirementLifecycleTests(unittest.TestCase):
    def test_guarded_fresh_addon_import_and_lifecycle_preserves_current_commands(self):
        attempts=[]
        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self,fullname,path=None,target=None):
                if fullname.split('.')[0] in ('ultralytics','thop') or fullname.endswith('build_detector'):
                    attempts.append(fullname)
                    raise AssertionError('Retired dependency: '+fullname)
        blocker=Block();original=builtins.__import__
        def guarded(name,*args,**kwargs):
            if name.split('.')[0] in ('ultralytics','thop') or name.endswith('build_detector'):
                attempts.append(name);raise AssertionError('Retired dependency: '+name)
            return original(name,*args,**kwargs)
        sys.meta_path.insert(0,blocker)
        try:
            # Use a unique package to exercise actual source imports even when
            # another regression imported the normal test addon earlier.
            with mock.patch.object(builtins,'__import__',side_effect=guarded):
                spec=importlib.util.spec_from_file_location('_vta_retirement_smoke',ROOT/'__init__.py',
                    submodule_search_locations=[str(ROOT)])
                addon=importlib.util.module_from_spec(spec);sys.modules[spec.name]=addon
                spec.loader.exec_module(addon)
                addon.register()
                try:
                    classes=addon._registration.classes
                    ids={getattr(c,'bl_idname','') for c in classes}
                    self.assertFalse(RETIRED.intersection(ids))
                    for command in ('vta_analyze','vta_review_assign','vta_update_tracking',
                                    'vta_generate_animation','vta_camera_cleanup','vta_retarget'):
                        self.assertIn('blendcap.'+command,ids)
                    self.assertFalse(hasattr(bpy.types,'BLENDCAP_OT_generate_mocap'))
                    capture=importlib.import_module(spec.name+'.blendcap.mocap')
                    for name in ('run_mocap_thread','monitor_progress_timer','load_proxy_to_vse','_run_fallback_overlay'):
                        self.assertFalse(hasattr(capture,name))
                    self.assertTrue(callable(capture._revert_proxy_strip))
                    self.assertTrue(callable(capture.run_bvh_thread))
                    ui=importlib.import_module(spec.name+'.blendcap.vta_review_ui')
                    self.assertTrue(callable(ui.BLENDCAP_MT_vta_commands.draw))
                finally:
                    addon.unregister()
                self.assertEqual(attempts,[])
        finally:
            sys.meta_path.remove(blocker)
            for name in tuple(sys.modules):
                if name=='_vta_retirement_smoke' or name.startswith('_vta_retirement_smoke.'):
                    del sys.modules[name]


if __name__=='__main__':
    unittest.main()
