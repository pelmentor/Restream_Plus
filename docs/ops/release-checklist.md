---
applies-to: v1.0.0+
last-verified: 2026-05-17
audience: maintainer
---

# Release checklist

For the maintainer cutting a `vX.Y.Z` release. Run straight down the
list; nothing here is optional unless the line says so.

## TL;DR

Pre-tag → tag-push → post-tag verify → publish release notes. CI does
the build, sign, and push; you do the things CI can't.

## When to use this

You are about to cut a release. You have `main` at the commit you
intend to ship.

## Prerequisites

- You are the repo admin / package owner.
- Local `gh` CLI is authenticated.
- Local `cosign` 2.2+ is installed.
- Local Docker is installed (for the post-publish smoke).
- Branch protection per [branch-protection.md](branch-protection.md)
  is already in place (verify in step 8 below).
- GHCR retention per [ghcr-retention.md](ghcr-retention.md) is
  already in place (verify in step 9 below).

## Pre-tag

### 1. main is green at HEAD

```bash
gh run list --branch main --workflow ci.yml --limit 1
# Expected: latest run is "completed", conclusion "success"
```

If CI is red, fix CI before tagging. Do NOT push the tag through a
red main. Do NOT use admin bypass to merge (BA-26).

### 2. Lockfiles are fresh

`gh run list` returns the overall run conclusion; inspect the
individual job via `gh run view`:

```bash
RUN_ID=$(gh run list --workflow ci.yml --branch main --limit 1 \
    --json databaseId --jq '.[0].databaseId')
gh run view "$RUN_ID" --json jobs \
  --jq '.jobs[] | select(.name=="backend-lockfile") | .conclusion'
# Expected: "success"
```

The `backend-lockfile` job catches drift between `pyproject.toml` and
`docker/requirements*.hashes.lock` (PA-30 required check).

### 3. Spot-check `platforms-reference.md`

Open [`docs/architecture/platforms-reference.md`](../architecture/platforms-reference.md)
and confirm each platform's ingest URL is still current. Same check
[maintenance.md](maintenance.md) puts on a quarterly cadence — a
release is a natural trigger for a re-verify.

If any URL changed, land the fix in a PR before tagging.

### 4. Draft the release notes

Include:

- Highlights (operator-relevant changes).
- **`[schema-bump]`** annotation if this release bumps
  `SCHEMA_VERSION` (it bumps the constant in
  [app/db/schema_init.py](../../app/db/schema_init.py)). Schema bumps
  REQUIRE an export → reimport entry added to
  [upgrade.md](upgrade.md) in the same release commit (S-22 / BA-22).
- Cosign version tested with this release.
- Any breaking changes (env-var rename, port change, etc.).
- Migration guidance.

### 5. Update `last-verified:` in ops docs

Edit the front-matter in every ops doc affected by this release. At
minimum touch the date on:

- [deployment.md](deployment.md)
- [backup-restore.md](backup-restore.md)
- [upgrade.md](upgrade.md)
- [maintenance.md](maintenance.md)
- [branch-protection.md](branch-protection.md)
- [ghcr-retention.md](ghcr-retention.md)

(release-checklist.md itself updates as part of this step. S-28.)

This is a *manual* edit by design (S-30 forbids auto-generated docs);
the release workflow does not touch `docs/ops/`.

### 6. Open a release-prep PR

Includes: release-notes draft (if you stage them in-repo for review),
`last-verified:` bumps, any `[schema-bump]` follow-through edits in
upgrade.md. Land via the normal PR flow with the 5 required checks
green.

## Tag-push

### 7. Push the tag

```bash
git tag v<X.Y.Z>
git push origin v<X.Y.Z>
```

Tag triggers `.github/workflows/release.yml`. PA-19 enforces tag
immutability: re-cutting `vX.Y.Z` fails the publish; the only path is
the next patch.

`release.yml` runs: preflight (cosign-verify the tag is unsigned-new) →
build amd64 + arm64 (native runners) → grype HIGH+ fixable blocks →
smoke (runtime amd64) → merge manifests → cosign keyless OIDC sign →
publish up to five tags from this workflow: `:vX.Y.Z`, `:vX.Y`,
`:vX` (omitted for `v0.*`), `:latest` (only for non-v0 non-pre-release
tags per PA-20), and `:sha-<short>`. The `:edge` tag is published
separately by `build-image.yml` on every main push, not by
`release.yml`.

## Post-tag verify

### 8. Confirm `release.yml` succeeded

```bash
gh run list --workflow release.yml --limit 1
# Expected: latest run completed, conclusion success
```

If `release.yml` failed: do NOT announce the release. Investigate the
failed job. Common causes: cosign signing dependency, GHCR rate-limit,
grype blocking. The tag itself remains immutable; you may need to cut
`v<X.Y.(Z+1)>` and roll forward.

### 9. Verify branch protection still in force

Per [branch-protection.md](branch-protection.md):

```bash
gh api repos/<OWNER>/Restream_Plus/branches/main/protection \
  --jq '{
    required_checks: .required_status_checks.contexts,
    enforce_admins: .enforce_admins.enabled
  }'
# Expected: required_checks contains all 5 PA-30 names;
#           enforce_admins is true.
```

If drift: re-apply settings per [branch-protection.md](branch-protection.md).

### 10. Verify GHCR retention policy

Per [ghcr-retention.md](ghcr-retention.md):

```bash
gh api -H "Accept: application/vnd.github+json" \
   /users/<OWNER>/packages/container/restream-plus/versions \
   --jq '[.[] | select(.metadata.container.tags | length == 0) |
          {id, updated_at}] | sort_by(.updated_at) | first'
# Expected: oldest untagged version is < 30 days old
# (if there are no untagged versions, output is `null`)
```

### 11. Cosign-verify the just-published image **against your local clock**

The signing job inside CI succeeds even if your local clock or
trust-store is misconfigured. Verifying locally catches OIDC /
Fulcio cert / Rekor inclusion issues that would block operators.

The cosign verify command is byte-identical with the same command in
[deployment.md](deployment.md) and [upgrade.md](upgrade.md) (S-9):

```bash
cosign verify \
  --certificate-identity-regexp "^https://github\.com/<OWNER>/Restream_Plus/\.github/workflows/release\.yml@refs/tags/v[0-9.]+$" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/<OWNER>/restream-plus:vX.Y.Z
```

(Substitute the tag you just pushed for `vX.Y.Z`.)

If verify fails locally: do NOT announce. Capture the error output
and investigate. Most likely a cosign-version mismatch or a clock
issue on your workstation; reconfirm against a fresh `cosign` from
the official release.

### 12. Real-RTMP smoke (CI verifies boot; humans verify the data path)

On a scratch host (or your dev box), spin up the just-published image
using [`docs/ops/compose.yaml`](compose.yaml) with the new tag.
Configure one target. Publish via OBS (preferred — OBS reads the key
from a non-echoing field) or ffmpeg.

ffmpeg path: read the ingest key into an unexported shell variable
via `read -rs` so it does NOT land in shell history or process argv:

```bash
read -rs -p 'Ingest key (input hidden): ' INGEST_KEY; echo
ffmpeg -re -i sample.mp4 -c copy -f flv \
  "rtmp://127.0.0.1:1935/live/${INGEST_KEY}"
unset INGEST_KEY
```

The URL still appears in `ps`-visible argv for the lifetime of
ffmpeg; this is unavoidable with ffmpeg's CLI. **Rotate the ingest
key after the smoke** if the workstation is not trusted with that
key long-term: in the panel, **Settings → General → Rotate ingest
key**.

Confirm in the panel that the target reaches `live`. Stop the
publish, click STOP in the panel, confirm RunState returns to
`offline`.

If the smoke fails: do NOT announce. The image boots but the data
path is broken — file an issue and roll forward with a patch.

### 13. Publish the release on GitHub

Before the publish, **re-read the release-notes file on disk** to
confirm it is the one you reviewed. The `--notes-file` flag will
publish whatever bytes the file holds; an editor / tool / clipboard
that pasted unexpected content (e.g., "for support, paste your env")
would ship to the world otherwise. Suggested:

```bash
# Inspect the file you're about to publish
shasum -a 256 release-notes-vX.Y.Z.md
less release-notes-vX.Y.Z.md
# Confirm the sha matches the version you reviewed in the PR.

gh release create v<X.Y.Z> --title "v<X.Y.Z>" --notes-file release-notes-vX.Y.Z.md
```

If the release-notes file is also committed to the repo (recommended
when the PR review is the trust boundary), prefer:

```bash
gh release create v<X.Y.Z> --title "v<X.Y.Z>" \
  --notes-file docs/releases/v<X.Y.Z>.md
```

— so the file's provenance is `git log` rather than your workstation.
Add `release-notes-*.md` (the workstation drafts) to `.gitignore`
in your local config to keep transient drafts out of accidental
`git add -A` commits.

If this release bumps `SCHEMA_VERSION`, the release body MUST carry
`[schema-bump]` in the title or the first line (S-22 / BA-22).

The release body is the canonical changelog (per
[phase-12-design-memo.md §I.4](../architecture/phase-12-design-memo.md)).
No tracked `RELEASES.md` / `CHANGELOG.md` is maintained for v1.

### 14. Announce

Link to the GitHub release. Tell operators the recommended pin (always
`:vX.Y.Z`), the cosign verify command, and any `[schema-bump]`
implications.

## What CI does not do (and you must)

| Item | Why |
| --- | --- |
| Cosign-verify the freshly-published image against YOUR clock | Catches local OIDC / clock / cosign-version drift that would block operators (S-29 / BA-24). |
| Real-RTMP publish smoke | CI smoke verifies the container boots and `/livez` is up. Humans verify the actual data path (S-27 / BA-23). |
| Update release notes | CI cannot anticipate the operator-relevant narrative (S-30 forbids auto-generated docs). |
| Spot-check platform URLs | Platforms move endpoints; CI doesn't know what's current. |
| Update `last-verified:` in ops docs | Per S-28 / S-30: manual; the release workflow does not touch `docs/ops/`. |
| Verify branch protection + GHCR retention | Settings-only; CI cannot self-verify. |

## Verify

After step 14:

```bash
# 1. Release exists
gh release view v<X.Y.Z>

# 2. Six tags published (subset visible via gh)
gh api -H "Accept: application/vnd.github+json" \
   /users/<OWNER>/packages/container/restream-plus/versions \
   --jq '[.[] | .metadata.container.tags[]] | unique | map(select(startswith("v") or .=="latest" or .=="edge" or startswith("sha-")))'

# 3. Cosign verify still passes
cosign verify \
  --certificate-identity-regexp "^https://github\.com/<OWNER>/Restream_Plus/\.github/workflows/release\.yml@refs/tags/v[0-9.]+$" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/<OWNER>/restream-plus:vX.Y.Z
```

## Troubleshoot

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `git push origin v<X.Y.Z>` rejected | Tag already exists (PA-19 immutability) | Cut the next patch: `v<X.Y.(Z+1)>`. NEVER force-push tags. |
| `release.yml` failed at grype scan | A HIGH+ CVE with a fix is present in the image | Bump the affected dep; cut the next patch. Do NOT add a grype suppression to push the release. |
| `cosign verify` fails locally on a freshly-published image | OIDC issuer drift / Rekor delay / local clock skew / cosign version | Re-check clock; retry with the latest `cosign` release. Wait 60 s if Rekor inclusion is propagating. If persistent, file an issue. |
| Branch protection drifted (a required check disappeared) | Workflow job rename without updating GitHub UI | Re-apply settings per [branch-protection.md](branch-protection.md). Note: re-add the new check name; remove the stale one. |
| GHCR retention shows untagged > 30 days | Policy not applied or was disabled | Re-apply per [ghcr-retention.md](ghcr-retention.md). |

## See also

- [branch-protection.md](branch-protection.md)
- [ghcr-retention.md](ghcr-retention.md)
- [deployment.md](deployment.md) — what operators will read after you ship.
- [upgrade.md](upgrade.md) — particularly *Schema-bump releases* if you bumped `SCHEMA_VERSION`.
- [phase-11-design-memo.md](../architecture/phase-11-design-memo.md) — CI/CD topology.
- [phase-12-design-memo.md](../architecture/phase-12-design-memo.md) — ops-docs design.
