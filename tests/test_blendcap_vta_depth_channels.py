"""Per-Character hierarchy, native ownership and review-safe interactions."""
from types import SimpleNamespace as NS
import unittest
from unittest import mock
import bpy
from blendcap_vta_timeline import TimelineCharacter,TimelineKey,TimelineDerived,TimelineView
from blendcap_vta_workspace import (TimelineMetrics,channel_rows,character_lanes,
    hit_review_key,hit_character,characters_in_box,review_keys_in_box)


class DepthChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_blendcap_vta_depth_tool import DepthToolTests
        DepthToolTests.setUpClass()
        for name in ('tool','keys','ui','session','workspace_ui'):setattr(cls,name,getattr(DepthToolTests,name))
        cls.ws=cls.workspace_ui
        cls.base=DepthToolTests

    def setUp(self):
        self.base.setUp(self)
        self.ws._collapsed_depth.clear()
        self.addCleanup(self.ws._collapsed_depth.clear)
        self.scene=bpy.context.scene
        self.tags={k:self.scene.get(k) for k in ('vta_project_id','vta_source_id')}
        self.scene['vta_project_id']='depth project';self.scene['vta_source_id']='depth source'
        self.addCleanup(self.restore_tags)
        self.view=TimelineView('depth source',(TimelineCharacter('a','Same name'),TimelineCharacter('b','Same name')),
            (TimelineKey('authority-a','a',20,'visible','oa'),TimelineKey('authority-b','b',35,'occluded',None)),
            (TimelineDerived('b',36,'visible'),))
        self.metrics=TimelineMetrics()
        self.ctx=NS(scene=self.scene,workspace=bpy.context.workspace,
            area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D'),
            region=NS(type='WINDOW',height=500,width=1000,view2d=NS(
                view_to_region=lambda frame,y,clip=False:(frame*10,y),
                region_to_view=lambda x,y:(x/10,y))))

    rig=lambda self,*args:self.base.rig(self,*args)
    cleanup=lambda self:self.base.cleanup(self)

    def restore_tags(self):
        for key,value in self.tags.items():
            if value is None:self.scene.pop(key,None)
            else:self.scene[key]=value

    def rows(self):return self.ws._rows(self.ctx,self.view,0,self.metrics)
    def depths(self):return {r.character_id:r.y for r in self.rows() if r.kind=='depth'}
    def markers(self):return self.ws.depth_key_markers(self.ctx,self.depths(),1)
    def event(self,kind,x,y):return NS(type=kind,mouse_region_x=x,mouse_region_y=y,ctrl=False,shift=False)
    def click(self,event):
        with mock.patch.object(self.ws,'_state',return_value=(self.view,(),None,100,0,'a')), \
                mock.patch.object(self.ws,'_metrics',return_value=self.metrics):
            return self.ws.BLENDCAP_OT_vta_timeline_select.invoke(NS(),self.ctx,event)

    def test_each_character_has_own_child_even_same_display_name(self):
        rows=self.rows()
        self.assertEqual([(r.character_id,r.kind) for r in rows],
                         [('a','character'),('a','depth'),('b','character'),('b','depth')])
        self.assertEqual(len({r.y for r in rows}),4)

    def test_location_keys_aggregate_xyz_and_never_leak_between_characters(self):
        self.a.keyframe_insert('location',frame=20)
        self.assertEqual([(m[0],m[1]) for m in self.markers()],[('a',20.)])
        self.b.keyframe_insert('location',frame=35)
        self.b.keyframe_insert('location',frame=20)
        markers=self.markers()
        self.assertEqual([(m[0],m[1]) for m in markers],[('a',20.),('b',20.),('b',35.)])
        for cid,frame,x,y in markers:self.assertEqual(y,self.depths()[cid]);self.assertEqual(x,frame*10)

    def test_auto_and_manual_keys_reflect_without_querying_project_state(self):
        bpy.context.scene.tool_settings.use_keyframe_insert_auto=True
        bpy.context.scene.tool_settings.auto_keying_mode='ADD_REPLACE_KEYS'
        bpy.context.scene.frame_set(20)
        self.tool.finish_key(bpy.context,self.a,True)
        self.b.keyframe_insert('location',frame=35)
        with mock.patch.object(self.ui,'_get_backend',side_effect=AssertionError('No project reads')):
            self.assertEqual([(m[0],m[1]) for m in self.markers()],[('a',20.),('b',35.)])

    def test_auto_off_no_depth_keys_until_manual_insert(self):
        self.scene.tool_settings.use_keyframe_insert_auto=False
        self.tool.apply_distance(self.a,self.camera,.3)
        self.tool.finish_key(bpy.context,self.a,True)
        self.assertEqual(self.markers(),())
        self.a.keyframe_insert('location',frame=22)
        self.assertEqual([(m[0],m[1]) for m in self.markers()],[('a',22.)])

    def test_external_move_delete_and_action_swap_reflect_immediately(self):
        self.a.keyframe_insert('location',frame=20)
        for curve in self.keys.location_curves(self.a):curve.keyframe_points[0].co.x=24.5;curve.update()
        self.assertEqual([(m[0],m[1]) for m in self.markers()],[('a',24.5)])
        action=self.a.animation_data.action
        self.a.animation_data.action=None
        self.assertEqual(self.markers(),())
        self.a.animation_data.action=action;self.a.animation_data.action_slot=action.slots[0]
        for curve in self.keys.location_curves(self.a):curve.keyframe_points.remove(curve.keyframe_points[0])
        self.assertEqual(self.markers(),())

    def test_disclosure_independent_and_stable_through_scrub_and_selection(self):
        before=self.view
        self.a.keyframe_insert('location',frame=20)
        self.ctx.region.type='CHANNELS'
        self.assertEqual(self.click(self.event('LEFTMOUSE',16,self.rows()[0].y)),{'FINISHED'})
        self.assertEqual([(r.character_id,r.kind) for r in self.rows()],
                         [('a','character'),('b','character'),('b','depth')])
        self.scene.frame_set(35);self.b.select_set(True);bpy.context.view_layer.objects.active=self.b
        self.assertNotIn('a',self.ws.expanded_characters(self.ctx,self.view))
        self.assertEqual(self.keys.keyed_frames(self.a),(20.,))
        self.assertIs(self.view,before)
        self.click(self.event('LEFTMOUSE',16,self.rows()[0].y))
        self.assertEqual(len(self.rows()),4)

    def test_collapse_and_keys_are_scene_local(self):
        self.a.keyframe_insert('location',frame=20)
        other=bpy.data.scenes.new('other depth Scene')
        try:
            other['vta_project_id']='depth project';other['vta_source_id']='depth source'
            self.ctx.region.type='CHANNELS'
            self.click(self.event('LEFTMOUSE',16,self.rows()[0].y))
            self.ctx.scene=other
            self.assertEqual(len(self.rows()),4)
            self.assertEqual(self.markers(),())
            self.ctx.scene=self.scene
            self.assertNotIn('a',self.ws.expanded_characters(self.ctx,self.view))
        finally:bpy.data.scenes.remove(other)

    def test_child_click_scrubs_exact_native_subframe_without_review_edits(self):
        self.a.keyframe_insert('location',frame=20.5)
        with mock.patch.object(self.ui,'select_timeline_key') as key, \
                mock.patch.object(self.ui,'select_timeline_character') as char, \
                mock.patch.object(self.ui,'select_repair_range') as repair:
            self.assertEqual(self.click(self.event('LEFTMOUSE',205,self.depths()['a'])),{'FINISHED'})
            self.assertEqual(self.scene.frame_current_final,20.5)
            key.assert_not_called();char.assert_not_called();repair.assert_not_called()
        self.assertEqual(self.view.keys[0].source_frame,20)

    def test_child_channel_and_right_click_do_not_select_review_or_hidden_native_keys(self):
        for region,button in [('CHANNELS','LEFTMOUSE'),('CHANNELS','RIGHTMOUSE'),('WINDOW','RIGHTMOUSE')]:
            self.ctx.region.type=region
            with mock.patch.object(self.ui,'select_timeline_character') as char, \
                    mock.patch.object(self.ui,'select_timeline_key') as key:
                self.assertEqual(self.click(self.event(button,100,self.depths()['a'])),{'FINISHED'})
                char.assert_not_called();key.assert_not_called()

    def test_authority_hits_and_box_selection_only_on_parent_rows(self):
        expanded=('a','b')
        lanes=dict(character_lanes(self.view,500,metrics=self.metrics,expanded=expanded))
        depth=self.depths()
        self.assertEqual(hit_review_key(self.view,self.ctx.region.view2d,500,(350,lanes['b']),expanded=expanded),'authority-b')
        self.assertIsNone(hit_review_key(self.view,self.ctx.region.view2d,500,(350,depth['b']),expanded=expanded))
        self.assertIsNone(hit_character(self.view,500,depth['a'],expanded=expanded))
        self.assertEqual(characters_in_box(self.view,500,(0,depth['a']-2),(900,depth['a']+2),expanded=expanded),())
        self.assertEqual(review_keys_in_box(self.view,self.ctx.region.view2d,1000,500,
            (0,depth['a']-2),(900,depth['a']+2),expanded=expanded),())

    def test_rows_scale_and_small_viewports_never_overlap_or_orphan(self):
        for scale in (.75,1,2):
            metrics=TimelineMetrics(scale,24)
            for height in (100,200,600):
                rows=channel_rows(self.view,height,1,metrics=metrics,expanded=('a','b'))
                if rows:self.assertEqual(rows[0].kind,'character')
                else:self.assertLess(height,metrics.top_padding+metrics.bottom_padding+metrics.row_height)
                for a,b in zip(rows,rows[1:]):self.assertAlmostEqual(a.y-b.y,metrics.row_height)

    def test_small_viewport_keeps_focused_character_and_its_child_visible(self):
        rows=channel_rows(self.view,194,1,expanded=('a','b'))  # Includes custom markers above the native event band.
        self.assertEqual([(r.character_id,r.kind) for r in rows],[('b','character'),('b','depth')])

    def test_file_load_clears_only_presentation_state(self):
        self.a.keyframe_insert('location',frame=20)
        self.ws._collapsed_depth[self.ws._expansion_key(self.ctx)]={'a'}
        self.ws._clear_channel_presentation()
        self.assertEqual(len(self.rows()),4)
        self.assertEqual(self.keys.keyed_frames(self.a),(20.,))

    def test_actual_draw_callbacks_keep_annotations_on_parents_and_depth_on_children(self):
        from dataclasses import replace
        from blendcap_vta_timeline import TimelineMotionInterval
        from blendcap_vta_tracking_hints import KeyHint
        import gpu,blf
        import gpu_extras.batch as batches
        self.view=replace(self.view,repair_ranges=(TimelineMotionInterval('b',30,40,'freeze'),))
        self.a.keyframe_insert('location',frame=20)
        self.b.keyframe_insert('location',frame=20)
        emitted=[];text=[];current=[None]
        shader=NS(bind=lambda:None,uniform_float=lambda name,value:current.__setitem__(0,tuple(value)))
        def batch(_shader,kind,data):
            return NS(draw=lambda _s:emitted.append((kind,tuple(data['pos']),current[0])))
        with mock.patch.object(self.ws.bpy,'context',self.ctx), \
                mock.patch.object(self.ws,'_scope',return_value=True), \
                mock.patch.object(self.ws,'_state',return_value=(self.view,(),None,0,0,'a')), \
                mock.patch.object(self.ws,'_metrics',return_value=self.metrics), \
                mock.patch.object(self.ws,'_row_style',return_value=((.2,.2,.2,1),(.9,.9,.9,1),None)), \
                mock.patch.object(self.ui,'selected_repair_range',return_value=None), \
                mock.patch.object(self.ui,'tracking_hints',return_value=(KeyHint('b',30,'forward','competing-candidates'),)), \
                mock.patch.object(gpu.shader,'from_builtin',return_value=shader), \
                mock.patch.object(gpu.state,'scissor_get',return_value=(0,0,1000,500)), \
                mock.patch.object(gpu.state,'scissor_set'), \
                mock.patch.object(batches,'batch_for_shader',side_effect=batch), \
                mock.patch.object(blf,'draw',side_effect=lambda font,value:text.append(value)):
            self.ws._draw_window()
            depths=self.depths()
            diamonds=[points for kind,points,color in emitted if kind=='TRIS' and color==(.95,.75,.3,1)]
            self.assertEqual(len(diamonds),2)  # Detect swallowed drawing errors too.
            self.assertEqual({round(sum(y for x,y in p)/6,5) for p in diamonds},set(depths.values()))
            parents={r.character_id:r.y for r in self.rows() if r.kind=='character'}
            hints=self.ws.tracking_hint_markers(self.ctx,self.view,0,self.metrics)
            self.assertEqual(hints[0][2],parents['b'])
            self.ctx.region.type='CHANNELS'
            self.ws._draw_channels()
            self.assertEqual(text.count('Depth'),2)
            self.assertEqual(text.count('Same name'),2)
            self.assertEqual(len([p for kind,p,c in emitted if kind=='TRIS' and len(p)==3]),2)

    def test_annotation_geometry_tracks_hierarchy_without_touching_review_state(self):
        from dataclasses import replace
        from blendcap_vta_timeline import TimelineMotionInterval
        from blendcap_vta_workspace import motion_interval_rects,hit_motion_interval
        self.view=replace(self.view,repair_ranges=(TimelineMotionInterval('b',30,40,'freeze'),))
        expanded=self.ws.expanded_characters(self.ctx,self.view)
        parents=dict(character_lanes(self.view,500,expanded=expanded))
        rectangles=motion_interval_rects(self.view,self.ctx.region.view2d,1000,500,expanded=expanded)
        self.assertEqual(len(rectangles),1)
        self.assertAlmostEqual((rectangles[0][-1]+rectangles[0][-2])/2,parents['b'])
        self.assertIsNotNone(hit_motion_interval(self.view,self.ctx.region.view2d,1000,500,
            (330,parents['b']),expanded=expanded))
        self.assertIsNone(hit_motion_interval(self.view,self.ctx.region.view2d,1000,500,
            (330,self.depths()['b']),expanded=expanded))

    def test_draw_callbacks_do_nothing_outside_vta_scope(self):
        import gpu
        with mock.patch.object(self.ws,'_scope',return_value=False), \
                mock.patch.object(gpu.shader,'from_builtin') as shader:
            self.ws._draw_window();self.ws._draw_channels()
            shader.assert_not_called()
