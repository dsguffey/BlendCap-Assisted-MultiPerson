"""Existing-data proxy reversion and BVH export; no capture/preview runner.

Proxy reversion preserves access to old saved source strips without generating
new proxies. BVH utilities still share the existing helpers/state dictionaries.
"""
import bpy
import os
import math
import shutil
import subprocess
import threading
import time

from .helpers import (
    blendcap_state, bvh_state,
    _addon_dir, _python_exe, _tag_redraw, addon_prefs,
    _host_spawn_prefix, _subprocess_env,
    _seq_accessors, _strip_visible_start, _strip_visible_end,
    _import_bvh, _write_cache_params,
    _ansi_re, _progress_re, _warning_re, _frame_re, _tqdm_re,
    _capture_noise_re,
    format_workspace_status,
)


# ============================================================
# BACKGROUND MOCAP RUNNER & VSE LOADER
# ============================================================




def _revert_proxy_strip(strip, seq_coll, seq_all):
    """Revert a proxy strip back to its original video/image sequence.
    Returns new strip on success, None on failure."""
    if "blendcap_source" not in strip:
        return None

    original_path = bpy.path.abspath(str(strip["blendcap_source"]))
    original_type = str(strip.get("blendcap_original_type", "MOVIE"))

    # Clip identity carried by the proxy — re-stamped onto the restored
    # strip at the end so a later re-capture maps back to this clip's
    # cache folder (and skips the capture collision guard). blendcap_
    # source is deliberately NOT restored: the strip is the original
    # video again, so capture must read trim from the strip itself,
    # not from stored proxy values.
    source_strip_id = str(strip.get("blendcap_source_strip", "") or "")

    # Use proxy's current visible position (user may have moved it)
    proxy_position = _strip_visible_start(strip)
    insert_channel = strip.channel

    # Use stored original channel (proxy may be on a different channel)
    orig_channel = int(strip.get("blendcap_original_channel", strip.channel))

    if original_type == 'IMAGE':
        # --- IMAGE STRIP REVERT ---
        insert_channel = orig_channel
        orig_dir = str(strip.get("blendcap_original_directory", original_path))
        elem_str = str(strip.get("blendcap_original_elements", ""))
        elements = elem_str.split("|") if elem_str else []
        orig_frame_start = int(strip.get("blendcap_original_image_frame_start", proxy_position))
        orig_offset_start = int(strip.get("blendcap_original_image_offset_start", 0))
        orig_offset_end = int(strip.get("blendcap_original_image_offset_end", 0))

        if not orig_dir or not os.path.isdir(orig_dir):
            return None
        if not elements:
            # Fallback: list directory for common image extensions
            img_exts = {'.png', '.jpg', '.jpeg', '.exr', '.tiff', '.tif', '.bmp', '.dpx', '.hdr'}
            elements = sorted(f for f in os.listdir(orig_dir)
                              if os.path.splitext(f)[1].lower() in img_exts)
        if not elements:
            return None

        # Compute frame_start so visible start lands at proxy_position
        insert_frame = proxy_position - orig_offset_start

        # Prefer the source-side strip name we stamped at capture time
        # so revert restores the user's original strip identity (e.g.
        # "myclip_001" from a split). Falls back to the directory name
        # for proxies created before that prop existed.
        strip_name = str(strip.get("blendcap_source_strip", "") or "") \
            or os.path.basename(orig_dir.rstrip(os.sep))
        seq_coll.remove(strip)

        first_file = os.path.join(orig_dir, elements[0])
        new_strip = seq_coll.new_image(strip_name, first_file, insert_channel, insert_frame)
        new_strip.directory = orig_dir
        for fname in elements[1:]:
            new_strip.elements.append(fname)

        # Restore trim (deprecated on ImageStrip, suppressed at module level)
        try:
            if orig_offset_start > 0:
                new_strip.frame_offset_start = orig_offset_start
            if orig_offset_end > 0:
                new_strip.frame_offset_end = orig_offset_end
        except Exception:
            pass

        # Force channel after trim (Blender may auto-place on higher channel)
        if new_strip.channel != insert_channel:
            new_strip.channel = insert_channel

    else:
        # --- MOVIE STRIP REVERT ---
        if not os.path.exists(original_path):
            return None

        orig_vis_start = int(strip.get("blendcap_original_vis_start", proxy_position))
        orig_vis_end = int(strip.get("blendcap_original_vis_end", 0))
        orig_offset_start = int(strip.get("blendcap_original_offset_start", 0))
        orig_frame_duration = int(strip.get("blendcap_original_frame_duration", 0))
        insert_channel = orig_channel

        # Prefer the stamped source-strip identifier (preserves the
        # user's original strip name across capture/revert cycles).
        vid_name = str(strip.get("blendcap_source_strip", "") or "") \
            or os.path.splitext(os.path.basename(original_path))[0]
        seq_coll.remove(strip)

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            # Probe frame_duration on a temp strip (high channel to avoid conflicts)
            probe = seq_coll.new_movie("_blendcap_probe", original_path, 128, 1)
            new_frame_dur = int(probe.frame_duration)
            seq_coll.remove(probe)

            # Scale offset to match the new frame space
            if orig_frame_duration > 0 and new_frame_dur != orig_frame_duration:
                ratio = new_frame_dur / orig_frame_duration
                scaled_offset = int(round(orig_offset_start * ratio))
            else:
                scaled_offset = orig_offset_start

            # Create final strip at correct position and channel
            new_frame_start = orig_vis_start - scaled_offset
            new_strip = seq_coll.new_movie(vid_name, original_path, insert_channel, new_frame_start)

            if orig_vis_end > orig_vis_start:
                new_strip.frame_final_end = orig_vis_end
                new_strip.frame_final_start = orig_vis_start

            # Blender may auto-place on a higher channel if the full
            # (untrimmed) range overlapped with adjacent strips. After
            # trimming, force the strip to the correct channel.
            if new_strip.channel != insert_channel:
                new_strip.channel = insert_channel

    if source_strip_id:
        new_strip["blendcap_source_strip"] = source_strip_id

    # Scale to fit project frame
    scene = bpy.context.scene
    scene_w = scene.render.resolution_x
    scene_h = scene.render.resolution_y
    if hasattr(new_strip, 'transform') and new_strip.transform is not None:
        strip_w = strip_h = 0
        if hasattr(new_strip, 'orig_width'):
            strip_w, strip_h = new_strip.orig_width, new_strip.orig_height
        if (strip_w <= 0 or strip_h <= 0) and hasattr(new_strip, 'elements') and len(new_strip.elements) > 0:
            strip_w, strip_h = new_strip.elements[0].orig_width, new_strip.elements[0].orig_height
        if strip_w > 0 and strip_h > 0 and scene_w > 0 and scene_h > 0:
            fit = min(scene_w / strip_w, scene_h / strip_h)
            new_strip.transform.scale_x = fit
            new_strip.transform.scale_y = fit

    return new_strip








# ============================================================
# BVH EXPORT BACKGROUND LOGIC
# ============================================================
def monitor_bvh_timer():
    if bvh_state['running']:
        # Mirror monitor_progress_timer: paint the workspace status bar
        # and redraw the panel so the in-panel progress bar ticks.
        pct = bvh_state.get('progress', 0.0)
        status = bvh_state.get('status_text', '')
        txt = format_workspace_status("BlendCap BVH", pct, status)
        try:
            for win in bpy.context.window_manager.windows:
                if win.workspace:
                    win.workspace.status_text_set(txt)
        except Exception:
            pass
        _tag_redraw('VIEW_3D', 'PROPERTIES')
        return 0.25

    # Finished — clear the workspace status bar.
    try:
        for win in bpy.context.window_manager.windows:
            if win.workspace:
                win.workspace.status_text_set(None)
    except Exception:
        pass

    if bvh_state['return_code'] == 0:
        # bvh_paths is a list of (final_path, stem) for all completed jobs.
        # `stem` is the final filename without extension and already encodes
        # the vid_name + component codes (e.g., "clip1_BHRF", "clip1_H",
        # "clip2_F", "clip1_BHR-clip2_F").
        paths = bvh_state.get('bvh_paths', [])
        if not paths and bvh_state.get('bvh_path'):
            stem = os.path.splitext(os.path.basename(
                bvh_state['bvh_path']))[0]
            paths = [(bvh_state['bvh_path'], stem)]
        for final_path, stem in paths:
            if not os.path.exists(final_path):
                print(f"[BlendCap] BVH file not found at {final_path}")
                continue
            try:
                _import_bvh(final_path, stem)
                # Tag the freshly-imported armature with provenance so
                # the per-rig camera-offset Bake button can re-run
                # NPZ→BVH for THIS specific clip without the user
                # having to re-pick anything in the Capture section.
                # `bvh_state['vid_name']` is the BARE clip identifier
                # (no Capture_ prefix). Stamped on the rig so the
                # camera-offset Bake operator can rebuild the cache
                # folder path (Capture_ + vid_name) and parse the BVH
                # stem (which also uses the bare name). Set after
                # import so bpy.context.active_object is the new rig.
                imported = bpy.context.active_object
                if imported is not None and imported.type == 'ARMATURE':
                    imported["blendcap_source_video"] = bvh_state.get(
                        'vid_name', '')
                    imported["blendcap_bvh_path"] = final_path
                    # Cumulative camera offset baked into THIS BVH on
                    # disk. Read by the Bake Camera Offset operator
                    # so a follow-up bake adds on top instead of
                    # treating the previous bake as 0. Set by the
                    # Generate / Bake operators BEFORE thread.start;
                    # default 0 for plain Generate, total for Bake.
                    imported["_blendcap_baked_pitch_deg"] = float(
                        bvh_state.get('baked_pitch_deg', 0.0))
                    imported["_blendcap_baked_roll_deg"] = float(
                        bvh_state.get('baked_roll_deg', 0.0))
                    imported["_blendcap_baked_head_pitch_deg"] = float(
                        bvh_state.get('baked_head_pitch_deg', 0.0))
                    imported["_blendcap_baked_head_roll_deg"] = float(
                        bvh_state.get('baked_head_roll_deg', 0.0))
                    imported["_blendcap_baked_height_m"] = float(
                        bvh_state.get('baked_height_m', 0.0))
                print(f"[BlendCap] BVH saved to library and imported: "
                      f"{final_path}")
            except Exception as e:
                print(f"[BlendCap] Failed to import {final_path}: {e}")
    elif bvh_state['return_code'] == -2:
        print("[BlendCap] BVH export cancelled.")
    else:
        print(f"[BlendCap] BVH Export failed: {bvh_state['error_msg']}")
        # Same failure dialog as the capture path: show the last lines
        # the export subprocess printed instead of console-only output.
        lines = [f"BVH export failed (exit code {bvh_state['return_code']})."]
        if bvh_state.get('error_msg'):
            lines.append(str(bvh_state['error_msg'])[:90])
        tail = [t for t in bvh_state.get('error_tail', []) if t][-5:]
        if tail:
            lines.append("Last output:")
            lines.extend(f"  {t[:90]}" for t in tail)
        blendcap_state['warnings'] = lines

        def _deferred_bvh_failure_popup():
            try:
                bpy.ops.blendcap.show_warnings('INVOKE_DEFAULT')
            except Exception as e:
                print(f"[BlendCap] Could not show failure dialog: {e}")
            return None
        bpy.app.timers.register(_deferred_bvh_failure_popup,
                                first_interval=0.3)
    return None


def run_bvh_thread(jobs, vid_name, cache_dir, bvh_library_dir=None):
    """Run a list of BVH-generation subprocesses sequentially.

    jobs: list of (cmd, intermediate_bvh_path, output_stem). Each command
    writes to its intermediate path; on success the file is moved to
    cache/bvh_exports/{output_stem}.bvh. The stem is the full final
    filename (without extension) using the naming convention described
    in BLENDCAP_OT_generate_bvh.execute().
    """
    n_jobs = len(jobs)
    bvh_state.update({
        'running': True, 'return_code': 0,
        'cancel_requested': False, 'active_process': None,
        'error_tail': [],
        'bvh_paths': [],
        # Clear the legacy single-path field at the start of every
        # run. Previously it was only assigned on a successful move-
        # to-library, so a Generate that crashed mid-run could leave
        # a stale path from a previous Generate, and monitor_bvh_
        # timer's fallback (`if not paths and bvh_state.get(
        # 'bvh_path')`) would re-import the OLD library file
        # silently — the "regenerate sometimes pulls from library"
        # bug. Setting it to "" up front means a failed run has no
        # path to fall back to and the monitor correctly does
        # nothing.
        'bvh_path': "",
        # NOTE: baked_pitch_deg / baked_roll_deg / baked_height_m are
        # deliberately NOT included here. They're set by the calling
        # operator BEFORE thread.start() (Generate sets zeros, Bake
        # sets cumulative totals) so the worker can't race-overwrite
        # them. Read by monitor_bvh_timer to set the _blendcap_baked_*
        # custom props on the imported armature so the next Bake can
        # add ON TOP.
        'progress': 0.0, 'status_text': "Starting...",
        'current_job': 0, 'total_jobs': n_jobs,
    })
    bvh_state['vid_name'] = vid_name
    bvh_exports_dir = bvh_library_dir or os.path.join(cache_dir, "bvh_exports")
    os.makedirs(bvh_exports_dir, exist_ok=True)

    def _set_progress(job_idx_zero, local_pct, status):
        # Map a per-job 0-100 into the global 0-100 across all jobs.
        slice_size = 100.0 / max(n_jobs, 1)
        overall = (job_idx_zero * slice_size
                   + (max(0.0, min(local_pct, 100.0)) / 100.0) * slice_size)
        bvh_state['progress'] = min(overall, 99.9)
        if status:
            if n_jobs > 1:
                bvh_state['status_text'] = (
                    f"Job {job_idx_zero + 1}/{n_jobs}: {status}")
            else:
                bvh_state['status_text'] = status

    try:
        for idx, (cmd, bvh_path, stem) in enumerate(jobs, start=1):
            if bvh_state['cancel_requested']:
                bvh_state['return_code'] = -2
                print("[BlendCap] BVH export cancelled by user.")
                return
            bvh_state['current_job'] = idx
            _set_progress(idx - 1, 0.0, "Starting...")
            if n_jobs > 1:
                print(f"[BlendCap] BVH job {idx}/{n_jobs} ({stem})")

            # Flatpak: npz_to_bvh runs the host venv python too (see
            # helpers._host_spawn_prefix); [] on every other platform.
            proc = subprocess.Popen(_host_spawn_prefix() + cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    bufsize=1, encoding='utf-8',
                                    errors='replace',
                                    env=_subprocess_env(),
                                    creationflags=getattr(
                                        subprocess, 'CREATE_NO_WINDOW', 0))
            bvh_state['active_process'] = proc
            for line in proc.stdout:
                if bvh_state['cancel_requested']:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    bvh_state['return_code'] = -2
                    print("[BlendCap] BVH export cancelled by user.")
                    return
                clean = _ansi_re.sub('', line).rstrip()
                # Parse `[PROGRESS pct] message` markers emitted by the
                # BVH pipeline (pipeline/modes.py, footskate_cleanup.py,
                # bvh_writer.py). Suppress the marker line from console
                # output to keep the log readable.
                m = _progress_re.search(clean)
                if m:
                    _set_progress(idx - 1,
                                  float(int(m.group(1))),
                                  m.group(2).strip())
                else:
                    print(clean)
                    if clean:
                        tail = bvh_state.setdefault('error_tail', [])
                        tail.append(clean[:120])
                        del tail[:-12]
            proc.wait()
            bvh_state['active_process'] = None
            # Cancel operator may have terminated the child directly while
            # the line iterator was blocked — treat that exit as a cancel.
            if bvh_state['cancel_requested']:
                bvh_state['return_code'] = -2
                print("[BlendCap] BVH export cancelled by user.")
                return
            if proc.returncode != 0:
                bvh_state['return_code'] = proc.returncode
                return

            final = os.path.join(bvh_exports_dir, f"{stem}.bvh")
            if os.path.exists(bvh_path):
                shutil.move(bvh_path, final)
                bvh_state['bvh_paths'].append((final, stem))
                # Keep single-path field populated for legacy callers
                bvh_state['bvh_path'] = final

            _set_progress(idx - 1, 100.0, "Job complete")

        bvh_state['progress'] = 100.0
        bvh_state['status_text'] = "Complete"
        bvh_state['return_code'] = 0
    except Exception as e:
        bvh_state['return_code'] = -1
        bvh_state['error_msg'] = str(e)
    finally:
        bvh_state['active_process'] = None
        bvh_state['running'] = False
