"""Production wire/process parity and hostile-input checks; disposable inputs only."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock

import vta_protocol as wire
from blendcap_vta_engine import EngineClient,EngineEndpoint,EngineError,SourceCoverageClient,resolve_engine

P=Path(__file__).resolve().parents[1];K=P.parent/'Video-to-Animation'
PY=Path(sys.prefix)/'bin/python.exe' if 'blender' in Path(sys.executable).name.lower() else Path(sys.executable)
ENTRY=K/'vta_engine.py';RID='a'*32


def fixture(root, *, empty=False):
    root=Path(root);video=root/'video & literal dollar $.bin';video.write_bytes(b'not inference')
    source=dict(source_id='s',fps=24,start_frame=0,end_frame=162,video_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
                width=640,height=360,coordinate_contract=dict(box_format='xyxy',units='pixels',origin='lower-left'))
    raw=dict(schema='video-to-animation-project',version='0.8',project_id='test',sources=[] if empty else [source],
        characters=[],analysis_revisions=[],active_analysis_revision_by_source={},identity_keyframes=[],
        candidate_suppression_history=[],repair_ranges=[],tracking_revisions=[],review_flags=[])
    (root/'project_state.json').write_text(json.dumps(raw),encoding='utf-8')
    return video


def payload(root,video,frame=0):return dict(companion_root=str(root),source_path=str(video),source_frame=frame)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.video=fixture(self.root)
        self.request=wire.request(RID,payload(self.root,self.video))

    def test_deterministic_round_trip(self):
        self.assertEqual(wire.loads(wire.dumps(self.request)),self.request)
        self.assertEqual(wire.dumps(dict(reversed(list(self.request.items())))),wire.dumps(self.request))

    def test_version_unknown_operation_and_identifier_rejected(self):
        for field,values in [('version',(True,0,wire.VERSION+1,'1')),('operation',('os.system','__import__','exec','source-coverage;cmd')),
                             ('request_id',('',RID.upper(),'x'*32,True,{}))]:
            for value in values:
                with self.subTest(field=field,value=value),self.assertRaises(wire.ProtocolError):
                    wire.validate_request({**self.request,field:value})

    def test_required_and_extra_fields_rejected(self):
        for name in self.request:
            data=self.request.copy();del data[name]
            with self.subTest(name=name),self.assertRaises(wire.ProtocolError):wire.validate_request(data)
        with self.assertRaises(wire.ProtocolError):wire.validate_request({**self.request,'callback':'run'})
        with self.assertRaises(wire.ProtocolError):wire.request(RID,{**self.request['payload'],'pickle':'bad'})

    def test_frame_bool_negative_float_and_unreasonable_rejected(self):
        for value in (True,-1,1.5,wire.MAX_FRAME+1,[],None):
            with self.subTest(value=value),self.assertRaises(wire.ProtocolError):
                wire.request(RID,{**self.request['payload'],'source_frame':value})

    def test_paths_relative_traversal_devices_controls_and_size_rejected(self):
        for path in ('relative',str(self.root/'..'/'escape'),'\\\\server\\share','/tmp/\x00x','x'*5000):
            with self.subTest(path=path),self.assertRaises(wire.ProtocolError):wire.path_form(path)

    def test_json_duplicate_nonfinite_nested_oversize_pickle_and_array_rejected(self):
        for raw in (b'{',b'[]',b'null',b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":1e999}',
                    b'\x80\x04pickle',b'x'*(wire.MAX_BYTES+1),b'{"a":'+b'['*30+b'0'+b']'*30+b'}'):
            with self.subTest(raw=raw[:30]),self.assertRaises(wire.ProtocolError):wire.loads(raw)
        with self.assertRaises(wire.ProtocolError):wire.dumps({'object':object()})

    def test_timeouts_reject_invalid_and_unbounded(self):
        for value in (True,0,-1,float('nan'),float('inf'),301,'1'):
            with self.subTest(value=value),self.assertRaises(wire.ProtocolError):wire.timeout_value(value)

    def test_response_identity_result_and_structured_error(self):
        response=wire.response(RID,'source-coverage',result={'source_range':[0,162]})
        self.assertEqual(wire.validate_response(wire.loads(wire.dumps(response)),RID,'source-coverage'),response)
        with self.assertRaises(wire.ProtocolError):wire.validate_response(response,'b'*32,'source-coverage')
        for bounds in ([True,162],[-1,2],[3,2],[0,1,2]):
            with self.assertRaises(wire.ProtocolError):wire.validate_response(wire.response(RID,'source-coverage',result={'source_range':bounds}),RID,'source-coverage')
        error=wire.response(RID,'source-coverage',error=dict(code='SOURCE_MISMATCH',message='No match'))
        self.assertEqual(wire.validate_response(error,RID,'source-coverage')['status'],'error')

    def test_shared_package_is_identical_and_has_no_host_domain_imports(self):
        for name in ('__init__.py','README.md','observation.py','domain.py','jobs.py','bootstrap.py','rotation.py','runtime.py','process.py'):
            self.assertEqual((P/'vta_protocol'/name).read_bytes(),(K/'vta_protocol'/name).read_bytes())
        tree=ast.parse((P/'vta_protocol/__init__.py').read_text())
        mods=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        mods += [a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
        self.assertEqual(set(mods),{'json','math','re','pathlib','hashlib','domain','jobs','bootstrap'})


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.video=fixture(self.root)
        self.client=EngineClient(EngineEndpoint(PY,ENTRY))

    def test_real_current_loader_parity_boundaries_no_writes_and_timings(self):
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        for frame in (0,162):
            result=SourceCoverageClient(self.client).source_coverage(NS(companion_root=self.root,source_path=self.video,source_frame=frame))
            self.assertEqual(result,(0,162));self.assertNotEqual(self.client.last_pid,os.getpid())
            self.assertTrue(all(x>=0 for x in self.client.last_metrics.values()))
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})

    def test_domain_errors_empty_source_missing_and_frame(self):
        for change,code in (({'source_frame':163},'FRAME_OUT_OF_RANGE'),
                            ({'source_path':str(self.root/'missing')},'SOURCE_UNAVAILABLE')):
            with self.assertRaises(EngineError) as caught:self.client.call('source-coverage',{**payload(self.root,self.video),**change})
            self.assertEqual(caught.exception.code,code)
        fixture(self.root,empty=True)
        with self.assertRaises(EngineError) as caught:self.client.call('source-coverage',payload(self.root,self.video))
        self.assertEqual(caught.exception.code,'SOURCE_MISMATCH')
        (self.root/'project_state.json').write_text('{}')
        with self.assertRaises(EngineError) as caught:self.client.call('source-coverage',payload(self.root,self.video))
        self.assertEqual(caught.exception.code,'PROJECT_UNAVAILABLE')

    def test_invalid_request_never_spawns_or_executes_shell(self):
        with mock.patch('blendcap_vta_engine.subprocess.Popen') as spawn:
            for op in ('__import__','os.system','source-coverage && echo bad'):
                with self.assertRaises(EngineError):self.client.call(op,payload(self.root,self.video))
            spawn.assert_not_called()
        # Actual path contains shell-looking characters; it is data, never a command.
        self.assertEqual(self.client.call('source-coverage',payload(self.root,self.video))['source_range'],[0,162])

    def test_server_rejects_hostile_wire_before_domain_execution(self):
        good=wire.request(RID,payload(self.root,self.video));cases=[b'garbage',b'\x80pickle',b'x'*(wire.MAX_BYTES+1)]
        cases += [json.dumps(value).encode() for value in ({**good,'version':wire.VERSION+1},{**good,'operation':'__import__'},
            {**good,'extra':True},{**good,'payload':{}},{**good,'request_id':'bad'})]
        before=(self.root/'project_state.json').read_bytes()
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                child=subprocess.run([str(PY),'-I','-B',str(ENTRY)],input=raw,capture_output=True,timeout=15)
                self.assertEqual(child.returncode,2)
                lines=child.stdout.splitlines();self.assertEqual(len(lines),2)
                error=wire.loads(lines[1]);self.assertEqual(error['status'],'error')
                self.assertIn(error['error']['code'],wire.CODES)
        self.assertEqual(before,(self.root/'project_state.json').read_bytes())

    def stub(self,tail,*,ready=True):
        script=self.root/'stub.py'
        head='import sys,json,os,time\nsys.path.insert(0,'+repr(str(P))+')\nimport vta_protocol as w\n'
        if ready:head+='sys.stdout.buffer.write(w.dumps(w.ready(os.getpid())));sys.stdout.buffer.flush()\n'
        script.write_text(head+tail,encoding='utf-8');return EngineEndpoint(PY,script)

    def test_malformed_extra_wrong_id_version_exit_and_crash(self):
        read="q=w.loads(sys.stdin.buffer.read());r=w.response(q['request_id'],q['operation'],result={'source_range':[0,162]})\n"
        cases=[("print('broken',flush=True)",'INVALID_RESPONSE'),("print('x'*20000,flush=True)",'INVALID_RESPONSE'),
            (read+"r['request_id']='b'*32;sys.stdout.buffer.write(w.dumps(r))",'INVALID_RESPONSE'),
            (read+"r['version']=w.VERSION+1;sys.stdout.buffer.write(w.dumps(r))",'INVALID_RESPONSE'),
            (read+"sys.stdout.buffer.write(w.dumps(r));print('{}',flush=True)",'INVALID_RESPONSE'),
            (read+"sys.stdout.buffer.write(w.dumps(r));sys.stdout.buffer.flush();sys.exit(9)",'CHILD_FAILED'),
            ("sys.exit(8)",'CHILD_FAILED')]
        for tail,code in cases:
            with self.subTest(code=code,tail=tail):
                client=EngineClient(self.stub(tail))
                with self.assertRaises(EngineError) as caught:client.call('source-coverage',payload(self.root,self.video))
                self.assertEqual(caught.exception.code,code)

    def test_start_failure_pre_ready_crash_timeout_and_no_living_child(self):
        with mock.patch('blendcap_vta_engine.subprocess.Popen',side_effect=OSError('denied')):
            with self.assertRaises(EngineError) as caught:self.client.call('source-coverage',payload(self.root,self.video))
            self.assertEqual(caught.exception.code,'ENGINE_UNAVAILABLE')
        endpoints=[(self.stub('sys.exit(9)',ready=False),15,'CHILD_FAILED'),(self.stub('time.sleep(10)'),.3,'TIMEOUT')]
        original=subprocess.Popen
        for endpoint,timeout,code in endpoints:
            children=[]
            def launch(*a,**kw):
                self.assertFalse(kw.get('shell'));self.assertIsInstance(a[0],list)
                child=original(*a,**kw);children.append(child);return child
            with mock.patch('blendcap_vta_engine.subprocess.Popen',side_effect=launch):
                with self.assertRaises(EngineError) as caught:EngineClient(endpoint,timeout=timeout).call('source-coverage',payload(self.root,self.video))
                self.assertEqual(caught.exception.code,code)
            self.assertEqual(len(children),1);self.assertTrue(all(c.poll() is not None for c in children))

    def test_diagnostics_bounded_and_clean_shutdown_after_error(self):
        endpoint=self.stub("q=w.loads(sys.stdin.buffer.read());sys.stderr.write('x'*30000);sys.stdout.buffer.write(w.dumps(w.response(q['request_id'],q['operation'],error={'code':'SOURCE_MISMATCH','message':'No match'})));sys.exit(2)")
        client=EngineClient(endpoint)
        with self.assertRaises(EngineError) as caught:client.call('source-coverage',payload(self.root,self.video))
        self.assertEqual(caught.exception.code,'SOURCE_MISMATCH');self.assertEqual(len(client.last_diagnostics),8192)

    def test_nonreading_child_cannot_block_stdin_past_deadline(self):
        endpoint=self.stub('time.sleep(10)')
        client=EngineClient(endpoint,timeout=.3)
        value=str(self.root/('x'*3900))
        with self.assertRaises(EngineError) as caught:
            client.call('source-coverage',dict(companion_root=value,source_path=value,source_frame=0))
        self.assertEqual(caught.exception.code,'TIMEOUT')

    def test_fresh_host_private_import_guard_and_child_host_guard(self):
        code='''import sys,json
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,sys.argv[1])
from blendcap_vta_process_probe import private_import_guard
from blendcap_vta_engine import SourceCoverageClient,EngineClient,resolve_engine
with private_import_guard():
 c=EngineClient(resolve_engine(sys.argv[2],python=sys.executable))
 r=SourceCoverageClient(c).source_coverage(NS(companion_root=Path(sys.argv[3]),source_path=Path(sys.argv[4]),source_frame=0))
 print(json.dumps(r))
'''
        done=subprocess.run([str(PY),'-I','-B','-c',code,str(P),str(K),str(self.root),str(self.video)],capture_output=True,text=True,timeout=20)
        self.assertEqual(done.returncode,0,done.stderr);self.assertEqual(json.loads(done.stdout),[0,162])
        childcode="import runpy,sys,importlib;d=runpy.run_path(sys.argv[1]);sys.meta_path.insert(0,d['HostImportGuard']());blocked=0\nfor n in ('bpy','mathutils','blendcap_vta_engine','save_mhr_data'):\n try:importlib.import_module(n)\n except ImportError:blocked+=1\nassert blocked==4"
        done=subprocess.run([str(PY),'-I','-B','-c',childcode,str(ENTRY)],capture_output=True,text=True,timeout=15)
        self.assertEqual(done.returncode,0,done.stderr)


try:
    import bpy
except ImportError:
    bpy=None


@unittest.skipUnless(bpy is not None,'Blender runtime required')
class CoverageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_core_polish import CorePolishTests
        CorePolishTests.setUpClass();cls.ui=CorePolishTests.ui

    def test_analyze_draw_uses_cached_bounds_and_never_starts_ipc_or_core(self):
        ui=self.ui;location=NS(companion_root=Path('companion'),source_path=Path('video'))
        context=NS(window_manager=NS(blendcap_vta_analyze_scope='WHOLE'))
        info=('p',dict(start_frame=0,end_frame=162),location,None)
        layout=mock.Mock()
        with mock.patch.object(ui,'_location',location),mock.patch.object(ui,'_view',None),\
             mock.patch.object(ui,'cached_scene_source',return_value=info),\
             mock.patch.object(ui,'_analysis_locked',return_value=False),\
             mock.patch.object(ui,'_analysis_status_for',return_value=None),\
             mock.patch.object(ui,'_source_coverage',side_effect=AssertionError('draw IPC')),\
             mock.patch.object(ui,'_get_backend',side_effect=AssertionError('draw core')):
            for _ in range(30):ui.VIEW3D_PT_blendcap_vta_analyze.draw(NS(layout=layout),context)
        self.assertEqual(sum(c.kwargs.get('text')=='Inclusive range: 163 frames' for c in layout.label.call_args_list),30)

    def test_explicit_ui_query_uses_client_and_has_no_legacy_fallback(self):
        ui=self.ui;endpoint=EngineEndpoint(PY,ENTRY)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=fixture(root);location=NS(companion_root=root,source_path=video,source_frame=0)
            with mock.patch.object(ui,'_python_exe',return_value=str(PY)),\
                 mock.patch.object(ui,'_configured_core_location',return_value=str(K)),\
                 mock.patch.object(ui,'_get_backend',side_effect=AssertionError('legacy core')):
                self.assertEqual(ui._source_coverage(None,location),(0,162))
                with mock.patch.object(ui,'resolve_engine',side_effect=ui.EngineError('ENGINE_UNAVAILABLE','missing')):
                    with self.assertRaisesRegex(ui.VtaReviewUiError,'ENGINE_UNAVAILABLE'):ui._source_coverage(None,location)


if __name__=='__main__':unittest.main()
