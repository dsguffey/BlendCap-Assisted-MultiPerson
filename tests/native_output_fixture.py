"""Frozen GPL fixture validation, only for pre-engine native test inputs.

Production native application always receives a neutral validated artifact.
This fixture never ships and is not a runtime fallback.
"""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import bpy

def install(module):
    from tests.legacy_native_validation import install as install_validators
    install_validators(module)
    if getattr(module.import_reviewed_output,'_fixture_adapter',False):return
    original=module.import_reviewed_output
    def apply(path,location,backend,**kw):
        if not getattr(backend,'is_engine_generation',False):
            backend=Fixture(module,backend,kw.get('depth_mode','AUTOMATIC'))
            kw.pop('validated',None)
        return original(path,location,backend,**kw)
    apply._fixture_adapter=True
    module.import_reviewed_output=apply

class Fixture:
    is_engine_generation=True
    def __init__(self,module,backend,depth):self.module=module;self.backend=backend;self.depth=depth
    def validate_output(self,location,path,**kw):
        return validate(self.module,self.backend,location,path,self.depth,**kw)

def validate(module,backend,location,path,depth_mode,*,previous_fingerprints=(),saved_reference=None,migration_approved=False):
    root=Path(path).resolve().parent;report=module._read_report(path)
    _inside=module._inside;_hash=module._hash
    validate_camera_registration=module.validate_camera_registration
    project, cache, _revision = backend._load_context(location)
    bounded = (report['plan'].get('generation_scope') == 'range'
               or report['version'] == 2 or report['plan'].get('version') == 2)
    options = {'frame_range': report['plan']['source_range']} if bounded else {}
    if 'character_scope' in report['plan']:
        options['character_ids'] = report['plan']['character_scope']
    plan = backend._core.build_skeletal_plan(project, cache, **options)
    if report['plan'] != plan:
        raise ValueError('Skeletal output is stale; Generate Animation again from current review')
    reconstruction = report.get('reconstruction')
    fingerprint = backend._core.reconstruction_fingerprint(reconstruction, plan.get('source_video_sha256'))
    expected = {c['character_id']: c for c in plan['characters']}
    import importlib
    require_source_aligned=importlib.import_module(module.__package__+'.vta_camera_cleanup').require_source_aligned
    require_source_aligned(bpy.context.scene, expected, plan['project_id'], plan['source_id'])
    records = report['characters']
    if len(records) != len(expected) or {r['character_id'] for r in records} != set(expected):
        raise ValueError('Skeletal output Character set differs from reviewed plan')
    paths = {}
    registration = {}
    for record in records:
        character = expected[record['character_id']]
        if record['schedule'] != backend._core.resolve_pose_schedule(
                character['samples'], record['source_frames'],
                repair_context=character.get('repair_context', ())):
            raise ValueError('Skeletal schedule differs from authoritative repair policy')
        if record['bvh'] is not None:
            bvh = _inside(root, record['bvh'])
            if _hash(bvh) != record['bvh_sha256']:
                raise ValueError('Skeletal BVH hash changed')
            paths[record['character_id']] = bvh
        if report['version'] == 4:
            registration[record['character_id']] = validate_camera_registration(
                record, plan, root, backend._core, reconstruction)
    if not paths or not any(s['state'] != 'unavailable' for r in records for s in r['schedule']):
        raise ValueError('Animation has no successful trustworthy or repaired pose; prior output retained')
    backend._core.require_compatible_reconstruction(fingerprint,previous_fingerprints,bounded=bounded and not migration_approved)
    cameras=[o for o in bpy.context.scene.objects if o.type=='CAMERA' and o.get('vta_project_id')==plan['project_id'] and o.get('vta_source_id')==plan['source_id']]
    reference=module.resolve_scene_reference(plan,records,cameras[0] if cameras else None,backend._core,path) if report['version']==4 else None
    plan={**plan,'analysis_revision_id':cache.revision_id}
    if bounded:plan['generation_scope']='range'
    report={**report,'plan':plan,'reconstruction':reconstruction or dict(fingerprint=fingerprint)}
    source=getattr(cache,'source',NS(start_frame=0,end_frame=plan.get('source_range',[0,0])[1]))
    return dict(report=report,depth_mode=depth_mode,output_sha256=_hash(path),source=dict(start_frame=source.start_frame,end_frame=source.end_frame),
        registration=registration,reference=reference,authority_sha256=_hash(location.companion_root/'project_state.json'),job_id='fixture-only',checkpoint=dict(revision=dict(sha256=_hash(location.companion_root/'project_state.json'))))
