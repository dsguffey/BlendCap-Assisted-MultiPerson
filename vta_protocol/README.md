# Neutral VTA protocol v4

Original protocol-only definitions, dedicated under **CC0-1.0** for verbatim use
by the GPL public adapter, proprietary engine and future standalone clients.
No Blender/provider/domain implementation or GPL-derived probe code is included.
To the extent possible under law, the authors waive copyright and related rights
in this package under CC0 1.0. Provided without warranty.
Full dedication: https://creativecommons.org/publicdomain/zero/1.0/legalcode

The public repository is the editable source; the private repository vendors an
identical copy under `vta_protocol/`. Hash parity is tested. The ready handshake
fingerprints the exact bytes of `__init__.py`, `observation.py` and `domain.py` in
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
legacy general Undo cannot be executed by v4. Source-only M3 requires matching
public/engine packages; do not deploy one half independently.
