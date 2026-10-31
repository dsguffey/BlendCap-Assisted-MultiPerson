"""M5 explicit roots, package ownership and real provider parent-loss coverage."""
import ast,copy,hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest import mock
from vta_protocol import runtime as contract
from blendcap_vta_runtime import load_runtime,configuration_path
import vta_package as package

P=Path(__file__).resolve().parents[1]

def configuration(root):
    roots={k:str(root/k) for k in contract.ROOTS}
    routes=dict(control_python='bin/python.exe',body_python='venv/Scripts/python.exe',analysis_python='Scripts/python.exe',body_root='.',kinesia_root='.',sam3_checkpoint='sam3.pt')
    for directory in roots.values():Path(directory).mkdir(parents=True,exist_ok=True)
    for name,owner in contract.ROUTES.items():
        if name.endswith('python') or name=='sam3_checkpoint':
            path=Path(roots[owner])/routes[name];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture-only')
    for owner,name in (('adapter','blendcap_vta_provider_worker.py'),('engine','vta_engine.py'),('analysis_source','src/sam_3d_pose_estimation/stream_detector.py'),('body','save_mhr_data.py')):
        path=Path(roots[owner])/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture-only')
    for name in contract.BODY_MODELS:
        path=Path(roots['body'])/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'model-fixture')
    return dict(schema='vta-runtime',version=1,product_version=contract.PRODUCT_VERSION,protocol=[contract.PROTOCOL_MIN,contract.PROTOCOL_MAX],mode='test',roots=roots,routes=routes)

class RuntimeRootTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);self.value=configuration(self.root)
    def test_explicit_source_installed_and_test_roots(self):
        for mode in ('source','installed','test'):
            self.value['mode']=mode;result=contract.resolve(self.value)
            self.assertEqual(result['engine_root'],self.root/'engine')
            self.assertEqual(result['body_python'],self.root/'body/venv/Scripts/python.exe')
    def test_missing_invalid_and_wrong_version_fail_actionably(self):
        cases=[dict(self.value,version=99),dict(self.value,product_version='old'),dict(self.value,protocol=[1,5]),dict(self.value,mode='guess')]
        for value in cases:
            with self.assertRaises(contract.RuntimeConfigurationError):contract.resolve(value)
        (self.root/'models/sam3.pt').unlink()
        with self.assertRaisesRegex(contract.RuntimeConfigurationError,'sam3_checkpoint'):contract.resolve(self.value)
    def test_runtime_paths_cannot_escape_or_choose_arbitrary_executable(self):
        for route in ('../evil.exe','C:/evil.exe','/evil','bin/../../evil','bin/python.exe:stream','bin\\python.exe','bin/cmd.exe'):
            value=copy.deepcopy(self.value);value['routes']['control_python']=route
            with self.assertRaises(contract.RuntimeConfigurationError):contract.resolve(value)
    def test_partial_config_has_no_installed_asset_fallback(self):
        del self.value['roots']['body']
        with self.assertRaises(contract.RuntimeConfigurationError):contract.resolve(self.value)

    def test_optional_review_runtime_is_explicit_paired_and_contained(self):
        root=self.root/'review';root.mkdir();(root/'python.exe').write_bytes(b'fixture')
        self.value['roots']['review_control']=str(root)
        with self.assertRaises(contract.RuntimeConfigurationError):contract.resolve(self.value)
        self.value['routes']['review_python']='python.exe'
        self.assertEqual(contract.resolve(self.value)['review_python'],root/'python.exe')
        self.value['routes']['review_python']='../python.exe'
        with self.assertRaises(contract.RuntimeConfigurationError):contract.resolve(self.value)
    def test_explicit_config_precedence_and_missing_never_guesses(self):
        path=self.root/'runtime.json';path.write_text(json.dumps(self.value),encoding='utf-8')
        self.assertEqual(load_runtime(path,environ={})['engine_root'],self.root/'engine')
        self.assertEqual(configuration_path(path,environ={'VTA_RUNTIME_CONFIG':'ignored'}),path)
        self.assertEqual(configuration_path(environ={'VTA_RUNTIME_CONFIG':str(path)}),path)
        with self.assertRaises(contract.RuntimeConfigurationError):load_runtime(self.root/'missing',environ={})
        with self.assertRaises(contract.RuntimeConfigurationError):configuration_path(environ={})
    def test_unknown_or_oversized_configuration_is_rejected(self):
        path=self.root/'runtime.json'
        for value in (dict(self.value,command='shell'),{'roots':{}},'x'*32769):
            path.write_text(json.dumps(value),encoding='utf-8')
            with self.assertRaises(contract.RuntimeConfigurationError):load_runtime(path)
    def test_no_production_private_imports_or_retired_worker_entrypoints(self):
        # AST plus computed-loader text: literal and previously dynamic sites.
        names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=P,text=True).splitlines()
        count=0
        for name in names:
            path=P/name
            if path.suffix!='.py' or not path.is_file() or name.startswith(('tests/','checkpoints/','models/','scripts/')):continue
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                modules=([node.module or ''] if isinstance(node,ast.ImportFrom) else [a.name for a in node.names] if isinstance(node,ast.Import) else [])
                self.assertFalse(any(m=='vta_core' or m.startswith('vta_core.') for m in modules),name)
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='import_module' and node.args:
                    self.assertNotIn('vta_core',ast.unparse(node.args[0]),name)
            count+=1
        self.assertGreater(count,60)
        for name in ('blendcap_vta_analyze_worker.py','blendcap_vta_reconstruct_worker.py','blendcap_vta_rotation_worker.py'):
            self.assertFalse((P/name).exists())
        self.assertNotIn('_get_source_backend',(P/'blendcap/vta_review_ui.py').read_text(encoding='utf-8'))

class PackageOwnershipTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);self.master=self.root/'master'
        path=self.master/'adapter/entry.py';path.parent.mkdir(parents=True);path.write_bytes(b'print("fixture")\n')
        self.value=dict(schema='vta-package',version=1,product_version=contract.PRODUCT_VERSION,files=[dict(path='adapter/entry.py',component='public-adapter',bytes=path.stat().st_size,sha256=package.file_hash(path),source='owner-fixture',license='GPL-3.0-or-later',notices='LICENSE',redistribution='approved')])
    def test_deterministic_staging_and_integrity(self):
        a=package.stage(self.master,self.root/'a',self.value);b=package.stage(self.master,self.root/'b',self.value)
        self.assertEqual(package.verify(a,self.value),[])
        self.assertEqual((a/'package.json').read_bytes(),(b/'package.json').read_bytes())
        self.assertEqual((a/'adapter/entry.py').read_bytes(),(b/'adapter/entry.py').read_bytes())
    def test_repair_missing_and_corrupt_owned_files_only(self):
        root=package.stage(self.master,self.root/'installed',self.value)
        project=root/'owner.blend';project.write_bytes(b'protected')
        log=root/'support.log';log.write_bytes(b'preserved')
        for broken in ('missing','corrupt'):
            path=root/'adapter/entry.py'
            if broken=='missing':path.unlink()
            else:path.write_bytes(b'corrupt')
            self.assertEqual(package.repair(root,self.master,self.value),['adapter/entry.py'])
            self.assertEqual(project.read_bytes(),b'protected');self.assertEqual(log.read_bytes(),b'preserved')
    def test_unsafe_payload_and_traversal_rejected(self):
        for name in ('../owner.blend','adapter/../engine.py','adapter/sources/private.mp4','adapter/QA_vta/project_state.json','adapter/owner.blend','adapter/x:ads','adapter\\evil.py'):
            value=copy.deepcopy(self.value);value['files'][0]['path']=name
            with self.assertRaises(contract.RuntimeConfigurationError):package.manifest(value)
    def test_unknown_provenance_cannot_be_bundled(self):
        for flag in ('conditional','external','unknown'):
            self.value['files'][0]['redistribution']=flag
            with self.assertRaisesRegex(contract.RuntimeConfigurationError,'redistribution'):package.stage(self.master,self.root/flag,self.value)
    def test_repair_rejects_corrupt_master(self):
        root=package.stage(self.master,self.root/'installed',self.value)
        (root/'adapter/entry.py').write_bytes(b'broken');(self.master/'adapter/entry.py').write_bytes(b'bad-master')
        with self.assertRaises(contract.RuntimeConfigurationError):package.repair(root,self.master,self.value)
        self.assertEqual((root/'adapter/entry.py').read_bytes(),b'broken')
    def test_uninstall_is_pure_allowlist_and_activation_is_versioned(self):
        root=package.stage(self.master,self.root/'installed',self.value);owner=root/'QA.blend';owner.write_bytes(b'protected')
        self.assertNotIn(owner,package.uninstall_plan(root,self.value));self.assertTrue(owner.exists())
        self.assertEqual(package.activation_record('m5-test',self.value)['product_version'],contract.PRODUCT_VERSION)
        for name in ('../bad','x/y','cmd;echo'):
            with self.assertRaises(contract.RuntimeConfigurationError):package.activation_record(name,self.value)

class ParentLossTests(unittest.TestCase):
    def test_actual_provider_exits_when_owner_dies(self):
        # The nested parent exits only after its child acquired the live handle.
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ready=root/'ready';child=root/'child.py';parent=root/'parent.py'
            child.write_text('import sys,os,time\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(P))+')\nfrom vta_protocol.process import watch_parent\nwatch_parent(int(sys.argv[1]))\nPath(sys.argv[2]).write_text("ready")\nwhile True:time.sleep(.1)\n',encoding='utf-8')
            parent.write_text('import os,sys,subprocess,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,"-I","-B",'+repr(str(child))+',str(os.getpid()),'+repr(str(ready))+'])\nwhile not Path('+repr(str(ready))+').exists():time.sleep(.01)\nprint("parent-exiting",flush=True)\nos._exit(0)\n',encoding='utf-8')
            python=Path(sys.prefix)/'bin/python.exe' if 'blender' in Path(sys.executable).name.lower() else Path(sys.executable)
            result=subprocess.run([str(python),'-I','-B',str(parent)],capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn(b'parent-exiting',result.stdout)

if __name__=='__main__':unittest.main()

class ErrorTaxonomyTests(unittest.TestCase):
    def test_stable_actionable_categories_do_not_echo_tracebacks(self):
        from blendcap_vta_errors import primary,MESSAGES
        for code in MESSAGES:
            message=primary(code);self.assertTrue(message);self.assertLess(len(message),180)
            self.assertNotIn('Traceback',message)
        self.assertIn('See Details',primary('secret-path-and-traceback'))

class RetiredInstallerTests(unittest.TestCase):
    def test_old_commands_have_no_install_or_delete_path(self):
        # Compile methods independently: no Blender dependencies or real shell.
        text=(P/'blendcap/operators_pipeline.py').read_text(encoding='utf-8')
        tree=ast.parse(text)
        targets={'BLENDCAP_OT_install_dependencies','BLENDCAP_OT_install_run','BLENDCAP_OT_uninstall_dependencies'}
        seen=set()
        from types import SimpleNamespace
        for cls in tree.body:
            if not isinstance(cls,ast.ClassDef) or cls.name not in targets:continue
            method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='execute')
            namespace={};exec(compile(ast.Module(body=[method],type_ignores=[]),'<retired installer>','exec'),namespace)
            report=mock.Mock();self.assertEqual(namespace['execute'](SimpleNamespace(report=report),None),{'CANCELLED'})
            self.assertIn('setup/repair',report.call_args.args[1]);seen.add(cls.name)
        self.assertEqual(seen,targets)
