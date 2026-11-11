"""Paint-level viewport contracts absent from the operation-time reveal tests."""
from dataclasses import replace
from types import SimpleNamespace as NS
from types import ModuleType
from pathlib import Path
import subprocess,sys
import unittest
from unittest import mock
from tests.test_blendcap_vta_ui_followup import HostFixture
from blendcap_vta_review import CharacterRow


class ViewportRootCauseTests(HostFixture):
    def test_overflow_wheel_visits_every_painted_parent_with_expanded_depth_children(self):
        self.refresh(self.a);self.painted_names()
        members=self.membership.stored(self.a)
        for i in range(30):
            identity=f'extra-{i}'
            self.characters.append(CharacterRow(identity,f'Extra {i:02}',None))
            members.add(identity)
        self.membership._write(self.a,members)
        self.revision+=1;self.bridge._refresh();self.drain()
        seen=set(self.painted_names())
        self.context.area=self.timeline;self.context.region=self.window_region
        for direction in ('WHEELUPMOUSE','WHEELDOWNMOUSE'):
            for _ in range(70):
                self.ws.BLENDCAP_OT_vta_timeline_scroll.invoke(NS(),self.context,
                    NS(type=direction,mouse_region_y=100))
                seen.update(self.painted_names())
        self.assertEqual(self.ws._row_scroll[self.ws._expansion_key(self.context)][2],
                         self.ws._scroll_range(self.context,self.ui._timeline,self.ws._metrics(self.context))[2])
        for _ in range(80):
            self.ws.BLENDCAP_OT_vta_timeline_scroll.invoke(NS(),self.context,
                NS(type='WHEELUPMOUSE',mouse_region_y=100))
        self.assertEqual(self.ws._row_scroll[self.ws._expansion_key(self.context)][2],0)
        self.assertTrue({f'Extra {i:02}' for i in range(30)}<=seen)
        rows=self.ws._rows(self.context,self.ui._timeline,0,self.ws._metrics(self.context))
        for a,b in zip(rows,rows[1:]):
            if b.kind=='depth':self.assertEqual(a.character_id,b.character_id)

    def test_right_edge_elevator_moves_both_regions_down_and_back_to_first_row(self):
        self.refresh(self.a);self.painted_names()
        members=self.membership.stored(self.a)
        for i in range(8):
            identity=f'extra-{i}'
            self.characters.append(CharacterRow(identity,f'Extra {i}',None));members.add(identity)
        self.membership._write(self.a,members)
        self.revision+=1;self.bridge._refresh();self.drain()
        ctx=self.context;ctx.area=self.timeline;ctx.region=self.window_region
        metrics=self.ws._metrics(ctx);bar=self.ws._scrollbar(ctx,self.ui._timeline,metrics)
        self.assertIsNotNone(bar)
        self.assertEqual(bar[1],ctx.region.width-2*metrics.ui_scale)
        window_rows=self.ws._rows(ctx,self.ui._timeline,0,metrics)
        ctx.region=self.channel_region
        self.assertIsNone(self.ws._scrollbar(ctx,self.ui._timeline,self.ws._metrics(ctx)))
        self.assertEqual(self.ws._rows(ctx,self.ui._timeline,0,self.ws._metrics(ctx)),window_rows)
        ctx.region=self.window_region
        event=lambda y,value='PRESS':NS(type='LEFTMOUSE',value=value,mouse_region_x=(bar[0]+bar[1])/2,
            mouse_region_y=y,mouse_y=y,shift=False,ctrl=False)
        op=self.ws.BLENDCAP_OT_vta_timeline_select()
        self.assertEqual(op.invoke(ctx,event(bar[2]+2)),{'RUNNING_MODAL'})
        self.assertEqual(op.modal(ctx,event(bar[2]+2,'RELEASE')),{'FINISHED'})
        self.assertGreater(self.ws._row_scroll[self.ws._expansion_key(ctx)][2],0)
        window_rows=self.ws._rows(ctx,self.ui._timeline,0,self.ws._metrics(ctx))
        ctx.region=self.channel_region
        self.assertEqual(self.ws._rows(ctx,self.ui._timeline,0,self.ws._metrics(ctx)),window_rows)
        ctx.region=self.window_region
        op=self.ws.BLENDCAP_OT_vta_timeline_select()
        self.assertEqual(op.invoke(ctx,event(bar[3]-2)),{'RUNNING_MODAL'})
        self.assertEqual(op.modal(ctx,event(bar[3]-2,'RELEASE')),{'FINISHED'})
        self.assertEqual(self.ws._row_scroll[self.ws._expansion_key(ctx)][2],0)

    def test_wheel_in_either_region_scrolls_character_names_and_keys_together(self):
        self.refresh(self.a);self.painted_names()
        members=self.membership.stored(self.a)
        for i in range(8):
            identity=f'extra-{i}'
            self.characters.append(CharacterRow(identity,f'Extra {i}',None));members.add(identity)
        self.membership._write(self.a,members)
        self.revision+=1;self.bridge._refresh();self.drain()
        ctx=self.context;ctx.area=self.timeline
        ctx.region=self.window_region
        self.ws._set_row_scroll(ctx,self.ui._timeline,self.ws._metrics(ctx),0)
        for region in (self.window_region,self.channel_region):
            ctx.region=region
            self.assertEqual(self.ws.BLENDCAP_OT_vta_timeline_scroll.invoke(
                NS(),ctx,NS(type='WHEELDOWNMOUSE',mouse_region_y=100)),{'FINISHED'})
            window_rows=None
            for drawing_region in (self.window_region,self.channel_region):
                ctx.region=drawing_region
                rows=self.ws._rows(ctx,self.ui._timeline,0,self.ws._metrics(ctx))
                if window_rows is None:window_rows=rows
                else:self.assertEqual(rows,window_rows)
        self.assertEqual(self.ws._row_scroll[self.ws._expansion_key(ctx)][2],2)
        for region in (self.channel_region,self.window_region):
            ctx.region=region
            self.assertEqual(self.ws.BLENDCAP_OT_vta_timeline_scroll.invoke(
                NS(),ctx,NS(type='WHEELUPMOUSE',mouse_region_y=100)),{'FINISHED'})
        self.assertEqual(self.ws._row_scroll[self.ws._expansion_key(ctx)][2],0)


    def test_create_with_unusable_operation_geometry_is_revealed_when_paint_geometry_arrives(self):
        self.refresh(self.a);self.painted_names()
        self.window_region.height=self.channel_region.height=100
        self.create('tall ref')
        self.assertIn('tall ref',[c.display_name for c in self.ui._timeline.characters])
        self.assertNotIn('tall ref',self.painted_names())
        self.window_region.height=self.channel_region.height=194
        self.assertIn('tall ref',self.painted_names())

    def test_new_model_adoption_reveals_without_a_second_operator_callback(self):
        self.refresh(self.a);self.painted_names()
        self.characters.append(CharacterRow('adopted','tall ref',None))
        self.membership.add(self.a,self.backend.observation,'adopted')
        self.revision+=1;self.bridge._refresh();self.drain()
        self.assertIn('tall ref',[c.display_name for c in self.ui._timeline.characters])
        self.assertIn('tall ref',self.painted_names())

    def test_renamed_model_keeps_edited_parent_visible_without_changing_selection(self):
        self.refresh(self.a);self.ui._selection.character_id='a';self.painted_names()
        self.characters=[replace(c,display_name='ZZ referee') if c.character_id=='a' else c
                         for c in self.characters]
        self.revision+=1;self.bridge._refresh();self.drain()
        self.assertIn('ZZ referee',self.painted_names())
        self.assertEqual(self.ui._selection.character_id,'a')

    def test_delete_rebases_anchor_and_ordinary_redraw_or_scroll_does_not_reveal_again(self):
        self.refresh(self.a);self.painted_names();self.create('tall ref')
        self.assertIn('tall ref',self.painted_names())
        self.context.area=self.timeline;self.context.region=self.window_region
        self.ws.BLENDCAP_OT_vta_timeline_scroll.invoke(NS(),self.context,NS(type='WHEELUPMOUSE',mouse_region_y=100))
        anchor=dict(self.ws._row_scroll)
        for _ in range(10):self.painted_names()
        self.assertEqual(self.ws._row_scroll,anchor)
        self.characters=[c for c in self.characters if c.character_id!='new-owner-a']
        self.revision+=1;self.bridge._refresh();self.drain()
        self.assertNotIn('tall ref',self.painted_names())
        for value in self.ws._row_scroll.values():self.assertNotEqual(value[0],'new-owner-a')

    def test_histories_identify_first_marker_slot_regression(self):
        root=Path(__file__).resolve().parents[1]
        from blendcap_vta_timeline import TimelineView,TimelineCharacter
        view=TimelineView('s',tuple(TimelineCharacter(i,i) for i in ('black','no','ref')),())
        painted={}
        for sha in ('ea4a6b2','9c9115f','8bb48cb','b3920d5','a1ac211'):
            module=ModuleType('historical_'+sha);module.__package__=''
            with mock.patch.dict(sys.modules,{module.__name__:module}):
                source=subprocess.check_output(['git','show',sha+':blendcap_vta_workspace.py'],cwd=root)
                exec(compile(source,sha+'/blendcap_vta_workspace.py','exec'),module.__dict__)
                rows=module.channel_rows(view,194,expanded=('black','no','ref'))
                painted[sha]=[(r.character_id,r.kind) for r in rows]
        self.assertIn(('ref','character'),painted['ea4a6b2'])
        self.assertNotIn(('ref','character'),painted['8bb48cb'])
        self.assertEqual([len(painted[c]) for c in painted],[5,5,4,3,2])


class BottomMarkerTests(HostFixture):
    def test_g_routes_to_local_preview_and_one_native_commit_on_release(self):
        ctx=self.context;ctx.area=self.timeline;ctx.region=self.window_region
        marker=self.a.timeline_markers.new('Grab',frame=10);marker.select=True
        event=lambda kind,x,value='PRESS':NS(type=kind,value=value,mouse_region_x=x,
            mouse_region_y=26,shift=False,ctrl=False)
        grab=self.markers.BLENDCAP_OT_vta_marker_drag()
        def begin(*args,**kw):
            grab.grab=kw['grab'];return grab.invoke(ctx,event('G',100))
        def commit(*args,**kw):
            self.assertEqual(args,('EXEC_DEFAULT',True))
            op=self.markers.BLENDCAP_OT_vta_marker_edit();op.action=kw['action'];op.frames=kw['frames']
            return op.execute(ctx)
        edit=mock.Mock(side_effect=commit)
        self.bpy.ops=NS(blendcap=NS(vta_marker_drag=begin,vta_marker_edit=edit))
        self.assertEqual(self.ws.BLENDCAP_OT_vta_timeline_keys.invoke(NS(action='MOVE'),ctx,event('G',100)),{'FINISHED'})
        self.assertEqual(grab.modal(ctx,event('MOUSEMOVE',140)),{'RUNNING_MODAL'})
        self.assertEqual(marker.frame,10);edit.assert_not_called()
        self.assertEqual(grab.modal(ctx,event('LEFTMOUSE',140,'RELEASE')),{'FINISHED'})
        self.assertEqual(marker.frame,14);edit.assert_called_once()

    def test_double_click_renames_same_native_marker_through_the_f2_dialog(self):
        ctx=self.context;ctx.area=self.timeline;ctx.region=self.window_region
        marker=self.a.timeline_markers.new('Original',frame=10)
        font=NS(size=lambda *a:None,dimensions=lambda f,s:(len(s)*6,12))
        def edit(*args,**kw):
            op=self.markers.BLENDCAP_OT_vta_marker_edit();op.action=kw['action']
            return op.invoke(ctx,NS())
        self.bpy.ops=NS(blendcap=NS(vta_marker_edit=mock.Mock(side_effect=edit)))
        event=NS(type='LEFTMOUSE',value='DOUBLE_CLICK',mouse_region_x=100,mouse_region_y=26)
        with mock.patch.dict(sys.modules,{'blf':font}):
            self.assertEqual(self.ws.BLENDCAP_OT_vta_timeline_select.invoke(NS(),ctx,event),{'FINISHED'})
        self.assertTrue(marker.select)
        op=self.wm.invoke_props_dialog.call_args.args[0];op.marker_name='Double-click renamed'
        self.assertEqual(op.execute(ctx),{'FINISHED'})
        self.assertEqual(marker.name,'Double-click renamed')

    def test_native_markers_map_receives_bottom_lane_bindings_but_normal_mode_is_unaffected(self):
        maps={}
        def keymap(name,**kw):
            entries=maps.setdefault(name,[])
            def add(identifier,button,value,**mods):
                item=NS(idname=identifier,type=button,value=value,properties=NS(),**mods)
                entries.append(item);return item
            return NS(keymap_items=NS(new=add))
        self.wm.keyconfigs=NS(addon=NS(keymaps=NS(new=keymap)))
        self.bpy.app.handlers.load_post=[]
        self.ws._window_handler=self.ws._channel_handler=self.ws._blank_handler=object()
        self.ws._header_registered=self.ws._preview_header_registered=self.ws._camera_header_registered=True
        self.ws.register()
        self.assertIn('Markers',maps)
        self.assertTrue(any(i.type=='LEFTMOUSE' and i.value=='DOUBLE_CLICK' for i in maps['Markers']))
        actions={(i.type,getattr(i.properties,'action',None)) for i in maps['Markers']}
        self.assertTrue({('M','ADD'),('F2','RENAME'),('G','MOVE'),('DEL','DELETE'),('X','DELETE')}<=actions)
        self.workspace.blendcap_vta_mode_enabled=False
        self.context.area=self.timeline;self.context.region=self.window_region
        self.assertFalse(self.ws.BLENDCAP_OT_vta_timeline_select.poll(self.context))
        self.assertFalse(self.ws.BLENDCAP_OT_vta_timeline_keys.poll(self.context))

    def test_marker_lane_stays_immediately_above_scrollbar_at_all_scales(self):
        from blendcap_vta_workspace import TimelineMetrics,channel_rows
        ctx=self.context;ctx.area=self.timeline;ctx.region=self.window_region
        self.a.timeline_markers.new('Fixed bottom',frame=10)
        self.refresh(self.a)
        for scale in (1,1.5,2,3):
            ctx.preferences.system.ui_scale=scale
            for first in (0,1,4):
                fills=[]
                font=NS(position=lambda *a:None,size=lambda *a:None,color=lambda *a:None,draw=lambda *a:None)
                with mock.patch.dict(sys.modules,{'blf':font}):
                    self.markers.draw(ctx,scale,None,None,lambda *a:fills.append(a[2:6]),lambda *a:None)
                self.assertEqual(fills,[])
                metrics=TimelineMetrics(scale,scroll_rows=first)
                for row in channel_rows(self.ui._timeline,400*scale,metrics=metrics,expanded=('a','spare')):
                    self.assertGreaterEqual(row.y-metrics.row_height/2,44*scale)


if __name__=='__main__':unittest.main()
