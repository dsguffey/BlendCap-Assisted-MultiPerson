"""Neutral VTA wire vocabulary and validation; no domain, host or provider code.

SPDX-License-Identifier: CC0-1.0
Original protocol-only code, shared verbatim with the standalone engine.
"""
import json
import math
import re
from pathlib import Path

PROTOCOL = 'vta-engine'
VERSION = 1
OPERATIONS = ('source-coverage',)
MAX_BYTES = 16384
MAX_FRAME = 2**31-1
CODES = frozenset(('INVALID_REQUEST','VERSION_MISMATCH','UNKNOWN_OPERATION','INVALID_PATH',
    'PROJECT_UNAVAILABLE','SOURCE_UNAVAILABLE','SOURCE_MISMATCH','FRAME_OUT_OF_RANGE',
    'INPUT_CHANGED','RESOURCE_LIMIT','INTERNAL_ERROR'))


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
    payload=value['payload'];fields(payload,('companion_root','source_path','source_frame'))
    path_form(payload['companion_root']);path_form(payload['source_path']);frame(payload['source_frame'])
    return value


def ready(pid):
    return dict(protocol=PROTOCOL,version=VERSION,kind='ready',pid=pid,operations=list(OPERATIONS))


def validate_ready(value,pid):
    fields(value,('protocol','version','kind','pid','operations'));envelope(value,'ready')
    require(type(value['pid']) is int and value['pid']==pid and value['pid']>0,'Invalid child identity.')
    require(value['operations']==list(OPERATIONS),'Incompatible engine capabilities.')


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
    else:
        fields(value['result'],('source_range',))
        bounds=value['result']['source_range']
        require(type(bounds) is list and len(bounds)==2,'Invalid source range.')
        frame(bounds[0]);frame(bounds[1]);require(bounds[0]<=bounds[1],'Reversed source range.')
    return value
