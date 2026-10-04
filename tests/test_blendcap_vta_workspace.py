"""Deterministic scope, projection, and redraw contracts for VTA workspace."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

from blendcap_vta_timeline import TimelineCharacter, TimelineDerived, TimelineKey, TimelineView
from blendcap_vta_workspace import (
    WORKSPACE_TAG, character_lanes, hit_character, hit_review_key,
    grid_step, is_review_region, review_area,
)
from blendcap_vta_area_mode import MODE_KEY, ROLE_PREVIEW, ROLE_TIMELINE, role_for, set_role
from blendcap.vta_invalidation import invalidate_windows

try:
    import bpy
except ImportError:
    bpy = None


def data():
    return TimelineView('source', (
        TimelineCharacter('red-id', 'Red'), TimelineCharacter('black-id', 'Black'),
    ), (
        TimelineKey('durable-red', 'red-id', 33, 'visible', 'observation'),
        TimelineKey('durable-black', 'black-id', 52, 'occluded', None),
    ), (TimelineDerived('red-id', 34, 'visible'),))


class ScopeTests(unittest.TestCase):
    def test_only_tagged_workspace_unique_dopesheet_is_review_host(self):
        area = SimpleNamespace(type='DOPESHEET_EDITOR', ui_type='DOPESHEET')
        other = SimpleNamespace(type='SEQUENCE_EDITOR', ui_type='SEQUENCE_EDITOR')
        class Screen(dict):
            def __init__(self, areas):
                super().__init__()
                self.areas = areas
        screen = Screen((area, other))
        workspace = {WORKSPACE_TAG: 1}
        window = SimpleNamespace(type='WINDOW')
        channels = SimpleNamespace(type='CHANNELS')
        self.assertIs(review_area(workspace, screen), area)
        self.assertTrue(is_review_region(workspace, screen, area, window))
        self.assertTrue(is_review_region(workspace, screen, area, channels))
        self.assertFalse(is_review_region(workspace, screen, other, window))
        self.assertIsNone(review_area({}, screen))
        self.assertIsNone(review_area(workspace, None))
        screen.areas = (area, SimpleNamespace(type='DOPESHEET_EDITOR', ui_type='DOPESHEET'))
        self.assertIsNone(review_area(workspace, screen))
        set_role({}, screen, area, ROLE_TIMELINE)
        self.assertIs(review_area({}, screen), area)

    def test_key_and_character_hit_use_stable_ids_and_frame_offset(self):
        view = data()
        lanes = dict(character_lanes(view, 240))
        self.assertEqual(hit_character(view, 240, lanes['black-id']), 'black-id')
        self.assertIsNone(hit_character(view, 240, 1))
        view2d = SimpleNamespace(view_to_region=lambda frame, _y, clip=False: (frame * 2, 0))
        self.assertEqual(hit_review_key(view, view2d, 240,
                                        (266, lanes['red-id']), offset=100), 'durable-red')
        self.assertIsNone(hit_review_key(view, view2d, 240,
                                         (266, lanes['black-id']), offset=100))
        self.assertEqual(view.derived[0].source_frame, 34)

    def test_first_character_key_is_below_native_ruler_and_clickable(self):
        view = data()
        height = 240
        top_id, top_y = character_lanes(view, height)[0]
        self.assertEqual(top_id, 'red-id')
        self.assertLess(top_y, height - 60)
        view2d = SimpleNamespace(view_to_region=lambda frame, _y, clip=False: (frame * 2, 0))
        self.assertEqual(hit_review_key(view, view2d, height, (66, top_y)),
                         'durable-red')
        self.assertIsNone(hit_review_key(view, view2d, height,
                                         (66, height - 20)))

    def test_grid_cadence_stays_bounded_at_extreme_zoom(self):
        self.assertEqual(grid_step(200, 1000), 10)
        self.assertGreaterEqual(grid_step(100_000_000, 1000), 4_800_000)

    def test_one_invalidation_reaches_preview_sequencer_timeline_and_sidebar(self):
        areas = [SimpleNamespace(type=kind, tag_redraw=mock.Mock()) for kind in
                 ('SEQUENCE_EDITOR', 'DOPESHEET_EDITOR', 'VIEW_3D', 'PROPERTIES')]
        self.assertEqual(invalidate_windows([SimpleNamespace(
            screen=SimpleNamespace(areas=areas))]), 3)
        for area in areas[:3]:
            area.tag_redraw.assert_called_once_with()
        areas[3].tag_redraw.assert_not_called()


@unittest.skipUnless(bpy is not None, 'requires Blender background runtime')
class BlenderWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        package = 'blendcap_workspace_test'
        spec = importlib.util.spec_from_file_location(
            package, root / '__init__.py', submodule_search_locations=[str(root)])
        addon = importlib.util.module_from_spec(spec)
        sys.modules[package] = addon
        spec.loader.exec_module(addon)
        cls.addon = addon
        cls.workspace = sys.modules[package + '.blendcap.vta_workspace']
        cls.ui = sys.modules[package + '.blendcap.vta_review_ui']

    def test_bundled_workspace_contains_only_tagged_unique_review_host(self):
        path = Path(__file__).resolve().parents[1] / 'vta_workspace_mode.blend'
        before_scenes = set(bpy.data.scenes.keys())
        before_clips = set(bpy.data.movieclips.keys())
        with bpy.data.libraries.load(str(path), link=False) as (source, target):
            self.assertIn('Video to Animation', source.workspaces)
            target.workspaces = ['Video to Animation']
        workspace = target.workspaces[0]
        self.assertEqual(workspace.get(WORKSPACE_TAG), 1)
        self.assertEqual(len(workspace.screens), 1)
        screen = workspace.screens[0]
        self.assertIsInstance(screen.get(MODE_KEY), str)
        self.assertIsNotNone(review_area(workspace, screen))
        kinds = {area.type for area in screen.areas}
        self.assertTrue({'VIEW_3D', 'SEQUENCE_EDITOR', 'DOPESHEET_EDITOR'} <= kinds)
        preview = next(area for area in screen.areas if area.type == 'SEQUENCE_EDITOR')
        self.assertEqual(preview.spaces.active.view_type, 'PREVIEW')
        self.assertEqual(role_for(workspace, screen, preview), ROLE_PREVIEW)
        self.assertEqual(set(bpy.data.scenes.keys()), before_scenes)
        self.assertEqual(set(bpy.data.movieclips.keys()), before_clips)
        self.assertFalse(any(area.type == 'DOPESHEET_EDITOR' and
                             area.ui_type == 'DOPESHEET' for area in
                             bpy.data.workspaces['Layout'].screens[0].areas))

    def test_selection_and_mutation_notification_share_existing_state(self):
        ui = self.ui
        view = data()
        with mock.patch.object(ui, '_timeline', view), \
             mock.patch.object(ui, '_sync_properties') as notify:
            self.assertTrue(ui.select_timeline_character(object(), 'red-id'))
            self.assertEqual(ui._selection.character_id, 'red-id')
            self.assertFalse(ui.select_timeline_character(object(), 'not-a-character'))
            notify.assert_called_once()
        with mock.patch.object(ui, 'refresh', return_value=True) as refresh, \
             mock.patch.object(ui._selection, 'accept_review'), \
             mock.patch.object(ui.vta_invalidation, 'request_redraw') as redraw:
            ui._accept_mutated_review(object(), object())
            refresh.assert_called_once_with(mock.ANY, force=True)
            redraw.assert_called_once()

    def test_key_click_syncs_character_without_navigation(self):
        ui = self.ui
        scene = SimpleNamespace(frame_set=mock.Mock())
        context = SimpleNamespace(scene=scene)
        location = SimpleNamespace(blender_frame=100, source_frame=0)
        with mock.patch.object(ui, '_timeline', data()), \
             mock.patch.object(ui, '_location', location), \
             mock.patch.object(ui, '_owning_vse_scene', return_value=scene), \
             mock.patch.object(ui, 'refresh', return_value=True), \
             mock.patch.object(ui, '_sync_properties') as notify:
            self.assertTrue(ui.select_timeline_key(context, 'durable-red'))
            self.assertEqual(ui._selection.character_id, 'red-id')
            self.assertEqual(ui._review_key_selection.active_keyframe_id, 'durable-red')
            scene.frame_set.assert_not_called()
            notify.assert_called_once()
            self.assertTrue(ui.select_timeline_key(context, 'durable-black', extend=True))
            self.assertEqual(ui._review_key_selection.keyframe_ids,
                             ('durable-black', 'durable-red'))
            self.assertTrue(ui.select_timeline_key(context, 'durable-red', activate=True))
            self.assertEqual(ui._selection.character_id, 'red-id')
            scene.frame_set.assert_not_called()
            self.assertTrue(ui.go_to_selected_timeline_key(context))
            scene.frame_set.assert_called_once_with(133)
            self.assertFalse(ui.select_timeline_key(context, 'missing'))

    def test_top_row_window_event_selects_vta_key(self):
        view = data()
        region = SimpleNamespace(type='WINDOW', height=240,
                                 view2d=SimpleNamespace(
                                     view_to_region=lambda frame, _y, clip=False: (frame * 2, 0)))
        context = SimpleNamespace(region=region)
        event = SimpleNamespace(type='LEFTMOUSE', ctrl=False,
                                mouse_region_x=66,
                                mouse_region_y=character_lanes(view, 240)[0][1])
        with mock.patch.object(self.workspace, '_state', return_value=(
            view, (), None, 0, 0, None)), \
             mock.patch.object(self.ui, 'select_timeline_key', return_value=True) as select:
            self.assertEqual(self.workspace.BLENDCAP_OT_vta_timeline_select.invoke(
                object(), context, event), {'FINISHED'})
            select.assert_called_once_with(context, 'durable-red', extend=False,
                                           activate=False)

    def test_mode_header_has_depressed_active_state_and_stock_exit(self):
        area = SimpleNamespace(type='DOPESHEET_EDITOR', ui_type='DOPESHEET')
        space = SimpleNamespace(show_region_ui=False)
        context = SimpleNamespace(area=area, space_data=space,
                                  region=SimpleNamespace(type='HEADER'))
        layout = mock.Mock()
        with mock.patch.object(self.workspace, 'role_for', return_value=ROLE_TIMELINE), \
             mock.patch.object(self.workspace, '_state', return_value=(
                 data(), (), None, 0, 0, None)):
            self.workspace._draw_header(SimpleNamespace(layout=layout), context)
        self.assertTrue(layout.operator.call_args.kwargs['depress'])
        self.assertIn('Exit VTA Mode', layout.operator.call_args.kwargs['text'])
        self.assertTrue(layout.menu.called)
        layout.prop.assert_called_once_with(space, 'show_region_ui',
                                            text='VTA Sidebar', toggle=True)
        layout.reset_mock()
        with mock.patch.object(self.workspace, 'role_for', return_value=None):
            self.workspace._draw_header(SimpleNamespace(layout=layout), context)
        self.assertFalse(layout.operator.call_args.kwargs['depress'])
        self.assertFalse(layout.menu.called)
        layout.prop.assert_not_called()

    def test_timeline_menu_and_sidebar_share_management_actions(self):
        ui = self.ui
        layout = mock.Mock()
        with mock.patch.object(ui, 'refresh', return_value=True), \
             mock.patch.object(ui, '_draw_management_actions') as actions, \
             mock.patch.object(ui, '_action_availability', return_value={
                 'update-tracking': True}):
            self.workspace.BLENDCAP_MT_vta_timeline_commands.draw(
                SimpleNamespace(layout=layout), object())
        actions.assert_called_once_with(layout)
        self.assertIn(('blendcap.vta_update_tracking',),
                      [call.args for call in layout.row.return_value.operator.call_args_list])
        self.assertEqual(self.workspace.BLENDCAP_PT_vta_timeline.bl_region_type, 'UI')
        self.assertEqual(self.workspace._text_size(SimpleNamespace(
            scene=SimpleNamespace(blendcap_vta_timeline_text_size=18))), 18)

    def test_real_timeline_sidebar_draws_commands_and_text_size(self):
        ui = self.ui
        layout = mock.Mock()
        context = SimpleNamespace(scene=SimpleNamespace(),
                                  area=SimpleNamespace(type='DOPESHEET_EDITOR',
                                                       ui_type='DOPESHEET'))
        availability = {'update-tracking': True}
        with mock.patch.object(self.workspace, 'role_for', return_value=ROLE_TIMELINE), \
             mock.patch.object(ui, 'refresh', return_value=True), \
             mock.patch.object(ui, '_action_availability', return_value=availability), \
             mock.patch.object(ui, '_draw_management_actions') as actions:
            self.assertTrue(self.workspace.BLENDCAP_PT_vta_timeline.poll(context))
            self.workspace.BLENDCAP_PT_vta_timeline.draw(
                SimpleNamespace(layout=layout), context)
        layout.prop.assert_called_once_with(
            context.scene, 'blendcap_vta_timeline_text_size',
            text='Timeline Text Size')
        self.assertEqual(actions.call_count, 2)
        self.assertEqual(actions.call_args_list[0].kwargs['actions'], {
            'clear-review-key', 'clear-selected-review-keys',
            'clear-all-review-keys-on-frame'})
        self.assertEqual(actions.call_args_list[1].kwargs['actions'], {
            'clear-assignment', 'rename-character', 'delete-character'})
        ids = [call.args[0] for call in layout.row.return_value.operator.call_args_list]
        self.assertIn('blendcap.vta_goto_review_key', ids)
        self.assertIn('blendcap.vta_update_tracking', ids)
        self.assertTrue(any(call.args == ('BLENDCAP_MT_vta_timeline_commands',)
                            for call in layout.menu.call_args_list))

    def test_text_size_change_requests_redraw(self):
        self.workspace.register()
        try:
            with mock.patch.object(self.workspace.vta_invalidation,
                                   'request_redraw') as redraw:
                bpy.context.scene.blendcap_vta_timeline_text_size = 18
                redraw.assert_called_once_with()
            self.assertEqual(bpy.context.scene.blendcap_vta_timeline_text_size, 18)
        finally:
            self.workspace.unregister()

    def test_registration_is_reversible_without_proxy_animation_data(self):
        workspace = self.workspace
        pre_actions = len(bpy.data.actions)
        workspace.register()
        self.assertIsNotNone(workspace._window_handler)
        self.assertIsNotNone(workspace._channel_handler)
        workspace.unregister()
        self.assertIsNone(workspace._window_handler)
        self.assertIsNone(workspace._channel_handler)
        self.assertEqual(len(bpy.data.actions), pre_actions)

    def test_open_is_idempotent_and_stock_dopesheet_poll_is_false(self):
        pre_actions = len(bpy.data.actions)
        self.addon.register()
        try:
            self.assertEqual(bpy.ops.blendcap.open_vta_workspace(), {'FINISHED'})
            self.assertEqual(bpy.ops.blendcap.open_vta_workspace(), {'FINISHED'})
            tagged = [w for w in bpy.data.workspaces if w.get(WORKSPACE_TAG) == 1]
            self.assertEqual(len(tagged), 1)
            self.assertEqual(tagged[0].name, 'Video to Animation')
            normal_screen = bpy.data.workspaces['Animation'].screens[0]
            normal_area = next(a for a in normal_screen.areas
                               if a.type == 'DOPESHEET_EDITOR' and a.ui_type == 'DOPESHEET')
            stock_context = SimpleNamespace(
                workspace=bpy.data.workspaces['Animation'], screen=normal_screen,
                area=normal_area, region=SimpleNamespace(type='WINDOW'))
            self.assertFalse(self.workspace.BLENDCAP_OT_vta_timeline_select.poll(stock_context))
            review_screen = tagged[0].screens[0]
            review_context = SimpleNamespace(
                workspace=tagged[0], screen=review_screen,
                area=review_area(tagged[0], review_screen),
                region=SimpleNamespace(type='WINDOW'))
            self.assertTrue(self.workspace.BLENDCAP_OT_vta_timeline_select.poll(review_context))
            self.assertTrue(review_context.area.spaces.active.show_region_ui)
            self.assertTrue(self.workspace.BLENDCAP_PT_vta_timeline.poll(review_context))
            self.assertTrue(hasattr(bpy.types, 'BLENDCAP_PT_vta_timeline'))
            self.assertEqual(len(bpy.data.actions), pre_actions)
        finally:
            self.addon.unregister()

    def test_toggle_scopes_mode_to_one_editor_and_restores_stock_dopesheet(self):
        class Screen(dict):
            def __init__(self, areas):
                super().__init__()
                self.areas = areas

        first_space = SimpleNamespace(show_region_ui=False)
        first = SimpleNamespace(type='DOPESHEET_EDITOR', ui_type='DOPESHEET',
                                spaces=SimpleNamespace(active=first_space))
        second = SimpleNamespace(type='DOPESHEET_EDITOR', ui_type='DOPESHEET')
        screen = Screen((first, second))
        context = SimpleNamespace(workspace={}, screen=screen, area=first,
                                  space_data=SimpleNamespace())
        operator = SimpleNamespace(report=mock.Mock())
        toggle = self.workspace.BLENDCAP_OT_vta_toggle_editor_mode
        with mock.patch.object(self.ui, 'refresh', return_value=True) as refresh, \
             mock.patch.object(self.workspace.vta_invalidation,
                               'request_redraw') as redraw:
            self.assertEqual(toggle.execute(operator, context), {'FINISHED'})
            self.assertIs(review_area({}, screen), first)
            self.assertTrue(first_space.show_region_ui)
            self.assertIsNone(role_for({}, screen, second))
            refresh.assert_called_once_with(context, force=True)
            self.assertEqual(toggle.execute(operator, context), {'FINISHED'})
            self.assertIsNone(review_area({}, screen))
            self.assertEqual(redraw.call_count, 2)


if __name__ == '__main__':
    unittest.main()
