# Persistent manual-QA project

Use a single owner-facing **QA.blend**, together with its **QA_vta** companion.
For the current owner's local workflow, the stable location is Desktop/QA.blend.
Internal backups and diagnostic copies may have arbitrary names; they do not
become owner-facing handoffs. Do not issue another alphabetized QA filename
unless the owner explicitly requests one.

The contract is: **latest functionality + latest owner-authored QA state**.
It is not a perpetually frozen baseline. Once established, start subsequent work
from the current QA.blend and its current companion, not an earlier clean fixture
or a derived annotation copy. An explicitly identified newer owner file takes
precedence; confirm its provenance before replacing QA.blend.

## Standing last-known-good recovery point

The live QA file may be prepared for the owner's current test, preserving the
latest imported assets, materials and manual edits. Maintain one separate,
designated local last-known-good backup of both QA.blend and QA_vta, outside Git
and separate from disposable experiments. Verify fresh/background opening and
companion consistency before capturing it. Record timestamp, accepted public and
private commit SHAs, and any distinction between the accepted baseline and the
current test build in metadata beside the backup.

Keep that recovery point unchanged while a new workflow is unaccepted. Once the
owner accepts a code change and it is checkpointed/promoted, verify the then-current
live project and replace the backup with that exact owner state, including all
manual edits made during QA. Do not substitute an older derived fixture. QA data
must never be committed. Installed-build rollback copies are separate from this
owner-project recovery point. The locally designated backup and restore paths
are recorded in the preparation notes/backup metadata; reuse that location.

## Safe update procedure

1. Identify the latest saved owner state. Do not infer it from an internal copy's
   filename or newer timestamp. Coordinate saving if the owner has unsaved edits.
2. Take an internal safety copy of the .blend and its required companion/media/
   output dependencies. Record input hashes. Do experiments only on disposable
   copies or in-memory clones; do not write experimental tracking into QA.blend.
3. Apply needed migrations/new functionality to a separate staging copy. Prefer
   supported native Save As for independent project copies. Copying the .blend
   alone does not preserve all external VTA state.
4. Compare before/after owner state: project/source/Character identities, all
   authoritative decisions and frames, review flags, suppression history, repair
   ranges, Scene membership, persistent selection, animation, source alignment,
   manual Object Mode/depth edits, and recovery state. Verify required cache/media/
   generated dependencies and their rebound paths. Raw project/cache bytes should
   remain identical when no schema migration is required.
5. Use evaluated samples for animation comparisons. Temporarily enable hidden rigs
   for sampling, update evaluation, then restore visibility. Check a fresh Blender
   process as well as the writing process.
6. Publish the validated result under the same QA.blend name, preserving the prior
   version internally. Check that the owner has not edited the input since the
   snapshot; never overwrite concurrent/newer owner work. If copying/migration
   cannot preserve a state field, stop and explain it, retaining the original.
7. Hand back the stable path and a short preservation summary. The owner should
   not have to recreate assignments or repeat already accepted visual QA unless
   a relevant behavior changed.

These are explicit development/handoff steps, **not an automatic background file
updater**. They do not change production Scene routing or Save As behavior.
Temporary worker status, locks and logs are not owner-authored state; do not carry
active-operation markers into a new QA project. Preserve the authoritative state
and the dependency closure instead.

Every handoff includes plainly:

- **Manual QA file:** exact stable path.
- **Based on:** exact owner source path and provenance.
- **Changes made to prepare this copy:** explicit list.
- **Known special test modifications:** list, or `None`.

If a separate diagnostic file contains synthetic transforms, injected failures,
partial replay output or stress-test modifications, explicitly label it:
**This is a stress-test/diagnostic fixture and is not suitable for normal visual QA.**
Do not substitute such a file for the persistent owner project.
