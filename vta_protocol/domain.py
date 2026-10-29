"""Strict M3 domain command/result vocabulary; CC0-1.0."""
import math
from . import fields,require,text,frame,revision,digest,identifier,checkpoint
from .observation import sequence,optional_id

MUTATIONS=('character-rename','character-create','review-assign','review-reassign','review-hard-state',
           'review-management','candidate-suppress','tracking-update','repair-edit','review-restore-last','analysis-register','generation-accept')
DETAIL_OPS=tuple(op for op in MUTATIONS if op not in ('character-rename','character-create'))
CACHE=('source_id','source_frame','analysis_revision_id','cache_sha256')
EXTRA={
 'character-create':('display_name',),
 'analysis-register':('job_id','descriptor_sha256'),
 'generation-accept':('job_id','artifact_sha256'),
 'review-management':('action','character_id','source_id','frame','keyframe_ids','confirmed','session_id'),
 'candidate-suppress':(*CACHE,'observation_id','suppressed','confirmed','session_id'),
 'tracking-update':(*CACHE,'character_ids','session_id'),
 'tracking-plan':(*CACHE,'character_ids','session_id'),
 'repair-edit':('source_id','character_ids','start_frame','end_frame','action','mode','previous_range','session_id'),
 'review-restore-last':('restore_id','session_id'),
}

def ids(value,maximum):
    sequence(value,maximum)
    for item in value:text(item)
    require(len(set(value))==len(value),'Duplicate IDs.')

def request_names(op):
    return ('project_id','expected_revision',*EXTRA[op]) if op in EXTRA else None

def validate_payload(op,p):
    if op not in EXTRA:return
    if 'session_id' in p:identifier(p['session_id'])
    if op in ('analysis-register','generation-accept'):
        identifier(p['job_id']);digest(p['descriptor_sha256'] if op=='analysis-register' else p['artifact_sha256'])
    if op=='review-management':
        require(p['action'] in ('clear-assignment','clear-review-key','clear-selected-review-keys','clear-all-review-keys-on-frame','delete-character'),'Invalid management action.')
        optional_id(p['character_id']);optional_id(p['source_id'])
        if p['frame'] is not None:frame(p['frame'])
        ids(p['keyframe_ids'],512);require(type(p['confirmed']) is bool,'Invalid confirmation.')
    if op in ('candidate-suppress','tracking-update','tracking-plan'):
        text(p['source_id']);frame(p['source_frame']);text(p['analysis_revision_id']);digest(p['cache_sha256'])
    if op=='candidate-suppress':
        text(p['observation_id']);require(type(p['suppressed']) is bool and type(p['confirmed']) is bool,'Invalid suppression/confirmation.')
    if op in ('tracking-update','tracking-plan','repair-edit'):
        ids(p['character_ids'],128);require(bool(p['character_ids']),'Select Characters.')
    if op=='repair-edit':
        text(p['source_id']);frame(p['start_frame']);frame(p['end_frame']);require(p['start_frame']<=p['end_frame'],'Reversed repair range.')
        require(p['action'] in ('mark','clear','change-mode','resize'),'Invalid repair action.')
        require(p['mode'] in (None,'freeze','interpolate'),'Invalid repair mode.')
        if p['previous_range'] is not None:
            sequence(p['previous_range'],2);require(len(p['previous_range'])==2,'Invalid prior range.')
            frame(p['previous_range'][0]);frame(p['previous_range'][1]);require(p['previous_range'][0]<=p['previous_range'][1],'Reversed prior range.')
    if op=='review-restore-last':identifier(p['restore_id'])

def scope(value):
    fields(value,('character_id','source_id','edited_frame','start_frame','end_frame','left_boundary','right_boundary','fallback_horizon_seconds'))
    text(value['character_id']);text(value['source_id'])
    for key in ('edited_frame','start_frame','end_frame'):frame(value[key])
    require(value['start_frame']<=value['end_frame'],'Reversed tracking scope.')
    for side in ('left_boundary','right_boundary'):
        boundary=value[side];fields(boundary,('frame','kind','keyframe_id'));frame(boundary['frame']);text(boundary['kind']);optional_id(boundary['keyframe_id'])
    number=value['fallback_horizon_seconds'];require(type(number) in (float,int) and math.isfinite(number) and 0<number<=300,'Invalid horizon.')

def update(value):
    fields(value,('revision_id','scope','matched','unresolved','conflicts','ambiguities','scopes'))
    text(value['revision_id']);scope(value['scope'])
    for key in ('matched','unresolved','conflicts'):require(type(value[key]) is int and 0<=value[key]<=2**31-1,'Invalid tracking count.')
    for item in sequence(value['scopes'],512):scope(item)
    for item in sequence(value['ambiguities'],1024):
        fields(item,('frame','direction','reason'));frame(item['frame']);require(item['direction'] in ('forward','backward','both'),'Invalid direction.');text(item['reason'],1024)

def validate_details(op,item):
    if op in ('review-assign','review-reassign','review-hard-state'):
        fields(item,('character_id','source_id','frame','keyframe_id'))
        for key in ('character_id','source_id','keyframe_id'):text(item[key])
        frame(item['frame']);return
    if op=='review-management':
        fields(item,('action','character_id','keyframe_ids','restore_id'));text(item['action']);optional_id(item['character_id']);ids(item['keyframe_ids'],512)
    elif op=='candidate-suppress':
        fields(item,('analysis_revision_id','observation_id','suppressed','restore_id'))
        text(item['analysis_revision_id']);text(item['observation_id']);require(type(item['suppressed']) is bool,'Invalid suppression receipt.')
    elif op=='tracking-update':
        fields(item,('outcomes','restore_id'))
        seen=set()
        for outcome in sequence(item['outcomes'],128):
            fields(outcome,('character_id','update','error'));text(outcome['character_id'])
            require(outcome['character_id'] not in seen,'Duplicate outcome.');seen.add(outcome['character_id'])
            if outcome['update'] is None:text(outcome['error'],1024)
            else:
                update(outcome['update']);require(outcome['error'] is None,'Conflicting tracking outcome.')
    elif op=='repair-edit':
        fields(item,('updates','restore_id'))
        for row in sequence(item['updates'],128):
            fields(row,('character_id','source_id','action','intervals'));text(row['character_id']);text(row['source_id']);text(row['action'])
            for interval in sequence(row['intervals'],1024):
                fields(interval,('start_frame','end_frame','mode'));frame(interval['start_frame']);frame(interval['end_frame']);require(interval['start_frame']<=interval['end_frame'] and interval['mode'] in ('freeze','interpolate'),'Invalid repair interval.')
    elif op in ('analysis-register','generation-accept'):
        fields(item,('job_id','identity'));identifier(item['job_id']);text(item['identity'])
    elif op=='review-restore-last':fields(item,('restore_id',))
    else:require(False,'Unknown details operation.')
    if item.get('restore_id') is not None:identifier(item['restore_id'])

def validate_plan(item):
    fields(item,('checkpoint','outcomes'));checkpoint(item['checkpoint'])
    for row in sequence(item['outcomes'],128):
        fields(row,('character_id','scopes','error'));text(row['character_id'])
        if row['error'] is not None:text(row['error'],1024)
        for value in sequence(row['scopes'],512):scope(value)

def validate_target(operation,payload,result):
    """Bind a valid response to the submitted revision, scope and explicit IDs."""
    require(result['project_id']==payload['project_id'] and result['before']['revision']==payload['expected_revision'],
            'Receipt project/revision does not match request.')
    if operation=='character-rename':require(result['character']['character_id']==payload['character_id'],'Rename target mismatch.')
    elif operation=='character-create':require(result['character']['display_name']==payload['display_name'].strip(),'Created Character name mismatch.')
    elif operation in ('review-assign','review-reassign','review-hard-state'):
        item=result['details'];require(item['character_id']==payload['character_id'] and item['source_id']==payload['source_id']
            and item['frame']==payload['source_frame'],'Review target mismatch.')
    elif operation=='review-management':
        item=result['details'];require(item['action']==payload['action'] and item['character_id']==payload['character_id'],'Management target mismatch.')
        # Delete computes the full multi-source dependency closure privately.
        # Only explicit key-selection commands carry an exact key target list.
        if payload['action'] in ('clear-review-key','clear-selected-review-keys'):
            require(set(item['keyframe_ids'])==set(payload['keyframe_ids']),'Management key targets mismatch.')
    elif operation=='candidate-suppress':
        item=result['details'];require(all(item[k]==payload[k] for k in ('analysis_revision_id','observation_id','suppressed')),'Suppression target mismatch.')
    elif operation=='tracking-update':
        outcomes=result['details']['outcomes'];require({r['character_id'] for r in outcomes}==set(payload['character_ids']),'Tracking targets mismatch.')
        for row in outcomes:
            if row['update'] is not None:
                for value in (row['update']['scope'],*row['update']['scopes']):
                    require(value['character_id']==row['character_id'] and value['source_id']==payload['source_id'],'Tracking scope mismatch.')
    elif operation=='repair-edit':
        rows=result['details']['updates'];require({r['character_id'] for r in rows}==set(payload['character_ids']),'Repair targets mismatch.')
        require(all(r['source_id']==payload['source_id'] and r['action']==payload['action'] for r in rows),'Repair source/action mismatch.')
    elif operation in ('analysis-register','generation-accept'):require(result['details']['job_id']==payload['job_id'],'Job receipt target mismatch.')
    elif operation=='review-restore-last':require(result['details']['restore_id']==payload['restore_id'],'Protected Restore identity mismatch.')
