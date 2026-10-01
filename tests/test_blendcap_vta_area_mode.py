"""Per-editor VTA mode is opt-in, screen-persistent, and fail-closed."""

from types import SimpleNamespace
import unittest

from blendcap_vta_area_mode import (
    MODE_KEY, ROLE_PREVIEW, ROLE_TIMELINE, WORKSPACE_TAG,
    initialize_workspace_preset, role_for, set_role,
)


class Screen(dict):
    def __init__(self, *areas):
        super().__init__()
        self.areas = list(areas)


def area(kind, subtype):
    return SimpleNamespace(type=kind, ui_type=subtype)


class AreaModeTests(unittest.TestCase):
    def setUp(self):
        self.timeline = area('DOPESHEET_EDITOR', 'DOPESHEET')
        self.stock = area('DOPESHEET_EDITOR', 'DOPESHEET')
        self.preview = area('SEQUENCE_EDITOR', 'SEQUENCE_EDITOR')
        self.screen = Screen(self.timeline, self.preview, self.stock)
        self.workspace = {WORKSPACE_TAG: None}

    def test_custom_workspace_independent_per_area_enter_exit(self):
        self.assertIsNone(role_for(self.workspace, self.screen, self.timeline))
        set_role(self.workspace, self.screen, self.timeline, ROLE_TIMELINE)
        set_role(self.workspace, self.screen, self.preview, ROLE_PREVIEW)
        self.assertEqual(role_for(self.workspace, self.screen, self.timeline), ROLE_TIMELINE)
        self.assertEqual(role_for(self.workspace, self.screen, self.preview), ROLE_PREVIEW)
        self.assertIsNone(role_for(self.workspace, self.screen, self.stock))
        set_role(self.workspace, self.screen, self.timeline, None)
        self.assertIsNone(role_for(self.workspace, self.screen, self.timeline))
        self.assertEqual(role_for(self.workspace, self.screen, self.preview), ROLE_PREVIEW)

    def test_saved_screen_map_round_trip_and_layout_change_fail_closed(self):
        set_role(self.workspace, self.screen, self.preview, ROLE_PREVIEW)
        saved = self.screen[MODE_KEY]
        restored = Screen(area('DOPESHEET_EDITOR', 'DOPESHEET'),
                          area('SEQUENCE_EDITOR', 'SEQUENCE_EDITOR'),
                          area('DOPESHEET_EDITOR', 'DOPESHEET'))
        restored[MODE_KEY] = saved
        self.assertEqual(role_for(self.workspace, restored, restored.areas[1]), ROLE_PREVIEW)
        restored.areas.insert(0, area('VIEW_3D', 'VIEW_3D'))
        self.assertIsNone(role_for(self.workspace, restored, restored.areas[2]))

    def test_preview_role_survives_preview_only_and_combined_view_switch(self):
        self.preview.spaces = SimpleNamespace(active=SimpleNamespace(view_type='PREVIEW'))
        set_role(self.workspace, self.screen, self.preview, ROLE_PREVIEW)
        self.assertEqual(role_for(self.workspace, self.screen, self.preview), ROLE_PREVIEW)
        self.preview.spaces.active.view_type = 'SEQUENCER_PREVIEW'
        self.assertEqual(role_for(self.workspace, self.screen, self.preview), ROLE_PREVIEW)

    def test_duplicate_timeline_rejected_and_invalid_role_rejected(self):
        set_role(self.workspace, self.screen, self.timeline, ROLE_TIMELINE)
        with self.assertRaisesRegex(ValueError, 'already has'):
            set_role(self.workspace, self.screen, self.stock, ROLE_TIMELINE)
        with self.assertRaisesRegex(ValueError, 'does not support'):
            set_role(self.workspace, self.screen, self.preview, ROLE_TIMELINE)

    def test_old_tagged_workspace_migrates_but_explicit_exit_persists(self):
        single = Screen(self.timeline, self.preview)
        tagged = {WORKSPACE_TAG: 1}
        self.assertEqual(role_for(tagged, single, self.timeline), ROLE_TIMELINE)
        self.assertEqual(role_for(tagged, single, self.preview), ROLE_PREVIEW)
        initialize_workspace_preset(tagged, single)
        self.assertIn(MODE_KEY, single)
        set_role(tagged, single, self.timeline, None)
        self.assertIsNone(role_for(tagged, single, self.timeline))
        self.assertEqual(role_for(tagged, single, self.preview), ROLE_PREVIEW)

    def test_corrupt_map_does_not_reenable_tagged_preset(self):
        screen = Screen(self.timeline)
        screen[MODE_KEY] = '{invalid'
        self.assertIsNone(role_for({WORKSPACE_TAG: 1}, screen, self.timeline))


if __name__ == '__main__':
    unittest.main()
