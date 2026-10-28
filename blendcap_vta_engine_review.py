"""GPL neutral review client and disposable presentation cache; no private imports."""
from pathlib import Path
from types import SimpleNamespace as NS
import hashlib,threading
from uuid import uuid4
if __package__:
    from . import vta_protocol as wire
    from .vta_protocol import observation as dto
    from .blendcap_vta_review import present_review,ReviewLocation,VtaReviewUiError,sha256_file
    from .blendcap_vta_timeline import present_timeline
else:
    import vta_protocol as wire
    from vta_protocol import observation as dto
    from blendcap_vta_review import present_review,ReviewLocation,VtaReviewUiError,sha256_file
    from blendcap_vta_timeline import present_timeline

def signature(path):
    try:s=path.stat();return s.st_size,s.st_mtime_ns
    except OSError:return None

def read_observation(root,manifest):
    # Caller cannot select a filename/path; a validated opaque ID names one file
    # in the fixed derived cache directory. No links/streams/owner files allowed.
    wire.validate_response(wire.response('0'*32,'review-observation',result=manifest),'0'*32,'review-observation')
    root=Path(root).resolve();path=root/'.vta-engine/views'/(manifest['artifact_id']+'.json')
    if not path.resolve().is_relative_to(root) or path.stat().st_nlink!=1:
        raise VtaReviewUiError('Observation artifact escapes the companion or is linked.')
    if path.stat().st_size!=manifest['bytes']:raise VtaReviewUiError('Observation artifact size changed.')
    with path.open('rb') as stream:raw=stream.read(dto.MAX_BYTES+1)
    if len(raw)!=manifest['bytes'] or hashlib.sha256(raw).hexdigest()!=manifest['sha256']:
        raise VtaReviewUiError('Observation artifact checksum changed.')
    value=dto.loads(raw)
    if (value['checkpoint']!=manifest['checkpoint'] or value['source']['source_id']!=manifest['source_id']
            or value['coverage']!=manifest['coverage']):raise VtaReviewUiError('Observation manifest identity mismatch.')
    return Observation(value)

class Observation:
    def __init__(self,value):
        self.value=value;self.project_id=value['checkpoint']['project_id']
        self.characters=tuple(NS(**c) for c in value['characters'])
        self.source=value['source'];self.coverage=tuple(value['coverage'])
        self.frames={r['frame']:r for r in value['frames']}
        timeline=value['timeline']
        self.timeline=present_timeline(NS(source_id=timeline['source_id'],characters=self.characters,
            keys=tuple(NS(**{**k,'state':NS(value=k['state'])}) for k in timeline['keys']),
            derived=tuple(NS(**{**r,'state':NS(value=r['state'])}) for r in timeline['derived']),
            repair_ranges=tuple(NS(**r) for r in timeline['repair_ranges'])))
    def frame(self,location,*,show_suppressed=False):
        row=self.frames.get(location.source_frame)
        if row is None:raise VtaReviewUiError('Review window is loading; no stale frame is shown.')
        # Restore only the explicit neutral DTO shape expected by public GPL
        # presentation formatting. This never constructs a private domain type.
        values={**row,'analysis_revision_id':row['analysis_revision_id'] or '',
            'state':NS(value=row['state']),'characters':self.characters}
        for key in ('candidates','character_assignments','character_decisions','derived_tracking'):
            values[key]=tuple(NS(**{**r,**({'state':NS(value=r['state'])} if 'state' in r else {})}) for r in row[key])
        return present_review(NS(**values),(self.source['width'],self.source['height']),show_suppressed=show_suppressed)
    def members(self,scene):
        valid={c.character_id for c in self.characters}
        ids=set(self.value['source_member_ids'])
        ids.update(o.get('vta_character_id') for o in scene.objects if o.type=='ARMATURE'
            and o.get('vta_project_id')==self.project_id and o.get('vta_source_id')==self.source['source_id'])
        return ids & valid

class EngineReviewBackend:
    """One cached bounded neutral observation, independently validated mutations."""
    def __init__(self,client,*,mutate=None):
        self.client=client;self.mutate=mutate;self.observation=None;self.stamp=None;self.key=None
        self._job=None;self._result=None;self._error=None
        self.is_engine_review=True
        self.session_id=uuid4().hex;self.last_restore_id=None;self.last_restore_after=None
        self._source_digests={}
    def _source_hash(self,path):
        path=Path(path);stamp=signature(path);entry=self._source_digests.get(path)
        if entry is None or entry[0]!=stamp:
            entry=stamp,sha256_file(path);self._source_digests[path]=entry
        return entry[1]
    def _signature(self,location):
        rid=self.observation.value['analysis_revision_id'] if self.observation else None
        return (signature(location.companion_root/'project_state.json'),signature(location.source_path),
                signature(location.companion_root/'analysis'/(rid+'.json')) if rid else None)
    def _fetch(self,location):
        before=(signature(location.companion_root/'project_state.json'),signature(location.source_path))
        manifest=self.client.call('review-observation',dict(companion_root=str(location.companion_root),
            source_path=str(location.source_path),source_frame=max(0,location.source_frame)))
        observation=read_observation(location.companion_root,manifest)
        after=(signature(location.companion_root/'project_state.json'),signature(location.source_path))
        if before!=after:raise VtaReviewUiError('Observation inputs changed; refresh again.')
        if observation.value['analysis_revision_id']:
            path=location.companion_root/'analysis'/(observation.value['analysis_revision_id']+'.json')
            if not path.resolve().is_relative_to(location.companion_root.resolve()) or path.stat().st_size>64*1024*1024:
                raise VtaReviewUiError('Observation evidence path/size is invalid.')
            evidence_stamp=signature(path)
            with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=observation.value['cache_sha256'] or evidence_stamp!=signature(path):raise VtaReviewUiError('Observation evidence changed; refresh again.')
        else:evidence_stamp=None
        observation.input_stamp=(*before,evidence_stamp)
        return observation
    def observe(self,location,*,force=False):
        key=(location.companion_root,location.source_path)
        if not force and self.observation is not None and self.key==key and self.stamp==self._signature(location) and location.source_frame in self.observation.frames:
            return self.observation
        if self._job is not None:
            self._job.join(self.client.timeout)
            if self._job.is_alive():raise VtaReviewUiError('Review observation is still pending; retry shortly.')
            self._job=None;self._result=None
        observation=self._fetch(location)
        # A returned manifest is a revision-scoped snapshot, never authority.
        self.observation=observation;self.key=key;self.stamp=observation.input_stamp
        if self.last_restore_after is not None and self.last_restore_after!=observation.value['checkpoint']:
            self.last_restore_id=self.last_restore_after=None
        return observation
    def observe_async(self,location):
        """Observer schedules worker IPC; main-thread polls adopt only ready DTOs."""
        key=(location.companion_root,location.source_path)
        if self._job is not None and not self._job.is_alive():
            job_key,observation,error,stamp=self._result;self._job=None;self._result=None
            if job_key==key and stamp==(signature(location.companion_root/'project_state.json'),signature(location.source_path),
                    signature(location.companion_root/'analysis'/(observation.value['analysis_revision_id']+'.json')) if observation is not None and observation.value['analysis_revision_id'] else None):
                if error is not None:raise VtaReviewUiError(str(error))
                self.observation=observation;self.key=key;self.stamp=stamp
                if self.last_restore_after is not None and self.last_restore_after!=observation.value['checkpoint']:
                    self.last_restore_id=self.last_restore_after=None
                return observation
        if (self.observation is not None and self.key==key and self.stamp==self._signature(location)
                and location.source_frame in self.observation.frames):return self.observation
        if self._job is None:
            def work():
                try:result=self._fetch(location);error=None
                except Exception as exc:result=None;error=exc
                stamp=result.input_stamp if result is not None else (signature(location.companion_root/'project_state.json'),signature(location.source_path),None)
                self._result=(key,result,error,stamp)
            self._job=threading.Thread(target=work,daemon=True);self._job.start()
        return self.observation if self.key==key else None
    def frame_from_context(self,context,location,*,show_suppressed=False):
        return context.frame(location,show_suppressed=show_suppressed)
    def timeline_from_context(self,context):return context.timeline
    def query(self,location,*,show_suppressed=False):return self.observe(location).frame(location,show_suppressed=show_suppressed)
    def query_timeline(self,location):return self.observe(location).timeline
    def _payload(self,location):
        observed=self.observe(location)
        return observed,dict(companion_root=str(location.companion_root),project_id=observed.project_id,
            expected_revision=observed.value['checkpoint']['revision'])
    def _cache_payload(self,location):
        observed,payload=self._payload(location)
        rid=observed.value['analysis_revision_id']
        if not rid:raise VtaReviewUiError('Analyze candidates before editing frame decisions.')
        payload.update(source_id=observed.source['source_id'],source_frame=location.source_frame,
                       analysis_revision_id=rid,cache_sha256=observed.value['cache_sha256'])
        return observed,payload
    def _send(self,location,operation,payload,observed):
        if self._job is not None:
            self._job.join(self.client.timeout)
            if self._job.is_alive():raise VtaReviewUiError('Review observation is still pending; retry shortly.')
            self._job=None;self._result=None
        if self.mutate is None:result=self.client.call(operation,payload)
        else:result=self.mutate(operation,payload,observed.value['checkpoint'])
        self.last_restore_id=result.get('details',{}).get('restore_id') if operation!='review-restore-last' else None
        self.last_restore_after=result['after'] if self.last_restore_id else None
        # The host recovery adapter may already have refreshed this cache.
        if self.observation is None or self.observation.value['checkpoint']!=result['after']:
            self.observe(location,force=True)
        return result
    def apply(self,location,*,observation_id,character_id):
        observed,payload=self._cache_payload(location);payload.update(observation_id=observation_id,character_id=character_id)
        result=self._send(location,'review-assign',payload,observed)
        return result,self.query(location)
    def prepare_reassignment(self,location,*,observation_id,character_id):
        observed,payload=self._cache_payload(location)
        payload.update(observation_id=observation_id,character_id=character_id,confirmed=True)
        return NS(operation='review-reassign',payload=payload,before=observed,observation_id=observation_id,character_id=character_id),self.query(location)
    def apply_confirmed_reassignment(self,location,request):
        result=self._send(location,request.operation,request.payload,request.before)
        return result,self.query(location)
    def prepare_hard_state(self,location,*,character_id,state):
        observed,payload=self._cache_payload(location);view=self.query(location)
        character=next((c for c in view.characters if c.character_id==character_id),None)
        if character is None:raise VtaReviewUiError('Character no longer exists.')
        required=character.decision_state is not None or character.occupied_observation_id is not None
        payload.update(character_id=character_id,state=state,confirmed=False)
        return NS(operation='review-hard-state',payload=payload,before=observed,character_id=character_id,
                  state=NS(value=state),requires_confirmation=required),view
    def apply_hard_state(self,location,request,*,confirmed):
        result=self._send(location,request.operation,{**request.payload,'confirmed':confirmed},request.before)
        return result,self.query(location)
    def create_character(self,location,*,display_name):
        observed,payload=self._payload(location);payload['display_name']=display_name
        result=self._send(location,'character-create',payload,observed)
        return NS(**result['character']),self.query(location)
    def prepare_management(self,location,action,**kwargs):
        if action=='rename-character':raise VtaReviewUiError('Character rename requires the engine mutation boundary.')
        observed,payload=self._payload(location)
        keys=tuple(kwargs.get('keyframe_ids',()))
        cid=kwargs.get('character_id');sid=kwargs.get('source_id');number=kwargs.get('frame')
        if action=='clear-assignment':
            keys=tuple(k.keyframe_id for k in observed.timeline.keys if k.character_id==cid and k.source_frame==number and k.observation_id is not None)
        elif action=='clear-all-review-keys-on-frame':keys=tuple(k.keyframe_id for k in observed.timeline.keys if k.source_frame==number)
        elif action=='delete-character':keys=tuple(k.keyframe_id for k in observed.timeline.keys if k.character_id==cid)
        payload.update(action=action,character_id=cid,source_id=sid,frame=number,keyframe_ids=list(keys),confirmed=False,session_id=self.session_id)
        return NS(operation='review-management',payload=payload,before=observed,action=action,character_id=cid,
                  keyframe_ids=keys,affected_count=len(keys))
    def apply_management(self,location,request,*,confirmed):
        result=self._send(location,request.operation,{**request.payload,'confirmed':confirmed},request.before)
        return result,self.query(location),self.query_timeline(location)
    def prepare_suppression(self,location,observation_id,*,suppressed):
        observed,payload=self._cache_payload(location);view=self.query(location,show_suppressed=True)
        candidate=next((c for c in view.candidates if c.observation_id==observation_id),None)
        if candidate is None:raise VtaReviewUiError('Candidate no longer exists on this frame.')
        assigned=tuple(c.character_id for c in view.characters if c.occupied_observation_id==observation_id)
        payload.update(observation_id=observation_id,suppressed=suppressed,confirmed=False,session_id=self.session_id)
        return NS(operation='candidate-suppress',payload=payload,before=observed,assigned_character_ids=assigned,
                  observation_id=observation_id,suppressed=suppressed)
    def apply_suppression(self,location,request,*,confirmed=False):
        return self._send(location,request.operation,{**request.payload,'confirmed':confirmed},request.before)
    def update_tracking_for_characters(self,location,character_ids):
        observed,payload=self._cache_payload(location);payload.update(character_ids=list(character_ids),session_id=self.session_id)
        result=self._send(location,'tracking-update',payload,observed)
        def scope(value):return NS(**{**value,'left_boundary':NS(**value['left_boundary']),'right_boundary':NS(**value['right_boundary'])})
        outcomes=[]
        for row in result['details']['outcomes']:
            value=row['update']
            update=None if value is None else NS(**{**value,'scope':scope(value['scope']),
                'scopes':tuple(scope(s) for s in value['scopes']),'ambiguities':tuple(NS(**a) for a in value['ambiguities'])})
            outcomes.append(NS(character_id=row['character_id'],update=update,error=row['error']))
        return tuple(outcomes),self.query(location),self.query_timeline(location)
    def update_tracking(self,location,character_id):
        outcomes,view,timeline=self.update_tracking_for_characters(location,(character_id,))
        if outcomes[0].update is None:raise VtaReviewUiError(outcomes[0].error)
        return outcomes[0].update,view,timeline
    def plan_tracking(self,location,character_ids):
        observed,payload=self._cache_payload(location);payload.update(character_ids=list(character_ids),session_id=self.session_id)
        return self.client.call('tracking-plan',payload)
    def set_motion_trust_for_characters(self,location,character_ids,start_visible,end_visible,*,action,mode=None,previous_range=None):
        observed,payload=self._payload(location);offset=location.blender_frame-location.source_frame
        payload.update(source_id=observed.source['source_id'],character_ids=list(character_ids),start_frame=start_visible-offset,
            end_frame=end_visible-offset,action=action,mode=mode,previous_range=list(previous_range) if previous_range is not None else None,session_id=self.session_id)
        result=self._send(location,'repair-edit',payload,observed)
        return tuple(NS(**{**row,'intervals':tuple(NS(**i) for i in row['intervals'])}) for row in result['details']['updates'])
    def set_motion_trust(self,location,character_id,start_visible,end_visible,**kwargs):
        return self.set_motion_trust_for_characters(location,(character_id,),start_visible,end_visible,**kwargs)[0]
    def restore_last(self,location):
        if not self.last_restore_id:raise VtaReviewUiError('No compatible protected result is available in this session.')
        observed,payload=self._payload(location);payload.update(restore_id=self.last_restore_id,session_id=self.session_id)
        return self._send(location,'review-restore-last',payload,observed)
