"""Mutation protocol and actual process-failure coverage, no private host imports."""
import json
from pathlib import Path
import subprocess
import tempfile
import shutil
from types import SimpleNamespace
from unittest import mock
import unittest
from uuid import uuid4
from tests.test_blendcap_vta_engine import P,K,PY,ENTRY,fixture
from blendcap_vta_engine import EngineClient,EngineEndpoint,EngineError
import vta_protocol as wire


def project(root):
    fixture(root);path=root/'project_state.json';raw=json.loads(path.read_text())
    raw['characters']=[dict(character_id='a',display_name='Alice'),dict(character_id='b',display_name='Bob')]
    path.write_text(json.dumps(raw),encoding='utf-8')


class MutationProcessTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name);project(self.root)
        self.client=EngineClient(EngineEndpoint(PY,ENTRY));self.state=self.client.call('character-state',dict(companion_root=str(self.root),character_id='a'))

    def payload(self,name='Changed'):
        return dict(companion_root=str(self.root),project_id='test',character_id='a',display_name=name,expected_revision=self.state['checkpoint']['revision'])

    def test_normal_duplicate_conflict_and_status(self):
        identity=uuid4().hex;result=self.client.call('character-rename',self.payload(),request_id=identity)
        self.assertEqual(self.client.call('character-rename',self.payload(),request_id=identity),result)
        self.assertEqual(self.client.call('mutation-status',dict(companion_root=str(self.root),mutation_id=identity))['receipt'],result)
        with self.assertRaises(EngineError) as error:self.client.call('character-rename',self.payload('Stale'))
        self.assertEqual(error.exception.code,'CONFLICT')

    def test_malformed_names_revisions_fields_and_unicode_rejected_before_spawn(self):
        values=[{**self.payload(),'extra':1},{**self.payload(),'display_name':'x'*8193},
            {**self.payload(),'display_name':'\ud800'}, {**self.payload(),'character_id':{}},
            {**self.payload(),'expected_revision':{'number':True,'sha256':'a'*64}},
            {**self.payload(),'expected_revision':{'number':-1,'sha256':'a'*64}},
            {**self.payload(),'expected_revision':{'number':2**53,'sha256':'a'*64}},
            {**self.payload(),'expected_revision':{'number':0,'sha256':'../file'}}]
        for payload in values:
            with self.subTest(payload=str(payload)[:80]),self.assertRaises(EngineError):self.client.call('character-rename',payload)
        self.assertIsNone(self.client.last_pid)

    def test_no_legacy_rename_or_snapshot_restore_fallback(self):
        from blendcap_vta_review import PrivateCoreBackend,VtaReviewUiError
        backend=PrivateCoreBackend(core=mock.Mock())
        self.assertFalse(hasattr(backend,'prepare_management'))
        self.assertFalse(hasattr(backend,'apply_management'))
        self.assertFalse(hasattr(backend,'restore_review_snapshot'))
        backend._core.assert_not_called()

    def test_control_characters_remain_escaped_inert_data_under_existing_rules(self):
        # Existing domain rules permit embedded newlines; transport must not invent
        # a new naming policy. JSON escapes them and subprocess never uses a shell.
        result=self.client.call('character-rename',self.payload('line\nname;$(echo)'))
        self.assertEqual(result['character']['display_name'],'line\nname;$(echo)')

    def test_receipt_structure_revision_and_identity_validation(self):
        result=self.client.call('character-rename',self.payload())
        for mutate in (lambda r:r.update(extra=1),lambda r:r['after']['revision'].update(number=True),
                       lambda r:r.update(request_id='bad'),lambda r:r.update(changed=False)):
            altered=json.loads(json.dumps(result));mutate(altered)
            with self.assertRaises(wire.ProtocolError):wire.receipt(altered)
        hello=wire.ready(123);hello['definition_sha256']='0'*64
        with self.assertRaises(wire.ProtocolError):wire.validate_ready(hello,123)

    def crash_endpoint(self,phase,sleep=False):
        script=self.root/'fault_engine.py'
        script.write_text('import sys,os,time\nsys.path.insert(0,'+repr(str(K))+')\n'
            'import vta_engine\nsys.meta_path.insert(0,vta_engine.HostImportGuard())\n'
            'from vta_core import engine_mutations as m\n'
            'def fault(point):\n if point=='+repr(phase)+': '+('time.sleep(20)' if sleep else 'os._exit(87)')+'\n'
            'm._fault=fault\nraise SystemExit(vta_engine.main())\n',encoding='utf-8')
        return EngineEndpoint(PY,script)

    def test_real_process_death_before_and_after_publish(self):
        for phase,expected in [('prepared','not-applied'),('linked','not-applied'),('published','applied'),('receipted','applied')]:
            self.state=self.client.call('character-state',dict(companion_root=str(self.root),character_id='a'))
            identity=uuid4().hex
            with self.subTest(phase=phase),self.assertRaises(EngineError):
                EngineClient(self.crash_endpoint(phase)).call('character-rename',self.payload(phase),request_id=identity)
            status=self.client.call('mutation-status',dict(companion_root=str(self.root),mutation_id=identity))
            self.assertEqual(status['state'],expected)
            self.assertFalse((self.root/'analysis/vta-analysis.lock').exists())

    def test_timeout_after_commit_resolved_by_status_not_blind_retry(self):
        identity=uuid4().hex;client=EngineClient(self.crash_endpoint('published',sleep=True),timeout=1)
        with self.assertRaises(EngineError) as error:client.call('character-rename',self.payload(),request_id=identity)
        self.assertEqual(error.exception.code,'TIMEOUT')
        status=self.client.call('mutation-status',dict(companion_root=str(self.root),mutation_id=identity))
        self.assertEqual(status['state'],'applied');self.assertEqual(status['receipt']['after']['revision']['number'],1)

    def test_import_isolated_actual_rename_and_status(self):
        code='''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from blendcap_vta_process_probe import private_import_guard
from blendcap_vta_engine import EngineClient,EngineEndpoint
with private_import_guard():
 c=EngineClient(EngineEndpoint(Path(sys.executable),Path(sys.argv[2])))
 s=c.call('character-state',dict(companion_root=sys.argv[3],character_id='a'))
 r=c.call('character-rename',dict(companion_root=sys.argv[3],project_id='test',character_id='a',display_name='Isolated',expected_revision=s['checkpoint']['revision']))
 assert c.call('mutation-status',dict(companion_root=sys.argv[3],mutation_id=r['request_id']))['receipt']==r
 assert not any(n=='vta_core' or n.startswith('vta_core.') for n in sys.modules)
 print('isolated')
'''
        done=subprocess.run([str(PY),'-I','-B','-c',code,str(P),str(ENTRY),str(self.root)],capture_output=True,text=True,timeout=20)
        self.assertEqual(done.returncode,0,done.stderr);self.assertEqual(done.stdout.strip(),'isolated')

    def test_corrupt_or_mismatched_response_after_commit_reconciles_by_id(self):
        for mode in ('malformed','request-id','character-id'):
            self.state=self.client.call('character-state',dict(companion_root=str(self.root),character_id='a'))
            script=self.root/'bad_response.py'
            script.write_text('import sys,copy\nsys.path.insert(0,'+repr(str(K))+')\n'
                'import vta_engine, vta_protocol as w\nfrom vta_core import engine_mutations\noriginal=w.response\n'
                'def response(*a,**kw):\n r=copy.deepcopy(original(*a,**kw))\n'
                +({'malformed':" r['unexpected']=True\n",'request-id':" r['request_id']='0'*32\n",
                    'character-id':" r['result']['character']['character_id']='b'\n"}[mode])+
                ' return r\nw.response=response\nraise SystemExit(vta_engine.main())\n',encoding='utf-8')
            identity=uuid4().hex
            with self.subTest(mode=mode),self.assertRaises(EngineError) as caught:
                EngineClient(EngineEndpoint(PY,script)).call('character-rename',self.payload(mode),request_id=identity)
            self.assertEqual(caught.exception.code,'INVALID_RESPONSE')
            status=self.client.call('mutation-status',dict(companion_root=str(self.root),mutation_id=identity))
            self.assertEqual(status['state'],'applied')
            self.assertEqual(status['receipt']['character']['display_name'],mode)

    def test_save_as_copy_preserves_durable_recovery_excludes_lock(self):
        from blendcap_vta_project_copy import CopyPlan
        result=self.client.call('character-rename',self.payload())
        plan=CopyPlan(self.root,self.root.parent/(self.root.name+'-copy'));plan.project()
        names={str(p).replace('\\','/') for p in plan.files}
        self.assertIn('.vta-engine/head.json',names)
        self.assertTrue(any(n.startswith('.vta-engine/requests/') for n in names))
        self.assertNotIn('.vta-engine/lease.lock',names)
        destination=plan.destination;self.addCleanup(lambda:shutil.rmtree(destination,ignore_errors=True))
        for relative,(source,_digest) in plan.files.items():
            path=destination/relative;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,path)
        self.assertEqual(self.client.call('mutation-status',dict(companion_root=str(destination),mutation_id=result['request_id']))['receipt'],result)
        self.client.call('character-rename',{**self.payload('Alice'),'companion_root':str(destination),'expected_revision':result['after']['revision']})
        self.assertEqual(json.loads((destination/'project_state.json').read_text())['characters'][0]['display_name'],'Alice')
        self.assertEqual(json.loads((self.root/'project_state.json').read_text())['characters'][0]['display_name'],'Changed')

    def test_save_as_omits_resolved_history_but_retains_legacy_pending_closure(self):
        from blendcap_vta_project_copy import CopyPlan
        old=self.root/'.vta-engine/snapshots/old.json';old.parent.mkdir();old.write_text('{}')
        def included():
            plan=CopyPlan(self.root,self.root.parent/(self.root.name+'-unused'));plan.project()
            return Path('.vta-engine/snapshots/old.json') in plan.files
        self.assertFalse(included());self.assertTrue(old.exists())
        (self.root/'.vta-host-pending.json').write_text('{}')
        self.assertTrue(included());self.assertTrue(old.exists())


if __name__=='__main__':unittest.main()
