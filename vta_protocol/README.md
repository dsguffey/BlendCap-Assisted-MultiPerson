# Neutral VTA protocol v5

Original protocol-only definitions, dedicated under **CC0-1.0** for verbatim use
by the GPL public adapter, proprietary engine and future standalone clients.
No Blender/provider/domain implementation or GPL-derived probe code is included.
To the extent possible under law, the authors waive copyright and related rights
in this package under CC0 1.0. Provided without warranty.
Full dedication: https://creativecommons.org/publicdomain/zero/1.0/legalcode

The public repository is the editable source; the private repository vendors an
identical copy under `vta_protocol/`. Hash parity is tested. The ready handshake
fingerprints the exact bytes of `__init__.py`, `observation.py`, `domain.py` and `jobs.py` in
that order. Neither implementation imports the other repository for definitions.
Changes require compatibility review and synchronized deployment.

V4 adds explicit review/identity/Character management, suppression, cache-only
tracking planning/reconciliation, repair edits and one protected-result Restore.
It retains source coverage, Character rename and durable mutation status. Strict
16 KiB control envelopes carry IDs, revisions and bounded typed results. A separate
purpose-built review artifact (32 MiB maximum, fixed private-owned directory and
opaque ID, manifest SHA-256) carries bounded neutral presentation records. It is
never a ProjectState dump, native object, callback or arbitrary file request.

Persistence, CAS, recovery, protected-result retention, identity rules and public
presentation policy live outside this package. No arbitrary RPC, shell execution,
pickle or executable payload. V2 historical receipts remain readable for recovery;
legacy general Undo cannot be executed. Source-only M4 requires matching
public/engine packages; do not deploy one half independently.

V5 adds bounded generation/analysis preparation and durable job control, explicit
private result acceptance, native-output validation and final-motion retarget
artifacts. Control messages remain 16 KiB. Purpose-built job/motion artifacts are
checksum-verified JSON (32 MiB maximum); pose arrays remain bounded no-pickle NPZ
and native BVH files with manifest identities. Saved data never chooses executable
code or provider paths. Runtime configuration is live trusted launch input only.
Generation authority excludes accepted-output bookkeeping; full-document CAS still
includes it. Native application acknowledgements describe delivery, not a native
Save, current Blender Action, or domain rollback. A native Restore does not rewind
the engine's accepted artifact. No M5 packaging or general-history API is added.
