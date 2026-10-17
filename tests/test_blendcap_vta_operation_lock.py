import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest import mock
import blendcap_vta_operation_lock as locks


class OperationLockTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.path=Path(t.name)/'analysis/vta-analysis.lock'

    def test_owned_lifecycle_active_then_removed_even_on_exception(self):
        with self.assertRaisesRegex(ValueError,'failure'):
            with locks.acquire(self.path,request_id='one'):
                raw=json.loads(self.path.read_text());self.assertEqual(raw['request_id'],'one')
                self.assertEqual(locks.state(self.path),'active');self.assertFalse(locks.recover(self.path))
                with self.assertRaises(FileExistsError):
                    with locks.acquire(self.path,request_id='two'):pass
                raise ValueError('failure')
        self.assertFalse(self.path.exists())

    @unittest.skipUnless(os.name=='nt','Legacy empty-lock proof uses Windows share exclusion')
    def test_legacy_held_descriptor_protected_and_abandoned_empty_recovered(self):
        self.path.parent.mkdir()
        descriptor=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        try:
            self.assertEqual(locks.state(self.path),'unknown')
            self.assertFalse(locks.recover(self.path));self.assertTrue(self.path.exists())
        finally:os.close(descriptor)
        self.assertEqual(locks.state(self.path),'stale')
        self.assertTrue(self.path.exists(),'Read-only status must not mutate')
        self.assertTrue(locks.recover(self.path));self.assertFalse(self.path.exists())

    def test_unknown_format_and_live_legacy_pid_remain_protected(self):
        self.path.parent.mkdir()
        for value in ('worker',str(os.getpid()),json.dumps({'version':99,'pid':123})):
            self.path.write_text(value)
            self.assertIn(locks.state(self.path),('active','unknown'))
            self.assertFalse(locks.recover(self.path));self.assertEqual(self.path.read_text(),value)

    def test_cleanup_cannot_remove_changed_file(self):
        self.path.parent.mkdir();self.path.write_bytes(b'new owner')
        self.assertFalse(locks._remove_exact(self.path,b'old owner'))
        self.assertEqual(self.path.read_bytes(),b'new owner')

    def test_abrupt_process_death_then_retry_acquires_cleanly(self):
        python=Path(sys.executable)
        if 'blender' in python.name.lower():
            python=next(python.parent.glob('*/python/bin/python.exe'))
        code=('import os,sys;sys.path.insert(0,sys.argv[1]);from blendcap_vta_operation_lock import acquire;'
            'lock=acquire(sys.argv[2],request_id="crashed");lock.__enter__();os._exit(27)')
        result=subprocess.run([str(python),'-c',code,str(Path(__file__).resolve().parents[1]),str(self.path)],
            timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,27);self.assertEqual(locks.state(self.path),'stale')
        with locks.acquire(self.path,request_id='retry'):
            self.assertEqual(json.loads(self.path.read_text())['request_id'],'retry')
        self.assertFalse(self.path.exists())
