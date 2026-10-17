"""Scene membership of canonical Character IDs, not a second identity model."""
from dataclasses import replace
import json

KEY='vta_scene_characters'


def stored(scene):
    if not scene.get('vta_project_id') or not scene.get('vta_source_id'):return None
    raw=scene.get(KEY)
    if raw is None:return None
    value=json.loads(raw)
    if value.get('version')!=1:
        raise ValueError('Scene Character membership is invalid; reopen the known-good file.')
    if (value.get('project_id'),value.get('source_id'))==(scene.get('vta_project_id'),scene.get('vta_source_id')):
        ids=value.get('ids')
    else:ids=value.get('history',{}).get(json.dumps([scene.get('vta_project_id'),scene.get('vta_source_id')]))
    if ids is None:return None
    if not isinstance(ids,list) or any(not isinstance(i,str) for i in ids):
        raise ValueError('Scene Character membership is invalid; reopen the known-good file.')
    return set(ids)


def allowed(scene,project):
    explicit=stored(scene)
    valid={c.character_id for c in project.characters}
    if explicit is not None:return explicit & valid
    # Read-only legacy inference: use source evidence and actual Scene rigs,
    # never names or every Character in a multi-source project.
    sid=scene.get('vta_source_id');pid=scene.get('vta_project_id');raw=project.to_dict()
    ids={o.get('vta_character_id') for o in scene.objects if o.type=='ARMATURE'
         and o.get('vta_project_id')==pid and o.get('vta_source_id')==sid}
    def visit(value):
        if isinstance(value,dict):
            if value.get('source_id')==sid and isinstance(value.get('character_id'),str):ids.add(value['character_id'])
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    for name in ('identity_keyframes','repair_ranges','review_flags','tracking_revisions'):visit(raw.get(name,[]))
    # Preserve unassigned Characters in old single-source projects.
    if len(raw['sources'])==1:ids.update(valid)
    return ids & valid


def bind(scene,project,*,fresh=False):
    prior=stored(scene)
    ids=(prior or set()) if fresh else allowed(scene,project)
    _write(scene,ids)


def add(scene,project,identity):
    ids=allowed(scene,project);ids.add(identity)
    _write(scene,ids)


def _write(scene,ids):
    previous=json.loads(scene.get(KEY,'{}'));history=previous.get('history',{})
    if previous.get('project_id') and previous.get('source_id'):
        history[json.dumps([previous['project_id'],previous['source_id']])]=previous['ids']
    history[json.dumps([scene.get('vta_project_id'),scene.get('vta_source_id')])]=sorted(ids)
    scene[KEY]=json.dumps(dict(version=1,project_id=scene.get('vta_project_id'),
        source_id=scene.get('vta_source_id'),ids=sorted(ids),history=history),sort_keys=True)


def frame(view,ids):
    return replace(view,characters=tuple(c for c in view.characters if c.character_id in ids),
        candidates=tuple(replace(c,
            assigned_character_name=c.assigned_character_name if c.assigned_character_id in ids else None,
            tracked_character_id=c.tracked_character_id if c.tracked_character_id in ids else None,
            tracked_character_name=c.tracked_character_name if c.tracked_character_id in ids else None)
            for c in view.candidates))


def timeline(view,ids):
    return replace(view,**{field:tuple(row for row in getattr(view,field) if row.character_id in ids)
        for field in ('characters','keys','derived','repair_ranges')})
