import importlib
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from blendcap_vta_tracking_hints import TrackingHints,KeyHint,marker_positions,hit_marker


def outcome(cid='grey', stops=((52,'forward','competing-candidates'),)):
    return NS(character_id=cid, update=NS(ambiguities=tuple(
        NS(frame=f,direction=d,reason=r) for f,d,r in stops)))


class HintStoreTests(unittest.TestCase):
    def setUp(self): self.hints=TrackingHints()

    def test_offset_direction_character_and_duplicate_filter(self):
        self.hints.remember('scene-a',1,[outcome(stops=((52,'forward','competing-candidates'),
            (52,'backward','competing-candidates'))),outcome('white',((82,'backward','competing-candidates'),))],1)
        hints=self.hints.current('scene-a',1,{'grey','white'})
        self.assertEqual([(h.character_id,h.frame,h.direction) for h in hints],
                         [('grey',53,'forward'),('white',83,'backward')])
        self.assertFalse(self.hints.current('scene-b',1,{'grey'}))
        self.assertEqual(len(self.hints.current('scene-a',1,{'white'})),1)

    def test_material_change_discards_even_if_old_stamp_later_returns(self):
        self.hints.remember('a',1,[outcome()],1)
        self.assertFalse(self.hints.current('a',2,{'grey'}))
        self.assertFalse(self.hints.current('a',1,{'grey'}))

    def test_technical_empty_failed_and_hard_boundary_never_suggest(self):
        for reason in ('missing-analysis','no-candidates','hard-boundary','error','cancelled','reserved'):
            self.hints.remember('a',1,[outcome(stops=((52,'forward',reason),)),NS(update=None)],1)
            self.assertFalse(self.hints.current('a',1,{'grey'}))

    def test_new_run_replaces_and_reload_clears(self):
        self.hints.remember('a',1,[outcome()],1)
        self.hints.remember('a',1,[outcome(stops=((61,'forward','competing-candidates'),))],1)
        self.assertEqual(self.hints.current('a',1,{'grey'})[0].frame,62)
        self.hints.clear();self.assertFalse(self.hints.current('a',1,{'grey'}))

    def test_bounded_session_memory(self):
        for i in range(100):self.hints.remember(i,1,[outcome()],1)
        self.assertEqual(len(self.hints.entries),32)

    def test_dismiss_only_this_hint_no_authority_or_frame_changes(self):
        self.hints.remember('a',1,[outcome(),outcome('white')],1)
        self.assertTrue(self.hints.dismiss('a','grey',53))
        self.assertFalse(self.hints.dismiss('a','grey',53))
        self.assertEqual([h.character_id for h in self.hints.current('a',1,{'grey','white'})],['white'])

    def test_marker_coordinates_share_character_row_not_key_collection(self):
        hint=KeyHint('grey',53,'forward','competing-candidates')
        markers=marker_positions((hint,),{'grey':120},lambda f:f*2,500,9)
        self.assertEqual(markers,((hint,106,120),))
        self.assertEqual(hit_marker(markers,(106,120),9),hint)
        self.assertIsNone(hit_marker(markers,(106,140),9))
        self.assertFalse(marker_positions((hint,),{'white':120},lambda f:f,500,9))
        self.assertFalse(marker_positions((hint,),{'grey':120},lambda f:999,500,9))


class HintBlenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_scene_binding import SceneBindingTests
        SceneBindingTests.setUpClass();cls.fixture=SceneBindingTests;cls.ui=SceneBindingTests.ui

    def test_jump_focus_and_stale_cancellation_no_authoritative_write(self):
        t=self.fixture();t.setUp();self.addCleanup(t.doCleanups)
        scene=t.owned();t.session.load(__import__('bpy').context,scene)
        bpy=__import__('bpy');ui=self.ui
        from blendcap_vta_review import CharacterRow
        ui._view=NS(characters=(CharacterRow('grey','Grey',None),))
        key=ui._feedback_key('tracking',bpy.context);stamp=(ui._project_signature,ui._cache_signature)
        ui._tracking_hints.remember(key,stamp,[outcome()],1)
        op=NS(character_id='grey',frame=53)
        with mock.patch.object(ui,'_presentation_current',return_value=True),mock.patch.object(ui,'_analysis_locked',return_value=False),\
                mock.patch.object(ui,'refresh',return_value=True) as refresh,mock.patch.object(ui,'select_timeline_character',return_value=True) as select:
            self.assertEqual(ui.BLENDCAP_OT_vta_goto_tracking_hint.execute(op,bpy.context),{'FINISHED'})
            self.assertEqual(scene.frame_current,53);select.assert_called_once_with(mock.ANY,'grey')
            self.assertEqual(sum(bool(c.kwargs.get('force')) for c in refresh.call_args_list),1)
            self.assertLessEqual(refresh.call_count,3,'Canonical validation plus cached frame refresh/handler only')
            # A successful authoritative edit/derived invalidation changes the cached signature.
            ui._project_signature=('new',1)
            self.assertEqual(ui.BLENDCAP_OT_vta_goto_tracking_hint.execute(op,bpy.context),{'CANCELLED'})
            self.assertFalse(ui.tracking_hints(bpy.context))

    def test_polls_draws_no_load_scene_gate_and_reload_reset(self):
        t=self.fixture();t.setUp();self.addCleanup(t.doCleanups)
        bpy=__import__('bpy');ui=self.ui;scene=t.owned();t.session.load(bpy.context,scene)
        from blendcap_vta_review import CharacterRow
        ui._view=NS(characters=(CharacterRow('grey','Grey',None),))
        key=ui._feedback_key('tracking',bpy.context)
        ui._tracking_hints.remember(key,(ui._project_signature,ui._cache_signature),[outcome()],1)
        with mock.patch.object(ui,'_presentation_current',return_value=True),mock.patch.object(ui,'_get_backend',side_effect=AssertionError('poll read')):
            for _ in range(20):self.assertTrue(ui.tracking_hints(bpy.context))
            other=bpy.data.scenes.new('unowned');self.addCleanup(bpy.data.scenes.remove,other)
            bpy.context.window.scene=other
            self.assertFalse(ui.tracking_hints(bpy.context))
        ui._reset_scene_presentation();self.assertFalse(ui._tracking_hints.entries)

    def test_dismiss_operator_does_not_jump_or_load_project(self):
        t=self.fixture();t.setUp();self.addCleanup(t.doCleanups)
        bpy=__import__('bpy');ui=self.ui;scene=t.owned();t.session.load(bpy.context,scene)
        from blendcap_vta_review import CharacterRow
        ui._view=NS(characters=(CharacterRow('grey','Grey',None),))
        key=ui._feedback_key('tracking',bpy.context)
        ui._tracking_hints.remember(key,(ui._project_signature,ui._cache_signature),[outcome()],1)
        original=scene.frame_current
        with mock.patch.object(ui,'_presentation_current',return_value=True),mock.patch.object(ui,'_analysis_locked',return_value=False),\
                mock.patch.object(ui,'refresh',side_effect=AssertionError('dismiss loaded project')):
            result=ui.BLENDCAP_OT_vta_goto_tracking_hint.execute(NS(character_id='grey',frame=53,dismiss=True),bpy.context)
            self.assertEqual(result,{'FINISHED'});self.assertEqual(scene.frame_current,original)
            self.assertFalse(ui.tracking_hints(bpy.context))

    def test_timeline_icon_hit_dispatch_tooltip_and_human_key_priority(self):
        ws=importlib.import_module(self.ui.__package__+'.vta_workspace')
        from blendcap_vta_workspace import TimelineMetrics,character_lanes
        view=NS(characters=(NS(character_id='grey'),),keys=(),derived=(),repair_ranges=())
        metrics=TimelineMetrics();y=dict(character_lanes(view,300,metrics=metrics))['grey']
        hint=KeyHint('grey',53,'forward','competing-candidates')
        context=NS(region=NS(type='WINDOW',width=500,height=300,
            view2d=NS(view_to_region=lambda f,y,**kw:(f*4,y))))
        dispatch=mock.Mock(return_value={'FINISHED'})
        with mock.patch.object(ws,'_ui',return_value=NS(tracking_hints=lambda _:(hint,))),\
                mock.patch.object(ws,'_state',return_value=(view,(),None,1,0,'grey')),\
                mock.patch.object(ws,'_metrics',return_value=metrics),\
                mock.patch.object(ws,'bpy',NS(ops=NS(blendcap=NS(vta_goto_tracking_hint=dispatch)))):
            self.assertIn('Non-authoritative',ws.timeline_tooltip(context,(212,y)))
            for button in ('LEFTMOUSE','RIGHTMOUSE'):
                event=NS(mouse_region_x=212,mouse_region_y=y,type=button)
                self.assertEqual(ws.BLENDCAP_OT_vta_timeline_select.invoke(NS(),context,event),{'FINISHED'})
                dispatch.assert_called_with(character_id='grey',frame=53,dismiss=button=='RIGHTMOUSE')
            ui=NS(tracking_hints=lambda _:(hint,),select_timeline_key=mock.Mock(return_value=True))
            with mock.patch.object(ws,'_ui',return_value=ui),mock.patch.object(ws,'hit_review_key',return_value='real-key'):
                self.assertEqual(ws.BLENDCAP_OT_vta_timeline_select.invoke(NS(),context,
                    NS(mouse_region_x=212,mouse_region_y=y,type='LEFTMOUSE',ctrl=False)),{'FINISHED'})
                ui.select_timeline_key.assert_called_once()
            blf=mock.Mock()
            with mock.patch.dict('sys.modules',{'blf':blf}),mock.patch.object(ws,'_line') as line:
                ws._draw_tracking_hint_markers(context,view,0,metrics,None,None)
                self.assertEqual(line.call_args.args[2],((212,y+8),(220,y-8),(204,y-8),(212,y+8)))
                blf.draw.assert_called_once_with(0,'?')

    def test_hints_are_dopesheet_only_not_sidebar_or_authoritative_keys(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        ui_source=(root/'blendcap/vta_review_ui.py').read_text()
        ws_source=(root/'blendcap/vta_workspace.py').read_text()
        self.assertNotIn('draw_tracking_hint(',ui_source+ws_source)
        self.assertIn('_draw_tracking_hint_markers(context, view, first',ws_source)
