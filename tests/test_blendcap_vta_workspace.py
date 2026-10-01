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
    is_review_region, review_area,
)
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
        screen = SimpleNamespace(areas=(area, other))
        workspace = {WORKSPACE_TAG: 1}
        workspace = SimpleNamespace(get=workspace.get, screens=(screen,))
        window = SimpleNamespace(type='WINDOW')
        channels = SimpleNamespace(type='CHANNELS')
        self.assertIs(review_area(workspace, screen), area)
        self.assertTrue(is_review_region(workspace, screen, area, window))
        self.assertTrue(is_review_region(workspace, screen, area, channels))
        self.assertFalse(is_review_region(workspace, screen, other, window))
        self.assertIsNone(review_area(SimpleNamespace(get=lambda *_: None,
                                                 screens=(screen,)), screen))
        self.assertIsNone(review_area(workspace, SimpleNamespace(areas=(area,))))
        screen.areas = (area, SimpleNamespace(type='DOPESHEET_EDITOR', ui_type='DOPESHEET'))
        self.assertIsNone(review_area(workspace, screen))

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
        path = Path(__file__).resolve().parents[1] / 'vta_workspace.blend'
        before_scenes = set(bpy.data.scenes.keys())
        before_clips = set(bpy.data.movieclips.keys())
        with bpy.data.libraries.load(str(path), link=False) as (source, target):
            self.assertIn('Video to Animation', source.workspaces)
            target.workspaces = ['Video to Animation']
        workspace = target.workspaces[0]
        self.assertEqual(workspace.get(WORKSPACE_TAG), 1)
        self.assertEqual(len(workspace.screens), 1)
        screen = workspace.screens[0]
        self.assertIsNotNone(review_area(workspace, screen))
        kinds = {area.type for area in screen.areas}
        self.assertTrue({'VIEW_3D', 'SEQUENCE_EDITOR', 'DOPESHEET_EDITOR'} <= kinds)
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

    def test_key_click_navigates_scene_and_syncs_stable_character(self):
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
            scene.frame_set.assert_called_once_with(133)
            notify.assert_called_once()
            self.assertTrue(ui.select_timeline_key(context, 'durable-black', extend=True))
            self.assertEqual(ui._review_key_selection.keyframe_ids,
                             ('durable-black', 'durable-red'))
            self.assertFalse(ui.select_timeline_key(context, 'missing'))

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
            self.assertEqual(len(bpy.data.actions), pre_actions)
        finally:
            self.addon.unregister()


if __name__ == '__main__':
    unittest.main()
