"""Source bootstrap and camera preflight contract. SPDX-License-Identifier: CC0-1.0"""
import math
from . import fields,require,path_form,text,digest,identifier
from .rotation import validate_request
OPS=('source-stage','source-publish','camera-prepare')
def request(op,p):
    if op not in OPS:return False
    if op=='source-stage':
        fields(p,('companion_root','source_path','fps','count','width','height'))
        path_form(p['source_path']);require(type(p['fps']) in (int,float) and math.isfinite(p['fps']) and p['fps']>0,'Invalid source rate.')
        for key in ('count','width','height'):require(type(p[key]) is int and 0<p[key]<=2**31-1,'Invalid source metadata.')
    elif op=='source-publish':fields(p,('companion_root','stage_id'));identifier(p['stage_id'])
    else:
        fields(p,('companion_root','source_path','source_id','project_sha256','cache_sha256','reconstruction'))
        path_form(p['source_path']);text(p['source_id']);digest(p['project_sha256']);digest(p['cache_sha256'])
        from .jobs import finite_tree
        finite_tree(p['reconstruction'],nodes=1024)
    path_form(p['companion_root']);return True
def response(op,p):
    if op.startswith('source-'):
        fields(p,('stage_id','project_id','source','analysis_revision_id','character_ids'))
        identifier(p['stage_id']);text(p['project_id'])
        from .observation import source
        source(p['source'])
        text(p['source']['source_id']);digest(p['source']['video_sha256'])
        require(type(p['character_ids']) is list and len(p['character_ids'])<=128,'Invalid source membership.')
        for cid in p['character_ids']:text(cid)
        if p['analysis_revision_id'] is not None:text(p['analysis_revision_id'])
    else:
        fields(p,('revision','calibration','start_frame','end_frame'));text(p['revision']);digest(p['calibration'])
        require(type(p['start_frame']) is int and type(p['end_frame']) is int and 0<=p['start_frame']<=p['end_frame'],'Invalid camera coverage.')
