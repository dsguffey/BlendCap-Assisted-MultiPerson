"""Native keying prevention and explicit safe metadata-only Action migration."""
import importlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace as NS
import unittest

import bpy


class MetadataTests(unittest.TestCase):
    def setUp(self):
        from tests.test_blendcap_vta_object_placement import ObjectPlacementTests
        ObjectPlacementTests.setUpClass();self.f=ObjectPlacementTests(methodName='runTest');self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.meta=importlib.import_module(self.f.output.__package__+'.vta_metadata')
        if 'vta_output_stale' not in bpy.types.Object.bl_rna.properties:
            self.meta.register();self.addCleanup(self.meta.unregister)
    def seed(self):
        for obj in self.f.rigs:
            obj['vta_output_stale']=True;obj['vta_camera_registration_version']=1
            for key in self.meta.FIELDS:
                for frame in (2,4):obj.keyframe_insert('["'+key+'"]',frame=frame)
    def test_native_custom_keying_excludes_metadata_but_keeps_user_properties(self):
        import _keyingsets_utils as ks
        obj=self.f.rigs[0];obj['owner_expression']=.5;paths=[]
        ks.RKS_GEN_custom_props(None,bpy.context,NS(paths=NS(add=lambda block,path,**kw:paths.append(path))),obj)
        self.assertIn('["owner_expression"]',paths)
        for key in self.meta.FIELDS:
            self.assertFalse(bpy.types.Object.bl_rna.properties[key].is_animatable)
            self.assertNotIn('["'+key+'"]',paths);self.assertNotIn(key,paths)
    def test_actual_native_transform_custom_keying_does_not_key_metadata(self):
        f=self.f;obj=f.rigs[0];obj['owner_expression']=.5
        for other in bpy.context.selected_objects:other.select_set(False)
        obj.select_set(True);bpy.context.view_layer.objects.active=obj
        area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D');region=next(r for r in area.regions if r.type=='WINDOW')
        with bpy.context.temp_override(area=area,region=region):
            self.assertEqual(bpy.ops.anim.keyframe_insert_by_name(type='LocRotScaleCProp'),{'FINISHED'})
        paths={c.data_path for c in f.output._iter_action_fcurves(obj.animation_data.action)}
        self.assertIn('["owner_expression"]',paths)
        self.assertFalse(paths.intersection({'["'+n+'"]' for n in self.meta.FIELDS}))
    def test_exact_known_curves_removed_and_native_transform_values_untouched(self):
        self.seed();f=self.f
        for obj in f.rigs:
            obj.location.y+=.3;obj.keyframe_insert('location',frame=3)
        before=[f.curves(o) for o in f.rigs];props=[dict(o.items()) for o in f.rigs]
        for obj in f.rigs:
            with self.assertRaises(ValueError):f.output._regenerable_curves(obj,None,True)
        rows=self.meta.repair(f.rigs);self.assertEqual(len(rows),2)
        for obj,old,values in zip(f.rigs,before,props):
            expected={k:v for k,v in old.items() if k[0] not in {'["'+n+'"]' for n in self.meta.FIELDS}}
            self.assertEqual(f.curves(obj),expected);self.assertEqual(dict(obj.items()),values)
            f.output._regenerable_curves(obj,None,True)
        self.assertEqual(self.meta.repair(f.rigs),[])  # Explicit migration is idempotent.
    def test_unknown_custom_animation_still_refused_and_every_action_unchanged(self):
        self.seed();f=self.f;obj=f.rigs[1];obj['owner_expression']=.5;obj.keyframe_insert('["owner_expression"]',frame=2)
        before=[(o.animation_data.action,f.curves(o)) for o in f.rigs];actions=set(bpy.data.actions)
        with self.assertRaises(ValueError):self.meta.repair(f.rigs)
        self.assertEqual(before,[(o.animation_data.action,f.curves(o)) for o in f.rigs]);self.assertEqual(actions,set(bpy.data.actions))
    def test_varying_version_or_stale_values_are_not_silently_deleted(self):
        self.seed();f=self.f
        for name in self.meta.FIELDS:
            with self.subTest(name=name):
                curve=next(c for c in f.output._iter_action_fcurves(f.rigs[1].animation_data.action) if c.data_path=='["'+name+'"]')
                curve.keyframe_points[0].co.y=0
                before=[f.curves(o) for o in f.rigs]
                with self.assertRaisesRegex(ValueError,'varies'):self.meta.repair(f.rigs)
                self.assertEqual(before,[f.curves(o) for o in f.rigs]);curve.keyframe_points[0].co.y=1
    def test_constraints_nla_shared_actions_and_unowned_are_not_migrated(self):
        self.seed();f=self.f;obj=f.rigs[0];old=obj.animation_data.action
        other=bpy.data.objects.new('shared proof',None);bpy.context.scene.collection.objects.link(other)
        other.animation_data_create();other.animation_data.action=old
        with self.assertRaises(ValueError):self.meta.repair([obj])
        bpy.data.objects.remove(other,do_unlink=True)
        constraint=obj.constraints.new('COPY_LOCATION')
        with self.assertRaises(ValueError):self.meta.repair([obj])
        obj.constraints.remove(constraint)
        saved=obj['vta_character_id'];del obj['vta_character_id']
        with self.assertRaises(ValueError):self.meta.repair([obj])
        obj['vta_character_id']=saved
    def test_fixed_and_auto_generation_after_repair_keep_depth_keys(self):
        self.seed();f=self.f
        for obj in f.rigs:obj.keyframe_insert('location',frame=3)
        self.meta.repair(f.rigs);before=[f.curves(o,True) for o in f.rigs]
        for mode in ('FIXED','AUTOMATIC','FIXED'):
            f.output.import_reviewed_output(f.make_report(False),f.location,f.backend,depth_mode=mode)
            self.assertEqual(before,[f.curves(o,True) for o in f.rigs])
    def test_native_undo_redo_of_explicit_migration(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([bpy.app.binary_path,'--background','--factory-startup','--disable-autoexec','--python-exit-code','1',
                '--python',str(root/'tests/vta_metadata_native_undo.py'),'--',folder],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue(json.loads((Path(folder)/'verified.json').read_text())['undo_redo'])
