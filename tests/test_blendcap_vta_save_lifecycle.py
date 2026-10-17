"""Actual native save lifecycle, two Scenes and fresh-process independent state."""
import base64
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock

try:import bpy
except ImportError:bpy=None

PUBLIC=Path(__file__).resolve().parents[1]


@unittest.skipUnless(bpy is not None,'Blender runtime required')
class SaveLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_output import BlenderOutputTests
        BlenderOutputTests.setUpClass()
        cls.addon=BlenderOutputTests.addon
        cls.ui=importlib.import_module(cls.addon.__name__+'.blendcap.vta_review_ui')
        cls.save=importlib.import_module(cls.addon.__name__+'.blendcap.vta_project_save')
        cls.copy=importlib.import_module(cls.addon.__name__+'.blendcap_vta_project_copy')
        sys.path.insert(0,str(PUBLIC.parent/'Video-to-Animation'))
        import vta_core
        cls.core=vta_core

    def setUp(self):
        from _bpy_restrict_state import RestrictBlend
        from tests.test_blendcap_vta_scene_timing import MOVIE
        with RestrictBlend():self.addon.register()
        self.addCleanup(self.addon.unregister)
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name)
        self.before=bpy.context.scene
        self.scenes=[];self.objects=[]
        self.addCleanup(self.cleanup)
        self.original=self.base/'Original.blend'
        # Establish an ordinary saved file before adding any VTA binding.
        bpy.ops.wm.save_as_mainfile(filepath=str(self.original))
        self.root=self.original.with_name('Original_vta')
        self.movie=self.base/'input.mp4';self.movie.write_bytes(base64.b64decode(MOVIE))
        project=self.core.ProjectState()
        source=self.core.SourceReference('source',24.,0,3,self.copy.file_hash(self.movie),16,16)
        project.add_source(source)
        project.create_character('Original character')
        self.core.save_project_state(project,self.root)
        for name in ('VTA A','VTA B'):
            scene=bpy.data.scenes.new(name);self.scenes.append(scene)
            scene['vta_scene_timing_version']=1;scene['vta_project_id']=project.project_id
            scene['vta_source_id']=source.source_id;scene['vta_source_video_sha256']=source.video_sha256
            strip=scene.sequence_editor_create().strips.new_movie('source',str(self.movie),channel=1,frame_start=1)
            strip['vta_source_strip_version']=1
            scene.sequence_editor.active_strip=strip
            scene.blendcap_props.video_source='VSE'
            scene.render.fps=24;scene.frame_set(1)
        bpy.context.window.scene=self.scenes[0]
        self.patch=mock.patch.object(self.ui,'_get_backend',return_value=NS(_core=self.core))
        self.patch.start();self.addCleanup(self.patch.stop)
        bpy.ops.wm.save_mainfile()

    def cleanup(self):
        bpy.context.window.scene=self.before
        self.save.reset()
        for scene in self.scenes:
            if scene.name in bpy.data.scenes:bpy.data.scenes.remove(scene)
        for obj in self.objects:
            if obj.name in bpy.data.objects:bpy.data.objects.remove(obj,do_unlink=True)

    def assert_usable(self,path):
        self.assertFalse(self.save.error(self.scenes[0]),self.save.error(self.scenes[0]))
        root=path.with_name(path.stem+'_vta')
        self.assertEqual(self.copy.file_hash(root/'project_state.json'),self.copy.file_hash(self.root/'project_state.json'))
        for scene in self.scenes:
            self.assertEqual(scene['vta_source_id'],'source')
            strip=scene.sequence_editor.active_strip
            self.assertTrue(Path(bpy.path.abspath(strip.filepath)).is_relative_to(root))
        self.assertIs(bpy.context.window.scene,self.scenes[0])
        return root

    def fresh(self,path,*,blocked=False):
        command=[bpy.app.binary_path,'--background','--factory-startup','--disable-autoexec','--python-exit-code','1',
            '--python',str(Path(__file__).resolve()),'--','--blocked' if blocked else '--fresh',str(path)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=40,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('FRESH_SAVE_COPY_PASS',result.stdout)

    def test_filename_only_rename_and_fresh_both_reopen(self):
        original=self.copy.file_hash(self.original)
        destination=self.base/'Renamed.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination))
        self.assert_usable(destination)
        self.assertEqual(self.copy.file_hash(self.original),original)
        self.fresh(destination);self.fresh(self.original)

    def test_cross_directory_and_save_copy_preserves_live_original_bindings(self):
        before=[s.sequence_editor.active_strip.filepath for s in self.scenes]
        destination=self.base/'different Å'/'Original.blend';destination.parent.mkdir()
        bpy.ops.wm.save_as_mainfile(filepath=str(destination),copy=True)
        self.assertEqual(Path(bpy.data.filepath),self.original)
        self.assertEqual([s.sequence_editor.active_strip.filepath for s in self.scenes],before)
        self.assertTrue((destination.parent/'Original_vta/project_state.json').is_file())
        self.fresh(destination)
        destination=self.base/'other'/'Renamed.blend';destination.parent.mkdir()
        bpy.ops.wm.save_as_mainfile(filepath=str(destination))
        self.assert_usable(destination);self.fresh(destination)

    def test_independent_authoritative_edits_in_both_directions(self):
        destination=self.base/'Independent.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination));root=self.assert_usable(destination)
        original=(self.root/'project_state.json').read_bytes()
        self.core.create_character_and_persist(root,'Copy only')
        self.assertEqual((self.root/'project_state.json').read_bytes(),original)
        copied=(root/'project_state.json').read_bytes()
        self.core.create_character_and_persist(self.root,'Original only')
        self.assertEqual((root/'project_state.json').read_bytes(),copied)

    def test_repeat_save_copy_replaces_owned_destination_not_merges(self):
        destination=self.base/'Repeat.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination),copy=True)
        root=destination.with_name('Repeat_vta')
        self.core.create_character_and_persist(root,'Discarded destination edit')
        (root/'previous-only.txt').write_text('must not survive whole-project replacement')
        bpy.ops.wm.save_as_mainfile(filepath=str(destination),copy=True)
        self.assertFalse(self.save.error(self.scenes[0]))
        self.assertEqual((root/'project_state.json').read_bytes(),(self.root/'project_state.json').read_bytes())
        self.assertFalse((root/'previous-only.txt').exists())
        self.assertEqual(Path(bpy.data.filepath),self.original)
        self.fresh(destination)
        self.assertFalse(list(self.base.glob('.vta-prior-*')))
        self.assertFalse(list(self.base.glob('.vta-save-*.bak')))

    def test_replacement_native_failure_restores_whole_destination_pair(self):
        destination=self.base/'Rollback.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination),copy=True)
        root=destination.with_name('Rollback_vta')
        (root/'independent.txt').write_text('keep')
        before=self.copy.tree_hashes(root);blend=destination.read_bytes()
        self.save.save_pre(str(destination))
        self.assertFalse((root/'independent.txt').exists())
        destination.write_bytes(b'failed native write')
        self.save.save_fail(str(destination))
        self.assertEqual(self.copy.tree_hashes(root),before)
        self.assertEqual(destination.read_bytes(),blend)

    def test_ordinary_save_renews_owned_receipt_before_later_replacement(self):
        destination=self.base/'Renew.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination))
        self.scenes[0]['user_edit']='legitimate ordinary Save'
        bpy.ops.wm.save_mainfile()
        self.assertEqual(self.copy.replacement_receipt(destination)['blend_sha256'],self.copy.file_hash(destination))

    def test_save_as_preserves_distinct_scene_membership_and_fresh_reopen(self):
        scope=importlib.import_module(self.ui.__package__+'.vta_scene_characters')
        project=self.core.load_project_state(self.root)
        second=project.create_character('Second Scene only');self.core.save_project_state(project,self.root)
        first=next(c for c in project.characters if c.character_id!=second.character_id)
        for scene,identity in zip(self.scenes,(first.character_id,second.character_id)):
            scope.bind(scene,project,fresh=True);scope.add(scene,project,identity)
        before=[s[scope.KEY] for s in self.scenes]
        destination=self.base/'Scoped.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination),copy=True)
        self.assertEqual([s[scope.KEY] for s in self.scenes],before)
        self.fresh(destination)

    def test_receipt_publication_failure_retains_both_previous_recovery_halves(self):
        destination=self.base/'SealFail.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination),copy=True)
        root=destination.with_name('SealFail_vta')
        blend=destination.read_bytes();before=self.copy.tree_hashes(root)
        self.save.save_pre(str(destination));job=self.save._transaction
        destination.write_bytes(b'successful new native write')
        with mock.patch.object(self.copy,'seal_receipt',side_effect=OSError('injected receipt failure')):
            self.save.save_post(str(destination))
        self.assertEqual(job['backup'].read_bytes(),blend)
        self.assertEqual(self.copy.tree_hashes(job['plan'].replaced),before)
        self.assertEqual(destination.read_bytes(),b'successful new native write')

    def test_different_scene_sources_remain_independently_scoped(self):
        other=self.base/'second.mp4';other.write_bytes(self.movie.read_bytes()+b'\0')
        project=self.core.load_project_state(self.root)
        second=self.core.SourceReference('second-source',24.,0,3,self.copy.file_hash(other),16,16)
        project.add_source(second);self.core.save_project_state(project,self.root)
        scene=self.scenes[1];scene['vta_source_id']=second.source_id
        scene['vta_source_video_sha256']=second.video_sha256
        scene.sequence_editor.active_strip.filepath=str(other)
        bpy.ops.wm.save_mainfile()
        destination=self.base/'TwoSources.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination))
        self.assertFalse(self.save.error(scene),self.save.error(scene))
        self.assertEqual([s['vta_source_id'] for s in self.scenes],['source','second-source'])
        self.assertNotEqual(self.scenes[0].sequence_editor.active_strip.filepath,
            self.scenes[1].sequence_editor.active_strip.filepath)
        self.fresh(destination);self.fresh(self.original)

    def test_read_only_scene_proxy_resolves_new_companion(self):
        session=importlib.import_module(self.ui.__package__+'.vta_scene_session')
        scene=self.scenes[0]
        context=session.scene_context(bpy.context,scene,scene.sequence_editor.active_strip)
        self.assertEqual(self.ui._location_from_context(context,allow_outside=True).companion_root,self.root)

    def test_destination_conflict_restores_existing_blend_and_companion(self):
        destination=self.base/'Existing.blend';destination.write_bytes(b'existing blend retained')
        root=destination.with_name('Existing_vta');root.mkdir();(root/'keep').write_bytes(b'existing state')
        original=(self.root/'project_state.json').read_bytes()
        bpy.ops.wm.save_as_mainfile(filepath=str(destination))
        self.assertEqual(destination.read_bytes(),b'existing blend retained')
        self.assertEqual((root/'keep').read_bytes(),b'existing state')
        self.assertEqual((self.root/'project_state.json').read_bytes(),original)
        self.assertTrue(self.save.error(self.scenes[0]))
        with self.assertRaisesRegex(ValueError,'disabled'):self.save.require_usable(self.scenes[0])
        # Explicit native Save As is also the narrow retry path for this failure.
        destination=self.base/'Recovered.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination));self.assert_usable(destination)

    def test_disk_copy_failure_is_persistently_blocked_without_original_mutation(self):
        original=(self.root/'project_state.json').read_bytes()
        destination=self.base/'Failed.blend'
        with mock.patch.object(self.copy.shutil,'copyfile',side_effect=OSError('injected disk full')):
            bpy.ops.wm.save_as_mainfile(filepath=str(destination))
        self.assertFalse(destination.with_name('Failed_vta').exists())
        self.assertEqual((self.root/'project_state.json').read_bytes(),original)
        self.assertTrue(self.save.error(self.scenes[0]))
        self.assertFalse(self.ui._source_actions_available(bpy.context))
        with self.assertRaisesRegex(ValueError,'disabled'):self.save.require_usable(self.scenes[1])
        self.assertFalse(list(self.base.glob('.vta-copy-*')))
        self.fresh(destination,blocked=True)

    def test_windows_realistic_long_path_succeeds_and_unsafe_path_fails_cleanly(self):
        folder=self.base/('nested-'+('a'*60))/('b'*35)
        folder.mkdir(parents=True)
        destination=folder/'Long.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination));self.assert_usable(destination)
        self.fresh(destination)
        folder=folder/('c'*45);folder.mkdir()
        destination=folder/'TooLong.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination))
        self.assertTrue(self.save.error(self.scenes[0]))
        self.assertFalse(destination.with_name('TooLong_vta').exists())

    def test_native_save_failure_rolls_back_published_companion_and_bindings(self):
        destination=self.base/'NativeFail.blend'
        before=[s.sequence_editor.active_strip.filepath for s in self.scenes]
        self.save.save_pre(str(destination))
        self.assertTrue(destination.with_name('NativeFail_vta').is_dir())
        self.save.save_fail(str(destination))
        self.assertFalse(destination.with_name('NativeFail_vta').exists())
        self.assertEqual([s.sequence_editor.active_strip.filepath for s in self.scenes],before)
        self.assertIsNone(self.save._transaction)

    def test_failed_native_write_restores_preexisting_destination_bytes(self):
        destination=self.base/'Preexisting.blend';destination.write_bytes(b'original destination')
        self.save.save_pre(str(destination))
        destination.write_bytes(b'partly written failed native save')
        self.save.save_fail(str(destination))
        self.assertEqual(destination.read_bytes(),b'original destination')
        self.assertFalse(destination.with_name('Preexisting_vta').exists())

    def test_failed_host_rebind_rolls_back_copy_and_preserves_source_bindings(self):
        destination=self.base/'RebindFail.blend'
        before=[s.sequence_editor.active_strip.filepath for s in self.scenes]
        change=self.save.change
        def interrupted(changes,owner,key,value,**kwargs):
            change(changes,owner,key,value,**kwargs)
            if key=='filepath':raise ValueError('injected host rebind failure')
        with mock.patch.object(self.save,'change',side_effect=interrupted):
            bpy.ops.wm.save_as_mainfile(filepath=str(destination))
        self.assertFalse(destination.with_name('RebindFail_vta').exists())
        self.assertEqual([s.sequence_editor.active_strip.filepath for s in self.scenes],before)
        self.assertTrue(self.save.error(self.scenes[0]))

    def test_running_worker_prevents_fork_not_worker_cancellation(self):
        destination=self.base/'Busy.blend'
        with mock.patch.object(self.ui,'_analysis_locked',return_value=True):
            bpy.ops.wm.save_as_mainfile(filepath=str(destination))
        self.assertTrue(self.save.error(self.scenes[0]))
        self.assertFalse(destination.with_name('Busy_vta').exists())

    def test_registration_lifecycle_is_idempotent(self):
        self.save.register();self.save.register()
        for handlers,fn in ((bpy.app.handlers.save_pre,self.save.save_pre),
                (bpy.app.handlers.save_post,self.save.save_post),(bpy.app.handlers.save_post_fail,self.save.save_fail)):
            self.assertEqual(list(handlers).count(fn),1)

    def test_blank_scenes_still_copy_detached_authoritative_history(self):
        for scene in self.scenes:
            for key in ('vta_scene_timing_version','vta_project_id','vta_source_id','vta_source_video_sha256'):
                scene.pop(key,None)
            scene['vta_scene_owner_version']=1;scene['vta_scene_owner_id']=scene.name
        destination=self.base/'BlankWithHistory.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(destination))
        self.assertEqual((destination.with_name('BlankWithHistory_vta')/'project_state.json').read_bytes(),
            (self.root/'project_state.json').read_bytes())
        self.assertTrue(all(not self.save.error(s) for s in self.scenes))


if __name__=='__main__':
    sys.dont_write_bytecode=True;sys.path.insert(0,str(PUBLIC))
    import types
    package=types.ModuleType('tests');package.__path__=[str(PUBLIC/'tests')];sys.modules['tests']=package
    args=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    if args and args[0] in ('--fresh','--blocked'):
        SaveLifecycleTests.setUpClass()
        SaveLifecycleTests.addon.register()
        bpy.ops.wm.open_mainfile(filepath=args[1])
        root=Path(args[1]).with_name(Path(args[1]).stem+'_vta')
        scenes=[s for s in bpy.data.scenes if s.get('vta_source_id') in ('source','second-source')]
        assert len(scenes)==2
        if args[0]=='--blocked':
            assert all(SaveLifecycleTests.save.error(scene) for scene in scenes)
            assert not root.exists()
            print('FRESH_SAVE_COPY_PASS BLOCKED',flush=True)
            raise SystemExit(0)
        project=SaveLifecycleTests.core.load_project_state(root)
        scope=importlib.import_module(SaveLifecycleTests.ui.__package__+'.vta_scene_characters')
        for scene in scenes:
            SaveLifecycleTests.save.require_usable(scene)
            path=Path(bpy.path.abspath(scene.sequence_editor.active_strip.filepath))
            record=project.source_reference(scene['vta_source_id']).to_dict()
            assert path.is_file() and SaveLifecycleTests.copy.file_hash(path)==record['video_sha256']
            assert scene['vta_project_id']==project.project_id
            if scope.KEY in scene:
                members=scope.stored(scene)
                assert members and members<=set(c.character_id for c in project.characters)
        scoped=[scope.stored(s) for s in scenes if scope.KEY in s]
        if len(scoped)==2:assert scoped[0].isdisjoint(scoped[1])
        print('FRESH_SAVE_COPY_PASS',flush=True)
    else:
        unittest.main(argv=[sys.argv[0]],verbosity=2)
