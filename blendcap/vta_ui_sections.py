"""Presentation-only drawers and cached support information."""
from functools import lru_cache


def section(layout, identity, title):
    header, body = layout.panel('vta_' + identity, default_closed=True)
    header.label(text=title)
    return body


@lru_cache(maxsize=1)
def version_lines():
    from .. import vta_protocol as wire
    from ..blendcap_vta_runtime import load_runtime
    from ..vta_protocol.runtime import PRODUCT_VERSION
    try:
        runtime = load_runtime()
        # Configuration is validated by the existing public runtime resolver.
        return (f"VTA: {PRODUCT_VERSION}", f'Protocol: {wire.VERSION}',
                'ProjectState: 0.9',
                'Deployment: ' + runtime['engine_root'].parent.name)
    except (OSError, ValueError, KeyError, RuntimeError):
        return (f'Protocol: {wire.VERSION}', 'Runtime details unavailable; use setup/repair.')
