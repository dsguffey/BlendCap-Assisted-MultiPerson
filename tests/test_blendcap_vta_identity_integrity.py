"""Focused existing one-box worker contract; fake estimator, no model inference."""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
import numpy as np
from blendcap_vta_reconstruct_worker import infer_reviewed_pose


class WorkerIdentityIntegrityTests(unittest.TestCase):
    def estimator(self):
        calls=[]
        def process(rgb,*,bboxes,inference_type,**kwargs):
            calls.append(bboxes.copy());marker=float(bboxes[0,0])
            return [dict(bbox=bboxes[0].copy(),pred_global_rots=np.tile(np.eye(3),(22,1,1)),
                pred_joint_coords=np.full((22,3),marker),pred_cam_t=np.array([marker,0,2]),focal_length=1000.)]
        return SimpleNamespace(process_one_image=process),calls

    def infer(self,estimator,box):
        return infer_reviewed_pose(estimator,np,SimpleNamespace(inference_mode=nullcontext),
            np.zeros((300,500,3),dtype=np.uint8),dict(box_xyxy=box),300)

    def test_distinct_reviewed_boxes_make_distinct_calls_and_payloads(self):
        estimator,calls=self.estimator()
        a=self.infer(estimator,(10,20,100,200));b=self.infer(estimator,(40,20,140,200))
        self.assertEqual(len(calls),2);self.assertFalse(np.array_equal(calls[0],calls[1]))
        self.assertFalse(np.array_equal(a['pred_joint_coords'],b['pred_joint_coords']))
        self.assertIsNot(a,b)

    def test_same_merged_box_cannot_gain_identity_from_character_label_not_in_api(self):
        estimator,calls=self.estimator()
        # Hypothetical shared-candidate routing would send identical inputs.
        # Distinct caller Character IDs cannot disambiguate this worker API.
        a=self.infer(estimator,(10,20,150,200));b=self.infer(estimator,(10,20,150,200))
        self.assertEqual(len(calls),2);self.assertTrue(np.array_equal(calls[0],calls[1]))
        self.assertTrue(np.array_equal(a['pred_joint_coords'],b['pred_joint_coords']))

    def test_wrong_box_response_still_fails_closed(self):
        estimator,_=self.estimator();original=estimator.process_one_image
        def wrong(*args,**kwargs):
            result=original(*args,**kwargs);result[0]['bbox'][0]+=1;return result
        estimator.process_one_image=wrong
        with self.assertRaisesRegex(RuntimeError,'different candidate box'):
            self.infer(estimator,(10,20,100,200))
