"""Cached control clients must not retain a previous Scene's mutation context."""
import importlib
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock
import unittest
try:
    import bpy
except ImportError:
    bpy=None


@unittest.skipUnless(bpy is not None,'Blender required')
class CharacterCreationContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_ui_polish import UiPolishTests
        UiPolishTests.setUpClass()
        cls.ui=importlib.import_module(UiPolishTests.addon.__name__+'.blendcap.vta_review_ui')

    def test_cached_backend_uses_each_callers_current_scene(self):
        ui=self.ui;first=NS(scene=NS(name='First'));second=NS(scene=NS(name='Second'))
        client=NS(endpoint=NS(launcher=Path('engine/vta_engine.py')))
        with mock.patch.object(ui,'_backend',None),mock.patch.object(ui,'_backend_location',None), \
             mock.patch.object(ui,'_backend_root',None),mock.patch.object(ui,'_configured_core_location',return_value='same engine'), \
             mock.patch.object(ui,'_engine_client',return_value=client) as connect, \
             mock.patch.object(ui,'_context_for_main_scene',side_effect=lambda c:c), \
             mock.patch.object(ui,'_domain_mutation',return_value={}) as submit:
            backend=ui._get_backend(first)
            for context in (second,first,second):
                self.assertIs(ui._get_backend(context),backend)
                backend.mutate('character-create',{'display_name':'New'},NS())
                self.assertIs(submit.call_args.args[0],context)
            connect.assert_called_once()
