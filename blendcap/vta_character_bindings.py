"""Bounded host application receipts for explicit Character-to-Scene adoption.

These receipts do not define Characters or replay domain edits. Explicit Load can
repair native membership rolled back by Blender Undo, after validating live IDs.
"""
from pathlib import Path
import json,os,uuid
from .. import vta_protocol as wire

FILE='.vta-host-bindings'
LIMIT=8*1024*1024

def path(root):
    root=Path(root).resolve();result=root/FILE
    if not result.resolve().is_relative_to(root):
        raise ValueError('Host binding receipt path is unsafe.')
    return result

def read(root,*,with_ids=False):
    location=path(root)
    if not location.exists():return []
    files=list(location.glob('*.json'))
    if len(files)>4096:raise ValueError('Host binding receipt count exceeds bounds.')
    rows=[];total=0
    for item in files:
        wire.identifier(item.stem)
        if not item.resolve().is_relative_to(Path(root).resolve()) or item.stat().st_nlink!=1 or item.stat().st_size>wire.MAX_BYTES:
            raise ValueError('Host binding receipt path or size is unsafe.')
        raw=item.read_bytes();total+=len(raw)
        if len(raw)>wire.MAX_BYTES or total>LIMIT:raise ValueError('Host binding receipts exceed bounds.')
        row=json.loads(raw.decode('utf-8'))
        wire.fields(row,('scene_owner_id','project_id','source_id','video_sha256','character_id'))
        wire.identifier(row['scene_owner_id']);wire.text(row['project_id']);wire.text(row['source_id'])
        wire.digest(row['video_sha256']);wire.text(row['character_id'])
        if with_ids:row={**row,'request_id':item.stem}
        rows.append(row)
    return rows

def record(root,binding,character_id,request_id):
    wire.identifier(request_id)
    row={**binding,'character_id':character_id};rows=read(root)
    folder=path(root);folder.mkdir(exist_ok=True);location=folder/(request_id+'.json')
    raw=wire.dumps(row)
    if location.exists():
        if wire.loads(location.read_bytes())!=row:raise ValueError('Host binding receipt identity conflict.')
        return
    if len(rows)>=4096:raise ValueError('Host binding receipt count exceeds bounds.')
    temporary=location.with_name(location.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,location)
    finally:temporary.unlink(missing_ok=True)

def apply_on_load(root,scene,observation):
    """Explicit Load only: restore intended membership, never native animation."""
    from . import vta_scene_characters as characters
    rows=[r for r in read(root) if r['project_id']==observation.project_id
        and r['source_id']==observation.source['source_id'] and r['video_sha256']==observation.source['video_sha256']]
    identity=scene.get('vta_scene_owner_id')
    if not identity:
        identities={r['scene_owner_id'] for r in rows}
        # An old native undo state may predate the receipt's host ID. Adopt it
        # only when this source has one receipt identity and one matching Scene.
        import bpy
        peers=[s for s in bpy.data.scenes if s.get('vta_project_id')==observation.project_id and s.get('vta_source_id')==observation.source['source_id']]
        if len(identities)==1 and len(peers)==1:
            identity=next(iter(identities));scene['vta_scene_owner_id']=identity
    valid={c.character_id for c in observation.characters}
    additions={r['character_id'] for r in rows if r['scene_owner_id']==identity and r['character_id'] in valid}
    if additions:
        members=characters.allowed(scene,observation);characters._write(scene,members|additions)
