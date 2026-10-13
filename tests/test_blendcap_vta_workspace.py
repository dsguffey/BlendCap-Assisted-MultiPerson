"""Deterministic scope, projection, and redraw contracts for VTA workspace."""

import importlib.util
from pathlib import Path
from types import MethodType, SimpleNamespace
import sys
import unittest
from unittest import mock

from blendcap_vta_timeline import TimelineCharacter, TimelineDerived, TimelineKey, TimelineView
from blendcap_vta_workspace import (
    WORKSPACE_TAG, TimelineMetrics, character_lanes, hit_character, hit_review_key,
    grid_step, is_review_region, review_area, review_keys_in_box,
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

    def test_scaled_rows_and_hit_targets_share_display_geometry(self):
        view = data()
        view2d = SimpleNamespace(view_to_region=lambda frame, _y, clip=False: (frame * 2, 0))
        for scale in (.75, 1, 1.5, 2, 3):
            for text_size in (13, 18, 32):
                with self.subTest(scale=scale, text_size=text_size):
                    metrics = TimelineMetrics(scale, text_size)
                    height = 400 * scale
                    lanes = dict(character_lanes(view, height, metrics=metrics))
                    y = lanes['red-id']
                    self.assertAlmostEqual(y - lanes['black-id'], metrics.row_height)
                    self.assertGreater(metrics.row_height, metrics.font_size)
                    self.assertLess(y + metrics.hit_radius, height - 24 * scale)
                    self.assertEqual(hit_character(view, height, y, metrics=metrics), 'red-id')
                    self.assertEqual(hit_review_key(
                        view, view2d, height, (66 + 8 * scale, y), metrics=metrics), 'durable-red')
                    self.assertIsNone(hit_review_key(
                        view, view2d, height, (66 + 10 * scale, y), metrics=metrics))

    def test_box_selects_only_visible_durable_centers_in_both_directions(self):
        view = data()
        view2d = SimpleNamespace(view_to_region=lambda frame, _y, clip=False: (frame * 2, 0))
        for scale in (1, 2):
            metrics = TimelineMetrics(scale, 32)
            height = 400 * scale
            lanes = dict(character_lanes(view, height, metrics=metrics))
            start, end = (250, lanes['black-id'] - 5), (310, lanes['red-id'] + 5)
            expected = ('durable-black', 'durable-red')
            for a, b in ((start, end), (end, start)):
                self.assertEqual(review_keys_in_box(
                    view, view2d, 500, height, a, b, offset=100, metrics=metrics), expected)
            self.assertEqual(review_keys_in_box(
                view, view2d, 500, height, (267, lanes['red-id'] - 5),
                (269, lanes['red-id'] + 5), offset=100, metrics=metrics), ())
            self.assertEqual(review_keys_in_box(
                view, view2d, 240, height, start, end, offset=100, metrics=metrics), ())

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
    def test_source_camera_view_is_confined_to_vta_workspace_and_keeps_sidebar(self):
        camera = {'vta_project_id': 'p', 'vta_source_id': 's'}
        def workspace(tag):
            region = SimpleNamespace(view_perspective='PERSP', view_camera_offset=(1, 1), view_camera_zoom=5)
            space = SimpleNamespace(use_local_camera=False, camera=None, region_3d=region, show_region_ui=True,
                                    overlay=SimpleNamespace(show_overlays=False, show_bones=False,
                                                            show_extras=False, show_floor=False),
                                    show_object_viewport_armature=False)
            area = SimpleNamespace(type='VIEW_3D', spaces=SimpleNamespace(active=space))
            return SimpleNamespace(get=lambda key: tag, screens=[SimpleNamespace(areas=[area])]), space
        owned, view = workspace(1)
        ordinary, other = workspace(None)
        self.assertEqual(self.workspace.configure_source_camera_views(SimpleNamespace(camera=camera), [owned, ordinary]), 1)
        self.assertIs(view.camera, camera)
        self.assertTrue(view.use_local_camera)
        self.assertEqual(view.region_3d.view_perspective, 'CAMERA')
        self.assertTrue(view.show_region_ui)
        self.assertTrue(view.overlay.show_overlays)
        self.assertTrue(view.overlay.show_bones)
        self.assertTrue(view.show_object_viewport_armature)
        self.assertFalse(view.overlay.show_extras)
        self.assertFalse(view.overlay.show_floor)
        self.assertEqual(other.region_3d.view_perspective, 'PERSP')
        self.assertIsNone(other.camera)
        self.assertFalse(other.overlay.show_overlays)
        self.assertFalse(other.overlay.show_bones)
        self.assertFalse(other.show_object_viewport_armature)

    def test_missing_or_unowned_source_camera_leaves_views_unchanged(self):
        for camera in (None, {}, {'vta_project_id': 'p'}, {'vta_source_id': 's'}):
            with self.subTest(camera=camera):
                workspace = SimpleNamespace(get=mock.Mock(), screens=mock.Mock())
                self.assertEqual(self.workspace.configure_source_camera_views(
                    SimpleNamespace(camera=camera), [workspace]), 0)
                workspace.get.assert_not_called()

    def test_real_bundled_view_restores_overlay_display_without_changing_camera_or_animation(self):
        path = Path(__file__).resolve().parents[1] / 'vta_workspace_mode.blend'
        with bpy.data.libraries.load(str(path), link=False) as (_source, target):
            target.workspaces = ['Video to Animation']
        workspace = target.workspaces[0]
        spaces = [area.spaces.active for screen in workspace.screens for area in screen.areas
                  if area.type == 'VIEW_3D']
        self.assertTrue(spaces)
        camera_data = bpy.data.cameras.new('workspace-overlay-regression')
        camera = bpy.data.objects.new('workspace-overlay-regression', camera_data)
        camera['vta_project_id'], camera['vta_source_id'] = 'p', 's'
        image = bpy.data.images.new('workspace-overlay-background', width=32, height=18)
        background = camera_data.background_images.new()
        background.image = image
        camera_data.show_background_images = True
        before_matrix = camera.matrix_world.copy()
        before_counts = tuple(len(getattr(bpy.data, key)) for key in ('objects', 'actions', 'cameras', 'images'))
        sidebar = [space.show_region_ui for space in spaces]
        try:
            for space in spaces:
                space.overlay.show_overlays = False
                space.overlay.show_bones = False
                space.show_object_viewport_armature = False
            for _ in range(2):
                self.assertEqual(self.workspace.configure_source_camera_views(
                    SimpleNamespace(camera=camera), [workspace]), len(spaces))
                for index, space in enumerate(spaces):
                    self.assertTrue(space.overlay.show_overlays)
                    self.assertTrue(space.overlay.show_bones)
                    self.assertTrue(space.show_object_viewport_armature)
                    self.assertTrue(space.use_local_camera)
                    self.assertIs(space.camera, camera)
                    self.assertEqual(space.region_3d.view_perspective, 'CAMERA')
                    self.assertEqual(space.show_region_ui, sidebar[index])
                self.assertTrue(camera_data.show_background_images)
                self.assertTrue(background.show_background_image)
                self.assertIs(background.image, image)
                self.assertEqual(camera.matrix_world, before_matrix)
                self.assertEqual(tuple(len(getattr(bpy.data, key)) for key in
                                       ('objects', 'actions', 'cameras', 'images')), before_counts)
        finally:
            bpy.data.objects.remove(camera, do_unlink=True)
            bpy.data.cameras.remove(camera_data)
            bpy.data.images.remove(image)

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

    def _box_context(self):
        region = SimpleNamespace(type='WINDOW', height=240, width=400, x=0, y=0,
                                 view2d=SimpleNamespace(
                                     view_to_region=lambda frame, _y, clip=False: (frame * 2, 0)))
        return SimpleNamespace(region=region, area=object(),
                               window_manager=SimpleNamespace(modal_handler_add=mock.Mock()))

    def _box_event(self, kind, value='PRESS', point=(50, 120), *, shift=False):
        return SimpleNamespace(type=kind, value=value, mouse_region_x=point[0],
                               mouse_region_y=point[1], mouse_x=point[0], mouse_y=point[1],
                               ctrl=False, shift=shift)

    def test_drag_select_commits_group_on_release_and_supports_additive_selection(self):
        cls = self.workspace.BLENDCAP_OT_vta_timeline_select
        for extend in (False, True):
            context = self._box_context()
            operator = SimpleNamespace()
            operator._finish_box = MethodType(cls._finish_box, operator)
            view = data()
            with mock.patch.object(self.workspace, '_state', return_value=(
                view, (), None, 0, 0, None)), \
                 mock.patch.object(self.workspace, '_scope', return_value=True), \
                 mock.patch.object(self.ui, 'select_timeline_keys', return_value=True) as select, \
                 mock.patch.object(self.workspace.vta_invalidation, 'request_redraw'):
                try:
                    self.assertEqual(cls.invoke(operator, context, self._box_event(
                        'LEFTMOUSE', shift=extend)), {'RUNNING_MODAL'})
                    context.window_manager.modal_handler_add.assert_called_once_with(operator)
                    self.assertEqual(cls.modal(operator, context, self._box_event(
                        'MOUSEMOVE', point=(120, 180))), {'RUNNING_MODAL'})
                    self.assertTrue(self.workspace._box_gesture['dragged'])
                    select.assert_not_called()
                    self.assertEqual(cls.modal(operator, context, self._box_event(
                        'LEFTMOUSE', 'RELEASE', (120, 180))), {'FINISHED'})
                    select.assert_called_once_with(context, ('durable-black', 'durable-red'),
                                                   extend=extend)
                    self.assertIsNone(self.workspace._box_gesture)
                finally:
                    self.workspace._box_gesture = None

    def test_drag_cancel_stale_view_and_native_ruler_do_not_change_selection(self):
        cls = self.workspace.BLENDCAP_OT_vta_timeline_select
        context = self._box_context()
        view = data()
        with mock.patch.object(self.workspace, '_state', return_value=(
            view, (), None, 0, 0, None)) as state, \
             mock.patch.object(self.workspace, '_scope', return_value=True) as scope, \
             mock.patch.object(self.ui, 'select_timeline_keys') as select, \
             mock.patch.object(self.workspace.vta_invalidation, 'request_redraw'):
            self.assertEqual(cls.invoke(object(), context, self._box_event(
                'LEFTMOUSE', point=(50, 235))), {'PASS_THROUGH'})
            context.window_manager.modal_handler_add.assert_not_called()
            for case in ('ESC', 'RIGHTMOUSE', 'stale', 'disabled', 'click'):
                operator = SimpleNamespace()
                operator._finish_box = MethodType(cls._finish_box, operator)
                state.return_value = (view, (), None, 0, 0, None)
                scope.return_value = True
                try:
                    cls.invoke(operator, context, self._box_event('LEFTMOUSE'))
                    if case == 'stale':
                        state.return_value = (data(), (), None, 0, 0, None)
                    if case == 'disabled':
                        scope.return_value = False
                    event = (self._box_event('LEFTMOUSE', 'RELEASE', (51, 121)) if case == 'click'
                             else self._box_event(case if case in {'ESC', 'RIGHTMOUSE'} else 'MOUSEMOVE'))
                    result = cls.modal(operator, context, event)
                    self.assertEqual(result, {'FINISHED'} if case == 'click' else {'CANCELLED'})
                    self.assertIsNone(self.workspace._box_gesture)
                    select.assert_not_called()
                finally:
                    self.workspace._box_gesture = None

    def test_box_selection_syncs_sidebar_without_navigating_or_mutating_view(self):
        ui = self.ui
        view = data()
        context = SimpleNamespace(scene=SimpleNamespace(frame_set=mock.Mock()))
        before = repr(view)
        with mock.patch.object(ui, '_timeline', view), \
             mock.patch.object(ui, '_location', object()), \
             mock.patch.object(ui._review_key_selection, '_ids', set()), \
             mock.patch.object(ui._review_key_selection, '_active_id', None), \
             mock.patch.object(ui, '_sync_properties') as sync:
            self.assertTrue(ui.select_timeline_keys(context, ('durable-red', 'durable-black')))
            self.assertEqual(ui._review_key_selection.keyframe_ids,
                             ('durable-black', 'durable-red'))
            self.assertEqual(ui._selection.character_id, 'black-id')
            sync.assert_called_once_with(context)
            context.scene.frame_set.assert_not_called()
            self.assertEqual(repr(view), before)

    def test_mode_header_has_one_toggle_and_commands_even_when_off(self):
        area = SimpleNamespace(type='DOPESHEET_EDITOR', ui_type='DOPESHEET')
        space = SimpleNamespace(show_region_ui=False)
        context = SimpleNamespace(area=area, space_data=space,
                                  workspace=SimpleNamespace(blendcap_vta_mode_enabled=True),
                                  region=SimpleNamespace(type='HEADER'))
        layout = mock.Mock()
        with mock.patch.object(self.workspace, 'role_for', return_value=ROLE_TIMELINE), \
             mock.patch.object(self.workspace, '_state', return_value=(
                 data(), (), None, 0, 0, None)):
            self.workspace._draw_header(SimpleNamespace(layout=layout), context)
        self.assertTrue(layout.operator.call_args.kwargs['depress'])
        self.assertEqual(layout.operator.call_args.kwargs['text'], 'Exit VTA Mode')
        self.assertEqual(layout.operator.call_args.kwargs['icon'], 'KEY_HLT')
        self.assertEqual(layout.operator.call_count, 1)
        layout.menu.assert_called_once_with('BLENDCAP_MT_vta_timeline_commands',
                                            text='VTA Commands')
        layout.prop.assert_not_called()
        layout.label.assert_not_called()
        layout.reset_mock()
        context.workspace.blendcap_vta_mode_enabled = False
        with mock.patch.object(self.workspace, 'role_for', return_value=None):
            self.workspace._draw_header(SimpleNamespace(layout=layout), context)
        self.assertFalse(layout.operator.call_args.kwargs['depress'])
        self.assertEqual(layout.operator.call_args.kwargs['text'], 'Enter VTA Mode')
        self.assertEqual(layout.operator.call_count, 1)
        self.assertTrue(layout.menu.called)
        layout.prop.assert_not_called()
        layout.label.assert_not_called()

    def test_mode_toggle_follows_native_key_menu_and_unregisters_cleanly(self):
        menu = bpy.types.DOPESHEET_MT_editor_menus
        layout = mock.Mock()
        context = SimpleNamespace(
            area=SimpleNamespace(type='DOPESHEET_EDITOR', ui_type='DOPESHEET'),
            space_data=SimpleNamespace(mode='DOPESHEET', show_markers=True),
            active_action=None, workspace=SimpleNamespace(use_filter_by_owner=False))
        workspace = self.workspace
        for _ in range(2):
            workspace.register()
            try:
                layout.reset_mock()
                with mock.patch.object(workspace, 'role_for', return_value=ROLE_TIMELINE), \
                     mock.patch.object(workspace, '_state', return_value=(
                         data(), (), None, 0, 0, None)):
                    menu.draw(SimpleNamespace(layout=layout), context)
                calls = layout.method_calls
                key_index = calls.index(mock.call.menu('DOPESHEET_MT_key'))
                toggle_index = next(i for i, call in enumerate(calls)
                                    if call[0] == 'operator' and
                                    call.args[0] == 'blendcap.vta_toggle_editor_mode')
                self.assertLess(key_index, toggle_index)
                self.assertEqual(layout.operator.call_count, 1)
            finally:
                workspace.unregister()
            layout.reset_mock()
            menu.draw(SimpleNamespace(layout=layout), context)
            layout.operator.assert_not_called()

    def test_ui_scale_is_applied_once_and_background_zero_falls_back(self):
        context = SimpleNamespace(
            scene=SimpleNamespace(blendcap_vta_timeline_text_size=18),
            preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=2, pixel_size=2)))
        metrics = self.workspace._metrics(context)
        self.assertEqual(metrics.font_size, 36)
        context.preferences.system.ui_scale = 0
        self.assertEqual(self.workspace._metrics(context).font_size, 18)

    def test_scaled_window_event_uses_same_lanes_and_key_radius(self):
        view = data()
        context = SimpleNamespace(
            region=SimpleNamespace(type='WINDOW', height=800, view2d=SimpleNamespace(
                view_to_region=lambda frame, _y, clip=False: (frame * 2, 0))),
            scene=SimpleNamespace(blendcap_vta_timeline_text_size=32),
            preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=2)))
        y = character_lanes(view, 800, metrics=TimelineMetrics(2, 32))[0][1]
        event = SimpleNamespace(type='LEFTMOUSE', ctrl=False, mouse_region_x=82,
                                mouse_region_y=y)
        with mock.patch.object(self.workspace, '_state', return_value=(
            view, (), None, 0, 0, None)), \
             mock.patch.object(self.ui, 'select_timeline_key', return_value=True) as select:
            self.assertEqual(self.workspace.BLENDCAP_OT_vta_timeline_select.invoke(
                object(), context, event), {'FINISHED'})
            select.assert_called_once_with(context, 'durable-red', extend=False,
                                           activate=False)

    def test_timeline_menu_and_sidebar_share_management_actions(self):
        ui = self.ui
        layout = mock.Mock()
        with mock.patch.object(ui, 'refresh', return_value=True), \
             mock.patch.object(ui, '_draw_management_actions') as actions, \
             mock.patch.object(ui, 'draw_cleanup_commands') as cleanup, \
             mock.patch.object(ui, '_action_availability', return_value={
                 'update-tracking': True}):
            context = object()
            self.workspace.BLENDCAP_MT_vta_timeline_commands.draw(
                SimpleNamespace(layout=layout), context)
            actions.assert_called_once_with(layout, availability={'update-tracking': True})
            cleanup.assert_called_once_with(layout, context, menu=True)
        self.assertIn(('blendcap.vta_update_tracking',),
                      [call.args for call in layout.row.return_value.operator.call_args_list])
        self.assertEqual(self.workspace.BLENDCAP_PT_vta_timeline.bl_region_type, 'UI')
        self.assertEqual(self.workspace._text_size(SimpleNamespace(
            scene=SimpleNamespace(blendcap_vta_timeline_text_size=18))), 18)

    def test_real_timeline_sidebar_draws_commands_and_text_size(self):
        ui = self.ui
        layout = mock.Mock()
        context = SimpleNamespace(scene=SimpleNamespace(),
                                  window_manager=SimpleNamespace(blendcap_vta_motion_start=67,
                                                                 blendcap_vta_motion_end=90),
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
                bpy.context.scene.blendcap_vta_timeline_text_size = 32
                redraw.assert_called_once_with()
            self.assertEqual(bpy.context.scene.blendcap_vta_timeline_text_size, 32)
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

    def test_channel_binding_matches_blender_dispatched_keymap_and_stays_scoped(self):
        workspace = self.workspace
        workspace.register()
        try:
            bindings = {(keymap.name, keymap.space_type, keymap.region_type)
                        for keymap, _ in workspace._keymaps}
            self.assertEqual(bindings, {('Dopesheet', 'DOPESHEET_EDITOR', 'WINDOW'),
                                       ('Animation Channels', 'EMPTY', 'WINDOW')})
            native = bpy.context.window_manager.keyconfigs.default.keymaps['Animation Channels']
            self.assertIn((native.name, native.space_type, native.region_type), bindings)
            # The generic map is shared with other animation editors; the VTA
            # poll must reject stock/out-of-scope channels instead of swallowing clicks.
            self.assertFalse(workspace.BLENDCAP_OT_vta_timeline_select.poll(SimpleNamespace(
                workspace={}, screen=None, area=SimpleNamespace(type='GRAPH_EDITOR'),
                region=SimpleNamespace(type='CHANNELS'))))
        finally:
            workspace.unregister()

    def test_open_is_idempotent_and_stock_dopesheet_poll_is_false(self):
        pre_actions = len(bpy.data.actions)
        self.addon.register()
        try:
            original_scene = bpy.context.window.scene
            self.assertEqual(bpy.ops.blendcap.open_vta_workspace(), {'FINISHED'})
            self.assertEqual(bpy.ops.blendcap.open_vta_workspace(), {'FINISHED'})
            self.assertIs(bpy.context.window.scene, original_scene)
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
            # Opening a layout no longer enters mode or guesses a Scene.
            self.assertFalse(tagged[0].blendcap_vta_mode_enabled)
            tagged[0].blendcap_vta_mode_enabled = True  # Scope-only geometry poll below.
            review_context = SimpleNamespace(
                workspace=tagged[0], screen=review_screen,
                area=review_area(tagged[0], review_screen),
                region=SimpleNamespace(type='WINDOW'))
            self.assertTrue(self.workspace.BLENDCAP_OT_vta_timeline_select.poll(review_context))
            # Opening does not restore editor choices; explicit Load does setup.
            self.assertFalse(review_context.area.spaces.active.show_region_ui)
            self.assertTrue(self.workspace.BLENDCAP_PT_vta_timeline.poll(review_context))
            self.assertTrue(hasattr(bpy.types, 'BLENDCAP_PT_vta_timeline'))
            self.assertEqual(len(bpy.data.actions), pre_actions)
        finally:
            self.addon.unregister()

    def test_toggle_delegates_explicit_session_without_rewriting_roles(self):
        session = importlib.import_module(self.workspace.__package__+'.vta_scene_session')
        context=SimpleNamespace(workspace=SimpleNamespace(blendcap_vta_mode_enabled=False))
        operator=SimpleNamespace(report=mock.Mock())
        toggle=self.workspace.BLENDCAP_OT_vta_toggle_editor_mode
        with mock.patch.object(session,'enter') as enter, mock.patch.object(session,'exit_mode') as exit_mode:
            self.assertEqual(toggle.execute(operator,context),{'FINISHED'})
            enter.assert_called_once_with(context);exit_mode.assert_not_called()
            context.workspace.blendcap_vta_mode_enabled=True
            self.assertEqual(toggle.execute(operator,context),{'FINISHED'})
            exit_mode.assert_called_once_with(context)
