"""VTA public Blender adapter entry point; upstream attribution is in Credits/NOTICE.

Product naming is provisional. Stable extension/package identifiers are retained.
Runtime provisioning is external to Preferences and owned by VTA setup/repair.
"""
import bpy
from .blendcap_vta_product import PRODUCT_NAME
from .blendcap import registration as _registration


def _redraw_viewport_panels(self, context):
    """Refresh the sidebar / Output panels when a visibility pref toggles, so
    hiding the Setup section or Clear All button takes effect immediately
    instead of waiting for the next viewport interaction."""
    from .blendcap.helpers import _tag_redraw
    _tag_redraw('VIEW_3D', 'PROPERTIES')


def _update_default_video_source(self, context):
    """When Default Source is switched to Video Editor Strip while
    Sequencer Render Warning is 'Ask each time', pop a dialog converting
    the warning pref to one of the two options that work for new files
    (ask-each-time never fires on File > New - there's no interactive
    switch to hook). OK -> Turn off automatically, Cancel -> Leave it
    alone. While the default stays VSE, 'Ask each time' is greyed out in
    the prefs UI (see the draw code).

    Deferred through a one-shot timer, which does double duty: it keeps
    the dialog from firing mid-click on the dropdown, and it re-checks
    both prefs at fire time - so if this update fired during userpref
    loading (properties are assigned in definition order, and the warning
    pref loads AFTER this one, briefly reading as its NOTIFY default) the
    re-check sees the real loaded value and stays quiet.
    """
    if self.default_video_source != 'VSE':
        return
    if self.sequencer_export_behavior != 'NOTIFY':
        return
    from .blendcap.helpers import blendcap_state
    if blendcap_state.get('vse_default_prompt_pending'):
        return
    blendcap_state['vse_default_prompt_pending'] = True
    # The prefs live in their own OS window, but a timer's default context
    # targets the main window - the dialog would open BEHIND Preferences.
    # Capture the window the click happened in and re-target the operator.
    src_window = getattr(context, "window", None)

    def _deferred_prompt():
        blendcap_state['vse_default_prompt_pending'] = False
        from .blendcap.helpers import addon_prefs
        prefs = addon_prefs()
        if (prefs is None
                or prefs.default_video_source != 'VSE'
                or prefs.sequencer_export_behavior != 'NOTIFY'):
            return None
        win = src_window
        if win is not None and win not in list(bpy.context.window_manager.windows):
            win = None  # window was closed between click and timer
        try:
            if win is not None:
                with bpy.context.temp_override(window=win):
                    bpy.ops.blendcap.vse_default_prompt('INVOKE_DEFAULT')
            else:
                bpy.ops.blendcap.vse_default_prompt('INVOKE_DEFAULT')
        except RuntimeError:
            # Context can be invalid (e.g. a script-driven change); the
            # runtime fallback treats a lingering NOTIFY as Leave it
            # alone, so failing quiet is safe.
            pass
        return None  # one-shot

    bpy.app.timers.register(_deferred_prompt, first_interval=0.05)




class BLENDCAP_AddonPreferences(bpy.types.AddonPreferences):
    # Must equal the add-on's package name. As an installed extension that is
    # bl_ext.<repo>.blendcap, and __package__ resolves to exactly that.
    bl_idname = __package__

    # ------------------------------------------------------------------
    # Persistent user preferences (saved in userpref.blend, global to the
    # install — NOT per-.blend). Where each one is consumed:
    #   cache_*               -> helpers._cache_dir (cache folder resolution)
    #   bvh_library_*         -> helpers._bvh_library_dir (library folder)
    #   sequencer_export_behavior -> properties.update_video_source
    #   hide_setup_section    -> panels.draw_blendcap_ui (Setup block)
    #   hide_clear_all        -> panels.draw_blendcap_ui (Clear All button)
    #   hide_donate_buttons   -> panels.draw_blendcap_ui + this draw()
    #                            (honor-system supporter toggle; only
    #                            relevant on source builds where
    #                            SHOW_DONATE_BUTTON is True)
    # ------------------------------------------------------------------

    # Honor-system supporter toggle. Only ever drawn on source builds
    # (paid builds hide the donate buttons entirely via is_paid_build).
    # Deliberately unverified - the button is an ask, not a gate, and
    # the people who tick this without donating were never going to.
    hide_donate_buttons: bpy.props.BoolProperty(  # type: ignore
        name="I've donated - hide the support buttons",
        default=False,
        update=_redraw_viewport_panels,
        description=(
            "Hide BlendCap's Donate / Support buttons here and in the "
            "3D-viewport panel. Works on the honor system - if you've "
            "chipped in (thank you!) or just want the buttons gone, "
            "tick this. Untick any time to bring them back"))

    # --- File & cache locations ---
    cache_use_custom: bpy.props.BoolProperty(  # type: ignore
        name="Set default cache folder",
        default=False,
        description=(
            "Save tracking data (captures, proxies, NPZ files) to a folder "
            "you choose. By default BlendCap saves next to your .blend file, "
            "or to a temporary folder (cleared when Blender closes) if the "
            "project hasn't been saved yet. Turn this on to use your chosen "
            "folder instead of that temporary one, so the data isn't cleared "
            "when Blender closes. Saved projects still keep their data next "
            "to the .blend file unless you also turn on 'Always use this "
            "folder' below"))
    cache_custom_path: bpy.props.StringProperty(  # type: ignore
        name="Cache Folder",
        subtype='DIR_PATH', default="",
        description="Folder used for tracking data when the option above is on")
    cache_always_use_custom: bpy.props.BoolProperty(  # type: ignore
        name="Always use this folder",
        default=False,
        description=(
            "Use the custom cache folder even after the project has been "
            "saved. When off, a saved project keeps its tracking data next "
            "to the .blend file and the custom folder is only used before "
            "the project has been saved"))

    bvh_library_use_custom: bpy.props.BoolProperty(  # type: ignore
        name="Use a custom BVH library folder",
        default=False,
        description=(
            "Read and save the BVH library from a folder you choose instead "
            "of inside the cache folder. Point several projects at one shared "
            "folder to reuse takes across them"))
    bvh_library_path: bpy.props.StringProperty(  # type: ignore
        name="BVH Library Folder",
        subtype='DIR_PATH', default="",
        description="Folder used for the BVH library when the option above is on")

    # Separate private component root. This is persisted in Blender's normal
    # user preferences and is used only by the public VTA runtime-discovery
    # boundary; no private code is bundled with this extension.
    vta_core_location: bpy.props.StringProperty(  # type: ignore
        name="StudioFree MoCap Core Location",
        subtype='DIR_PATH', default="",
        description="Folder containing the separately installed StudioFree MoCap Core")
    vta_analysis_python: bpy.props.StringProperty(  # type: ignore
        name="MoCap Analysis Python", subtype='FILE_PATH', default="",
        description="Fallback Python executable when the evaluated CUDA SAM3 environment is not discoverable")
    vta_kinesia_root: bpy.props.StringProperty(  # type: ignore
        name="MoCap SAM3 Source", subtype='DIR_PATH', default="",
        description="Existing evaluated Kinesia source checkout; no files are installed")
    vta_sam3_checkpoint: bpy.props.StringProperty(  # type: ignore
        name="MoCap SAM3 Checkpoint", subtype='FILE_PATH', default="",
        description="Existing offline SAM3 checkpoint for candidate analysis")

    # --- Capture & tracking ---
    default_video_source: bpy.props.EnumProperty(  # type: ignore
        name="Default Source",
        items=[
            ('FILE', "File Path",
             "New scenes start with the capture source set to a video file "
             "picked from disk"),
            ('VSE', "Video Editor Strip",
             "New scenes start with the capture source set to the active clip "
             "in Blender's Video Sequencer"),
        ],
        default='FILE',
        update=_update_default_video_source,
        description=(
            "Which BlendCap video capture source is selected by default when "
            "you open Blender. Set this to \"Video Editor Strip\" if you "
            "usually prefer to capture videos from the timeline"))
    sequencer_export_behavior: bpy.props.EnumProperty(  # type: ignore
        name="Sequencer Render Warning",
        items=[
            ('NOTIFY', "Ask each time",
             "Warn and offer to turn off Sequencer export when you switch the "
             "source to a Video Editor strip. Not available while Default "
             "Source is Video Editor Strip - new files start on a strip "
             "without a switch to ask on"),
            ('AUTO_DISABLE', "Turn off automatically",
             "Silently turn off Sequencer export when you switch the source to "
             "a Video Editor strip, or when a new file starts on one via "
             "Default Source. Saved projects keep their own setting"),
            ('LEAVE', "Leave it alone",
             "Don't warn and don't change the Sequencer export setting"),
        ],
        default='NOTIFY',
        description=(
            "What to do with Blender's \"Sequencer\" render option when you "
            "switch the capture source to a Video Editor strip. Leaving it on "
            "can make renders export the video strip instead of your scene"))

    # --- Interface ---
    hide_setup_section: bpy.props.BoolProperty(  # type: ignore
        name="Hide the Setup section",
        default=False,
        update=_redraw_viewport_panels,
        description=(
            "Hide workspace and Preferences shortcuts in the MoCap sidebar "
            "panel. Turn this on once your workspace is set up and you no "
            "longer need those buttons"))
    hide_clear_all: bpy.props.BoolProperty(  # type: ignore
        name="Hide the \"Clear All\" cache button",
        default=False,
        update=_redraw_viewport_panels,
        description=(
            "Hide the \"Clear All\" button so the whole capture cache can't "
            "be deleted by accident. \"Clear Cache\" (current clip only) "
            "stays available. Useful when several projects share one cache "
            "folder"))

    # ------------------------------------------------------------------
    # Draw - split into per-section helpers so blocks are easy to reorder.
    # ------------------------------------------------------------------
    def draw(self, context):
        layout = self.layout
        layout.label(text=PRODUCT_NAME+' Preferences', icon='PREFERENCES')
        layout.prop(self, 'hide_setup_section', text='Hide Workspace Shortcuts')
        layout.label(text='Source, review, generation and retarget settings belong to the project.')
        box = layout.box()
        box.label(text='Runtime and diagnostics', icon='INFO')
        box.label(text='MoCap setup/repair manages runtime and model locations.')
        box.label(text='Use Advanced → Open Logs Folder for project diagnostics.')
        box.label(text='Developer source configuration is separate from project data.')



classes = (
    BLENDCAP_AddonPreferences,
)


def register():
    # Feature classes/props first so the operators the preferences UI
    # references already exist, then the preferences panel itself.
    _registration.register()
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    _registration.unregister()
