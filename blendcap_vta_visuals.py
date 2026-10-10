"""Shared categorical provenance palette; existing VTA colors, not confidence."""

# Preserve the established green user keys and blue-grey accepted tracking ticks.
ASSIGNMENT_COLOR = (.45, .95, .48, 1)
TRACKING_COLOR = (.55, .68, .72, .55)
UNRESOLVED_COLOR = (.48, .5, .52, .4)
NEUTRAL_COLOR = (.1, .85, 1, 1)
SUPPRESSED_COLOR = (.45, .45, .45, .65)


def provenance_color(origin, *, label=False):
    color = {'human': ASSIGNMENT_COLOR, 'derived': TRACKING_COLOR,
             'neutral': NEUTRAL_COLOR, 'suppressed': SUPPRESSED_COLOR}[origin]
    return (*color[:3], 1) if label else color
