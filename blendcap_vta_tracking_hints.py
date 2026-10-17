"""Bounded session-only tracking guidance. No disk reads, writes or authority."""
from dataclasses import dataclass


@dataclass(frozen=True)
class KeyHint:
    character_id: str
    frame: int  # Blender frame, not source frame
    direction: str
    reason: str


class TrackingHints:
    def __init__(self):
        self.entries = {}

    def clear(self):
        self.entries.clear()

    def remember(self, key, stamp, outcomes, offset):
        hints = {}
        for item in outcomes:
            if item.update is None:
                continue
            for stop in getattr(item.update, 'ambiguities', ())[:3]:
                if (stop.reason not in {'competing-candidates', 'directional-disagreement'}
                        or stop.direction not in {'forward', 'backward', 'both'}):
                    continue
                hint = KeyHint(item.character_id, stop.frame + offset, stop.direction, stop.reason)
                hints.setdefault((hint.character_id, hint.frame), hint)
        self.entries.pop(key, None)
        if hints:
            self.entries[key] = stamp, tuple(hints.values())
        while len(self.entries) > 32:
            self.entries.pop(next(iter(self.entries)))

    def current(self, key, stamp, characters):
        entry = self.entries.get(key)
        if entry is None:
            return ()
        if entry[0] != stamp:
            self.entries.pop(key, None)
            return ()
        return tuple(h for h in entry[1] if h.character_id in characters)

    def dismiss(self, key, character_id, frame):
        entry = self.entries.get(key)
        if entry is None:
            return False
        remaining = tuple(h for h in entry[1] if (h.character_id, h.frame) != (character_id, frame))
        if len(remaining) == len(entry[1]):
            return False
        if remaining:
            self.entries[key] = entry[0], remaining
        else:
            self.entries.pop(key)
        return True


def marker_positions(hints, lanes, frame_to_x, width, radius):
    """Same row centers as human keys, but not members of the key collection."""
    lanes = dict(lanes)
    return tuple((h, x, lanes[h.character_id]) for h in hints
        if h.character_id in lanes and radius <= (x := frame_to_x(h.frame)) <= width-radius)


def hit_marker(markers, point, radius):
    matches = [(abs(x-point[0])+abs(y-point[1]), h) for h,x,y in markers
        if abs(x-point[0]) <= radius and abs(y-point[1]) <= radius]
    return min(matches, key=lambda item: (item[0], item[1].frame, item[1].character_id))[1] if matches else None
