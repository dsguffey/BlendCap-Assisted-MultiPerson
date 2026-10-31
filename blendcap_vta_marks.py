"""Session-only candidate intent, separate from selection and identity authority."""
ASSIGN_ICON = 'DECORATE_KEYFRAME'
SUPPRESS_ICON = 'HIDE_ON'
ASSIGN_SYMBOL = '[A]'
SUPPRESS_SYMBOL = '[S]'
MAX_MARKS = 128


class CandidateMarks:
    def __init__(self):
        self.scopes = {}

    def clear(self, scope=None, kind=None):
        if scope is None:
            self.scopes.clear()
        elif kind is None:
            self.scopes.pop(scope, None)
        elif scope in self.scopes:
            self.scopes[scope][kind].clear()

    def items(self, scope, kind):
        return tuple(sorted(self.scopes.get(scope, {}).get(kind, {}).values(),
                            key=lambda row: (row['frame'], row['observation_id'])))

    def toggle(self, scope, kind, frame, observation_id, analysis_revision_id, cache_sha256):
        if kind not in ('assign', 'suppress'):
            raise ValueError('Unknown mark kind.')
        groups = self.scopes.setdefault(scope, {'assign': {}, 'suppress': {}})
        key = frame if kind == 'assign' else (frame, observation_id)
        prior = groups[kind].get(key)
        if prior and prior['observation_id'] == observation_id:
            del groups[kind][key]
            return False
        if key not in groups[kind] and len(groups[kind]) >= MAX_MARKS:
            raise ValueError('Apply or clear the current marks before adding more.')
        # A candidate cannot simultaneously be proposed for identity and exclusion.
        if kind == 'assign':
            groups['suppress'].pop((frame, observation_id), None)
        elif groups['assign'].get(frame, {}).get('observation_id') == observation_id:
            del groups['assign'][frame]
        groups[kind][key] = dict(frame=frame, observation_id=observation_id,
            analysis_revision_id=analysis_revision_id, cache_sha256=cache_sha256)
        return True

    def handled(self, scope, kind, submitted):
        """Clear only successfully handled marks; preserve newer edits."""
        groups = self.scopes.get(scope)
        if groups is None:
            return
        for row in submitted:
            key = row['frame'] if kind == 'assign' else (row['frame'], row['observation_id'])
            if groups[kind].get(key) == row:
                del groups[kind][key]
