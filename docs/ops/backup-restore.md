---
applies-to: v1.0.0+
last-verified: 2026-05-17
audience: operator
---

# Backup and restore

How to take a recoverable backup of `/data` while the container is
running, and how to restore one.

## TL;DR

```bash
docker exec restream-plus sqlite3 /data/restream.db \
  ".backup '/data/backup-$(date -u +%Y%m%dT%H%M%SZ).db'"

docker cp restream-plus:/data/backup-*.db ./backups/
docker cp restream-plus:/data/.kdf_salt ./backups/
docker cp restream-plus:/data/.kdf_salt.previous ./backups/ 2>/dev/null || true
```

**A `cp -r /data` of a hot container is NOT a valid backup.** It
captures a half-written WAL and yields silent corruption on restore.
Always use `sqlite3 .backup`.

## When to use this

- Immediately after first boot: take a baseline and store it
  off-host.
- Before every upgrade (mandatory; see [upgrade.md](upgrade.md)).
- On a schedule: nightly is reasonable for a homelab; hourly if
  reconfiguring targets frequently.
- After every master-passphrase rotation: take a fresh full backup
  and retire older backups (see *Backup and rotation* below).

## Prerequisites

- The container is running.
- `docker exec` access (the entrypoint runs as root briefly; the
  control plane and nginx run as uid 10001; `sqlite3` is available in
  the image).
- Host filesystem with enough space (the encrypted DB is small; logs
  are excluded).
- An off-host destination for backups (cloud / NAS / external drive),
  preferably with at-rest encryption (see *Storage handling* below).

## What goes in a backup

| Path | Required? | Why |
| --- | --- | --- |
| The `sqlite3 .backup` output file (e.g., `backup-20260517T120000Z.db`) | YES | Consistent snapshot of the encrypted DB. Replaces `restream.db` + `.db-wal` + `.db-shm` on restore. |
| `/data/.kdf_salt` | YES | 16-byte salt. Argon2id derives the root key from `passphrase + salt`. No salt = no decryption. |
| `/data/.kdf_salt.previous` | If present | Lives ≤ 24 h after a passphrase rotation. Include so a same-window restore can fall back to the prior passphrase. |
| `/data/tls/` | If non-empty | Operator-supplied TLS certs (uncommon if a reverse proxy terminates TLS). |
| `/data/logs/` | **NO** | Operational data, sensitive (can contain ingest-key fragments per Phase 10 §I), and not needed for restoration. Exclude. |

The `.first-boot-password` file referenced in `.gitignore` is
precautionary; the codebase does not write it (S-14).

## The primitive — `sqlite3 .backup`

SQLite's online-backup API streams pages while honoring WAL semantics:
the control plane keeps running and the backup is consistent.

```bash
docker exec restream-plus sqlite3 /data/restream.db \
  ".backup '/data/backup-$(date -u +%Y%m%dT%H%M%SZ).db'"
```

Why not `cp -r /data`? With SQLite WAL, `restream.db`, `.db-wal`, and
`.db-shm` are three coupled files. A naïve `cp` captures them at
different instants and the restore replays a partial WAL against an
already-truncated main file. The DB looks fine for a few queries, then
silently misreads or fails to decrypt.

`VACUUM INTO '/path/file.db'` is a valid alternative; it produces a
defragmented copy but acquires a write lock for the duration
(a few hundred milliseconds at our scale). Use it if you also want
defrag; otherwise `.backup` is the default.

## Procedure — hot backup

```bash
# 1. Snapshot inside the container
TS=$(date -u +%Y%m%dT%H%M%SZ)
docker exec restream-plus sqlite3 /data/restream.db \
  ".backup '/data/backup-${TS}.db'"

# 2. Sanity-check the snapshot
docker exec restream-plus sqlite3 "/data/backup-${TS}.db" "PRAGMA integrity_check"
# Expected output: ok

# 3. Copy out the snapshot + salt(s).
#    `docker cp` does not honor the parent shell's umask; force-tighten
#    perms with `install -d -m 0700` for the directory and explicit
#    `chmod 0600` for each file (salts paired with the encrypted DB are
#    key material — guard them the same way you guard the DB).
install -d -m 0700 ./backups
docker cp "restream-plus:/data/backup-${TS}.db"  "./backups/backup-${TS}.db"
docker cp  restream-plus:/data/.kdf_salt          "./backups/kdf_salt.${TS}"
docker cp  restream-plus:/data/.kdf_salt.previous "./backups/kdf_salt.previous.${TS}" 2>/dev/null || true
chmod 0600 "./backups/backup-${TS}.db" "./backups/kdf_salt.${TS}"
[ -f "./backups/kdf_salt.previous.${TS}" ] && chmod 0600 "./backups/kdf_salt.previous.${TS}" || true

# 4. Remove the snapshot from inside the container
docker exec restream-plus rm "/data/backup-${TS}.db"

# 5. Encrypt the backup for off-host storage (recommended)
tar -cf - -C ./backups "backup-${TS}.db" "kdf_salt.${TS}" \
  | age -r <recipient-age-pubkey> \
  > "./backups/restream-${TS}.tar.age"
```

Replace `age` with `gpg --symmetric` / `borg create` / equivalent if
you prefer those.

## Procedure — cold backup (container stopped)

If you need a single tar of the full `/data` tree:

```bash
docker stop -t 30 restream-plus   # graceful; flushes WAL + writes audit row (S-8)

# /var/lib/docker/volumes/restream-data/_data is the host path for a
# named volume; substitute your bind-mount path if you use one.
sudo tar -cf - -C /var/lib/docker/volumes/restream-data/_data \
    --exclude=logs \
    . | age -r <pubkey> > backup-cold.tar.age

docker start restream-plus
```

The hot backup is preferred for routine use; cold backup is for
host-migration / disk-image scenarios where you also want the full
directory shape.

## Storage handling

The backup file is a **plaintext SQLite database**. It contains:

- Encrypted stream-key ciphertexts — useless without the master
  passphrase + matching salt.
- Session and API-token HMAC fingerprints — useless without the
  HMAC key (also derived from the passphrase).
- All non-secret rows in cleartext: target labels, target RTMP URLs
  (which may themselves embed sensitive endpoints), audit log, IPs,
  login timestamps, settings.

The backup is **partially sensitive on its own** even without the
passphrase: it leaks target metadata, login history, and audit
content. Therefore:

- **Backup files MUST be encrypted at rest off-host.** `age`,
  `gpg --symmetric`, `borg --encryption=repokey`, or equivalent.
  NEVER raw to a deduplicating cloud sync (S3 default, Backblaze B2
  without client-side encryption, Google Drive consumer accounts).
- **Backup files MUST be `chmod 600`** on the host filesystem
  (`umask 077` before the snapshot copy).
- **The master passphrase MUST be stored separately** from the
  backup — different keystore, different operator-memorized location.
  If both live in the same vault, one breach exposes everything.

## Backup and rotation — the gotcha

A backup taken at T-1 is encrypted with the master passphrase that
was active at T-1.

If at T you rotate the master passphrase, then within the 24-h grace
window (the `.kdf_salt.previous` lifetime), a restore can use either
passphrase, *provided* the matching salt file is in the restored
`/data`.

After T + 24 h the previous-salt file is removed. The T-1 backup, if
it does NOT include `.kdf_salt.previous`, becomes undecryptable
unless you also remember the old passphrase AND the original
`.kdf_salt` (which is what was current at T-1) is also in the backup.

> **WARNING.** A backup taken before a master-passphrase rotation can
> only be restored with the passphrase that was active when the backup
> was taken, using the salt that was active at the same time. If you
> rotate the passphrase and 24 hours pass, an older backup requires
> *both* the older passphrase AND the matching `.kdf_salt` (or
> `.kdf_salt.previous`) present in the backup.

Recommended practice: take a fresh full backup *immediately after*
every passphrase rotation. Retire older backups within the 24-h
grace window (or never delete them, but keep them paired with the
matching passphrase-of-record so you can always read them).

## Procedure — restore

The ordering is load-bearing. Do not reorder.

```bash
# 1. Stop the running container (-t 30 per S-8)
docker stop -t 30 restream-plus
docker rm restream-plus

# 2. Move the existing /data aside (rollback target if restore fails;
#    do NOT delete yet).
#
# Named-volume deployment: copy the current volume into a snapshot
# volume so it survives the swap.
ASIDE_TS=$(date -u +%Y%m%dT%H%M%SZ)
docker volume create "restream-data.broken-${ASIDE_TS}"
docker run --rm \
  -v "restream-data:/from:ro" \
  -v "restream-data.broken-${ASIDE_TS}:/to" \
  busybox sh -c "cp -a /from/. /to/"
docker volume rm restream-data
docker volume create restream-data
#
# Bind-mount deployment instead?
#   sudo mv /srv/restream "/srv/restream.broken-${ASIDE_TS}"
#   sudo install -d -m 0750 -o 10001 -g 10001 /srv/restream

# 3. Place the restored DB + salt(s) at the right paths.
#    Spin a one-shot helper container to do the copy + chown.
TS=20260517T120000Z   # whichever backup
docker run --rm -v restream-data:/data -v "$PWD/backups:/in:ro" busybox sh -c "
  set -e
  cp /in/backup-${TS}.db /data/restream.db
  cp /in/kdf_salt.${TS}  /data/.kdf_salt
  [ -f /in/kdf_salt.previous.${TS} ] && cp /in/kdf_salt.previous.${TS} /data/.kdf_salt.previous || true
  chown -R 10001:10001 /data
  chmod 0750 /data
  chmod 0640 /data/restream.db /data/.kdf_salt
  [ -f /data/.kdf_salt.previous ] && chmod 0640 /data/.kdf_salt.previous || true
"

# 4. Start the container with the passphrase that was active when the
#    backup was taken. If you have rotated, this may be the OLD passphrase.
docker run -d --name restream-plus --restart unless-stopped --stop-timeout 30 \
  --env-file /etc/restream/env \
  -p 127.0.0.1:1935:1935 -p 127.0.0.1:8000:8000 \
  -v restream-data:/data \
  ghcr.io/<OWNER>/restream-plus:vX.Y.Z

# 5. Watch logs for schema mismatch / decryption errors.
#    Tail with --since so you don't replay older content; restore-time
#    log lines can include target labels and RTMP URL fragments — treat
#    the output as sensitive. If you need to share for support, run it
#    through the redaction pipeline in maintenance.md first.
docker logs --since 1m -f restream-plus
# Healthy boot: 'schema_init fresh=False', no SchemaVersionMismatchError
```

If you see `SchemaVersionMismatchError`: the backup is from a
different `SCHEMA_VERSION` than the image you started. Use the same
image version that wrote the backup, or follow the export → reimport
procedure in [upgrade.md](upgrade.md).

## Verify

After restore, before relying on the data:

```bash
# 1. /readyz reports ok across the board
curl -fsS http://127.0.0.1:8000/readyz
# Expected: every "ok": true under "checks"

# 2. Schema version is what you expected
docker exec restream-plus sqlite3 /data/restream.db "PRAGMA user_version"
# Expected: matches the SCHEMA_VERSION of the binary

# 3. Row counts match the source (no plaintext exposure)
docker exec restream-plus sqlite3 /data/restream.db \
  "SELECT 'targets='||count(*) FROM targets UNION ALL
   SELECT 'credentials='||count(*) FROM credentials UNION ALL
   SELECT 'audit_log='||count(*) FROM audit_log;"
```

End-to-end: log in, confirm the targets list shows expected entries
with masked credentials (`●●●● 7F2A`), start a brief test stream,
and confirm fanout reaches at least one configured target. If the
fanout works, the encrypted credentials decrypted successfully — the
strongest non-revealing test for v1.

## Troubleshoot

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `sqlite3 .backup`: "database is locked" | A transaction is holding a long write lock | Retry; if persistent, restart the container (`-t 30`) and retry. |
| Restored DB returns 500 on every panel request | Wrong salt or wrong passphrase — credentials cannot decrypt | Check you restored `.kdf_salt` (and `.kdf_salt.previous` if applicable) from the *same* backup as the DB; check the passphrase matches what was active when the backup was taken. |
| `SchemaVersionMismatchError` on boot after restore | Backup is from a different `SCHEMA_VERSION` than the running image | Use the same image version that wrote the backup, or follow export → reimport in [upgrade.md](upgrade.md). |
| Restore succeeds, targets are present, but credentials show "decryption failed" | `/data/.kdf_salt` is from a different rotation epoch than the DB | Restore both files from the same backup snapshot. If you only have `.kdf_salt.previous` and not `.kdf_salt` from that time, you must use the matching older passphrase. |

## Log handling

The backup procedure excludes `/data/logs/` for size + sensitivity
reasons. If you DO include logs (e.g., for forensic export), treat
them as sensitive: `/data/logs/nginx-error.log` can contain ingest-key
fragments in upstream HTTP error context (Phase 10 §I).
**Do not share `/data/logs/` files publicly without redaction** (S-22).

## See also

- [deployment.md](deployment.md)
- [upgrade.md](upgrade.md)
- [maintenance.md](maintenance.md) — backup verification cadence.
- [ADR-0006](../architecture/ADR-0006-secret-encryption.md) — passphrase / KDF / AEAD.
- [ADR-0007](../architecture/ADR-0007-persistence.md) — schema and one-shot bring-up.
