"""Native subprocess regressions; isolation cases always use a fresh proof host.

Direct --fixture-proof execution under factory-startup Blender is an opt-in proof,
not addon registration or production routing. No private imports in this file.
"""
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

PUBLIC = Path(__file__).resolve().parents[1]
PRIVATE = PUBLIC.parent/'Video-to-Animation'
PYTHON = Path(os.environ.get('VTA_PROOF_PYTHON', sys.executable))
if 'blender' in PYTHON.name.lower():
    PYTHON = Path(sys.prefix)/'bin/python.exe'
LAUNCHER = PRIVATE/'vta_core_process_probe.py'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def fixture(root, *, duplicate=False):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    video=root/'video Å space.bin';video.write_bytes(b'proof-only fixture, not inference')
    source=dict(source_id='source',fps=24,start_frame=0,end_frame=162,
                video_sha256=digest(video),width=640,height=360,
                coordinate_contract=dict(box_format='xyxy',units='pixels',origin='lower-left'))
    state=dict(schema='video-to-animation-project',version='0.8',project_id='proof',
        sources=[source],characters=[],analysis_revisions=[],active_analysis_revision_by_source={},
        identity_keyframes=[],candidate_suppression_history=[],repair_ranges=[],tracking_revisions=[],review_flags=[])
    if duplicate:
        state['sources'].append({**source,'source_id':'duplicate'})
    (root/'project_state.json').write_text(json.dumps(state),encoding='utf-8')
    return video


def clean_host(code, *arguments):
    prefix = "import sys; sys.dont_write_bytecode=True; sys.path.insert(0,"+repr(str(PUBLIC))+');\n'
    output=subprocess.run([str(PYTHON),'-I','-B','-c',prefix+code,*map(str,arguments)],
        capture_output=True,text=True,timeout=25)
    if output.returncode:
        raise AssertionError(output.stdout+'\n'+output.stderr)
    return json.loads(output.stdout)


QUERY = '''
import json, os
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace as NS
from blendcap_vta_process_probe import SourceCoverageProcessClient, ProbeError
location=NS(companion_root=Path(sys.argv[3]),source_path=Path(sys.argv[4]),source_frame=json.loads(sys.argv[5]))
client=SourceCoverageProcessClient(sys.argv[1],sys.argv[2],timeout=float(sys.argv[6]))
try:
    result=client.source_coverage(location)
    print(json.dumps(dict(ok=True,result=result,timing=asdict(client.last_result),host_pid=os.getpid())))
except ProbeError as exc:
    print(json.dumps(dict(ok=False,code=exc.code,has_result=client.last_result is not None)))
'''


class ProcessProbeTests(unittest.TestCase):
    def query(self,root,video,frame=0,launcher=LAUNCHER,timeout=10):
        return clean_host(QUERY,PYTHON,launcher,root,video,json.dumps(frame),timeout)

    def test_real_child_matches_existing_adapter_without_private_host_import_and_no_writes(self):
        with tempfile.TemporaryDirectory(prefix='coverage Å ') as folder:
            root=Path(folder);video=fixture(root)
            before=(digest(root/'project_state.json'),digest(video),set(root.iterdir()))
            for frame in (0,162):
                response=self.query(root,video,frame)
                self.assertEqual(response['result'],[0,162])
                self.assertNotEqual(response['host_pid'],response['timing']['engine_pid'])
                self.assertTrue(all(math.isfinite(response['timing'][key]) and response['timing'][key]>=0
                    for key in ('startup_s','operation_s','total_s','engine_compute_s')))
            self.assertEqual(before,(digest(root/'project_state.json'),digest(video),set(root.iterdir())))
            baseline=clean_host('''
import json
from pathlib import Path
from types import SimpleNamespace as NS
from blendcap_vta_review import PrivateCoreBackend, ReviewLocation
raw=json.loads((Path(sys.argv[1])/'project_state.json').read_text(encoding='utf-8'))
backend=PrivateCoreBackend(core=NS(load_project_state=lambda root:NS(to_dict=lambda:raw)))
print(json.dumps(backend.source_coverage(ReviewLocation(Path(sys.argv[1]),Path(sys.argv[2]),0,1))))
''',root,video)
            self.assertEqual(response['result'],baseline)

    def test_real_project_source_and_frame_errors_no_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=fixture(root)
            for frame in (-1,163):
                self.assertEqual(self.query(root,video,frame)['code'],'FRAME_OUT_OF_RANGE')
            self.assertEqual(self.query(root,video,True)['code'],'INVALID_REQUEST')
            self.assertEqual(self.query(root,root/'missing')['code'],'SOURCE_UNAVAILABLE')
            wrong=root/'wrong';wrong.write_bytes(b'wrong')
            self.assertEqual(self.query(root,wrong)['code'],'SOURCE_MISMATCH')
            fixture(root,duplicate=True)
            self.assertEqual(self.query(root,video)['code'],'SOURCE_MISMATCH')
            (root/'project_state.json').write_text('{}',encoding='utf-8')
            self.assertEqual(self.query(root,video)['code'],'PROJECT_UNAVAILABLE')
            (root/'project_state.json').unlink()
            self.assertEqual(self.query(root,video)['code'],'PROJECT_UNAVAILABLE')

    def test_guard_blocks_import_cached_module_and_private_search_path(self):
        response=clean_host('''
import importlib,json
from types import SimpleNamespace as NS
from blendcap_vta_process_probe import private_import_guard,SourceCoverageProcessClient,ProbeError
codes=[]
with private_import_guard():
    try: importlib.import_module('vta_core')
    except ProbeError as exc: codes.append(exc.code)
sys.modules['vta_core']=NS()
try: SourceCoverageProcessClient('missing','missing').source_coverage(NS())
except ProbeError as exc: codes.append(exc.code)
del sys.modules['vta_core']
sys.path.append(sys.argv[1])
try:
    with private_import_guard(): pass
except ProbeError as exc: codes.append(exc.code)
print(json.dumps(codes))
''',PRIVATE)
        self.assertEqual(response,['PRIVATE_IMPORT_BLOCKED','HOST_NOT_ISOLATED','HOST_NOT_ISOLATED'])

    def stub(self,root,tail,*,ready=True):
        path=root/'stub.py'
        header="import json,os,sys,time\nschema='vta-source-coverage-probe'\n"
        if ready:
            header+="print(json.dumps(dict(schema=schema,version=1,kind='ready',pid=os.getpid(),ok=True,host_modules_loaded=[])),flush=True)\n"
            header+="request=json.load(sys.stdin)\nreply=dict(schema=schema,version=1,kind='result',pid=os.getpid(),ok=True,request_id=request['request_id'],source_range=[0,162],compute_s=0)\n"
        path.write_text(header+tail,encoding='utf-8')
        return path

    def test_malformed_unbounded_wrong_identity_and_extra_output_rejected(self):
        cases=("print('not json')", "print('x'*9000)",
            "reply['request_id']='wrong';print(json.dumps(reply))",
            "reply['pid']+=1;print(json.dumps(reply))",
            "reply['version']=True;print(json.dumps(reply))",
            "reply['source_range']=[True,162];print(json.dumps(reply))",
            "reply['compute_s']=float('nan');print(json.dumps(reply))",
            "print(json.dumps(reply));print('{}')",
            "print(json.dumps(reply)[:-1]+',\"ok\":true}')")
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=fixture(root)
            for case in cases:
                with self.subTest(case=case):
                    response=self.query(root,video,launcher=self.stub(root,case))
                    self.assertEqual(response['code'],'INVALID_RESPONSE')
                    self.assertFalse(response['has_result'])

    def test_child_failure_unavailable_engine_and_timeout_are_clean(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=fixture(root)
            self.assertEqual(self.query(root,video,launcher=root/'absent')['code'],'ENGINE_UNAVAILABLE')
            for ready in (False,True):
                self.assertEqual(self.query(root,video,launcher=self.stub(root,'sys.exit(7)',ready=ready))['code'],'CHILD_FAILED')
            # Instrumentation wraps a real Popen; oracle is error/terminated PID,
            # never a wall-clock performance threshold.
            stub=self.stub(root,'time.sleep(10)')
            response=clean_host('''
import json,subprocess
from pathlib import Path
from types import SimpleNamespace as NS
import blendcap_vta_process_probe as probe
original=probe.subprocess.Popen;children=[]
def spawn(*args,**kw):
    process=original(*args,**kw);children.append(process);return process
probe.subprocess.Popen=spawn
try:
    probe.SourceCoverageProcessClient(sys.argv[1],sys.argv[2],timeout=.5).source_coverage(
        NS(companion_root=Path(sys.argv[3]),source_path=Path(sys.argv[4]),source_frame=0))
except probe.ProbeError as exc:
    print(json.dumps(dict(code=exc.code,count=len(children),terminated=all(p.poll() is not None for p in children))))
''',PYTHON,stub,root,video)
            self.assertEqual(response,dict(code='TIMEOUT',count=1,terminated=True))


def fixture_proof(arguments):
    """Factory-startup Blender only; never load/save the user's .blend."""
    import bpy
    from blendcap_vta_process_probe import SourceCoverageProcessClient, private_import_guard
    from blendcap_vta_review import PrivateCoreBackend, ReviewLocation
    root,video,python,report=map(Path,arguments)
    addons=[addon.module for addon in bpy.context.preferences.addons]
    if (bpy.data.filepath or '--factory-startup' not in sys.argv or '--disable-autoexec' not in sys.argv
            or any('blendcap' in name.lower() for name in addons)):
        raise AssertionError('Use factory startup, disabled autoexec and no VTA addon.')
    before={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
    video_before=digest(video)
    raw=json.loads((root/'project_state.json').read_text(encoding='utf-8'))
    location=ReviewLocation(root,video,0,1)
    measurements=[]
    with private_import_guard():
        # Execute the ORIGINAL public adapter method using a test-only JSON
        # facade for its loader. This baseline needs no private import in any
        # GPL/Blender process; the candidate child performs real core validation.
        baseline=PrivateCoreBackend(core=SimpleNamespace(
            load_project_state=lambda _root:SimpleNamespace(to_dict=lambda:raw))).source_coverage(location)
        client=SourceCoverageProcessClient(python,LAUNCHER)
        for _ in range(5):
            assert client.source_coverage(location)==baseline
            assert client.last_result.engine_pid!=os.getpid()
            measurements.append(asdict(client.last_result))
    assert before=={str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file()}
    assert digest(video)==video_before and bpy.data.filepath==''
    result=dict(baseline=list(baseline),host_pid=os.getpid(),samples=measurements,
        factory_addons=addons,
        host_private_modules=[n for n in sys.modules if n=='vta_core' or n.startswith('vta_core.')],
        snapshot_sha256=before['project_state.json'],source_sha256=video_before,
        unchanged=True,baseline_method='Original public source_coverage with test-only authoritative JSON loader facade',
        timing_medians={key:statistics.median(m[key] for m in measurements)
            for key in ('startup_s','operation_s','total_s','engine_compute_s')})
    assert result['host_private_modules']==[]
    report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('FACTORY_PROCESS_PROOF_PASS',json.dumps(result),flush=True)


if __name__=='__main__':
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(PUBLIC))
    args=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    if args and args[0]=='--fixture-proof':
        fixture_proof(args[1:])
    else:
        unittest.main(argv=[sys.argv[0]],verbosity=2)
