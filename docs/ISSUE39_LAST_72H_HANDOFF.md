# Issue #39 — Last 72h handoff / PR #44 review

**Checkpoint:** `NEW_CUSTOMER_WEB_BUILD_AND_APEX_REPLACE` → `PR44_REVIEW_AND_LAST72H_HANDOFF` (Owner GO #7071362357). This document records only evidence available in GitHub and the current Codex thread. It does not assert staging or production verification.

## Sources and context recovery

- Repository: `Haisala/sala-bmt-listings`; PR branch `codex/issue39-source-reconciliation`; main baseline `83394a901dcc2c1944f083b65fc01c22e5a6602f`.
- PR #44 was reviewed at head `7527037e05de71fc5c9d905fdc0556d8ad342704` before this handoff change; it was open, draft, unmerged, GitHub `mergeable=true`.
- Owner/receipt sources: [Phase 1 local receipt #7058638110](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7058638110), [auth boundary #7066142822](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066142822), [one-system lock #7066301660](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066301660), [partial integration checkpoint #7066438757](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066438757), [source reconciliation GO #7066590041](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066590041), [First Web architecture/maintenance lock #7066721682](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066721682), [PR #44 receipt #7066860840](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066860840), and [current task #7071362357](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071362357).
- Codex history: current thread is accessible for the recent 21–22 Nov 2026 turn, but older-page retrieval returned `Invalid conversation`; other candidate local threads did not provide verifiable 19–22 Nov content. **CONTEXT_GAP:** no complete three-day Codex transcript or reliable comment timestamps were available through this session. No missing events/dates have been invented. GitHub issue/PR records above remain the source for decisions and receipts.

## Decision ledger (still in force)

1. One SALA BĐS system, one login/session, existing password; one canonical Property Master. Website Admin is a module for publication and website operations; Customer Web is read-only for approved projection and returns traffic events. Source: #7066301660 and #7066721682.
2. First Web core, admin/maintenance and release/data safety precede skinning; full First Web must be accepted before AI Content/CRM/Social/Market expansion. This is a design lock, **not evidence of implementation**: #7066721682.
3. Preserve the five master phases: Phase 1 local scope receipt does not prove integrated Phase 1; Phase 2 is NO_GO until isolated staging, exact SHA, and Owner gate; Phase 3 physical device tests, Phase 4 readiness, Phase 5 release/acceptance remain gated. Master #7058182954; rolling checkpoint rule #7062626669.
4. Never merge PR #44 or modify live runtime/data as part of this task. Current PR remains draft and unmerged.

## Timeline evidence (19–22 Nov 2026)

- **19–22 Nov window:** exact event dates for referenced older comments could not be read from the connector (`created_at` was absent); order and IDs are verifiable, calendar-day assignment is not. Treat this chronology as ordered evidence, not timestamp proof.
- Phase 1 Code B was reported `PASS_LOCAL` for its stated TEST_ONLY scope (#7058638110); not integrated/staging/production PASS.
- Owner locked one-system/one-session/canonical-core and publication-module architecture (#7066301660); later checkpoint still found separate auth authorities and canonical integration unverified (#7066438757).
- Owner authorized reconciling Code B source into a clean review branch/Draft PR only (#7066590041); later locked First Web and maintenance/data-preservation gates before other SALA BĐS modules (#7066721682).
- PR #44 was reported as 33 paths and seven isolated synthetic suites passing; Code A/B dirty worktrees were preserved. These are local TEST_ONLY receipts, not live runtime proof (#7066860840).
- Latest Owner action authorizes reviewing PR #44 and recording this durable handoff, with no merge or Phase 2 (#7071362357).

## Current code/review evidence

- PR #44 contains the Code B Admin/Property Memory port, tests, source narrative and SHA manifest. Seven previously recorded isolated suites passed per #7066860840. Their prior receipt was not rerun wholesale.
- Review found `admin/admin_server.py` eagerly constructed `UsageLedger(PRIVATE_ROOT / "provider-usage.sqlite3")` at import. `UsageLedger.__init__` creates the private root/database. This could write into the configured/default private store merely by importing the Admin module. Fixed narrowly with a thread-safe lazy ledger wrapper; isolated subprocess verification confirmed import creates no private root and first report initializes the ledger under a temporary root.
- Scope note: the port also includes AI9/AI Content/provider modules and Admin routes. This overlaps later-stage capabilities from the Owner lock. They remain disabled by default and no external model call was made, but this PR should not be interpreted as authorization to expose/activate those modules or proceed to a later phase. Review verdict remains **PARTIAL** pending explicit scope disposition and unified integration evidence.
- `keychain.py` was source-reviewed only; no Keychain command, credential resolution, or model call was executed. Tests for live provider behavior were not run.
- Artifact-builder coverage remains absent from the PR’s stated prior receipt: the test/source could not be ported against the recorded main baseline because `assets/brand/b-mark.png` was absent. No replacement asset was fabricated.

## Environment and safety state

| Layer | Verified state |
|---|---|
| DESIGN | Owner locks listed above; design only |
| CODE | Draft PR #44; narrow import-side-effect fix and handoff are on PR branch |
| LOCAL | Seven previous TEST_ONLY suites per #7066860840; this turn ran the temporary-root import/lazy-init check and `git diff --check` |
| STAGING | NOT_VERIFIED in this task |
| PRODUCTION | NOT_VERIFIED; no access/action performed |

No intake/property/review/media records, credentials, runtime processes, tunnels, DNS, or production deployment were read or changed. No API/model calls, approval, publication, or merge occurred. Original dirty worktrees were not modified.

## Active checkpoint and next action

**Review verdict:** `PARTIAL`; no P0 identified in the reviewed paths. One narrow P1 import-time write was fixed and isolated-tested. Remaining P1: resolve PR scope against the First Web sequencing lock and prove the unified one-session/canonical integration before any merge or Phase 2. `mergeable=true` is Git metadata, not review approval.

**One next action:** Owner reviews this handoff and decides whether the AI9/Content/provider paths remain in PR #44 as dormant dependencies or are split out before an integration review. Until then, keep PR draft/unmerged and Phase 2 NO_GO.

## START HERE IN NEXT CODEX SESSION

1. Read Issue #39 current master map and comments #7058182954, #7062626669, #7066301660, #7066590041, #7066721682, #7071362357.
2. Fetch PR #44 metadata and changed paths; confirm its current head before reviewing. Do not assume the SHA in this handoff is still current.
3. Read `docs/ISSUE39_SOURCE_RECONCILIATION.md`, `docs/ISSUE39_SOURCE_SHA256_MANIFEST.txt`, this handoff, and the changed Admin/Property Memory paths.
4. Preserve all dirty worktrees. No merge, runtime, staging, production, credentials, or phase transition without a distinct Owner gate.
