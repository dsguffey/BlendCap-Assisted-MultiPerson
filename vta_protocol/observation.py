"""Purpose-built bounded review presentation artifact; CC0-1.0."""
import json,math
from . import fields,require,text,frame,digest,checkpoint
MAX_BYTES=32*1024*1024
MAX_FRAMES=65536
STATES=('visible','partially-visible','occluded','no-valid-candidate','not-visible','unresolved','not-analyzed')

def sequence(value,maximum):
    require(type(value) is list and len(value)<=maximum,'Observation collection exceeds bounds.')
    return value

def optional_id(value):
    if value is not None:text(value)

def member_ids(value,characters):
    sequence(value,1024)
    for item in value:text(item)
    require(len(set(value))==len(value) and set(value)<=characters,'Invalid Character membership.')

def state(value):require(value in STATES,'Invalid review state.')

def source(value):
    fields(value,('source_id','fps','start_frame','end_frame','video_sha256','width','height','coordinate_contract'))
    text(value['source_id']);digest(value['video_sha256']);frame(value['start_frame']);frame(value['end_frame'])
    require(value['start_frame']<=value['end_frame'],'Invalid source bounds.')
    require(type(value['fps']) in (int,float) and math.isfinite(value['fps']) and value['fps']>0,'Invalid FPS.')
    for key in ('width','height'):require(type(value[key]) is int and 0<value[key]<=100000,'Invalid dimensions.')
    require(value['coordinate_contract']==dict(box_format='xyxy',units='pixels',origin='lower-left'),'Invalid coordinates.')

def frame_view(value,characters,source_id,revision_id):
    fields(value,('analysis_revision_id','source_id','frame','state','candidates','characters',
        'character_assignments','character_decisions','derived_tracking','pose_rejected_character_ids'))
    require(value['source_id']==source_id and value['analysis_revision_id']==revision_id,'Frame identity mismatch.')
    frame(value['frame']);require(value['state'] in ('not-analyzed','analyzed-empty','analyzed-with-candidates'),'Invalid analysis state.')
    # Characters are shared at artifact scope; do not repeat names in each frame.
    require(value['characters'] is None,'Unexpected per-frame Character document.')
    seen=set()
    for row in sequence(value['candidates'],1024):
        fields(row,('analysis_revision_id','observation_id','box_xyxy','suppressed'))
        require(row['analysis_revision_id']==revision_id,'Candidate revision mismatch.')
        text(row['observation_id']);require(row['observation_id'] not in seen,'Duplicate candidate.')
        seen.add(row['observation_id']);sequence(row['box_xyxy'],4)
        require(len(row['box_xyxy'])==4 and all(type(x) in (float,int) and math.isfinite(x) for x in row['box_xyxy']),'Invalid box.')
        require(row['box_xyxy'][0]<row['box_xyxy'][2] and row['box_xyxy'][1]<row['box_xyxy'][3],'Invalid box extent.')
        require(type(row['suppressed']) is bool,'Invalid suppression.')
    for field,keys in (('character_assignments',('character_id','observation_id')),
                       ('character_decisions',('character_id','keyframe_id','state','observation_id')),
                       ('derived_tracking',('character_id','state','observation_id'))):
        ids=set()
        for row in sequence(value[field],1024):
            fields(row,keys);require(row['character_id'] in characters and row['character_id'] not in ids,'Invalid Character decision identity.')
            ids.add(row['character_id']);optional_id(row['observation_id'])
            if 'state' in row:state(row['state'])
            if 'keyframe_id' in row:text(row['keyframe_id'])
    member_ids(value['pose_rejected_character_ids'],characters)

def validate(value):
    fields(value,('schema','version','checkpoint','source','analysis_revision_id','cache_sha256',
                  'characters','source_member_ids','frames','timeline','coverage','generation_authority_sha256'))
    require(value['schema']=='vta-review-observation' and type(value['version']) is int and value['version']==1,'Unsupported observation schema.')
    digest(value['generation_authority_sha256'])
    checkpoint(value['checkpoint']);source(value['source']);optional_id(value['analysis_revision_id'])
    if value['cache_sha256'] is not None:digest(value['cache_sha256'])
    characters=set()
    for row in sequence(value['characters'],1024):
        fields(row,('character_id','display_name'));text(row['character_id']);text(row['display_name'],8192)
        require(row['character_id'] not in characters,'Duplicate Character.');characters.add(row['character_id'])
    member_ids(value['source_member_ids'],characters)
    coverage=sequence(value['coverage'],2);require(len(coverage)==2,'Invalid observation coverage.')
    frame(coverage[0]);frame(coverage[1])
    require(value['source']['start_frame']<=coverage[0]<=coverage[1]<=value['source']['end_frame'],'Invalid observation window.')
    seen=set()
    for item in sequence(value['frames'],MAX_FRAMES):
        frame_view(item,characters,value['source']['source_id'],value['analysis_revision_id'])
        require(coverage[0]<=item['frame']<=coverage[1] and item['frame'] not in seen,'Duplicate/outside frame.')
        seen.add(item['frame'])
    require(len(seen)==coverage[1]-coverage[0]+1,'Incomplete observation window.')
    timeline=value['timeline'];fields(timeline,('source_id','keys','derived','repair_ranges'))
    require(timeline['source_id']==value['source']['source_id'],'Timeline source mismatch.')
    for field in ('keys','derived','repair_ranges'):
        for row in sequence(timeline[field],200000):
            keys={'keys':('keyframe_id','character_id','frame','state','observation_id','tracking_state'),
                  'derived':('character_id','frame','state'),
                  'repair_ranges':('character_id','start_frame','end_frame','mode')}[field]
            fields(row,keys);require(row['character_id'] in characters,'Unknown timeline Character.')
            if field=='repair_ranges':
                frame(row['start_frame']);frame(row['end_frame']);require(row['start_frame']<=row['end_frame'] and row['mode'] in ('freeze','interpolate'),'Invalid repair range.')
            else:frame(row['frame']);state(row['state'])
            if field=='keys':
                text(row['keyframe_id']);optional_id(row['observation_id']);require(row['tracking_state'] in ('pending','incorporated'),'Invalid incorporation state.')
    return value

def loads(raw):
    require(type(raw) is bytes and 0<len(raw)<=MAX_BYTES,'Observation exceeds size limit.')
    def pairs(items):
        value={}
        for k,v in items:
            require(k not in value,'Duplicate observation JSON key.');value[k]=v
        return value
    try:value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=lambda _:require(False,'Invalid number.'))
    except (ValueError,UnicodeError,RecursionError) as exc:raise ValueError('Malformed observation artifact.') from exc
    return validate(value)

def dumps(value):
    validate(value)
    raw=json.dumps(value,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('utf-8')
    require(len(raw)<=MAX_BYTES,'Observation exceeds size limit.')
    return raw
