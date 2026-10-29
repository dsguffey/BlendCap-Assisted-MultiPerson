"""Explicit generation/provider job vocabulary. SPDX-License-Identifier: CC0-1.0"""
import math
from . import fields,require,text,frame,digest,identifier,checkpoint,revision,path_form
from .domain import ids

CONTROL_OPS=('generation-prepare','analysis-prepare','job-start','job-status','job-cancel',
             'job-decision','job-result','job-application','generation-validate','retarget-bindings','retarget-export')
STATES=('prepared','starting','running','awaiting-decision','ready','accepted','cancelled','failed','unknown')
COMMON=('companion_root','project_id','expected_revision','source_id','source_path')
RUNTIME_FIELDS=('public_root','body_root','body_python','analysis_python','kinesia_root','sam3_checkpoint')

def pair(value):
    require(type(value) is list and len(value)==2,'Expected frame range.')
    for n in value:frame(n)
    require(value[0]<=value[1] and value[1]-value[0]<65536,'Reversed or excessive frame range.')

def runtime(value):
    # Live launch configuration only. Never serialized into a saved job/project.
    fields(value,RUNTIME_FIELDS)
    for item in value.values():path_form(item)

def request(op,p):
    if op not in CONTROL_OPS:return False
    if op=='retarget-bindings':
        fields(p,('companion_root',));path_form(p['companion_root']);return True
    if op=='retarget-export':
        fields(p,('companion_root','project_id','expected_revision','source_id','input_id','input_sha256','input_bytes'))
        path_form(p['companion_root']);text(p['project_id']);revision(p['expected_revision']);text(p['source_id'])
        identifier(p['input_id']);digest(p['input_sha256']);require(type(p['input_bytes']) is int and 0<p['input_bytes']<=32*1024*1024,'Invalid motion input size.');return True
    if op in ('generation-prepare','analysis-prepare'):
        extra=('character_ids','frame_range','fov_mode','depth_mode','previous_fingerprints','frame_offset') if op=='generation-prepare' else ('frame_range',)
        fields(p,(*COMMON,*extra));text(p['project_id']);revision(p['expected_revision']);text(p['source_id']);path_form(p['source_path']);pair(p['frame_range'])
        if op=='generation-prepare':
            ids(p['character_ids'],128);require(bool(p['character_ids']),'Select Characters.')
            require(p['fov_mode'] in ('FIRST','EVERY') and p['depth_mode'] in ('FIXED','AUTOMATIC'),'Invalid generation settings.')
            ids(p['previous_fingerprints'],128);require(type(p['frame_offset']) is int and abs(p['frame_offset'])<=2**31-1,'Invalid native frame offset.')
    else:
        extra={'job-start':('runtime',),'job-status':(),'job-cancel':(),
               'job-decision':('accept','frame_range'),'job-result':(),
               'generation-validate':('project_id','expected_revision','output_index','previous_fingerprints','saved_reference','migration_approved'),
               'job-application':('application_id','state','artifact_sha256')}[op]
        fields(p,('companion_root','job_id',*extra));identifier(p['job_id'])
        if op=='job-start':runtime(p['runtime'])
        if op=='generation-validate':
            text(p['project_id']);revision(p['expected_revision']);require(type(p['output_index']) is int and 0<=p['output_index']<=1,'Invalid output stage.')
            ids(p['previous_fingerprints'],128);require(type(p['migration_approved']) is bool,'Invalid migration approval.')
            if p['saved_reference'] is not None:
                fields(p['saved_reference'],('reference','source_video_sha256','output_file','output_sha256'));digest(p['saved_reference']['source_video_sha256']);path_form(p['saved_reference']['output_file']);digest(p['saved_reference']['output_sha256']);finite_tree(p['saved_reference']['reference'],nodes=1024)
        if op=='job-decision':
            require(type(p['accept']) is bool,'Invalid decision.')
            if p['accept']:require(p['frame_range'] is not None,'Accepted calibration needs an explicit range.')
            if p['frame_range'] is not None:pair(p['frame_range'])
        if op=='job-application':
            identifier(p['application_id']);digest(p['artifact_sha256']);require(p['state'] in ('applied','failed'),'Invalid application state.')
    path_form(p['companion_root']);return True

def status(value):
    fields(value,('job_id','kind','project_id','source_id','input_revision','state','phase','progress','cancel_requested',
                  'result','error','application','pid'))
    identifier(value['job_id']);require(value['kind'] in ('generation','analysis'),'Invalid job kind.')
    text(value['project_id']);text(value['source_id']);revision(value['input_revision'])
    require(value['state'] in STATES,'Invalid job state.');text(value['phase'],128)
    if value['progress'] is not None:
        fields(value['progress'],('completed','total'))
        require(all(type(n) is int for n in value['progress'].values()) and 0<=value['progress']['completed']<=value['progress']['total']<=2**31-1,'Invalid progress.')
    require(type(value['cancel_requested']) is bool,'Invalid cancellation.')
    if value['result'] is not None:manifest(value['result'])
    if value['error'] is not None:
        fields(value['error'],('code','message'));text(value['error']['code'],64);text(value['error']['message'],900)
    if value['pid'] is not None:require(type(value['pid']) is int and value['pid']>0,'Invalid PID.')
    if value['application'] is not None:
        fields(value['application'],('application_id','state','artifact_sha256'));identifier(value['application']['application_id'])
        require(value['application']['state'] in ('applied','failed'),'Invalid application.');digest(value['application']['artifact_sha256'])
    return value

def manifest(value):
    fields(value,('job_id','name','sha256','bytes'))
    identifier(value['job_id']);require(value['name'] in ('plan.json','result.json','validated.json'),'Invalid job artifact name.')
    digest(value['sha256']);require(type(value['bytes']) is int and 0<value['bytes']<=32*1024*1024,'Invalid job artifact size.')

def response(op,value):
    if op=='retarget-bindings':
        fields(value,('bindings',));require(type(value['bindings']) is list and 0<len(value['bindings'])<=64,'Invalid source bindings.')
        seen=set()
        for row in value['bindings']:
            fields(row,('id','parent','bone','endpoint','optional'));text(row['id']);text(row['bone'])
            for name in ('parent','endpoint'):
                if row[name] is not None:text(row[name])
            require(type(row['optional']) is bool and row['id'] not in seen,'Invalid/duplicate binding.');seen.add(row['id'])
    elif op=='retarget-export':
        fields(value,('artifact_id','sha256','bytes','checkpoint'));identifier(value['artifact_id']);digest(value['sha256']);checkpoint(value['checkpoint'])
        require(type(value['bytes']) is int and 0<value['bytes']<=32*1024*1024,'Invalid motion result size.')
    elif op=='generation-validate':fields(value,('artifact',));manifest(value['artifact'])
    elif op=='job-result':
        fields(value,('status','artifact'));status(value['status'])
        if value['artifact'] is not None:manifest(value['artifact'])
    elif op in ('generation-prepare','analysis-prepare'):
        fields(value,('status','plan'));status(value['status']);manifest(value['plan'])
    else:status(value)

def finite_tree(value,*,nodes=2000000,depth=24):
    """Bound neutral artifacts before any private interpretation/native apply."""
    budget=[nodes]
    def visit(item,level):
        budget[0]-=1;require(budget[0]>=0 and level<=depth,'Artifact structure exceeds bounds.')
        if isinstance(item,dict):
            require(all(type(k) is str and len(k)<=256 for k in item),'Invalid artifact keys.')
            for v in item.values():visit(v,level+1)
        elif isinstance(item,list):
            for v in item:visit(v,level+1)
        elif type(item) is float:require(math.isfinite(item),'Nonfinite artifact value.')
        elif type(item) is str:require(len(item)<=8192,'Artifact text exceeds bounds.')
        else:require(item is None or type(item) in (int,bool),'Artifact is not neutral JSON.')
    visit(value,0);return value
