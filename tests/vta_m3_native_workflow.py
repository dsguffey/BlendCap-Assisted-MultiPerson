"""Fresh Blender UI routing, native Undo and Save As under private-import guard.

Background operators/data only; no GUI/event automation or owner QA writes.
"""
import sys,json,base64,importlib.util,importlib,subprocess,hashlib
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock
import bpy
sys.dont_write_bytecode=True
P=Path(__file__).resolve().parents[1];K=P.parent/'Video-to-Animation';sys.path.insert(0,str(P))
OUT=Path(sys.argv[-1]).resolve();OUT.mkdir(parents=True,exist_ok=True)
BLEND=OUT/'case.blend';ROOT=OUT/'case_vta';ROOT.mkdir(exist_ok=True)
movie_spec=importlib.util.spec_from_file_location('m3_movie',P/'tests/test_blendcap_vta_scene_timing.py')
movie=importlib.util.module_from_spec(movie_spec);movie_spec.loader.exec_module(movie)
VIDEO=OUT/'source.mp4';VIDEO.write_bytes(base64.b64decode(movie.MOVIE))
seed='''import sys,hashlib
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import vta_core as c
root=Path(sys.argv[2]);video=Path(sys.argv[3]);p=c.ProjectState('native-m3')
s=c.SourceReference('s',24.,0,3,hashlib.sha256(video.read_bytes()).hexdigest(),16,16);p.add_source(s)
p.create_character('Amy',character_id='amy');p.create_character('Zed',character_id='zed')
cache=c.AnalysisCache('a',s,c.ProviderDescriptor('test','test','test'));c.register_analysis_cache_revision(p,cache)
for n in range(4):cache.record_frame(c.ProviderFrameResult(n,n/24.,(c.ProviderCandidate('obs'+str(n),(1.,1.,8.,12.),.8),)))
c.save_project_state(p,root);c.save_analysis_cache(cache,root)
'''
PY=Path(sys.prefix)/'bin/python.exe'
subprocess.run([str(PY),'-I','-B','-c',seed,str(K),str(ROOT),str(VIDEO)],check=True)
from blendcap_vta_process_probe import private_import_guard
spec=importlib.util.spec_from_file_location('m3_native_addon',P/'__init__.py',submodule_search_locations=[str(P)])
addon=importlib.util.module_from_spec(spec);sys.modules[spec.name]=addon
with private_import_guard():
    spec.loader.exec_module(addon)
    from _bpy_restrict_state import RestrictBlend
    with RestrictBlend():addon.register()
    ui=importlib.import_module(spec.name+'.blendcap.vta_review_ui')
    session=importlib.import_module(spec.name+'.blendcap.vta_scene_session')
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))
    scene=bpy.data.scenes.new('VTA Native M3');bpy.context.window.scene=scene
    for key,value in dict(vta_scene_timing_version=1,vta_project_id='native-m3',vta_source_id='s',vta_source_video_sha256=hashlib.sha256(VIDEO.read_bytes()).hexdigest()).items():scene[key]=value
    seq=scene.sequence_editor_create();strip=seq.strips.new_movie('test',str(VIDEO),channel=1,frame_start=1);seq.active_strip=strip
    scene.blendcap_props.video_source='VSE';scene.render.fps=24;scene.frame_set(1)
    workspace=bpy.context.workspace;workspace['blendcap_vta_review_workspace']=1
    preview=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D');preview.type='SEQUENCE_EDITOR';preview.spaces.active.view_type='SEQUENCER_PREVIEW'
    viewport=next(a for a in bpy.context.screen.areas if a.type=='PROPERTIES');viewport.type='VIEW_3D'
    session.load(bpy.context,scene)
    assert ui._view is not None and ui._display.context.project_id=='native-m3'
    assert not hasattr(ui._get_backend(bpy.context),'_core')
    with bpy.context.temp_override(area=preview,region=next(r for r in preview.regions if r.type=='WINDOW')):
        ui._selection.character_id='amy';ui._selection.observation_id='obs0'
        assert bpy.ops.blendcap.vta_review_assign('EXEC_DEFAULT')=={'FINISHED'}
        assert next(c for c in ui._view.characters if c.character_id=='amy').occupied_observation_id=='obs0'
        backend=ui._get_backend(bpy.context);location=ui._location
        request,_=backend.prepare_hard_state(location,character_id='amy',state='occluded')
        operator=NS(decision_kind='occluded',_confirmed_request=request,_confirmation_location=location,report=mock.Mock())
        assert ui.BLENDCAP_OT_vta_review_hard_state.execute(operator,bpy.context)=={'FINISHED'}
        request=backend.prepare_management(location,'clear-all-review-keys-on-frame',source_id='s',frame=0)
        backend.apply_management(location,request,confirmed=True)
        assert bpy.ops.blendcap.vta_restore_protected('EXEC_DEFAULT')=={'FINISHED'}
        assert next(c for c in ui._view.characters if c.character_id=='amy').decision_state=='occluded'
        assert ui._location.source_frame==0
        # Cached polls/frame redraws cannot start engine calls.
        with mock.patch.object(backend.client,'call',side_effect=AssertionError('Poll/draw IPC')):
            for _ in range(30):
                ui.BLENDCAP_OT_vta_review_assign.poll(bpy.context)
                ui.overlay_state();ui.timeline_state(bpy.context)
    obj=bpy.data.objects.new('Native manual edit',bpy.data.armatures.new('Native manual bones'));scene.collection.objects.link(obj)
    obj.location=(0.,0.,0.);obj.keyframe_insert('location',frame=1);obj.scale=(1.2,1.3,1.4);obj.keyframe_insert('scale',frame=1)
    names=lambda:json.loads((ROOT/'project_state.json').read_text())['identity_keyframes']
    truth=names()
    keys=importlib.import_module(spec.name+'.blendcap.vta_depth_keys')
    def keyed():return keys.keyed_frames(bpy.data.objects['Native manual edit'])
    with bpy.context.temp_override(area=viewport,region=next(r for r in viewport.regions if r.type=='WINDOW')):
        bpy.ops.ed.undo_push(message='Native base')
        obj.location.z=1.;obj.keyframe_insert('location',frame=2);bpy.ops.ed.undo_push(message='Native keyed depth')
        client=ui._get_backend(bpy.context).client;actual=client.call;operations=[]
        def counted(op,*args,**kwargs):operations.append(op);return actual(op,*args,**kwargs)
        with mock.patch.object(client,'call',side_effect=counted):
            for _ in range(3):
                bpy.ops.ed.undo();assert names()==truth;assert keyed()==(1.,)
                bpy.ops.ed.redo();assert names()==truth;assert keyed()==(1.,2.)
        from m3_native_addon import vta_protocol as wire
        assert not set(operations)&set(wire.MUTATIONS),operations
        bpy.ops.ed.undo_push(message='Before successful Character creation')
        created,_=ui._get_backend(bpy.context).create_character(ui._location,display_name='Created before native Undo')
        bpy.ops.ed.undo_push(message='Created native membership');bpy.ops.ed.undo()
        membership_module=importlib.import_module(spec.name+'.blendcap.vta_scene_characters')
        assert created.character_id not in membership_module.stored(bpy.context.scene)
        session.load(bpy.context,bpy.context.scene)
        assert created.character_id in membership_module.stored(bpy.context.scene)
        recovery=importlib.import_module(spec.name+'.blendcap.vta_review_undo')
        real=ui._engine_client(bpy.context)
        def lose(op,payload,**kwargs):
            if op=='character-create':real.call(op,payload,**kwargs)
            raise OSError('lost create response and status')
        failed=NS(call=lose)
        try:
            with mock.patch.object(recovery,'_client',return_value=failed):
                ui._get_backend(bpy.context).create_character(ui._location,display_name='Lost reply Character')
        except OSError:pass
        else:raise AssertionError('Lost response did not remain pending')
        assert recovery._pending and (ROOT/recovery.PENDING_FILE).is_file()
        bpy.ops.ed.undo_push(message='Pending public adoption');bpy.ops.ed.undo()
        assert recovery._pending
    bpy.ops.wm.save_mainfile()
    before=(ROOT/'project_state.json').read_bytes()
    copied=OUT/'Independent.blend';bpy.ops.wm.save_as_mainfile(filepath=str(copied))
    assert (OUT/'Independent_vta/project_state.json').read_bytes()==before
    bpy.ops.wm.open_mainfile(filepath=str(copied));ROOT=OUT/'Independent_vta'
    session.load(bpy.context,bpy.context.scene)
    assert recovery._pending
    recovery.retry(bpy.context)
    assert not recovery._pending
    created=next(c for c in ui._get_backend(bpy.context).observation.characters if c.display_name=='Lost reply Character')
    membership=importlib.import_module(spec.name+'.blendcap.vta_scene_characters').stored(bpy.context.scene)
    assert created.character_id in membership
    assert (OUT/'case_vta'/recovery.PENDING_FILE).is_file()
    assert ui._get_backend(bpy.context).last_restore_id is None
    assert (ROOT/'project_state.json').read_bytes()==before
    assert bpy.data.objects.get('Native manual edit') is not None
    ui._selection.character_id='amy';ui._selection.observation_id='obs0'
    assert not any(n=='vta_core' or n.startswith('vta_core.') for n in sys.modules)
    addon.unregister()
(OUT/'verified.json').write_text(json.dumps(dict(real_ui_assignment=True,real_ui_hard_state=True,explicit_restore=True,
    poll_draw_ipc=0,native_undo_mutations=0,native_cycles=3,save_reopen=True,independent_save_as=True,
    ordinary_restore_clears_on_reopen=True,explicit_load_recovers_created_character_after_native_undo=True,
    pending_create_reconciles_scene_membership=True,private_host_imports=0),indent=2))
print('M3 real UI/native/persistence isolation passed')
