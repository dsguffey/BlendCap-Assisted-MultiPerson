"""Optional, disposable committed review projection. SPDX-License-Identifier: CC0-1.0"""
import hashlib
from . import fields,require,path_form,frame,digest,identifier,dumps,request,response,validate_response

OPS=('review-batch','review-assign','review-reassign','candidate-suppress')

def context(value,operation,payload):
    require(operation in OPS,'Presentation is unavailable for this operation.')
    fields(value,('source_path','source_frame'))
    path_form(value['source_path']);frame(value['source_frame'])
    if operation=='review-batch':require(payload['action'] in ('assign','suppress'),'Presentation unavailable for this batch action.')

def fingerprint(request_id,operation,payload):
    return hashlib.sha256(dumps(request(request_id,payload,operation))).hexdigest()

def token(value):
    fields(value,('size','mtime_ns','file_id'))
    for key,maximum in (('size',2**63-1),('mtime_ns',2**64-1),('file_id',2**128-1)):
        require(type(value[key]) is int and 0<=value[key]<=maximum,'Invalid snapshot input token.')

def validate(value,operation,payload,receipt):
    require('presentation' in payload,'Unrequested presentation.')
    context(payload['presentation'],operation,payload)
    fields(value,('version','request_id','request_sha256','head_sha256','inputs','observation'))
    require(type(value['version']) is int and value['version']==1,'Unsupported presentation descriptor.')
    identifier(value['request_id']);digest(value['request_sha256']);digest(value['head_sha256'])
    require(value['request_id']==receipt['request_id'] and value['request_sha256']==fingerprint(receipt['request_id'],operation,payload),'Presentation request scope mismatch.')
    inputs=value['inputs'];fields(inputs,('project','head','source','analysis'))
    for item in inputs.values():token(item)
    manifest=value['observation']
    validate_response(response(receipt['request_id'],'review-observation',result=manifest),receipt['request_id'],'review-observation')
    require(manifest['checkpoint']==receipt['after'] and manifest['source_id']==payload['source_id'],'Presentation committed identity mismatch.')
    frames=([r['frame'] for r in payload['items']] if operation=='review-batch' else [payload['source_frame']])
    frames=[*frames,payload['presentation']['source_frame']]
    require(all(manifest['coverage'][0]<=n<=manifest['coverage'][1] for n in frames),'Presentation does not cover the requested frames.')
    return value
