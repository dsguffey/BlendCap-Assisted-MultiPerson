"""Strict identity-only Blender organization; never camera calibration or authority."""
import bpy
from mathutils import Matrix


ROOT_TAG = 'vta_scene_root_version'
ROOT_VERSION = 1


def validate_scene_root(root, project_id, source_id):
    """Reject edited containers; never repair user placement implicitly."""
    if (not project_id or not source_id or root.type != 'EMPTY'
            or type(root.get(ROOT_TAG)) is not int or root.get(ROOT_TAG) != ROOT_VERSION
            or root.get('vta_project_id') != project_id or root.get('vta_source_id') != source_id
            or root.parent is not None or root.animation_data is not None or root.constraints
            or root.matrix_world != Matrix.Identity(4) or root.matrix_basis != Matrix.Identity(4)
            or tuple(root.location) != (0., 0., 0.)
            or tuple(root.rotation_euler) != (0., 0., 0.)
            or tuple(root.scale) != (1., 1., 1.)
            or tuple(root.delta_location) != (0., 0., 0.)
            or tuple(root.delta_rotation_euler) != (0., 0., 0.)
            or tuple(root.delta_scale) != (1., 1., 1.)):
        raise ValueError('VTA SceneRoot is unsupported or edited; generation requires an owned, unanimated identity Empty')
    return root


def supported_parent(obj):
    if obj.parent is None:
        return True
    try:
        validate_scene_root(obj.parent, obj.get('vta_project_id'), obj.get('vta_source_id'))
    except ValueError:
        return False
    return obj.parent_type == 'OBJECT' and obj.matrix_parent_inverse == Matrix.Identity(4)


def find_scene_root(scene, project_id, source_id):
    # Presence of the dedicated tag finds malformed owned roots as well; a
    # moved/unsupported-version root must fail, not produce a second container.
    roots = [obj for obj in scene.objects if ROOT_TAG in obj
             and obj.get('vta_project_id') == project_id and obj.get('vta_source_id') == source_id]
    if len(roots) > 1:
        raise ValueError('Multiple VTA SceneRoots own this project/source; resolve duplicate containers first')
    return validate_scene_root(roots[0], project_id, source_id) if roots else None


def ensure_scene_root(scene, project_id, source_id):
    root = find_scene_root(scene, project_id, source_id)
    if root is None:
        root = bpy.data.objects.new('VTA_SceneRoot', None)
        scene.collection.objects.link(root)
        root.empty_display_type = 'PLAIN_AXES'
        root[ROOT_TAG] = ROOT_VERSION
        root['vta_project_id'], root['vta_source_id'] = project_id, source_id
    return validate_scene_root(root, project_id, source_id)


def parent_to_scene_root(obj, root):
    """Identity parent/inverse: do not decompose world matrices or touch LRS/keys.

    Caller snapshots hierarchy BEFORE calling and includes newly created roots
    in its existing datablock transaction. Fresh imports are registered first.
    """
    validate_scene_root(root, obj.get('vta_project_id'), obj.get('vta_source_id'))
    if not supported_parent(obj) or (obj.parent is not None and obj.parent != root):
        raise ValueError('VTA object has an unsupported parent; cannot adopt SceneRoot safely')
    obj.parent = root
    obj.parent_type = 'OBJECT'
    obj.matrix_parent_inverse = Matrix.Identity(4)
