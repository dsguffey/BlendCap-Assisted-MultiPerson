# StudioFree MoCap support and troubleshooting — release candidate draft

Start with the [customer guide](studiofree_customer_guide.md). This page is
prepared for final release QA; the final support contact and download channel
will be supplied with the commercial release.

| Symptom | First check |
| --- | --- |
| Installer cannot find Blender | Install Blender 5.2.x separately, close it, and choose its `blender.exe` when prompted. Other versions are not validated. |
| Installer reports missing or changed package files | Extract the entire official ZIP again. Keep the EXE and `payload` together. Do not bypass verification or combine versions. |
| Install was interrupted or files are damaged | Keep Blender closed; rerun the matching complete installer to repair the active version. Check free disk space first. |
| Activation fails or status is unavailable | Check the entered key and internet connection; use **Resources → Licensing → Check Status** or **Retry Validation**. A new key needs online activation. Never send the key in a support report. |
| Video or review is unavailable | Confirm the movie still exists and that the `.blend` is accompanied by its matching `_vta` folder. Use **Load MoCap Scene** for the intended Scene. Do not replace a companion with one from another project. |
| Analysis fails or is slow | Check the selected footage range, available disk space, NVIDIA driver/GPU compatibility and the error shown in StudioFree. Large clips and several people can take longer. Avoid running another analysis over an active job. |
| A Character follows the wrong person | Review the crossing frames, apply explicit Candidate assignments or suppression, then **Update Tracking** for the selected Character. Tracking is derived and may remain unchanged until updated. |
| Generated motion has an unwanted transition | Inspect native Blender keys, remove unsuitable intermediate keys, adjust interpolation and preserve useful anchors. Regenerate a limited range only when needed. Occlusion can require manual edits. |
| Export or retarget is disabled/refused | Use Object Mode, select an eligible generated Character for **Export BVH**, or choose a supported target profile for **Retarget Animation**. Check the selected frame range and the profile's required skeleton. |
| Update or rollback fails | Keep the complete matching package and previous release available. Do not edit installed files. Use the verified installer to repair, or contact support for rollback procedure. |

In **Resources → Logs and Support**, choose **Open Logs Folder**. A failed
operation may also show **Go to Log** beside its details. For installation or
repair failures, the maintenance log is under
`%LOCALAPPDATA%\VTA\logs\maintenance.jsonl`. Other operation logs are in the
project companion's analysis/reconstruction areas. Share only the relevant
error excerpt or a sanitized log, after checking it for personal paths and
other sensitive information. Do not routinely upload source videos, entire
`_vta` directories, license keys or customer projects.

## Issue-report template

- StudioFree version and displayed deployment/build ID:
- Blender version:
- Windows version:
- NVIDIA GPU and driver version:
- Operation and steps to reproduce:
- Expected result:
- Actual result and exact visible error:
- Does it recur with the same project or a disposable new project?
- Optional relevant, sanitized log excerpt:

Support contact: **[final owner-approved support channel to be added before
publication]**. Include screenshots only after checking for private footage,
paths and license information.
