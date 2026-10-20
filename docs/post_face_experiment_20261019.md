# Archived facial experiment — 2026-10-19

Facial V1 was successfully implemented: independent external MediaPipe inference,
normalized evidence, private decisions/persistence and public Blender application.
All 51 supported VTA channels bound/normalized; FaceCap `_L`/`_R` aliases and
existing-animation preservation safeguards worked. Temporal filtering reduced
coefficient jitter without changing raw evidence or ambiguity handling.

Owner assessment: visual quality remained below the desired product bar and
better external first-pass demos. Decision on **2026-10-19**: remove facial
animation from current VTA scope; preserve this experiment for possible future
work. Final experimental validation: 617 public / 436 private tests passed.

Archive branch: `archive/post-face-experiment-20261019` in both repositories.
Pre-face public base: `ac247bc951ad64aceafc5ceadee0539567fd523a`;
private base: `e5185ca751ab571c3f12babf1217af094a5991b4`.

Ignored face proof/implementation/asset/stability checkpoints, reports, model
caches and installed-code backups remain local under `checkpoints/dev-preview`
and `checkpoints/mediapipe`. Desktop FaceCap QA assets, owner QA.blend/QA_vta and
comparison .blends are intentionally not committed. The two pre-existing public
source-coverage process-proof files remain untracked/local, not part of this
facial snapshot. No downloaded mesh/model binaries are included in this branch.
