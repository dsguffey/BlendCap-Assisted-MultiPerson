"""Foreground callback lifecycle and native-backed marker bridge, without bpy.

The host stubs are input/font/GPU/authority sinks, not replacement row models.
No Blender subprocess, desktop capture, owner project write or private import.
"""
import importlib
from dataclasses import replace
from types import ModuleType, SimpleNamespace as NS
import sys
from pathlib import Path
import unittest
from unittest import mock

from tests import test_blendcap_vta_ui_regressions as regression
callbacks=regression.callbacks
from blendcap_vta_review import CharacterRow, character_name_exists
from blendcap_vta_timeline import TimelineCharacterSelection, TimelineKey, key_at
from blendcap_vta_area_mode import role_for, ROLE_TIMELINE, ROLE_PREVIEW


class Pointer(NS):
    def as_pointer(self):return id(self)


class Markers(list):
    def new(self,name,*,frame):
        marker=Pointer(name=name,frame=frame,select=False)
        self.append(marker);return marker


class HostFixture(unittest.TestCase):
    refresh=regression.RefreshTests.refresh

    def setUp(self):
        regression.RefreshTests.setUp(self)
        Scene=self.env['bpy'].types.Scene
        class Container(dict):
            def as_pointer(self):return id(self)
        self.workspace=Container(blendcap_vta_review_workspace=1)
        self.workspace.blendcap_vta_mode_enabled=True
        self.workspace.blendcap_vta_loaded_scene=self.a
        self.workspace.sequencer_scene=None
        self.window_region=Pointer(type='WINDOW',height=194,width=1000,x=0,y=0,
            view2d=NS(view_to_region=lambda f,y,clip=False:(f*10,y),region_to_view=lambda x,y:(x/10,y)))
        self.channel_region=Pointer(type='CHANNELS',height=194,width=200)
        self.timeline=Pointer(type='DOPESHEET_EDITOR',ui_type='DOPESHEET',
            regions=[self.window_region,self.channel_region],tag_redraw=mock.Mock())
        self.sidebar=Pointer(type='VIEW_3D',ui_type='VIEW_3D',tag_redraw=mock.Mock())
        self.screen=Container();self.screen.areas=[self.timeline,self.sidebar]
        self.workspace.screens=[self.screen]
        self.window=Pointer(scene=self.a,workspace=self.workspace,screen=self.screen,
            cursor_modal_set=mock.Mock(),cursor_modal_restore=mock.Mock())
        self.timers=[]
        self.wm=NS(windows=[self.window],blendcap_vta_new_character_name='',
            event_timer_add=mock.Mock(return_value=object()),event_timer_remove=mock.Mock(),
            modal_handler_add=mock.Mock(),invoke_props_dialog=mock.Mock(return_value={'RUNNING_MODAL'}))
        self.context=NS(scene=self.a,window=self.window,workspace=self.workspace,screen=self.screen,
            area=self.sidebar,region=NS(type='UI'),window_manager=self.wm,
            preferences=NS(system=NS(ui_scale=1)))
        self.bpy=NS(types=NS(Scene=Scene,Operator=object,Panel=object,Menu=object),
            props=NS(EnumProperty=lambda **kw:None,StringProperty=lambda **kw:None,
                     IntProperty=lambda **kw:None,BoolProperty=lambda **kw:None),
            app=NS(background=False,handlers=NS(persistent=lambda f:f),
                timers=NS(is_registered=lambda f:f in self.timers,register=lambda f,**kw:self.timers.append(f))),
            data=NS(filepath='synthetic.blend',scenes=[self.a,self.b]),context=self.context)
        patch=mock.patch.dict(sys.modules,{'bpy':self.bpy});patch.start();self.addCleanup(patch.stop)
        child=self.package+'.blendcap'
        session=sys.modules[child+'.vta_scene_session'];session.main_scene=lambda c:c.window.scene
        ui=ModuleType(child+'.vta_review_ui');ui.__dict__.update(self.env)
        sys.modules[ui.__name__]=ui;self.env=ui.__dict__;self.ui=ui
        self.env.update(bpy=self.bpy,SimpleNamespace=NS,TimelineCharacterSelection=TimelineCharacterSelection,
            role_for=role_for,ROLE_TIMELINE=ROLE_TIMELINE,ROLE_PREVIEW=ROLE_PREVIEW,
            _edit_available=lambda op:True,character_name_exists=character_name_exists,
            _MANAGEMENT_ACTIONS=(),_pending_presentation=None,_syncing_properties=False,_syncing_objects=False,
            _NO_SELECTION='none',sync_review_to_objects=mock.Mock(),record_scene_source=mock.Mock(),
            _tracking_hints=NS(clear=mock.Mock()))
        callbacks('blendcap/vta_review_ui.py',('_activate_review_scene','refresh','_accept_mutated_review',
            '_is_timeline_context','timeline_character_selection','current_character_id','_lane_scroll',
            '_presentation_current','timeline_state','_assignment_scope','_sync_properties',
            'sync_character_to_timeline','_queue_presentation','_presentation_timer',
            'BLENDCAP_OT_vta_review_create_character',
            'BLENDCAP_OT_vta_review_manage'),self.env)
        self.ws=importlib.import_module(child+'.vta_workspace')
        self.markers=importlib.import_module(child+'.vta_timeline_markers')
        self.busy=importlib.import_module(child+'.vta_busy')
        self.invalidation=importlib.import_module(child+'.vta_invalidation')
        ui.vta_invalidation=self.invalidation
        self.management=importlib.import_module(child+'.vta_management_selection')
        self.ws._row_style=lambda *a:((.1,.1,.1,1),(.8,.8,.8,1),None)
        skeletal=ModuleType(child+'.vta_skeletal_output');skeletal.mark_stale_outputs=mock.Mock()
        bindings=ModuleType(child+'.vta_character_bindings');bindings.record=mock.Mock()
        sys.modules[skeletal.__name__]=skeletal;sys.modules[bindings.__name__]=bindings
        self.bridge=ModuleType(child+'.vta_review_undo');sys.modules[self.bridge.__name__]=self.bridge
        self.bridge.__dict__.update(__package__=child,bpy=self.bpy,Path=Path)
        self.bridge.AppliedMutationError=type('AppliedMutationError',(RuntimeError,),{})
        callbacks('blendcap/vta_review_undo.py',('_adopt_creation','_refresh'),self.bridge.__dict__)
        self.characters=[CharacterRow('a','black shirt',None),CharacterRow('spare','no shirt',None),
                         CharacterRow('b','Foreign',None)]
        self.membership._write(self.a,{'a','spare'})
        for scene,owner in ((self.a,'owner-a'),(self.b,'owner-b')):
            scene['vta_scene_owner_id']=owner;scene['vta_source_video_sha256']='0'*64
            scene.timeline_markers=Markers();scene.tool_settings=NS(lock_markers=False)
        self.revision=0
        self.backend.mutate=self.mutate
        self.env['_timing_source']=self.timing
        self.busy.install(ui.BLENDCAP_OT_vta_review_create_character)
        self.busy.install(ui.BLENDCAP_OT_vta_review_manage,defer=False)

    def snapshot(self):
        result=regression.RefreshTests.snapshot(self)
        result.value['checkpoint']=dict(project_id='p',revision={'sha256':str(getattr(self,'revision',0))*64,'bytes':1})
        result.timeline=replace(result.timeline,characters=tuple(sorted(result.timeline.characters,key=lambda c:c.display_name.casefold())))
        return result

    def timing(self,context,*,force=False):
        self.backend.observe(self.location,force=force)
        return self.env['cached_scene_source'](context)

    def mutate(self,operation,payload,before):
        owner=self.context.scene
        if operation=='character-create':
            identity='new-'+owner['vta_scene_owner_id']
            self.characters.append(CharacterRow(identity,payload['display_name'],None))
            character=dict(character_id=identity,display_name=payload['display_name'])
            intent=dict(root=str(self.location.companion_root),binding=dict(
                scene_owner_id=owner['vta_scene_owner_id'],project_id='p',source_id='source',video_sha256='0'*64))
            self.bridge._adopt_creation(intent,dict(character=character,request_id='1'*32),self.context)
        elif operation=='review-management':
            self.characters=[c for c in self.characters if c.character_id!=payload['character_id']]
            character={}
        else:raise AssertionError(operation)
        self.revision+=1
        self.bridge._refresh()
        return dict(character=character,after=self.backend.observation.value['checkpoint'])

    def drain(self):
        # A native app-timer callback has no button/Dope Sheet area. Exercise
        # that context change and the real selection/presentation callback too.
        self.context.area=None;self.context.region=None
        while self.timers:self.timers.pop(0)()
        self.context.area=self.sidebar;self.context.region=NS(type='UI')

    def painted_names(self):
        text=[];ctx=self.context
        old_area,old_region=ctx.area,ctx.region
        ctx.area=self.timeline;ctx.region=self.channel_region
        font=NS(position=lambda *a:None,size=lambda *a:None,color=lambda *a:None,
                draw=lambda f,s:text.append(s))
        shader=NS(bind=lambda:None,uniform_float=lambda *a:None)
        gpu=NS(shader=NS(from_builtin=lambda *a:shader),
               state=NS(scissor_get=lambda:(0,0,self.channel_region.width,self.channel_region.height),
                        scissor_set=mock.Mock()))
        batch=NS(batch_for_shader=lambda *a:NS(draw=lambda *a:None))
        try:
            with mock.patch.dict(sys.modules,{'blf':font,'gpu':gpu,'gpu_extras.batch':batch}):
                self.ws._draw_channels()
        finally:ctx.area,ctx.region=old_area,old_region
        return text

    def create(self,name):
        self.wm.blendcap_vta_new_character_name=name
        op=self.ui.BLENDCAP_OT_vta_review_create_character();op.report=mock.Mock()
        before=len(self.characters)
        self.assertEqual(op.execute(self.context),{'RUNNING_MODAL'})
        self.assertEqual(len(self.characters),before,'Must exercise deferred foreground execute, not background bypass')
        self.assertEqual(op.modal(self.context,NS(type='TIMER')),{'FINISHED'},op.report.call_args)
        self.drain();return op

class HostTests(HostFixture):
    def test_wheel_then_shift_click_and_b_box_use_the_same_depth_parent_geometry(self):
        self.refresh(self.a);self.create('tall ref')
        self.context.area=self.timeline;self.context.region=self.channel_region
        self.env['key_at']=key_at
        callbacks('blendcap/vta_review_ui.py',('clear_repair_selection','select_timeline_key','select_timeline_keys'),self.env)
        self.ui._timeline=replace(self.ui._timeline,keys=(TimelineKey('new-key','new-owner-a',30,'occluded',None),))
        self.assertEqual(self.ws.BLENDCAP_OT_vta_timeline_scroll.invoke(NS(),self.context,
            NS(type='WHEELUPMOUSE',mouse_region_y=120)),{'FINISHED'})
        self.context.region=self.window_region
        rows=self.ws._rows(self.context,self.ui._timeline,0,self.ws._metrics(self.context))
        self.assertIn(('spare','depth'),[(r.character_id,r.kind) for r in rows])
        y=next(r.y for r in rows if (r.character_id,r.kind)==('new-owner-a','character'))
        def event(kind,x,y,value='PRESS',shift=False):
            return NS(type=kind,value=value,mouse_region_x=x,mouse_region_y=y,mouse_x=x,mouse_y=y,ctrl=False,shift=shift)
        for expected in (('new-key',),()):
            self.assertEqual(self.ws.BLENDCAP_OT_vta_timeline_select.invoke(NS(),self.context,
                event('LEFTMOUSE',310,y,shift=True)),{'FINISHED'})
            self.assertEqual(self.ui._review_key_selection.keyframe_ids,expected)
        cls=self.ws.BLENDCAP_OT_vta_timeline_keys;op=cls();op.action='BOX'
        self.assertEqual(op.invoke(self.context,event('B',290,y-10)),{'RUNNING_MODAL'})
        self.assertEqual(op.modal(self.context,event('LEFTMOUSE',290,y-10)),{'RUNNING_MODAL'})
        self.assertEqual(op.modal(self.context,event('MOUSEMOVE',330,y+10)),{'RUNNING_MODAL'})
        self.assertEqual(op.modal(self.context,event('LEFTMOUSE',330,y+10,'RELEASE')),{'FINISHED'})
        self.assertEqual(self.ui._review_key_selection.keyframe_ids,('new-key',))
        self.assertEqual(self.ws._rows(self.context,self.ui._timeline,0,self.ws._metrics(self.context)),rows)

    def test_foreground_create_timer_redraw_paints_new_row_without_changing_targets(self):
        self.refresh(self.a);self.env['_selection'].character_id='a'
        self.assertNotIn('tall ref',self.painted_names())
        self.timeline.tag_redraw.reset_mock()
        self.create('tall ref')
        self.assertIn('tall ref',self.painted_names())
        self.assertEqual(self.ui._selection.character_id,'a')
        self.assertEqual(self.management.selected(),'new-owner-a')
        self.assertGreater(self.timeline.tag_redraw.call_count,0)
        self.wm.event_timer_remove.assert_called_once()

    def test_foreground_create_two_scenes_return_and_explicit_scroll_anchor(self):
        self.refresh(self.a)
        # Preserve a real viewport anchor, not merely an unsliced row model.
        target=NS(**vars(self.context));target.area=self.timeline;target.region=self.window_region
        self.ws._row_scroll[self.ws._expansion_key(target)]=('a','character',0)
        self.create('tall ref')
        self.assertIn('tall ref',self.painted_names())
        self.window.scene=self.b;self.workspace.blendcap_vta_loaded_scene=self.b
        self.refresh(self.b);self.create('Zebra B')
        self.assertIn('Zebra B',self.painted_names());self.assertNotIn('tall ref',self.painted_names())
        self.window.scene=self.a;self.workspace.blendcap_vta_loaded_scene=self.a
        self.refresh(self.a);self.drain()
        self.assertIn('tall ref',self.painted_names());self.assertNotIn('Zebra B',self.painted_names())

    def test_render_reconciles_new_model_without_an_operation_time_reveal_callback(self):
        self.refresh(self.a);self.painted_names()
        self.create('tall ref')
        self.assertIn('tall ref',[c.display_name for c in self.ui._timeline.characters])
        self.assertIn('tall ref',self.painted_names())
        self.assertFalse(hasattr(self.ws,'reveal_character'))

    def test_confirmed_rename_delete_paint_and_keep_other_scene_and_assignment(self):
        self.refresh(self.a);self.ui._selection.character_id='a'
        self.create('tall ref')
        identity=self.management.selected()
        self.bridge.__dict__.update(require_ready=lambda:None,_client=lambda c:None,
            _intent=lambda root,before,kind,operation,payload:dict(payload=payload),
            _mark=mock.Mock(),_unmark=mock.Mock())
        def rename_send(intent,client):
            payload=intent['payload']
            self.characters=[replace(c,display_name=payload['display_name'])
                if c.character_id==payload['character_id'] else c for c in self.characters]
            self.revision+=1
            return {}
        self.bridge._send=rename_send
        callbacks('blendcap/vta_review_undo.py',('rename',),self.bridge.__dict__)
        op=self.ui.BLENDCAP_OT_vta_review_manage();op.report=mock.Mock()
        op.action='rename-character';op.new_name='AAA referee'
        op._pending_location=self.ui._location
        op._pending_request=dict(character_id=identity,checkpoint=self.backend.observation.value['checkpoint'])
        self.assertEqual(op.execute(self.context),{'FINISHED'},op.report.call_args)
        self.drain()
        self.assertIn('AAA referee',self.painted_names());self.assertNotIn('tall ref',self.painted_names())
        self.assertEqual(self.ui._selection.character_id,'a')
        op.action='delete-character'
        op._pending_request=self.backend.prepare_management(self.ui._location,'delete-character',character_id=identity)
        self.assertEqual(op.execute(self.context),{'FINISHED'},op.report.call_args)
        self.drain();self.assertNotIn('AAA referee',self.painted_names())
        self.assertEqual(self.ui._selection.character_id,'a')
        self.window.scene=self.b;self.workspace.blendcap_vta_loaded_scene=self.b
        self.refresh(self.b);self.drain()
        self.assertIn('Foreign',self.painted_names())
        self.assertNotIn('AAA referee',self.painted_names())


class MarkerTests(HostFixture):
    # The fixture imports the real marker module/classes, never a copied marker model.
    def marker_context(self):
        self.context.area=self.timeline;self.context.region=self.window_region
        return self.context

    def edit(self,action,**kw):
        op=self.markers.BLENDCAP_OT_vta_marker_edit()
        op.action=action;op.frames=kw.get('frames',0);op.marker_name=kw.get('marker_name','')
        return op.execute(self.marker_context())

    def test_native_collection_add_rename_move_delete_and_scene_isolation(self):
        self.assertEqual(self.edit('ADD'),{'FINISHED'})
        marker=self.a.timeline_markers[0]
        self.assertEqual(self.edit('RENAME',marker_name='Shared marker'),{'FINISHED'})
        self.assertEqual(self.edit('MOVE',frames=9),{'FINISHED'})
        self.assertEqual((marker.name,marker.frame),('Shared marker',10))
        self.assertEqual(len(self.b.timeline_markers),0)
        self.assertEqual(self.edit('DELETE'),{'FINISHED'});self.assertEqual(self.a.timeline_markers,[])
        self.assertIn('UNDO',self.markers.BLENDCAP_OT_vta_marker_edit.bl_options)

    def test_full_window_draw_covers_native_keys_and_renders_custom_marker_above_scrollbar(self):
        self.refresh(self.a);ctx=self.marker_context()
        self.a.timeline_markers.new('Shared native marker',frame=12)
        self.ui.selected_repair_range=lambda c:None;self.ui.tracking_hints=lambda c:()
        labels=[]
        font=NS(position=lambda *a:None,size=lambda *a:None,color=lambda *a:None,
                draw=lambda f,s:labels.append(s))
        shader=NS(bind=lambda:None,uniform_float=lambda *a:None)
        gpu=NS(shader=NS(from_builtin=lambda *a:shader),
               state=NS(scissor_get=lambda:(0,0,self.window_region.width,self.window_region.height),
                        scissor_set=mock.Mock()))
        batch=NS(batch_for_shader=lambda *a:NS(draw=lambda *a:None))
        with mock.patch.dict(sys.modules,{'blf':font,'gpu':gpu,'gpu_extras.batch':batch}), \
                mock.patch.object(self.ws,'_fill',wraps=self.ws._fill) as fill:
            self.ws._draw_window()
        self.assertEqual(fill.call_args_list[0].args[2:6],(0,0,1000,194))
        self.assertFalse(any(call.args[2:6]==(0,16,1000,44) for call in fill.call_args_list))
        gpu.state.scissor_set.assert_any_call(0,44,1000,88)
        gpu.state.scissor_set.assert_called_with(0,0,1000,194)
        self.assertIn('Shared native marker',labels,'Also detect swallowed errors before the marker draw')

    def test_external_edits_are_read_on_every_draw_and_names_and_positions_hit(self):
        ctx=self.marker_context();marker=self.a.timeline_markers.new('Native original',frame=10)
        labels=[];lines=[]
        font=NS(position=lambda *a:None,size=lambda *a:None,color=lambda *a:None,
            draw=lambda f,s:labels.append(s),dimensions=lambda f,s:(len(s)*6,12))
        with mock.patch.dict(sys.modules,{'blf':font}):
            self.markers.draw(ctx,1,None,None,mock.Mock(),lambda s,b,p,c:lines.append(p))
            self.assertEqual(self.markers.hit(ctx,130,1),marker)
            self.assertEqual(self.markers.hit(ctx,100,1),marker)
            marker.name='Native renamed';marker.frame=20
            labels.clear();lines.clear()
            self.markers.draw(ctx,1,None,None,mock.Mock(),lambda s,b,p,c:lines.append(p))
            self.assertEqual(labels,['Native renamed']);self.assertEqual(self.markers.positions(ctx)[0][1],200)
            self.a.timeline_markers.remove(marker)
            self.assertEqual(self.markers.positions(ctx),())
        self.assertTrue(all(y>=16 for line in lines for x,y in line))

    def test_drag_preview_is_local_and_one_undoable_move_is_called_on_release(self):
        ctx=self.marker_context();marker=self.a.timeline_markers.new('Drag',frame=10)
        def commit(*args,**kw):
            self.assertEqual(args,('EXEC_DEFAULT',True))
            return self.edit(kw['action'],frames=kw['frames'])
        operation=mock.Mock(side_effect=commit)
        self.bpy.ops=NS(blendcap=NS(vta_marker_edit=operation))
        font=NS(size=lambda *a:None,dimensions=lambda f,s:(len(s)*6,12))
        event=lambda kind,x,value='PRESS',shift=False:NS(type=kind,value=value,mouse_region_x=x,
            mouse_region_y=26,shift=shift,ctrl=False)
        op=self.markers.BLENDCAP_OT_vta_marker_drag();op.grab=False
        with mock.patch.dict(sys.modules,{'blf':font}):
            self.assertEqual(op.invoke(ctx,event('LEFTMOUSE',100)),{'RUNNING_MODAL'})
        self.assertTrue(marker.select)
        for x in (110,130,155):
            self.assertEqual(op.modal(ctx,event('MOUSEMOVE',x)),{'RUNNING_MODAL'})
            self.assertEqual(marker.frame,10);operation.assert_not_called()
        self.assertEqual(self.markers.positions(ctx)[0][1],160)
        self.assertEqual(op.modal(ctx,event('LEFTMOUSE',160,'RELEASE')),{'FINISHED'})
        self.assertEqual(marker.frame,16);operation.assert_called_once()
        self.assertIsNone(self.markers._drag)

    def test_cancel_lock_and_shift_click_do_not_commit_a_move(self):
        ctx=self.marker_context();marker=self.a.timeline_markers.new('Drag',frame=10);marker.select=True
        font=NS(size=lambda *a:None,dimensions=lambda f,s:(len(s)*6,12))
        op=self.markers.BLENDCAP_OT_vta_marker_drag();op.grab=False
        event=NS(type='LEFTMOUSE',value='PRESS',mouse_region_x=100,mouse_region_y=26,shift=True,ctrl=False)
        with mock.patch.dict(sys.modules,{'blf':font}):
            self.assertEqual(op.invoke(ctx,event),{'FINISHED'})
        self.assertFalse(marker.select)
        marker.select=True;op.grab=True
        self.assertEqual(op.invoke(ctx,event),{'RUNNING_MODAL'})
        self.assertEqual(op.modal(ctx,NS(type='ESC')),{'CANCELLED'})
        self.assertEqual(marker.frame,10)
        self.a.tool_settings.lock_markers=True
        self.assertEqual(self.edit('MOVE',frames=6),{'CANCELLED'});self.assertEqual(marker.frame,10)

    def test_m_f2_delete_dispatch_and_rename_confirmation_use_native_collection(self):
        ctx=self.marker_context()
        def operation(*args,**kw):
            self.assertTrue(args[1],'Completed nested edit must request native Undo')
            op=self.markers.BLENDCAP_OT_vta_marker_edit();op.action=kw['action']
            op.marker_name='';op.frames=0
            return op.invoke(ctx,NS()) if args[0]=='INVOKE_DEFAULT' else op.execute(ctx)
        edit=mock.Mock(side_effect=operation)
        self.bpy.ops=NS(blendcap=NS(vta_marker_edit=edit))
        event=NS(mouse_region_y=26)
        cls=self.ws.BLENDCAP_OT_vta_timeline_keys
        self.assertEqual(cls.invoke(NS(action='ADD'),ctx,event),{'FINISHED'})
        self.assertEqual(len(self.a.timeline_markers),1)
        self.assertEqual(cls.invoke(NS(action='RENAME'),ctx,event),{'FINISHED'})
        dialog=self.wm.invoke_props_dialog.call_args.args[0];dialog.marker_name='From F2'
        self.assertEqual(dialog.execute(ctx),{'FINISHED'})
        self.assertEqual(self.a.timeline_markers[0].name,'From F2')
        self.assertEqual(cls.invoke(NS(action='DELETE'),ctx,event),{'FINISHED'})
        self.assertEqual(len(self.a.timeline_markers),0)

    def test_left_click_reaches_bottom_bridge_without_starting_box(self):
        ctx=self.marker_context();marker=self.a.timeline_markers.new('Click',frame=10)
        font=NS(size=lambda *a:None,dimensions=lambda f,s:(len(s)*6,12))
        event=NS(type='LEFTMOUSE',value='PRESS',mouse_region_x=100,mouse_region_y=26,shift=False,ctrl=False)
        op=self.markers.BLENDCAP_OT_vta_marker_drag();op.grab=False
        self.bpy.ops=NS(blendcap=NS(vta_marker_drag=lambda *args:op.invoke(ctx,event)))
        with mock.patch.dict(sys.modules,{'blf':font}):
            self.assertEqual(self.ws.BLENDCAP_OT_vta_timeline_select.invoke(NS(),ctx,event),{'RUNNING_MODAL'})
        self.assertTrue(marker.select);self.assertIsNone(self.ws._box_gesture)
        self.assertEqual(op.modal(ctx,NS(type='ESC')),{'CANCELLED'})
        self.assertFalse(self.markers.contains(ctx,NS(mouse_region_y=10),1))


if __name__=='__main__':unittest.main()
