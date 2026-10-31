"""Validate a disposable review reply against current committed input bytes."""
import hashlib,stat,time
from pathlib import Path
if __package__:
    from .vta_protocol import review_reply as dto
else:
    from vta_protocol import review_reply as dto

def token(path):
    value=path.stat()
    if not stat.S_ISREG(value.st_mode):raise ValueError('Review input is not a regular file.')
    return dict(size=value.st_size,mtime_ns=value.st_mtime_ns,file_id=value.st_ino)

def digest(path,expected,deadline):
    result=hashlib.sha256();remaining=expected['size']
    with path.open('rb') as stream:
        while remaining:
            if time.monotonic()>deadline:raise ValueError('Review input verification deadline exceeded.')
            chunk=stream.read(min(1024*1024,remaining))
            if not chunk:raise ValueError('Review input was truncated.')
            result.update(chunk);remaining-=len(chunk)
        if stream.read(1):raise ValueError('Review input grew.')
    return result.hexdigest()

def accept(location,operation,payload,receipt,value,reader,*,timeout=15):
    dto.validate(value,operation,payload,receipt)
    root=location.companion_root.resolve();source=location.source_path.resolve()
    context=payload['presentation']
    if (Path(payload['companion_root']).resolve()!=root or Path(context['source_path']).resolve()!=source
            or context['source_frame']!=location.source_frame):raise ValueError('Review presentation location changed.')
    revision=payload['analysis_revision_id']
    if not revision or any(c in revision for c in '/\\:'):raise ValueError('Invalid review evidence identity.')
    paths=dict(project=root/'project_state.json',head=root/'.vta-engine/head.json',source=source,analysis=root/'analysis'/(revision+'.json'))
    for key,path in paths.items():
        if key!='source' and (not path.resolve().is_relative_to(root) or path.stat().st_nlink!=1):raise ValueError('Review input escapes companion or is linked.')
    before={key:token(path) for key,path in paths.items()}
    if before!=value['inputs']:raise ValueError('Prefetched review inputs changed.')
    for key,maximum in (('project',64*1024*1024),('head',16384),('analysis',64*1024*1024)):
        if before[key]['size']>maximum:raise ValueError('Review input exceeds bounded size.')
    observation=reader(root,value['observation']);view=observation.value
    if (view['checkpoint']!=receipt['after'] or view['analysis_revision_id']!=revision
            or view['cache_sha256']!=payload['cache_sha256']):raise ValueError('Review snapshot/evidence identity mismatch.')
    expected=dict(project=receipt['after']['revision']['sha256'],head=value['head_sha256'],source=view['source']['video_sha256'],analysis=view['cache_sha256'])
    deadline=time.monotonic()+timeout
    for key,path in paths.items():
        if digest(path,before[key],deadline)!=expected[key]:raise ValueError('Review snapshot content changed: '+key)
    rows=payload['items'] if operation=='review-batch' else [dict(frame=payload['source_frame'],observation_id=payload['observation_id'])]
    assigning=operation in ('review-assign','review-reassign') or (operation=='review-batch' and payload['action']=='assign')
    for item in rows:
        frame=observation.frames[item['frame']]
        if assigning:
            if not any(r['character_id']==payload['character_id'] and r['observation_id']==item['observation_id'] for r in frame['character_assignments']):raise ValueError('Review assignment scope mismatch.')
        else:
            suppressed=payload.get('suppressed',True)
            if not any(r['observation_id']==item['observation_id'] and r['suppressed']==suppressed for r in frame['candidates']):raise ValueError('Review suppression scope mismatch.')
    if before!={key:token(path) for key,path in paths.items()}:raise ValueError('Review inputs changed during adoption.')
    observation.input_stamp=tuple((before[key]['size'],before[key]['mtime_ns']) for key in ('project','source','analysis'))
    observation.verified_project=(root,before['project'],expected['project'])
    return observation
