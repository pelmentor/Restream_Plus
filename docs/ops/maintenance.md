---
applies-to: v1.0.0+
last-verified: 2026-05-18
audience: operator
---

# Maintenance

What recurring tasks Restream_Plus needs so it doesn't slowly rot.

## TL;DR

| Cadence | Action |
| --- | --- |
| Weekly | `/readyz` green; tail `audit_log` for surprises; check `nginx-error.log` size. |
| Monthly | `PRAGMA integrity_check` on the DB; verify backup restore on a throwaway dir; review GHCR for new releases. |
| Quarterly | Re-verify `platforms-reference.md` ingest URLs; verify GHCR retention policy; review API tokens. |
| Per-release | Read release notes; follow [upgrade.md](upgrade.md). |
| Per-incident | Rotate master passphrase if exposure is suspected; rotate admin password. |

## When to use this

- You have Restream_Plus deployed and running.
- You want a calendar of operator actions so nothing rots silently.

## Prerequisites

- Working deployment per [deployment.md](deployment.md).
- Backup procedure per [backup-restore.md](backup-restore.md).

## Weekly

### `/readyz` is green

```bash
curl -fsS http://127.0.0.1:8000/readyz | head -c 400
# Expected: every "ok": true under "checks"
```

Failures don't always page; a quick weekly check catches creeping
degradation (disk fill, slow DB, supervisor stuck).

### Audit-log scan

```bash
docker exec restream-plus sqlite3 /data/restream.db \
  "SELECT at, event_type, actor FROM audit_log
   WHERE event_type IN ('login_failure','master_passphrase_rotated','api_token_created','admin_password_changed')
   ORDER BY at DESC LIMIT 50;"
```

Anything you didn't do is the prompt to investigate.

### Log size check

```bash
docker exec restream-plus sh -c "du -h /data/logs/* 2>/dev/null | tail -20"
```

`/data/logs/nginx-error.log` grows fastest under abusive scans on
`:1935` (if exposed beyond loopback). If it crosses a few hundred MB,
rotate (see *Log rotation* below).

## Monthly

### DB integrity

```bash
docker exec restream-plus sqlite3 /data/restream.db "PRAGMA integrity_check"
# Expected: ok
```

Anything other than `ok` is a corrupted DB — restore from backup
([backup-restore.md](backup-restore.md)) before you lose more.

### Backup verification

Take a backup, copy it to a throwaway dir, and check it opens. The
`sqlite3` binary is not in `busybox`; reuse the running container's
copy via `docker exec` (the simplest path), or pull a dedicated
sqlite image.

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
docker exec restream-plus sqlite3 /data/restream.db \
  ".backup '/data/verify-${TS}.db'"

# Open the snapshot read-only (still inside the container) and confirm.
docker exec restream-plus sqlite3 "/data/verify-${TS}.db" "PRAGMA integrity_check"
# Expected: ok

# Clean up
docker exec restream-plus rm "/data/verify-${TS}.db"
```

For a true off-container verification, copy the snapshot out and run
your host's `sqlite3` against it (`sqlite3 ./verify.db "PRAGMA integrity_check"`).

Untested backups are not backups.

### Review GHCR for new releases

[List the tags](https://github.com/<OWNER>/Restream_Plus/pkgs/container/restream-plus)
on the package page. For any non-trivial bump (minor+), cosign-verify
and follow [upgrade.md](upgrade.md). For trivial patches, follow the
release-notes guidance.

### Prune per-target worker logs

```bash
# Default retention is 14 days (settings.log_retention_days); the
# panel exposes a knob under Settings → Logs.
docker exec restream-plus sh -c "find /data/logs/target-*/ -type f -mtime +14 -delete 2>/dev/null"
```

## Quarterly

### Platform-URL drift check

Open [`docs/architecture/platforms-reference.md`](../architecture/platforms-reference.md)
and verify each platform's RTMP endpoint is still current:

- **Twitch:** the [ingest endpoints](https://help.twitch.tv/s/twitch-ingest-recommendation) list.
- **YouTube Live:** the [Live Control Room](https://www.youtube.com/livestreaming/dashboard) stream URL.
- **Kick:** the streamer dashboard.
- **VK Video Live:** the streamer dashboard. VK is the most volatile;
  expect at least one endpoint change per year.

If a platform changed, update `platforms-reference.md` in a PR and
notify operators in the next release notes.

### Verify GHCR retention policy

Per [ghcr-retention.md](ghcr-retention.md) — the policy is "untagged
> 30 days deleted; tagged never auto-deleted" (PA-31). Run the
verification snippet from that doc.

### Review API tokens

```bash
docker exec restream-plus sqlite3 /data/restream.db \
  "SELECT id, label, created_at, last_used_at FROM api_tokens
   WHERE revoked_at IS NULL ORDER BY last_used_at DESC NULLS LAST;"
```

Revoke any token you no longer recognize via the panel
(**Settings → API tokens**).

**Never `SELECT *` on `credentials` or `api_tokens`.** Those tables
hold `ciphertext_blob` / `token_hmac` columns that are designed to
stay inside the process. Always project only the non-secret columns
you actually need (`id`, `label`, `created_at`, etc.).

## Per-release

Read the release notes for every new version. Note any `[schema-bump]`
annotation. Follow [upgrade.md](upgrade.md).

## Per-incident

### Rotate master passphrase

Use the panel: **Settings → Security → Rotate master passphrase**.

Procedure (the UI walks you through this; surface notes for the
operator):

1. The panel refuses rotation unless `RunState` is `offline`. Click
   **STOP** on the dashboard first.
2. The panel re-prompts your *current* admin password.
3. The panel rotates `.kdf_salt` (the old one becomes
   `.kdf_salt.previous`, kept for 24 h grace), re-wraps every
   encrypted credential with the new key, and revokes every HTTP
   session + API token.
4. **You must now update the host's env file with the new passphrase
   BEFORE the next container restart.** Otherwise the entrypoint
   fail-fast refuses to start.
5. Within 24 h, take a fresh backup of `/data`. Older backups are
   keyed to the OLD passphrase + `.kdf_salt.previous` and will become
   unreadable once the grace file is cleaned up.

The API equivalent (`POST /api/security/rotate-passphrase`) is for
automation only — it has a 1.0 s timing floor per call, and rotation
is a deliberate human action, not a script.

### Rotate admin password

Two paths:

- **In-panel** (preferred): **Settings → Change password**. The flow
  re-prompts the old password, sets the new one, revokes all
  existing sessions.
- **Recovery** (lost password): restart the container with
  `RESTREAM_RESET_ADMIN_PASSWORD=1` and
  `RESTREAM_ADMIN_PASSWORD=<new-12+-char>` in the env file. Next boot
  resets the admin row and revokes all sessions.

### Rotate a per-target stream key

Two-step operation:

1. **At the platform first** (Twitch dashboard / YouTube Studio /
   Kick / VK Video Live). Generate a new stream key. The platform-
   side rotation is what actually invalidates the leaked key.
2. **In the panel** (**Settings → Targets → Edit** the affected
   target). Paste the new key. The panel re-encrypts it under the
   current master passphrase.

Updating the panel only stops *us* sending to the old destination;
without step 1, the leaked key is still live.

### Rotate the OBS ingest key

(Different concept from per-target stream keys — this is the key
OBS uses to PUBLISH to Restream_Plus.)

**Settings → General → Rotate ingest key.** The panel writes a new
key and keeps the previous one accepted for a short grace window
(`ingest_key_grace_until`) so a stream in progress isn't cut. Update
OBS with the new key during the grace window; after the window
closes, OBS reconfigured with the old key will be rejected at
`on_publish`.

## "I'm locked out of my own panel"

The login form has in-memory rate limiting (ADR-0005). If you trip
it accidentally (mistyped password a few times), the lockout state is
in process memory — restart the container to reset it. Use a
graceful stop (S-8) rather than `docker restart` so the existing
session gets its 30-second drain:

```bash
docker stop -t 30 restream-plus && docker start restream-plus
```

This is documented here so you stop opening tickets for it (BA-20).

## Log rotation

`/data/logs/` is owned by the container (uid 10001). The image does
NOT install logrotate; you run rotation from the host or via a
sidecar.

Simplest: a host-side script (cron + run as the operator) that
restarts the container so nginx re-opens its log file:

```bash
# Run when logs exceed N MB; e.g., via systemd timer
sudo find /var/lib/docker/volumes/restream-data/_data/logs \
  -name 'nginx-*.log' -size +200M -exec mv {} {}.$(date +%Y%m%d) \;

# Tell nginx to reopen logs (cleanest: just bounce the container).
docker stop -t 30 restream-plus && docker start restream-plus
```

If you instead use a centralized log shipper (Vector / Fluent Bit /
Promtail), keep the same redaction discipline as below — `nginx-error.log`
can carry ingest-key fragments and should be filtered before egress.

## Log handling for support

`/data/logs/nginx-error.log` MUST be assumed to contain ingest-key
fragments in upstream HTTP error context (Phase 10 §I).
**Do not paste `/data/logs/` files into public issues, chat threads,
or support forms** (S-22).

For support, share filtered `docker logs` instead. The sed pipeline
below catches the obvious key-shaped tokens; it is NOT a guarantee
of completeness. Always re-read the output by hand before sharing —
the patterns below evolve and a missed shape can leak a key:

```bash
docker logs --since 5m restream-plus 2>&1 \
  | sed -E 's|(/live/)[A-Za-z0-9_-]+|\1<redacted>|g' \
  | sed -E 's|(rtmps?://[^ ]+/)[A-Za-z0-9_-]+|\1<redacted>|g' \
  | sed -E 's|([?&](key|token|stream_key|name)=)[A-Za-z0-9_.-]+|\1<redacted>|gI' \
  | sed -E 's|(Authorization:\s*Bearer\s+)[A-Za-z0-9_.-]+|\1<redacted>|gI' \
  | sed -E 's|[A-Za-z0-9_-]{32,}|<redacted-32+ token>|g' \
  > support-bundle.txt
```

The last line is intentionally broad (any base64url-shaped run of 32+
chars). It will redact legitimate hashes / commit SHAs in the log;
that's acceptable — better to over-redact than to leak. After running
the pipeline, open `support-bundle.txt` in a pager and scan for any
remaining tokens, ingest URLs, target labels you don't want public,
or anything that looks like it could be a secret (S-23). When in
doubt, do not attach the file.

## Verify

After any maintenance task that touches state:

```bash
# 1. /readyz is green
curl -fsS http://127.0.0.1:8000/readyz | head -c 400

# 2. Last backup is recent
ls -lt ./backups/*.db | head -3

# 3. No SchemaVersionMismatchError in recent logs
docker logs --since 1h restream-plus 2>&1 | grep -i 'schema' || echo "ok: no schema errors"
```

## Troubleshoot

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `/readyz` shows `db.ok=false` | Disk full or WAL corruption | `df -h` on host. If disk full, free space. If corruption, restore from backup. |
| `/readyz` shows `rtmp_ingest.ok=false` | nginx died inside container | s6 should auto-restart; check `docker exec restream-plus s6-svstat /run/s6-rc/servicedirs/nginx`. If not, restart container (`-t 30`). |
| `/readyz` shows `supervisor.ok=false` | Event-loop wedged | `docker logs --tail 200 restream-plus`. Capture logs, then restart container. File an issue. |
| Login locked out after typos | In-memory rate limit | Restart container (`-t 30`). |
| Stream silently fails to one platform | Platform changed ingest URL since last quarterly check | Re-verify per quarterly cadence above; update target URL. |

## See also

- [deployment.md](deployment.md)
- [backup-restore.md](backup-restore.md)
- [upgrade.md](upgrade.md)
- [ghcr-retention.md](ghcr-retention.md)
- [ADR-0011](../architecture/ADR-0011-health-liveness.md) — health endpoints.
- [phase-10-design-memo.md §I](../architecture/phase-10-design-memo.md) — nginx log redaction.
