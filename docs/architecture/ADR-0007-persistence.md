# ADR-0007: Persistence — SQLite (WAL), single-shot schema, no migrations chain

## Status
Accepted — 2026-05-15

## Context

The control plane needs to persist:

- `users` (single admin row, password hash, created_at)
- `sessions` (HTTP session IDs, expiry, last_seen)
- `api_tokens` (HMAC fingerprints, label, last_used)
- `targets` (URL, type, label, enabled flag — see ADR-0009 for `Credential`)
- `credentials` (encrypted key material per target, lifetime metadata)
- `audit_log` (append-only event stream — see scope below)
- `sessions_history` (run sessions: start, end, per-target outcome — distinct from HTTP sessions)
- `settings` (singleton: idle timeout, log retention, ingest key bag, first-run flag)
- A KV-shaped `system` table for internal markers (kdf-salt-rotated-at, last-passphrase-rotation, schema_version).

Deployment is a single container with one writer (the control plane process).
Throughput is trivially small: a handful of writes per second during a live
run (audit + status snapshots only — runtime worker status is in-memory and
broadcast over WebSocket, never persisted).

This is the persistence-choice ADR that ADR-0001 and ADR-0006 were vague
about. Software Architect review F# SA-B flagged the gap.

## Decision

**SQLite via `aiosqlite` with SQLAlchemy 2.x async ORM. Single canonical
schema, no migrations chain (per Rule №2). WAL mode, `synchronous=NORMAL`.**

### Engine configuration

```python
# Pragmas set once at connect time. Documented here so they are the single
# source of truth — the `schema_init` module asserts them on every boot.
PRAGMAS = {
    "journal_mode": "WAL",        # writers don't block readers
    "synchronous": "NORMAL",      # crash-safe under WAL with one fsync per
                                  # checkpoint, not per write
    "foreign_keys": "ON",
    "busy_timeout": "5000",       # 5s — covers WAL checkpoint contention
    "temp_store": "MEMORY",
    "mmap_size": "67108864",      # 64 MiB
    "cache_size": "-32000",       # 32 MiB page cache
}
```

`synchronous=NORMAL` under WAL is durable across application crashes (the
WAL log is replayed). The only loss window is OS-level power loss between
the WAL fsync and a checkpoint. After slice 8 the worst-case checkpoint
lag is bounded by the `_audit_log_retention_loop` cadence (6 h on
non-empty deletes; at most 24 h on idle ticks). A power loss in that
window can lose up to one tick's worth of post-fsync writes that had not
yet checkpointed — durable under application crash, but the OS-power-loss
case is no longer "microseconds" as the original ADR-0007 text claimed.
See §Audit-log durability and transactional shape and §Consequences for
the trade.

**Audit-log durability and transactional shape (Hex Audit BA-F4, slice 8 — 2026-05-19):**
The original design ran `PRAGMA wal_checkpoint(TRUNCATE)` after EVERY
audit append from a dedicated session (`AuditLogRepository` constructed
with a `sessionmaker` factory). That created a forensic gap: the audit
row's session was distinct from the caller's business-write session,
so a request could commit business state (e.g., target deleted) while
the audit row's separate commit silently failed — leaving the
forensic record missing for events that ARE security-relevant.

The slice 8 refactor inverts this: `AuditLogRepository.append(session,
...)` is the canonical path; it adds the audit row to the caller's
`AsyncSession` BEFORE the caller commits. Business write and audit
row commit atomically (or roll back together if anything in the txn
raises) — in one physical transaction (a single SQLite write, one
`COMMIT`). The repository keeps a second method, `append_standalone(...)`,
reserved for paths that legitimately have no caller session: the
`app_started` lifespan emit and the supervisor's lifecycle helper
(`run_started`, `run_stopped`, `worker_failed_open`, etc.). Background
loops (kdf-salt and ingest-key cleanup) merge their audit row into the
same physical transaction as the underlying DB write.

**Why audit-of-audit and not a separate `audit_log_retention_runs`
table?** The retention marker is itself a forensic event ("at time
T, rows older than cutoff C were purged; N rows affected"). Splitting
it into a sibling table doubles the query surface for "what
happened in the audit subsystem" without changing the durability
story — both tables would share the same WAL and the same retention
ceiling. The signal-to-noise concern (audit-of-audit growing without
bound on low-traffic deployments) is addressed by excluding
`event_type='audit_log_retention_run'` from the retention DELETE
predicate (see §Audit log scope — System maintenance), not by
relocating the rows. One table, one query path, the marker rows are
eternal but tiny.

**2-phase audit for non-atomic operations.** Passphrase rotation
(`app/api/security_api.py`) is the reference case: the DB rewrap of
every ciphertext commits inside one `session.begin()` block, but the
on-disk `.kdf_salt` rename is a separate filesystem syscall that can
OSError after the DB commit succeeds. A single `passphrase_rotated`
audit row committed alongside the rewrap would *lie* on the orphan
path: the audit says "rotated" but the salt file is OLD, and the
next boot fails to derive the KEK against the NEW ciphertexts. The
2-phase pattern: emit `passphrase_rotation_started` INSIDE the
rewrap txn (atomic with the DB writes); emit `passphrase_rotated`
via `append_standalone(checkpoint=False)` AFTER the rename AND the
in-process key swap succeed; emit `passphrase_rotation_orphaned`
on the orphan path before re-raising. Any operation where the
audit-true outcome depends on a post-commit side effect should use
this pattern. The `_started` row is the durable evidence the
operation was attempted; the `_completed`/`_orphaned` row resolves
the outcome.

`PRAGMA wal_checkpoint(TRUNCATE)` no longer fires per audit append.
Instead, a single `_audit_log_retention_loop` ticks every 6 h, deletes
rows older than `settings.audit_log_retention_days` (default 365,
configurable via `RESTREAM_AUDIT_LOG_RETENTION_DAYS`, floor 30 days,
ceiling 3650 days, **environment-only for v1.1.4** — see "ADR drift
acknowledgement" below), emits an `audit_log_retention_run`
audit-of-the-audit row on non-empty ticks (in the same physical
transaction as the delete), and runs the TRUNCATE checkpoint afterwards.
Idle ticks (no rows past cutoff) checkpoint at most once per day to
bound WAL growth under read-heavy loads. The `append_standalone`
path defaults `checkpoint=True` for the lifespan startup case, but
the supervisor `_audit` helper and the lifespan `app_started` emit
both pass `checkpoint=False` so the hot path never revives the
per-event TRUNCATE that BA-F4 set out to remove. The retention loop
is the canonical WAL-hygiene actor; per-call checkpoints are reserved
for paths where the retention loop has not yet had a chance to run.

The retention loop's per-tick body is extracted into
`_audit_log_retention_once(...)` (testable in isolation) and tracks a
consecutive-failure counter; on the 5th consecutive failure
(~30 h of unsuccessful retention) the loop emits
`_logger.critical("audit_log_retention_stuck", ...)` so a degraded
WAL/disk surfaces as a loud structured-log event instead of one quiet
warning per 6 h forever. Every successful tick emits
`audit_log_retention_tick` at INFO with `rows_deleted` (may be 0),
`cutoff`, `retention_days`, and `checkpoint_ran` — the operator's
`journalctl | grep audit_log_retention_tick | tail -5` heartbeat that
distinguishes "young deployment, nothing past cutoff" from "loop is
dead/degraded".

**ADR drift acknowledgement (Hex Audit BA2-F7, slice 9):**
`audit_log_retention_days` lives in `Settings` (Pydantic env binding)
today, NOT in the `settings` SQLite row. The sibling
`log_retention_days` IS in the row. This is intentional drift for
v1.1.4: changing the audit-log retention floor is an operator decision
that should require a restart (the same way passphrase-source mode
does), not a hot-tunable from the UI where the next request could
trim the floor below the previous tick's deletion horizon. If a future
compliance request forces variable retention (HIPAA 6 y, PCI 1 y,
ISO 27001 variable), this ADR is amended in a later slice to migrate
the field into the `settings` row with env-var seeding on first boot
only.

Failure semantic: under the in-session contract, an audit-write
failure IS a business-txn failure. The caller's `await session.commit()`
raises, FastAPI's dependency teardown propagates, the global exception
handler returns 500. The client retries; the operation has not landed;
no forensic gap. No `contextlib.suppress(Exception)` wraps any audit
append in the production code path (the pattern was the bug).

### Schema management — single bring-up, no chained migrations

Per Rule №2 there is no Alembic. The schema lives in **one** file:

```
app/db/schema.sql
```

On first boot (`PRAGMA schema_version = 0` reads as 0):

1. Execute `schema.sql` in one transaction.
2. Set `PRAGMA user_version = <SCHEMA_VERSION_CONSTANT>`.
3. Insert seed rows: the singleton `settings` row, an `admin` user row.

On subsequent boots:

1. Read `PRAGMA user_version`.
2. If it equals the compiled-in `SCHEMA_VERSION_CONSTANT`: proceed.
3. If lower: **refuse to start with a clear error**. The user upgrades by
   running an export-then-reimport (see ADR-0006 backup/restore). We do
   not auto-migrate.

This is the strict reading of "no migration baggage". The cost is that
schema changes between versions require operator action; the benefit is
no Alembic clutter and zero ambiguity about target state. The user gets
a clean error, a documented procedure, and never a half-migrated
database.

### File layout

```
/data/restream.db          # main file
/data/restream.db-wal      # WAL log (managed by SQLite)
/data/restream.db-shm      # shared memory (managed by SQLite)
```

Mounted via the volume. Owned by the container's `restream` uid.

### Audit log scope

Per F# SA-E.audit, the audit log scope is trimmed from the initial
sprawl to the events that have forensic value:

- Auth events: login success/failure (with IP), session creation/
  revocation, password change, "log out everywhere".
- Run lifecycle: `run_started`, `run_stopped`, `run_interrupted_recovered`
  (per F# BA-D1 — see "crash recovery" below).
- Secret lifecycle: target stream key created/modified/cleared, master
  passphrase rotated, API token created/revoked.
- Worker exceptional events: target entered `failed_open` (circuit
  breaker tripped), target reset by user.

- System maintenance lifecycle: `audit_log_retention_run` (per non-
  empty purge tick; the marker row is *excluded* from the retention
  DELETE predicate via `AUDIT_LOG_RETENTION_EXCLUDED_EVENT_TYPES` so
  the forensic record of past purges is eternal but bounded by tick
  cadence at ~1460 rows/year), `kdf_salt_previous_cleared` (24 h
  post-rotation salt-file cleanup), `ingest_key_previous_cleared`
  (grace-window-expiry cleanup), `app_started` (lifespan boot row,
  one per process start). These rows are emitted by background loops
  and lifespan code, not by request handlers; they are not gated by
  any user action.

Events that are **not** audited (deliberately — F# SA-E.audit):

- Per-second status snapshots. Those live in WebSocket only.
- Successful publishes / disconnects from OBS. nginx access log has
  this if needed; we don't duplicate.

Note: every CRUD on targets / settings IS audited (`target_created`,
`target_updated`, `target_deleted`, `settings_updated`). The original
ADR text said otherwise; the actual production behavior, since v1.0.0,
has been to audit these — the slice 8 audit-doc closure of SA-F4
amends this discrepancy.

**Retention (Hex Audit FG2-M6, slice 8):** rows older than
`settings.audit_log_retention_days` (default 365 days, operator-
configurable via `RESTREAM_AUDIT_LOG_RETENTION_DAYS`) are purged by
`_audit_log_retention_loop` on a 6 h cadence. The loop emits an
`audit_log_retention_run` row on every non-empty tick (in the same
transaction as the delete, so the forensic record of *what was
purged* is preserved). Floor of 30 days enforced at config-load; an
operator who tries to set a smaller window fails fast.

### Crash recovery — interrupted sessions (F# BA-D1)

On boot, if the most recent `sessions_history` row has `ended_at IS NULL`:

1. Write a new `audit_log` row: `interrupted_session_recovered` with the
   prior session's id and an estimate of end time (last status snapshot
   timestamp, or `started_at` if none).
2. Patch the prior session row: `ended_at = estimate`,
   `end_reason = "control_plane_crash"`.
3. Do **not** auto-resume the run. The user explicitly clicks START again.

This makes "control plane crashed mid-stream" a single observable event
in the audit log, not a silent inconsistency.

## Consequences

**Easier:**
- Backup is `sqlite3 restream.db ".backup '/data/backups/restream-YYYY-MM-DD.db'"` — atomic, online, safe to run while the app is up. No pg_dump, no quiesce.
- Restore is `cp` plus a checksum compare. The whole `/data/` directory is the unit of backup.
- Schema is *literally one file* in version control. Diffing across versions is trivial.
- WAL means readers and writers don't block each other under our load.
- Audit-log retention is tunable per deployment via a single env var, with config-load fail-fast on out-of-bounds values.

**Harder:**
- Schema changes between versions are an export/import for the user. The README and `docs/ops/upgrade.md` (to write later) must spell this out, and the version-bump process for maintainers is "bump `SCHEMA_VERSION_CONSTANT`, write export+reimport notes."
- Single writer means we cannot fan out to multiple control-plane processes. Not in scope; if it ever is, ADR-0007 is replaced, not amended.
- Audit-write failures now propagate as 500/503 from the request handler (the in-session contract makes the audit row a part of the business txn). `change_password` returns 503 + `Retry-After: 5` + `ErrorCode.PASSWORD_CHANGE_RETRY` on persist failure so the SPA can disambiguate "try again" from a generic crash; other write paths surface as 500 by default and the operator must retry.
- The OS-power-loss durability window grew from "microseconds" to "up to one retention tick" (≤ 24 h on idle ticks, ≤ 6 h on busy ones). On a hard power cut between two checkpoints, post-fsync writes that had not yet checkpointed may be re-rolled when WAL replay runs on next boot. Acceptable for our threat model (single-tenant appliance, the audit row is the surviving evidence either way).

**Rejected alternatives:**

- **PostgreSQL** — overkill. Adds an operator dep (the user now runs Postgres), introduces a network surface, slows boot. The trade is wrong for a single-tenant appliance.
- **DuckDB** — analytical engine, not OLTP. Wrong tool.
- **JSON/TOML files on disk** — loses ACID, loses indexing, loses concurrent-read safety. Tempting for simplicity, wrong for correctness.
- **Sqlcipher** — see ADR-0006. We encrypt at the field level for secrets and leave the rest in cleartext.
- **Alembic-style migrations chain** — explicitly forbidden by Rule №2.

## Open questions / revisit triggers

- If the audit log ever needs structured query (e.g., "show me all
  failed_open events for target X over 30 days"), revisit whether
  storage shape is the right one. Today it's a normalized table with
  `event_type`, `actor`, `target_id?`, `at`, `data JSON`.
- If the single-writer model becomes a contention bottleneck (it won't
  at our write rate), revisit.
- **Dual-contract audit repo (slice 8, BA-F4):** the repository
  exposes `append(session, ...)` for the in-txn canonical path AND
  `append_standalone(...)` for the handful of paths that have no
  caller session. The standalone surface is intentionally narrow:
  lifespan `app_started`, supervisor `_audit` lifecycle helper
  (`run_started`/`run_stopped`/`worker_failed_open`), and the
  passphrase rotation 2-phase audit's post-rename row. If a future
  feature needs to grow this set past **3 net-new standalone call
  sites** without one of those being the canonical exception
  (lifespan / supervisor / post-side-effect amendment), the
  dual-contract has become a smell — revisit whether the standalone
  helper should be folded back into the canonical session-bound path
  via a thin "borrow a session" adapter.
