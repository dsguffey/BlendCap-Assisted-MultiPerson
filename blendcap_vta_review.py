"""Thin, Blender-independent presentation boundary for VTA frame review.

The public BlendCap shell owns only transient selection and presentation
state.  Authoritative review, identity, evidence, and persistence remain in
the separately distributed private ``vta_core`` package.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
from pathlib import Path
from typing import Callable

if __package__:
    from .blendcap_vta_timeline import present_timeline
else:  # standalone Blender-independent unit tests
    from blendcap_vta_timeline import present_timeline


class VtaReviewUiError(RuntimeError):
    """The visible review slice cannot safely resolve or apply its context."""


@dataclass(frozen=True)
class CandidateRow:
    analysis_revision_id: str
    observation_id: str
    box_xyxy: tuple[float, float, float, float]
    label: str
    assigned_character_id: str | None
    assigned_character_name: str | None
    position: int = 0

    @property
    def assignable(self) -> bool:
        return self.assigned_character_id is None


@dataclass(frozen=True)
class CharacterRow:
    character_id: str
    display_name: str
    occupied_observation_id: str | None
    decision_state: str | None = None
    derived_state: str | None = None
    derived_observation_id: str | None = None

    @property
    def available(self) -> bool:
        return self.occupied_observation_id is None and self.decision_state is None


@dataclass(frozen=True)
class ReviewView:
    analysis_revision_id: str
    source_id: str
    source_frame: int
    source_width: int | None
    source_height: int | None
    state: str
    state_label: str
    candidates: tuple[CandidateRow, ...]
    characters: tuple[CharacterRow, ...]


@dataclass(frozen=True)
class ReviewLocation:
    companion_root: Path
    source_path: Path
    source_frame: int
    blender_frame: int


@dataclass(frozen=True)
class AssignmentIntent:
    """Presentation-only summary of one selected candidate and Character."""

    candidate: CandidateRow
    character: CharacterRow
    current_character_name: str | None
    occupied_candidate: CandidateRow | None
    current_decision_state: str | None = None

    @property
    def requires_confirmation(self) -> bool:
        return (
            self.current_character_name is not None
            or self.occupied_candidate is not None
            or self.current_decision_state is not None
        )


HARD_STATE_LABELS = {
    "occluded": "Occluded",
    "no-valid-candidate": "No Valid Candidate",
    "not-visible": "Not Visible",
}


@dataclass(frozen=True)
class HardStateIntent:
    character: CharacterRow
    state: str

    @property
    def requires_confirmation(self) -> bool:
        return (
            self.character.decision_state is not None
            or self.character.occupied_observation_id is not None
        )


def hard_state_confirmation_lines(intent: HardStateIntent) -> tuple[str, ...]:
    old = intent.character.decision_state
    prior = (
        "candidate assignment"
        if old in ("visible", "partially-visible") or intent.character.occupied_observation_id
        else HARD_STATE_LABELS.get(old, old)
    )
    return (
        "Replace authoritative review decision?",
        f"{intent.character.display_name}: {prior} will become {HARD_STATE_LABELS[intent.state]}.",
        "The previous decision will no longer be authoritative on this frame.",
    )


def confirmation_lines(intent: AssignmentIntent) -> tuple[str, ...]:
    """Describe only the visible effects of one confirmed correction."""
    candidate_name = _candidate_display_name(intent.candidate)
    target_name = intent.character.display_name
    lines = ["Reassign Character?"]
    if intent.current_character_name is not None:
        lines.append(
            f"{candidate_name} is assigned to {intent.current_character_name}. "
            f"Change it to {target_name}?"
        )
    if intent.occupied_candidate is not None:
        occupied_name = _candidate_display_name(intent.occupied_candidate)
        lines.append(
            f"Move {target_name} from {occupied_name} "
            f"to {candidate_name}?"
        )
        lines.append(f"{occupied_name} will become unassigned.")
    if intent.current_decision_state in HARD_STATE_LABELS:
        lines.append(
            f"Replace {target_name}'s {HARD_STATE_LABELS[intent.current_decision_state]} "
            f"decision with {candidate_name}?"
        )
    elif intent.current_decision_state in ("visible", "partially-visible"):
        lines.append(f"Replace {target_name}'s existing candidate assignment with {candidate_name}?")
    return tuple(lines)


def _candidate_display_name(candidate: CandidateRow) -> str:
    return (
        f"Candidate {candidate.position}"
        if candidate.position > 0
        else candidate.label.partition(" - ")[0]
    )


STATE_LABELS = {
    "not-analyzed": "Not Analyzed",
    "analyzed-empty": "Analyzed Empty",
    "analyzed-with-candidates": "Candidates Available",
}


ERROR_MESSAGES = {
    "revision-mismatch": "The analysis revision changed. Refresh and select again.",
    "frame-not-assignable": "This frame has no assignable candidates.",
    "candidate-not-found": "The selected candidate is stale. Refresh and select again.",
    "observation-not-found": "The selected candidate is stale. Refresh and select again.",
    "character-not-found": "The selected Character no longer exists.",
    "candidate-occupied": "That candidate is already assigned.",
    "character-occupied": "That Character already has a candidate on this frame.",
    "already-assigned": "That assignment already exists.",
    "project-load-failed": "The VTA project state is missing or unavailable.",
    "provenance-mismatch": "The project and analysis cache no longer match.",
    "persistence-failed": "The assignment could not be saved.",
    "invalid-request": "The review decision is invalid or stale.",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise VtaReviewUiError(f"Cannot read the active source video: {exc}") from exc
    return digest.hexdigest()


def companion_root_for_blend(blend_path: str | Path) -> Path:
    path = Path(blend_path)
    if not str(blend_path) or not path.name or path.suffix.lower() != ".blend":
        raise VtaReviewUiError("Save the Blender project before using VTA review.")
    return path.with_name(f"{path.stem}_vta")


def map_vse_frame_to_source(
    *,
    blender_frame: int,
    visible_start: int,
    visible_end: int,
    source_offset: int,
) -> int:
    """Map an exact visible VSE frame to the zero-based source frame."""
    values = (blender_frame, visible_start, visible_end, source_offset)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise VtaReviewUiError("VSE frame mapping values must be integers.")
    if visible_end <= visible_start:
        raise VtaReviewUiError("The active VSE strip has an invalid visible range.")
    if source_offset < 0:
        raise VtaReviewUiError("The active VSE strip has an invalid source offset.")
    if not visible_start <= blender_frame < visible_end:
        raise VtaReviewUiError("The current Blender frame is outside the active VSE strip.")
    return source_offset + blender_frame - visible_start


def present_review(
    review: object,
    source_dimensions: tuple[int, int] | None = None,
) -> ReviewView:
    """Convert the private immutable projection into public immutable rows."""
    try:
        state = review.state.value
        state_label = STATE_LABELS[state]
        assignments = {
            item.observation_id: item.character_id
            for item in review.character_assignments
        }
        names = {
            item.character_id: item.display_name for item in review.characters
        }
        occupied_by_character = {
            item.character_id: item.observation_id
            for item in review.character_assignments
        }
        occupied_by_character.update({
            item.character_id: item.observation_id
            for item in getattr(review, "character_decisions", ())
            if item.observation_id is not None
        })
        decisions_by_character = {
            item.character_id: item.state.value
            for item in getattr(review, "character_decisions", ())
        }
        derived_by_character = {
            item.character_id: item
            for item in getattr(review, "derived_tracking", ())
        }
        candidates = tuple(
            CandidateRow(
                position=index,
                analysis_revision_id=item.analysis_revision_id,
                observation_id=item.observation_id,
                box_xyxy=tuple(item.box_xyxy),
                label=_candidate_label(index, item.observation_id),
                assigned_character_id=assignments.get(item.observation_id),
                assigned_character_name=names.get(assignments.get(item.observation_id)),
            )
            for index, item in enumerate(review.candidates, start=1)
        )
        characters = tuple(
            sorted(
                (
                    CharacterRow(
                        character_id=item.character_id,
                        display_name=item.display_name,
                        occupied_observation_id=occupied_by_character.get(item.character_id),
                        decision_state=decisions_by_character.get(item.character_id),
                        derived_state=(derived_by_character[item.character_id].state.value
                                       if item.character_id in derived_by_character else None),
                        derived_observation_id=(
                            derived_by_character[item.character_id].observation_id
                            if item.character_id in derived_by_character else None
                        ),
                    )
                    for item in review.characters
                ),
                key=lambda item: item.display_name.casefold(),
            )
        )
        return ReviewView(
            analysis_revision_id=review.analysis_revision_id,
            source_id=review.source_id,
            source_frame=review.frame,
            source_width=None if source_dimensions is None else source_dimensions[0],
            source_height=None if source_dimensions is None else source_dimensions[1],
            state=state,
            state_label=state_label,
            candidates=candidates,
            characters=characters,
        )
    except (AttributeError, KeyError, TypeError) as exc:
        raise VtaReviewUiError(f"Private frame review is malformed: {exc}") from exc


def _candidate_label(position: int, observation_id: str) -> str:
    suffix = observation_id if len(observation_id) <= 18 else f"{observation_id[:15]}..."
    return f"Candidate {position} - observation {suffix}"


class SelectionState:
    """Disposable Blender interaction state; never semantic authority."""

    def __init__(self) -> None:
        self.review_key: tuple[str, int] | None = None
        self.observation_id = ""
        self.character_id = ""

    def accept_review(self, view: ReviewView) -> None:
        key = (view.analysis_revision_id, view.source_frame)
        if key != self.review_key:
            self.observation_id = ""
        self.review_key = key
        valid_characters = {item.character_id for item in view.characters}
        if self.character_id not in valid_characters:
            self.character_id = ""
        # Selection is inspection-only UI state, so assigned candidates remain
        # selectable.  ``can_assign`` independently enforces assignment rules.
        valid_candidates = {item.observation_id for item in view.candidates}
        if self.observation_id not in valid_candidates:
            self.observation_id = ""

    def select_observation(self, view: ReviewView, observation_id: str) -> bool:
        """Select one durable observation from the current immutable review."""
        self.accept_review(view)
        if view.state != "analyzed-with-candidates":
            return False
        if observation_id not in {item.observation_id for item in view.candidates}:
            return False
        self.observation_id = observation_id
        return True

    def can_assign(self, view: ReviewView) -> bool:
        return self.assignment_intent(view) is not None

    def assignment_intent(self, view: ReviewView) -> AssignmentIntent | None:
        if view.state != "analyzed-with-candidates":
            return None
        candidate = next(
            (item for item in view.candidates if item.observation_id == self.observation_id),
            None,
        )
        character = next(
            (item for item in view.characters if item.character_id == self.character_id),
            None,
        )
        if candidate is None or character is None:
            return None
        if candidate.assigned_character_id == character.character_id:
            return None
        current_character_name = candidate.assigned_character_name
        if candidate.assigned_character_id is not None and current_character_name is None:
            current_character_name = next(
                (item.display_name for item in view.characters
                 if item.character_id == candidate.assigned_character_id),
                "another Character",
            )
        occupied_candidate = next(
            (item for item in view.candidates
             if item.observation_id == character.occupied_observation_id),
            None,
        )
        return AssignmentIntent(
            candidate, character, current_character_name, occupied_candidate,
            character.decision_state
            if character.decision_state in HARD_STATE_LABELS
            or (character.decision_state in ("visible", "partially-visible")
                and occupied_candidate is None)
            else None,
        )

    def hard_state_intent(self, view: ReviewView, state: str) -> HardStateIntent | None:
        if state not in HARD_STATE_LABELS:
            return None
        character = next(
            (item for item in view.characters if item.character_id == self.character_id),
            None,
        )
        if character is None or character.decision_state == state:
            return None
        return HardStateIntent(character, state)

    def select_character(self, view: ReviewView, character_id: str) -> bool:
        """Select a Character as intent without touching authoritative state."""
        self.accept_review(view)
        if character_id not in {item.character_id for item in view.characters}:
            return False
        self.character_id = character_id
        return True


def user_message(error: BaseException) -> str:
    code = getattr(getattr(error, "code", None), "value", None)
    if code in ERROR_MESSAGES:
        return ERROR_MESSAGES[code]
    if isinstance(error, VtaReviewUiError):
        return str(error)
    return f"VTA review failed: {error}"


def character_name_exists(view: ReviewView, display_name: str) -> bool:
    """Use the private core's existing trim/casefold uniqueness rule."""
    key = display_name.strip().casefold()
    return bool(key) and any(
        row.display_name.strip().casefold() == key for row in view.characters
    )


class PrivateCoreBackend:
    """Late-bound adapter to the separately installed private ``vta_core``."""

    def __init__(
        self,
        core: object | None = None,
        importer: Callable[[str], object] = importlib.import_module,
    ) -> None:
        if core is None:
            try:
                core = importer("vta_core")
            except (ImportError, ModuleNotFoundError) as exc:
                raise VtaReviewUiError(
                    "Private VTA core is unavailable in Blender's Python environment."
                ) from exc
        self._core = core
        self._source_digest_cache: dict[Path, tuple[int, int, str]] = {}

    def query(self, location: ReviewLocation) -> ReviewView:
        project, cache, revision_id = self._load_context(location)
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
        return present_review(review, dimensions)

    def query_timeline(self, location: ReviewLocation):
        """Read all authoritative keys once per project change, never per draw."""
        project, cache, revision_id = self._load_context(location)
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

    def prepare_management(self, location: ReviewLocation, action: str, **kwargs):
        project, cache, revision_id = self._load_context(location)
        try:
            self._core.validate_analysis_cache_registration(project, cache, revision_id)
            return self._core.build_review_management_request(project, action, **kwargs)
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core"):
                raise VtaReviewUiError(str(exc)) from exc
            raise

    def apply_management(self, location: ReviewLocation, request, *, confirmed: bool):
        self._load_context(location)
        try:
            receipt = self._core.apply_review_management_and_persist(
                location.companion_root, request, confirmed=confirmed,
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
        self, location: ReviewLocation, request: object
    ) -> tuple[object, ReviewView]:
        """Submit the already confirmed snapshot; private core revalidates it."""
        _project, cache, _revision_id = self._load_context(location)
        try:
            receipt = self._core.apply_confirmed_reassignment_and_persist(
                location.companion_root, cache, request
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
        self, location: ReviewLocation, request: object, *, confirmed: bool
    ) -> tuple[object, ReviewView]:
        """Persist via private authority, then reload the canonical review."""
        _project, cache, _revision_id = self._load_context(location)
        try:
            receipt = self._core.apply_hard_frame_decision_and_persist(
                location.companion_root, cache, request, confirmed=confirmed
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

    def _load_context(self, location: ReviewLocation) -> tuple[object, object, str]:
        try:
            project = self._core.load_project_state(location.companion_root)
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core") or isinstance(exc, OSError):
                raise VtaReviewUiError("The VTA project state is missing or unavailable.") from exc
            raise
        source_hash = self._source_hash(location.source_path)
        raw = project.to_dict()
        sources = [
            item for item in raw.get("sources", [])
            if item.get("video_sha256") == source_hash
        ]
        if len(sources) != 1:
            raise VtaReviewUiError(
                "The active VSE source does not uniquely match the VTA project."
            )
        source = sources[0]
        if not source["start_frame"] <= location.source_frame <= source["end_frame"]:
            raise VtaReviewUiError("The mapped source frame is outside VTA source coverage.")
        active = raw.get("active_analysis_revision_by_source", {})
        revision_id = active.get(source["source_id"])
        if not isinstance(revision_id, str) or not revision_id:
            raise VtaReviewUiError("The active source has no active analysis revision.")
        try:
            cache = self._core.load_analysis_cache(location.companion_root, revision_id)
        except Exception as exc:
            if exc.__class__.__module__.startswith("vta_core") or isinstance(exc, OSError):
                raise VtaReviewUiError("The active analysis cache is missing or unavailable.") from exc
            raise
        return project, cache, revision_id

    def _source_hash(self, path: Path) -> str:
        try:
            stat = path.stat()
        except OSError as exc:
            raise VtaReviewUiError(f"Cannot inspect the active source video: {exc}") from exc
        resolved = path.resolve()
        signature = (stat.st_size, stat.st_mtime_ns)
        cached = self._source_digest_cache.get(resolved)
        if cached is not None and cached[:2] == signature:
            return cached[2]
        digest = sha256_file(resolved)
        self._source_digest_cache[resolved] = (signature[0], signature[1], digest)
        return digest
