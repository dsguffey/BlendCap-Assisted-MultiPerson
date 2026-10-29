"""Frozen pre-engine presentation parity adapter, tests only."""
def install(ui):
    if getattr(ui._display,'_fixture_adapter',False):return
    base=ui._display.__class__
    class ParityPresentation(base):
        _fixture_adapter=True
        def observe(self,backend,location,source,*,force=False,asynchronous=False):
            if getattr(backend,'is_engine_review',False):return super().observe(backend,location,source,force=force,asynchronous=asynchronous)
            return legacy_observe(self,backend,location,source,force=force)
    ui._display=ParityPresentation()

def legacy_observe(self,backend,location,source,*,force=False):
    from blendcap_vta_review import VtaReviewUiError
    key = self.identity(backend, location)
    if not force and self.key == key and self.stamp == self.signature(location):
        return False
    self.clear()
    self.key = key
    try:
        # Load exactly one project and one active cache per invalidation.
        # Metadata was validated by the source/session boundary. The hash
        # check here protects independent refresh/file changes as well.
        project = backend._core.load_project_state(location.companion_root)
        raw = project.to_dict()
        digest = backend._source_hash(location.source_path)
        matches = [s for s in raw['sources'] if s.get('video_sha256') == digest]
        if len(matches) != 1 or matches[0]['source_id'] != source['source_id']:
            raise VtaReviewUiError('Presentation source differs from canonical project state.')
        self.coverage = matches[0]['start_frame'], matches[0]['end_frame']
        revision = raw.get('active_analysis_revision_by_source', {}).get(source['source_id'])
        self.revision = revision
        if not revision:
            raise VtaReviewUiError('The active source has no active analysis revision.')
        cache = backend._core.load_analysis_cache(location.companion_root, revision)
        self.context = project, cache, revision
    except Exception as error:
        self.error = error
    self.stamp = self.signature(location)
    return True
