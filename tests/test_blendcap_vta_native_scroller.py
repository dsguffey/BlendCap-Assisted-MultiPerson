"""Temporary native channel filtering without Blender or owner-file writes."""

import importlib
from types import SimpleNamespace as NS

from tests.test_blendcap_vta_ui_followup import HostFixture


class NativeScrollerTests(HostFixture):
    def test_mocap_filter_is_scoped_restored_and_not_saved(self):
        self.bpy.data.workspaces = [self.workspace]
        filters = NS(filter_text='owner search', show_summary=True,use_filter_invert=True)
        self.timeline.spaces = NS(active=NS(dopesheet=filters))
        scroller = importlib.import_module(self.package + '.blendcap.vta_native_scroller')
        self.addCleanup(scroller.unregister)

        scroller.activate(self.workspace)
        self.assertEqual(filters.filter_text, scroller._FILTER)
        self.assertFalse(filters.show_summary)
        self.assertFalse(filters.use_filter_invert)
        scroller._save_pre()
        self.assertEqual((filters.filter_text, filters.show_summary, filters.use_filter_invert),
                         ('owner search', True, True))
        scroller._save_post()
        self.assertEqual(filters.filter_text, scroller._FILTER)
        scroller.deactivate(self.workspace)
        self.assertEqual((filters.filter_text, filters.show_summary, filters.use_filter_invert),
                         ('owner search', True, True))

        scroller.activate(self.workspace)
        scroller._save_pre()  # The saved .blend contains the owner's settings.
        scroller._load_post()  # Opening it reapplies the scoped MoCap filter.
        self.assertEqual(filters.filter_text, scroller._FILTER)
        scroller.deactivate(self.workspace)
        self.assertEqual((filters.filter_text, filters.show_summary, filters.use_filter_invert),
                         ('owner search', True, True))
