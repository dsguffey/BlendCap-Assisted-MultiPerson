"""Session-only repeat action: real operator methods and normal Assign guards."""
from contextlib import ExitStack
from dataclasses import replace
import ast
import hashlib
import importlib.util
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace as NS, MethodType
import unittest
from unittest import mock

from blendcap_vta_review import CandidateRow, CharacterRow, ReviewView, ReviewLocation, SelectionState

try:
    import bpy
except ImportError:
    bpy = None

ROOT = Path(__file__).resolve().parents[1]


def view(frame=0):
    return ReviewView('rev', 'source', frame, 1920, 1080,
        'analyzed-with-candidates', 'Candidates Available',
        (CandidateRow('rev', f'obs-{frame}', (1, 2, 3, 4), 'Candidate 1', None, None, 1),),
        (CharacterRow('white', 'White', None), CharacterRow('yellow', 'Yellow', None)))


class RepeatSourceTests(unittest.TestCase):
    def test_registration_uses_real_central_class_list_and_no_saved_property(self):
        code = (ROOT / 'blendcap/vta_review_ui.py').read_text()
        tree = ast.parse(code)
        registration = ast.parse((ROOT / 'blendcap/registration.py').read_text())
        imports = [n for n in registration.body if isinstance(n, ast.ImportFrom) and n.module == 'vta_review_ui']
        self.assertIn('BLENDCAP_OT_vta_repeat_assignment', {a.name for n in imports for a in n.names})
        classes = next(n for n in registration.body if isinstance(n, ast.Assign) and
                       any(isinstance(t, ast.Name) and t.id == 'classes' for t in n.targets))
        self.assertIn('BLENDCAP_OT_vta_repeat_assignment', {n.id for n in ast.walk(classes) if isinstance(n, ast.Name)})
        remembered_writes = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and
                            isinstance(n.func, ast.Name) and n.func.id == '_remember_assignment']
        self.assertEqual(len(remembered_writes), 1)
        normal = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BLENDCAP_OT_vta_review_assign')
        self.assertIn(remembered_writes[0], list(ast.walk(normal)))
        self.assertNotIn('blendcap_vta_last_assignment', code)
        self.assertNotIn('BoolProperty', ast.get_source_segment(code, next(n for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == 'BLENDCAP_OT_vta_repeat_assignment')))


@unittest.skipUnless(bpy is not None, 'requires Blender background runtime')
class RepeatOperatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package = 'blendcap_repeat_test'
        spec = importlib.util.spec_from_file_location(package, ROOT / '__init__.py',
                                                      submodule_search_locations=[str(ROOT)])
        addon = importlib.util.module_from_spec(spec)
        sys.modules[package] = addon
        spec.loader.exec_module(addon)
        cls.ui = sys.modules[package + '.blendcap.vta_review_ui']

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        ui = self.ui
        self.context = NS(area=NS(type='SEQUENCE_EDITOR'), space_data=NS(view_type='PREVIEW'),
            screen=None, workspace=None,
            scene=NS(frame_current=1, sequence_editor=object()),
            window_manager=NS(invoke_props_dialog=mock.Mock(return_value={'RUNNING_MODAL'})))
        self.context.scene.frame_set = mock.Mock(side_effect=lambda f: setattr(self.context.scene, 'frame_current', f))
        self.backend = NS(apply=mock.Mock(side_effect=self.apply),
            prepare_reassignment=mock.Mock(side_effect=lambda loc, **kw: (NS(**kw), ui._view)),
            apply_confirmed_reassignment=mock.Mock(side_effect=lambda loc, req: self.apply(loc,
                observation_id=req.observation_id, character_id=req.character_id)),
            source_coverage=mock.Mock(return_value=(0, 9)))
        self.calls = []
        for name, value in (('_selection', SelectionState()), ('_view', view()),
            ('_location', ReviewLocation(Path('project_vta'), Path('source.mp4'), 0, 1)),
            ('_last_assignment_target', None), ('_status', ''), ('_status_is_error', False)):
            self.stack.enter_context(mock.patch.object(ui, name, value))
        self.stack.enter_context(mock.patch.object(ui, '_analysis_locked', return_value=False))
        self.stack.enter_context(mock.patch.object(ui, '_get_backend', return_value=self.backend))
        self.stack.enter_context(mock.patch.object(ui, 'role_for', return_value='preview'))
        self.stack.enter_context(mock.patch.object(ui, '_active_video_strip', return_value=object()))
        self.stack.enter_context(mock.patch.object(ui, '_strip_visible_end', return_value=11))
        self.stack.enter_context(mock.patch.object(ui, '_sync_properties'))
        self.stack.enter_context(mock.patch.object(ui, '_rebuild_enum_items'))
        self.stack.enter_context(mock.patch.object(ui.vta_invalidation, 'request_redraw'))
        self.stack.enter_context(mock.patch.object(ui, 'refresh', side_effect=self.refresh))
        self.select()

    def refresh(self, context, **kw):
        ui = self.ui
        frame = context.scene.frame_current - 1
        if ui._view.source_frame != frame:
            ui._view = view(frame)
        ui._location = replace(ui._location, source_frame=frame, blender_frame=frame+1)
        ui._selection.accept_review(ui._view)
        ui._repeat_character_id()
        return True

    def apply(self, location, *, observation_id, character_id):
        self.calls.append((location.source_frame, observation_id, character_id))
        self.ui._view = replace(self.ui._view,
            candidates=tuple(replace(c, assigned_character_id=character_id, assigned_character_name=character_id)
                if c.observation_id == observation_id else c for c in self.ui._view.candidates),
            characters=tuple(replace(c, occupied_observation_id=observation_id, decision_state='visible')
                if c.character_id == character_id else c for c in self.ui._view.characters))
        return object(), self.ui._view

    def select(self, character='white'):
        self.ui._selection.accept_review(self.ui._view)
        self.ui._selection.character_id = character
        self.ui._selection.observation_id = f'obs-{self.context.scene.frame_current - 1}'

    def operator(self, repeat=False):
        cls = self.ui.BLENDCAP_OT_vta_repeat_assignment if repeat else self.ui.BLENDCAP_OT_vta_review_assign
        op = NS(report=mock.Mock())
        for name in ('invoke', 'execute', 'draw') + (('_prepare',) if repeat else ()):
            setattr(op, name, MethodType(getattr(cls, name), op))
        return op

    def explicit(self, character='white'):
        self.select(character)
        return self.operator().invoke(self.context, None)

    def next(self):
        self.context.scene.frame_current += 1
        self.refresh(self.context)
        self.select()

    def test_no_history_is_disabled_and_direct_execute_fails_without_advance(self):
        self.assertFalse(self.ui.BLENDCAP_OT_vta_repeat_assignment.poll(self.context))
        self.assertEqual(self.operator(True).execute(self.context), {'CANCELLED'})
        self.assertEqual(self.calls, [])
        self.context.scene.frame_set.assert_not_called()

    def test_explicit_assign_remembers_only_character_and_does_not_advance(self):
        self.assertEqual(self.explicit(), {'FINISHED'})
        self.assertEqual(self.ui._repeat_character_id(), 'white')
        self.assertEqual(self.ui._last_assignment_target, (Path('project_vta'), Path('source.mp4'), 'source', 'white'))
        self.context.scene.frame_set.assert_not_called()

    def test_repeat_uses_current_candidate_and_advances_exactly_one(self):
        self.explicit()
        self.next()
        self.select('yellow')  # current Character selection must not replace remembered White
        self.assertTrue(self.ui.BLENDCAP_OT_vta_repeat_assignment.poll(self.context))
        self.assertEqual(self.operator(True).invoke(self.context, None), {'FINISHED'})
        self.assertEqual(self.calls, [(0, 'obs-0', 'white'), (1, 'obs-1', 'white')])
        self.context.scene.frame_set.assert_called_once_with(3)
        self.assertEqual(self.ui._selection.observation_id, '')
        self.assertEqual(self.ui._repeat_character_id(), 'white')

    def test_multiple_repeats_keep_target_and_require_new_selection(self):
        self.explicit(); self.next()
        for frame in (2, 3, 4):
            self.select('yellow')
            self.assertEqual(self.operator(True).invoke(self.context, None), {'FINISHED'})
            self.assertEqual(self.context.scene.frame_current, frame+1)
            self.assertFalse(self.ui.BLENDCAP_OT_vta_repeat_assignment.poll(self.context))
        self.assertEqual([c[2] for c in self.calls], ['white']*4)

    def test_new_explicit_assignment_changes_target(self):
        self.explicit(); self.next()
        self.assertEqual(self.explicit('yellow'), {'FINISHED'})
        self.next()
        self.operator(True).invoke(self.context, None)
        self.assertEqual(self.calls[-1], (2, 'obs-2', 'yellow'))

    def test_rename_retains_stable_id_delete_clears_no_substitution(self):
        self.explicit(); self.next()
        self.ui._view = replace(self.ui._view, characters=(CharacterRow('white', 'Renamed', None),
                                                          CharacterRow('yellow', 'Yellow', None)))
        self.assertEqual(self.ui._repeat_character_id(), 'white')
        self.ui._view = replace(self.ui._view, characters=(CharacterRow('yellow', 'Yellow', None),))
        self.assertIsNone(self.ui._repeat_character_id())
        self.assertIsNone(self.ui._last_assignment_target)
        self.assertFalse(self.ui.BLENDCAP_OT_vta_repeat_assignment.poll(self.context))

    def test_project_or_source_switch_invalidates_memory(self):
        self.explicit(); self.next()
        self.ui._location = replace(self.ui._location, companion_root=Path('different_vta'))
        self.assertIsNone(self.ui._repeat_character_id())

    def test_failure_preserves_target_and_frame(self):
        self.explicit(); self.next()
        remembered = self.ui._last_assignment_target
        self.backend.apply.side_effect = RuntimeError('persistence-failed')
        self.assertEqual(self.operator(True).invoke(self.context, None), {'CANCELLED'})
        self.assertEqual(self.ui._last_assignment_target, remembered)
        self.context.scene.frame_set.assert_not_called()

    def test_suppressed_missing_and_no_selection_are_blocked(self):
        self.explicit(); self.next()
        for selected, suppressed in (('', False), ('missing', False), ('obs-1', True)):
            self.ui._selection.observation_id = selected
            self.ui._view = replace(self.ui._view, candidates=(replace(self.ui._view.candidates[0], suppressed=suppressed),))
            self.assertFalse(self.ui.BLENDCAP_OT_vta_repeat_assignment.poll(self.context))
            self.assertEqual(self.operator(True).execute(self.context), {'CANCELLED'})
        self.assertEqual(len(self.calls), 1)
        self.context.scene.frame_set.assert_not_called()

    def test_owned_candidate_uses_normal_confirmation_and_cancel_does_not_advance(self):
        self.explicit(); self.next()
        self.ui._view = replace(self.ui._view, candidates=(replace(self.ui._view.candidates[0],
            assigned_character_id='yellow', assigned_character_name='Yellow'),))
        op = self.operator(True)
        self.assertEqual(op.invoke(self.context, None), {'RUNNING_MODAL'})
        self.assertEqual(len(self.calls), 1)
        self.context.scene.frame_set.assert_not_called()
        self.assertEqual(self.context.window_manager.invoke_props_dialog.call_args.kwargs['confirm_text'], 'Reassign')
        self.assertEqual(op.execute(self.context), {'FINISHED'})
        self.backend.apply_confirmed_reassignment.assert_called_once()
        self.context.scene.frame_set.assert_called_once_with(3)

    def test_hard_states_require_confirmation_not_history_change(self):
        self.explicit(); self.next()
        remembered = self.ui._last_assignment_target
        for state in ('occluded', 'not-visible', 'no-valid-candidate'):
            self.ui._view = replace(view(1), characters=(CharacterRow('white', 'White', None, state),
                                                         CharacterRow('yellow', 'Yellow', None)))
            op = self.operator(True)
            self.assertEqual(op.invoke(self.context, None), {'RUNNING_MODAL'})
            self.assertEqual(self.ui._last_assignment_target, remembered)
            self.assertTrue(any(state.replace('-', ' ').title() in s for s in op._confirmation_lines))
        self.assertEqual(len(self.calls), 1)
        self.context.scene.frame_set.assert_not_called()

    def test_direct_execute_cannot_bypass_conflict_confirmation(self):
        self.explicit(); self.next()
        self.ui._view = replace(view(1), characters=(CharacterRow('white', 'White', 'old-observation'),))
        # Existing occupancy with a visible old candidate is what triggers normal confirmation.
        old = replace(view(1).candidates[0], observation_id='old-observation',
                      assigned_character_id='white', assigned_character_name='White', position=2)
        self.ui._view = replace(self.ui._view, candidates=(*self.ui._view.candidates, old))
        self.assertEqual(self.operator(True).execute(self.context), {'CANCELLED'})
        self.assertEqual(len(self.calls), 1)
        self.context.scene.frame_set.assert_not_called()

    def test_changed_confirmation_selection_or_frame_rejects_without_advance(self):
        self.explicit(); self.next()
        self.ui._view = replace(view(1), characters=(CharacterRow('white', 'White', None, 'occluded'),))
        op = self.operator(True)
        self.assertEqual(op.invoke(self.context, None), {'RUNNING_MODAL'})
        self.ui._selection.observation_id = 'missing'
        self.assertEqual(op.execute(self.context), {'CANCELLED'})
        self.select()
        op = self.operator(True)
        self.assertEqual(op.invoke(self.context, None), {'RUNNING_MODAL'})
        self.context.scene.frame_current = 3
        self.assertEqual(op.execute(self.context), {'CANCELLED'})
        self.assertEqual(len(self.calls), 1)
        self.context.scene.frame_set.assert_not_called()

    def test_final_frame_success_stays_in_source_and_trimmed_strip_range(self):
        self.explicit(); self.next()
        self.backend.source_coverage.return_value = (0, 1)
        self.assertEqual(self.operator(True).invoke(self.context, None), {'FINISHED'})
        self.assertEqual(self.calls[-1], (1, 'obs-1', 'white'))
        self.context.scene.frame_set.assert_not_called()
        # A trimmed VSE strip can end before the complete source range.
        self.next()
        self.backend.source_coverage.return_value = (0, 9)
        with mock.patch.object(self.ui, '_strip_visible_end', return_value=4):
            self.assertEqual(self.operator(True).invoke(self.context, None), {'FINISHED'})
        self.assertEqual(self.calls[-1], (2, 'obs-2', 'white'))
        self.context.scene.frame_set.assert_not_called()

    def test_lock_unanalyzed_and_non_vta_context_disable(self):
        self.explicit(); self.next()
        with mock.patch.object(self.ui, '_analysis_locked', return_value=True):
            self.assertFalse(self.ui.BLENDCAP_OT_vta_repeat_assignment.poll(self.context))
            self.assertEqual(self.operator(True).invoke(self.context, None), {'CANCELLED'})
        self.ui._view = replace(view(1), state='not-analyzed', candidates=())
        self.assertFalse(self.ui.BLENDCAP_OT_vta_repeat_assignment.poll(self.context))
        self.context.area.type = 'VIEW_3D'
        self.assertFalse(self.ui.BLENDCAP_OT_vta_repeat_assignment.poll(self.context))

    def test_derived_hard_state_hold_or_suppression_refresh_never_establishes_history(self):
        for kwargs in ({'derived_state': 'visible', 'derived_observation_id': 'obs-0'},
            {'decision_state': 'occluded'}, {'decision_state': 'not-visible'},
            {'decision_state': 'no-valid-candidate'}, {'pose_rejected': True}):
            self.ui._view = replace(view(), characters=(CharacterRow('white', 'White', None, **kwargs),))
            self.refresh(self.context)
            self.assertIsNone(self.ui._last_assignment_target)
        self.ui._view = replace(view(), candidates=(replace(view().candidates[0], suppressed=True),))
        self.refresh(self.context)
        self.assertIsNone(self.ui._last_assignment_target)

    def test_reload_handler_clears_memory_without_touching_authority(self):
        self.explicit()
        authority = self.ui._view
        self.ui._reset_repeat_assignment_session('reloaded.blend')
        self.assertIsNone(self.ui._last_assignment_target)
        self.assertEqual(self.ui._view, authority)
        self.assertFalse(self.ui.BLENDCAP_OT_vta_repeat_assignment.poll(self.context))

    def test_preview_and_combined_menus_show_disabled_then_enabled_action(self):
        class Layout:
            def __init__(self, items): self.items, self.enabled = items, True
            def row(self): return Layout(self.items)
            def operator(self, name, **kwargs):
                self.items.append((name, self.enabled, getattr(self, 'operator_context', None)))
                return NS()
            def menu(self, name, **kwargs): self.items.append((name, self.enabled))
            def separator(self): pass
            def label(self, **kwargs): pass
        for mode in ('PREVIEW', 'SEQUENCER_PREVIEW'):
            self.context.space_data.view_type = mode
            for remembered, enabled in ((None, False),
                ((Path('project_vta'), Path('source.mp4'), 'source', 'white'), True)):
                self.ui._last_assignment_target = remembered
                items = []
                self.ui.BLENDCAP_MT_vta_commands.draw(NS(layout=Layout(items)), self.context)
                self.assertIn(('blendcap.vta_repeat_assignment', enabled, 'INVOKE_DEFAULT'), items)
            items = []
            self.ui._draw_vta_header(NS(layout=Layout(items)), self.context)
            self.assertIn(('BLENDCAP_MT_vta_commands', True), items)

    def test_shortcut_idempotent_registration_and_exact_removal(self):
        item = object()
        km = NS(keymap_items=NS(new=mock.Mock(return_value=item), remove=mock.Mock()))
        config = NS(keymaps=NS(new=mock.Mock(return_value=km)))
        fake = NS(context=NS(window_manager=NS(keyconfigs=NS(addon=config))))
        with mock.patch.object(self.ui, 'bpy', fake), mock.patch.object(self.ui, '_repeat_assignment_keymap', None):
            self.ui._register_repeat_assignment_keymap(); self.ui._register_repeat_assignment_keymap()
            config.keymaps.new.assert_called_once_with(name='Video Sequence Editor', space_type='SEQUENCE_EDITOR', region_type='WINDOW')
            km.keymap_items.new.assert_called_once_with('blendcap.vta_repeat_assignment', 'J', 'PRESS', repeat=False)
            self.ui._unregister_repeat_assignment_keymap(); self.ui._unregister_repeat_assignment_keymap()
            km.keymap_items.remove.assert_called_once_with(item)

    def test_plain_j_has_no_default_preview_sequencer_or_shared_map_conflict(self):
        data = runpy.run_path(str(Path(bpy.utils.system_resource('SCRIPTS')) /
            'presets/keyconfig/keymap_data/blender_default.py'))
        relevant = {'Window', 'Screen', 'Screen Editing', 'Frames', 'Animation',
                    'Markers', 'View2D', 'View2D Buttons List', 'User Interface',
                    'Region Context Menu', 'Video Sequence Editor', 'Preview'}
        conflicts = [(name, operator, event)
            for name, _params, contents in data['generate_keymaps'](data['Params']())
            if name in relevant or name.startswith('Sequencer')
            for operator, event, _properties in contents['items']
            if event.get('type') == 'J' and event.get('value') in {'PRESS', 'ANY'}
            and (event.get('any') or not any(event.get(key)
                 for key in ('ctrl', 'alt', 'shift', 'oskey')))]
        self.assertEqual(conflicts, [])

    def real_backend_fixture(self):
        from blendcap_vta_runtime import discover_vta_core
        resolution = discover_vta_core()
        if not resolution.available:
            self.skipTest('requires separately installed private VTA core')
        core = resolution.core
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        root = Path(temporary) / 'scene_vta'
        source_path = Path(temporary) / 'source.mp4'
        source_path.write_bytes(b'synthetic-source; no video inference')
        source = core.SourceReference('source', 25.0, 0, 2,
            hashlib.sha256(source_path.read_bytes()).hexdigest(), 640, 480)
        project = core.ProjectState('repeat-real-core-test')
        project.add_source(source)
        for name in ('white', 'yellow', 'other'):
            project.create_character(name.title(), character_id=name)
        provider = core.ProviderDescriptor('test', 'synthetic', 'config')
        cache = core.AnalysisCache('rev', source, provider)
        observations = []
        for frame in range(3):
            candidates = tuple(core.ProviderCandidate(f'{prefix}-{frame}', (x, 20, x+40, 100), .9)
                for prefix, x in (('obs', 10), ('other', 100)))
            cache.record_frame(core.ProviderFrameResult(frame, frame/25.0, candidates))
            observations.extend(core.CandidateObservation(c.observation_id, 'source', frame,
                frame/25.0, c.box_xyxy) for c in candidates)
        project.add_analysis_revision(core.AnalysisRevision('rev', 'source', 'test',
            'synthetic', 'config', tuple(observations)))
        project.add_tracking_revision(core.TrackingRevision('yellow-tracking', 'rev',
            tuple(core.DerivedTrackingRecord('yellow', 'source', frame, core.PresenceState.VISIBLE,
                project.candidate_evidence('rev', f'obs-{frame}')) for frame in (1, 2))))
        project.add_tracking_revision(core.TrackingRevision('other-tracking', 'rev',
            (core.DerivedTrackingRecord('other', 'source', 1, core.PresenceState.VISIBLE,
                project.candidate_evidence('rev', 'other-1')),)))
        core.save_project_state(project, root)
        core.save_analysis_cache(cache, root)
        backend = self.ui.PrivateCoreBackend(core=core)
        self.stack.enter_context(mock.patch.object(self.ui, '_get_backend', return_value=backend))
        def refresh(context, **kw):
            self.ui._location = ReviewLocation(root, source_path,
                context.scene.frame_current-1, context.scene.frame_current)
            self.ui._view = backend.query(self.ui._location)
            self.ui._selection.accept_review(self.ui._view)
            self.ui._repeat_character_id()
            return True
        self.stack.enter_context(mock.patch.object(self.ui, 'refresh', side_effect=refresh))
        refresh(self.context)
        return core, root, project, cache, refresh

    def test_normal_assign_real_core_persists_and_refreshes_after_derived_conflict(self):
        core, root, before, cache, refresh = self.real_backend_fixture()
        self.context.scene.frame_current = 2
        refresh(self.context)
        self.select('white')
        self.assertEqual(self.operator().invoke(self.context, None), {'FINISHED'})
        self.assertEqual(self.context.scene.frame_current, 2)
        self.assertEqual(self.ui._repeat_character_id(), 'white')
        loaded = core.load_project_state(root)
        self.assertIsNone(loaded.effective_record('yellow', 'source', 1))
        self.assertEqual(loaded.effective_record('yellow', 'source', 2),
                         before.effective_record('yellow', 'source', 2))
        self.assertEqual(loaded.effective_record('other', 'source', 1),
                         before.effective_record('other', 'source', 1))
        self.assertEqual(core.load_analysis_cache(root, 'rev').to_json(), cache.to_json())
        selected = next(c for c in self.ui._view.candidates if c.observation_id == 'obs-1')
        self.assertEqual(selected.assigned_character_id, 'white')
        self.assertIsNone(selected.tracked_character_id)

    def test_repeat_real_core_inherits_retirement_and_advances_after_durable_success(self):
        core, root, before, _cache, refresh = self.real_backend_fixture()
        self.select('white')
        self.assertEqual(self.operator().invoke(self.context, None), {'FINISHED'})
        self.context.scene.frame_current = 2
        refresh(self.context)
        self.select('yellow')
        self.assertEqual(self.operator(True).invoke(self.context, None), {'FINISHED'})
        self.context.scene.frame_set.assert_called_once_with(3)
        loaded = core.load_project_state(root)
        self.assertEqual(loaded.effective_record('white', 'source', 1).to_dict()['authority'], 'user')
        self.assertIsNone(loaded.effective_record('yellow', 'source', 1))
        self.assertEqual(loaded.effective_record('yellow', 'source', 2),
                         before.effective_record('yellow', 'source', 2))
        self.assertEqual(self.ui._selection.observation_id, '')
        self.assertEqual(self.ui._repeat_character_id(), 'white')

    def test_repeat_real_publication_failure_keeps_frame_authority_and_derived_ownership(self):
        _core, root, _before, _cache, refresh = self.real_backend_fixture()
        self.select('white')
        self.assertEqual(self.operator().invoke(self.context, None), {'FINISHED'})
        self.context.scene.frame_current = 2
        refresh(self.context)
        self.select('yellow')
        frozen = (root / 'project_state.json').read_bytes()
        with mock.patch('vta_core.atomic_json.os.replace', side_effect=OSError('publish failed')):
            self.assertEqual(self.operator(True).invoke(self.context, None), {'CANCELLED'})
        self.assertEqual((root / 'project_state.json').read_bytes(), frozen)
        self.context.scene.frame_set.assert_not_called()
        self.assertEqual(self.context.scene.frame_current, 2)
        self.assertEqual(self.ui._repeat_character_id(), 'white')


if __name__ == '__main__':
    unittest.main()
