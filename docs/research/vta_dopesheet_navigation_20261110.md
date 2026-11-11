# MoCap Dope Sheet navigation

The MoCap canvas has one Scene/source/editor scoped `_row_scroll` anchor. It
positions Character names, review-key lanes, empty-key Characters, and Depth
children through the same `channel_rows` geometry. The anchor stores a fractional
row position so MMB and elevator motion follows pixels; plain wheel over names
moves half a row per notch. Partially visible rows are clipped to the scrollable
lane, and row hit tests use that same boundary. The custom vertical track is
drawn at the left edge of `CHANNELS` and operates only above the fixed bottom
marker and horizontal-scroll band. Wheel events over names and vertical MMB
motion change this same anchor.

Blender's `View2D` keymap handles plain wheel and MMB before the Dope Sheet
and Animation Channels keymaps. The MoCap navigation bindings therefore live
at the head of `View2D`, guarded by the MoCap editor scope. A left-click binding
there claims only the custom elevator; ordinary clicks pass through to the
existing selection handlers. It gives the elevator first claim on its own
left-edge hit area, including immediately after a wheel event.

MMB horizontal motion delegates to Blender's `view2d.pan` in the Dope Sheet
`WINDOW` region. Keys, markers, and the playhead continue to use that View2D
projection. Plain wheel over `WINDOW` passes to Blender. Blender's native right
scrollbar remains visible and controls native Dope Sheet state, not MoCap rows.
No native scrollbar suppression or repurposing is involved.

Host tests cover layout, resize, fractional scroll endpoints, clipping, shared
row and hit geometry, and native-pan dispatch. Foreground interaction feel still
require owner manual QA; no Blender process was launched for this pass.
