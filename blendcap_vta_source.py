"""Neutral source staging; authority stays in the one-shot engine."""
from types import SimpleNamespace as NS
def stage(client,root,path,*,fps,count,width,height):
    value=client.call('source-stage',dict(companion_root=str(root),source_path=str(path),fps=fps,count=count,width=width,height=height))
    # Only the host presentation interface needed by session setup, not ProjectState.
    project=NS(project_id=value['project_id'],characters=(),to_dict=lambda:dict(
        active_analysis_revision_by_source={value['source']['source_id']:value['analysis_revision_id']} if value['analysis_revision_id'] else {}))
    def publish():return client.call('source-publish',dict(companion_root=str(root),stage_id=value['stage_id']))
    return value,project,publish
