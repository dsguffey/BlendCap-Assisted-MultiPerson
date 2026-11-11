"""Conservative, Blender-independent plan for MoCap Scene/Character cleanup.

Project data is supplied only after the private engine validates its checkpoint.
This module plans host references; the private engine remains the only project writer.
"""
from dataclasses import dataclass

PROJECT_FIELDS=frozenset(('active_analysis_revision_by_source','analysis_revisions',
    'candidate_suppression_history','characters','generation_outputs',
    'identity_keyframes','project_id','repair_ranges','review_flags','schema',
    'sources','tracking_revisions','version'))


@dataclass(frozen=True)
class ReconcilePlan:
    orphans: tuple[str,...]
    stale_receipts: tuple[str,...]
    stale_memberships: tuple[tuple[int,tuple[str,...]],...]
    preserved: tuple[tuple[str,str],...]


def _mentions(value, identity):
    if isinstance(value,str):return identity in value
    if isinstance(value,dict):return any(_mentions(item,identity) for item in value.values())
    if isinstance(value,(list,tuple)):return any(_mentions(item,identity) for item in value)
    return False


def plan(project, scenes, receipts, native_ids=(), *, pending=False):
    """Conservatively classify IDs using *all* surviving Scene and project data.

    scenes are validated host snapshots with owner/project/source/membership fields.
    An absent or malformed membership blocks automatic Character deletion.
    """
    characters={row['character_id'] for row in project['characters']}
    if len(characters)!=len(project['characters']):raise ValueError('Duplicate Character identities.')
    pid=project['project_id']
    live=[row for row in scenes if row['project_id']==pid or pid in row.get('history_projects',())]
    members=set(native_ids)
    uncertain=pending or set(project)!=PROJECT_FIELDS
    stale=[]
    for index,scene in enumerate(live):
        ids=scene['membership']
        if ids is None:
            uncertain=True
            continue
        members.update(ids)
        unknown=tuple(sorted(set(ids)-characters))
        if unknown:stale.append((index,unknown))
    owners={row['owner_id'] for row in live if row['owner_id']}
    stale_receipts=[]
    for receipt in receipts:
        if receipt['project_id']!=pid:continue
        if receipt['scene_owner_id'] in owners:
            members.add(receipt['character_id'])
        elif any(row['owner_id'] is None for row in live):
            members.add(receipt['character_id'])
            uncertain=True
        else:
            stale_receipts.append(receipt['request_id'])
    authority={key:value for key,value in project.items() if key!='characters'}
    orphans=[];preserved=[]
    for identity in sorted(characters):
        reason=('surviving Scene/native/receipt' if identity in members else
                'canonical project reference' if _mentions(authority,identity) else
                'uncertain Scene or project state' if uncertain else '')
        if reason:preserved.append((identity,reason))
        else:orphans.append(identity)
    if pending or set(project)!=PROJECT_FIELDS:
        return ReconcilePlan((),(),(),tuple(preserved))
    return ReconcilePlan(tuple(orphans),tuple(stale_receipts),tuple(stale),tuple(preserved))
