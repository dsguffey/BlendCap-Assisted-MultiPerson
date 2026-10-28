"""Bounded neutral presentation parity, caching and isolated review workflow."""
import dataclasses,json,subprocess,tempfile,unittest,copy,threading
from pathlib import Path
from unittest import mock
from tests.test_blendcap_vta_engine import P,K,PY,ENTRY
from blendcap_vta_engine import EngineClient,EngineEndpoint
from blendcap_vta_engine_review import EngineReviewBackend,read_observation
from blendcap_vta_review import ReviewLocation,VtaReviewUiError
from vta_protocol import observation as dto

class ObservationTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        code='''import sys,hashlib,json,dataclasses
from pathlib import Path
sys.path[:0]=[sys.argv[1],sys.argv[2]]
import vta_core as c
from tests.legacy_review_backend import LegacyReviewBackend
from blendcap_vta_review import ReviewLocation
root=Path(sys.argv[3]);video=root/'video.mp4';video.write_bytes(b'local-test-video')
p=c.ProjectState('p');s=c.SourceReference('s',24.,0,99,hashlib.sha256(video.read_bytes()).hexdigest(),16,16);p.add_source(s)
p.create_character('Amy',character_id='amy');p.create_character('Zed',character_id='zed')
cache=c.AnalysisCache('a',s,c.ProviderDescriptor('test','test','test'));c.register_analysis_cache_revision(p,cache)
for n in range(3):cache.record_frame(c.ProviderFrameResult(n,n/24.,(c.ProviderCandidate('obs'+str(n),(1.,1.,8.,12.),.8),)))
c.save_project_state(p,root);c.save_analysis_cache(cache,root)
backend=LegacyReviewBackend(c);location=ReviewLocation(root,video,0,1)
context=backend._load_context(location)
frames=[dataclasses.asdict(backend.frame_from_context(context,ReviewLocation(root,video,n,n+1))) for n in range(100)]
(root/'baseline.json').write_text(json.dumps(dict(frames=frames,timeline=dataclasses.asdict(backend.timeline_from_context(context)))))
'''
        done=subprocess.run([str(PY),'-I','-B','-c',code,str(K),str(P),str(self.root)],capture_output=True,text=True,timeout=30)
        self.assertEqual(done.returncode,0,done.stderr)
        self.location=ReviewLocation(self.root,self.root/'video.mp4',0,1)
        self.backend=EngineReviewBackend(EngineClient(EngineEndpoint(PY,ENTRY)))
    def test_all_frame_and_timeline_dtos_match_accepted_public_formatting(self):
        observation=self.backend.observe(self.location)
        baseline=json.loads((self.root/'baseline.json').read_text())
        frames=[dataclasses.asdict(observation.frame(dataclasses.replace(self.location,source_frame=n,blender_frame=n+1))) for n in range(100)]
        self.assertEqual(json.loads(json.dumps(frames)),baseline['frames'])
        self.assertEqual(json.loads(json.dumps(dataclasses.asdict(observation.timeline))),baseline['timeline'])
        with mock.patch.object(self.backend.client,'call',side_effect=AssertionError('Repeated UI query started IPC')):
            for n in range(100):self.backend.query(dataclasses.replace(self.location,source_frame=n,blender_frame=n+1))
    def test_purpose_artifact_rejects_unknown_fields_bad_types_and_missing_frames(self):
        value=self.backend.observe(self.location).value
        variants=[]
        for update in ({'extra':1},{'frames':[]},{'characters':'invalid'},{'coverage':[10,1]}):
            variants.append({**copy.deepcopy(value),**update})
        altered=copy.deepcopy(value);altered['frames'][0]['candidates'][0]['box_xyxy']=[1.,2.,float('nan'),4.];variants.append(altered)
        for variant in variants:
            with self.subTest(keys=list(variant)),self.assertRaises((ValueError,TypeError)):
                dto.loads(json.dumps(variant).encode())
    def test_async_observer_starts_one_worker_and_adopts_only_ready_data(self):
        observed=self.backend.observe(self.location);self.backend.stamp=None
        entered=threading.Event();release=threading.Event()
        def fetch(location):entered.set();release.wait(10);return observed
        with mock.patch.object(self.backend,'_fetch',side_effect=fetch) as calls:
            self.assertIs(self.backend.observe_async(self.location),observed)
            self.assertTrue(entered.wait(5))
            for _ in range(40):self.assertIs(self.backend.observe_async(self.location),observed)
            self.assertEqual(calls.call_count,1)
            release.set();self.backend._job.join(5)
            self.assertIs(self.backend.observe_async(self.location),observed)
            self.assertIsNone(self.backend._job)
    def test_host_review_workflow_has_no_private_imports(self):
        code='''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from blendcap_vta_process_probe import private_import_guard
from blendcap_vta_engine import EngineClient,EngineEndpoint
from blendcap_vta_engine_review import EngineReviewBackend
from blendcap_vta_review import ReviewLocation
root=Path(sys.argv[3]);location=ReviewLocation(root,root/'video.mp4',0,1)
with private_import_guard():
 b=EngineReviewBackend(EngineClient(EngineEndpoint(Path(sys.executable),Path(sys.argv[2]))))
 b.observe(location);b.apply(location,observation_id='obs0',character_id='amy')
 request,_=b.prepare_reassignment(location,observation_id='obs0',character_id='zed');b.apply_confirmed_reassignment(location,request)
 request,_=b.prepare_hard_state(location,character_id='zed',state='occluded');b.apply_hard_state(location,request,confirmed=True)
 assert b.query(location).characters[1].decision_state=='occluded'
 request=b.prepare_management(location,'clear-all-review-keys-on-frame',source_id='s',frame=0);b.apply_management(location,request,confirmed=True)
 b.restore_last(location)
 assert b.query(location).characters[1].decision_state=='occluded'
 assert not any(n=='vta_core' or n.startswith('vta_core.') for n in sys.modules)
 print('isolated review passed')
'''
        done=subprocess.run([str(PY),'-I','-B','-c',code,str(P),str(ENTRY),str(self.root)],capture_output=True,text=True,timeout=45)
        self.assertEqual(done.returncode,0,done.stdout+done.stderr);self.assertIn('isolated review passed',done.stdout)
