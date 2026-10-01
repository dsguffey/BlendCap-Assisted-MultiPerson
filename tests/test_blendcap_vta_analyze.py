"""Offline contract tests for the thin, cache-only Analyze worker."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import blendcap_vta_analyze_worker as worker

try:
    import bpy
except ImportError:
    bpy = None


class AnalyzeContractTests(unittest.TestCase):
    def test_whole_and_inclusive_range_validation(self):
        self.assertEqual(worker.validate_range(None, None, 0, 162), (0, 162))
        self.assertEqual(worker.validate_range(12, 24, 0, 162), (12, 24))
        self.assertEqual(worker.validate_range(5, 5, 0, 162), (5, 5))
        for start, end in ((None, 2), (1, None), (5, 4), (-1, 2), (0, 163),
                           (True, 2), (1.5, 2)):
            with self.subTest(start=start, end=end), self.assertRaises(worker.AnalysisLaunchError):
                worker.validate_range(start, end, 0, 162)

    def test_status_round_trip_is_persistent_and_replaced_as_one_document(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker.write_status(root, {"state": "running", "frame": 3})
            self.assertEqual(json.loads(worker.status_path(root).read_text())["frame"], 3)
            worker.write_status(root, {"state": "complete", "frame": 5})
            self.assertEqual(json.loads(worker.status_path(root).read_text()),
                             {"state": "complete", "frame": 5})
            self.assertEqual(list((root / "analysis").glob("*.tmp")), [])

    def test_status_replace_retries_transient_windows_access_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker.write_status(root, {"state": "starting"})
            original_replace = worker.os.replace
            calls = []

            def transient(source, destination):
                calls.append((source, destination))
                # The temp writer is closed before publication begins.
                with Path(source).open("rb") as handle:
                    self.assertIn(b'"running"', handle.read())
                if len(calls) == 1:
                    error = PermissionError("simulated Windows destination reader")
                    error.winerror = 5
                    raise error
                return original_replace(source, destination)

            with mock.patch.object(worker.os, "replace", side_effect=transient), \
                 mock.patch.object(worker.time, "sleep") as sleep:
                worker.write_status(root, {"state": "running"})
            self.assertEqual(len(calls), 2)
            sleep.assert_called_once_with(worker._STATUS_RETRY_DELAYS[0])
            self.assertEqual(json.loads(worker.status_path(root).read_text())["state"], "running")
            self.assertEqual(list((root / "analysis").glob("*.tmp")), [])

    def test_persistent_status_conflict_preserves_prior_status_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker.write_status(root, {"state": "starting"})

            def denied(_source, _destination):
                error = PermissionError("persistent Windows destination lock")
                error.winerror = 32
                raise error

            with mock.patch.object(worker.os, "replace", side_effect=denied), \
                 mock.patch.object(worker.time, "sleep") as sleep, \
                 self.assertRaisesRegex(worker.AnalysisLaunchError, "status file"):
                worker.write_status(root, {"state": "running"})
            self.assertEqual(sleep.call_count, len(worker._STATUS_RETRY_DELAYS))
            self.assertEqual(json.loads(worker.status_path(root).read_text())["state"], "starting")
            self.assertEqual(list((root / "analysis").glob("*.tmp")), [])

    def test_non_sharing_status_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(worker.os, "replace", side_effect=PermissionError("ACL")), \
                 mock.patch.object(worker.time, "sleep") as sleep, \
                 self.assertRaises(worker.AnalysisLaunchError):
                worker.write_status(root, {"state": "starting"})
            sleep.assert_not_called()
            self.assertEqual(list((root / "analysis").glob("*.tmp")), [])

    def test_source_is_bound_by_hash_not_filename_and_no_character_is_created(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "renamed.mp4"
            video.write_bytes(b"synthetic-video")
            digest = hashlib.sha256(video.read_bytes()).hexdigest()
            source = SimpleNamespace(source_id="source", start_frame=0, end_frame=2,
                                     video_sha256=digest)
            project = SimpleNamespace(
                to_dict=lambda: {"sources": [{"source_id": "source", "video_sha256": digest}]},
                source_reference=lambda _id: source,
                create_character=mock.Mock(side_effect=AssertionError("Analyze created a Character")),
            )
            self.assertIs(worker._source_for_video(project, video), source)
            project.create_character.assert_not_called()
            video.write_bytes(b"changed")
            with self.assertRaises(worker.AnalysisLaunchError):
                worker._source_for_video(project, video)

    def test_existing_revision_whole_and_range_checkpoint_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "vta_core").mkdir()
            (root / "vta_core" / "__init__.py").touch()
            detector = root / "src" / "sam_3d_pose_estimation" / "stream_detector.py"
            detector.parent.mkdir(parents=True)
            detector.touch()
            video = root / "video.mp4"
            video.write_bytes(b"synthetic-video")
            digest = hashlib.sha256(video.read_bytes()).hexdigest()
            source = SimpleNamespace(source_id="source", start_frame=0, end_frame=2,
                                     video_sha256=digest)
            descriptor = SimpleNamespace(checkpoint_sha256="f" * 64)
            frames = {0}
            cache = SimpleNamespace(
                provider=descriptor, revision_id="existing", source=source,
                frame_result=lambda frame: object() if frame in frames else None,
                analyzed_frames=frames,
            )
            project = SimpleNamespace(
                to_dict=lambda: {
                    "sources": [{"source_id": "source", "video_sha256": digest}],
                    "active_analysis_revision_by_source": {"source": "existing"},
                },
                source_reference=lambda _id: source,
                create_character=mock.Mock(side_effect=AssertionError("Analyze created a Character")),
            )
            called = []
            stop = root / "analysis" / worker.STOP_NAME
            cancel_first = {"value": True}

            def analyze(kind, *args, **kwargs):
                called.append(kind)
                start, end = (0, 2) if kind == "whole" else (args[5], args[6])
                newly = skipped = completed = 0
                for frame in range(start, end + 1):
                    if kwargs["should_stop"]():
                        return SimpleNamespace(stopped=True, newly_analyzed_count=newly,
                                               skipped_completed_count=skipped)
                    if frame in frames:
                        skipped += 1
                    else:
                        frames.add(frame)
                        newly += 1
                    completed += 1
                    kwargs["on_progress"](SimpleNamespace(
                        status=SimpleNamespace(value="running"), current_frame=frame,
                        newly_analyzed_count=newly, skipped_completed_count=skipped,
                        completed_count=completed,
                    ))
                    if cancel_first["value"] and completed == 2:
                        stop.write_text("stop")
                        cancel_first["value"] = False
                return SimpleNamespace(stopped=False, newly_analyzed_count=newly,
                                       skipped_completed_count=skipped)

            runtime = SimpleNamespace(
                load=lambda: SimpleNamespace(descriptor=descriptor), unload=mock.Mock(),
            )
            modules = {}
            def module(name, **values):
                item = ModuleType(name)
                item.__dict__.update(values)
                modules[name] = item
            module("vta_core", __path__=[])
            module("vta_core.analysis_cache", AnalysisCache=object,
                   load_analysis_cache=lambda *_: cache, save_analysis_cache=mock.Mock())
            module("vta_core.cache_evidence_promotion",
                   register_analysis_cache_revision=mock.Mock(),
                   validate_analysis_cache_registration=mock.Mock())
            module("vta_core.project_persistence", load_project_state=lambda *_: project,
                   save_project_state=mock.Mock())
            module("vta_core.providers", __path__=[])
            module("vta_core.providers.sam3_runtime",
                   Sam3Runtime=SimpleNamespace(for_evaluated_pytorch=lambda *_: runtime))
            module("vta_core.range_analysis", analyze_frame_range=lambda *a, **k: analyze("range", *a, **k))
            module("vta_core.source_preflight",
                   preflight_source=lambda *_: SimpleNamespace(frame_count=3))
            module("vta_core.whole_video_analysis",
                   analyze_whole_video=lambda *a, **k: analyze("whole", *a, **k))
            base = dict(core_root=str(root), companion_root=str(root), video=str(video),
                        kinesia_root=str(root), checkpoint=str(video))
            with mock.patch.dict(sys.modules, modules):
                first = worker.run_analysis(argparse.Namespace(**base, start=None, end=None))
                self.assertEqual(frames, {0, 1})
                self.assertFalse(stop.exists())
                second = worker.run_analysis(argparse.Namespace(**base, start=1, end=2))
            self.assertEqual(called, ["whole", "range"])
            self.assertEqual(first["state"], "cancelled")
            self.assertEqual(first["total_analyzed_frames"], 2)
            self.assertEqual(second["completed_in_requested_range"], 2)
            self.assertEqual(second["newly_analyzed"], 1)
            self.assertEqual(second["skipped"], 1)
            self.assertEqual(second["total_analyzed_frames"], 3)
            self.assertEqual(json.loads(worker.status_path(root).read_text())["state"], "complete")
            self.assertFalse(stop.exists())
            project.create_character.assert_not_called()
            runtime.unload.assert_called()

    def test_exclusive_lock_prevents_concurrent_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "analysis").mkdir()
            lock = root / "analysis" / worker.LOCK_NAME
            lock.write_text("other process")
            with self.assertRaises(worker.AnalysisLaunchError):
                worker.main(["--core-root", directory, "--kinesia-root", directory,
                             "--checkpoint", directory, "--companion-root", directory,
                             "--video", directory])
            self.assertEqual(lock.read_text(), "other process")

    def test_early_failure_persists_actionable_status_and_cleans_own_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(worker.AnalysisLaunchError, "private VTA core"):
                worker.main(["--core-root", directory, "--kinesia-root", directory,
                             "--checkpoint", directory, "--companion-root", directory,
                             "--video", directory])
            status = json.loads(worker.status_path(root).read_text())
            self.assertEqual(status["state"], "failed")
            self.assertIn("private VTA core", status["error"])
            self.assertFalse((root / "analysis" / worker.LOCK_NAME).exists())

    def test_status_failure_does_not_mask_original_or_leave_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arguments = ["--core-root", directory, "--kinesia-root", directory,
                         "--checkpoint", directory, "--companion-root", directory,
                         "--video", directory]
            with mock.patch.object(worker, "run_analysis", side_effect=
                                   worker.AnalysisLaunchError("original status publication failure")), \
                 mock.patch.object(worker, "write_status", side_effect=
                                   worker.AnalysisLaunchError("secondary status failure")), \
                 self.assertRaisesRegex(worker.AnalysisLaunchError,
                                        "original status publication failure"):
                worker.main(arguments)
            self.assertFalse((root / "analysis" / worker.LOCK_NAME).exists())


@unittest.skipUnless(bpy is not None, "requires Blender background runtime")
class BlenderAnalyzeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        package = "blendcap_analyze_ui_test"
        spec = importlib.util.spec_from_file_location(
            package, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        addon = importlib.util.module_from_spec(spec)
        sys.modules[package] = addon
        spec.loader.exec_module(addon)
        cls.addon = addon
        cls.ui = sys.modules[package + ".blendcap.vta_review_ui"]

    def test_range_enum_exposes_frame_units(self):
        self.addon.register()
        try:
            property_rna = bpy.types.WindowManager.bl_rna.properties[
                "blendcap_vta_analyze_scope"]
            self.assertEqual(property_rna.enum_items['RANGE'].name, "Frame Range")
        finally:
            self.addon.unregister()

    def test_analysis_timer_redraws_progress_and_completion(self):
        ui = self.ui
        area = SimpleNamespace(tag_redraw=mock.Mock())
        window = SimpleNamespace(screen=SimpleNamespace(areas=[area]))
        process = SimpleNamespace(poll=mock.Mock(side_effect=[None, 0]))
        fake_bpy = SimpleNamespace(context=SimpleNamespace(
            window_manager=SimpleNamespace(windows=[window])))
        with mock.patch.object(ui, "bpy", fake_bpy), \
             mock.patch.object(ui, "_analysis_process", process):
            self.assertEqual(ui._analysis_timer(), 0.5)
            self.assertIsNone(ui._analysis_timer())
            self.assertIsNone(ui._analysis_process)
        self.assertEqual(area.tag_redraw.call_count, 2)

    def test_analysis_timer_updates_native_progress_and_invalidates_cache(self):
        ui = self.ui
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            area = SimpleNamespace(tag_redraw=mock.Mock())
            manager = SimpleNamespace(
                windows=[SimpleNamespace(screen=SimpleNamespace(areas=[area]))],
                progress_update=mock.Mock(), progress_end=mock.Mock(),
            )
            fake_bpy = SimpleNamespace(context=SimpleNamespace(window_manager=manager))
            process = SimpleNamespace(poll=mock.Mock(side_effect=[None, 0]))
            with mock.patch.object(ui, "bpy", fake_bpy), \
                 mock.patch.object(ui, "_analysis_process", process), \
                 mock.patch.object(ui, "_analysis_root", root), \
                 mock.patch.object(ui, "_analysis_progress_started", True), \
                 mock.patch.object(ui, "_analysis_total", 7), \
                 mock.patch.object(ui, "_cache_signature", (1, 2)), \
                 mock.patch.object(ui, "_analysis_status_for", return_value={
                     "completed_in_requested_range": 4,
                 }):
                self.assertEqual(ui._analysis_timer(), 0.5)
                self.assertIsNone(ui._analysis_timer())
                self.assertIsNone(ui._cache_signature)
                self.assertFalse(ui._analysis_progress_started)
            self.assertEqual(manager.progress_update.call_args_list,
                             [mock.call(4), mock.call(4)])
            manager.progress_end.assert_called_once()

    def test_nonzero_worker_exit_exposes_error_when_status_cannot_publish(self):
        ui = self.ui
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'analysis').mkdir()
            (root / 'analysis' / 'vta-analysis.log').write_text(
                'warning\nVTA analysis failed: Could not update analysis status file: '
                '[WinError 5] target\n', encoding='utf-8')
            manager = SimpleNamespace(windows=[], blendcap_vta_analysis_progress=0.0)
            fake_bpy = SimpleNamespace(context=SimpleNamespace(window_manager=manager))
            process = SimpleNamespace(poll=lambda: 1, pid=22)
            with mock.patch.object(ui, 'bpy', fake_bpy), \
                 mock.patch.object(ui, '_analysis_process', process), \
                 mock.patch.object(ui, '_analysis_root', root), \
                 mock.patch.object(ui, '_analysis_start', 11), \
                 mock.patch.object(ui, '_analysis_end', 162), \
                 mock.patch.object(ui, '_analysis_fallback_status', None), \
                 mock.patch.object(ui, '_analysis_status_for', return_value={
                     'state': 'complete', 'pid': 21, 'completed_in_requested_range': 0,
                     'total_analyzed_frames': 15,
                 }):
                self.assertIsNone(ui._analysis_timer())
                self.assertEqual(ui._analysis_fallback_status['state'], 'failed')
                self.assertIn('WinError 5', ui._analysis_fallback_status['error'])
                self.assertEqual(ui._analysis_fallback_status['total_analyzed_frames'], 15)

    def test_analysis_lock_blocks_mutations_but_not_read_only_selection(self):
        ui = self.ui
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "analysis").mkdir()
            (root / "analysis" / "vta-analysis.lock").write_text("worker")
            character = SimpleNamespace(character_id="character", occupied_observation_id="candidate")
            view = SimpleNamespace(characters=(character,), source_frame=4,
                                   source_id="source", candidates=())
            selection = SimpleNamespace(character_id="character", can_assign=lambda _view: True)
            operator = SimpleNamespace(report=mock.Mock())
            with mock.patch.object(ui, "_location", SimpleNamespace(companion_root=root)), \
                 mock.patch.object(ui, "_analysis_process", None), \
                 mock.patch.object(ui, "_view", view), \
                 mock.patch.object(ui, "_selection", selection), \
                 mock.patch.object(ui, "_timeline", SimpleNamespace(keys=())):
                self.assertTrue(ui._analysis_locked())
                self.assertFalse(any(ui._action_availability().values()))
                self.assertEqual(ui.BLENDCAP_OT_vta_review_assign.execute(operator, None),
                                 {'CANCELLED'})
                self.assertEqual(ui.BLENDCAP_OT_vta_review_create_character.execute(operator, None),
                                 {'CANCELLED'})
                self.assertEqual(ui.BLENDCAP_OT_vta_update_tracking.execute(operator, None),
                                 {'CANCELLED'})
                self.assertIn("Unavailable while candidate analysis is running",
                              operator.report.call_args.args[1])
                with mock.patch.object(ui.BLENDCAP_OT_vta_candidate_select,
                                       "poll", return_value=True), \
                     mock.patch.object(ui, "select_overlay_candidate_at", return_value=True):
                    event = SimpleNamespace(mouse_region_x=1, mouse_region_y=2)
                    self.assertEqual(ui.BLENDCAP_OT_vta_candidate_select.invoke(
                        None, None, event), {'FINISHED'})

    def test_ui_uses_resolved_runtime_and_reports_missing_environment(self):
        ui = self.ui
        root = Path("C:/private-core")
        runtime = SimpleNamespace(python=Path("C:/cuda/python.exe"),
                                  source=Path("C:/sam3/source"),
                                  checkpoint=Path("C:/sam3/sam3.pt"))
        prefs = SimpleNamespace(vta_analysis_python="", vta_kinesia_root="",
                                vta_sam3_checkpoint="")
        with mock.patch.object(ui, "addon_prefs", return_value=prefs), \
             mock.patch.object(ui, "_backend_root", root), \
             mock.patch.object(ui, "_python_exe", return_value="C:/blendcap/python.exe"), \
             mock.patch.object(ui, "resolve_analysis_runtime", return_value=runtime) as resolve:
            self.assertEqual(ui._configured_analysis_paths(None),
                             (runtime.python, runtime.source, runtime.checkpoint))
            self.assertEqual(resolve.call_args.args,
                             (root, Path("C:/blendcap/python.exe")))
            self.assertEqual(resolve.call_args.kwargs["configured_python"], "")
        with mock.patch.object(ui, "addon_prefs", return_value=None), \
             mock.patch.object(ui, "_backend_root", root), \
             mock.patch.object(ui, "resolve_analysis_runtime", return_value=runtime):
            self.assertEqual(ui._configured_analysis_paths(None)[0], runtime.python)
        with mock.patch.object(ui, "addon_prefs", return_value=prefs), \
             mock.patch.object(ui, "_backend_root", root), \
             mock.patch.object(ui, "resolve_analysis_runtime", side_effect=
                               ui.AnalysisRuntimeUnavailable("Analysis environment unavailable. "
                                   "Open BlendCap Preferences → Video to Animation.")):
            with self.assertRaisesRegex(ui.VtaReviewUiError,
                                        "Analysis environment unavailable.*BlendCap Preferences"):
                ui._configured_analysis_paths(None)

    def test_analyze_panel_uses_frame_units_and_offers_configuration_on_failure(self):
        ui = self.ui
        layout = mock.Mock()
        wm = SimpleNamespace(blendcap_vta_analyze_scope='RANGE')
        context = SimpleNamespace(window_manager=wm)
        panel = SimpleNamespace(layout=layout)
        with mock.patch.object(ui, "_location", None), \
             mock.patch.object(ui, "_view", None), \
             mock.patch.object(ui, "_status_is_error", True), \
             mock.patch.object(ui, "_status", "Analysis environment unavailable"):
            ui.VIEW3D_PT_blendcap_vta_analyze.draw(panel, context)
        self.assertEqual(layout.row.return_value.prop.call_args_list, [
            mock.call(wm, "blendcap_vta_analyze_start", text="Start Frame"),
            mock.call(wm, "blendcap_vta_analyze_end", text="End Frame"),
        ])
        layout.operator.assert_any_call("screen.userpref_show",
                                        text="Open BlendCap Preferences")

    def test_analyze_panel_presents_visible_progress_and_inclusive_count(self):
        ui = self.ui
        layout = mock.Mock()
        root = Path('C:/fixture')
        location = SimpleNamespace(companion_root=root, source_frame=0, blender_frame=1)
        wm = SimpleNamespace(blendcap_vta_analyze_scope='RANGE',
                             blendcap_vta_analyze_start=4,
                             blendcap_vta_analyze_end=10,
                             blendcap_vta_analysis_progress=57.0,
                             blendcap_vta_error_details=False)
        context = SimpleNamespace(window_manager=wm)
        status = dict(state='running', start_frame=3, end_frame=9,
                      current_frame=6, completed_in_requested_range=4,
                      newly_analyzed=3, skipped=1, total_analyzed_frames=15,
                      source_coverage=[0, 162])
        with mock.patch.object(ui, '_location', location), \
             mock.patch.object(ui, '_view', None), \
             mock.patch.object(ui, '_backend', SimpleNamespace(
                 source_coverage=lambda _location: (0, 162))), \
             mock.patch.object(ui, '_analysis_locked', return_value=False), \
             mock.patch.object(ui, '_analysis_status_for', return_value=status):
            ui.VIEW3D_PT_blendcap_vta_analyze.draw(
                SimpleNamespace(layout=layout), context)
        texts = [call.kwargs['text'] for call in layout.box.return_value.label.call_args_list]
        self.assertIn('Analyzing…', texts)
        self.assertIn('Blender frames 4–10 (inclusive)', texts)
        self.assertIn('Current run: 4 / 7 frames', texts)
        self.assertIn('New this run: 3; reused cached: 1', texts)
        self.assertIn('Total analyzed: 15 / 163 frames', texts)
        self.assertIn('Most recent Blender frame: 7', texts)
        layout.box.return_value.row.return_value.prop.assert_any_call(
            wm, 'blendcap_vta_analysis_progress', text='Progress', slider=True)
        layout.box.return_value.operator.assert_any_call(
            'blendcap.vta_analyze_stop', text='Cancel Analysis')

    def test_progress_calculation_and_failure_details_disclosure(self):
        ui = self.ui
        self.assertEqual(ui._analysis_percent(dict(start_frame=11, end_frame=162,
                                                    completed_in_requested_range=0)), 0.0)
        self.assertEqual(ui._analysis_percent(dict(start_frame=11, end_frame=162,
                                                    completed_in_requested_range=76)), 50.0)
        detail = "[WinError 5] Access denied: C:/fixture/vta-analysis-status.json"
        root = Path('C:/fixture')
        location = SimpleNamespace(companion_root=root, source_frame=0, blender_frame=1)
        wm = SimpleNamespace(blendcap_vta_analyze_scope='WHOLE',
                             blendcap_vta_error_details=False,
                             blendcap_vta_analysis_progress=0.0)
        status = dict(state='failed', error=detail, start_frame=11, end_frame=162,
                      completed_in_requested_range=0)
        layout = mock.Mock()
        with mock.patch.object(ui, '_location', location), \
             mock.patch.object(ui, '_view', None), \
             mock.patch.object(ui, '_backend', SimpleNamespace(
                 source_coverage=lambda _location: (0, 162))), \
             mock.patch.object(ui, '_analysis_locked', return_value=False), \
             mock.patch.object(ui, '_analysis_status_for', return_value=status):
            ui.VIEW3D_PT_blendcap_vta_analyze.draw(SimpleNamespace(layout=layout),
                                                   SimpleNamespace(window_manager=wm))
            labels = [call.kwargs['text'] for call in layout.box.return_value.label.call_args_list]
            self.assertIn('Analysis failed: could not update its status file.', labels)
            self.assertFalse(any('WinError' in text for text in labels))
            wm.blendcap_vta_error_details = True
            ui.VIEW3D_PT_blendcap_vta_analyze.draw(SimpleNamespace(layout=layout),
                                                   SimpleNamespace(window_manager=wm))
        detail_labels = [call.kwargs['text'] for call in
                         layout.box.return_value.box.return_value.label.call_args_list]
        self.assertTrue(any('WinError' in text for text in detail_labels))
        layout.box.return_value.box.return_value.operator.assert_any_call(
            'blendcap.vta_copy_analysis_error', text='Copy Details', icon='COPYDOWN')

    def test_copy_error_details_uses_full_status_text(self):
        ui = self.ui
        wm = SimpleNamespace(clipboard='')
        operator = SimpleNamespace(report=mock.Mock())
        with mock.patch.object(ui, '_location_from_context', return_value=
                               SimpleNamespace(companion_root=Path('C:/fixture'))), \
             mock.patch.object(ui, '_analysis_status_for', return_value={
                 'state': 'failed', 'error': '[WinError 5] technical path'}):
            self.assertEqual(ui.BLENDCAP_OT_vta_copy_analysis_error.execute(
                operator, SimpleNamespace(window_manager=wm)), {'FINISHED'})
        self.assertEqual(wm.clipboard, '[WinError 5] technical path')

    def test_missing_runtime_cancels_without_launch_or_tracking(self):
        ui = self.ui
        root = Path("C:/project")
        context = SimpleNamespace(window_manager=SimpleNamespace(
            blendcap_vta_analyze_scope='WHOLE'))
        operator = SimpleNamespace(report=mock.Mock())
        backend = SimpleNamespace(update_tracking=mock.Mock(),
                                  source_coverage=lambda _location: (0, 162))
        with mock.patch.object(ui, "_analysis_process", None), \
             mock.patch.object(ui, "_location_from_context", return_value=
                               SimpleNamespace(companion_root=root)), \
             mock.patch.object(ui, "_get_backend", return_value=backend), \
             mock.patch.object(ui, "_backend_root", root), \
             mock.patch.object(ui, "_configured_analysis_paths", side_effect=
                               ui.VtaReviewUiError("Analysis environment unavailable. "
                                   "Open BlendCap Preferences → Video to Animation.")), \
             mock.patch.object(ui.subprocess, "Popen") as popen:
            self.assertEqual(ui.BLENDCAP_OT_vta_analyze.execute(operator, context),
                             {'CANCELLED'})
        popen.assert_not_called()
        backend.update_tracking.assert_not_called()
        self.assertIn("Analysis environment unavailable", operator.report.call_args.args[1])

    def test_cancel_requests_between_frame_stop_without_killing_worker(self):
        ui = self.ui
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'analysis').mkdir()
            operator = SimpleNamespace(report=mock.Mock())
            with mock.patch.object(ui, '_location_from_context', return_value=
                                   SimpleNamespace(companion_root=root)), \
                 mock.patch.object(ui, '_analysis_status_for', return_value={
                     'state': 'running',
                 }):
                self.assertEqual(ui.BLENDCAP_OT_vta_analyze_stop.execute(operator, None),
                                 {'FINISHED'})
            self.assertEqual((root / 'analysis' / ui.STOP_NAME).read_text(), 'stop\n')

    def test_whole_and_range_operator_commands_are_external_and_statusful(self):
        ui = self.ui
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = root / "python.exe"
            kinesia = root / "source"
            checkpoint = root / "sam3.pt"
            location = SimpleNamespace(companion_root=root, source_path=root / "video.mp4",
                                       source_frame=0, blender_frame=1)
            wm = SimpleNamespace(blendcap_vta_analyze_scope='WHOLE',
                                 blendcap_vta_analyze_start=4,
                                 blendcap_vta_analyze_end=10)
            context = SimpleNamespace(window_manager=wm)
            operator = SimpleNamespace(report=mock.Mock())
            process = SimpleNamespace(poll=lambda: None)
            timers = SimpleNamespace(is_registered=lambda _timer: False,
                                     register=mock.Mock())
            fake_bpy = SimpleNamespace(app=SimpleNamespace(timers=timers))
            with mock.patch.object(ui, "bpy", fake_bpy), \
                 mock.patch.object(ui, "_analysis_process", None), \
                 mock.patch.object(ui, "_location_from_context", return_value=location), \
                 mock.patch.object(ui, "_get_backend", return_value=
                                   SimpleNamespace(source_coverage=lambda _location: (0, 162))), \
                 mock.patch.object(ui, "_backend_root", root), \
                 mock.patch.object(ui, "_configured_analysis_paths",
                                   return_value=(python, kinesia, checkpoint)), \
                 mock.patch.object(ui, "_analysis_status_for", return_value=None), \
                 mock.patch.object(ui.subprocess, "Popen", return_value=process) as popen:
                self.assertEqual(ui.BLENDCAP_OT_vta_analyze.execute(operator, context),
                                 {'FINISHED'})
                command = popen.call_args.args[0]
                self.assertEqual(command[0], str(python))
                self.assertNotIn('--start', command)
                self.assertNotIn('--end', command)
                self.assertEqual(popen.call_args.kwargs['env']['HF_HUB_OFFLINE'], '1')
                self.assertEqual(popen.call_args.kwargs['env']['PYTHONNOUSERSITE'], '1')
                timers.register.assert_called_once()
                wm.blendcap_vta_analyze_scope = 'RANGE'
                ui._analysis_process = None
                self.assertEqual(ui.BLENDCAP_OT_vta_analyze.execute(operator, context),
                                 {'FINISHED'})
                command = popen.call_args.args[0]
                self.assertEqual(command[command.index('--start') + 1], '3')
                self.assertEqual(command[command.index('--end') + 1], '9')
                wm.blendcap_vta_analyze_start = 11
                ui._analysis_process = None
                popen.reset_mock()
                self.assertEqual(ui.BLENDCAP_OT_vta_analyze.execute(operator, context),
                                 {'CANCELLED'})
                popen.assert_not_called()

    def test_workflow_subpanels_and_direct_keymap_register(self):
        ui = self.ui
        children = (ui.VIEW3D_PT_blendcap_vta_analyze,
                    ui.VIEW3D_PT_blendcap_vta_characters,
                    ui.VIEW3D_PT_blendcap_vta_current_frame,
                    ui.VIEW3D_PT_blendcap_vta_tracking,
                    ui.VIEW3D_PT_blendcap_vta_display,
                    ui.VIEW3D_PT_blendcap_vta_advanced)
        self.assertEqual([item.bl_label for item in children],
                         ['Analyze', 'Characters', 'Current Frame Review',
                          'Update Tracking', 'Display', 'Advanced'])
        self.assertTrue(all(item.bl_parent_id == 'VIEW3D_PT_blendcap_vta_review'
                            for item in children))
        self.assertFalse(hasattr(ui, 'BLENDCAP_WST_vta_review_key_select'))
        self.assertIsNotNone(ui.BLENDCAP_OT_vta_review_key_select)


if __name__ == "__main__":
    unittest.main()
