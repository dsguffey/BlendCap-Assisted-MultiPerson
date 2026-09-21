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


class VtaReviewUiError(RuntimeError):
    """The visible review slice cannot safely resolve or apply its context."""


@dataclass(frozen=True)
class CandidateRow:
    analysis_revision_id: str
    observation_id: str
    label: str
    assigned_character_id: str | None
    assigned_character_name: str | None

    @property
    def assignable(self) -> bool:
        return self.assigned_character_id is None


@dataclass(frozen=True)
class CharacterRow:
    character_id: str
    display_name: str
    occupied_observation_id: str | None

    @property
    def available(self) -> bool:
        return self.occupied_observation_id is None


@dataclass(frozen=True)
class ReviewView:
    analysis_revision_id: str
    source_id: str
    source_frame: int
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


def present_review(review: object) -> ReviewView:
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
        candidates = tuple(
            CandidateRow(
                analysis_revision_id=item.analysis_revision_id,
                observation_id=item.observation_id,
                label=_candidate_label(index, item.observation_id),
                assigned_character_id=assignments.get(item.observation_id),
                assigned_character_name=names.get(assignments.get(item.observation_id)),
            )
            for index, item in enumerate(review.candidates, start=1)
        )
        characters = tuple(
            CharacterRow(
                character_id=item.character_id,
                display_name=item.display_name,
                occupied_observation_id=occupied_by_character.get(item.character_id),
            )
            for item in review.characters
        )
        return ReviewView(
            analysis_revision_id=review.analysis_revision_id,
            source_id=review.source_id,
            source_frame=review.frame,
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
        valid_characters = {item.character_id for item in view.characters if item.available}
        if self.character_id not in valid_characters:
            self.character_id = ""
        valid_candidates = {item.observation_id for item in view.candidates if item.assignable}
        if self.observation_id not in valid_candidates:
            self.observation_id = ""

    def can_assign(self, view: ReviewView) -> bool:
        return (
            view.state == "analyzed-with-candidates"
            and self.observation_id in {
                item.observation_id for item in view.candidates if item.assignable
            }
            and self.character_id in {
                item.character_id for item in view.characters if item.available
            }
        )


def user_message(error: BaseException) -> str:
    code = getattr(getattr(error, "code", None), "value", None)
    if code in ERROR_MESSAGES:
        return ERROR_MESSAGES[code]
    if isinstance(error, VtaReviewUiError):
        return str(error)
    return f"VTA review failed: {error}"


class PrivateCoreBackend:
    """Late-bound adapter to the separately installed private ``vta_core``."""

    def __init__(self, importer: Callable[[str], object] = importlib.import_module) -> None:
        try:
            self._core = importer("vta_core")
        except (ImportError, ModuleNotFoundError) as exc:
            raise VtaReviewUiError(
                "Private VTA core is unavailable in Blender's Python environment."
            ) from exc
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
        return present_review(review)

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
