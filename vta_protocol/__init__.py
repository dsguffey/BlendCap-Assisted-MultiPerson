"""Neutral VTA wire vocabulary and validation; no domain, host or provider code.

SPDX-License-Identifier: CC0-1.0
Original protocol-only code, shared verbatim with the standalone engine.
"""
import json
import math
import re
import hashlib
from pathlib import Path

PROTOCOL = 'vta-engine'
VERSION = 6
OPERATIONS = ('source-stage','source-publish','camera-prepare','source-coverage','character-state','character-rename',
              'project-state','mutation-status','review-assign','review-reassign','review-hard-state','review-observation','character-create','review-management','candidate-suppress','tracking-plan','tracking-update','repair-edit','review-restore-last','generation-prepare','analysis-prepare','job-cleanup','job-start','job-status','job-cancel','job-decision','job-result','job-application','analysis-register','generation-accept','generation-validate','retarget-bindings','retarget-export')
MAX_BYTES = 16384
MAX_FRAME = 2**31-1
CODES = frozenset(('INVALID_REQUEST','VERSION_MISMATCH','UNKNOWN_OPERATION','INVALID_PATH',
    'PROJECT_UNAVAILABLE','SOURCE_UNAVAILABLE','SOURCE_MISMATCH','FRAME_OUT_OF_RANGE',
    'INPUT_CHANGED','RESOURCE_LIMIT','INTERNAL_ERROR','CONFLICT','INVALID_NAME',
    'RUNTIME_UNAVAILABLE','CHARACTER_UNAVAILABLE','PROJECT_BUSY','RECOVERY_REQUIRED','IDEMPOTENCY_CONFLICT','JOB_NOT_FOUND'))


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None,'Invalid digest.')


def revision(value):
    fields(value,('number','sha256'))
    require(type(value['number']) is int and 0<=value['number']<=2**53-1,'Invalid revision number.')
    digest(value['sha256'])


def text(value, maximum=256, *, empty=False):
    require(type(value) is str and (empty or bool(value)) and len(value)<=maximum,'Invalid bounded text.')
    require(not any(0xD800<=ord(c)<=0xDFFF for c in value),'Invalid Unicode surrogate.')


def checkpoint(value):
    fields(value,('project_id','revision'))
    text(value['project_id']);revision(value['revision'])


def receipt(value):
    fields(value,('request_id','operation','project_id','before','after','changed','character',
                  *(('details',) if value.get('operation') in DETAIL_OPS else ())))
    identifier(value['request_id']);text(value['project_id'])
    # Historical restore receipts remain readable; restore is not executable.
    require(value['operation'] in (*MUTATIONS,'review-restore'),'Invalid receipt operation.')
    checkpoint(value['before']);checkpoint(value['after'])
    require(value['before']['project_id']==value['after']['project_id']==value['project_id'],'Receipt project mismatch.')
    require(type(value['changed']) is bool,'Invalid changed flag.')
    before,after=value['before']['revision'],value['after']['revision']
    require(after['number']==before['number']+int(value['changed']),'Invalid revision transition.')
    require((before['sha256']!=after['sha256'])==value['changed'],'Invalid receipt change.')
    if value['operation'] in ('character-rename','character-create'):
        fields(value['character'],('character_id','display_name'))
        text(value['character']['character_id']);text(value['character']['display_name'],8192)
    else:
        require(value['character'] is None,'Unexpected Character payload.')
        if value['operation']!='review-restore':validate_details(value['operation'],value['details'])


class ProtocolError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, message, code='INVALID_REQUEST'):
    if not condition:
        raise ProtocolError(code, message)


def fields(value, names):
    require(type(value) is dict and set(value) == set(names), 'Unexpected or missing fields.')


def identifier(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{32}', value) is not None,
            'Request ID must be 32 lowercase hex characters.')
    return value


def frame(value):
    require(type(value) is int and 0 <= value <= MAX_FRAME, 'Invalid source frame.')
    return value


def path_form(value):
    require(type(value) is str and 0 < len(value) <= 4096 and not any(ord(c)<32 for c in value),
            'Invalid path string.', 'INVALID_PATH')
    p = Path(value)
    require(p.is_absolute() and '..' not in p.parts and not value.startswith(('\\\\','//')),
            'An absolute local path without traversal/device/network syntax is required.', 'INVALID_PATH')
    # Reject Windows ADS; a single drive colon is the only accepted colon there.
    require(not (p.drive and ':' in value[len(p.drive):]), 'Alternate stream path rejected.', 'INVALID_PATH')
    return p


def timeout_value(value):
    require(type(value) in (float,int) and math.isfinite(value) and .05 <= value <= 300,
            'Timeout must be finite and between .05 and 300 seconds.')
    return float(value)


def loads(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, 'Invalid message size.')
    def pairs(items):
        result = {}
        for k,v in items:
            require(k not in result, 'Duplicate JSON key.')
            result[k] = v
        return result
    def invalid(_):
        raise ProtocolError('INVALID_REQUEST','Nonfinite JSON value.')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)
        nodes = [value]; count = 0
        def visit(v, depth=0):
            nonlocal count
            count += 1
            require(count <= 2048 and depth <= 12, 'Message complexity limit exceeded.')
            require(type(v) in (dict,list,str,int,float,bool,type(None)), 'Unsupported serialized value.')
            if type(v) is float:require(math.isfinite(v),'Nonfinite number.')
            if type(v) is dict:
                for x in v.values():visit(x,depth+1)
            elif type(v) is list:
                for x in v:visit(x,depth+1)
        visit(value)
        require(type(value) is dict,'Message must be an object.')
        return value
    except (UnicodeError,ValueError,RecursionError,OverflowError) as exc:
        if isinstance(exc,ProtocolError):raise
        raise ProtocolError('INVALID_REQUEST','Malformed JSON message.') from exc


def dumps(value):
    try:
        raw = json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()+b'\n'
        loads(raw)  # Same resource/type limits on either side of the wire.
        return raw
    except (ValueError,TypeError,RecursionError) as exc:
        if isinstance(exc,ProtocolError):raise
        raise ProtocolError('INVALID_REQUEST','Value is not a neutral JSON message.') from exc


def envelope(value, kind):
    require(value.get('protocol') == PROTOCOL and type(value.get('version')) is int
            and value['version'] == VERSION, 'Unsupported protocol version.', 'VERSION_MISMATCH')
    require(value.get('kind') == kind, 'Invalid message kind.')


def request(request_id, payload, operation='source-coverage'):
    value = dict(protocol=PROTOCOL,version=VERSION,kind='request',request_id=request_id,
                 operation=operation,payload=payload)
    validate_request(value)
    return value


def validate_request(value):
    fields(value,('protocol','version','kind','request_id','operation','payload'))
    envelope(value,'request');identifier(value['request_id'])
    require(type(value['operation']) is str and value['operation'] in OPERATIONS,
            'Unknown operation.', 'UNKNOWN_OPERATION')
    payload=value['payload'];operation=value['operation']
    if validate_bootstrap_request(operation,payload):return value
    if validate_job_request(operation,payload):return value
    names={'source-coverage':('source_path','source_frame'),
        'character-state':('character_id',),'project-state':(),
        'character-rename':('project_id','character_id','display_name','expected_revision'),
        'mutation-status':('mutation_id',),'review-observation':('source_path','source_frame'),
        **{op:('project_id','expected_revision','source_id','source_frame','analysis_revision_id',
               'cache_sha256','character_id',*extra) for op,extra in (
            ('review-assign',('observation_id',)),('review-reassign',('observation_id','confirmed')),
            ('review-hard-state',('state','confirmed')))},
        **{op:request_names(op) for op in EXTRA}}[operation]
    fields(payload,('companion_root',*names));path_form(payload['companion_root'])
    if operation in ('source-coverage','review-observation'):path_form(payload['source_path']);frame(payload['source_frame'])
    if 'character_id' in payload and operation!='review-management':text(payload['character_id'])
    if 'project_id' in payload:text(payload['project_id'])
    if 'expected_revision' in payload:revision(payload['expected_revision'])
    if 'display_name' in payload:text(payload['display_name'],8192,empty=True)
    if 'mutation_id' in payload:identifier(payload['mutation_id'])
    if operation in ('review-assign','review-reassign','review-hard-state'):
        text(payload['source_id']);frame(payload['source_frame']);text(payload['analysis_revision_id'])
        digest(payload['cache_sha256'])
        if 'observation_id' in payload:text(payload['observation_id'])
        if 'confirmed' in payload:require(type(payload['confirmed']) is bool,'Invalid confirmation.')
        if 'state' in payload:require(payload['state'] in ('occluded','no-valid-candidate','not-visible'),'Invalid hard boundary.')
    validate_payload(operation,payload)
    return value


def ready(pid):
    return dict(protocol=PROTOCOL,version=VERSION,kind='ready',pid=pid,operations=list(OPERATIONS),
                definition_sha256=hashlib.sha256(b''.join((Path(__file__).parent/name).read_bytes() for name in ('__init__.py','observation.py','domain.py','jobs.py','bootstrap.py','rotation.py','runtime.py','process.py'))).hexdigest())


def validate_ready(value,pid):
    fields(value,('protocol','version','kind','pid','operations','definition_sha256'));envelope(value,'ready')
    require(type(value['pid']) is int and value['pid']==pid and value['pid']>0,'Invalid child identity.')
    require(value['operations']==list(OPERATIONS),'Incompatible engine capabilities.')
    require(value['definition_sha256']==ready(pid)['definition_sha256'],'Protocol definitions differ.','VERSION_MISMATCH')


def response(request_id, operation, *, result=None, error=None):
    value=dict(protocol=PROTOCOL,version=VERSION,kind='response',request_id=request_id,
               operation=operation,status='error' if error is not None else 'ok')
    value['error' if error is not None else 'result']=error if error is not None else result
    return value


def validate_response(value, request_id, operation):
    require(type(value) is dict,'Response must be an object.')
    error=value.get('status')=='error'
    fields(value,('protocol','version','kind','request_id','operation','status','error' if error else 'result'))
    envelope(value,'response')
    require(value['request_id']==request_id and value['operation']==operation,'Response identity mismatch.')
    require(value['status'] in ('ok','error'),'Invalid response status.')
    if error:
        item=value['error'];fields(item,('code','message'))
        require(type(item['code']) is str and item['code'] in CODES,'Unknown error code.')
        require(type(item['message']) is str and 0<len(item['message'])<=1024,'Invalid error message.')
    elif operation in BOOTSTRAP_OPS:validate_bootstrap_response(operation,value['result'])
    elif operation=='source-coverage':
        fields(value['result'],('source_range',))
        bounds=value['result']['source_range']
        require(type(bounds) is list and len(bounds)==2,'Invalid source range.')
        frame(bounds[0]);frame(bounds[1]);require(bounds[0]<=bounds[1],'Reversed source range.')
    elif operation=='review-observation':
        item=value['result'];fields(item,('checkpoint','artifact_id','sha256','bytes','source_id','coverage'))
        checkpoint(item['checkpoint']);identifier(item['artifact_id']);digest(item['sha256']);text(item['source_id'])
        require(type(item['bytes']) is int and 0<item['bytes']<=32*1024*1024,'Invalid observation size.')
        require(type(item['coverage']) is list and len(item['coverage'])==2,'Invalid observation coverage.')
        frame(item['coverage'][0]);frame(item['coverage'][1]);require(item['coverage'][0]<=item['coverage'][1],'Reversed observation window.')
    elif operation=='tracking-plan':validate_plan(value['result'])
    elif operation in JOB_CONTROL_OPS:validate_job_response(operation,value['result'])
    elif operation=='project-state':checkpoint(value['result'])
    elif operation=='character-state':
        item=value['result'];fields(item,('checkpoint','character_id','display_name'))
        checkpoint(item['checkpoint']);text(item['character_id']);text(item['display_name'],8192)
    elif operation in MUTATIONS:
        receipt(value['result'])
        require(value['result']['request_id']==request_id and value['result']['operation']==operation,'Receipt identity mismatch.')
    elif operation=='mutation-status':
        item=value['result'];fields(item,('mutation_id','state','receipt'))
        identifier(item['mutation_id']);require(item['state'] in ('absent','not-applied','applied'),'Invalid mutation status.')
        if item['state']=='applied':
            receipt(item['receipt']);require(item['receipt']['request_id']==item['mutation_id'],'Status receipt mismatch.')
        else:require(item['receipt'] is None,'Unexpected receipt.')
    else:raise ProtocolError('UNKNOWN_OPERATION','Unknown response operation.')
    return value


from .domain import MUTATIONS,DETAIL_OPS,EXTRA,request_names,validate_payload,validate_details,validate_plan,validate_target

from .jobs import CONTROL_OPS as JOB_CONTROL_OPS,request as validate_job_request,response as validate_job_response

from .bootstrap import OPS as BOOTSTRAP_OPS,request as validate_bootstrap_request,response as validate_bootstrap_response
