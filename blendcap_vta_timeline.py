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
class TimelineView:
    source_id: str
    characters: tuple[TimelineCharacter, ...]
    keys: tuple[TimelineKey, ...]


def present_timeline(private_timeline) -> TimelineView:
    characters = tuple(
        TimelineCharacter(item.character_id, item.display_name)
        for item in private_timeline.characters
    )
    keys = tuple(
        TimelineKey(item.keyframe_id, item.character_id, item.frame,
                    item.state.value, item.observation_id)
        for item in private_timeline.keys
    )
    if len({item.keyframe_id for item in keys}) != len(keys):
        raise ValueError("timeline has duplicate review-key identities")
    return TimelineView(private_timeline.source_id, characters, keys)


class ReviewKeySelection:
    """Transient stable-ID selection; neither rows nor frames are identities."""

    def __init__(self):
        self._ids: set[str] = set()

    @property
    def keyframe_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._ids))

    def reconcile(self, view: TimelineView | None) -> None:
        valid = {item.keyframe_id for item in view.keys} if view else set()
        self._ids.intersection_update(valid)

    def click(self, view: TimelineView, keyframe_id: str, *, extend: bool = False) -> bool:
        if keyframe_id not in {item.keyframe_id for item in view.keys}:
            return False
        if extend:
            if keyframe_id in self._ids:
                self._ids.remove(keyframe_id)
            else:
                self._ids.add(keyframe_id)
        else:
            self._ids = {keyframe_id}
        return True

    def clear(self) -> None:
        self._ids.clear()


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
