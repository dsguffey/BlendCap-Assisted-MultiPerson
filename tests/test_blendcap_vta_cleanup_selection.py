"""Contextual Character selection, Cleanup routing, and bounded mouse gestures."""

from contextlib import ExitStack
import importlib.util
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest import mock

from blendcap_vta_review import PrivateCoreBackend, ReviewLocation, SelectionState, VtaReviewUiError, tracking_update_error
from blendcap_vta_timeline import TimelineCharacter, TimelineCharacterSelection, TimelineKey, TimelineView
from blendcap_vta_workspace import TimelineMetrics, character_lanes, characters_in_box, derived_mark_color

try:
    import bpy
except ImportError:
    bpy = None


def view():
    return TimelineView('s', tuple(TimelineCharacter(cid, cid.title()) for cid in ('a', 'b', 'c')),
                        (TimelineKey('ka', 'a', 7, 'visible', 'obs-a'),
                         TimelineKey('kb', 'b', 7, 'visible', 'obs-b')))


class CharacterSelectionTests(unittest.TestCase):
    def test_empty_single_and_ctrl_toggle_active(self):
        selection = TimelineCharacterSelection()
        self.assertEqual(selection.character_ids, ())
        self.assertIsNone(selection.active_id)
        self.assertTrue(selection.click(view(), 'b'))
        self.assertEqual((selection.character_ids, selection.active_id), (('b',), 'b'))
        selection.click(view(), 'a', extend=True)
        self.assertEqual((selection.character_ids, selection.active_id), (('a', 'b'), 'a'))
        selection.click(view(), 'a', extend=True)
        self.assertEqual((selection.character_ids, selection.active_id), (('b',), 'b'))
        selection.click(view(), 'b', extend=True)
        self.assertEqual((selection.character_ids, selection.active_id), ((), None))

    def test_normal_click_replaces_and_reconcile_uses_display_order(self):
        selection = TimelineCharacterSelection()
        selection.select_box(view(), ('c', 'b'), active_id='c')
        selection.click(view(), 'a')
        self.assertEqual(selection.character_ids, ('a',))
        selection.select_box(view(), ('c', 'b', 'a'))
        reordered = TimelineView('s', tuple(reversed(view().characters)), ())
        selection.click(reordered, 'a', extend=True)
        self.assertEqual(selection.active_id, 'c')
        selection.reconcile(TimelineView('s', (TimelineCharacter('b', 'B'),), ()))
        self.assertEqual((selection.character_ids, selection.active_id), (('b',), 'b'))

    def test_invalid_selection_is_noop_and_empty_box_clears(self):
        selection = TimelineCharacterSelection()
        selection.click(view(), 'a')
        self.assertFalse(selection.click(view(), 'missing'))
        self.assertFalse(selection.select_box(view(), ('b', 'missing')))
        self.assertEqual(selection.character_ids, ('a',))
        selection.select_box(view(), (), extend=True)
        self.assertEqual(selection.character_ids, ('a',))
        selection.select_box(view(), ())
        self.assertIsNone(selection.active_id)

    def test_channel_box_selects_rows_not_keys_at_multiple_scales(self):
        for scale in (1, 2, 3):
            metrics = TimelineMetrics(scale, 18)
            height = 300 * scale
            lanes = dict(character_lanes(view(), height, metrics=metrics))
            start, end = (1, lanes['c']), (20, lanes['a'])
            self.assertEqual(characters_in_box(view(), height, start, end, metrics=metrics), ('a', 'b', 'c'))
            self.assertEqual(characters_in_box(view(), height, end, start, metrics=metrics), ('a', 'b', 'c'))

    def test_tracking_tick_colors_are_state_only_not_selection(self):
        self.assertEqual(derived_mark_color('visible'), (.55, .68, .72, .55))
        self.assertEqual(derived_mark_color('unresolved'), (.48, .5, .52, .4))
        message = tracking_update_error(VtaReviewUiError('Motion is Untrusted here; clear Motion Trust first'))
        self.assertIn('Hold', message)
        self.assertNotIn('Untrusted', message)
        self.assertNotIn('Motion Trust', message)

    def test_backend_bulk_uses_one_core_command_and_visible_frame_mapping(self):
        core = NS(apply_motion_trust_many_and_persist=mock.Mock(return_value=('b', 'a')))
        backend = PrivateCoreBackend(core)
        project = NS(characters=tuple(NS(character_id=cid) for cid in ('a', 'b', 'c')),
                     to_json=lambda: 'canonical')
        location = ReviewLocation(Path('companion'), Path('video'), 7, 108)
        with mock.patch.object(backend, '_load_project_source', return_value=(project,
                dict(source_id='s', start_frame=0, end_frame=150))):
            self.assertEqual(backend.set_motion_trust_for_characters(location, ('b', 'a'),
                             110, 120, action='mark'), ('b', 'a'))
            core.apply_motion_trust_many_and_persist.assert_called_once_with(
                Path('companion'), ('a', 'b'), 's', 9, 19, action='mark',
                expected_project_sha256=__import__('hashlib').sha256(b'canonical').hexdigest())
            for ids in ((), ('a', 'a'), ('a', 'missing')):
                with self.assertRaises(VtaReviewUiError):
                    backend.set_motion_trust_for_characters(location, ids, 110, 120, action='mark')
        self.assertEqual(core.apply_motion_trust_many_and_persist.call_count, 1)


@unittest.skipUnless(bpy is not None, 'requires Blender background runtime')
class CleanupBlenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        package = 'blendcap_cleanup_test'
        spec = importlib.util.spec_from_file_location(package, root / '__init__.py',
                                                      submodule_search_locations=[str(root)])
        addon = importlib.util.module_from_spec(spec)
        sys.modules[package] = addon
        spec.loader.exec_module(addon)
        cls.ui = sys.modules[package + '.blendcap.vta_review_ui']
        cls.workspace = sys.modules[package + '.blendcap.vta_workspace']

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.timeline = view()
        self.location = ReviewLocation(Path('companion'), Path('video'), 7, 108)
        self.context = NS(area=NS(type='DOPESHEET_EDITOR'), screen=object(),
            scene=NS(frame_current=108, frame_set=mock.Mock()),
            preferences=bpy.context.preferences,
            window_manager=NS(blendcap_vta_motion_start=110, blendcap_vta_motion_end=120,
                              modal_handler_add=mock.Mock()))
        ui = self.ui
        self.real_timeline_context = ui._is_timeline_context
        review = NS(characters=tuple(NS(character_id=cid, occupied_observation_id=None,
                           display_name=cid.title(), motion_untrusted=False) for cid in ('a', 'b', 'c')), source_frame=7)
        for name, value in (('_timeline', self.timeline), ('_location', self.location),
                ('_view', review), ('_selection', SelectionState()), ('_character_selections', {}),
                ('_hold_selections', {})):
            self.stack.enter_context(mock.patch.object(ui, name, value))
        self.stack.enter_context(mock.patch.object(ui, '_is_timeline_context', side_effect=lambda c:
            getattr(getattr(c, 'area', None), 'type', None) == 'DOPESHEET_EDITOR'))
        self.stack.enter_context(mock.patch.object(ui, '_analysis_locked', return_value=False))
        self.stack.enter_context(mock.patch.object(ui.vta_invalidation, 'request_redraw'))
        self.stack.enter_context(mock.patch.object(ui._review_key_selection, '_ids', set()))
        self.stack.enter_context(mock.patch.object(ui._review_key_selection, '_active_id', None))

    def test_loaded_legacy_wrestling_rows_work_from_empty_session_with_real_scope(self):
        # Structural equivalent of the saved QA file: tagged legacy workspace,
        # four persisted rows including old test Characters, existing keys and
        # Cleanup, but no new transient selection stored in the .blend.
        from blendcap_vta_area_mode import WORKSPACE_TAG
        from blendcap_vta_timeline import TimelineMotionInterval
        ui, workspace, context = self.ui, self.workspace, self.context
        loaded = TimelineView('s', tuple(TimelineCharacter(cid, cid) for cid in
            ('a', 'QA independence only', 'white', 'yellow')),
            (TimelineKey('white-key', 'white', 0, 'visible', 'white-obs'),), (),
            (TimelineMotionInterval('white', 70, 85), TimelineMotionInterval('yellow', 67, 90)))
        ui._timeline = loaded
        ui._character_selections.clear()
        context.area.ui_type = 'DOPESHEET'
        class Screen(dict):
            areas = (context.area,)
        context.screen = Screen()
        context.workspace = {WORKSPACE_TAG: 1}
        context.region = NS(type='CHANNELS', height=701, width=401, x=0, y=0)
        cls = workspace.BLENDCAP_OT_vta_timeline_select
        snapshot = repr(loaded)
        with mock.patch.object(ui, '_is_timeline_context', side_effect=self.real_timeline_context):
            self.assertTrue(cls.poll(context))
            self.assertEqual(ui.timeline_character_selection(context).character_ids, ())
            lanes = dict(character_lanes(loaded, context.region.height, metrics=workspace._metrics(context)))
            for cid, ctrl, expected in (('white', False, ('white',)),
                    ('yellow', True, ('white', 'yellow')), ('yellow', True, ('white',))):
                operator = NS()
                operator._finish_box = MethodType(cls._finish_box, operator)
                event = NS(type='LEFTMOUSE', value='PRESS', ctrl=ctrl, shift=False,
                    mouse_region_x=20, mouse_region_y=lanes[cid], mouse_x=20, mouse_y=lanes[cid])
                self.assertEqual(cls.invoke(operator, context, event), {'RUNNING_MODAL'})
                event.value = 'RELEASE'
                self.assertEqual(cls.modal(operator, context, event), {'FINISHED'})
                self.assertEqual(ui.timeline_character_selection(context).character_ids, expected)
                self.assertEqual(ui._selection.character_id, cid if len(expected) > 1 else 'white')
        self.assertEqual(repr(loaded), snapshot)
        context.scene.frame_set.assert_not_called()

    def test_rows_sync_active_globally_but_sets_are_editor_local(self):
        ui, context = self.ui, self.context
        ui.select_timeline_character(context, 'a')
        ui.select_timeline_character(context, 'b', extend=True)
        selection = ui.timeline_character_selection(context)
        self.assertEqual((selection.character_ids, selection.active_id), (('a', 'b'), 'b'))
        self.assertEqual(ui._selection.character_id, 'b')
        self.assertEqual(context.window_manager.blendcap_vta_character, 'b')
        other = NS(area=NS(type='DOPESHEET_EDITOR'), screen=context.screen)
        self.assertEqual(ui.timeline_character_selection(other).character_ids, ())
        self.assertIsNone(ui.current_character_id(other))
        ui.select_timeline_character(context, 'b', extend=True)
        self.assertEqual(ui._selection.character_id, 'a')
        ui.select_timeline_character(context, 'a', extend=True)
        self.assertEqual(ui._selection.character_id, '')
        context.scene.frame_set.assert_not_called()

    def test_frame_refresh_preserves_set_source_change_clears_it(self):
        ui, context = self.ui, self.context
        ui.select_timeline_characters(context, ('a', 'b'))
        ui._timeline = view()  # Rebuilt projection does not erase selection.
        ui._location = ReviewLocation(Path('companion'), Path('video'), 8, 109)
        self.assertEqual(ui.timeline_character_selection(context).character_ids, ('a', 'b'))
        ui._location = ReviewLocation(Path('other'), Path('video'), 8, 109)
        self.assertEqual(ui.timeline_character_selection(context).character_ids, ())

    def execute(self, action):
        return self.ui.BLENDCAP_OT_vta_motion_trust.execute(NS(action=action, report=mock.Mock()), self.context)

    def test_no_rows_never_falls_back_to_sidebar(self):
        self.ui._selection.character_id = 'c'
        with mock.patch.object(self.ui, '_location_from_context', return_value=self.location), \
             mock.patch.object(self.ui, '_get_backend') as backend:
            for action in ('MARK', 'CLEAR'):
                self.assertEqual(self.execute(action), {'CANCELLED'})
            backend.assert_not_called()
        self.assertFalse(self.ui._action_availability(self.context)['rename-character'])

    def test_bulk_targets_only_rows_even_when_sidebar_changes(self):
        ui = self.ui
        ui.select_timeline_characters(self.context, ('b', 'a'))
        ui._selection.character_id = 'c'
        backend = NS(set_motion_trust_for_characters=mock.Mock(), update_tracking=mock.Mock())
        with mock.patch.object(ui, '_location_from_context', return_value=self.location), \
             mock.patch.object(ui, 'refresh', return_value=True), \
             mock.patch.object(ui, '_get_backend', return_value=backend):
            for action in ('MARK', 'CLEAR'):
                self.assertEqual(self.execute(action), {'FINISHED'})
                backend.set_motion_trust_for_characters.assert_called_with(self.location, ('a', 'b'),
                    110, 120, action=action.lower())
        backend.update_tracking.assert_not_called()
        self.context.scene.frame_set.assert_not_called()

    def test_removed_target_during_refresh_prevents_partial_command(self):
        ui = self.ui
        ui.select_timeline_characters(self.context, ('a', 'b'))
        def refresh(*args, **kwargs):
            ui._timeline = TimelineView('s', (TimelineCharacter('a', 'A'),), ())
            return True
        with mock.patch.object(ui, '_location_from_context', return_value=self.location), \
             mock.patch.object(ui, 'refresh', side_effect=refresh), \
             mock.patch.object(ui, '_get_backend') as backend:
            self.assertEqual(self.execute('MARK'), {'CANCELLED'})
            backend.assert_not_called()

    def test_boundary_commands_are_independent_and_do_not_author(self):
        with mock.patch.object(self.ui, '_location_from_context', return_value=self.location), \
             mock.patch.object(self.ui, '_get_backend') as backend:
            self.assertEqual(self.execute('START_CURRENT'), {'FINISHED'})
            self.assertEqual((self.context.window_manager.blendcap_vta_motion_start,
                              self.context.window_manager.blendcap_vta_motion_end), (108, 120))
            self.context.window_manager.blendcap_vta_motion_start = 100
            self.assertEqual(self.execute('END_CURRENT'), {'FINISHED'})
            self.assertEqual((self.context.window_manager.blendcap_vta_motion_start,
                              self.context.window_manager.blendcap_vta_motion_end), (100, 108))
            backend.assert_not_called()

    def test_tracking_uses_all_selected_not_sidebar_active(self):
        ui = self.ui
        ui.select_timeline_characters(self.context, ('a', 'b'))
        ui._selection.character_id = 'c'
        outcomes = tuple(NS(character_id=cid, update='result', error=None) for cid in ('a', 'b'))
        backend = NS(update_tracking_for_characters=mock.Mock(return_value=(outcomes, None, None)))
        with mock.patch.object(ui, '_location_from_context', return_value=self.location), \
             mock.patch.object(ui, '_get_backend', return_value=backend), \
             mock.patch.object(ui, 'refresh', return_value=True), \
             mock.patch.object(ui, 'tracking_update_success', return_value='done'):
            operator = NS(report=mock.Mock())
            self.assertEqual(ui.BLENDCAP_OT_vta_update_tracking.execute(operator, self.context), {'FINISHED'})
        backend.update_tracking_for_characters.assert_called_once_with(self.location, ('a', 'b'))
        ui.select_timeline_characters(self.context, ())
        ui._selection.character_id = 'c'
        backend.update_tracking_for_characters.reset_mock()
        with mock.patch.object(ui, '_location_from_context', return_value=self.location), \
             mock.patch.object(ui, '_get_backend', return_value=backend):
            self.assertEqual(ui.BLENDCAP_OT_vta_update_tracking.execute(operator, self.context), {'CANCELLED'})
        backend.update_tracking_for_characters.assert_not_called()

    def test_tracking_poll_zero_single_and_stale_refresh_are_guarded(self):
        ui, context = self.ui, self.context
        operator = NS(report=mock.Mock())
        ui._selection.character_id = 'c'
        self.assertFalse(ui.BLENDCAP_OT_vta_update_tracking.poll(context))
        self.assertFalse(ui._action_availability(context)['update-tracking'])
        ui.select_timeline_character(context, 'b')
        self.assertTrue(ui.BLENDCAP_OT_vta_update_tracking.poll(context))
        backend = NS(update_tracking_for_characters=mock.Mock(return_value=(
            (NS(character_id='b', update='result', error=None),), None, None)))
        with mock.patch.object(ui, '_location_from_context', return_value=self.location), \
             mock.patch.object(ui, '_get_backend', return_value=backend), \
             mock.patch.object(ui, 'refresh', return_value=True), \
             mock.patch.object(ui, 'tracking_update_success', return_value='done'):
            self.assertEqual(ui.BLENDCAP_OT_vta_update_tracking.execute(operator, context), {'FINISHED'})
        backend.update_tracking_for_characters.assert_called_once_with(self.location, ('b',))
        backend.update_tracking_for_characters.reset_mock()
        def refresh(*args, **kwargs):
            ui._timeline = TimelineView('s', (TimelineCharacter('a', 'A'),), ())
            return True
        with mock.patch.object(ui, '_location_from_context', return_value=self.location), \
             mock.patch.object(ui, '_get_backend', return_value=backend), \
             mock.patch.object(ui, 'refresh', side_effect=refresh):
            self.assertEqual(ui.BLENDCAP_OT_vta_update_tracking.execute(operator, context), {'CANCELLED'})
        backend.update_tracking_for_characters.assert_not_called()

    def test_preview_update_uses_opted_in_timeline_set_never_global_active(self):
        import sys
        helper = sys.modules[self.ui.__package__.rsplit('.', 1)[0] + '.blendcap_vta_workspace']
        self.ui.select_timeline_characters(self.context, ('a', 'b'))
        preview = NS(area=NS(type='SEQUENCE_EDITOR'), screen=self.context.screen, workspace={})
        self.ui._selection.character_id = 'c'
        with mock.patch.object(helper, 'review_area', return_value=self.context.area):
            self.assertEqual(self.ui.tracking_character_ids(preview), ('a', 'b'))
        with mock.patch.object(helper, 'review_area', return_value=None):
            self.assertEqual(self.ui.tracking_character_ids(preview), ())

    def test_hold_click_exact_selection_fields_and_no_authoring_or_navigation(self):
        from blendcap_vta_timeline import TimelineMotionInterval
        ui, workspace, context = self.ui, self.workspace, self.context
        interval = TimelineMotionInterval('b', 9, 19)
        other = TimelineMotionInterval('c', 20, 22)
        ui._timeline = TimelineView('s', self.timeline.characters, self.timeline.keys, (), (interval, other))
        ui.select_timeline_key(context, 'ka')
        keys_before = ui._review_key_selection.keyframe_ids
        snapshot = repr(ui._timeline)
        context.region = NS(type='WINDOW', width=2000, height=300,
                            view2d=NS(view_to_region=lambda x, y, clip=False: (x*10, y)))
        metrics = workspace._metrics(context)
        y = dict(character_lanes(ui._timeline, 300, metrics=metrics))['b']
        event = NS(type='LEFTMOUSE', ctrl=False, shift=False, mouse_region_x=1150, mouse_region_y=y)
        with mock.patch.object(workspace, '_box_gesture', None), \
             mock.patch.object(ui, '_get_backend') as backend:
            result = workspace.BLENDCAP_OT_vta_timeline_select.invoke(NS(), context, event)
            self.assertEqual(result, {'FINISHED'})
            backend.assert_not_called()
        self.assertEqual(ui.selected_hold_interval(context), interval)
        self.assertEqual(ui.timeline_character_selection(context).character_ids, ('a', 'b'))
        self.assertEqual(ui.current_character_id(context), 'b')
        self.assertEqual(ui._selection.character_id, 'b')
        self.assertEqual((context.window_manager.blendcap_vta_motion_start,
                          context.window_manager.blendcap_vta_motion_end), (110, 120))
        self.assertEqual(ui._review_key_selection.keyframe_ids, keys_before)
        self.assertEqual(repr(ui._timeline), snapshot)
        context.scene.frame_set.assert_not_called()
        context.window_manager.modal_handler_add.assert_not_called()
        context.window_manager.blendcap_vta_motion_start = 113
        self.assertEqual(repr(ui._timeline), snapshot)  # Controls aren't live authority.
        ui.select_hold_interval(context, other)
        self.assertEqual(ui.selected_hold_interval(context), other)
        self.assertEqual(ui.timeline_character_selection(context).character_ids, ('a', 'b', 'c'))
        self.assertEqual((context.window_manager.blendcap_vta_motion_start,
                          context.window_manager.blendcap_vta_motion_end), (121, 123))
        ui._location = ReviewLocation(Path('companion'), Path('video'), 8, 109)
        self.assertEqual(ui.selected_hold_interval(context), other)  # Ordinary scrub.
        ui._location = ReviewLocation(Path('companion'), Path('video'), 8, 110)
        self.assertIsNone(ui.selected_hold_interval(context))  # Changed strip mapping.
        ui.select_hold_interval(context, other)
        ui._timeline = self.timeline  # Explicit clear/merge invalidates transient range.
        self.assertIsNone(ui.selected_hold_interval(context))

    def test_window_draw_all_tracking_rows_and_only_selected_hold_outline(self):
        from blendcap_vta_timeline import TimelineDerived, TimelineMotionInterval
        from blendcap_vta_visuals import TRACKING_COLOR, UNRESOLVED_COLOR
        ui, workspace, context = self.ui, self.workspace, self.context
        interval = TimelineMotionInterval('b', 9, 19)
        derived = tuple(TimelineDerived(cid, 12, state) for cid, state in
                        (('a', 'visible'), ('b', 'partially-visible'), ('c', 'unresolved')))
        ui._timeline = TimelineView('s', self.timeline.characters, (), derived, (interval,))
        context.region = NS(type='WINDOW', width=2000, height=300,
            view2d=NS(view_to_region=lambda x, y, clip=False: (x*10, y),
                      region_to_view=lambda x, y: (x/10, y)))
        lines = []
        fake_gpu = NS(shader=NS(from_builtin=mock.Mock(return_value=NS())))
        fake_batch = NS(batch_for_shader=mock.Mock())
        # Mock GPU/font sinks, not the renderer's all-row logic or selection state.
        with mock.patch.object(workspace, 'bpy', NS(context=context)), \
             mock.patch.object(workspace, '_scope', return_value=True), \
             mock.patch.object(workspace, '_line', side_effect=lambda s,b,p,c: lines.append((p,c))), \
             mock.patch.object(workspace, '_fill'), \
             mock.patch.dict(sys.modules, {'gpu': fake_gpu, 'gpu_extras.batch': fake_batch,
                                          'blf': NS(position=mock.Mock(), size=mock.Mock(), color=mock.Mock(), draw=mock.Mock())}):
            for selected in ((), ('a',), ('a', 'b')):
                lines.clear()
                ui.select_timeline_characters(context, selected)
                workspace._draw_window()
                ticks = [(p,c) for p,c in lines if c in (TRACKING_COLOR, UNRESOLVED_COLOR)]
                self.assertEqual(len(ticks), 3)
                self.assertEqual([c for p,c in ticks], [TRACKING_COLOR, TRACKING_COLOR, UNRESOLVED_COLOR])
            lines.clear()
            ui.select_hold_interval(context, interval)
            workspace._draw_window()
            color = (*context.preferences.themes[0].user_interface.wcol_list_item.text_sel, 1)
            self.assertEqual(len([p for p,c in lines if c == color and len(p) == 5]), 1)

    def test_review_key_selection_keeps_time_and_activates_its_character(self):
        ui = self.ui
        ui.select_timeline_key(self.context, 'ka')
        ui.select_timeline_key(self.context, 'kb', extend=True)
        self.assertEqual(ui.timeline_character_selection(self.context).character_ids, ('a', 'b'))
        self.assertEqual(ui.current_character_id(self.context), 'b')
        self.assertEqual(ui._review_key_selection.keyframe_ids, ('ka', 'kb'))
        ui.select_timeline_key(self.context, 'ka', activate=True)
        self.assertEqual(ui.current_character_id(self.context), 'a')
        self.context.scene.frame_set.assert_not_called()

    def test_theme_uses_native_selected_active_and_unselected_colors(self):
        ui = self.ui
        ui.select_timeline_characters(self.context, ('a', 'b'))
        colors = [self.workspace._row_style(self.context, cid, 'a') for cid in ('a', 'b', 'c')]
        self.assertEqual(colors[0][0], tuple(bpy.context.preferences.themes[0].user_interface.wcol_list_item.inner_sel))
        self.assertIsNotNone(colors[0][2])
        self.assertIsNone(colors[1][2])
        self.assertEqual(len({c[0] for c in colors}), 3)
        self.assertTrue(all(len(c[1]) == 4 for c in colors))

    def test_transparent_theme_row_is_composited_not_opaque_white(self):
        theme = NS(user_interface=NS(wcol_list_item=NS(
            inner=(1, 1, 1, 0), inner_sel=(.2, .4, .7, 1),
            text=(.8, .8, .8), text_sel=(1, 1, 1), outline_sel=(.1, .1, .1, 1))),
            dopesheet_editor=NS(space=NS(back=(.1, .12, .15))))
        self.context.preferences = NS(themes=[theme])
        self.assertEqual(self.workspace._row_style(self.context, 'a', None)[0], (.1, .12, .15, 1))
        self.ui.select_timeline_characters(self.context, ('a', 'b'))
        active = self.workspace._row_style(self.context, 'a', 'a')[0]
        selected = self.workspace._row_style(self.context, 'b', 'a')[0]
        self.assertEqual(active, (.2, .4, .7, 1))
        self.assertNotEqual(selected, active)
        self.assertNotEqual(selected, (1, 1, 1, 1))

    def test_channel_click_drag_cancel_do_not_select_keys_or_move_time(self):
        ui, workspace, context = self.ui, self.workspace, self.context
        context.region = NS(type='CHANNELS', width=200, height=240, x=0, y=0)
        metrics = TimelineMetrics()
        lanes = dict(character_lanes(self.timeline, 240, metrics=metrics))
        event = lambda kind, point, value='PRESS', ctrl=False: NS(type=kind, value=value,
            ctrl=ctrl, shift=False, mouse_region_x=point[0], mouse_region_y=point[1],
            mouse_x=point[0], mouse_y=point[1])
        cls = workspace.BLENDCAP_OT_vta_timeline_select
        with mock.patch.object(workspace, '_state', return_value=(self.timeline, (), None, 101, 0, None)), \
             mock.patch.object(workspace, '_scope', return_value=True), \
             mock.patch.object(workspace, '_metrics', return_value=metrics), \
             mock.patch.object(ui, 'select_timeline_keys') as key_selection:
            for gesture in ('click', 'ctrl', 'drag', 'cancel'):
                operator = NS()
                operator._finish_box = MethodType(cls._finish_box, operator)
                self.addCleanup(setattr, workspace, '_box_gesture', None)
                start = (20, lanes['a'] if gesture == 'click' else lanes['b'])
                self.assertEqual(cls.invoke(operator, context, event('LEFTMOUSE', start,
                    ctrl=gesture == 'ctrl')), {'RUNNING_MODAL'})
                if gesture == 'cancel':
                    before = ui.timeline_character_selection(context).character_ids
                    self.assertEqual(cls.modal(operator, context, event('ESC', start)), {'CANCELLED'})
                    self.assertEqual(ui.timeline_character_selection(context).character_ids, before)
                else:
                    end = (100, lanes['c']) if gesture == 'drag' else start
                    self.assertEqual(cls.modal(operator, context, event('LEFTMOUSE', end, 'RELEASE')), {'FINISHED'})
                    expected = ('a',) if gesture == 'click' else (('a', 'b') if gesture == 'ctrl' else ('b', 'c'))
                    self.assertEqual(ui.timeline_character_selection(context).character_ids, expected)
            key_selection.assert_not_called()
            self.assertEqual(ui._review_key_selection.keyframe_ids, ())
            context.scene.frame_set.assert_not_called()

    def test_commands_menu_and_panel_share_actions_and_no_selection_availability(self):
        ui = self.ui
        calls = []
        class Layout:
            enabled = True
            def row(self, **kwargs): return Layout()
            def operator(self, name, **kwargs):
                result = NS()
                calls.append((name, kwargs['text'], self, result))
                return result
        for menu in (False, True):
            calls.clear()
            ui.draw_cleanup_commands(Layout(), self.context, menu=menu)
            self.assertEqual([c[3].action for c in calls], ['START_CURRENT', 'END_CURRENT', 'MARK', 'CLEAR'])
            self.assertTrue(all(c[0] == 'blendcap.vta_motion_trust' for c in calls))
            self.assertEqual([c[2].enabled for c in calls], [True, True, False, False])
        ui.select_timeline_characters(self.context, ('a', 'b'))
        calls.clear()
        ui.draw_cleanup_commands(Layout(), self.context)
        self.assertTrue(all(c[2].enabled for c in calls))
        self.assertEqual([c[1] for c in calls][-2:], ['Hold Animation', 'Clear Hold Range'])


if __name__ == '__main__':
    unittest.main()
