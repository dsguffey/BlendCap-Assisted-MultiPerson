"""Committed projection reuse, hostile optional data and real one-shot children."""
import copy,hashlib,json,os,subprocess,unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4
from tests import test_blendcap_vta_engine_observation as observation_fixtures
from tests.test_blendcap_vta_engine import PY,ENTRY
from blendcap_vta_engine import EngineClient,EngineEndpoint,resolve_engine,EngineError
from blendcap_vta_engine_review import EngineReviewBackend
from blendcap_vta_runtime import load_runtime
import blendcap_vta_engine as engine
from vta_protocol import review_reply

class FastReviewTests(unittest.TestCase):
    setUp=observation_fixtures.ObservationTests.setUp

    def start_backend(self):
        review=load_runtime().get('review_python',PY)
        self.client=EngineClient(EngineEndpoint(PY,ENTRY,review))
        self.review=EngineReviewBackend(self.client);self.review.observe(self.location)
        return self.review

    def send(self,items=None,action='assign'):
        observed,payload=self.review._cache_payload(self.location);payload.pop('source_frame')
        payload.update(action=action,character_id='amy' if action=='assign' else None,
            items=items or [dict(frame=0,observation_id='obs0')],confirmed=False,session_id=self.review.session_id)
        return self.review._send(self.location,'review-batch',payload,observed)

    def test_one_batch_and_suppression_use_one_child_and_one_observation(self):
        backend=self.start_backend();calls=[];processes=[];original=self.client.call;popen=engine.subprocess.Popen
        def call(op,*a,**kw):
            value=original(op,*a,**kw);calls.append(op);return value
        def launch(*a,**kw):
            self.assertFalse(kw['shell'])
            if os.name=='nt':self.assertTrue(kw['creationflags']&subprocess.CREATE_NO_WINDOW)
            value=popen(*a,**kw);processes.append(value);return value
        with mock.patch.object(self.client,'call',side_effect=call),mock.patch.object(engine.subprocess,'Popen',side_effect=launch):
            for action,items in [('assign',[dict(frame=0,observation_id='obs0')]),
                ('assign',[dict(frame=0,observation_id='obs0'),dict(frame=1,observation_id='obs1')]),
                ('suppress',[dict(frame=0,observation_id='obs0'),dict(frame=1,observation_id='obs1')])]:
                calls.clear();count=len(processes);result=self.send(items,action)
                self.assertEqual(calls,['review-batch']);self.assertEqual(len(processes),count+1)
                self.assertEqual(backend.observation.value['checkpoint'],result['after'])
                self.assertEqual(self.client.last_python,self.client.endpoint.review_python)
                self.assertTrue(hasattr(backend.observation,'verified_project'))
                self.assertIsNone(backend._prefetched)
                backend.restore_last(self.location)
        for process in processes:
            self.assertIsNotNone(process.poll())
            self.assertTrue(all(s.closed for s in (process.stdin,process.stdout,process.stderr)))

    def test_review_runtime_does_not_replace_other_control_runtime(self):
        self.start_backend();self.client.call('project-state',dict(companion_root=str(self.root)))
        self.assertEqual(self.client.last_python,PY)
        self.review.observe(self.location,force=True)
        self.assertEqual(self.client.last_python,self.client.endpoint.review_python)
        self.send(action='suppress');paths=[];launch=engine.subprocess.Popen
        def capture(argv,**kwargs):paths.append(argv[0]);return launch(argv,**kwargs)
        with mock.patch.object(engine.subprocess,'Popen',side_effect=capture):self.send(action='restore')
        self.assertEqual(paths[0],str(PY))

    def test_existing_reassignment_and_suppression_share_guarded_contract(self):
        backend=self.start_backend();original=self.client.call
        with mock.patch.object(self.client,'call',wraps=original) as calls:
            backend.apply(self.location,observation_id='obs0',character_id='amy')
            request,_=backend.prepare_reassignment(self.location,observation_id='obs0',character_id='zed')
            backend.apply_confirmed_reassignment(self.location,request)
            request=backend.prepare_suppression(self.location,'obs0',suppressed=True)
            backend.apply_suppression(self.location,request,confirmed=True)
        self.assertEqual([c.args[0] for c in calls.call_args_list],['review-assign','review-reassign','candidate-suppress'])
        self.assertTrue(backend.query(self.location,show_suppressed=True).candidates[0].suppressed)

    def test_invalid_optional_metadata_falls_back_without_second_mutation(self):
        self.start_backend();original=self.client.call;calls=[]
        edits=[lambda v:v.update(request_id='0'*32),lambda v:v.update(request_sha256='0'*64),
            lambda v:v.update(head_sha256='0'*64),lambda v:v.update(extra=True),
            lambda v:v['observation']['checkpoint'].update(project_id='foreign'),
            lambda v:v['observation'].update(sha256='0'*64),
            lambda v:v['inputs']['project'].update(mtime_ns=0),lambda v:v.update(version=99)]
        for edit in edits:
            def call(op,*a,**kw):
                value=original(op,*a,**kw);calls.append(op)
                if op=='review-batch':edit(self.client.last_presentation)
                return value
            with self.subTest(edit=edit),mock.patch.object(self.client,'call',side_effect=call):
                calls.clear();result=self.send()
                self.assertEqual(calls,['review-batch','review-observation'])
                self.assertEqual(self.review.observation.value['checkpoint'],result['after'])
            self.review.restore_last(self.location)

    def test_malformed_and_wrong_effect_artifacts_fall_back(self):
        self.start_backend();original=self.client.call
        for mode in ('schema','effect'):
            calls=[]
            def call(op,*a,**kw):
                value=original(op,*a,**kw);calls.append(op)
                if op=='review-batch':
                    manifest=self.client.last_presentation['observation'];path=self.root/'.vta-engine/views'/(manifest['artifact_id']+'.json')
                    artifact=json.loads(path.read_bytes())
                    if mode=='schema':artifact['extra']=True
                    else:artifact['frames'][0]['character_assignments'][0]['character_id']='zed'
                    raw=json.dumps(artifact).encode();path.write_bytes(raw)
                    manifest.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
                return value
            with self.subTest(mode=mode),mock.patch.object(self.client,'call',side_effect=call):
                result=self.send();self.assertEqual(calls,['review-batch','review-observation'])
                self.assertEqual(self.review.observation.value['checkpoint'],result['after'])
            self.review.restore_last(self.location)

    def test_intervening_mutation_never_adopts_older_view(self):
        self.start_backend();original=self.client.call;other=EngineClient(self.client.endpoint)
        def call(op,*a,**kw):
            result=original(op,*a,**kw)
            if op=='review-batch':other.call('character-rename',dict(companion_root=str(self.root),project_id='p',expected_revision=result['after']['revision'],character_id='amy',display_name='Changed'))
            return result
        with mock.patch.object(self.client,'call',side_effect=call):result=self.send()
        view=self.review.observation
        self.assertGreater(view.value['checkpoint']['revision']['number'],result['after']['revision']['number'])
        self.assertEqual(next(c.display_name for c in view.characters if c.character_id=='amy'),'Changed')
        self.assertIsNone(self.review.last_restore_id)

    def test_preserved_stat_tokens_do_not_hide_changed_project_or_head(self):
        for target in ('project','head'):
            self.start_backend();original=self.client.call
            def call(op,*a,**kw):
                result=original(op,*a,**kw)
                if op=='review-batch':
                    path=self.root/('project_state.json' if target=='project' else '.vta-engine/head.json');before=path.stat();raw=path.read_bytes()
                    changed=(raw.replace(b'Amy',b'Eve') if target=='project' else raw.replace(b'"number":1',b'"number":2'))
                    self.assertNotEqual(raw,changed);self.assertEqual(len(raw),len(changed))
                    path.write_bytes(changed);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
                return result
            with self.subTest(target=target),mock.patch.object(self.client,'call',side_effect=call):result=self.send()
            self.assertFalse(hasattr(self.review.observation,'verified_project'))
            if target=='project':self.assertEqual(next(c.display_name for c in self.review.observation.characters if c.character_id=='amy'),'Eve')
            # Independent fresh fixture for the second preserved-token attack.
            if target=='project':self.doCleanups();observation_fixtures.ObservationTests.setUp(self)

    def test_force_after_success_is_still_a_fresh_query(self):
        self.start_backend();self.send();original=self.client.call
        with mock.patch.object(self.client,'call',wraps=original) as call:self.review.observe(self.location,force=True)
        self.assertEqual([c.args[0] for c in call.call_args_list],['review-observation'])

    def test_generation_planning_and_tracking_update_immediately_after_assignment(self):
        from blendcap_vta_jobs import EngineGenerationBackend
        self.start_backend()
        receipt=self.send([dict(frame=0,observation_id='obs0'),dict(frame=2,observation_id='obs2')])
        generation=EngineGenerationBackend(self.client,self.review)
        handle,plan=generation.prepare(self.location,['amy'],[0,2])
        self.assertEqual(plan['checkpoint'],receipt['after'])
        self.assertEqual(plan['settings']['depth_mode'],'FIXED')
        self.assertEqual(self.client.last_python,PY)
        self.assertEqual(handle.call('job-cancel')['state'],'cancelled')
        outcomes,view,timeline=self.review.update_tracking_for_characters(self.location,['amy'])
        self.assertEqual(len(outcomes),1)
        self.assertIsNotNone(outcomes[0].update,outcomes[0].error)
        self.assertEqual(self.client.last_python,self.client.endpoint.review_python)  # Fresh observation after normal control.
        self.assertGreater(self.review.observation.value['checkpoint']['revision']['number'],receipt['after']['revision']['number'])

    def test_process_death_before_commit_after_commit_and_before_projection(self):
        from tests import test_blendcap_vta_mutations as faults
        for phase,expected in [('linked','not-applied'),('published','applied'),('presentation','applied')]:
            self.start_backend();observed,payload=self.review._cache_payload(self.location);payload.pop('source_frame')
            payload.update(action='assign',character_id='amy',items=[dict(frame=0,observation_id='obs0')],confirmed=False,session_id=self.review.session_id,
                presentation=dict(source_path=str(self.location.source_path),source_frame=0))
            endpoint=faults.MutationProcessTests.crash_endpoint(self,phase);identity=uuid4().hex
            with self.subTest(phase=phase),self.assertRaises(EngineError):EngineClient(endpoint).call('review-batch',payload,request_id=identity)
            status=self.client.call('mutation-status',dict(companion_root=str(self.root),mutation_id=identity))
            self.assertEqual(status['state'],expected)
            fresh=self.review.observe(self.location,force=True)
            self.assertEqual(len(fresh.value['frames'][0]['character_assignments']),int(expected=='applied'))
            if expected=='applied':self.assertNotIn('presentation',status['receipt'])
            if phase!='presentation':self.doCleanups();observation_fixtures.ObservationTests.setUp(self)

class RuntimeSelectionTests(unittest.TestCase):
    def test_core_override_cannot_bypass_trusted_runtime_or_guess_python(self):
        with mock.patch.object(engine,'load_runtime',side_effect=ValueError('no config')):
            with self.assertRaises(EngineError):resolve_engine(ENTRY.parent)
        runtime=load_runtime()
        with mock.patch.object(engine,'load_runtime',return_value=runtime):
            endpoint=resolve_engine(ENTRY.parent)
            self.assertEqual(endpoint.review_python,runtime.get('review_python'))
