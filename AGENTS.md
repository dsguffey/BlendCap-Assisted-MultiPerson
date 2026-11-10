# Owner computer access

Do not use computer control, UI automation, screen capture, or desktop takeover
unless the owner explicitly requests it for the current task. The owner uses
the computer while development is running. Use code, files, and command-line
checks; leave interactive QA to the owner. Do not launch Blender for this
regression pass after the owner's explicit stop request.

# Owner-facing QA workflow

Before preparing a manual-QA .blend handoff, read `docs/qa_workflow.md` in full.

The stable owner-facing file is Desktop/QA.blend with its QA_vta companion.
Preserve the latest owner-authored state across development passes. Do not issue
new alphabetized QA filenames, overwrite newer owner work with an old fixture, or
bake disposable research into the handoff. Use internal safety/staging copies and
verify preservation before replacing the stable file. An owner-explicit newer
source supersedes an older derived annotation copy.

Report exact file provenance, preparation changes and any special diagnostic
modifications. Do not require the owner to recreate authoritative assignments.
