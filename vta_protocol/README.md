# Neutral VTA protocol v2

Original, protocol-only definitions, dedicated under **CC0-1.0** for verbatim use
by the GPL public adapter, proprietary engine and future standalone clients.
No Blender/provider/domain implementation or GPL-derived probe code is included.
To the extent possible under law, the authors waive copyright and related rights
in this package under CC0 1.0. Provided without warranty.
Full dedication: https://creativecommons.org/publicdomain/zero/1.0/legalcode

The public repository is the editable source; the private repository vendors an
identical copy under `vta_protocol/`. Hash parity is tested and the ready handshake
requires the exact definition-file SHA-256. Neither implementation imports the
other repository to obtain definitions. Preserve byte identity (including line
endings); changes require compatibility review and synchronized deployment.

Version 2 adds neutral Character observation/rename, checkpoint/restore and
mutation-status vocabulary to source coverage. Revision/checkpoint/receipt schemas
carry bounded values and opaque IDs, never domain objects or snapshot documents.
Persistence, recovery, name validation and Undo policy live outside this package.
No arbitrary RPC, callbacks or unsafe deserialization.
