import importlib,json,unittest
from types import SimpleNamespace as NS
from unittest import mock
import bpy


class SceneCharacterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_scene_binding import SceneBindingTests
        SceneBindingTests.setUpClass()
        cls.fixture_type=SceneBindingTests
        cls.ui=SceneBindingTests.ui
        cls.scope=importlib.import_module(cls.ui.__package__+'.vta_scene_characters')

    def test_legacy_multi_source_inference_does_not_expose_project_wide_characters(self):
        class Scene(dict):objects=()
        a=Scene(vta_project_id='p',vta_source_id='a');b=Scene(vta_project_id='p',vta_source_id='b')
        project=NS(characters=[NS(character_id='white'),NS(character_id='woman')],to_dict=lambda:dict(
            sources=['a','b'],identity_keyframes=[dict(character_id='white',source_id='a'),dict(character_id='woman',source_id='b')]))
        self.assertEqual(self.scope.allowed(a,project),{'white'});self.assertEqual(self.scope.allowed(b,project),{'woman'})
        self.scope.bind(a,project);self.scope.bind(b,project)
        self.assertEqual(self.scope.stored(a),{'white'});self.assertEqual(self.scope.stored(b),{'woman'})
        c=Scene(vta_project_id='p',vta_source_id='a');self.scope.bind(c,project,fresh=True)
        self.assertEqual(self.scope.allowed(c,project),set())

    def test_foreign_assigned_candidate_never_selects_foreign_character(self):
        from blendcap_vta_review import ReviewView,CharacterRow,CandidateRow,SelectionState
        view=ReviewView('r','s',1,100,100,'analyzed-with-candidates','ok',(
            CandidateRow('r','o',(0,0,1,1),'Candidate','foreign','Foreign'),),
            (CharacterRow('local','Local',None),CharacterRow('foreign','Foreign','o')))
        filtered=self.scope.frame(view,{'local'})
        self.assertEqual([c.character_id for c in filtered.characters],['local'])
        self.assertIsNone(filtered.candidates[0].assigned_character_name)
        self.assertFalse(filtered.candidates[0].assignable,'Do not erase authority/pretend candidate is free')
        selection=SelectionState();selection.character_id='local'
        self.assertTrue(selection.select_observation(filtered,'o'));self.assertEqual(selection.character_id,'local')

    def test_clear_rechoose_retains_only_this_scenes_membership_per_source(self):
        class Scene(dict):objects=()
        scene=Scene(vta_project_id='p',vta_source_id='a')
        project=NS(characters=[NS(character_id='white'),NS(character_id='woman')])
        self.scope.bind(scene,project,fresh=True);self.scope.add(scene,project,'white')
        scene.pop('vta_project_id');scene.pop('vta_source_id')
        self.assertIsNone(self.scope.stored(scene))
        scene.update(vta_project_id='p',vta_source_id='b');self.scope.bind(scene,project,fresh=True)
        self.assertEqual(self.scope.stored(scene),set());self.scope.add(scene,project,'woman')
        scene['vta_source_id']='a';self.scope.bind(scene,project,fresh=True)
        self.assertEqual(self.scope.stored(scene),{'white'})
        other=Scene(vta_project_id='p',vta_source_id='a');self.scope.bind(other,project,fresh=True)
        self.assertEqual(self.scope.stored(other),set())

    def test_object_mode_multiselection_active_and_reverse_sync_without_recursion(self):
        t=self.fixture_type();t.setUp();self.addCleanup(t.doCleanups)
        scene=t.owned();t.session.load(bpy.context,scene)
        from blendcap_vta_timeline import TimelineView,TimelineCharacter
        from blendcap_vta_review import ReviewView,CharacterRow,CandidateRow
        ui=self.ui;objects=[]
        for identity in ('a','b'):
            data=bpy.data.armatures.new('rig');obj=bpy.data.objects.new('same display label',data)
            scene.collection.objects.link(obj);objects.append(obj)
            obj['vta_project_id']=scene['vta_project_id'];obj['vta_source_id']='s';obj['vta_character_id']=identity
            self.addCleanup(bpy.data.objects.remove,obj,do_unlink=True)
        ui._timeline=TimelineView('s',tuple(TimelineCharacter(i,i) for i in ('a','b')),())
        ui._view=ReviewView('r','s',0,16,16,'analyzed-with-candidates','ok',tuple(
            CandidateRow('r','o'+i,(0,0,1,1),'candidate',i,i) for i in ('a','b')),
            tuple(CharacterRow(i,i,'o'+i) for i in ('a','b')))
        ui._scene_character_ids={'a','b'}
        ui._rebuild_enum_items()
        ui._object_selection_tokens.clear()
        bpy.context.view_layer.update()
        for obj in objects:obj.select_set(True)
        bpy.context.view_layer.objects.active=objects[1]
        with mock.patch.object(ui,'_presentation_current',return_value=True):
            self.assertTrue(ui.sync_objects_to_review(bpy.context))
            self.assertEqual(ui.current_character_id(),'b')
            self.assertEqual(ui.overlay_state()[1],'ob')
            self.assertEqual(set(ui.tracking_character_ids(bpy.context)),{'a','b'})
            self.assertFalse(ui.sync_objects_to_review(bpy.context))
            with bpy.context.temp_override(area=t.timeline):
                self.assertTrue(ui.select_timeline_character(bpy.context,'a'))
            self.assertTrue(objects[0].select_get());self.assertFalse(objects[1].select_get())
            self.assertIs(bpy.context.view_layer.objects.active,objects[0])
            self.assertEqual(ui.overlay_state()[1],'oa')

    def test_cleanup_panel_is_collapsible_child_and_generation_reason_is_visible(self):
        cls=self.ui.VIEW3D_PT_blendcap_vta_animation_cleanup
        self.assertEqual(cls.bl_parent_id,self.ui.VIEW3D_PT_blendcap_vta_animation.bl_idname)
        self.assertIn('DEFAULT_CLOSED',cls.bl_options)
        with mock.patch.object(self.ui,'_analysis_locked',return_value=True):
            self.assertIn('operation',self.ui.generation_unavailable_reason(None))

    def test_real_scene_round_trip_filters_timeline_preview_and_enums(self):
        from blendcap_vta_review import ReviewView,CharacterRow
        from blendcap_vta_timeline import TimelineView,TimelineCharacter
        t=self.fixture_type();t.setUp();self.addCleanup(t.doCleanups)
        backend=t.display_backend()
        project=backend._core.load_project_state.return_value
        project.characters=(NS(character_id='a'),NS(character_id='b'))
        backend.query=lambda loc,**kw:ReviewView('analysis','s',loc.source_frame,16,16,'analyzed-empty','empty',(),
            tuple(CharacterRow(i,i,None) for i in ('a','b')))
        backend.timeline_from_context.return_value=TimelineView('s',tuple(TimelineCharacter(i,i) for i in ('a','b')),())
        a=t.owned();b=a.copy()
        for scene,identity in ((a,'a'),(b,'b')):
            self.scope.bind(scene,project,fresh=True);self.scope.add(scene,project,identity)
        for scene,expected in ((a,'a'),(b,'b'),(a,'a')):
            t.session.load(bpy.context,scene)
            self.assertEqual([row.character_id for row in self.ui._view.characters],[expected])
            self.assertEqual([row.character_id for row in self.ui._timeline.characters],[expected])
            self.assertEqual([row[0] for row in self.ui.character_items(None,bpy.context)][1:],[expected])
            self.assertEqual([row.character_id for row in self.ui.overlay_state()[0].characters],[expected])
            with bpy.context.temp_override(area=t.timeline):
                self.assertFalse(self.ui.select_timeline_character(bpy.context,'b' if expected=='a' else 'a'))
