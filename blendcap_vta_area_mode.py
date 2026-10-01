"""Blender-independent persistence for opt-in VTA editor roles.

Blender Areas and Spaces cannot hold ID properties in 5.2. A Screen can, so
roles are saved against that Screen's ordered editor slots. A changed editor
topology invalidates the entire map (fail closed) rather than silently moving
a VTA role to a different editor. Area pointers are used only during a call.
"""

from __future__ import annotations

import json


MODE_KEY = "blendcap_vta_editor_modes_v1"
WORKSPACE_TAG = "blendcap_vta_review_workspace"
ROLE_TIMELINE = "review-timeline"
ROLE_PREVIEW = "preview"
_ROLES = {ROLE_TIMELINE, ROLE_PREVIEW}


def _signature(screen):
    return [[area.type, area.ui_type] for area in screen.areas]


def _index(screen, area):
    return next((i for i, item in enumerate(screen.areas)
                 if item is area or (hasattr(item, 'as_pointer') and item == area)), None)


def _supported(area, role):
    if role == ROLE_TIMELINE:
        return area.type == "DOPESHEET_EDITOR" and area.ui_type == "DOPESHEET"
    if role == ROLE_PREVIEW:
        return area.type == "SEQUENCE_EDITOR"
    return False


def _stored(screen):
    raw = screen.get(MODE_KEY)
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get("version") != 1
            or value.get("signature") != _signature(screen)
            or not isinstance(value.get("roles"), dict)):
        return None
    roles = value["roles"]
    if any(not isinstance(key, str) or not key.isdecimal()
           or role not in _ROLES for key, role in roles.items()):
        return None
    if sum(role == ROLE_TIMELINE for role in roles.values()) > 1:
        return None
    return roles


def _legacy_preset_role(workspace, screen, area):
    """Recognize the first bundled workspace without silently changing it.

    Writing an explicit map (including an empty one) disables this one-time
    compatibility path, so Exit VTA Mode persists across reloads.
    """
    if workspace is None or workspace.get(WORKSPACE_TAG) != 1:
        return None
    if _supported(area, ROLE_TIMELINE):
        matches = [item for item in screen.areas if _supported(item, ROLE_TIMELINE)]
        return ROLE_TIMELINE if len(matches) == 1 else None
    if _supported(area, ROLE_PREVIEW):
        return ROLE_PREVIEW
    return None


def role_for(workspace, screen, area):
    """Read the mode of one current Area; never treat an Area pointer as state."""
    if screen is None or area is None:
        return None
    index = _index(screen, area)
    if index is None:
        return None
    roles = _stored(screen)
    if roles is not None:
        role = roles.get(str(index))
        return role if _supported(area, role) else None
    # An invalid explicit map fails closed. Only an absent map may migrate the
    # older tagged workspace asset.
    if screen.get(MODE_KEY) is not None:
        return None
    return _legacy_preset_role(workspace, screen, area)


def set_role(workspace, screen, area, role):
    """Persist one editor's role, with no accidental duplicate timeline host."""
    if screen is None or area is None:
        raise ValueError("An editor area is required.")
    index = _index(screen, area)
    if index is None:
        raise ValueError("The editor does not belong to the active screen.")
    if role is not None and not _supported(area, role):
        raise ValueError("This editor does not support the requested VTA mode.")
    roles = _stored(screen)
    if roles is None:
        roles = {str(i): _legacy_preset_role(workspace, screen, item)
                 for i, item in enumerate(screen.areas)} if screen.get(MODE_KEY) is None else {}
        roles = {key: value for key, value in roles.items() if value is not None}
    key = str(index)
    if role is None:
        roles.pop(key, None)
    else:
        if role == ROLE_TIMELINE and any(
            other != key and value == ROLE_TIMELINE for other, value in roles.items()
        ):
            raise ValueError("This screen already has a VTA Review Timeline.")
        roles[key] = role
    screen[MODE_KEY] = json.dumps({"version": 1,
                                   "signature": _signature(screen),
                                   "roles": roles}, sort_keys=True, separators=(",", ":"))


def initialize_workspace_preset(workspace, screen):
    """Make the bundled layout explicit without overriding a saved user choice."""
    if screen.get(MODE_KEY) is not None or workspace.get(WORKSPACE_TAG) != 1:
        return
    roles = {str(i): role for i, area in enumerate(screen.areas)
             if (role := _legacy_preset_role(workspace, screen, area)) is not None}
    screen[MODE_KEY] = json.dumps({"version": 1,
                                   "signature": _signature(screen),
                                   "roles": roles}, sort_keys=True, separators=(",", ":"))
