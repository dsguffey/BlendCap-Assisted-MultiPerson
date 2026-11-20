# StudioFree MoCap 1.0 customer guide — release candidate draft

**Status:** Prepared for final release QA. StudioFree MoCap 1.0 is not yet a
commercially released or clean-machine validated product. The installer steps
below describe the intended signed customer package; use only a package from
the official seller channel when it becomes available.

## Before you install

- Windows x64 and a separately installed **Blender 5.2.x** are the current
  supported configuration. Other Blender versions and operating systems have
  not been validated for this release.
- The current AI inference path requires a compatible NVIDIA GPU and driver.
  The exact minimum GPU and driver are **pending clean-machine release QA**;
  the development machine's GPU is not a minimum specification.
- The last full internal package was about **10.58 GB to download** and
  **15.60 GB extracted**. Allow **at least 60 GB free** during installation
  for extraction, versioned releases and staging. Final customer package size
  must be checked when that package is built.
- Internet access is needed for initial license activation and later license
  revalidation. An already activated installation has a limited offline grace
  period; see [Licensing](#licensing-and-offline-use).

StudioFree's complete offline package includes its Python runtimes, AI models
and other application dependencies. Blender and the NVIDIA driver are separate
prerequisites; no additional Python or model download is intended.

## Install and first launch

1. Download the **complete ZIP** from the official seller channel and extract
   it fully. Keep the installer EXE beside its `payload` directory. Do not run
   it from inside the ZIP or move the EXE away from the payload.
2. Check that Windows identifies the expected StudioFree publisher on the
   installer signature. If the publisher is missing, unexpected, or the
   signature is invalid, stop and obtain a fresh official package. Signing
   does not guarantee that Windows SmartScreen will show no warning.
3. Close Blender, run the installer, and select your existing Blender 5.2.x
   executable if prompted. The installer verifies the package before running
   its included application code.
4. Launch Blender and open the **StudioFree MoCap** workspace/sidebar. Under
   **Resources → Licensing**, choose **Enter License Key** and enter the key
   from your purchase. A popup reports the result. **Check Status** displays
   the current state; **Retry Validation** requests an online check.

If installation stops, keep the ZIP and extracted payload together and use
the matching installer again after resolving the error. Do not disable package
verification or substitute files from another build. See
[Support and troubleshooting](studiofree_support.md).

## First project: footage to animation

1. In **MoCap Setup**, choose **Create MoCap Workspace**. Use **Video Source**
   to choose a movie clip. Use **New MoCap Scene** or **Load MoCap Scene** when
   working with saved MoCap Scenes. A Blender `.blend` file and its adjacent
   `_vta` project companion belong together; keep both when moving a project.
2. In **Analyze**, choose **Whole Source** or **Frame Range** and analyze the
   footage. Analysis produces detected **Candidates**: observations of people
   at particular frames, not persistent person identities.
3. In **Character Management**, create or select **Characters**. A Character
   is the persistent identity you want to animate. Its management selection
   does not itself assign a Candidate or choose a generation target.
4. In **Character Assignment**, choose a target Character and Candidate, use
   **Mark for Assignment** across the desired frames, then **Assign Marked**.
   **Mark for Suppression** and **Suppress Marked** exclude mistaken detected
   observations from automatic continuation. Pending marks are temporary;
   applying them saves the review decision. For unclear footage, use the
   available Occluded / Not Visible / No Valid Candidate decisions as
   appropriate.
5. Use **Update Tracking** on selected Character rows when you want the system
   to reconsider the frames between explicit decisions. An assignment edit is
   authoritative immediately, while derived tracking does not necessarily
   change until you explicitly update it. Inspect ambiguous crossings rather
   than assuming continuity is correct.
6. In **Animation**, choose **Whole Source** or **Frame Range**. Select the
   Character rows to generate; coverage selects **frames**, row selection
   selects **Characters**. Choose **Generate Animation**, watch progress, and
   review the resulting editable Blender animation.
7. Save the Blender project and its `_vta` companion. Use **Export / Retarget**
   when the animation is ready for another tool or supported target rig.

People crossing, obscuring one another or leaving the frame may need manual
identity and animation corrections. Review decisions and native animation
edits remain separate: assigning a person does not automatically edit a
generated Blender Action.

## Refine animation and Depth

Inspect the generated motion in Blender. Remove inappropriate intermediate
keys when useful, use Blender interpolation to smooth transitions, and retain
intentional sparse edits. Regenerate a selected **Frame Range** only when the
source decisions or motion genuinely need recomputation. Difficult occlusions
may need direct manual correction.

**Fixed Depth** is the default. Optional **Automatic Depth** is available in
advanced camera controls, but accurate moving-camera depth recovery is not a
1.0 promise. For manual camera-axis placement, select a generated Character
in **Object Mode**, choose the **VTA Depth** viewport tool and drag its handle.
With Auto Key on, the tool adds native Blender Object Location keys; with Auto
Key off, it changes location without adding a key. The Character's **Depth**
child row shows these native keys. Blender Undo/Redo, Graph Editor editing and
ordinary interpolation apply. [Depth guidance](vta_depth.md) has the exact
selection and keying behavior.

## Export and retarget

For **Export BVH**, select one generated Character armature in Object Mode and
use **Export / Retarget → Export BVH**. It exports the current evaluated
animation over the Blender Scene frame range, including current Depth and
cleanup edits. It does not export meshes, materials or an arbitrary control
rig. Keep a saved copy of your `.blend` before major edits.

For **Retarget Animation**, choose a source Character and a target armature in
Object Mode, select a compatible target profile and frame range, then run the
command. The bake creates a new Action and retains prior animation. The current
profile set includes Mixamo FK, stock generated Rigify FK, Unreal/Epic UE4 FK,
UE5 Manny/Quinn and Godot Humanoid skeleton profiles. Validation depth varies
by profile; [profile status and constraints](vta_retarget_profiles.md) are the
authority. Arbitrary rigs, control rigs and universal retarget compatibility
are not promised.

## Licensing and offline use

Open **Resources → Licensing**. **Enter License Key** activates online and
reports success or failure in a popup; the panel also shows status. **Check
Status** reads the current entitlement, while **Retry Validation** asks the
licensing service to check it again. If activation fails, verify the key,
network connection and purchase status, then retry. Do not include the key in
support messages or screenshots.

After successful activation, temporary network failure does not immediately
disable an existing entitlement. The current policy attempts revalidation
after seven days and permits up to 30 days from the last successful check
while the service is temporarily unreachable. A confirmed refund, revocation,
chargeback, unresolved dispute or ended entitlement can stop protected work
immediately. Reinstallation, update and repair preserve the protected local
entitlement; a different Windows user or a lost entitlement may require
activation again. Multi-seat use follows the purchased quantity; contact the
seller's support channel for seat recovery. Final customer terms and support
channel are pending owner approval.

## Update, repair, rollback and uninstall

Version 1.0 uses **complete offline ZIP downloads** for updates. Close Blender,
extract the new package and run its signed installer. It installs a versioned
release beside the previous one, verifies it and switches the active version.
Compatible unchanged large files may be reused locally; the download is still
complete. There is no background auto-updater or binary-delta patch.

To **repair** missing or corrupted application files, rerun the installer for
the **currently active version** from its complete, verified package. Do not
mix an EXE and payload from different versions. A previously retained version
can be restored through the maintenance rollback operation after verification;
there is no documented in-Blender rollback button. If a customer needs
rollback, contact support for the correct package and procedure.

Uninstall through Windows Installed Apps. The installer removes StudioFree-owned
application files and attempts to remove the Blender extension if Blender is
still available. It **preserves** your `.blend` files, adjacent `_vta`
companions, exports, protected entitlement, logs and other user-owned content.
Keep backups of important projects before any major upgrade or uninstall.

## Limits and legal information

This release targets editable skeletal motion from single-camera footage.
Identity ambiguity and difficult occlusions can require human decisions and
manual animation cleanup. Facial motion capture, moving-camera automatic depth
recovery, arbitrary third-party rig retargeting, non-NVIDIA inference and
non-Windows/Blender 5.2 configurations are outside the currently validated
1.0 scope.

**Resources → Credits** provides concise acknowledgments. The distribution's
`Third-Party-Notices/` and `Corresponding-Source/` bundle carries the applicable
full notices and source material for the exact package. Credits do not replace
those notices. The public Blender integration derives from BlendCap; its
attribution and license remain in the accompanying source/notice materials.
Third-party projects are not represented as endorsing StudioFree.
