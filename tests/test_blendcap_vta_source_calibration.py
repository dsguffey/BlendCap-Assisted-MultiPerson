"""Derived calibration cache and Blender mixed-output transactional boundaries."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from contextlib import nullcontext
from unittest import mock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'Video-to-Animation'))
try:
    from vta_core import source_calibration as calibration
except ImportError:
    calibration=None
import blendcap_vta_reconstruct_worker as worker


@unittest.skipUnless(calibration is not None,'private calibration runtime required')
class CalibrationTests(unittest.TestCase):
    def identity(self, source='a'*64, provider=None):
        return calibration.calibration_identity(source,1000,600,provider or {'model':'one','config':'v1'})

    def test_cache_reuses_and_source_model_config_algorithm_invalidate(self):
        with tempfile.TemporaryDirectory() as folder:
            compute=mock.Mock(return_value=[[500.,0,500],[0,500.,300],[0,0,1]])
            first,created=calibration.load_or_compute_calibration(folder,self.identity(),compute)
            self.assertTrue(created)
            again,created=calibration.load_or_compute_calibration(folder,self.identity(),compute)
            self.assertFalse(created)
            self.assertEqual(first,again)
            compute.assert_called_once()
            for identity in (self.identity(source='b'*64),self.identity(provider={'model':'two'}),
                             self.identity(provider={'model':'one','config':'v2'})):
                changed,created=calibration.load_or_compute_calibration(folder,identity,compute)
                self.assertTrue(created)
                self.assertNotEqual(changed['key'],first['key'])
            with mock.patch.object(calibration,'CALIBRATION_ALGORITHM','future-v2'):
                changed,created=calibration.load_or_compute_calibration(folder,self.identity(),compute)
                self.assertTrue(created)
                self.assertNotEqual(changed['key'],first['key'])

    def test_invalid_or_corrupt_record_fails_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            identity=self.identity()
            for matrix in ([[float('nan'),0,500],[0,500,300],[0,0,1]],
                           [[500,0,501],[0,500,300],[0,0,1]],[[500,0,500],[0,400,300],[0,0,1]]):
                with self.assertRaises(calibration.StateModelError):
                    calibration.load_or_compute_calibration(folder,identity,lambda:matrix)
                self.assertEqual(list(Path(folder).rglob('*.json')),[])
            record,_=calibration.load_or_compute_calibration(folder,identity,lambda:[[500,0,500],[0,500,300],[0,0,1]])
            path=next(Path(folder).rglob('*.json'))
            record['intrinsics'][0][0]=600
            path.write_text(json.dumps(record))
            before=path.read_bytes();compute=mock.Mock()
            with self.assertRaises(calibration.StateModelError):
                calibration.load_or_compute_calibration(folder,identity,compute)
            compute.assert_not_called();self.assertEqual(before,path.read_bytes())

    def test_fixed_matrix_shared_between_characters_and_no_default_fallback(self):
        import numpy as np
        source=SimpleNamespace(video_sha256='a'*64,require_dimensions=lambda:(1000,600))
        torch=SimpleNamespace(as_tensor=np.asarray,float32=np.float32,inference_mode=nullcontext)
        fov=mock.Mock(return_value=np.asarray([[[500,0,500],[0,500,300],[0,0,1]]]))
        estimator=SimpleNamespace(fov_estimator=SimpleNamespace(get_cam_intrinsics=fov))
        decoded=mock.Mock(return_value=np.zeros((600,1000,3),dtype=np.uint8))
        with tempfile.TemporaryDirectory() as folder:
            _,record,k,_=worker.fixed_source_calibration(folder,source,{'provider':'test'},estimator,np,torch,decoded)
            decoded.assert_called_once_with(0);fov.assert_called_once()
            pose={'bbox':np.array([0,0,1000,600]),'pred_global_rots':np.tile(np.eye(3),(2,1,1)),
                  'pred_joint_coords':np.zeros((2,3)),'pred_cam_t':np.ones(3),'focal_length':500.}
            estimator.process_one_image=mock.Mock(return_value=[pose])
            for character in ('White','Yellow'):
                for frame in (19,22,30):
                    worker.infer_reviewed_pose(estimator,np,torch,decoded.return_value,
                        {'box_xyxy':[0,0,1000,600]},600,cam_int=k)
            for call in estimator.process_one_image.call_args_list:
                self.assertIs(call.kwargs['cam_int'],k)
            estimator.fov_estimator=None
            with self.assertRaisesRegex(RuntimeError,'no default or per-frame fallback'):
                worker.fixed_source_calibration(folder,source,{'provider':'absent'},estimator,np,torch,decoded)

    def test_fingerprint_distinguishes_variants_and_rejects_bounded_legacy(self):
        with tempfile.TemporaryDirectory() as folder:
            identity=self.identity()
            record,_=calibration.load_or_compute_calibration(folder,identity,lambda:[[500,0,500],[0,500,300],[0,0,1]])
            first=calibration.reconstruction_record('FIRST',identity,record)
            every=calibration.reconstruction_record('EVERY',identity)
            fp=calibration.reconstruction_fingerprint(first,'a'*64)
            self.assertNotEqual(fp,calibration.reconstruction_fingerprint(every))
            for previous in (calibration.LEGACY_FINGERPRINT,every['fingerprint'],'future-variant'):
                with self.assertRaisesRegex(calibration.StateModelError,'Regenerate the whole source'):
                    calibration.require_compatible_reconstruction(fp,[previous],bounded=True)
                calibration.require_compatible_reconstruction(fp,[previous],bounded=False)
            calibration.require_compatible_reconstruction(fp,[fp,fp],bounded=True)
            bad=copy.deepcopy(first);bad['calibration']['intrinsics'][0][0]=501
            with self.assertRaises(calibration.StateModelError):calibration.reconstruction_fingerprint(bad)


try:
    import bpy
except ImportError:
    bpy=None


@unittest.skipUnless(bpy is not None and calibration is not None,'Blender and private calibration runtime required')
class BlenderCalibrationTests(unittest.TestCase):
    def setUp(self):
        from tests.test_blendcap_vta_object_placement import ObjectPlacementTests
        ObjectPlacementTests.setUpClass()
        self.fixture=ObjectPlacementTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.backend._core.reconstruction_fingerprint=calibration.reconstruction_fingerprint
        self.fixture.backend._core.require_compatible_reconstruction=calibration.require_compatible_reconstruction

    def calibrated_report(self,bounded,source_frames=None):
        import numpy as np
        fixture=self.fixture;path=fixture.make_report(bounded,source_frames=source_frames)
        report=json.loads(path.read_text())
        identity=calibration.calibration_identity(report['plan']['source_video_sha256'],1000,600,{'model':'one'})
        record,_=calibration.load_or_compute_calibration(fixture.root,identity,
            lambda:[[500,0,500],[0,500,300],[0,0,1]])
        report['reconstruction']=calibration.reconstruction_record('FIRST',identity,record)
        for character in report['characters']:
            raw_path=path.parent/character['npz']
            with np.load(raw_path) as raw:data={key:raw[key] for key in raw.files}
            data['reconstruction_fingerprint']=np.asarray(report['reconstruction']['fingerprint'])
            np.savez(raw_path,**data)
            character['npz_sha256']=fixture.output._hash(raw_path)
        path.write_text(json.dumps(report))
        return path,report['reconstruction']['fingerprint']

    def test_legacy_bounded_rejected_without_any_blender_mutation(self):
        f=self.fixture;before=[f.curves(r) for r in f.rigs];objects=set(bpy.data.objects)
        path,_=self.calibrated_report(True)
        with self.assertRaisesRegex(calibration.StateModelError,'Regenerate the whole source'):
            f.output.import_reviewed_output(path,f.location,f.backend)
        self.assertEqual(before,[f.curves(r) for r in f.rigs]);self.assertEqual(objects,set(bpy.data.objects))

    def test_full_migration_compatible_bounded_preserves_outside_and_metadata_reload(self):
        f=self.fixture;path,fp=self.calibrated_report(False)
        rigs,_=f.output.import_reviewed_output(path,f.location,f.backend)
        before=[f.curves(r) for r in rigs];camera=bpy.context.scene.camera
        path,again=self.calibrated_report(True);self.assertEqual(fp,again)
        f.output.import_reviewed_output(path,f.location,f.backend)
        for rig,keys in zip(rigs,before):
            self.assertEqual(rig['vta_reconstruction_fingerprint'],fp)
            for channel,settings in keys.items():
                outside=[p for p in settings[-1] if p[0][0]<3 or p[0][0]>4]
                after=f.curves(rig)[channel][-1]
                self.assertTrue(all(p in after for p in outside))
            self.assertEqual(rig.parent.name,'VTA_SceneRoot')
        self.assertEqual(camera.data['vta_reconstruction_fingerprint'],fp)
        saved=f.root/'calibration.blend'
        bpy.data.libraries.write(str(saved),{*rigs,camera,rigs[0].parent})
        with bpy.data.libraries.load(str(saved)) as (source,target):target.objects=source.objects
        restored=target.objects
        try:
            for obj in restored:
                if obj.type=='ARMATURE':self.assertEqual(obj['vta_reconstruction_fingerprint'],fp)
                if obj.type=='CAMERA':self.assertEqual(obj.data['vta_reconstruction_fingerprint'],fp)
        finally:
            for obj in restored:bpy.data.objects.remove(obj,do_unlink=True)
