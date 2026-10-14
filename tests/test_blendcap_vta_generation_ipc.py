"""Real filesystem/thread lifecycle regressions, including embedded Windows paths."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import blendcap_vta_generation_ipc as ipc


class GenerationIpcTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'reconstruction').mkdir()

    def job(self,root=None):
        job=ipc.new_request(root or self.root,decision_required=True)
        job.update(plan={'source_range':[19,20]},process=SimpleNamespace(poll=lambda:None))
        return job

    def test_new_current_has_no_decision_path_or_migration_gate(self):
        job=ipc.new_request(self.root)
        self.assertIsNone(job['decision'])
        self.assertFalse(ipc.requires_migration([], 'current'))
        self.assertFalse(ipc.requires_migration(['current','current'], 'current'))
        self.assertTrue(ipc.requires_migration(['legacy-per-frame-intrinsics'], 'current'))
        self.assertFalse(list((self.root/'reconstruction').glob('*.decision')))

    def test_direct_accept_atomic_idempotent_and_worker_read_only(self):
        job=self.job()
        ipc.publish_decision(job,[0,30])
        before=job['decision'].read_bytes()
        ipc.publish_decision(job,[0,30])
        for _ in range(5):
            self.assertEqual(ipc.read_decision(job['decision'],job['root'],job['id'],[19,20]),[0,30])
        self.assertEqual(job['decision'].read_bytes(),before)
        self.assertFalse(list(job['decision'].parent.glob('*.tmp')))

    def test_cancel_retry_accept_uses_new_id_and_ignores_previous_markers(self):
        first=self.job(); ipc.cancel_request(first); ipc.cancel_request(first)
        with self.assertRaises(ipc.GenerationCancelled):ipc.publish_decision(first,[0,30])
        self.assertTrue(first['stop'].exists())
        second=self.job(); self.assertNotEqual(first['id'],second['id'])
        self.assertFalse(second['cancelled']); self.assertFalse(second['stop'].exists())
        ipc.publish_decision(second,[0,30])
        self.assertEqual(ipc.read_decision(second['decision'],second['root'],second['id'],[19,20]),[0,30])
        self.assertTrue(first['stop'].exists())  # second never cleans first

    def test_stale_temp_stop_and_decision_do_not_affect_repeated_requests(self):
        previous=self.job();ipc.cancel_request(previous)
        old_temp=previous['decision'].with_name('.old.tmp');old_temp.write_text('stale partial JSON')
        previous['decision'].write_text('{}')
        ids=[]
        for _ in range(12):
            job=self.job();ids.append(job['id'])
            self.assertFalse(job['stop'].exists())
            ipc.publish_decision(job,[0,30])
            job['process']=SimpleNamespace(poll=lambda:0)
            self.assertTrue(ipc.cleanup_request(job))
            self.assertFalse(job['decision'].exists())
        self.assertEqual(len(set(ids)),12)
        self.assertEqual(old_temp.read_text(),'stale partial JSON')
        self.assertTrue(previous['stop'].exists());self.assertTrue(previous['decision'].exists())

    def test_cleanup_while_publisher_worker_and_poller_active_cannot_remove_temp(self):
        job=self.job()
        def action(_):
            ipc.publish_decision(job,[0,30])
            for _ in range(5):
                self.assertFalse(ipc.cleanup_request(job))
                self.assertEqual(ipc.read_decision(job['decision'],job['root'],job['id'],[19,20]),[0,30])
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(action,range(8)))
        self.assertFalse(list(job['decision'].parent.glob('*.tmp')))
        self.assertTrue(job['decision'].exists())
        job['process']=SimpleNamespace(poll=lambda:0)
        self.assertTrue(ipc.cleanup_request(job));self.assertTrue(ipc.cleanup_request(job))

    def test_conflicting_decision_cannot_overwrite_existing_publish(self):
        job=self.job();ipc.publish_decision(job,[0,30]);before=job['decision'].read_bytes()
        with self.assertRaisesRegex(ValueError,'different decision'):
            ipc.publish_decision(job,[0,31])
        self.assertEqual(job['decision'].read_bytes(),before)

    def test_wrong_id_path_or_payload_is_rejected(self):
        a,b=self.job(),self.job()
        ipc.publish_decision(a,[0,30])
        with self.assertRaisesRegex(ValueError,'another request'):
            ipc.read_decision(a['decision'],a['root'],b['id'],[19,20])
        altered=json.loads(a['decision'].read_text());altered['generation_id']=b['id']
        b['decision'].write_text(json.dumps(altered))
        with self.assertRaisesRegex(ValueError,'another request'):
            ipc.read_decision(b['decision'],b['root'],a['id'],[19,20])

    def test_payload_id_and_range_are_validated_after_atomic_read(self):
        job=self.job()
        payload={'version':1,'generation_id':'a'*32,'requested_range':[19,20],'migration_range':[0,30]}
        job['decision'].write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError,'another request'):
            ipc.read_decision(job['decision'],job['root'],job['id'],[19,20])

    def test_atomic_publication_longer_than_max_path_on_embedded_blender(self):
        # This directory itself exceeds MAX_PATH. Prefix all OS operations; a
        # host policy/manifest change must not be required to publish safely.
        root=self.root/'path-depth-proof'/('p'*90)/('q'*90)/('r'*40)
        os.makedirs(ipc.filesystem_path(root/'reconstruction'))
        job=self.job(root)
        self.assertGreater(len(str(job['decision'])),260)
        ipc.publish_decision(job,[0,30]);ipc.publish_decision(job,[0,30])
        self.assertEqual(ipc.read_decision(job['decision'],root,job['id'],[19,20]),[0,30])
        ipc.cancel_request(job)
        job['process']=SimpleNamespace(poll=lambda:0)
        self.assertTrue(ipc.cleanup_request(job))
        # Remove only this test-owned temporary tree using extended paths.
        import shutil
        shutil.rmtree(ipc.filesystem_path(self.root/'path-depth-proof'))
