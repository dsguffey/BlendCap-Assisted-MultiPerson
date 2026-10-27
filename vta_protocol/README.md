# Neutral VTA protocol v1

Original, protocol-only definitions, dedicated under **CC0-1.0** for verbatim use
by the GPL public adapter, proprietary engine and future standalone clients.
No Blender/provider/domain implementation or GPL-derived probe code is included.
To the extent possible under law, the authors waive copyright and related rights
in this package under CC0 1.0. Provided without warranty.
Full dedication: https://creativecommons.org/publicdomain/zero/1.0/legalcode

The public repository is the editable source; the private repository vendors an
identical copy under `vta_protocol/`. Hash parity is tested. Neither engine nor
client adds the other repository to its import path to obtain these definitions.
Changes require protocol compatibility review and synchronized copies. Version 1
has one allowlisted read-only operation, no arbitrary RPC or object serialization.
