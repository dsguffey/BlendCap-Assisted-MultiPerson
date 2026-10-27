"""Native Object Location keys/selection; never own a second correction track."""


def location_curves(obj):
    animation = obj.animation_data
    action = animation.action if animation else None
    if action is None:
        return ()
    # Blender 4.4+ Actions can contain several objects' slots. Never read a
    # sibling slot, even when its channels happen to have identical names.
    if getattr(action, 'is_action_layered', False):
        slot = animation.action_slot
        if slot is None:
            return ()
        curves = [curve for layer in action.layers for strip in layer.strips
                  if strip.type == 'KEYFRAME'
                  for bag in [strip.channelbag(slot)] if bag is not None
                  for curve in bag.fcurves]
    else:
        curves = action.fcurves
    return tuple(curve for curve in curves
                 if curve.data_path == 'location' and curve.array_index in (0, 1, 2))


def keyed_frames(obj):
    """Blender frames, including subframes; independent of author or muting."""
    return tuple(sorted({float(key.co.x) for curve in location_curves(obj)
                         for key in curve.keyframe_points}))


def scene_keyed_frames(scene, character_ids):
    """Only unique Scene-local generated rigs for the presented Characters."""
    result = {}
    for cid in character_ids:
        rigs = [obj for obj in scene.objects if obj.type == 'ARMATURE'
                and obj.get('vta_character_id') == cid
                and obj.get('vta_project_id') == scene.get('vta_project_id')
                and obj.get('vta_source_id') == scene.get('vta_source_id')]
        if len(rigs) == 1:
            result[cid] = keyed_frames(rigs[0])
    return result


def scene_rigs(scene, character_ids):
    """Resolve unique owned rigs; ambiguous duplicate Character tags are excluded."""
    result = {}
    for cid in character_ids:
        rigs = [o for o in scene.objects if o.type == 'ARMATURE'
                and o.get('vta_character_id') == cid
                and o.get('vta_project_id') == scene.get('vta_project_id')
                and o.get('vta_source_id') == scene.get('vta_source_id')]
        if len(rigs) == 1:
            result[cid] = rigs[0]
    return result


def selected_frames(obj):
    return tuple(sorted({float(k.co.x) for c in location_curves(obj)
                         for k in c.keyframe_points if k.select_control_point}))


def validate_edit(obj):
    import bpy
    data = obj.animation_data
    if (not obj.is_editable or len(obj.users_scene) != 1
            or data and (data.drivers or data.nla_tracks)
            or data and data.action and not data.action.is_editable):
        raise ValueError('Depth keys require editable Scene-local animation without NLA or drivers.')
    if data and data.action and any(o is not obj and o.animation_data
            and o.animation_data.action == data.action
            and getattr(o.animation_data, 'action_slot', None) == getattr(data, 'action_slot', None)
            for o in bpy.data.objects):
        raise ValueError('Character Action slot is shared; make animation single-user before editing Depth keys.')


def clear_selection(rigs):
    for other in rigs.values():
        try: validate_edit(other)
        except ValueError: continue
        for c in location_curves(other):
            for k in c.keyframe_points:
                k.select_control_point = k.select_left_handle = k.select_right_handle = False


def select_frame(rigs, character_id, frame, *, extend=False):
    """Native key flags only; Shift/Ctrl toggles a whole XYZ aggregate."""
    obj = rigs.get(character_id)
    if obj is None:
        raise ValueError('The Depth Character is not uniquely available in this Scene.')
    validate_edit(obj)
    hit = [k for c in location_curves(obj) for k in c.keyframe_points if abs(k.co.x-frame) < 1e-5]
    selected = not (extend and any(k.select_control_point for k in hit))
    if not extend:
        clear_selection(rigs)
    for k in hit:
        k.select_control_point = k.select_left_handle = k.select_right_handle = selected
    return bool(hit)


def selected_scene_frames(scene, character_ids):
    return {cid: selected_frames(obj) for cid, obj in scene_rigs(scene, character_ids).items()
            if selected_frames(obj)}


def delete_selected(scene, character_ids):
    """One displayed decision removes every XYZ native key at that frame."""
    rows = [(obj, selected_frames(obj)) for obj in scene_rigs(scene, character_ids).values()
            if selected_frames(obj)]
    for obj, _frames in rows: validate_edit(obj)  # Preflight every target before mutation.
    count = 0
    for obj, frames in rows:
        for c in location_curves(obj):
            for i in range(len(c.keyframe_points)-1, -1, -1):
                if any(abs(c.keyframe_points[i].co.x-f) < 1e-5 for f in frames):
                    c.keyframe_points.remove(c.keyframe_points[i], fast=True)
            c.update()
        obj.animation_data.action.update_tag()
        obj.update_tag()
        count += len(frames)
    return count


_KEY_FIELDS = ('co', 'handle_left_type', 'handle_right_type', 'interpolation', 'easing',
               'amplitude', 'back', 'period', 'type', 'select_control_point',
               'select_left_handle', 'select_right_handle', 'handle_left', 'handle_right')


def location_snapshot(obj):
    """Rollback only native Location insertion; no full Action swap or pose copying."""
    data = obj.animation_data
    return (data is not None, data.action if data else None,
            [(c, [{field:tuple(getattr(k, field)) if field in ('co','handle_left','handle_right')
                    else getattr(k, field) for field in _KEY_FIELDS} for k in c.keyframe_points])
             for c in location_curves(obj)])


def restore_location_snapshot(obj, snapshot):
    import bpy
    had_data, action, old = snapshot
    data = obj.animation_data
    if action is None:
        created = data.action if data else None
        if data: data.action = None
        if not had_data: obj.animation_data_clear()
        if created is not None and created.users == 0: bpy.data.actions.remove(created)
        return
    old_pointers = {c.as_pointer() for c, _ in old}
    for c in location_curves(obj):
        if c.as_pointer() in old_pointers: continue
        if action.is_action_layered:
            bags = [strip.channelbag(data.action_slot) for layer in action.layers
                    for strip in layer.strips if strip.type == 'KEYFRAME']
            container = next(bag.fcurves for bag in bags if bag is not None and any(fc == c for fc in bag.fcurves))
            container.remove(c)  # Do not access the freed RNA curve again.
        else: action.fcurves.remove(c)
    for c, points in old:
        while c.keyframe_points: c.keyframe_points.remove(c.keyframe_points[-1], fast=True)
        c.keyframe_points.add(len(points))
        for k, values in zip(c.keyframe_points, points):
            for field in _KEY_FIELDS: setattr(k, field, values[field])
        c.update()
    action.update_tag();obj.update_tag()
