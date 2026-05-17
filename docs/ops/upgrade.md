---
applies-to: v1.0.0+
last-verified: 2026-05-17
audience: operator
---

# Upgrade

How to move from version A to version B without losing data or
downtime beyond a controlled window.

## TL;DR

```bash
# 1. Read the release notes for the target version.
# 2. Take a backup (mandatory).
# 3. Cosign-verify the target image.
# 4. Pull. Stop with -t 30. Run new.
# 5. Verify /readyz. Smoke test.
# 6. Only then remove the old image.
```

If the release notes say `[schema-bump]`, you must go through
export → reimport (see *Schema-bump releases* below), not a direct
upgrade.

## When to use this

- A new `:vX.Y.Z` you've cosign-verified is published.
- You're rolling back to a previous version (see *Rollback* below).

This doc does NOT cover:
- First-time installation — see [deployment.md](deployment.md).
- Moving to a different fork's image — that's effectively a fresh
  deploy.

## Prerequisites

- A current backup, taken per [backup-restore.md](backup-restore.md).
- `cosign` 2.2+ on the host.
- Read the release notes — especially whether the release bumps
  `SCHEMA_VERSION`.

## The pre-upgrade checklist

In this exact order (S-21):

1. **Read the release notes** for the target version. Note any
   `[schema-bump]` annotation. Note breaking changes.
2. **Take a backup.** See [backup-restore.md](backup-restore.md).
   Confirm `PRAGMA integrity_check` returns `ok` and the snapshot
   file size > 0.
3. **Cosign-verify the target image** (below).
4. **`docker pull` the verified image.**
5. **`docker stop -t 30`** the running container (compose:
   `docker compose stop` with `stop_grace_period: 30s` already
   set per [compose.yaml](compose.yaml)).
6. **`docker rm`** the old container.
7. **`docker run`** the new image with the same `/data` volume and
   same `--env-file`.
8. **`curl /readyz`** until all five sub-checks return `ok: true`.
9. **Smoke test:** log in, start a brief test stream, confirm
   fanout reaches at least one configured target.
10. **Only then** `docker image rm` the old image.

`docker stop -t 30` is non-negotiable. Default `docker stop` (10 s)
SIGKILLs mid-publish — corrupted segment on the platform, orphan
audit row with `ended_at IS NULL`, audit gap. Phase 10 I-D4 (S-8).

## Verify the image before pulling

The cosign verify command is byte-identical with the same command in
[deployment.md](deployment.md) and
[release-checklist.md](release-checklist.md) (S-9):

```bash
cosign verify \
  --certificate-identity-regexp "^https://github\.com/<OWNER>/Restream_Plus/\.github/workflows/release\.yml@refs/tags/v[0-9.]+$" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/<OWNER>/restream-plus:vX.Y.Z
```

Both `--certificate-identity-regexp` and `--certificate-oidc-issuer`
are required. A `cosign verify` without one of these is no security
at all (it would accept any keyless signature in Rekor).

If verification fails, STOP. Do not pull. Do not retry with
`--insecure-skip-verify`. See [deployment.md](deployment.md)
*Verify the image before you run it* for the failure-path actions.

## Upgrade — direct (same `SCHEMA_VERSION`)

For minor / patch releases where the release notes do NOT carry
`[schema-bump]`:

```bash
# 2. Backup (per backup-restore.md). Required.

# 3. Cosign-verify.

# 4. Pull
docker pull ghcr.io/<OWNER>/restream-plus:vX.Y.Z

# 5. Stop old (-t 30 — S-8)
docker stop -t 30 restream-plus
docker rm restream-plus

# 6. Run new
docker run -d --name restream-plus --restart unless-stopped --stop-timeout 30 \
  --env-file /etc/restream/env \
  -p 127.0.0.1:1935:1935 -p 127.0.0.1:8000:8000 \
  -v restream-data:/data \
  ghcr.io/<OWNER>/restream-plus:vX.Y.Z

# 7. Wait for /readyz
until curl -fsS http://127.0.0.1:8000/readyz >/dev/null; do sleep 2; done
echo "ready"

# 8. Verify schema version
docker exec restream-plus sqlite3 /data/restream.db "PRAGMA user_version"
# Expected: matches the new image's SCHEMA_VERSION (per release notes)

# 9. Smoke test in the panel.

# 10. Only after smoke passes:
docker image rm ghcr.io/<OWNER>/restream-plus:vA.B.C   # previous version
```

## Upgrade — schema-bump releases (`[schema-bump]`)

Restream_Plus does NOT auto-migrate (ADR-0007, Rule №2). For releases
that bump `SCHEMA_VERSION`, the upgrade path is **export → reimport**.

The new image's `_sync_bootstrap_schema` reads `PRAGMA user_version`;
if it does not match the binary's compiled-in `SCHEMA_VERSION`, the
container fails to start with `SchemaVersionMismatchError`. This is
true in BOTH directions — newer DB on older binary AND older DB on
newer binary both refuse to boot (S-20).

Procedure (the release notes for a schema-bump release must include
the exact equivalent of this procedure tailored to that release):

1. Take a backup (mandatory).
2. With the **OLD** image still running, use the panel's
   **Settings → Export** flow to download an encrypted bundle.
   Store the bundle off-host alongside the backup.
3. `docker stop -t 30` the old container.
4. Move `/data` aside (rename the named volume, or `mv` the bind
   path) — keep it as the rollback target.
5. Create a fresh, empty data volume.
6. Start the **NEW** image with the fresh `/data` and the same
   `RESTREAM_MASTER_PASSPHRASE`. The fresh-boot path runs
   `schema.sql` at the new version.
7. Open the panel; first-boot admin bootstrap applies as it did on
   day one (ADR-0005 §"Bootstrap"). Either set
   `RESTREAM_ADMIN_PASSWORD` for the new container or let it
   autogenerate and capture the password from
   `docker logs restream-plus 2>&1 | grep -A 8 'FIRST BOOT'`.
8. Use the panel's **Settings → Import** to load the bundle into the
   new DB.
9. Verify targets, credentials, and audit history all appear.
10. Take a fresh backup of the new `/data`.
11. After confidence: discard the aside `/data` and the old image.

## Verify

After upgrade (any path):

```bash
# Container is healthy
docker inspect --format '{{.State.Health.Status}}' restream-plus
# Expected: healthy   (may take up to 90 s after start)

# Schema version matches expected
docker exec restream-plus sqlite3 /data/restream.db "PRAGMA user_version"
# Expected: per release notes

# Version reported by the API matches what you pulled
curl -fsS http://127.0.0.1:8000/api/about | head -c 200
# Expected: "version":"X.Y.Z" matches your pull tag
```

## Rollback

The rule (S-20):

- **Same `SCHEMA_VERSION` as the version you rolled forward from:**
  `docker stop -t 30 new && docker run old` works. The same `/data`
  is readable by both images.
- **Different `SCHEMA_VERSION`:** rollback by image-swap alone will
  fail (`SchemaVersionMismatchError`). The only path is
  **restore from the pre-upgrade backup** per
  [backup-restore.md](backup-restore.md), then run the old image.

This is why pre-upgrade backups are non-negotiable: rollback across a
schema bump has no other path.

## Tag pinning policy

| For | Tag form | Notes |
| --- | --- | --- |
| Production | `:vX.Y.Z` | Immutable per PA-19, signed, reproducible. |
| Auto-patch staging | `:vX.Y` | Moves on patch releases. PA-19 protects only `:vX.Y.Z`. |
| Bleeding edge / dev | `:edge` | Tracks `main`. NEVER production. |
| "Current stable" | `:latest` | Moves on release tag, NOT main HEAD. Silent upgrades on next `docker pull`. Not recommended for production (S-24). |

Pin production to `:vX.Y.Z` (by digest if you also want
attacker-resistance against registry-level tag substitution).

## Troubleshoot

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `SchemaVersionMismatchError` on boot of the new image | Release bumps `SCHEMA_VERSION`; you did a direct upgrade instead of export → reimport | Stop the new image. Roll back to the old image. Then follow *Schema-bump releases* above. |
| `SchemaVersionMismatchError` on boot of the old image after a "quick rollback" | New image already migrated `PRAGMA user_version` on a prior boot | Restore the pre-upgrade backup (the one you took at step 2), then run the old image. |
| `docker pull` succeeds but `cosign verify` fails | OIDC issuer changed, certificate identity drift, or compromised image | STOP. Do not deploy. Re-read [deployment.md](deployment.md) *Verify the image before you run it* and file an issue. |
| `/readyz` stuck at 503 after upgrade | Paste-mode container needs interactive unlock, OR a sub-check is failing | `curl /readyz` and read the `checks{}` body — see [maintenance.md](maintenance.md) on-call playbook. |
| Audit log shows `ended_at IS NULL` rows from the upgrade window | `docker stop` was run without `-t 30` | Cannot recover the lost session-end audit; in future use `-t 30` or `docker compose stop` with `stop_grace_period: 30s`. (S-8.) |

## See also

- [deployment.md](deployment.md)
- [backup-restore.md](backup-restore.md)
- [release-checklist.md](release-checklist.md)
- [ADR-0007](../architecture/ADR-0007-persistence.md) — schema model.
- [ADR-0010](../architecture/ADR-0010-headless-restart.md) — passphrase availability across restarts.
