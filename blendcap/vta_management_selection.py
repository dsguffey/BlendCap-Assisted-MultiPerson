"""Management-only, session-scoped presentation selection. No project writes."""
import bpy

_targets = {}


def scope():
    from . import vta_review_ui as ui
    if ui._view is None or ui._location is None or not hasattr(ui._view, 'source_id') or not hasattr(ui._location, 'companion_root'): return None
    identity = ui._assignment_scope()
    return (identity, ui.pointer(ui._presentation_scene)) if identity is not None else None


def selected():
    from . import vta_review_ui as ui
    target = _targets.get(scope())
    return target if ui._view and any(c.character_id == target for c in ui._view.characters) else None


def select(character_id):
    key = scope()
    if key is not None:
        if character_id: _targets[key] = character_id
        else: _targets.pop(key, None)


def get_index(_self):
    from . import vta_review_ui as ui
    return next((i for i, row in enumerate(ui._character_enum_items) if row[0] == selected()), 0)


def set_index(_self, index):
    from . import vta_review_ui as ui
    if 0 <= index < len(ui._character_enum_items):
        value = ui._character_enum_items[index][0]
        select(None if value == ui._NO_SELECTION else value)
        ui.vta_invalidation.request_redraw()


@bpy.app.handlers.persistent
def clear(*_args):
    _targets.clear()
