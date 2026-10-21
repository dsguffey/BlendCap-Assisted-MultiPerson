# Retarget UI assessment — 2026-10-20

This pass changes public Blender presentation only. No private implementation,
calibration, motion policy, baker, inference, ProjectState schema or Review Key UX
changes. The FK engine remains the GPL BlendCap reuse path, not proprietary code.

## Existing functionality inventoried before hiding the old UI

| BlendCap facility | VTA decision / retained value |
|---|---|
| Native source/target armature pointers with eyedroppers | Reuse the native interaction, with separate VTA-scoped filtered pointers. Do not trigger legacy rig-picker callbacks/preset/IK mutations. |
| Prefix-aware mapping table and per-pair validation | Retain machinery. VTA shows actual resolved names and match count read-only; its validated automatic map remains authoritative. |
| Standard/custom presets, namespace prefixes, reload/save/delete maps | Keep operators, files and data unchanged for future skeleton support. Hide in VTA QA. |
| Auto-match, add/all/remove/sort/clear pairs, channel/axis/anchor editing | Keep underneath; manual remapping/general profile editor not exposed or certified for V1. |
| Current-pose/custom saved-rest tools and preview | Keep for later assessment. VTA keeps automatic actual-rest/anatomical calibration; does not apply object transforms or overwrite bind/rest data. |
| Auto scale, world-location and leg/anchor policies | Retain generic controls, but VTA keeps its already validated rest-leg ratio/composite-pelvis-once policy. |
| FK→IK chain discovery, IK/pole assignments and conversion | Retain operators, settings and examples; no VTA IK certification/UI yet. |
| Advanced body/face channels, amplification, constraint/denoise options | Retain legacy implementation, do not enable in VTA body FK or revive facial capture. |
| Bake progress/cancel | Preserve VTA's staged transaction, modal status and Esc cancellation. No direct writes into the old target Action. |
| Source/target object selection | Use Object Mode / 3D View pickers. Mesh parts are excluded, so a multipart character is represented by its armature. |

The old draw helpers remain in panels.py for later comparison. They are no longer
called by the normal host sidebar/Output panel. Legacy preset resources, mapping
properties, FK/IK tools and operator registration are unchanged. No code is copied
to the private core, and no supported VTA workflow routes through unsafe generic
Apply Retargeting defaults.

## Approved visible scope

- Rename the existing 3D sidebar tab/root to VTA; retain stable internal class IDs
  and parent relationships to avoid breaking references.
- Native Object pointers filtered to generated source armatures in the current
  VTA Scene, and non-VTA target armatures in that Scene; no mesh-part dropdown.
- Object Mode retarget panel, explicit inclusive range, automatic preflight,
  collapsible read-only mapping and the existing staged-Action bake.
- No retarget entry in Preview. The generic Blender F3 operator remains a 3D-view
  convenience, sharing these choices rather than a separate retarget pathway.
- Dedicated last Credits panel with BlendCap/Arcomade contribution and copyright,
  no-warranty/GPL notices, local license/notices, original/modified source links
  and existing support button. Existing paid/hide-support preferences still apply.

Pointer choices and the map expansion toggle are Blender presentation state only,
not private authority. Draw/poll does not sample motion, load project JSON, mutate
maps or run calibration/baking. Required-bone/refusal/old-Action safety is unchanged.

## Licensing scope check

The repository's LICENSE and NOTICE identify GPL-3.0-or-later, copyright 2026
Arcomade. GPLv3 sections 4/5 permit modification subject to retained notices,
modified-work/source obligations and applicable interactive legal notices.
Changing the visible workflow/branding is not a license transfer: reused engine
and public host changes remain GPL-covered. No new UI-move licensing blocker was
identified with those notices preserved; this is not a legal opinion or a new
certification of proprietary-core separation, dependencies, paid installer or
offline redistribution. Those broader audits remain separate.

Primary license text: https://www.gnu.org/licenses/gpl-3.0.html
Accessible authoritative mirror: https://gcc.gnu.org/onlinedocs/gcc/Copying.html
Local copies: LICENSE and NOTICE.txt. No attribution/license file was removed.
