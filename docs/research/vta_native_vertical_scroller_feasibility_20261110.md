# Native Dope Sheet vertical scroller feasibility — 2026-11-10

This is a source-level feasibility audit. No Blender process, computer control,
owner file, native animation channel, or installed addon was changed.

## Supported surface

Blender 5.2's Dope Sheet [sets the View2D vertical total from its filtered
native animation-channel count on every draw](https://github.com/blender/blender/blob/blender-v5.2-release/source/blender/editors/space_action/space_action.cc#L182-L220).
Its right vertical scroller has the `V2D_SCROLL_VERTICAL_HIDE` behavior. When
the native total fits the visible View2D, [Blender hides the
scroller](https://github.com/blender/blender/blob/blender-v5.2-release/source/blender/editors/interface/view2d/view2d.cc#L125-L145).
The [public View2D API](https://docs.blender.org/api/5.2/bpy.types.View2D.html)
provides coordinate transforms, including a way to observe a changed vertical
view coordinate. The [public operators](https://docs.blender.org/api/5.2/bpy.ops.view2d.html)
can pan, scroll, and invoke native scroller interaction. They do not expose the
native total, scrollbar position/range, or a supported setter for a custom
total. Native scroller dragging owns its mouse interaction.

## Required scenario check

| Native channels | MoCap rows | Source-level outcome |
| --- | --- | --- |
| Few or none | Many Characters, including empty-key rows | Native extent can fit the viewport, so the native vertical scroller disappears or has no useful travel while MoCap still needs scrolling. |
| Many | Few Characters | Native scroller travels through native data even when the MoCap row offset has no range. Its thumb/range cannot be made coherent from the public API. |
| Fewer than MoCap Character + expanded Depth rows | Many | A native thumb may move, but its endpoints and travel are set by unrelated native rows; full MoCap reach and stable normalization cannot be guaranteed. |
| Native channel count changes during review | Fixed MoCap rows | Blender recalculates/clamps the native total on draw, changing or removing the control independently of the MoCap row model. |

`region_to_view()` could detect a *change* in the native vertical view when the
native scroller actually moves. It cannot make the scroller exist or move when
there are too few native channels, and it does not provide the total needed to
normalize its position. Calling native pan/scroll operators after a MoCap wheel
event cannot overcome the native range clamp. Thus one MoCap offset can continue
to serve Character names, key lanes, Depth children, wheel, and the current
custom elevator, but the native elevator cannot reliably be another input to
that offset across the required scenarios.

Decision: native repurposing fails the bounded, supported implementation gate.
Leave the working custom scrollbar and Blender's native scrollbar unchanged.
Do not repeat the channel-filter workaround or deploy speculative View2D state
changes. Foreground Blender behavior was not tested because launching Blender
and controlling the owner's computer were explicitly prohibited.
