# Developer note: legacy Face capture retirement

The facial experiment is preserved separately on
`archive/post-face-experiment-20261019` in both repositories. It is outside the
current VTA product scope; no experimental facial code is routed in this branch.

The inherited MediaPipe capture worker, scheduling branches, Face capture RNA
property/toggle and facial application operator registrations/UI are removed.
Saved legacy `track_face` data cannot schedule work: capture now requires Body,
and a face-only request fails before source resolution or output creation.
Existing user files/caches are not mutated. Cached facial data is no longer
silently included in body-capture overlays.

Body/identity/tracking/camera code and private core need no MediaPipe. Requirements
no longer install it; no OpenCV or shared dependency was removed. Existing
installations may still contain MediaPipe, but supported VTA is verified with
its imports blocked. Package uninstallation is not necessary for this proof.

Shared body-export modules and generic existing-data retarget helpers remain;
some retain facial terminology/compatibility parameters and attribution. Do not
delete those imports wholesale or strip notices from retained assets/code.

The paid installer sources/offline wheel stash are not available in these
checkouts or the installed add-on. They must consume the updated requirements
and separately exclude MediaPipe checks/downloads before a commercial build can
claim installer-level removal. This change does not create an installer or
repair the existing duplicate OpenCV distributions.
