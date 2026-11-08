"""Transient read-only display snapshots. No persistence or authoritative writes."""
from collections import OrderedDict
from dataclasses import replace

from ..blendcap_vta_review import VtaReviewUiError


def file_signature(path):
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


class Presentation:
    """One current source snapshot; bounded memoized frame views.

    Disk observations are explicit/observer work only. Frame changes and draws
    use peek/frame; commands still validate canonical state independently.
    """
    def __init__(self):
        self.clear()

    def clear(self):
        self.key = self.stamp = self.context = self.error = None
        self.revision = None
        self.frames = OrderedDict()
        self.timeline = None
        self.coverage = None

    @staticmethod
    def identity(backend, location):
        return id(backend), location.companion_root, location.source_path

    def signature(self, location):
        root = location.companion_root
        return (file_signature(root/'project_state.json'),
                file_signature(location.source_path),
                file_signature(root/'analysis'/f'{self.revision}.json') if self.revision else None)

    def current(self, backend, location):
        return self.key == self.identity(backend, location)

    def observe(self, backend, location, source, *, force=False, asynchronous=False):
        observation=(backend.observe_async(location) if asynchronous else backend.observe(location,force=force))
        if observation is None:return False
        changed=self.context is not observation
        self.context=observation;self.key=self.identity(backend,location)
        self.coverage=observation.coverage;self.revision=observation.value['analysis_revision_id']
        self.stamp=backend.stamp;self.error=None
        if changed:self.frames.clear();self.timeline=None
        return changed

    def frame(self, backend, location, *, show_suppressed=False):
        if self.error:
            raise self.error
        if self.context is None or not self.current(backend, location):
            raise VtaReviewUiError('Load / Refresh MoCap presentation first.')
        if not self.coverage[0] <= location.source_frame <= self.coverage[1]:
            raise VtaReviewUiError('The mapped source frame is outside MoCap source coverage.')
        key = location.source_frame, bool(show_suppressed)
        if key not in self.frames:
            view = backend.frame_from_context(self.context, location, show_suppressed=show_suppressed)
            self.frames[key] = view
            if len(self.frames) > 256:
                self.frames.popitem(last=False)
        self.frames.move_to_end(key)
        return self.frames[key]

    def review_timeline(self, backend):
        if self.error:
            raise self.error
        if self.timeline is None:
            self.timeline = backend.timeline_from_context(self.context)
        return self.timeline


def pointer(owner):
    return owner.as_pointer() if hasattr(owner, 'as_pointer') else id(owner)


def source_identity(scene, blend_path):
    """RNA-only fingerprint: no filesystem access, hashing or model loading."""
    try:
        editor = scene.sequence_editor
        strip = editor.active_strip if editor else None
        if strip is None or strip.type != 'MOVIE':
            return None
        return (blend_path, pointer(scene), pointer(strip),
                tuple(scene.get(k) for k in ('vta_project_id','vta_source_id','vta_source_video_sha256')),
                scene.blendcap_props.video_source, strip.filepath, strip.get('blendcap_source'),
                # fps is a READ-ONLY media-derived getter that reopens the movie
                # (~36 ms/read on the fixture). Validate it at explicit/file-
                # change boundaries, not in polls or frame/draw fingerprints.
                tuple(getattr(strip,k,None) for k in ('stream_index','frame_start','frame_final_start','frame_final_end',
                    'frame_offset_start','frame_offset_end','animation_offset_start','animation_offset_end',
                    'speed_factor','use_reverse_frames')),
                len(getattr(strip,'retiming_keys',())), len(getattr(strip,'modifiers',())))
    except (AttributeError, ReferenceError, RuntimeError):
        return None


def at_frame(info, scene):
    location = info[2]
    offset = location.blender_frame - location.source_frame
    return replace(location, blender_frame=scene.frame_current,
                   source_frame=scene.frame_current-offset)
