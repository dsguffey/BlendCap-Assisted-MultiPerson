# StudioFree MoCap — licensing, protection, updates and rollback

Approved product/release direction, **2026-11-09**. The Windows licensing
prototype was added on 2026-11-12; live Gumroad activation, owner UI QA,
installer/updater, and release assurance remain pending. See [roadmap](current_status.md) and
[runtime/package foundation](vta_runtime_installation.md).

## Lightweight licensing

Public 1.0 is expected to use **lightweight licensing rather than aggressive
DRM**. **Gumroad is the current preferred distribution/licensing provider**, not
an irrevocable architectural dependency. Prefer its software license-key
capability if it remains suitable when implementation begins:

- The purchaser receives a key and StudioFree MoCap verifies it during activation.
- Successful activation should support practical offline use afterward; avoid an
  online check on every launch.
- Avoid aggressive one-machine hardware locking. If activation counting is used,
  choose forgiving limits and reasonable reinstall/hardware-change recovery.
- Legitimate users should not be frequently blocked. Temporary network failure
  should not make an already activated local product unusable without good reason.
- Discourage casual copying while minimizing support burden. Do not add invasive
  DRM or telemetry merely to enforce licensing.

The prototype checked Gumroad's official license-key documentation on 2026-11-12
and uses its fixed HTTPS verify endpoint. Before release, recheck its current
API, activation/verification behavior, refund/revocation behavior, usage/activation
counting, offline/cache implications, terms and pricing. These are required future
release checks. Do not
hard-code Gumroad dependencies into unrelated core systems before that validation.

## Private-engine protection

Keep public/GPL Blender-side code appropriately source-visible. The private
Nuitka standalone prototype now enforces entitlement before protected
compiled operations. Licensing enforcement belongs in that boundary rather than
in trivially removable Blender-side Python. Compilation and licensing complement
each other as deterrents to casual redistribution. Native compilation is not
absolute protection against reverse engineering, and perfect DRM is not the goal.
The prototype is not the final installed release.

## Required update and rollback mechanism

Public 1.0 requires a supported update **and rollback** mechanism. Prefer versioned
immutable StudioFree MoCap application/runtime releases installed side-by-side.
User projects remain separate; configuration, caches, models and assets remain
separate where appropriate. Updates must not overwrite project files. Keep the
previous known-good release available for rollback.

The M5 foundation already describes coherent release staging and verified reuse
of pinned runtimes/assets by content identity. Preserve that separation; do not
mutate active Python environments or DLLs in place. Exact release retention and
rollback policy remains to be designed.

## Preferred initial update UX

Initial direction: **Resources / Preferences → Check for Updates**. Exact visible
placement/wording can still change during later polish. Expected sequence:

1. Check a versioned update manifest.
2. Determine whether a newer compatible build exists.
3. Download the new release package.
4. Verify package integrity and authenticity.
5. Install beside the existing release.
6. Switch the active release/configuration atomically or recoverably.
7. Validate startup.
8. Retain the previous release and roll back to known-good after a failed update.

**Public 1.0 does not require binary-delta patching.** A full application/runtime
release package is acceptable initially. Prefer correctness, authenticity,
rollback and project preservation over minimal patch sizes. Binary deltas may be
considered later if download size becomes a demonstrated problem.

## Update security and recovery requirements

The later implementation/security release pass must include:

- authenticated/signed update metadata or manifest;
- package integrity verification and resistance to update-source substitution;
- path/archive traversal protection;
- explicit version/component compatibility checks;
- atomic or recoverable active-version switching;
- rollback to a known-good release after failed updates;
- safe handling of stale/partial downloads.

Exact signing infrastructure remains open. These controls feed into ARAP T2 and
package/update/recovery release assurance. Existing staging/repair proofs are not
a complete updater or proof that these release requirements are already satisfied.

## Licensing and updates remain separate

Activation determines whether an installed product is licensed. Future update
entitlement may be checked when obtaining/activating paid updates. Normal startup
should not necessarily contact either licensing or update services every time.
No subscription or update-entitlement policy is being implemented now; pricing
and paid-upgrade policy remain open.

## Order and open decisions

Keep the roadmap order: fresh end-to-end workflow; workflow-driven bug/performance
cleanup; private-engine compilation/IP-protection prototype; lightweight
Gumroad-oriented licensing prototype; installer/package/update/repair/uninstall;
security/ARAP T2; licensing/compliance/provenance; final regression/clean-machine
QA; release documentation/support. Do not move licensing ahead of fresh-clip QA.

Still open: final storefront/distribution provider, activation limits, whether updates are free
forever/version-limited/tied to paid upgrades, signing infrastructure, exact
retention/rollback policy and final installer/updater UX. Tentative product-name
clearance and other existing release decisions remain open too. The later
licensing prototype has a seven-day revalidation interval and 30-day offline
grace; those values remain adjustable before release. This strategy document
does not authorize installer or updater deployment.
