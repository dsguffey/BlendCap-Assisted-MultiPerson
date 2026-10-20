"""Independent companion transport, no Blender or authoritative model mutation."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import blendcap_vta_project_copy as copy


class ProjectCopyTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name)
        self.source=self.base/'Original_vta';self.source.mkdir()
        (self.source/'analysis').mkdir()
        self.state=dict(project_id='p',sources=[],analysis_revisions=[dict(revision_id='old'),dict(revision_id='a')],
            active_analysis_revision_by_source={'s':'a'},identity_keyframes=[dict(analysis_revision_id='old')])
        (self.source/'project_state.json').write_text(json.dumps(self.state))
        for name in ('a','old'):(self.source/'analysis'/(name+'.json')).write_text(json.dumps({'id':name}))
        self.run=self.source/'reconstruction'/'run-1';self.run.mkdir(parents=True)
        (self.run/'body.bvh').write_bytes(b'BVH sample')
        (self.run/'body.npz').write_bytes(b'NPZ sample')
        report=dict(schema='vta-reviewed-skeletal-output',characters=[dict(bvh='body.bvh',npz='body.npz',
            bvh_sha256=copy.file_hash(self.run/'body.bvh'),npz_sha256=copy.file_hash(self.run/'body.npz'))])
        (self.run/'output.json').write_text(json.dumps(report))
        (self.run/'worker.log').write_text('excluded')
        (self.source/'reconstruction'/'old.tmp').write_text('excluded')
        self.media=self.base/'source.mp4';self.media.write_bytes(b'source media')

    def plan(self,destination=None):
        plan=copy.CopyPlan(self.source,destination or self.base/'Renamed_vta')
        plan.project();plan.output(self.run/'output.json');plan.video(self.media,copy.file_hash(self.media))
        return plan

    def test_filename_rename_copies_required_closure_only(self):
        plan=self.plan();plan.publish()
        self.assertEqual(copy.file_hash(plan.destination/'project_state.json'),copy.file_hash(self.source/'project_state.json'))
        self.assertTrue((plan.destination/'analysis/old.json').is_file())
        self.assertTrue((plan.destination/'reconstruction/run-1/body.npz').is_file())
        self.assertFalse((plan.destination/'reconstruction/run-1/worker.log').exists())
        self.assertFalse((plan.destination/'reconstruction/old.tmp').exists())
        self.assertEqual((plan.destination/'sources'/self.media.name).exists(),False)
        self.assertTrue(next((plan.destination/'sources').iterdir()).is_file())

    def test_facial_evidence_copy_is_independent_and_excludes_jobs(self):
        root=self.source/'facial';root.mkdir()
        value=dict(contract='vta-face-evidence-1',project_id='p',characters=[])
        h=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        (root/(h+'.json')).write_text(json.dumps(value))
        (root/'index.json').write_text(json.dumps(dict(contract='vta-face-index-1',project_id='p',runs=[dict(file=h+'.json',sha256=h)])))
        (root/'jobs').mkdir();(root/'jobs/transient.log').write_text('local only')
        plan=self.plan();plan.publish()
        self.assertEqual((plan.destination/'facial/index.json').read_bytes(),(root/'index.json').read_bytes())
        self.assertEqual((plan.destination/'facial'/(h+'.json')).read_bytes(),(root/(h+'.json')).read_bytes())
        self.assertFalse((plan.destination/'facial/jobs').exists())
        (plan.destination/'facial'/(h+'.json')).write_text('copy edit')
        self.assertEqual(json.loads((root/(h+'.json')).read_text()),value)

    def test_facial_corrupt_or_escaping_reference_blocks_copy(self):
        root=self.source/'facial';root.mkdir()
        for entry in [dict(file='../escape.json',sha256='a'*64),dict(file='a'*64+'.json',sha256='a'*64)]:
            (root/'index.json').write_text(json.dumps(dict(contract='vta-face-index-1',project_id='p',runs=[entry])))
            (root/('a'*64+'.json')).write_text('{}')
            with self.assertRaises(ValueError):self.plan()

    def test_cross_directory_unicode_and_same_filename(self):
        plan=self.plan(self.base/'different Å folder'/'Original_vta');plan.publish()
        self.assertTrue((plan.destination/'project_state.json').is_file())
        self.assertTrue((self.source/'project_state.json').is_file())

    def test_two_way_independent_mutations_no_hardlinks(self):
        plan=self.plan();plan.publish()
        (plan.destination/'project_state.json').write_text('copy edit')
        self.assertEqual(json.loads((self.source/'project_state.json').read_text()),self.state)
        (self.source/'analysis/a.json').write_text('original edit')
        self.assertEqual(json.loads((plan.destination/'analysis/a.json').read_text()),{'id':'a'})
        (plan.destination/'sources'/next((plan.destination/'sources').iterdir()).name).write_bytes(b'changed copy media')
        self.assertEqual(self.media.read_bytes(),b'source media')

    def test_existing_destination_directory_or_file_is_never_merged(self):
        for directory in (True,False):
            target=self.base/('existing-dir' if directory else 'existing-file')
            if directory:target.mkdir();(target/'keep').write_text('mine')
            else:target.write_text('mine')
            plan=self.plan(target)
            with self.assertRaises(FileExistsError):plan.publish()
            self.assertEqual((target/'keep' if directory else target).read_text(),'mine')

    def test_copy_failure_cleans_only_its_owned_stage(self):
        plan=self.plan()
        with mock.patch.object(copy.shutil,'copyfile',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):plan.publish()
        self.assertFalse(plan.destination.exists())
        self.assertFalse(list(self.base.glob('.vta-copy-*')))
        self.assertEqual(json.loads((self.source/'project_state.json').read_text()),self.state)

    def owned_copy(self):
        plan=self.plan();blend=self.base/'Renamed.blend'
        plan.authorize_replacement(blend);plan.publish()
        blend.write_bytes(b'saved native blend');plan.finish()
        return plan,blend

    def test_owned_replacement_is_whole_tree_and_rollback_restores_independent_edits(self):
        prior,blend=self.owned_copy()
        (prior.destination/'user-note').write_text('destination only')
        (prior.destination/'project_state.json').write_text(json.dumps({**self.state,'edit':'independent'}))
        hashes=copy.tree_hashes(prior.destination)
        plan=self.plan();plan.authorize_replacement(blend);plan.publish()
        self.assertFalse((plan.destination/'user-note').exists())
        self.assertEqual(json.loads((plan.destination/'project_state.json').read_text()),self.state)
        self.assertTrue(plan.rollback())
        self.assertEqual(copy.tree_hashes(plan.destination),hashes)

    def test_owned_replacement_commits_only_after_native_success(self):
        prior,blend=self.owned_copy()
        plan=self.plan();plan.authorize_replacement(blend);plan.publish()
        self.assertTrue(plan.replaced.is_dir())
        blend.write_bytes(b'new native bytes');plan.finish()
        self.assertIsNone(plan.replaced)
        self.assertEqual(copy.replacement_receipt(blend)['blend_sha256'],copy.file_hash(blend))

    def test_receipt_rejects_unmatched_blend_v1_and_changed_destination(self):
        prior,blend=self.owned_copy()
        blend.write_bytes(b'unrecognized edit')
        with self.assertRaisesRegex(ValueError,'ownership'):self.plan().authorize_replacement(blend)
        # Old v1 receipt is insufficient ownership evidence even with same IDs.
        manifest=copy.read_json(prior.destination/'copy_manifest.json');manifest['version']=1
        (prior.destination/'copy_manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'ownership'):self.plan().authorize_replacement(blend)

    def test_owned_destination_race_does_not_publish_or_remove_anything(self):
        prior,blend=self.owned_copy()
        plan=self.plan();plan.authorize_replacement(blend)
        (prior.destination/'new-edit').write_text('arrived during staging')
        with self.assertRaisesRegex(ValueError,'changed'):plan.publish()
        self.assertTrue((prior.destination/'new-edit').is_file())
        self.assertIsNone(plan.replaced)

    def test_source_changed_during_copy_is_not_published(self):
        plan=self.plan();(self.source/'project_state.json').write_text('changed')
        with self.assertRaisesRegex(ValueError,'verification|changed'):plan.publish()
        self.assertFalse(plan.destination.exists())

    def test_native_failure_rollback_does_not_remove_independently_changed_copy(self):
        plan=self.plan();plan.publish()
        self.assertTrue(plan.rollback());self.assertFalse(plan.destination.exists())
        plan.publish();(plan.destination/'analysis/a.json').write_text('independent edit')
        self.assertFalse(plan.rollback());self.assertTrue(plan.destination.exists())

    def test_missing_required_evidence_and_tampered_output_fail(self):
        (self.source/'analysis/a.json').unlink()
        with self.assertRaises(OSError):self.plan()
        (self.source/'analysis/a.json').write_text('{}')
        (self.run/'body.bvh').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'changed'):self.plan()

    def test_output_dependency_traversal_and_overlapping_roots_refused(self):
        report=json.loads((self.run/'output.json').read_text());report['characters'][0]['bvh']='../escape.bvh'
        (self.run/'output.json').write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,'Unsafe'):self.plan()
        with self.assertRaisesRegex(ValueError,'overlap'):copy.CopyPlan(self.source,self.source/'nested')

    def test_foreign_immutable_output_is_imported_not_left_linked(self):
        foreign=self.base/'historical'/'reconstruction'/'other-run';foreign.mkdir(parents=True)
        (foreign/'output.json').write_text(json.dumps(dict(schema='vta-reviewed-skeletal-output',characters=[])))
        plan=self.plan();new=plan.output(foreign/'output.json');plan.publish()
        self.assertTrue(new.is_relative_to(plan.destination/'reconstruction'))
        self.assertEqual(new.read_bytes(),(foreign/'output.json').read_bytes())

    def test_windows_preflight_rejects_unsafe_length_before_copy(self):
        # This checks deterministic path arithmetic, not timing or OS behavior.
        from blendcap_vta_generation_ipc import preflight_generation_paths
        with self.assertRaisesRegex(ValueError,'too long'):
            preflight_generation_paths(self.base/('a'*230),windows=True)
        preflight_generation_paths(self.base/'short',windows=True)


if __name__=='__main__':unittest.main()
