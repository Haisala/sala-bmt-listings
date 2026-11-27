# Issue #39 — Customer/Admin UI handoff contract

Status: source contract for the existing First Web implementation; not evidence of staging, production, or Owner iPhone acceptance. Keep the Owner-approved ChatSite visual shell unchanged when wiring these fields and actions.

## Customer Web public read surface

| UI action | HTTP contract | Required lineage / behavior |
|---|---|---|
| Home / feed | `GET /api/listings` → `{items:[...]}` | Admin projection only; fail closed with 503 if projection is missing or invalid. |
| Search / transaction filter | `GET /api/listings?q=<text>&transaction_type=SALE\|RENT` | Query text is not sent to analytics; only an allowlisted `query_used` or transaction filter event is emitted. |
| Listing card | Feed item fields: `listing_id`, `property_id`, `property_revision`, `projection_id`, `publication_revision`, `publication_id`, `transaction_type`, `title`, `price_vnd`, `area_m2`, `property_type`, `location`, `media[]`. | Card and analytics use the same active projection/publication lineage. |
| Detail | `GET /api/listings/{listing_id}` | Same property/revision/publication identity plus description and approved media refs; private fields excluded. |
| Media | `GET /media/{media_id}` | Server resolves only approved refs beneath the configured public assets root; stale/revoked rights return 404. |
| Page/detail/CTA receipt | `POST /api/analytics/events` | Customer backend relays a small allowlisted event to Admin; detail/card/CTA require both `property_id` and active `publication_id`. No cookies, IP, user-agent, phone, email or free-text search are forwarded. No relay configuration → 503. |

## Admin-only owner flow

1. Admin Owner session accesses Intake, review facts, Gate A and Property Core. Gate A does not publish.
2. Admin's distinct Website publication decision produces the revision/media-rights-bound public projection. Revoke or canonical revision change makes the prior projection and event publication ID invalid.
3. Existing Content Studio API is protected by the Admin session: `POST /api/copy-studio/preview/{ai_run_id}` and `POST /api/copy-studio/generate/{ai_run_id}`. Preview is deterministic/offline. Live generation is separately opt-in and currently BLOCKED by Issue #38 credential preflight; no paid call was made here.
4. The live generation contract currently creates five private variants per batch. Content is not a Website publication. The present source has no complete Owner UI/API lifecycle for selecting one variant, editing it, approving content/Gate B, and binding that exact version to Website publication. Do not represent the goal flow as complete.

## Remaining UI work / compatibility

- The current `customer_web_v2/frontend` is a compact service UI, not the approved ChatSite visual shell. The legacy Customer Owner panel has been removed from the new public bundle; the deployed entrypoint disables legacy Customer Owner/intake/review APIs. A verified design-preserving integration remains outstanding.
- Do not copy the Admin cookie/password to Customer Web. Admin Owner session remains server-side; Customer public reads do not require login. The public service starts without the legacy Customer SQLite store, Owner credential, or session authority.
- Owner's disposable-draft decision applies to classified old SALA BĐS listing drafts only. It does not authorize deletion of code, secrets, connections, shared services, or unclassified data. No cleanup/cutover occurs in this source handoff.

Acceptance evidence must separately include real Admin→Core→projection→Customer HTTP, active/revoked publication analytics receipt, the Content Studio credential/model/usage evidence required by Issue #38, then Owner's physical iPhone review after a separately authorized release. Synthetic tests cannot establish any of those live gates.
