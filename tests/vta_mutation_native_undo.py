"""Fresh factory Blender native Undo/Redo/save/reopen, no GUI or owner files.

The host uses neutral JSON and production IPC/recovery code under a private-import
guard. The minimal UI facade samples authoritative names for parity assertions.
"""
import importlib
import json
from pathlib import Path
import sys
import time
import types
import shutil
from unittest import mock
import bpy

sys.dont_write_bytecode=True
P=Path(__file__).resolve().parents[1];K=P.parent/'Video-to-Animation'
OUT=Path(sys.argv[-1]).resolve();OUT.mkdir(parents=True,exist_ok=True)
ROOT=OUT/'test_vta';ROOT.mkdir(exist_ok=True)
BLEND=OUT/'test.blend'
sys.path.insert(0,str(P))
from blendcap_vta_engine import EngineClient,EngineEndpoint,EngineError
from blendcap_vta_process_probe import private_import_guard
PY=Path(sys.prefix)/'bin/python.exe';metrics=[]
def client(_context=None):return EngineClient(EngineEndpoint(PY,K/'vta_engine.py'))
raw=dict(schema='video-to-animation-project',version='0.8',project_id='native-m2',sources=[],
    characters=[dict(character_id='alice',display_name='Alice'),dict(character_id='bob',display_name='Bob')],
    analysis_revisions=[],active_analysis_revision_by_source={},identity_keyframes=[],candidate_suppression_history=[],
    repair_ranges=[],tracking_revisions=[],review_flags=[])
(ROOT/'project_state.json').write_text(json.dumps(raw),encoding='utf-8')
for name,path in [('m2host',P),('m2host.blendcap',P/'blendcap')]:
    module=types.ModuleType(name);module.__path__=[str(path)];sys.modules[name]=module
ui=types.ModuleType('m2host.blendcap.vta_review_ui');sys.modules[ui.__name__]=ui
def authority():return next(c['display_name'] for c in json.loads((ROOT/'project_state.json').read_text())['characters'] if c['character_id']=='alice')
def refresh(*_a,**_kw):bpy.context.scene['m2_visible_name']=authority()
ui._engine_client=client;ui.refresh=refresh;ui._display={};ui._tracking_hints={}
ui.vta_invalidation=types.SimpleNamespace(request_redraw=lambda:None)
ui.set_operation_feedback=lambda *_a,**_kw:None
bridge=importlib.import_module('m2host.blendcap.vta_review_undo')

class Rename(bpy.types.Operator):
    bl_idname='test.m2_rename';bl_label='M2 Rename';bl_options={'REGISTER'}
    name: bpy.props.StringProperty()
    def execute(self,context):
        observed=client().call('character-state',dict(companion_root=str(ROOT),character_id='alice'))
        started=time.perf_counter();bridge.rename(context,ROOT,observed,self.name);refresh()
        metrics.append(dict(kind='rename',seconds=time.perf_counter()-started));return {'FINISHED'}

def assert_name(name):
    assert authority()==name,(authority(),name)
    assert bpy.context.scene['m2_visible_name']==name

def undo():
    started=time.perf_counter();bpy.ops.ed.undo();metrics.append(dict(kind='undo',seconds=time.perf_counter()-started))

def redo():
    started=time.perf_counter();bpy.ops.ed.redo();metrics.append(dict(kind='redo',seconds=time.perf_counter()-started))

with private_import_guard():
    bridge.register();bpy.utils.register_class(Rename)
    refresh();bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))
    area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
    region=next(r for r in area.regions if r.type=='WINDOW')
    with bpy.context.temp_override(area=area,region=region):
        bpy.context.scene['native_depth']=0.;bpy.ops.ed.undo_push(message='Base')
        bpy.ops.test.m2_rename(name='B');bpy.context.scene['native_depth']=1.;bpy.ops.ed.undo_push(message='Depth one');assert_name('B')
        bpy.ops.test.m2_rename(name='C');bpy.context.scene['native_depth']=2.;bpy.ops.ed.undo_push(message='Depth two');assert_name('C')
        for _ in range(3):
            undo();assert_name('C');assert bpy.context.scene['native_depth']==1.
            undo();assert_name('C');assert bpy.context.scene['native_depth']==0.
            redo();assert_name('C');redo();assert_name('C')
        # Native Undo never needs the mutation client, even if it is unavailable.
        failed=mock.Mock();failed.call.side_effect=EngineError('ENGINE_UNAVAILABLE','injected unavailable')
        with mock.patch.object(bridge,'_client',return_value=failed):undo()
        failed.call.assert_not_called();assert_name('C');assert not bridge._pending
        # Lose the response AND status after a real committed rename. Preserve
        # its exact request identity through native Undo, Save, and Save As.
        real=client();observed=real.call('character-state',dict(companion_root=str(ROOT),character_id='alice'))
        def lose(operation,payload,**kw):
            if operation=='character-rename':real.call(operation,payload,**kw)
            raise OSError('lost response/status')
        failed.call.side_effect=lose
        try:
            with mock.patch.object(bridge,'_client',return_value=failed):bridge.rename(bpy.context,ROOT,observed,'Committed')
        except OSError:pass
        else:raise AssertionError('Lost response must retain pending state')
        bpy.ops.ed.undo_push(message='Pending native edit');undo()
        assert_name('Committed')
        assert bridge._pending and (ROOT/bridge.PENDING_FILE).is_file()
        try:bridge.require_ready()
        except RuntimeError:pass
        else:raise AssertionError('Pending state did not block edits')
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))
    from blendcap_vta_project_copy import CopyPlan
    original=ROOT;copied=OUT/'copied_vta';plan=CopyPlan(original,copied);plan.project()
    for relative,(source,_digest) in plan.files.items():
        path=copied/relative;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,path)
    BLEND=OUT/'copied.blend';bpy.ops.wm.save_as_mainfile(filepath=str(BLEND));ROOT=copied
    bpy.ops.wm.open_mainfile(filepath=str(BLEND))
    assert bridge._pending and authority()=='Committed'
    bridge.retry(bpy.context);assert_name('Committed');assert not bridge._pending
    assert json.loads((original/'project_state.json').read_text())['characters'][0]['display_name']=='Committed'
    assert (original/bridge.PENDING_FILE).is_file() # Copy recovery cannot clear original.
    assert not any(t.get('vta_reconciliation_pending') for t in bpy.data.texts)
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))
    bpy.ops.wm.open_mainfile(filepath=str(BLEND));refresh();assert_name('Committed')
    assert not bridge._pending
    # New session after reopen starts at the persisted authoritative state.
    area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D');region=next(r for r in area.regions if r.type=='WINDOW')
    with bpy.context.temp_override(area=area,region=region):
        bpy.ops.ed.undo_push(message='Reopened base');bpy.ops.test.m2_rename(name='D');bpy.ops.ed.undo_push(message='D')
        undo();assert_name('D');redo();assert_name('D')
        # Native Undo can remove a generated output created since the last step.
        # Warn rather than recreating it or rewinding authority.
        rig=bpy.data.objects.new('Generated diagnostic rig',bpy.data.armatures.new('Diagnostic bones'))
        bpy.context.scene.collection.objects.link(rig)
        rig['vta_project_id']='native-m2';rig['vta_character_id']='alice';rig['vta_output_file']='diagnostic-output.json'
        bpy.ops.ed.undo_push(message='Native generated output')
        undo();assert_name('D');assert 'Generated diagnostic rig' not in bpy.data.objects
        assert bridge._presentation_warning
        redo();assert_name('D');assert 'Generated diagnostic rig' in bpy.data.objects
        assert bridge._presentation_warning
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND));bpy.ops.wm.open_mainfile(filepath=str(BLEND));refresh();assert_name('D')
    assert not any(n=='vta_core' or n.startswith('vta_core.') for n in sys.modules)
    assert not any(t.get('vta_review_undo') for t in bpy.data.texts)
    assert not (ROOT/'.vta-engine/snapshots').exists()
    bridge.unregister();bpy.utils.unregister_class(Rename)
(OUT/'verified.json').write_text(json.dumps(dict(native_cycles=3,multistep=True,pending_save_reopen=True,
    explicit_retry=True,pending_save_as_independent=True,normal_save_reopen=True,domain_undo=False,missing_output_warned_not_regenerated=True,private_host_imports=False,metrics=metrics),indent=2))
print('M2 minimal Undo/recovery/save/reopen passed')
