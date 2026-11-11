# MoCap Dope Sheet navigation

The MoCap canvas has one Scene/source/editor scoped `_row_scroll` anchor. It
positions Character names, review-key lanes, empty-key Characters, and Depth
children through the same `channel_rows` geometry. The custom vertical track is
drawn at the left edge of `CHANNELS` and operates only above the fixed bottom
marker and horizontal-scroll band. Wheel events over names and vertical MMB
motion change this same anchor; panning clears residual trackpad fractions.

MMB horizontal motion delegates to Blender's `view2d.pan` in the Dope Sheet
`WINDOW` region. Keys, markers, and the playhead continue to use that View2D
projection. Plain wheel over `WINDOW` passes to Blender. Blender's native right
scrollbar remains visible and controls native Dope Sheet state, not MoCap rows.
No native scrollbar suppression or repurposing is involved.

Host tests cover layout, resize, scroll endpoints, clamping, shared row geometry,
and native-pan dispatch. Foreground event routing and interaction feel still
require owner manual QA; no Blender process was launched for this pass.
