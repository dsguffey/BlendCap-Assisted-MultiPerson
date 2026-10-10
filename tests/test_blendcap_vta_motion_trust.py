"""Thin routing and paint-only motion intervals; no private algorithms copied."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

from blendcap_vta_review import PrivateCoreBackend, ReviewLocation, VtaReviewUiError, present_review
from blendcap_vta_timeline import TimelineCharacter, TimelineKey, TimelineMotionInterval, TimelineView, present_timeline
from blendcap_vta_workspace import TimelineMetrics, character_lanes, hit_review_key, motion_interval_rects

try:
    import bpy
except ImportError:
    bpy = None


def timeline():
    return TimelineView('s', (TimelineCharacter('yellow', 'Yellow'), TimelineCharacter('white', 'White')),
        (TimelineKey('key', 'yellow', 7, 'visible', 'obs'),), (),
        (TimelineMotionInterval('yellow', 5, 9),))


class MotionPresentationTests(unittest.TestCase):
    def test_preview_motion_status_is_independent_of_authoritative_identity(self):
        private = SimpleNamespace(state=SimpleNamespace(value='analyzed-with-candidates'),
            analysis_revision_id='analysis', source_id='s', frame=7, candidates=(),
            character_assignments=(SimpleNamespace(character_id='yellow', observation_id='obs'),),
            characters=(SimpleNamespace(character_id='yellow', display_name='Yellow'),
                        SimpleNamespace(character_id='white', display_name='White')),
            motion_untrusted_character_ids=('yellow',))
        view = present_review(private)
        rows = {row.character_id: row for row in view.characters}
        self.assertTrue(rows['yellow'].motion_untrusted)
        self.assertEqual(rows['yellow'].occupied_observation_id, 'obs')
        self.assertFalse(rows['white'].motion_untrusted)

    def test_projection_preserves_per_character_source_intervals(self):
        private = SimpleNamespace(source_id='s', characters=(SimpleNamespace(character_id='yellow', display_name='Yellow'),),
            keys=(), derived=(), untrusted_motion_intervals=(SimpleNamespace(character_id='yellow', start_frame=5, end_frame=9),))
        view = present_timeline(private)
        self.assertEqual(view.untrusted_motion_intervals, (TimelineMotionInterval('yellow', 5, 9),))

    def test_inclusive_cells_offset_zoom_and_display_scaling(self):
        for scale in (.75, 1, 2, 3):
            for zoom in (1, 4, 11):
                metrics = TimelineMetrics(scale, 32)
                view2d = SimpleNamespace(view_to_region=lambda x, y, clip=False: (x * zoom, y))
                spans = motion_interval_rects(timeline(), view2d, 2000, 600 * scale, 100, metrics=metrics)
                self.assertEqual(len(spans), 1)
                char, x0, x1, y0, y1 = spans[0]
                self.assertEqual(char, 'yellow')
                self.assertEqual((x0, x1), ((105 - .5) * zoom, (109 + .5) * zoom))
                lanes = dict(character_lanes(timeline(), 600 * scale, metrics=metrics))
                self.assertAlmostEqual((y0 + y1) / 2, lanes['yellow'])
                self.assertGreater(y0, lanes['white'])

    def test_untrusted_shading_does_not_intercept_key_hit_or_selection(self):
        view2d = SimpleNamespace(view_to_region=lambda x, y, clip=False: (x * 5, y))
        view = timeline()
        y = dict(character_lanes(view, 300))['yellow']
        before = repr(view)
        self.assertEqual(hit_review_key(view, view2d, 300, (35, y)), 'key')
        motion_interval_rects(view, view2d, 1000, 300)
        self.assertEqual(hit_review_key(view, view2d, 300, (35, y)), 'key')
        self.assertEqual(repr(view), before)

    def test_hold_hit_uses_exact_inclusive_rectangle_and_keys_take_priority(self):
        from blendcap_vta_workspace import hit_motion_interval
        for scale in (1, 2, 3):
            metrics = TimelineMetrics(scale, 13)
            view = timeline()
            view2d = SimpleNamespace(view_to_region=lambda x, y, clip=False: (x * 30, y))
            spans = motion_interval_rects(view, view2d, 5000, 300*scale, 100,
                                         metrics=metrics, include_interval=True)
            interval, x0, x1, y0, y1 = spans[0]
            for point in ((x0, y0), (x1, y1), (105*30, (y0+y1)/2)):
                self.assertEqual(hit_motion_interval(view, view2d, 5000, 300*scale, point, 100,
                                                    metrics=metrics), interval)
            self.assertIsNone(hit_motion_interval(view, view2d, 5000, 300*scale,
                                                 (107*30, (y0+y1)/2), 100, metrics=metrics))
            self.assertIsNone(hit_motion_interval(view, view2d, 100, 300*scale,
                                                 (105*30, (y0+y1)/2), 100, metrics=metrics))

    def test_offscreen_and_hidden_character_spans_not_painted(self):
        view2d = SimpleNamespace(view_to_region=lambda x, y, clip=False: (x * 5, y))
        self.assertEqual(motion_interval_rects(timeline(), view2d, 20, 300, 100), ())
        self.assertEqual(motion_interval_rects(timeline(), view2d, 100, 100, first=1), ())

    def test_backend_routes_existing_frame_conversion_and_expected_hash(self):
        core = SimpleNamespace(apply_motion_trust_many_and_persist=mock.Mock(return_value=('receipt',)))
        backend = PrivateCoreBackend(core)
        project = SimpleNamespace(characters=(SimpleNamespace(character_id='yellow'),), to_json=lambda: 'state')
        location = ReviewLocation(Path('companion'), Path('video'), 46, 147)
        with mock.patch.object(backend, '_load_project_source', return_value=(project, dict(source_id='s', start_frame=0, end_frame=162))):
            self.assertEqual(backend.set_motion_trust(location, 'yellow', 168, 191, action='mark'), 'receipt')
        call = core.apply_motion_trust_many_and_persist.call_args
        self.assertEqual(call.args, (Path('companion'), ('yellow',), 's', 67, 90))
        self.assertEqual(call.kwargs['action'], 'mark')
        self.assertEqual(len(call.kwargs['expected_project_sha256']), 64)

    def test_backend_rejects_bad_range_or_missing_character_before_core(self):
        core = SimpleNamespace(apply_motion_trust_many_and_persist=mock.Mock())
        backend = PrivateCoreBackend(core)
        project = SimpleNamespace(characters=(SimpleNamespace(character_id='yellow'),), to_json=lambda: 'state')
        location = ReviewLocation(Path('companion'), Path('video'), 0, 1)
        with mock.patch.object(backend, '_load_project_source', return_value=(project, dict(source_id='s', start_frame=0, end_frame=162))):
            for char, start, end in (('missing', 2, 3), ('yellow', 5, 4), ('yellow', 0, 3)):
                with self.assertRaises(VtaReviewUiError):
                    backend.set_motion_trust(location, char, start, end, action='mark')
        core.apply_motion_trust_many_and_persist.assert_not_called()


@unittest.skipUnless(bpy is not None, 'requires Blender background runtime')
class MotionBlenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        package = 'blendcap_motion_test'
        spec = importlib.util.spec_from_file_location(package, root / '__init__.py', submodule_search_locations=[str(root)])
        addon = importlib.util.module_from_spec(spec)
        sys.modules[package] = addon
        spec.loader.exec_module(addon)
        cls.ui = sys.modules[package + '.blendcap.vta_review_ui']
        cls.workspace = sys.modules[package + '.blendcap.vta_workspace']

    def context(self):
        return SimpleNamespace(window_manager=SimpleNamespace(blendcap_vta_motion_start=67, blendcap_vta_motion_end=90),
                               scene=SimpleNamespace(frame_current=67, frame_set=mock.Mock()))

    def test_mark_and_clear_preserve_character_playhead_and_request_redraw(self):
        ui = self.ui
        context = self.context()
        backend = SimpleNamespace(set_motion_trust_for_characters=mock.Mock(), update_tracking=mock.Mock())
        for action in ('MARK', 'CLEAR'):
            operator = SimpleNamespace(action=action, report=mock.Mock())
            with mock.patch.object(ui, '_edit_available', return_value=True), \
                 mock.patch.object(ui, '_location_from_context', return_value='location'), \
                 mock.patch.object(ui._selection, 'character_id', 'yellow'), \
                 mock.patch.object(ui, '_is_timeline_context', return_value=True), \
                 mock.patch.object(ui, '_location', 'location'), \
                 mock.patch.object(ui, 'timeline_character_selection', return_value=SimpleNamespace(character_ids=('yellow',))), \
                 mock.patch.object(ui, '_get_backend', return_value=backend), \
                 mock.patch.object(ui, 'refresh', return_value=True) as refresh, \
                 mock.patch.object(ui.vta_invalidation, 'request_redraw') as redraw:
                result = ui.BLENDCAP_OT_vta_motion_trust.execute(operator, context)
                self.assertEqual(result, {'FINISHED'})
                self.assertEqual(ui._selection.character_id, 'yellow')
                backend.set_motion_trust_for_characters.assert_called_with('location', ('yellow',), 67, 90,
                    action='mark' if action == 'MARK' else 'clear')
                self.assertEqual(refresh.call_args_list, [mock.call(context, force=True)] * 2)
                redraw.assert_called_once()
            context.scene.frame_set.assert_not_called()
            backend.update_tracking.assert_not_called()

    def test_analysis_lock_blocks_without_core_call(self):
        ui = self.ui
        operator = SimpleNamespace(action='MARK', report=mock.Mock())
        with mock.patch.object(ui, '_analysis_locked', return_value=True), \
             mock.patch.object(ui, '_get_backend') as backend:
            self.assertEqual(ui.BLENDCAP_OT_vta_motion_trust.execute(operator, self.context()), {'CANCELLED'})
            backend.assert_not_called()

    def test_use_current_changes_controls_only(self):
        ui = self.ui
        context = self.context()
        operator = SimpleNamespace(action='START_CURRENT', report=mock.Mock())
        with mock.patch.object(ui, '_edit_available', return_value=True), \
             mock.patch.object(ui, '_location_from_context', return_value=SimpleNamespace(blender_frame=81)), \
             mock.patch.object(ui, '_is_timeline_context', return_value=True), \
             mock.patch.object(ui, '_get_backend') as backend:
            self.assertEqual(ui.BLENDCAP_OT_vta_motion_trust.execute(operator, context), {'FINISHED'})
            self.assertEqual((context.window_manager.blendcap_vta_motion_start, context.window_manager.blendcap_vta_motion_end), (81, 90))
            backend.assert_not_called()

    def test_panel_controls_status_and_current_character_only(self):
        ui = self.ui
        context = self.context()
        calls = []
        class Layout:
            def column(self): return self
            def row(self, **kwargs): return self
            def label(self, **kwargs): calls.append(('label', kwargs['text']))
            def prop(self, target, key, **kwargs): calls.append(('prop', kwargs['text']))
            def operator(self, name, **kwargs):
                calls.append(('operator', kwargs['text']))
                return SimpleNamespace()
        location = SimpleNamespace(blender_frame=1, source_frame=0)
        with mock.patch.object(ui, '_view', SimpleNamespace(characters=(SimpleNamespace(character_id='yellow', motion_untrusted=True),))), \
             mock.patch.object(ui._selection, 'character_id', 'yellow'), \
             mock.patch.object(ui, '_analysis_locked', return_value=False), \
             mock.patch.object(ui, '_timeline', timeline()), mock.patch.object(ui, '_location', location):
            ui.draw_motion_trust(Layout(), context)
        self.assertIn(('prop', 'Hold Start'), calls)
        self.assertIn(('prop', 'Hold End'), calls)
        self.assertIn(('label', 'Animation Held'), calls)
        self.assertIn(('label', 'Hold: 6–10'), calls)
        self.assertIn(('operator', 'Clear Hold Range'), calls)
