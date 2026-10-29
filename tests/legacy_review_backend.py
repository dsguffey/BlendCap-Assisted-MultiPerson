"""Frozen pre-M3 public adapter for behavioral parity tests only.

Copied from accepted public 8aa4682198f2ed54c6b0e3302160b149777cb99c.
Production review must use EngineReviewBackend; this fixture cannot be selected
by runtime discovery. Retains GPL public adapter provenance.
"""
from pathlib import Path
import hashlib
from blendcap_vta_review import *
from tests.legacy_source_backend import PrivateCoreBackend
from blendcap_vta_timeline import present_timeline

class LegacyReviewBackend(PrivateCoreBackend):
    def query(self, location: ReviewLocation, *, show_suppressed=False) -> ReviewView:
        return self.frame_from_context(self._load_context(location), location,
                                       show_suppressed=show_suppressed)


    def frame_from_context(self, context, location: ReviewLocation, *, show_suppressed=False):
        """Read-only presentation of an already validated in-memory snapshot.

        Authoritative commands continue using _load_context, never this cache.
        """
        project, cache, revision_id = context
        try:
            review = self._core.build_frame_review(
                project,
                cache,
                analysis_revision_id=revision_id,
                frame_index=location.source_frame,
            )
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                raise VtaReviewUiError(str(exc)) from exc
            raise
        source = getattr(cache, "source", None)
        require_dimensions = getattr(source, "require_dimensions", None)
        dimensions = require_dimensions() if callable(require_dimensions) else None
        return present_review(review, dimensions, show_suppressed=show_suppressed)


    def prepare_suppression(self, location, observation_id, *, suppressed):
        project, cache, _revision = self._load_context(location)
        try:
            return self._core.build_candidate_suppression_request(
                project, cache, observation_id, suppressed=suppressed)
        except self._core.StateModelError as exc:
            raise VtaReviewUiError(str(exc)) from exc


    def apply_suppression(self, location, request, *, confirmed=False, **undo_guard):
        try:
            return self._core.apply_candidate_suppression_and_persist(
                location.companion_root, request, confirmed=confirmed, **undo_guard)
        except self._core.StateModelError as exc:
            raise VtaReviewUiError(str(exc)) from exc


    def query_timeline(self, location: ReviewLocation):
        """Read all authoritative keys once per project change, never per draw."""
        return self.timeline_from_context(self._load_context(location))


    def timeline_from_context(self, context):
        project, cache, revision_id = context
        try:
            self._core.validate_analysis_cache_registration(project, cache, revision_id)
            private = self._core.build_review_timeline(
                project, cache.source.source_id, cache=cache
            )
            return present_timeline(private)
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                raise VtaReviewUiError(str(exc)) from exc
            raise


    def update_tracking(self, location: ReviewLocation, character_id: str):
        """Explicit cache-only local recomputation; never creates review keys."""
        project, cache, revision_id = self._load_context(location)
        if character_id not in {item.character_id for item in project.characters}:
            raise VtaReviewUiError("Select an existing Character before Update Tracking.")
        try:
            self._core.validate_analysis_cache_registration(project, cache, revision_id)
            result = self._core.update_tracking_and_persist(
                location.companion_root, character_id,
                cache.source.source_id, location.source_frame,
            )
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                raise VtaReviewUiError(str(exc)) from exc
            raise
        return result, self.query(location), self.query_timeline(location)


    def update_tracking_for_characters(self, location, character_ids, **undo_guard):
        project, cache, revision_id = self._load_context(location)
        if (not isinstance(character_ids, (tuple, list)) or not character_ids
                or any(not isinstance(cid, str) or not cid for cid in character_ids)
                or len(set(character_ids)) != len(character_ids)
                or not set(character_ids) <= {c.character_id for c in project.characters}):
            raise VtaReviewUiError("Select Character rows in the VTA Dope Sheet before Update Tracking.")
        try:
            self._core.validate_analysis_cache_registration(project, cache, revision_id)
            outcomes = self._core.update_tracking_many_and_persist(
                location.companion_root, tuple(sorted(character_ids)), cache.source.source_id,
                location.source_frame,
                expected_project_sha256=hashlib.sha256(project.to_json().encode()).hexdigest(), **undo_guard)
        except Exception as exc:
            if exc.__class__.__module__.startswith('vta_core'):
                raise VtaReviewUiError(str(exc)) from exc
            raise
        return outcomes, self.query(location), self.query_timeline(location)


    def prepare_management(self, location: ReviewLocation, action: str, **kwargs):
        if action == 'rename-character':
            raise VtaReviewUiError('Character rename requires the engine mutation boundary.')
        project, cache, revision_id = self._load_context(location)
        try:
            self._core.validate_analysis_cache_registration(project, cache, revision_id)
            return self._core.build_review_management_request(project, action, **kwargs)
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                raise VtaReviewUiError(str(exc)) from exc
            raise


    def set_motion_trust(self, location: ReviewLocation, character_id: str,
                         start_visible: int, end_visible: int, *, action: str, mode=None):
        return self.set_motion_trust_for_characters(
            location, (character_id,), start_visible, end_visible, action=action, mode=mode)[0]


    def set_motion_trust_for_characters(self, location: ReviewLocation, character_ids,
                                       start_visible: int, end_visible: int, *, action: str, mode=None, previous_range=None):
        """Convert existing VSE frame coordinates, delegate all motion truth to core."""
        project, source = self._load_project_source(location)
        if (not isinstance(character_ids, (tuple, list)) or not character_ids
                or any(not isinstance(cid, str) or not cid for cid in character_ids)
                or len(set(character_ids)) != len(character_ids)
                or not set(character_ids) <= {c.character_id for c in project.characters}):
            raise VtaReviewUiError("Select existing Character rows before editing repair ranges.")
        start, end = map_visible_analysis_range(location, source["start_frame"], source["end_frame"],
                                               start_visible, end_visible)
        digest = hashlib.sha256(project.to_json().encode("utf-8")).hexdigest()
        try:
            receipt = self._core.apply_motion_trust_many_and_persist(
                location.companion_root, tuple(sorted(character_ids)), source["source_id"], start, end,
                action=action, mode=mode, expected_project_sha256=digest,
                **({'previous_range': previous_range} if previous_range is not None else {}))
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core") or isinstance(exc, OSError):
                raise VtaReviewUiError(str(exc)) from exc
            raise
        return receipt


    def apply_management(self, location: ReviewLocation, request, *, confirmed: bool, **undo_guard):
        if request.action == 'rename-character':
            raise VtaReviewUiError('Character rename requires the engine mutation boundary.')
        self._load_context(location)
        try:
            receipt = self._core.apply_review_management_and_persist(
                location.companion_root, request, confirmed=confirmed, **undo_guard,
            )
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                raise VtaReviewUiError(str(exc)) from exc
            raise
        return receipt, self.query(location), self.query_timeline(location)


    def apply(
        self,
        location: ReviewLocation,
        *,
        observation_id: str,
        character_id: str,
        **undo_guard,
    ) -> tuple[object, ReviewView]:
        project, cache, revision_id = self._load_context(location)
        try:
            review = self._core.build_frame_review(
                project,
                cache,
                analysis_revision_id=revision_id,
                frame_index=location.source_frame,
            )
            request = self._core.build_review_decision_request(
                review,
                analysis_revision_id=revision_id,
                observation_id=observation_id,
                character_id=character_id,
            )
            receipt = self._core.apply_review_decision_and_persist(
                location.companion_root,
                cache,
                request,
                **undo_guard,
            )
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                raise VtaReviewUiError(user_message(exc)) from exc
            raise
        # Canonical reload is mandatory; never infer success by mutating review.
        return receipt, self.query(location)


    def prepare_reassignment(
        self,
        location: ReviewLocation,
        *,
        observation_id: str,
        character_id: str,
    ) -> tuple[object, ReviewView]:
        """Capture current private occupancy before the confirmation dialog."""
        project, cache, revision_id = self._load_context(location)
        try:
            review = self._core.build_frame_review(
                project, cache,
                analysis_revision_id=revision_id,
                frame_index=location.source_frame,
            )
            request = self._core.build_confirmed_reassignment_request(
                review,
                analysis_revision_id=revision_id,
                observation_id=observation_id,
                character_id=character_id,
            )
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                raise VtaReviewUiError(user_message(exc)) from exc
            raise
        source = getattr(cache, "source", None)
        require_dimensions = getattr(source, "require_dimensions", None)
        dimensions = require_dimensions() if callable(require_dimensions) else None
        return request, present_review(review, dimensions)


    def apply_confirmed_reassignment(
        self, location: ReviewLocation, request: object, **undo_guard
    ) -> tuple[object, ReviewView]:
        """Submit the already confirmed snapshot; private core revalidates it."""
        _project, cache, _revision_id = self._load_context(location)
        try:
            receipt = self._core.apply_confirmed_reassignment_and_persist(
                location.companion_root, cache, request, **undo_guard
            )
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                raise VtaReviewUiError(user_message(exc)) from exc
            raise
        return receipt, self.query(location)


    def prepare_hard_state(
        self, location: ReviewLocation, *, character_id: str, state: str
    ) -> tuple[object, ReviewView]:
        """Capture the current authoritative per-Character frame decision."""
        project, cache, revision_id = self._load_context(location)
        try:
            review = self._core.build_frame_review(
                project, cache,
                analysis_revision_id=revision_id,
                frame_index=location.source_frame,
            )
            request = self._core.build_hard_frame_decision_request(
                review, analysis_revision_id=revision_id,
                character_id=character_id, state=state,
            )
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                raise VtaReviewUiError(user_message(exc)) from exc
            raise
        source = getattr(cache, "source", None)
        require_dimensions = getattr(source, "require_dimensions", None)
        dimensions = require_dimensions() if callable(require_dimensions) else None
        return request, present_review(review, dimensions)


    def apply_hard_state(
        self, location: ReviewLocation, request: object, *, confirmed: bool, **undo_guard
    ) -> tuple[object, ReviewView]:
        """Persist via private authority, then reload the canonical review."""
        _project, cache, _revision_id = self._load_context(location)
        try:
            receipt = self._core.apply_hard_frame_decision_and_persist(
                location.companion_root, cache, request, confirmed=confirmed, **undo_guard
            )
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                raise VtaReviewUiError(user_message(exc)) from exc
            raise
        return receipt, self.query(location)


    def create_character(
        self, location: ReviewLocation, *, display_name: str
    ) -> tuple[object, ReviewView]:
        """Create via private authority, then return a canonical re-query."""
        self._load_context(location)
        try:
            character = self._core.create_character_and_persist(
                location.companion_root, display_name
            )
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                if exc.__class__.__name__ == "NameConflictError":
                    name = getattr(exc, "display_name", display_name)
                    raise VtaReviewUiError(
                        f"Character {name!r} already exists. Select it or enter a different name."
                    ) from exc
                raise VtaReviewUiError(str(exc)) from exc
            raise
        return character, self.query(location)
