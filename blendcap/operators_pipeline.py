"""Installation and existing-data BVH utilities; no legacy capture planner.

Modern VTA analysis and reviewed reconstruction have dedicated workers.
"""
import bpy
import os
import sys
import shlex
import shutil
import subprocess
import threading
import textwrap
import time

from .helpers import (
    blendcap_state, bvh_state,
    _cache_dir, _bvh_library_dir, _addon_dir, _python_exe,
    _resolve_video_path, _resolve_clip_name, _strip_capture_prefix,
    _next_free_clip_name, _active_video_strip, _file_mode_source,
    source_mode_error,
    _strip_visible_start, _strip_visible_end,
    check_npz_coverage, _show_warning_popup,
    _cache_params_match, _SUPPORTED_VIDEO_EXTS,
    _installer_dir, _install_marker_path, _license_path,
    _host_spawn_prefix, _host_spawn_ok, _host_spawn_grant_cmd, _in_flatpak,
    _subprocess_env,
    read_install_marker, is_dependencies_installed,
    addon_prefs, _tag_redraw, _terminate_state_process,
)
from .mocap import (
    run_bvh_thread, monitor_bvh_timer,
)






def _bvh_face_denoise_args(props):
    """Build the CLI args for BVH face denoising from the addon props.

    Property values and CLI flag both use [0,1] blendshape units; no
    conversion needed. Returns an empty list when the master toggle
    is off so the subprocess command stays uncluttered.
    """
    if not getattr(props, "bvh_face_denoise_enabled", False):
        return []
    args = ["--face-denoise-enabled"]
    for region in ("jaw", "gaze", "lids", "brows", "lips", "cheeks", "nose", "tongue"):
        val = float(getattr(props, f"bvh_face_denoise_{region}", 0.0))
        if val > 0:
            args += [f"--face-denoise-{region}", f"{val:.4f}"]
    return args


def _bvh_face_boost_value(props):
    """Effective face-boost value for the BVH export CLI.

    Forces 1.0 (no boost) when per-region Face Motion Scaling is on,
    since the two would compound. The UI greys the global multiplier
    in that case; this enforces the same behavior in the actual
    subprocess command.
    """
    if getattr(props, "bvh_face_amp_enabled", False):
        return 1.0
    return float(props.face_expression_multiplier)


def _bvh_face_amp_args(props):
    """Build the CLI args for BVH per-region face amplitude scaling.

    Returns empty list when the master toggle is off, or when every
    region is at 1.0 (1:1 pass-through, nothing to bake).
    """
    if not getattr(props, "bvh_face_amp_enabled", False):
        return []
    args = ["--face-amp-enabled"]
    any_non_unit = False
    for region in ("jaw", "gaze", "lids", "brows", "lips", "cheeks", "nose", "tongue"):
        val = float(getattr(props, f"bvh_face_amp_{region}", 1.0))
        if val != 1.0:
            args += [f"--face-amp-{region}", f"{val:.4f}"]
            any_non_unit = True
    return args if any_non_unit else []


def _bvh_depth_denoise_args(props):
    """Build the CLI args for BVH body depth-axis noise filtering.

    Sliders are in cm/frame; the CLI takes meters/frame. Returns an
    empty list when the master toggle is off or every region is 0
    (nothing to suppress).
    """
    if not getattr(props, "bvh_depth_denoise_enabled", False):
        return []
    args = []
    any_on = False
    for region in ("feet", "hands", "head", "root"):
        val_cm = float(getattr(props, f"bvh_depth_denoise_{region}", 0.0))
        if val_cm > 0.0:
            args += [f"--denoise-depth-{region}", f"{val_cm / 100.0:.4f}"]
            any_on = True
    return args if any_on else []


# ============================================================
# MAIN OPERATORS
# ============================================================
# Plain-English digest of the Meta SAM License key terms. The full text
# ships in licenses/SAM_LICENSE.txt and is one click away via the
# "View Full SAM License" button — this digest is the at-a-glance version,
# not a substitute for the agreement the user is accepting.
_SAM_LICENSE_SUMMARY = (
    "BlendCap's body tracking uses Meta's SAM 3D Body model, which is",
    "covered by the Meta SAM License. By installing, you agree to it:",
    "",
    "    - Commercial use is allowed, royalty-free.",
    "    - You may copy, modify and redistribute the model, but must",
    "      include a copy of the SAM License when you do.",
    "    - Prohibited uses: military / warfare, weapons, nuclear,",
    "      espionage, and anything barred by U.S. trade-control law.",
    "    - Provided \"as is\" - no warranty and no Meta support.",
)


class BLENDCAP_OT_open_license(bpy.types.Operator):
    bl_idname = "blendcap.open_license"
    bl_label = "View Full License"
    bl_description = "Open the full license text in your system's default viewer"
    bl_options = {'INTERNAL'}

    # Bundled license filename, resolved by helpers._license_path (root
    # for LICENSE/NOTICE.txt, licenses/ for the rest). Defaults to the
    # SAM License since that's the one the user is being asked to agree to.
    filename: bpy.props.StringProperty(default="SAM_LICENSE.txt")  # type: ignore

    def execute(self, context):
        path = _license_path(self.filename)
        if not os.path.exists(path):
            self.report({'ERROR'}, f"License file not found: {path}")
            return {'CANCELLED'}
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: Windows-only, guarded above
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.report({'ERROR'}, f"Couldn't open license file: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


# ------------------------------------------------------------
# Install wizard (INLINE in the Add-on Preferences panel)
# ------------------------------------------------------------
# NOT a popup. Blender's popups can't give all of: a greyable button, control
# over button order, no clipping, and a working Cancel — invoke_popup has no
# button that closes it, and invoke_props_dialog won't let you grey or reorder
# its native footer buttons. So the wizard is drawn INLINE in the Add-on
# Preferences draw() (see draw_install_wizard below), where every button is an
# ordinary panel button: greyable via row.enabled, positioned by layout order,
# and never clipped (it just scrolls with the panel).
#
# Session state lives on WindowManager (registration.py): blendcap_install_
# active (wizard vs idle Install button), _page (INTRO/LICENSE/CONFIRM),
# _agreed (license checkbox), _force (reinstall vs first install). Forward/back
# are prop_enum on _page (in-place redraw); Cancel + the final Install are
# operators. The license gate is the greyed Continue button — it can't be
# clicked until _agreed is True, so the Confirm/Install page is unreachable
# without ticking the box. The viewport Setup button (panels.py) just jumps to
# this prefs section via blendcap.install_open_prefs.
class BLENDCAP_LicenseLine(bpy.types.PropertyGroup):
    """One display line of license text for the scrollable viewer."""
    text: bpy.props.StringProperty(default="")  # type: ignore


class BLENDCAP_UL_license_text(bpy.types.UIList):
    """Read-only scrollable viewer for a license file. Each item is one
    pre-wrapped line; the UIList supplies the scrollbar a column of labels
    in a popup can't."""
    bl_idname = "BLENDCAP_UL_license_text"

    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index):
        # A blank string renders as a zero-height row; a single space keeps
        # paragraph breaks visible.
        layout.label(text=item.text if item.text else " ")

    def filter_items(self, context, data, propname):
        # Preserve file order, no name filter. Empty lists => show all,
        # don't reorder.
        return [], []


def _load_license_lines(context, filename="SAM_LICENSE.txt", width=58):
    """Read a bundled license file, soft-wrap it to `width` columns, and push
    the lines into the WindowManager collection the scrollable viewer binds
    to. Called when the wizard starts (BLENDCAP_OT_install_dependencies)."""
    wm = context.window_manager
    wm.blendcap_license_lines.clear()
    path = _license_path(filename)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except Exception:
        for msg in ("(Could not read the bundled license file at:",
                    " " + path + ")"):
            wm.blendcap_license_lines.add().text = msg
        return
    for line in raw.splitlines():
        if not line.strip():
            wm.blendcap_license_lines.add().text = ""
            continue
        for piece in (textwrap.wrap(line, width=width,
                                    break_long_words=False,
                                    break_on_hyphens=False) or [""]):
            wm.blendcap_license_lines.add().text = piece


# Approximate model + environment download size, and the free-disk figure we
# show users - the SAME 32 GB the documentation asks for, so the two sources
# never disagree. The install scripts enforce a lower silent floor (hard-fail
# below 12 GB, where the install genuinely can't fit; warn below 32) but all
# user-facing text speaks only in the documented 32 GB.
_INSTALL_DOWNLOAD_GB = 11
_INSTALL_FREE_SPACE_GB = 32


# --- inline wizard page bodies ---------------------------------------------
def _wizard_intro(layout):
    layout.label(text="Install AI Tracking Dependencies", icon='CONSOLE')
    layout.separator()
    col = layout.column(align=True)
    for line in (
        "BlendCap's tracking runs on a bundled, isolated Python",
        "environment plus a set of AI models (SAM 3D Body and",
        "supporting networks). This one-time setup will:",
        "",
        "    1. Build the bundled Python environment.",
        f"    2. Download the AI models (about {_INSTALL_DOWNLOAD_GB} GB).",
        "",
        "You'll need a working internet connection, a compatible GPU,",
        f"and at least {_INSTALL_FREE_SPACE_GB} GB of free disk space.",
    ):
        col.label(text=line)


def _wizard_license(context, layout):
    wm = context.window_manager
    layout.label(text="Meta SAM License Agreement", icon='TEXT')
    # License on the left (wider), summary digest on the right.
    split = layout.split(factor=0.62)
    left = split.column()
    left.label(text="Full license (scroll to read):")
    left.template_list("BLENDCAP_UL_license_text", "", wm,
                       "blendcap_license_lines", wm,
                       "blendcap_license_index", rows=14)
    right = split.column()
    right.label(text="Summary", icon='INFO')
    sbox = right.box().column(align=True)
    for line in _SAM_LICENSE_SUMMARY:
        sbox.label(text=line)

    layout.separator()
    layout.prop(wm, "blendcap_install_agreed")


def _wizard_confirm(layout, verb):
    layout.label(text=f"Ready to {verb}", icon='IMPORT')
    layout.separator()
    col = layout.column(align=True)
    lines = [
        f"Press {verb} to open a separate console window and:",
        "",
        "    - Build the bundled Python environment.",
        "    - Download the SAM 3D Body model and supporting",
        f"      AI models (about {_INSTALL_DOWNLOAD_GB} GB - needs internet).",
        "",
        "If they aren't already on your system, it also fetches",
        "these:",
        "    - uv (the Python environment manager)",
        "    - Git (used to fetch some components)",
    ]
    # The VC++ Redistributable is a Windows-only system component, and
    # the ONLY tool whose install needs permissions (uv and Git arrive
    # as portable app-local copies).
    if sys.platform == "win32":
        lines.append("    - Microsoft Visual C++ Redistributable")
        lines.append("      (if missing, Windows asks for permissions")
        lines.append("      in order to install it)")
    lines += [
        "",
        f"You'll need at least {_INSTALL_FREE_SPACE_GB} GB of free disk space.",
        "The first run can take a while. You can keep using Blender;",
        "BlendCap reports when it's ready.",
    ]
    for line in lines:
        col.label(text=line)


def _draw_flatpak_grant_warning(layout):
    """If running under Flatpak WITHOUT the host-spawn grant, draw a red
    alert with the one-time command the user must run on the host. Drawn at
    the top of the install wizard so it's seen before Install is pressed
    (the installer itself, and later every capture, run via flatpak-spawn
    and would fail without the grant). No-op on every other platform and
    once the grant is present (_host_spawn_ok is cached, so cheap to call
    from this draw path)."""
    if not _in_flatpak() or _host_spawn_ok():
        return
    box = layout.box()
    box.alert = True
    col = box.column(align=True)
    col.label(text="Flatpak: one-time permission needed", icon='ERROR')
    col.label(text="Blender is sandboxed. Run this in a terminal, then")
    col.label(text="restart Blender, before installing:")
    col.separator()
    cmd = box.box()
    cmd.label(text=_host_spawn_grant_cmd())
    box.operator("blendcap.copy_grant_cmd", text="Copy Command", icon='COPYDOWN')


class BLENDCAP_OT_copy_grant_cmd(bpy.types.Operator):
    bl_idname = "blendcap.copy_grant_cmd"
    bl_label = "Copy Flatpak Permission Command"
    bl_description = ("Copy the one-time 'flatpak override' command to the "
                      "clipboard so you can paste it into a host terminal")
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        context.window_manager.clipboard = _host_spawn_grant_cmd()
        self.report({'INFO'}, "Command copied - paste it in a terminal, then "
                              "restart Blender.")
        return {'FINISHED'}


def draw_install_wizard(context, layout):
    """Render the inline install wizard inside the Add-on Preferences panel.

    Real panel buttons throughout: greyable via row.enabled, ordered by layout,
    never clipped. Forward/back are prop_enum on the WindowManager page property
    (in-place redraw); Cancel and the final Install are operators. The greyed
    Continue button on the license page is the license gate.
    """
    wm = context.window_manager
    page = wm.blendcap_install_page
    # "Reinstall" only when an environment is actually present right now;
    # "Install" otherwise. Checked live (not via the entry button's flag) so
    # the label always reflects the real state.
    verb = "Reinstall" if is_dependencies_installed() else "Install"

    # Flatpak host-spawn grant must be in place before Install (the installer
    # runs on the host via flatpak-spawn). Shown above the wizard box.
    _draw_flatpak_grant_warning(layout)

    box = layout.box()
    if page == 'LICENSE':
        _wizard_license(context, box)
    elif page == 'CONFIRM':
        _wizard_confirm(box, verb)
    else:
        _wizard_intro(box)

    box.separator()
    footer = box.row(align=True)
    footer.scale_y = 1.3
    # Cancel — far left.
    footer.operator("blendcap.install_cancel", text="Cancel", icon='X')
    # Back — present past the first page.
    if page == 'LICENSE':
        footer.prop_enum(wm, "blendcap_install_page", 'INTRO',
                         text="Back", icon='BACK')
    elif page == 'CONFIRM':
        footer.prop_enum(wm, "blendcap_install_page", 'LICENSE',
                         text="Back", icon='BACK')
    # Forward — far right.
    fwd = footer.row(align=True)
    if page == 'INTRO':
        fwd.prop_enum(wm, "blendcap_install_page", 'LICENSE',
                      text="Continue", icon='FORWARD')
    elif page == 'LICENSE':
        # Greyed until the agree box is ticked — the hard license gate.
        fwd.enabled = wm.blendcap_install_agreed
        fwd.prop_enum(wm, "blendcap_install_page", 'CONFIRM',
                      text="Continue", icon='FORWARD')
    else:  # CONFIRM
        fwd.operator("blendcap.install_run", text=verb, icon='IMPORT')


class BLENDCAP_OT_install_dependencies(bpy.types.Operator):
    bl_idname = "blendcap.install_dependencies"
    bl_label = "Install AI Dependencies"
    bl_description = ("Set up BlendCap's AI tracking dependencies (a bundled "
                      "Python environment + the AI models). Opens an inline "
                      "wizard: intro, the Meta SAM License, then a "
                      "confirmation before running the installer. Skips if "
                      "already installed.")
    bl_options = {'REGISTER'}

    # True for the reinstall path (bypasses the already-installed skip).
    force: bpy.props.BoolProperty(  # type: ignore
        default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        self.report({'ERROR'}, 'MoCap setup/repair manages its separate runtimes. The legacy in-addon installer is retired.')
        return {'CANCELLED'}


class BLENDCAP_OT_install_cancel(bpy.types.Operator):
    bl_idname = "blendcap.install_cancel"
    bl_label = "Cancel"
    bl_description = "Close the installer without installing anything"
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        wm = context.window_manager
        wm.blendcap_install_active = False
        wm.blendcap_install_page = 'INTRO'
        wm.blendcap_install_agreed = False
        _tag_redraw('PREFERENCES')
        return {'FINISHED'}


class BLENDCAP_OT_install_run(bpy.types.Operator):
    bl_idname = "blendcap.install_run"
    bl_label = "Run Installer"
    bl_description = "Launch the BlendCap dependency installer"
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        self.report({'ERROR'}, 'MoCap setup/repair manages its separate runtimes. The legacy in-addon installer is retired.')
        return {'CANCELLED'}


class BLENDCAP_OT_install_open_prefs(bpy.types.Operator):
    bl_idname = "blendcap.install_open_prefs"
    bl_label = "Open MoCap Preferences"
    bl_description = ("Open persistent MoCap preferences; runtime setup is separate. "
                      "")
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        # The AddonPreferences (and the wizard) live in the ROOT package, one
        # level up from this blendcap/ subpackage.
        root_pkg = (__package__ or "").rpartition('.')[0]
        try:
            bpy.ops.preferences.addon_show(module=root_pkg)
            return {'FINISHED'}
        except Exception:
            pass
        # Fallback: open the Preferences window at the Add-ons section.
        try:
            bpy.ops.screen.userpref_show('INVOKE_DEFAULT')
            context.preferences.active_section = 'ADDONS'
        except Exception:
            self.report({'INFO'},
                        "Open Edit > Preferences > Add-ons > MoCap. "
                        "")
        return {'FINISHED'}


def _rm_readonly(func, path, _exc):
    """shutil.rmtree onerror hook: clear the read-only bit and retry the
    delete. venvs and bundled git checkouts contain read-only files that
    rmtree otherwise can't remove on Windows. Swallows anything it still
    can't delete - the caller re-checks whether the directory survived."""
    import stat
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


class BLENDCAP_OT_uninstall_dependencies(bpy.types.Operator):
    bl_idname = "blendcap.uninstall_dependencies"
    bl_label = "Uninstall AI Dependencies"
    bl_description = ("Remove BlendCap's installed AI dependencies - the "
                      "bundled Python environment and the downloaded AI "
                      "models - to free up disk space. You can reinstall "
                      "them later from this panel")
    bl_options = {'REGISTER', 'INTERNAL'}

    # ARM shows the inline confirm block, CANCEL hides it, RUN performs the
    # delete. Inline (driven via the prefs panel) rather than a popup because
    # dialogs launched from the Preferences window get clipped.
    mode: bpy.props.EnumProperty(  # type: ignore
        items=[('ARM', "Arm", ""), ('RUN', "Run", ""), ('CANCEL', "Cancel", "")],
        default='ARM', options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        self.report({'ERROR'}, 'MoCap setup/repair manages its separate runtimes. The legacy in-addon installer is retired.')
        return {'CANCELLED'}


class BLENDCAP_OT_deps_missing(bpy.types.Operator):
    """Friendly blocking dialog shown when a capture is started before the AI
    dependencies are installed. Beats a cryptic 'Cannot find Python' report —
    it explains what's missing and offers a one-click jump to the installer."""
    bl_idname = "blendcap.deps_missing"
    bl_label = "AI Dependencies Required"
    bl_options = {'REGISTER', 'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="BlendCap's AI tracking isn't installed yet.",
                  icon='ERROR')
        col.separator()
        col.label(text="Capturing needs a one-time setup: a bundled Python")
        col.label(text="environment and the AI models (about "
                       f"{_INSTALL_DOWNLOAD_GB} GB).")
        col.separator()
        col.label(text="Open the Add-on Preferences and click")
        col.label(text="\"Install Dependencies\", then try again.")
        layout.separator()
        layout.operator("blendcap.install_open_prefs",
                        text="Open Preferences", icon='PREFERENCES')

    def execute(self, context):
        return {'FINISHED'}


class BLENDCAP_OT_append_workspace(bpy.types.Operator):
    bl_idname = 'blendcap.append_workspace'
    bl_label = 'Open MoCap Workspace'
    bl_description = 'Open the MoCap review and animation workspace'
    def execute(self, context):
        return bpy.ops.blendcap.open_vta_workspace()



class BLENDCAP_OT_cancel(bpy.types.Operator):
    bl_idname = "blendcap.cancel"
    bl_label = "Cancel Running Process"
    bl_description = "Stop the current process"

    def execute(self, context):
        # Set the flag FIRST, then kill the child directly. The worker's
        # reader loop only re-checks the flag between reads, and a capture
        # subprocess can sit silent for minutes loading checkpoints (or
        # wedge entirely) — terminating it closes the pipe, the blocked
        # read returns EOF, and the worker sees cancel_requested and winds
        # down. Flag alone = a Cancel button that can be ignored forever.
        if bvh_state['running']:
            bvh_state['cancel_requested'] = True
            _terminate_state_process(bvh_state)
            self.report({'INFO'}, "Cancelling BVH export... please wait.")
        else:
            self.report({'INFO'}, "Nothing running to cancel.")
        return {'FINISHED'}




class BLENDCAP_OT_generate_bvh(bpy.types.Operator):
    bl_idname = "blendcap.generate_bvh"
    bl_label = "Generate & Import BVH"
    bl_description = ("Generate a BVH (and/or hands/root variants) "
                      "from the cached tracking data of the currently "
                      "selected clip and import it into the scene")

    def invoke(self, context, event):
        """Validate selection + tracking data, then check clip length."""
        props = context.scene.blendcap_props
        if props.export_face or props.use_separate_face:
            self.report({'ERROR'}, "Legacy facial export is not supported. Use a body/hands/root-only Scene.")
            return {'CANCELLED'}
        if blendcap_state['running'] or bvh_state['running']:
            return self.execute(context)

        props = context.scene.blendcap_props

        # ---- 1. Selection check -------------------------------------
        if not (props.export_body or props.export_hands or props.export_root):
            _show_warning_popup([
                "Nothing selected to export.",
                "Enable at least one of: Body, Hands or Root.",
            ])
            return {'CANCELLED'}

        # ---- 2. Resolve source video --------------------------------
        video_path = ""
        if props.video_source == 'FILE':
            video_path, _empty = _file_mode_source(context)
        else:
            seq_ed = context.scene.sequence_editor
            strip = _active_video_strip(seq_ed)
            if strip and strip.type in ('MOVIE', 'IMAGE'):
                if "blendcap_source" in strip:
                    video_path = bpy.path.abspath(str(strip["blendcap_source"]))
                elif strip.type == 'IMAGE':
                    video_path = bpy.path.abspath(strip.directory)
                else:
                    video_path = bpy.path.abspath(strip.filepath)

        if not video_path:
            # Let execute() report the missing-source error in the
            # status bar. Note: the file itself is NOT required to
            # exist — BVH generation reads only the cached NPZ, and
            # the path is only needed to derive the cache folder name.
            return self.execute(context)

        # ---- 3. Locate NPZ caches -----------------------------------
        cache_dir = _cache_dir()
        clip_name = _resolve_clip_name(context) or "sequence"
        vid_name = f"Capture_{clip_name}"
        output_dir = os.path.join(cache_dir, vid_name)
        body_npz = os.path.join(output_dir, "mhr_frames.npz")
        face_npz = os.path.join(output_dir, "face_frames.npz")
        has_body = os.path.exists(body_npz)
        has_face = os.path.exists(face_npz)

        has_separate = False
        if props.use_separate_face and props.separate_face_data != 'NONE':
            sep_npz = os.path.join(cache_dir, props.separate_face_data,
                                   "face_frames.npz")
            has_separate = os.path.exists(sep_npz)

        # ---- 4. Tracking-data check (popup + cancel on miss) --------
        needs_body = (props.export_body or props.export_hands
                      or props.export_root)
        needs_own_face = props.export_face
        needs_sep_face = props.use_separate_face

        if needs_body and not has_body:
            if not has_face and not has_separate:
                msg = ["This clip hasn't been tracked yet.",
                       "Run Capture first to generate motion data."]
            else:
                msg = ["Body tracking data is missing for this clip.",
                       "Run Capture (with body tracking) first."]
            _show_warning_popup(msg)
            return {'CANCELLED'}

        if needs_own_face and not has_face:
            _show_warning_popup([
                "Face tracking data is missing for this clip.",
                "Run Capture (with face tracking) first.",
            ])
            return {'CANCELLED'}

        if needs_sep_face and not has_separate:
            if props.separate_face_data == 'NONE':
                msg = ["No separate face data selected.",
                       "Pick a clip from the Separate Face Data dropdown."]
            else:
                msg = ["Selected separate face data wasn't found.",
                       "Pick a different clip from the dropdown."]
            _show_warning_popup(msg)
            return {'CANCELLED'}

        return self.execute(context)

    def execute(self, context):
        if blendcap_state['running']:
            self.report({'WARNING'}, "A capture is running. Wait for it to finish before generating BVH.")
            return {'CANCELLED'}
        if bvh_state['running']:
            self.report({'WARNING'}, "A BVH export is already running.")
            return {'CANCELLED'}

        props = context.scene.blendcap_props

        # Resolve video path (simplified — BVH only needs the cache folder name)
        if props.export_face or props.use_separate_face:
            self.report({'ERROR'}, "Legacy facial export is not supported. Use a body/hands/root-only Scene.")
            return {'CANCELLED'}
        if props.video_source == 'FILE':
            video_path, _empty = _file_mode_source(context)
        else:
            seq_ed = context.scene.sequence_editor
            strip = _active_video_strip(seq_ed)
            if strip and strip.type in ('MOVIE', 'IMAGE'):
                if "blendcap_source" in strip:
                    video_path = bpy.path.abspath(str(strip["blendcap_source"]))
                elif strip.type == 'IMAGE':
                    video_path = bpy.path.abspath(strip.directory)
                else:
                    video_path = bpy.path.abspath(strip.filepath)
            else:
                video_path = ""

        # The video file itself is not required — generation reads only
        # the cached NPZ, and the path's only job is naming the cache
        # folder. Requiring existence would block regenerating from a
        # perfectly good cache after the source clip was moved or
        # deleted (Bake already works that way via the rig's tag).
        if not video_path:
            self.report({'ERROR'}, source_mode_error(context))
            return {'CANCELLED'}

        cache_dir = _cache_dir()
        clip_name = _resolve_clip_name(context) or "sequence"
        vid_name = f"Capture_{clip_name}"
        output_dir = os.path.join(cache_dir, vid_name)
        body_npz = os.path.join(output_dir, "mhr_frames.npz")
        face_npz = os.path.join(output_dir, "face_frames.npz")
        bvh_path = os.path.join(output_dir, "output.bvh")

        has_body = os.path.exists(body_npz)
        has_face = os.path.exists(face_npz)

        separate_face_npz = ""
        if props.use_separate_face and props.separate_face_data != 'NONE':
            separate_face_npz = os.path.join(cache_dir, props.separate_face_data, "face_frames.npz")
            if os.path.exists(separate_face_npz):
                has_face = True

        if not has_body and not has_face:
            self.report({'ERROR'}, "No tracking data found. Run Capture first.")
            return {'CANCELLED'}

        addon_dir = _addon_dir()
        python_exe = _python_exe()
        script_path = os.path.join(addon_dir, "npz_to_bvh.py")
        if not os.path.exists(script_path):
            self.report({'ERROR'}, f"npz_to_bvh.py not found at {script_path}")
            return {'CANCELLED'}

        mhr_model = os.path.join(addon_dir, "checkpoints",
                                 "sam-3d-body-dinov3", "assets", "mhr_model.pt")

        # Resolve face source (own face or separate-clip face). Mutually
        # exclusive in the UI, but check explicit existence for safety.
        face_path = ""
        if props.export_face and os.path.exists(face_npz):
            face_path = face_npz
        elif (props.use_separate_face and separate_face_npz
              and os.path.exists(separate_face_npz)):
            face_path = separate_face_npz

        # Common args every sub-command shares
        def _common():
            return [python_exe, script_path, "--mhr-model", mhr_model]

        # Face source identity for filename annotation. Bare clip
        # identifier (no Capture_ prefix) — the prefix only exists on
        # cache folder names for VSE-strip clarity.
        #   - own face  → face_source_name == clip_name (folds into codes as "F")
        #   - separate  → face_source_name == bare clip name of separate cache
        #                 (pulled out to "-{source}_F")
        face_source_name = ""
        if face_path == face_npz:
            face_source_name = clip_name
        elif face_path and face_path == separate_face_npz:
            face_source_name = _strip_capture_prefix(props.separate_face_data)

        # Build job list. Each job: (cmd, intermediate_bvh_path, output_stem)
        # where output_stem is the final filename (without .bvh extension)
        # following the convention: {clip}_{BHRF codes}[-{face_clip}_F].
        jobs = []

        if has_body and props.export_body:
            codes = "B"
            if props.export_hands:
                codes += "H"
            if props.export_root:
                codes += "R"
            if face_path and face_source_name == clip_name:
                codes += "F"
                stem = f"{clip_name}_{codes}"
            elif face_path:
                stem = f"{clip_name}_{codes}-{face_source_name}_F"
            else:
                stem = f"{clip_name}_{codes}"

            cmd = _common()
            cmd.extend(["--input", body_npz, "--rest-pose", "tpose"])
            if props.export_root:
                cmd.append("--root-motion")
            if face_path:
                cmd.extend(["--face", face_path])
            if props.smoothing_enabled and props.smooth_body > 0:
                cmd.extend(["--smooth", str(props.smooth_body)])
            if props.smoothing_enabled and props.smooth_root > 0:
                cmd.extend(["--smooth-root", str(props.smooth_root)])
            if props.smoothing_enabled and props.smooth_vertical > 0:
                cmd.extend(["--smooth-vertical", str(props.smooth_vertical)])
            cmd.extend(["--smooth-face", str(props.smooth_face if props.smoothing_enabled else 0)])
            cmd.extend(_bvh_face_denoise_args(props))
            cmd.extend(_bvh_face_amp_args(props))
            cmd.extend(_bvh_depth_denoise_args(props))
            if props.finger_curl_multiplier > 1.0:
                cmd.extend(["--finger-boost",
                            f"{props.finger_curl_multiplier:.1f}"])
            if props.bvh_hand_solver:
                cmd.extend(["--hand-solver", "on"])
            cmd.extend(["--face-boost",
                        f"{_bvh_face_boost_value(props):.1f}"])
            if not props.export_hands:
                cmd.append("--no-hands")
            # Foot locking flags (Pass 1 + Pass 2)
            if not props.foot_lock_enabled:
                cmd.append("--no-foot-lock")
            cmd.extend(["--foot-sensitivity",
                        f"{props.foot_sensitivity:.3f}"])
            cmd.extend(["--max-gap",
                        f"{props.foot_max_gap_sec:.3f}"])
            # Properties are stored in cm for UI clarity; CLI takes
            # meters, so divide by 100 here.
            cmd.extend(["--max-lift",
                        f"{props.foot_max_lift_cm / 100.0:.3f}"])
            if props.foot_recover_glitches:
                cmd.append("--recover-glitches")
            cmd.extend(["--recovery-lift",
                        f"{props.foot_recovery_lift_cm / 100.0:.3f}"])
            if not props.foot_pin_enabled:
                cmd.append("--no-foot-pin")
            cmd.extend(["--foot-pin-strength",
                        f"{props.foot_pin_strength:.2f}"])
            cmd.extend(["--foot-pin-release",
                        f"{props.foot_pin_release_cm / 100.0:.3f}"])
            if props.foot_define_floor:
                cmd.append("--feet-define-floor")
            if props.body_grounding_enabled:
                cmd.append("--body-grounding")
                cmd.extend(["--body-catch-distance",
                            f"{props.body_catch_distance_cm / 100.0:.3f}"])
                cmd.extend(["--body-skin-radius",
                            f"{props.body_skin_radius_cm / 100.0:.4f}"])
            # Camera angle offset is per-rig (Object.blendcap_camera_offset)
            # and applied via Bake Offset to BVH after the rig is imported,
            # not here.
            cmd.extend(["--output", bvh_path])
            jobs.append((cmd, bvh_path, stem))
        else:
            # Standalone hands armature
            if props.export_hands:
                if not has_body:
                    self.report({'ERROR'},
                                "Hands export requires body tracking data. "
                                "Run Capture first.")
                    return {'CANCELLED'}
                hands_bvh = os.path.join(output_dir, "output_hands.bvh")
                cmd = _common()
                cmd.extend(["--input", body_npz, "--export", "hands"])
                if props.smoothing_enabled and props.smooth_body > 0:
                    cmd.extend(["--smooth", str(props.smooth_body)])
                if props.finger_curl_multiplier > 1.0:
                    cmd.extend(["--finger-boost",
                                f"{props.finger_curl_multiplier:.1f}"])
                if props.bvh_hand_solver:
                    cmd.extend(["--hand-solver", "on"])
                cmd.extend(["--output", hands_bvh])
                jobs.append((cmd, hands_bvh, f"{clip_name}_H"))

            # Standalone root armature
            if props.export_root:
                if not has_body:
                    self.report({'ERROR'},
                                "Root export requires body tracking data. "
                                "Run Capture first.")
                    return {'CANCELLED'}
                root_bvh = os.path.join(output_dir, "output_root.bvh")
                cmd = _common()
                cmd.extend(["--input", body_npz, "--export", "root"])
                if props.smoothing_enabled and props.smooth_root > 0:
                    cmd.extend(["--smooth-root", str(props.smooth_root)])
                if props.smoothing_enabled and props.smooth_vertical > 0:
                    cmd.extend(["--smooth-vertical", str(props.smooth_vertical)])
                cmd.extend(["--output", root_bvh])
                jobs.append((cmd, root_bvh, f"{clip_name}_R"))

            # Standalone face armature (own face or separate).
            # Base name follows the face source — separate face from clipX
            # becomes `clipX_F` regardless of which clip is active.
            if face_path:
                face_bvh = os.path.join(output_dir, "output_face.bvh")
                cmd = _common()
                cmd.extend(["--face", face_path, "--export", "face"])
                cmd.extend(["--smooth-face", str(props.smooth_face if props.smoothing_enabled else 0)])
                cmd.extend(_bvh_face_denoise_args(props))
                cmd.extend(_bvh_face_amp_args(props))
                cmd.extend(["--face-boost",
                            f"{_bvh_face_boost_value(props):.1f}"])
                cmd.extend(["--output", face_bvh])
                jobs.append((cmd, face_bvh, f"{face_source_name}_F"))
        # face_source_name here is already the bare clip identifier.

        if not jobs:
            self.report({'ERROR'},
                        "Nothing selected to export. Enable at least one of: "
                        "Body, Hands, Root, Face, or Use Separate Face Data.")
            return {'CANCELLED'}

        # Generate path: no baked-in camera offset on the new rig.
        # Reset before the worker starts so a stale value from a
        # previous Bake doesn't leak onto the freshly-imported rig.
        bvh_state['baked_pitch_deg'] = 0.0
        bvh_state['baked_roll_deg'] = 0.0
        bvh_state['baked_head_pitch_deg'] = 0.0
        bvh_state['baked_head_roll_deg'] = 0.0
        bvh_state['baked_height_m'] = 0.0
        # Resolve the BVH library dir on THIS (main) thread - run_bvh_thread
        # can't safely read add-on prefs from the worker.
        _bvh_lib = _bvh_library_dir()
        # Pre-set state before the thread starts so monitor_bvh_timer can't
        # read a previous run's stale running/return_code (same race as
        # monitor_progress_timer). daemon: see generate_mocap.
        bvh_state['running'] = True
        bvh_state['return_code'] = 0
        bvh_state['cancel_requested'] = False
        thread = threading.Thread(target=run_bvh_thread,
                                  args=(jobs, clip_name, cache_dir, _bvh_lib),
                                  daemon=True)
        thread.start()
        bpy.app.timers.register(monitor_bvh_timer)
        msg = ("Generating BVH... It will auto-import when finished."
               if len(jobs) == 1
               else f"Generating {len(jobs)} BVHs... They will auto-import "
                    f"as separate armatures when finished.")
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def _parse_bake_flags(stem, vid_name):
    """Recover the export flags the rig was originally generated with by
    parsing its BVH stem. Mirrors the naming scheme in
    BLENDCAP_OT_generate_bvh.execute(): {vid}_{codes}[-{face_source}_F]
    where codes is a substring of "BHRF" (B=body, H=hands, R=root,
    F=face from own clip).

    Returns dict {hands, root, face, face_source} on success, None when
    the stem doesn't match the convention (e.g. an externally-renamed
    file). Caller is responsible for falling back to scene props in
    that case.
    """
    # Body with separate face: vid_<codes>-<face_source>_F
    if stem.startswith(vid_name + "_") and "-" in stem[len(vid_name) + 1:]:
        suffix = stem[len(vid_name) + 1:]
        codes, sep = suffix.split("-", 1)
        if sep.endswith("_F"):
            return {
                "hands": "H" in codes,
                "root": "R" in codes,
                "face": True,
                "face_source": sep[:-2],
            }
        return None
    # Body / standalone with own clip: vid_<codes>
    if stem.startswith(vid_name + "_"):
        codes = stem[len(vid_name) + 1:]
        return {
            "hands": "H" in codes,
            "root": "R" in codes,
            "face": "F" in codes,
            "face_source": "",
        }
    # Standalone face from a separate clip: <face_source>_F
    if stem.endswith("_F"):
        return {
            "hands": False,
            "root": False,
            "face": True,
            "face_source": stem[:-2],
        }
    return None


class BLENDCAP_OT_bake_camera_offset(bpy.types.Operator):
    """Bake the active rig's pitch/roll offset into its BVH file.

    Re-runs npz_to_bvh.py for the source clip tagged on the rig
    (`armature["blendcap_source_video"]` set during the original
    generation), passing the current scene props plus pitch/roll and
    the height offset. The
    output overwrites the existing BVH on disk and monitor_bvh_timer
    re-imports it; mocap.py's _import_bvh path deletes the old armature
    first so the <stem> name transfers cleanly. After re-import the
    new armature's blendcap_camera_offset PropertyGroup defaults to 0/0
    — the offset now lives in the file rather than in Blender state,
    so the live-preview overlay starts from a clean baseline.

    Body vs face-only is detected from the rig's bones (presence of
    Hips). Body bakes use the body command path; face-only bakes use
    the standalone-face path (no body NPZ required).

    Why a separate operator instead of folding it into Generate &
    Import BVH: the user has often imported MULTIPLE clips and is
    iterating on just one of them. Bake is scoped to whichever rig is
    active so the other clips in the scene aren't disturbed.
    """
    bl_idname = "blendcap.bake_camera_offset"
    bl_label = "Bake Settings to BVH"
    bl_description = ("Re-generate the BVH for the currently selected "
                      "armature with the current post-processing "
                      "settings. Overwrites the BVH file on disk and "
                      "re-imports.")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        if blendcap_state.get('running') or bvh_state.get('running'):
            return False
        rig = context.active_object
        if rig is None or rig.type != 'ARMATURE':
            return False
        return "blendcap_source_video" in rig

    def execute(self, context):
        rig = context.active_object
        vid_name = str(rig.get("blendcap_source_video", "") or "")
        if not vid_name:
            self.report({'ERROR'},
                        "Active rig has no source-clip tag — generate "
                        "the BVH first via 'Generate & Import BVH'.")
            return {'CANCELLED'}

        # Resolve the output stem from the BVH path the rig was
        # imported from. Survives renames (rig.name might no longer
        # match the file). Falls back to rig.name if the custom prop
        # wasn't recorded (e.g. legacy rigs from before the prop
        # existed).
        bvh_path_prop = str(rig.get("blendcap_bvh_path", "") or "")
        if bvh_path_prop:
            stem = os.path.splitext(os.path.basename(bvh_path_prop))[0]
        else:
            stem = rig.name

        offset = rig.blendcap_camera_offset
        slider_pitch = float(offset.pitch_deg)
        slider_roll = float(offset.roll_deg)
        slider_head_pitch = float(offset.head_pitch_deg)
        slider_head_roll = float(offset.head_roll_deg)
        slider_height = float(offset.height_offset_m)
        # Additive bakes: each Bake adds the current slider values on
        # TOP of whatever was previously baked into this rig's BVH.
        # Track the cumulative baked value via custom props on the rig
        # so a sequence of bakes (1° then later 3° more) ends up at
        # the sum (4°) in the BVH file. The new rig that comes back
        # from re-import inherits these totals via bvh_state ->
        # mocap.monitor_bvh_timer.
        prev_baked_pitch = float(rig.get("_blendcap_baked_pitch_deg", 0.0))
        prev_baked_roll = float(rig.get("_blendcap_baked_roll_deg", 0.0))
        prev_baked_head_pitch = float(
            rig.get("_blendcap_baked_head_pitch_deg", 0.0))
        prev_baked_head_roll = float(
            rig.get("_blendcap_baked_head_roll_deg", 0.0))
        prev_baked_height = float(
            rig.get("_blendcap_baked_height_m", 0.0))
        pitch = prev_baked_pitch + slider_pitch
        roll = prev_baked_roll + slider_roll
        head_pitch = prev_baked_head_pitch + slider_head_pitch
        head_roll = prev_baked_head_roll + slider_head_roll
        height = prev_baked_height + slider_height

        # vid_name (loaded from rig prop) is the BARE clip identifier
        # so it can be embedded in BVH stems. The on-disk cache folder
        # carries the Capture_ prefix; reconstruct it here.
        cache_dir = _cache_dir()
        output_dir = os.path.join(cache_dir, f"Capture_{vid_name}")
        body_npz = os.path.join(output_dir, "mhr_frames.npz")
        face_npz = os.path.join(output_dir, "face_frames.npz")

        # Detect export type from the rig structure. Bake should
        # preserve whatever the rig was originally generated with —
        # the user's body/hands/face/root checkboxes apply to
        # Generate, not to re-baking an existing rig.
        pose_bones = rig.pose.bones if rig.pose else None
        has_hips = bool(pose_bones and "Hips" in pose_bones)
        has_head = bool(pose_bones and "Head" in pose_bones)
        has_root = bool(pose_bones and "Root" in pose_bones)
        has_left_hand = bool(pose_bones and "LeftHand" in pose_bones)

        # Recover hands/root/face/face_source from the stem so the
        # bake command exactly matches the original anatomy. Returns
        # None if the stem doesn't follow the naming convention.
        flags = _parse_bake_flags(stem, vid_name)

        addon_dir = _addon_dir()
        python_exe = _python_exe()
        script_path = os.path.join(addon_dir, "npz_to_bvh.py")
        mhr_model = os.path.join(addon_dir, "checkpoints",
                                 "sam-3d-body-dinov3", "assets",
                                 "mhr_model.pt")

        props = context.scene.blendcap_props

        def _resolve_face_path():
            """Locate the face NPZ to use based on parsed flags. Same
            cache folder as the original generate used: own clip when
            face_source is empty, otherwise the separate face clip's
            cache folder."""
            if not flags or not flags["face"]:
                return ""
            if flags["face_source"]:
                sf = os.path.join(cache_dir, f"Capture_{flags['face_source']}",
                                  "face_frames.npz")
                if os.path.exists(sf):
                    return sf
            if os.path.exists(face_npz):
                return face_npz
            return ""

        if has_hips:
            # ---- Body bake (B + optional H / R / F) ----
            if not os.path.exists(body_npz):
                self.report({'ERROR'},
                            f"Body NPZ not found for clip '{vid_name}'. "
                            f"Has the cache been cleared?")
                return {'CANCELLED'}

            # Use parsed flags; fall back to bone-presence heuristics
            # if the stem couldn't be parsed (renamed rig).
            if flags is not None:
                use_hands = flags["hands"]
                use_root = flags["root"]
                use_face = flags["face"]
            else:
                # Stem unparseable — preserve what the rig actually
                # contains rather than touching the scene checkboxes.
                use_hands = has_left_hand
                use_root = has_root
                use_face = bool(pose_bones and any(
                    pb.name not in ("Root", "Hips", "Spine", "Chest",
                                    "Neck", "Head")
                    and pb.parent and pb.parent.name == "Head"
                    for pb in pose_bones))
            face_path = _resolve_face_path() if use_face else ""

            bvh_path = os.path.join(output_dir, "output.bvh")
            cmd = [python_exe, script_path, "--mhr-model", mhr_model,
                   "--input", body_npz, "--rest-pose", "tpose"]
            if use_root:
                cmd.append("--root-motion")
            if face_path:
                cmd.extend(["--face", face_path])
            if props.smoothing_enabled and props.smooth_body > 0:
                cmd.extend(["--smooth", str(props.smooth_body)])
            if props.smoothing_enabled and props.smooth_root > 0:
                cmd.extend(["--smooth-root", str(props.smooth_root)])
            if props.smoothing_enabled and props.smooth_vertical > 0:
                cmd.extend(["--smooth-vertical", str(props.smooth_vertical)])
            cmd.extend(["--smooth-face", str(props.smooth_face if props.smoothing_enabled else 0)])
            cmd.extend(_bvh_face_denoise_args(props))
            cmd.extend(_bvh_face_amp_args(props))
            cmd.extend(_bvh_depth_denoise_args(props))
            if props.finger_curl_multiplier > 1.0:
                cmd.extend(["--finger-boost",
                            f"{props.finger_curl_multiplier:.1f}"])
            if props.bvh_hand_solver:
                cmd.extend(["--hand-solver", "on"])
            cmd.extend(["--face-boost",
                        f"{_bvh_face_boost_value(props):.1f}"])
            if not use_hands:
                cmd.append("--no-hands")
            if not props.foot_lock_enabled:
                cmd.append("--no-foot-lock")
            cmd.extend(["--foot-sensitivity",
                        f"{props.foot_sensitivity:.3f}"])
            cmd.extend(["--max-gap",
                        f"{props.foot_max_gap_sec:.3f}"])
            cmd.extend(["--max-lift",
                        f"{props.foot_max_lift_cm / 100.0:.3f}"])
            if props.foot_recover_glitches:
                cmd.append("--recover-glitches")
            cmd.extend(["--recovery-lift",
                        f"{props.foot_recovery_lift_cm / 100.0:.3f}"])
            if not props.foot_pin_enabled:
                cmd.append("--no-foot-pin")
            cmd.extend(["--foot-pin-strength",
                        f"{props.foot_pin_strength:.2f}"])
            cmd.extend(["--foot-pin-release",
                        f"{props.foot_pin_release_cm / 100.0:.3f}"])
            if props.foot_define_floor:
                cmd.append("--feet-define-floor")
            if props.body_grounding_enabled:
                cmd.append("--body-grounding")
                cmd.extend(["--body-catch-distance",
                            f"{props.body_catch_distance_cm / 100.0:.3f}"])
                cmd.extend(["--body-skin-radius",
                            f"{props.body_skin_radius_cm / 100.0:.4f}"])
            if abs(pitch) > 1e-6:
                cmd.extend(["--camera-pitch", f"{pitch:.3f}"])
            if abs(roll) > 1e-6:
                cmd.extend(["--camera-roll", f"{roll:.3f}"])
            if abs(head_pitch) > 1e-6:
                cmd.extend(["--head-pitch", f"{head_pitch:.3f}"])
            if abs(head_roll) > 1e-6:
                cmd.extend(["--head-roll", f"{head_roll:.3f}"])
            if abs(height) > 1e-6:
                cmd.extend(["--height-offset", f"{height:.4f}"])
            cmd.extend(["--output", bvh_path])

        elif has_head and not has_root:
            # ---- Face-only bake ----
            face_path = _resolve_face_path()
            if not face_path and os.path.exists(face_npz):
                face_path = face_npz
            if not face_path:
                self.report({'ERROR'},
                            f"Face NPZ not found for clip '{vid_name}'. "
                            f"Run face capture first.")
                return {'CANCELLED'}

            bvh_path = os.path.join(output_dir, "output_face.bvh")
            cmd = [python_exe, script_path, "--mhr-model", mhr_model,
                   "--face", face_path, "--export", "face"]
            cmd.extend(["--smooth-face", str(props.smooth_face if props.smoothing_enabled else 0)])
            cmd.extend(_bvh_face_denoise_args(props))
            cmd.extend(_bvh_face_amp_args(props))
            cmd.extend(["--face-boost",
                        f"{_bvh_face_boost_value(props):.1f}"])
            # Camera pitch/roll and height offset silently dropped —
            # no Root to rotate, no Hips to shift.
            cmd.extend(["--output", bvh_path])

        elif has_left_hand and not has_hips:
            # ---- Hands-only bake ----
            if not os.path.exists(body_npz):
                self.report({'ERROR'},
                            f"Body NPZ not found for clip '{vid_name}'. "
                            f"Has the cache been cleared?")
                return {'CANCELLED'}
            bvh_path = os.path.join(output_dir, "output_hands.bvh")
            cmd = [python_exe, script_path, "--mhr-model", mhr_model,
                   "--input", body_npz, "--export", "hands"]
            if props.smoothing_enabled and props.smooth_body > 0:
                cmd.extend(["--smooth", str(props.smooth_body)])
            if props.finger_curl_multiplier > 1.0:
                cmd.extend(["--finger-boost",
                            f"{props.finger_curl_multiplier:.1f}"])
            if props.bvh_hand_solver:
                cmd.extend(["--hand-solver", "on"])
            cmd.extend(["--output", bvh_path])

        elif has_root and not has_hips and not has_head:
            # ---- Root-only bake ----
            if not os.path.exists(body_npz):
                self.report({'ERROR'},
                            f"Body NPZ not found for clip '{vid_name}'. "
                            f"Has the cache been cleared?")
                return {'CANCELLED'}
            bvh_path = os.path.join(output_dir, "output_root.bvh")
            cmd = [python_exe, script_path, "--mhr-model", mhr_model,
                   "--input", body_npz, "--export", "root"]
            if props.smoothing_enabled and props.smooth_root > 0:
                cmd.extend(["--smooth-root", str(props.smooth_root)])
            if props.smoothing_enabled and props.smooth_vertical > 0:
                cmd.extend(["--smooth-vertical", str(props.smooth_vertical)])
            cmd.extend(["--output", bvh_path])

        else:
            self.report({'ERROR'},
                        "Active rig isn't a recognized BlendCap export "
                        "type (no Hips, Head, hand, or Root bones).")
            return {'CANCELLED'}

        jobs = [(cmd, bvh_path, stem)]
        # Stash the cumulative baked totals BEFORE starting the
        # worker thread. The worker's bvh_state.update inside
        # run_bvh_thread does NOT touch these keys, so setting them
        # here ensures monitor_bvh_timer can read the right values
        # after the import completes. Without this pre-launch write,
        # a stale value from a previous bake or an uninitialized
        # state could land on the new rig.
        bvh_state['baked_pitch_deg'] = pitch
        bvh_state['baked_roll_deg'] = roll
        bvh_state['baked_head_pitch_deg'] = head_pitch
        bvh_state['baked_head_roll_deg'] = head_roll
        bvh_state['baked_height_m'] = height
        # Resolve the BVH library dir on THIS (main) thread - run_bvh_thread
        # can't safely read add-on prefs from the worker.
        _bvh_lib = _bvh_library_dir()
        # Pre-set state before the thread starts so monitor_bvh_timer can't
        # read a previous run's stale running/return_code (same race as
        # monitor_progress_timer). daemon: see generate_mocap.
        bvh_state['running'] = True
        bvh_state['return_code'] = 0
        bvh_state['cancel_requested'] = False
        thread = threading.Thread(target=run_bvh_thread,
                                  args=(jobs, vid_name, cache_dir, _bvh_lib),
                                  daemon=True)
        thread.start()
        bpy.app.timers.register(monitor_bvh_timer)
        self.report({'INFO'},
                    f"Baking offsets into '{stem}.bvh' "
                    f"(body pitch {pitch:+.2f}deg roll {roll:+.2f}deg; "
                    f"head pitch {head_pitch:+.2f}deg roll "
                    f"{head_roll:+.2f}deg; height {height:+.3f}m)...")
        return {'FINISHED'}
