# Issue #39 — controlled source reconciliation

## Baselines

- GitHub `main`: `83394a901dcc2c1944f083b65fc01c22e5a6602f`.
- Code A (`codex/designer-web`): `720ebd4c87344d35935e6ccb3da39b1b9e04d758`, dirty; left untouched.
- Code B (`codex/system-backend`): `008057acd084e5452928c81bc7421baf9575d8191`, dirty/untracked source present; left untouched.
- Reconciliation branch starts at the exact GitHub `main` SHA above. This is source review only; no service/runtime or property data was changed.

## Source mapping

`admin/admin_server.py` plus its direct dependency closure under `property_memory/` supplies the Admin auth boundary, Intake/Gate A, PropertyStore, review and revision handling, public-release approval, safe projection, and analytics endpoints/store. These paths were absent from `main`; their inputs came from the Code B checkout. The working-tree Admin server change was reviewed as a source delta and carried with the modules it imports.

Ported source paths:

- `admin/admin_server.py`
- `property_memory/{__init__,ai9_layers,ai_gateway,analytics_store,content_studio,evidence,evidence_read_model,intake_adapter,intake_review,intelligence,keychain,matcher,mutation_service,projection_builder,public_contract_adapter,review_center,runtime_config,runtime_lock,staging_artifact_writer,store,usage_ledger}.py`
- `property_memory/providers/{__init__,openai_provider}.py`

TEST_ONLY coverage added from Code B:

- `scripts/test_analytics_admin.py`
- `scripts/test_analytics_store.py`
- `scripts/test_public_release_admin.py`
- `scripts/test_slice1_private_integration.py`
- `scripts/test_slice2_gate_a_private.py`
- `scripts/test_slice3_projection_private.py`
- `tests/test_property_purge_contract.py`

`property_memory/keychain.py` is existing provider adapter source only; no Keychain command, credential read, API request, or new credential store was run or added. Test execution used isolated temporary paths.

## Deliberate exclusions and known gaps

- No Customer Web UI, legacy listing adapter, NaviBDS, SALA Media, real DB, private media, logs, passwords, tokens, or deploy workflow was ported.
- The `market_comparables` endpoint test was excluded because it depends on an unrelated module outside this Phase 1 integration set.
- The separate artifact-builder test was not ported: its required `assets/brand/b-mark.png` is absent from this exact `main` tree. Existing Phase 1 artifact receipt is retained and not re-run here.
- This source reconciliation does not prove one shared login/session, a verified active canonical read path, connected analytics receipts, public HTTPS, or apex readiness. No real record was read or written; no approval/publication was performed.

## Test-only changes

- Analytics date assertions use UTC because stored event timestamps are UTC.
- Projection allowlist expectation includes the already emitted source/publication revision and media-rights metadata; all other unapproved output remains rejected.
