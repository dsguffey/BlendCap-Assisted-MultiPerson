# Engine boundary — Architecture Cleanup Milestone 1

Repository implementation, 2026-10-27. Only **source coverage** has migrated.
Other features still use the legacy in-process private backend and mixed workers.
This is not a claim that the whole addon is isolated or ready for release.

Explicit Analyze/Repeat Assignment coverage queries use
`blendcap_vta_engine.SourceCoverageClient` → a standalone private `vta_engine.py`
process → the canonical private project loader. The old backend coverage method
is removed. The Analyze panel reads validated source metadata for its range label;
draw/poll/observer paths do not launch coverage subprocesses.

Transport: one child per request, inherited stdin/stdout pipes, no listener or
persistent endpoint. The public adapter starts and owns shutdown. `-I -B` isolates
the child's Python path and suppresses bytecode. A ready/version/capabilities/PID
handshake precedes a UUID-correlated request and one final response. The default
overall deadline is 15 seconds; invalid responses, crash, nonzero exit and timeout
fail visibly, kill/reap an owned live child and close pipes. There is no legacy
fallback. A writer thread also bounds the case where the child stops reading stdin.

Neutral definitions live in `vta_protocol/`, original **CC0-1.0** protocol-only
code, vendored byte-identically in the private repo. Neither side imports the
other implementation to obtain them. No Blender, provider or domain dependency.
This placement is an engineering/provenance choice, not legal certification.

Version 1 uses deterministic UTF-8 JSON with strict field/type/version/operation/ID
validation, 16 KiB messages, depth/node limits, finite numbers, bounded integer
frames, absolute local paths, and no traversal/device/network/alternate-stream
syntax. Only `source-coverage` is allowed. No pickle, objects, callbacks, imports,
shell commands or generic RPC. Engine file references resolve to regular files;
project/analysis references cannot escape the selected companion. The canonical
project document is capped at 64 MiB; source hashing streams data. File signatures
are rechecked to reject detected input changes. No project writes occur.

Installation executable paths are trusted user/install configuration, never wire
payload. Same-user processes/files are trusted at the OS level: this is not an OS
sandbox, privilege boundary or protection from a user replacing their engine.
No unrelated process can connect through a persistent service endpoint because
none exists. No new authentication service is needed for these owned pipes.

Failures have stable error codes and bounded messages. Standard Python logging
records request ID, operation, child PID, exit and bounded escaped stderr (8 KiB);
the client also retains last diagnostics and startup/round-trip/lifetime metrics.
Logging destinations follow host configuration; no logs are written into owner
project state by this layer. Successful queries do not dump project contents.

Discovery uses the saved Core folder or existing product/development defaults,
checking launcher files without importing private modules. It uses an existing
standalone Python (addon venv when present, otherwise bundled/system Python);
no dependency installation is added. Packaging/version-matched executable
deployment remains later work. This milestone does not update the installed addon.

No session engine is resident: a dead process is discarded and a later explicit
query starts clean. Owned children are reaped on handled failures; persistent
orphan detection and parent-crash supervision remain packaging/recovery work.
Future mutation can reuse request/error/lifecycle framing, but needs receipts/CAS
and Undo first. Progress/cancel and large file manifests require explicit typed
protocol extensions; no job orchestration was implemented now.

Verification includes fresh factory-startup Blender with private-import blocking,
real private execution with host-import blocking, accepted project parity and
owner-state hashes, malformed/hostile wire cases, crash/timeout/pipe backpressure,
native Undo and full public/private regressions. Detailed measurements and remaining
milestone estimates live in the private M1 research report and canonical contract.
