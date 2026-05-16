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
the WAL fsync and a checkpoint — at our write rate this is microseconds.
For the audit log (the only data with a "must not lose" property), we
issue an explicit `PRAGMA wal_checkpoint(TRUNCATE)` after every audit
write. That's expensive at high write rates; at our rate (≤ 1 write/s
typical) it is negligible.

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

Events that are **not** audited (deliberately — F# SA-E.audit):

- Every CRUD on targets / settings. The current state of the row is
  authoritative; an audit trail of every label edit is noise.
- Per-second status snapshots. Those live in WebSocket only.
- Successful publishes / disconnects from OBS. nginx access log has
  this if needed; we don't duplicate.

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

**Harder:**
- Schema changes between versions are an export/import for the user. The README and `docs/ops/upgrade.md` (to write later) must spell this out, and the version-bump process for maintainers is "bump `SCHEMA_VERSION_CONSTANT`, write export+reimport notes."
- Single writer means we cannot fan out to multiple control-plane processes. Not in scope; if it ever is, ADR-0007 is replaced, not amended.

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
