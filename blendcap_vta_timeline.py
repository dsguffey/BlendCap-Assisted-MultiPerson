"""Blender-independent, non-authoritative VTA review-lane presentation."""

from __future__ import annotations

from dataclasses import dataclass


KEY_LABELS = {
    "visible": "Candidate Assignment",
    "partially-visible": "Candidate Assignment",
    "occluded": "Occluded",
    "no-valid-candidate": "No Valid Candidate",
    "not-visible": "Not Visible",
}


@dataclass(frozen=True)
class TimelineCharacter:
    character_id: str
    display_name: str


@dataclass(frozen=True)
class TimelineKey:
    keyframe_id: str
    character_id: str
    source_frame: int
    state: str
    observation_id: str | None

    @property
    def label(self) -> str:
        return KEY_LABELS[self.state]

    @property
    def is_assignment(self) -> bool:
        return self.observation_id is not None


@dataclass(frozen=True)
class TimelineDerived:
    character_id: str
    source_frame: int
    state: str


@dataclass(frozen=True)
class TimelineMotionInterval:
    character_id: str
    start_frame: int
    end_frame: int


@dataclass(frozen=True)
class TimelineView:
    source_id: str
    characters: tuple[TimelineCharacter, ...]
    keys: tuple[TimelineKey, ...]
    derived: tuple[TimelineDerived, ...] = ()
    untrusted_motion_intervals: tuple[TimelineMotionInterval, ...] = ()


def present_timeline(private_timeline) -> TimelineView:
    characters = sorted((
        TimelineCharacter(item.character_id, item.display_name)
        for item in private_timeline.characters
    ), key=lambda item: (item.display_name.casefold(), item.display_name,
                         item.character_id))
    keys = tuple(
        TimelineKey(item.keyframe_id, item.character_id, item.frame,
                    item.state.value, item.observation_id)
        for item in private_timeline.keys
    )
    if len({item.keyframe_id for item in keys}) != len(keys):
        raise ValueError("timeline has duplicate review-key identities")
    derived = tuple(
        TimelineDerived(item.character_id, item.frame, item.state.value)
        for item in getattr(private_timeline, "derived", ())
    )
    intervals = tuple(TimelineMotionInterval(i.character_id, i.start_frame, i.end_frame)
                      for i in getattr(private_timeline, "untrusted_motion_intervals", ()))
    return TimelineView(private_timeline.source_id, tuple(characters), keys, derived, intervals)


class TimelineCharacterSelection:
    """Session-only selected set plus one active member, ordered by displayed rows."""

    def __init__(self):
        self._ids = set()
        self.active_id = None

    @property
    def character_ids(self):
        return tuple(sorted(self._ids))

    def reconcile(self, view):
        ordered = () if view is None else tuple(c.character_id for c in view.characters)
        self._ids.intersection_update(ordered)
        if self.active_id not in self._ids:
            self.active_id = next((cid for cid in ordered if cid in self._ids), None)

    def click(self, view, character_id, *, extend=False):
        if character_id not in {c.character_id for c in view.characters}:
            return False
        if extend and character_id in self._ids:
            self._ids.remove(character_id)
        else:
            self._ids = self._ids | {character_id} if extend else {character_id}
            self.active_id = character_id
        self.reconcile(view)
        return True

    def select_box(self, view, character_ids, *, extend=False, active_id=None):
        ids = set(character_ids)
        if not ids <= {c.character_id for c in view.characters}:
            return False
        self._ids = self._ids | ids if extend else ids
        if active_id in self._ids:
            self.active_id = active_id
        self.reconcile(view)
        return True


class ReviewKeySelection:
    """Transient stable-ID selection; neither rows nor frames are identities."""

    def __init__(self):
        self._ids: set[str] = set()
        self._active_id: str | None = None

    @property
    def keyframe_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._ids))

    @property
    def active_keyframe_id(self) -> str | None:
        return self._active_id

    def reconcile(self, view: TimelineView | None) -> None:
        valid = {item.keyframe_id for item in view.keys} if view else set()
        self._ids.intersection_update(valid)
        if self._active_id not in self._ids:
            self._active_id = min(self._ids) if self._ids else None

    def click(self, view: TimelineView, keyframe_id: str, *, extend: bool = False) -> bool:
        if keyframe_id not in {item.keyframe_id for item in view.keys}:
            return False
        if extend:
            if keyframe_id in self._ids:
                self._ids.remove(keyframe_id)
                if self._active_id == keyframe_id:
                    self._active_id = min(self._ids) if self._ids else None
            else:
                self._ids.add(keyframe_id)
                self._active_id = keyframe_id
        else:
            self._ids = {keyframe_id}
            self._active_id = keyframe_id
        return True

    def activate(self, view: TimelineView, keyframe_id: str) -> bool:
        """Right-click activates a durable key, retaining an existing group."""
        if keyframe_id not in {item.keyframe_id for item in view.keys}:
            return False
        if keyframe_id not in self._ids:
            self._ids = {keyframe_id}
        self._active_id = keyframe_id
        return True

    def select_box(self, view: TimelineView, keyframe_ids, *, extend: bool = False) -> bool:
        """Replace or add a group of durable keys; additive selection never toggles."""
        ids = set(keyframe_ids)
        if not ids.issubset({item.keyframe_id for item in view.keys}):
            return False
        self._ids = self._ids | ids if extend else ids
        if self._active_id not in self._ids:
            self._active_id = min(self._ids) if self._ids else None
        return True

    def clear(self) -> None:
        self._ids.clear()
        self._active_id = None


def key_at(view: TimelineView, keyframe_id: str) -> TimelineKey | None:
    return next((item for item in view.keys if item.keyframe_id == keyframe_id), None)


def lane_layout(view: TimelineView, *, top: float, row_height: float):
    """Return stable-ID lane centers; empty Character rows remain present."""
    return tuple(
        (item.character_id, top - row_height * (index + .5))
        for index, item in enumerate(view.characters)
    )


def hit_test_keys(
    view: TimelineView, lane_centers, *, frame_to_x, point, radius: float = 9.0,
):
    """Hit-test projected symbols without consulting semantic candidate numbers."""
    centers = dict(lane_centers)
    px, py = point
    hits = []
    for key in view.keys:
        x = frame_to_x(key.source_frame)
        y = centers.get(key.character_id)
        if y is not None and abs(x - px) <= radius and abs(y - py) <= radius:
            hits.append(((x - px) ** 2 + (y - py) ** 2, key.keyframe_id))
    return min(hits)[1] if hits else None
