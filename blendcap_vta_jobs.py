"""GPL job client: bounded neutral artifacts and background control calls.

No private imports, models or Blender objects. Native UI adopts ready messages.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from dataclasses import dataclass
import hashlib,json,threading
from pathlib import Path
from uuid import uuid4
if __package__:
    from . import vta_protocol as wire
    from .vta_protocol import jobs as contract
else:
    import vta_protocol as wire
    from vta_protocol import jobs as contract

def read_artifact(root,manifest):
    contract.manifest(manifest);root=Path(root).resolve()
    path=root/'.vta-engine/jobs'/manifest['job_id']/manifest['name']
    if not path.resolve().is_relative_to(root) or path.stat().st_nlink!=1:
        raise ValueError('Job artifact path is unsafe.')
    if path.stat().st_size!=manifest['bytes']:raise ValueError('Job artifact size changed.')
    with path.open('rb') as stream:raw=stream.read(32*1024*1024+1)
    if len(raw)!=manifest['bytes'] or hashlib.sha256(raw).hexdigest()!=manifest['sha256']:
        raise ValueError('Job artifact checksum changed.')
    # Reuse duplicate-key/nonfinite safeguards with the separate artifact bounds.
    def pairs(items):
        value={}
        for k,v in items:
            if k in value:raise ValueError('Duplicate artifact field.')
            value[k]=v
        return value
    value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=lambda _:wire.require(False,'Nonfinite artifact.'))
    return contract.finite_tree(value)

class ControlTask:
    """At most one control RPC worker, never called synchronously by a draw."""
    def __init__(self):self.thread=None;self.value=None;self.error=None
    def submit(self,fn):
        if self.thread is not None:raise RuntimeError('A job control request is still pending.')
        self.value=self.error=None
        def work():
            try:self.value=fn()
            except Exception as exc:self.error=exc
        self.thread=threading.Thread(target=work,daemon=True);self.thread.start()
    def take(self):
        if self.thread is None or self.thread.is_alive():return None
        self.thread=None
        if self.error is not None:
            error=self.error;self.error=None;raise error
        value=self.value;self.value=None;return value

@dataclass
class JobHandle:
    root: Path
    job_id: str
    kind: str
    client: object
    def __post_init__(self):
        wire.identifier(self.job_id);self.root=Path(self.root).resolve();self.task=ControlTask();self.status=None
    def call(self,operation,**values):
        value=self.client.call(operation,dict(companion_root=str(self.root),job_id=self.job_id,**values))
        status=value.get('status',value)
        contract.status(status)
        if status['job_id']!=self.job_id or status['kind']!=self.kind:raise ValueError('Job status identity mismatch.')
        self.status=status;return value
    def poll_async(self):
        result=self.task.take()
        if self.task.thread is None:self.task.submit(lambda:self.call('job-status'))
        return result
    def cancel_async(self):
        if self.task.thread is not None:return False
        self.task.submit(lambda:self.call('job-cancel'));return True
    def result(self):
        result=self.call('job-result')
        if result['artifact'] is None:raise ValueError('This job has no accepted or ready result.')
        return read_artifact(self.root,result['artifact'])

class EngineGenerationBackend:
    """Typed explicit generation planning/application boundary; no `_core`."""
    is_engine_generation=True
    def __init__(self,client,review_backend,*,mutate=None):
        self.client=client;self.review=review_backend;self.mutate=mutate
    def retarget_source_bindings(self,location,*,include_fingers=False):
        result=self.client.call('retarget-bindings',dict(companion_root=str(location.companion_root)))
        return tuple(result['bindings'] if include_fingers else [r for r in result['bindings'] if not any(f in r['id'] for f in ('_thumb_','_index_','_middle_','_ring_','_pinky_'))])
    def export_retarget_motion(self,location,**snapshot):
        root=Path(location.companion_root).resolve();observed=self.review.observe(location,force=True)
        contract.finite_tree(snapshot)
        raw=json.dumps(snapshot,separators=(',',':'),sort_keys=True,allow_nan=False).encode()
        if len(raw)>32*1024*1024:raise ValueError('Native motion snapshot exceeds the bounded artifact size.')
        folder=root/'.vta-host-inputs'
        if not folder.resolve().is_relative_to(root):raise ValueError('Unsafe native input directory.')
        folder.mkdir(exist_ok=True);identity=uuid4().hex;path=folder/(identity+'.json')
        try:
            with path.open('xb') as stream:stream.write(raw)
            response=self.client.call('retarget-export',dict(companion_root=str(root),project_id=observed.project_id,
                expected_revision=observed.value['checkpoint']['revision'],source_id=observed.source['source_id'],
                input_id=identity,input_sha256=hashlib.sha256(raw).hexdigest(),input_bytes=len(raw)))
            artifact=root/'.vta-engine/motion'/(response['artifact_id']+'.json')
            if not artifact.resolve().is_relative_to(root) or artifact.stat().st_nlink!=1 or artifact.stat().st_size!=response['bytes']:raise ValueError('Motion artifact path/size changed.')
            with artifact.open('rb') as stream:data=stream.read(32*1024*1024+1)
            if len(data)!=response['bytes'] or hashlib.sha256(data).hexdigest()!=response['sha256']:raise ValueError('Motion artifact checksum changed.')
            value=json.loads(data);contract.finite_tree(value)
            if value.get('provenance',{}).get('state_token')!=response['checkpoint']['revision']['sha256']:raise ValueError('Motion checkpoint identity changed.')
            return value
        finally:path.unlink(missing_ok=True)
    def prepare(self,location,character_ids,frame_range,*,fov_mode='FIRST',depth_mode='FIXED',previous_fingerprints=(),request_id=None):
        observed=self.review.observe(location)
        payload=dict(companion_root=str(location.companion_root),project_id=observed.project_id,
            expected_revision=observed.value['checkpoint']['revision'],source_id=observed.source['source_id'],source_path=str(location.source_path),
            character_ids=list(character_ids),frame_range=list(frame_range),fov_mode=fov_mode,depth_mode=depth_mode,
            previous_fingerprints=list(previous_fingerprints),frame_offset=location.blender_frame-location.source_frame)
        result=self.client.call('generation-prepare',payload,request_id=request_id)
        handle=JobHandle(location.companion_root,result['status']['job_id'],'generation',self.client);handle.status=result['status']
        return handle,read_artifact(location.companion_root,result['plan'])
    def prepare_analysis(self,location,frame_range,*,request_id=None):
        observed=self.review.observe(location)
        result=self.client.call('analysis-prepare',dict(companion_root=str(location.companion_root),project_id=observed.project_id,
            expected_revision=observed.value['checkpoint']['revision'],source_id=observed.source['source_id'],source_path=str(location.source_path),frame_range=list(frame_range)),request_id=request_id)
        handle=JobHandle(location.companion_root,result['status']['job_id'],'analysis',self.client);handle.status=result['status']
        return handle,read_artifact(location.companion_root,result['plan'])
    def accept(self,location,handle):
        observed=self.review.observe(location,force=True);status=handle.call('job-status')
        if status['state']=='accepted':return status
        if status['state']!='ready':raise ValueError('Job is not ready for acceptance.')
        before=observed.value['checkpoint'];payload=dict(companion_root=str(location.companion_root),project_id=observed.project_id,
            expected_revision=before['revision'],job_id=handle.job_id,artifact_sha256=status['result']['sha256'])
        if self.mutate is None:self.client.call('generation-accept',payload)
        else:self.mutate('generation-accept',payload,before)
        self.review.observe(location,force=True)
        return handle.call('job-status')
    def validate_output(self,location,path,*,previous_fingerprints=(),saved_reference=None,migration_approved=False):
        root=Path(location.companion_root).resolve();path=Path(path).resolve()
        relative=path.relative_to(root/'reconstruction');parts=relative.parts
        if len(parts)!=3 or not parts[0].startswith('job-') or parts[2]!='output.json':raise ValueError('Output is not a versioned generation job artifact.')
        jid=parts[0][4:];wire.identifier(jid)
        if parts[1] not in ('0','1'):raise ValueError('Invalid generation output stage.')
        observed=self.review.observe(location)
        result=self.client.call('generation-validate',dict(companion_root=str(root),job_id=jid,project_id=observed.project_id,
            expected_revision=observed.value['checkpoint']['revision'],output_index=int(parts[1]),previous_fingerprints=list(previous_fingerprints),saved_reference=saved_reference,migration_approved=migration_approved))
        value=read_artifact(root,result['artifact'])
        wire.fields(value,('schema','version','job_id','checkpoint','result_sha256','report','registration','reference','source','depth_mode','authority_sha256','output_sha256'))
        if value['schema']!='vta-validated-native-output' or type(value['version']) is not int or value['version']!=1 or value['job_id']!=jid or value['checkpoint']!=observed.value['checkpoint']:
            raise ValueError('Output validation identity changed.')
        return value
