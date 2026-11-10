"""UI geometry and dispatch checks runnable without starting Blender.

Only the tested host callbacks are compiled; RNA, GPU and window input are sinks.
Native marker operators themselves require the owner's interactive acceptance.
"""
import ast
from collections import OrderedDict
from dataclasses import replace
import importlib
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest import mock

from blendcap_vta_timeline import TimelineCharacter, TimelineKey, TimelineView, ReviewKeySelection
from blendcap_vta_workspace import (
    TimelineMetrics, channel_items, channel_rows, character_lanes,
    hit_character, hit_review_key, review_keys_in_box, characters_in_box,
)
from blendcap_vta_review import ReviewView, CharacterRow, ReviewLocation, SelectionState, VtaReviewUiError

ROOT = Path(__file__).resolve().parents[1]


def callbacks(file, names, env):
    """Execute real callback bodies with host dependencies supplied explicitly."""
    tree = ast.parse((ROOT/file).read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if getattr(node, 'name', None) in names]
    assert {n.name for n in nodes} == set(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT/file), 'exec'), env)
    return env


def model(count=30):
    return TimelineView('source', tuple(TimelineCharacter(str(i), f'Character {i:02}') for i in range(count)),
        tuple(TimelineKey(f'key-{i}', str(i), 12+i*3, 'occluded', None) for i in range(count)))


class GeometryTests(unittest.TestCase):
    def test_overflow_every_parent_and_depth_row_is_reachable_and_clipped(self):
        view = model()
        expanded = [c.character_id for c in view.characters]
        for scale in (.75, 1, 2, 3):
            for size in (13, 24, 32):
                for slots in (1, 2, 3, 7):
                    metrics = TimelineMetrics(scale, size)
                    height = metrics.top_padding + metrics.bottom_padding + slots*metrics.row_height + 1
                    seen = set()
                    for offset in range(60):
                        m = replace(metrics, scroll_rows=offset)
                        rows = channel_rows(view, height, metrics=m, expanded=expanded)
                        seen.update((r.character_id, r.kind) for r in rows)
                        for r in rows:
                            self.assertGreaterEqual(r.y - m.row_height/2, m.bottom_padding)
                            self.assertLessEqual(r.y + m.row_height/2, height-m.top_padding)
                        for a,b in zip(rows, rows[1:]):
                            self.assertAlmostEqual(a.y-b.y, m.row_height)
                    self.assertEqual(seen, set(channel_items(view, expanded)))

    def test_click_shift_and_box_share_scrolled_parent_coordinates_and_x_transform(self):
        view=model();expanded=[c.character_id for c in view.characters]
        projection=NS(view_to_region=lambda f,y,clip=False:(f*2,0))
        selection=ReviewKeySelection()
        for offset in (1, 8, 27, 53):
            metrics=TimelineMetrics(scroll_rows=offset)
            rows=channel_rows(view,240,metrics=metrics,expanded=expanded)
            expected=[]
            for row in rows:
                if row.kind=='depth':
                    self.assertIsNone(hit_character(view,240,row.y,metrics=metrics,expanded=expanded))
                    continue
                key=next(k for k in view.keys if k.character_id==row.character_id)
                x=(key.source_frame+100)*2
                self.assertEqual(hit_character(view,240,row.y,metrics=metrics,expanded=expanded),key.character_id)
                self.assertEqual(hit_review_key(view,projection,240,(x,row.y),100,metrics=metrics,expanded=expanded),key.keyframe_id)
                selection.click(view,key.keyframe_id,extend=True)
                expected.append(key.keyframe_id)
            ids=review_keys_in_box(view,projection,2000,240,(0,42),(1999,239),100,
                                   metrics=metrics,expanded=expanded)
            self.assertEqual(set(ids),set(expected))
            self.assertEqual(set(characters_in_box(view,240,(0,42),(1999,239),metrics=metrics,expanded=expanded)),
                             {r.character_id for r in rows if r.kind=='character'})
        self.assertTrue(selection.keyframe_ids)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.view=model()
        self.ui=NS(_timeline=self.view)
        self.env=dict(math=math, replace=replace, TimelineMetrics=TimelineMetrics,
            channel_items=channel_items, _row_scroll={}, _row_pan={}, _box_gesture=None,
            _text_size=lambda c:13, _expansion_key=lambda c:c.scope,
            expanded_characters=lambda c,v:[ch.character_id for ch in v.characters],
            _ui=lambda:self.ui, bpy=NS(types=NS(Operator=object),props=NS(EnumProperty=lambda **kw:None)),
            vta_invalidation=NS(request_redraw=mock.Mock()))
        callbacks('blendcap/vta_workspace.py',('_metrics','_native_bottom','_rows',
            'BLENDCAP_OT_vta_timeline_scroll','BLENDCAP_OT_vta_timeline_keys'),self.env)
        self.env['channel_rows']=channel_rows
        self.ctx=NS(scope='A',region=NS(type='WINDOW',height=240))
        self.env['_state']=lambda c:(self.ui._timeline,(),None,0,0,None)

    def event(self,kind,y=120,**kw):return NS(type=kind,mouse_region_y=y,**kw)

    def scroll(self,event):return self.env['BLENDCAP_OT_vta_timeline_scroll'].invoke(NS(),self.ctx,event)

    def test_wheel_scroll_stays_on_screen_across_redraw_selection_and_scene_roundtrip(self):
        for _ in range(11):self.assertEqual(self.scroll(self.event('WHEELDOWNMOUSE')),{'FINISHED'})
        before=self.env['_rows'](self.ctx,self.view,0,self.env['_metrics'](self.ctx))
        self.assertEqual(before[0].character_id,'5')
        for _ in range(20):
            rows=self.env['_rows'](self.ctx,self.view,20,self.env['_metrics'](self.ctx))
            self.assertEqual(rows,before)
        self.ctx.scope='B'
        self.assertIsNone(self.env['_metrics'](self.ctx).scroll_rows)
        self.ctx.scope='A'
        self.assertEqual(self.env['_metrics'](self.ctx).scroll_rows,11)

    def test_rename_create_delete_preserve_anchor_and_delete_anchor_clamps(self):
        for _ in range(10):self.scroll(self.event('WHEELDOWNMOUSE'))
        anchor=self.env['_row_scroll']['A'][:2]
        self.ui._timeline=replace(self.view,characters=tuple(reversed(self.view.characters)))
        index=self.env['_metrics'](self.ctx).scroll_rows
        self.assertEqual(channel_items(self.ui._timeline,[c.character_id for c in self.view.characters])[index],anchor)
        self.ui._timeline=replace(self.view,characters=(TimelineCharacter('new','AAA'),)+self.view.characters)
        self.assertEqual(self.env['_metrics'](self.ctx).scroll_rows,12)
        self.ui._timeline=replace(self.view,characters=tuple(c for c in self.view.characters if c.character_id!='0'))
        self.assertEqual(self.env['_metrics'](self.ctx).scroll_rows,8)
        self.ui._timeline=replace(self.view,characters=self.view.characters[:2])
        rows=self.env['_rows'](self.ctx,self.ui._timeline,0,self.env['_metrics'](self.ctx))
        self.assertEqual(len(rows),4)

    def test_trackpad_vertical_and_horizontal_and_native_bottom(self):
        self.assertEqual(self.scroll(self.event('TRACKPADPAN',mouse_x=5,mouse_prev_x=5,mouse_y=80,mouse_prev_y=40)),{'FINISHED'})
        state=dict(self.env['_row_scroll'])
        self.assertEqual(self.scroll(self.event('TRACKPADPAN',mouse_x=80,mouse_prev_x=0,mouse_y=5,mouse_prev_y=0)),{'PASS_THROUGH'})
        self.assertEqual(self.scroll(self.event('WHEELDOWNMOUSE',y=10)),{'PASS_THROUGH'})
        self.assertEqual(state,self.env['_row_scroll'])

    def test_trackpad_small_deltas_accumulate_instead_of_skipping_rows(self):
        event=self.event('TRACKPADPAN',mouse_x=0,mouse_prev_x=0,mouse_y=5,mouse_prev_y=0)
        for _ in range(5):self.assertEqual(self.scroll(event),{'FINISHED'})
        self.assertEqual(self.env['_row_scroll'],{})
        self.scroll(event)
        self.assertEqual(self.env['_row_scroll']['A'][2],1)

    def test_native_bottom_b_a_alt_a_delete_pass_through_even_with_review_selection(self):
        for scale in (1,2,3):
            self.ctx.preferences=NS(system=NS(ui_scale=scale))
            for action in ('BOX','ALL','NONE','DELETE'):
                self.assertEqual(self.env['BLENDCAP_OT_vta_timeline_keys'].invoke(
                    NS(action=action),self.ctx,self.event('DEL',y=41*scale)),{'PASS_THROUGH'})

    def test_menu_off_and_on_and_mode_commands_removed(self):
        package=ModuleType('_marker_regression');package.__path__=[]
        session=ModuleType('_marker_regression.vta_scene_session');session.enabled=lambda ws:ws.active
        session.warning=lambda c:''
        self.env['__package__']='_marker_regression'
        callbacks('blendcap/vta_workspace.py',('_draw_header',),self.env)
        callbacks('blendcap/vta_review_ui.py',('draw_scene_workflow',),self.env)
        ctx=NS(area=NS(type='DOPESHEET_EDITOR',ui_type='DOPESHEET'),workspace=NS(active=False))
        layout=mock.Mock()
        with mock.patch.dict(sys.modules,{'_marker_regression':package,'_marker_regression.vta_scene_session':session}):
            self.env['_draw_header'](NS(layout=layout),ctx);layout.menu.assert_not_called()
            ctx.workspace.active=True
            self.env['_draw_header'](NS(layout=layout),ctx);layout.menu.assert_called_once()
            layout.reset_mock();self.env['draw_scene_workflow'](layout,ctx)
        self.assertEqual([c.args[0] for c in layout.operator.call_args_list],
                         ['blendcap.vta_load_scene','blendcap.vta_new_scene'])

    def test_draw_clip_restored_even_when_view_is_unavailable(self):
        callbacks('blendcap/vta_workspace.py',('_draw_window',),self.env)
        self.env.update(_scope=lambda c:True,_state=lambda c:(None,(),None,0,0,None),
                        _fill=mock.Mock(),bpy=NS(context=self.ctx))
        self.ctx.region.width=1000
        gpu=NS(shader=NS(from_builtin=mock.Mock()),state=NS(scissor_get=lambda:(7,11,1000,240),scissor_set=mock.Mock()))
        with mock.patch.dict(sys.modules,{'gpu':gpu,'gpu_extras.batch':NS(batch_for_shader=mock.Mock())}):
            self.env['_draw_window']()
        self.assertEqual(gpu.state.scissor_set.call_args_list,[mock.call(7,53,1000,198),mock.call(7,11,1000,240)])

    def test_registration_leaves_native_m_f2_g_and_modified_wheel_unbound(self):
        bindings=[]
        def add(identifier,button,value,**mods):
            item=NS(idname=identifier,type=button,properties=NS(),**mods)
            bindings.append(item);return item
        keymap=NS(keymap_items=NS(new=add))
        class Scene:pass
        self.env.update(_window_handler=object(),_channel_handler=object(),_blank_handler=object(),
            _header_registered=True,_preview_header_registered=True,_camera_header_registered=True,
            _keymaps=[],_clear_channel_presentation=mock.Mock(),_timeline_text_size_changed=mock.Mock(),
            BLENDCAP_OT_vta_timeline_select=NS(bl_idname='blendcap.vta_timeline_select'),
            BLENDCAP_OT_vta_timeline_hover=NS(bl_idname='blendcap.vta_timeline_hover'),
            bpy=NS(types=NS(Scene=Scene),props=NS(IntProperty=lambda **kw:None),
                app=NS(handlers=NS(load_post=[])),context=NS(window_manager=NS(
                    keyconfigs=NS(addon=NS(keymaps=NS(new=lambda **kw:keymap)))))))
        callbacks('blendcap/vta_workspace.py',('register',),self.env)
        self.env['register']()
        self.assertFalse({'M','F2','G'} & {b.type for b in bindings})
        self.assertTrue({'B','A','X','DEL'} <= {b.type for b in bindings})
        self.assertEqual({b.type for b in bindings if b.idname=='blendcap.vta_timeline_scroll'},
                         {'WHEELUPMOUSE','WHEELDOWNMOUSE','TRACKPADPAN'})
        self.assertFalse(any(getattr(b,'shift',False) or getattr(b,'ctrl',False)
                             for b in bindings if b.idname=='blendcap.vta_timeline_scroll'))


class RefreshTests(unittest.TestCase):
    def setUp(self):
        # Load pure public adapters under a package without importing the host's
        # bpy-dependent registration. No Blender process or engine is involved.
        self.package='_refresh_regression_host'
        parent=ModuleType(self.package);parent.__path__=[str(ROOT)]
        child=ModuleType(self.package+'.blendcap');child.__path__=[str(ROOT/'blendcap')]
        session=ModuleType(child.__name__+'.vta_scene_session')
        session.owned=lambda scene:True;session.source_bound=lambda scene:True
        timing=ModuleType(child.__name__+'.vta_scene_timing');timing.owns_scene=lambda *a:True
        self.modules=mock.patch.dict(sys.modules,{parent.__name__:parent,child.__name__:child,
            session.__name__:session,timing.__name__:timing})
        self.modules.start();self.addCleanup(self.modules.stop)
        presentation=importlib.import_module(child.__name__+'.vta_presentation')
        self.membership=importlib.import_module(child.__name__+'.vta_scene_characters')
        self.backend_type=importlib.import_module(parent.__name__+'.blendcap_vta_engine_review').EngineReviewBackend
        class Scene(dict):
            objects=()
            frame_current=1
            def as_pointer(self):return id(self)
        self.a=Scene(vta_project_id='p',vta_source_id='source')
        self.b=Scene(vta_project_id='p',vta_source_id='source')
        self.membership._write(self.a,{'a'});self.membership._write(self.b,{'b'})
        self.characters=[CharacterRow('a','A',None),CharacterRow('b','B',None)]
        self.location=ReviewLocation(ROOT/'unused-diagnostic-root',ROOT/'unused-video',0,1)
        self.backend=self.backend_type(NS(timeout=1))
        self.backend._fetch=mock.Mock(side_effect=lambda loc:self.snapshot())
        self.env=dict(__package__=child.__name__,bpy=NS(types=NS(Scene=Scene)),
            SelectionState=SelectionState,ReviewKeySelection=ReviewKeySelection,
            _scene_sessions={},_active_review_scene=None,_selection=SelectionState(),
            _review_key_selection=ReviewKeySelection(),_character_selections={},_repair_selections={},
            _last_assignment_target=None,_timeline=None,_view=None,_location=None,
            _project_signature=None,_cache_signature=None,_timeline_identity=None,_presentation_scene=None,
            _scene_character_ids=None,_display=presentation.Presentation(),_display_backend=None,
            _requires_owned_scene=lambda c:True,loaded_context_matches=lambda *a:True,
            _source_files_changed=lambda *a:False,
            _context_for_main_scene=lambda c:c,_get_backend=lambda *a,**kw:self.backend,
            pointer=presentation.pointer,at_frame=presentation.at_frame,
            _rebuild_enum_items=mock.Mock(),_repeat_character_id=mock.Mock(),_queue_presentation=mock.Mock(),
            _character_scroll={},vta_invalidation=NS(request_redraw=mock.Mock()),
            user_message=str,VtaReviewUiError=VtaReviewUiError)
        self.env['cached_scene_source']=lambda c:('p',{'source_id':'source'},self.location,None)
        callbacks('blendcap/vta_review_ui.py',('_activate_review_scene','refresh','_accept_mutated_review'),self.env)
        self.context=NS(scene=self.a,workspace=NS(),window_manager=NS())

    def snapshot(self):
        rows=tuple(self.characters)
        return NS(project_id='p',source={'source_id':'source'},characters=rows,
            value={'analysis_revision_id':None},frames={0:None},coverage=(0,3),input_stamp=(None,None,None),
            timeline=TimelineView('source',tuple(TimelineCharacter(c.character_id,c.display_name) for c in rows),()),
            frame=lambda loc,**kw:ReviewView('r','source',0,100,100,'analyzed-empty','empty',(),rows))

    def refresh(self,scene):
        self.context.scene=scene
        self.assertTrue(self.env['refresh'](self.context,check_disk=True),self.env.get('_status'))

    def names(self):return [c.display_name for c in self.env['_timeline'].characters]

    def accept(self):
        # Production must invalidate its memoized projections even when the
        # transport observation was already adopted during mutation completion.
        fresh=self.snapshot();self.backend.observation.__dict__.update(fresh.__dict__)
        self.env['_accept_mutated_review'](self.context,self.backend.query(self.location))

    def test_create_rename_delete_immediate_two_scenes_with_same_cached_backend(self):
        self.refresh(self.a);self.assertEqual(self.names(),['A'])
        self.env['_selection'].character_id='a'
        self.characters.append(CharacterRow('new-a','New A',None))
        self.membership.add(self.a,self.backend.observation,'new-a')
        self.accept();self.assertEqual(self.names(),['A','New A'])
        self.assertEqual(self.env['_selection'].character_id,'a','Management creation must not retarget assignment')
        self.refresh(self.b);self.assertEqual(self.names(),['B'])
        self.characters.append(CharacterRow('new-b','New B',None))
        self.membership.add(self.b,self.backend.observation,'new-b')
        self.accept();self.assertEqual(self.names(),['B','New B'])
        self.characters[-1]=replace(self.characters[-1],display_name='Renamed B')
        self.accept();self.assertEqual(self.names(),['B','Renamed B'])
        self.characters=[c for c in self.characters if c.character_id!='b']
        self.accept();self.assertEqual(self.names(),['Renamed B'])
        self.refresh(self.a);self.assertEqual(self.names(),['A','New A'])
        self.assertEqual({c.character_id for c in self.env['_view'].characters},{'a','new-a'})
        self.backend._fetch.assert_called_once()

    def test_cached_transport_rebinds_mutation_to_each_callers_current_scene(self):
        callbacks('blendcap/vta_review_ui.py',('_get_backend',),self.env)
        client=NS(endpoint=NS(launcher=Path('engine/vta_engine.py')))
        self.env.update(_backend=None,_backend_location=None,_backend_root=None,
            _configured_core_location=lambda c:'same engine',_engine_client=mock.Mock(return_value=client),
            _domain_mutation=mock.Mock(return_value={}))
        contexts=[NS(scene=s) for s in (self.a,self.b,self.a,self.b)]
        backend=self.env['_get_backend'](contexts[0])
        for context in contexts:
            self.assertIs(self.env['_get_backend'](context),backend)
            backend.mutate('character-create',{},NS())
            self.assertIs(self.env['_domain_mutation'].call_args.args[0],context)
        self.env['_engine_client'].assert_called_once()


if __name__=='__main__':unittest.main()
