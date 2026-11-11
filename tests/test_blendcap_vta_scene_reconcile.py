"""One-shot Scene topology planning without Blender or owner project writes."""
import unittest
import ast,hashlib,json,sys,tempfile
from pathlib import Path
from types import ModuleType,SimpleNamespace as NS
from unittest import mock
from blendcap_vta_scene_reconcile import PROJECT_FIELDS,plan
from tests.test_blendcap_vta_ui_regressions import callbacks


def project(*ids):
    value={key:[] for key in PROJECT_FIELDS}
    value.update(project_id='p',schema='vta-project',version=1,
                 characters=[dict(character_id=cid,display_name=cid) for cid in ids])
    return value


def scene(owner,ids,source='s'):
    return dict(project_id='p',source_id=source,owner_id=owner,
                membership=None if ids is None else tuple(ids),history_projects=())


def receipt(owner,cid,request_id):
    return dict(project_id='p',source_id='s',scene_owner_id=owner,
                character_id=cid,request_id=request_id)


class SceneReconcileTests(unittest.TestCase):
    def test_missing_scene_load_fails_before_any_binding_change(self):
        parent=ModuleType('missing_scene_fixture');parent.__path__=[]
        child=ModuleType('missing_scene_fixture.blendcap');child.__path__=[]
        modules={m.__name__:m for m in (parent,child)}
        for name in ('vta_review_ui','vta_workspace','properties'):
            module=ModuleType(child.__name__+'.'+name);modules[module.__name__]=module
        env=dict(__package__=child.__name__,bpy=NS(data=NS(scenes=[])))
        with mock.patch.dict(sys.modules,modules):
            callbacks('blendcap/vta_scene_session.py',('load',),env)
            with self.assertRaisesRegex(ValueError,'Blender Scene no longer exists'):
                env['load'](NS(),None)
            with self.assertRaisesRegex(ValueError,'Blender Scene no longer exists'):
                env['load'](NS(),NS(name='Deleted Scene'))

    def test_explicit_load_reconciles_before_presentation_refresh(self):
        source=Path(__file__).resolve().parents[1]/'blendcap'/'vta_scene_session.py'
        definition=next(node for node in ast.parse(source.read_text(encoding='utf-8')).body
                        if isinstance(node,ast.FunctionDef) and node.name=='load')
        calls=[(node.func.id if isinstance(node.func,ast.Name) else node.func.attr,node.lineno)
               for node in ast.walk(definition) if isinstance(node,ast.Call)
               and isinstance(node.func,(ast.Name,ast.Attribute))]
        self.assertLess(next(line for name,line in calls if name=='reconcile'),
                        next(line for name,line in calls if name=='refresh'))

    def test_load_choices_only_enumerate_surviving_blender_scenes(self):
        parent=ModuleType('scene_choices_fixture');parent.__path__=[]
        child=ModuleType('scene_choices_fixture.blendcap');child.__path__=[]
        session=ModuleType(child.__name__+'.vta_scene_session');session.validate=lambda ctx,scene:None
        existing=NS(name='Surviving Scene')
        env=dict(__package__=child.__name__,bpy=NS(data=NS(scenes=[existing])),
                 _load_scene_enum_items=[])
        with mock.patch.dict(sys.modules,{m.__name__:m for m in (parent,child,session)}):
            callbacks('blendcap/vta_review_ui.py',('build_load_scene_choices',),env)
            self.assertEqual([row[0] for row in env['build_load_scene_choices'](NS())],['Surviving Scene'])

    def test_file_open_schedules_one_reconciliation_without_polling(self):
        scheduled=[]
        bpy=NS(data=NS(filepath='existing.blend'),app=NS(
            handlers=NS(persistent=lambda fn:fn),timers=NS(
                is_registered=lambda fn:fn in scheduled,
                register=lambda fn,**kwargs:scheduled.append(fn))))
        env=dict(__package__='open_fixture.blendcap',bpy=bpy,reconcile=mock.Mock())
        callbacks('blendcap/vta_scene_reconcile.py',('_run_after_open','file_opened'),env)
        env['file_opened']();env['file_opened']()
        self.assertEqual(len(scheduled),1)
        bpy.context=NS()
        self.assertIsNone(scheduled.pop()())
        env['reconcile'].assert_called_once_with(bpy.context)

    def test_deleted_scene_receipt_and_unreferenced_character_are_pruned(self):
        result=plan(project('a','b'),[scene('owner-a',('a',))],
                    [receipt('owner-b','b','receipt-b')])
        self.assertEqual(result.stale_receipts,('receipt-b',))
        self.assertEqual(result.orphans,('b',))
        self.assertIn(('a','surviving Scene/native/receipt'),result.preserved)

    def test_valid_other_scene_and_authoritative_decision_preserve_characters(self):
        value=project('a','b','c')
        value['identity_keyframes']=[dict(character_id='c',source_id='s',frame=0)]
        result=plan(value,[scene('owner-a',('a',)),scene('owner-b',('b',))],[])
        self.assertEqual(result.orphans,())
        self.assertIn(('c','canonical project reference'),result.preserved)

    def test_missing_membership_or_legacy_owner_ambiguity_blocks_cleanup(self):
        result=plan(project('a'),[scene(None,None)],[receipt('old','a','receipt-a')])
        self.assertEqual(result.orphans,())
        self.assertEqual(result.stale_receipts,())

    def test_native_reference_and_history_preserve_without_scene_transfer(self):
        value=project('a','b')
        surviving=scene('owner-a',('a',))
        surviving['history_projects']=('p',)
        self.assertEqual(plan(value,[surviving],[],native_ids=('b',)).orphans,())

    def test_unknown_scene_membership_ids_are_identified_for_pruning(self):
        result=plan(project('a'),[scene('owner-a',('a','deleted'))],[])
        self.assertEqual(result.stale_memberships,((0,('deleted',)),))

    def test_unknown_project_schema_or_pending_recovery_never_deletes(self):
        value=project('a');value['future_field']='unknown'
        self.assertEqual(plan(value,[],[]).orphans,())
        self.assertEqual(plan(project('a'),[],[],pending=True).orphans,())

    def test_runtime_removes_only_deleted_owner_and_uses_engine_for_orphan(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'checkpoints'/'dev-preview') as directory:
            root=Path(directory);path=root/'project_state.json'
            state=project('a','b');path.write_text(json.dumps(state),encoding='utf-8')
            folder=root/'.vta-host-bindings';folder.mkdir()
            (folder/'receipt-b.json').write_text('{}')
            class Scene(dict):
                def as_pointer(self):return id(self)
            member=json.dumps(dict(version=1,project_id='p',source_id='s',ids=['a','deleted'],history={}))
            existing=Scene(vta_project_id='p',vta_source_id='s',vta_scene_owner_id='owner-a',
                           vta_scene_characters=member)
            bpy=NS(data=NS(scenes=[existing],objects=[],armatures=[],actions=[],workspaces=[],filepath=str(root/'QA.blend')))
            calls=[]
            def engine_call(operation,payload):
                calls.append(operation)
                if operation=='project-state':
                    return dict(project_id='p',revision=dict(number=0,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
                self.assertEqual(operation,'review-management')
                self.assertEqual(payload['character_id'],'b')
                self.assertEqual(payload['action'],'delete-character')
                state['characters']=[c for c in state['characters'] if c['character_id']!='b']
                path.write_text(json.dumps(state),encoding='utf-8')
                return dict(after=dict(project_id='p',revision=dict(number=1,sha256=hashlib.sha256(path.read_bytes()).hexdigest())))
            client=NS(call=engine_call)
            ui=ModuleType('reconcile_fixture.blendcap.vta_review_ui')
            ui._engine_client=lambda context:client;ui._analysis_locked=lambda:False
            ui._display=NS(clear=mock.Mock());ui._observer_tokens={}
            bindings=ModuleType('reconcile_fixture.blendcap.vta_character_bindings')
            bindings.read=lambda root,with_ids: [receipt('owner-b','b','receipt-b')]
            bindings.path=lambda root:folder
            undo=ModuleType('reconcile_fixture.blendcap.vta_review_undo');undo.blocked=lambda:False
            session=ModuleType('reconcile_fixture.blendcap.vta_scene_session');session.LAST='loaded'
            parent=ModuleType('reconcile_fixture');parent.__path__=[]
            child=ModuleType('reconcile_fixture.blendcap');child.__path__=[]
            modules={m.__name__:m for m in (parent,child,ui,bindings,undo,session)}
            env=dict(__package__=child.__name__,bpy=bpy,Path=Path,hashlib=hashlib,json=json,
                     uuid4=lambda:NS(hex='1'*32),PROJECT_FIELDS=PROJECT_FIELDS,plan=plan,
                     companion_root_for_blend=lambda path:root)
            with mock.patch.dict(sys.modules,modules):
                callbacks('blendcap/vta_scene_reconcile.py',
                          ('_membership','_native_ids','_clear_missing_remembered','reconcile'),env)
                result=env['reconcile'](NS(),root)
            self.assertEqual(result.orphans,('b',))
            self.assertEqual(calls,['project-state','review-management'])
            self.assertEqual([c['character_id'] for c in state['characters']],['a'])
            self.assertNotIn('b',[c['display_name'] for c in state['characters']])
            self.assertFalse((folder/'receipt-b.json').exists())
            self.assertEqual(json.loads(existing['vta_scene_characters'])['ids'],['a'])


if __name__=='__main__':unittest.main()
