"""Real isolated child review mutations; no private imports in the public host."""
import hashlib,json,subprocess,tempfile,unittest,shutil
from uuid import uuid4
from pathlib import Path
from tests.test_blendcap_vta_engine import K,PY,ENTRY
from blendcap_vta_engine import EngineClient,EngineEndpoint,EngineError
from tests import test_blendcap_vta_mutations as fault_tests

class EngineReviewProcessTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        code="import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from tests.test_apply_review_decision import project_cache_request;from vta_core import save_project_state,save_analysis_cache;p,c,_=project_cache_request();save_project_state(p,Path(sys.argv[2]));save_analysis_cache(c,Path(sys.argv[2]))"
        subprocess.run([str(PY),'-I','-B','-c',code,str(K),str(self.root)],check=True,capture_output=True)
        self.client=EngineClient(EngineEndpoint(PY,ENTRY))
    def payload(self):
        state=self.client.call('project-state',dict(companion_root=str(self.root)))
        return dict(companion_root=str(self.root),project_id=state['project_id'],expected_revision=state['revision'],
            source_id='source-1',source_frame=5,analysis_revision_id='analysis-1',
            cache_sha256=hashlib.sha256((self.root/'analysis/analysis-1.json').read_bytes()).hexdigest(),character_id='character-amy')
    def test_assignment_hard_state_and_status_through_real_child(self):
        result=self.client.call('review-assign',dict(**self.payload(),observation_id='selected'))
        status=self.client.call('mutation-status',dict(companion_root=str(self.root),mutation_id=result['request_id']))
        self.assertEqual(status['receipt'],result)
        result=self.client.call('review-hard-state',dict(**self.payload(),state='occluded',confirmed=True))
        self.assertEqual(result['details']['character_id'],'character-amy')
        self.assertEqual(json.loads((self.root/'project_state.json').read_text())['identity_keyframes'][0]['state'],'occluded')
    def test_malformed_new_fields_are_refused_before_spawn(self):
        for extra in ({'state':'visible','confirmed':True},{'state':'occluded','confirmed':1},
                      {'state':'occluded','confirmed':False,'unexpected':True}):
            payload=self.payload()
            with self.subTest(extra=extra),self.assertRaises(EngineError):
                self.client.call('review-hard-state',{**payload,**extra})
            self.assertIsNone(self.client.last_pid)

    def management(self):
        value=self.payload()
        return {k:v for k,v in value.items() if k in ('companion_root','project_id','expected_revision','source_id','character_id')}|dict(
            frame=5,action='clear-assignment',keyframe_ids=[],confirmed=True,session_id='1'*32)

    def test_selected_assignment_key_delete_matches_clear_and_protected_restore(self):
        self.client.call('review-assign',dict(**self.payload(),observation_id='selected'))
        before=(self.root/'project_state.json').read_bytes()
        key=json.loads(before)['identity_keyframes'][0]['keyframe_id']
        original=self.root
        outcomes=[]
        for action in ('clear-assignment','clear-selected-review-keys'):
            destination=original/action
            destination.mkdir()
            shutil.copyfile(original/'project_state.json',destination/'project_state.json')
            shutil.copytree(original/'analysis',destination/'analysis')
            self.root=destination
            payload=self.management()
            payload.update(action=action,keyframe_ids=[key] if action!='clear-assignment' else [])
            result=self.client.call('review-management',payload)
            after=(destination/'project_state.json').read_bytes()
            self.assertNotEqual(after,before)
            self.assertFalse(any(k['keyframe_id']==key for k in json.loads(after)['identity_keyframes']))
            outcomes.append(after)
            self.client.call('review-restore-last',dict(companion_root=str(destination),
                project_id=result['project_id'],expected_revision=result['after']['revision'],
                restore_id=result['request_id'],session_id='1'*32))
            self.assertEqual((destination/'project_state.json').read_bytes(),before)
            self.assertFalse((destination/'.vta-engine/restore/slot.json').exists())
        self.root=original
        self.assertEqual(outcomes[0],outcomes[1])

    def test_destructive_crash_save_as_recovery_and_restore_consume(self):
        self.client.call('review-assign',dict(**self.payload(),observation_id='selected'))
        before=(self.root/'project_state.json').read_bytes();identity=uuid4().hex
        endpoint=fault_tests.MutationProcessTests.crash_endpoint(self,'published')
        with self.assertRaises(EngineError):
            EngineClient(endpoint).call('review-management',self.management(),request_id=identity)
        from blendcap_vta_project_copy import CopyPlan
        copy_folder=tempfile.TemporaryDirectory();self.addCleanup(copy_folder.cleanup)
        destination=Path(copy_folder.name)/'independent';plan=CopyPlan(self.root,destination);plan.project()
        self.assertTrue(any(str(p).replace('\\','/').startswith('.vta-engine/restore/') for p in plan.files))
        for relative,(source,_digest) in plan.files.items():
            target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        result=self.client.call('mutation-status',dict(companion_root=str(destination),mutation_id=identity))['receipt']
        restore_id=uuid4().hex;payload=dict(companion_root=str(destination),project_id=result['project_id'],
            expected_revision=result['after']['revision'],restore_id=identity,session_id='1'*32)
        with self.assertRaises(EngineError):EngineClient(endpoint).call('review-restore-last',payload,request_id=restore_id)
        status=self.client.call('mutation-status',dict(companion_root=str(destination),mutation_id=restore_id))
        self.assertEqual(status['state'],'applied');self.assertEqual((destination/'project_state.json').read_bytes(),before)
        self.assertFalse((destination/'.vta-engine/restore/slot.json').exists())
        self.assertNotEqual((self.root/'project_state.json').read_bytes(),before)

    def test_request_identity_and_third_state_are_never_overwritten(self):
        payload=dict(**self.payload(),observation_id='selected');identity=uuid4().hex
        result=self.client.call('review-assign',payload,request_id=identity)
        self.assertEqual(self.client.call('review-assign',payload,request_id=identity),result)
        with self.assertRaises(EngineError) as caught:
            self.client.call('review-assign',{**payload,'character_id':'character-zed'},request_id=identity)
        self.assertEqual(caught.exception.code,'IDEMPOTENCY_CONFLICT')
        identity=uuid4().hex;endpoint=fault_tests.MutationProcessTests.crash_endpoint(self,'linked')
        with self.assertRaises(EngineError):
            EngineClient(endpoint).call('review-hard-state',dict(**self.payload(),state='occluded',confirmed=True),request_id=identity)
        path=self.root/'project_state.json';third=path.read_bytes()+b'\n';path.write_bytes(third)
        with self.assertRaises(EngineError) as caught:
            self.client.call('mutation-status',dict(companion_root=str(self.root),mutation_id=identity))
        self.assertEqual(caught.exception.code,'RECOVERY_REQUIRED');self.assertEqual(path.read_bytes(),third)
