---
applies-to: v1.0.0+
last-verified: 2026-05-18
audience: maintainer
---

# GHCR retention

How the GHCR registry is pruned, what's protected, and how to verify
the policy matches PA-31.

## What this configures

The container-package retention rule on
`ghcr.io/<OWNER>/restream-plus`. Untagged manifests get deleted after
30 days; tagged manifests are never auto-deleted (PA-19 tag
immutability).

## Contract

From [phase-11-design-memo.md §K](../architecture/phase-11-design-memo.md):

> **PA-31 (GHCR retention)**: untagged > 30 days deleted; tagged never
> auto-deleted.

Rationale: multi-arch manifest lists (the thing operators pull as
`:vX.Y.Z`) reference per-arch digests (the amd64-only and arm64-only
sub-images). When `:edge` is bumped, the prior `:edge` manifest-list
overwrite leaves the per-arch digests *untagged* — still in storage,
costing $. After 30 days they are presumed not-being-rolled-back-to.

## Image tag policy (the same block as deployment.md / release-checklist.md — S-5)

| Tag | Moves on | Use for |
| --- | --- | --- |
| `:vX.Y.Z` | Never (immutable per PA-19) | **Production.** Pin this. |
| `:vX.Y` | Each patch release | Auto-patching stage / non-prod. |
| `:vX` | Each minor release | Non-prod. |
| `:latest` | Each stable release tag (NOT main HEAD) | "Current stable" — moves silently on `docker pull`. Not recommended for production. |
| `:edge` | Each push to `main` | Bleeding edge / development only. NEVER production. |
| `:sha-<short>` | Never (matches a commit) | Debugging anchor. May be pruned after 30 days; do NOT pin production to `:sha-*`. |

## How to set

GitHub does not expose per-package retention rules via `gh api` today;
the policy is set in the UI. Navigation:

`https://github.com/<OWNER>/Restream_Plus/pkgs/container/restream-plus`
→ `Package settings` → `Manage Actions access` → `Retention rules`

Configure:

- **Retain for:** `30 days`
- **Apply to:** `Untagged versions`

(Tagged versions remain forever — this is intentional and matches the
PA-19 contract.)

## How to verify

Run quarterly per [maintenance.md](maintenance.md).

```bash
gh api -H "Accept: application/vnd.github+json" \
   /users/<OWNER>/packages/container/restream-plus/versions \
   --jq '[.[] | select(.metadata.container.tags | length == 0) |
          {id, updated_at}] | sort_by(.updated_at) | first'
```

**Expected output (policy in effect):**

- `null` — no untagged versions exist, OR
- A single object whose `updated_at` is < 30 days old.

**Drift signal (policy NOT in effect):**

```json
{"id": 12345678, "updated_at": "2025-09-01T12:00:00Z"}
```

— an untagged version older than 30 days survives. Re-apply the
retention rule via the UI; the next GHCR sweep will reconcile.

## Operator-pull implications

A note that lives here because operators reach it from the image
they pulled:

- **Pulling by digest** (`ghcr.io/.../restream-plus@sha256:...`)
  gets a permanent image *as long as any tag still references the
  manifest list*. A pure-untagged digest survives the 30-day window
  only by accident.
- **Pulling `:vX.Y.Z`** is permanent — the tag is immutable per
  PA-19, and the manifest list it points at always retains its
  per-arch sub-images.
- **Pulling `:sha-<short>`** is at risk after 30 days if no semver
  tag promoted that commit's build. `:sha-<short>` is a debugging
  anchor, not a deployment pin.

This is the operator-visible side of the policy; the deployment
recommendation (always pin `:vX.Y.Z`) is owned by
[deployment.md](deployment.md).

## What breaks if this drifts

| Drift | Consequence |
| --- | --- |
| Retention off / not configured | Registry grows forever. Cost surprise on the GHCR billing page. |
| Retention applies to "All versions" instead of "Untagged versions" | A semver tag gets deleted after its retention window — violates PA-19 and breaks every operator who pinned that tag. **High-severity.** Re-apply with the correct filter immediately. |
| Retention window too short (< 7 days) | An operator pinning `:sha-<short>` for a debugging session may find the image pruned before they're done. Bump back to 30 days. |

## See also

- [release-checklist.md](release-checklist.md) — step 10 verifies this.
- [branch-protection.md](branch-protection.md) — companion repo-settings doc.
- [deployment.md](deployment.md) — operator-side tag pinning policy.
- [phase-11-design-memo.md §K](../architecture/phase-11-design-memo.md) — PA-31 canonical statement.
