# ADR-0013: Audit event vocabulary — event_type strings + data_json shape

## Status
Accepted — 2026-05-19 (slice 9 — retroactively codifying the
event_type catalogue per Hex Audit SA-F7)

## Context

ADR-0007 §"Audit log scope" names the **categories** of events that
are audited (auth, run lifecycle, secret lifecycle, worker
exceptional events, system maintenance lifecycle). It does NOT pin
the **exact** `event_type` strings or the **shape** of `data_json`
for each event. Every new call site that called `audit.append(...,
event_type="something", data={...})` was making three implicit
choices the ADR had no opinion on:

1. **Naming convention** — is it `password_changed` or
   `passwordChanged` or `PasswordChanged`? Is it
   `target_credential_set` or `credential_set`?
2. **Outcome encoding** — does failure go in `event_type` as a
   suffix (`password_change_failed`) or in `data_json` as a field
   (`{"outcome": "failed", ...}`)?
3. **Schema-of-data** — does `data_json` have a fixed shape per
   `event_type`, or is it a free-form bag?

Without an ADR, the answers drifted. Hex Audit SA-F7 surfaced two
specific drifts: (a) `audit_log_retention_run` encodes success +
empty-actionable-tick + non-empty-delete all under one
`event_type` with outcome encoded in `data.rows_deleted`, while
(b) the original `target_*` rows had different `data_json` shapes
per call site (target_id sometimes as a top-level column, sometimes
nested in data, sometimes both).

This ADR pins the conventions so the next call site doesn't reinvent
them.

## Decision

**Audit `event_type` strings follow `snake_case`, **`<noun>_<past-tense
verb>`** order, and outcome (success/failure/edge-case) lives in
`data_json` — NEVER in the `event_type` suffix. `data_json` is a
discretionary shape per `event_type` (no global schema), but every
event_type's shape is enumerated in this ADR so the catalogue is
greppable.**

### Naming rules

1. **`snake_case`**, lowercase. No `camelCase`, no `PascalCase`, no
   hyphens.
2. **`<noun>_<past-tense verb>`** order — the *thing* comes first,
   then *what happened to it*. `target_created`, not
   `created_target`. `passphrase_rotated`, not `rotation_completed`.
   For events that are themselves the thing (`app_started`,
   `audit_log_retention_run`), the noun-verb shape becomes
   noun-verb-as-one-word; the rule "thing first" is preserved.
3. **No outcome suffixes.** `password_changed` is correct;
   `password_change_succeeded` and `password_change_failed` are
   forbidden. Outcome goes in `data_json` as `{"outcome": "..."}`
   when there is a non-binary outcome, or as fields whose
   presence/value implies the outcome (`{"rows_deleted": 0}`).
4. **No verb-tense drift.** Past-tense for events that already
   happened; the only past-progressive exception is the 2-phase
   audit pattern (`passphrase_rotation_started` →
   `passphrase_rotated` / `passphrase_rotation_orphaned`) where
   the `_started` row is itself the durable evidence that the
   operation began.
5. **Stable across versions.** Once an `event_type` is shipped,
   renaming it is a breaking change to any operator who greps
   their audit log; treat the catalogue as part of the API
   surface.

### Outcome-encoding rule (binding)

The retention loop emits one `event_type` regardless of whether
the tick deleted 0 rows or 1000:

```python
audit.append(
    session,
    event_type="audit_log_retention_run",
    data={"rows_deleted": rows, "cutoff": cutoff.isoformat(),
          "retention_days": retention_days},
)
```

This is the **reference pattern**. Splitting into
`audit_log_retention_run_success` + `audit_log_retention_run_empty`
would have:

- Doubled the catalogue cardinality.
- Forced every consumer (operator grep, future analytics query,
  future dashboard widget) to know the suffix vocabulary.
- Made it impossible to compute "how many retention ticks have
  fired total" without OR-ing across N suffixes.

The same rule applies to: `password_changed` (no `_failed` sibling
— the 503 + `PASSWORD_CHANGE_RETRY` response IS the failure signal,
because slice 8's transactional contract means a failed audit row
means a failed business write); `target_deleted` (no
`_deleted_failed` — the row simply doesn't land if the txn rolls
back); `passphrase_rotated` (no `_failed` — the 2-phase
`_rotation_started` + `_rotation_orphaned` pair carries the
failure-mode signal explicitly).

### Catalogue (production v1.1.4)

Grouped by ADR-0007 audit-scope bucket. The `actor`, `target_id`,
and `at` columns are repository-level (not in `data_json`) and are
omitted from the per-event tables below.

#### Auth events

| event_type | data_json shape | Emitted from | Notes |
| --- | --- | --- | --- |
| `password_changed` | `{}` (no payload) | `app/api/auth.py::change_password` | actor = the user changing their own password |
| `password_rehashed` | `{}` | `app/api/auth.py::_rehash_admin_password` (BackgroundTask) | actor = `"system"` |
| `admin_password_set` | `{"source": "env", "last4": "...."}` | `app/db/schema_init.py` (fresh boot, env-supplied) | bootstrap path; last4 only, never plaintext |
| `admin_password_autogenerated` | `{"source": "autogen", "last4": "...."}` | `app/db/schema_init.py` (fresh boot, autogen) | bootstrap path |
| `admin_password_reset` | `{"source": "reset", "last4": "...."}` | `app/db/schema_init.py` (`RESTREAM_RESET_ADMIN_PASSWORD=1`) | one-shot env-triggered reset |
| `session_revoked` | `{"scope": "self" \| "all_for_user", "count": <int>}` | `app/api/security_api.py::revoke_session` / `revoke_all_for_user` | scope distinguishes "this session" vs "logout everywhere" |

#### Run lifecycle events

| event_type | data_json shape | Emitted from | Notes |
| --- | --- | --- | --- |
| `run_started` | `{"run_id": <uuid>, "target_count": <int>, "worker_count": <int>}` | `app/fanout/supervisor.py::_audit` (helper) | one row per cold start; ADR-0015 auto-run-on-publish means every start is publish-driven, so a separate `trigger` field is omitted |
| `run_stopped` | `{"reason": "publish_idle" \| "control_plane_crash" \| "normal" \| "error"}` | `app/fanout/supervisor.py::_audit` | one row per stop. `publish_idle` is the auto-stop on OBS publish-end (ADR-0015, no grace window); `normal` is graceful lifespan shutdown; `error` is the start-time spawn failure path; `control_plane_crash` is the lifespan-on-exception path |
| `interrupted_session_recovered` | `{"session_id": <int>, "estimated_end_at": <iso>}` | `app/db/schema_init.py` (boot-time recovery) | F# BA-D1; written if the prior `sessions_history` row had `ended_at IS NULL` |

#### Secret lifecycle events

| event_type | data_json shape | Emitted from | Notes |
| --- | --- | --- | --- |
| `target_created` | `{"label": "<str>", "type": "twitch" \| ...}` | `app/api/targets.py::create_target` | |
| `target_updated` | `{"diff": {<changed-fields>}}` | `app/api/targets.py::update_target` | stream key changes are NEVER in the diff — see `credential_set` |
| `target_deleted` | `{"target_id": <id>, "label": "<str>"}` | `app/api/targets.py::delete_target` | `target_id` is also in `data_json` (not just the FK column) because the FK is `ON DELETE SET NULL`; `list_by_target` queries both per FG3-F6 |
| `credential_set` | `{"last4": "...."}` | `app/api/targets.py::set_credential` | last4 only; the credential itself never appears |
| `credential_revealed` | `{"reveal_grant_id_fp": "<sha256-fp>"}` | `app/api/targets.py::reveal_credential` | fingerprint of the burned reprompt grant per ADR-0005 §"reprompt burn-on-failure" |
| `credential_cleared` | `{}` | `app/api/targets.py::clear_credential` | |
| `ingest_key_rotated` | `{"last4_new": "...."}` | `app/api/settings_api.py::rotate_ingest_key` | the *old* key is not logged — grace-window decisions are recorded in `settings.ingest_key_grace_until` |
| `ingest_key_revealed` | `{}` | `app/api/settings_api.py::reveal_ingest_key` | last4 deliberately omitted (it's available via the read endpoint) |
| `passphrase_rotation_started` | `{"credential_count": <int>}` | `app/api/security_api.py::rotate_passphrase` (inside rewrap txn) | 2-phase audit phase 1 |
| `passphrase_rotated` | `{"credential_count": <int>}` | `app/api/security_api.py::rotate_passphrase` (standalone, AFTER rename+key-swap succeed) | 2-phase audit phase 2 (success) |
| `passphrase_rotation_orphaned` | `{"credential_count": <int>, "rename_error": "<exc-class>"}` | `app/api/security_api.py::rotate_passphrase` (standalone, on rename OSError) | 2-phase audit phase 2 (failure); precedes the re-raise |
| `api_token_created` | `{"label": "<str>", "last4": "...."}` | `app/api/api_tokens_api.py::create_token` | |
| `api_token_revoked` | `{"label": "<str>"}` | `app/api/api_tokens_api.py::revoke_token` | |
| `vk_session_credential_cleared` | `{"target_id": <id>, "reason": "session_end" \| "operator"}` | `app/fanout/supervisor.py::_wipe_per_session_credentials` | one row per affected VK target |

#### Worker exceptional events

| event_type | data_json shape | Emitted from | Notes |
| --- | --- | --- | --- |
| `worker_failed_open` | `{"target_id": <id>, "failure_count_in_window": <int>}` | `app/fanout/supervisor.py::_audit` (helper) | circuit breaker tripped per ADR-0003 |
| `target_reset_by_user` | `{"target_id": <id>}` | `app/fanout/supervisor.py::_audit` (helper) | user-initiated "Retry now" on a `failed_open` target |

#### System maintenance lifecycle events

| event_type | data_json shape | Emitted from | Notes |
| --- | --- | --- | --- |
| `app_started` | `{"version": "<str>", "build_sha": "<str>"}` | `app/main.py` lifespan | one row per process start; emit failure is FATAL (no `contextlib.suppress`) per slice 8.5 FG3-F1; uses `append_standalone(checkpoint=False)` |
| `kdf_salt_previous_cleared` | `{}` | `app/main.py::_kdf_salt_previous_cleanup_loop` | 24 h post-rotation cleanup |
| `ingest_key_previous_cleared` | `{}` | `app/main.py::_ingest_key_previous_cleanup_loop` | grace-window expiry cleanup |
| `audit_log_retention_run` | `{"rows_deleted": <int>, "cutoff": <iso>, "retention_days": <int>}` | `app/main.py::_audit_log_retention_once` | only emitted on non-empty ticks (rows_deleted > 0); idle ticks emit `audit_log_retention_tick` at INFO instead (structured-log, not audit-row); marker rows are EXCLUDED from the retention DELETE predicate (eternal forensic record) |

### What is intentionally NOT audited

The exclusions from ADR-0007 §"Audit log scope" stand:

- Per-second status snapshots (WebSocket only).
- Successful publishes / disconnects from OBS (nginx access log).
- Login *attempts* that fail rate-limit pre-check (the
  rate-limiter is its own log channel; auditing every failed
  attempt would let an attacker DoS the audit log by hammering
  the login endpoint).
- WebSocket message-level events (the bus is for fan-out, not for
  forensics).

## Consequences

**Easier:**

- One ADR to grep for "what does my `audit.append(event_type=X)`
  buy me?" — answered by reading the catalogue table.
- The outcome-in-data rule means future analytics queries don't
  need to OR across suffixes.
- Naming consistency makes the catalogue mechanically extensible
  — a new feature adds one row, picks its noun-verb name, and the
  pattern is self-documenting.

**Harder:**

- Adding a new event_type requires touching THIS ADR (the
  catalogue) in addition to the code site. Mitigation: this is a
  feature, not a bug — surfaces the cardinality decision at the
  ADR layer where it belongs.
- The `data_json` shapes are discretionary, not enforced by a
  global schema. A future drift between an entry here and the
  actual production payload is possible. Mitigation: every
  `event_type` is grep-able, and the catalogue rows above name
  the file + function that emits each row, so a reviewer can
  re-derive the actual shape in ≤ 60 s.

## Rejected alternatives

- **A global Pydantic schema for `data_json` keyed by
  `event_type`.** Considered. Rejected because (a) it forces every
  call site to import a discriminated-union model; (b) it adds a
  migration burden every time we add a field; (c) the audit log
  is forensic, not transactional — losing a row's payload because
  it didn't validate would be worse than losing the shape
  contract. The catalogue above is the "shape ADR" without the
  runtime cost.
- **`event_type` suffix for outcome (`_failed` / `_success`).**
  Rejected per the outcome-encoding rule above. The catalogue
  cardinality would double; consumers would have to know the
  suffix vocabulary; no analytical query benefits.
- **Free-form `event_type` (no convention).** This is what we
  drifted to before this ADR. The cost is exactly what SA-F7
  surfaced: the next call site has to read the existing emit
  sites to infer the convention, and the convention drifts.
- **Per-bucket repository (one repo per audit-scope bucket).**
  Considered. Rejected because the buckets are a *taxonomy*, not
  a separation-of-concerns boundary — every bucket writes to the
  same table, observes the same retention loop, and shares the
  same dual-contract (`append` + `append_standalone`). Splitting
  the repo would force callers to import the right one per
  event_type, which the catalogue already documents at the ADR
  layer for free.

## Open questions / revisit triggers

- If a future operator-facing audit-log UI ships (greppable in the
  panel), the catalogue here becomes the source of truth for the
  filter chips ("show me all `passphrase_*` events"). Until then
  the audit log is read via `sqlite3 restream.db
  "SELECT * FROM audit_log..."` and grep.
- If we ever add a new audit-scope bucket beyond the 5 in ADR-0007,
  add a new section above; do not silently insert rows into an
  existing bucket.
- If a regulatory regime mandates a specific event_type vocabulary
  (e.g., RFC 5424-style structured syslog with SD-IDs), we'd add
  a translation layer at the read side, not retrofit the
  vocabulary here. The internal vocabulary is for our forensic
  needs; an export adapter is the right place to bridge.
