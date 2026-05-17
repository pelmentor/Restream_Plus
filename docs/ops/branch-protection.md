---
applies-to: edge
last-verified: 2026-05-17
audience: maintainer
---

# Branch protection

How to configure GitHub branch protection on `main` so the required
status checks match PA-30, and how to verify them.

## What this configures

The `main` branch's protection rule. Every push to `main` goes
through a PR; every PR must have the 5 PA-30 required status checks
green; admins are subject to the same rules.

## Contract

From [phase-11-design-memo.md §I](../architecture/phase-11-design-memo.md):

> **PA-30 (branch protection contract)**: required status checks are
> `backend-test`, `backend-lint`, `frontend`, `backend-lockfile`,
> `workflow-pins`. Plus: "Require linear history" ON, "Require branches
> up to date" ON. Workflow-file paths AND job IDs are part of the
> public CI contract; renames need an ADR + branch-protection update
> in the same PR.

`pr-image-smoke` is informational — visible signal, not blocking.

## How to set

GitHub UI navigation:

`Settings → Branches → Branch protection rules → Add rule` (or
`Edit` if a rule already exists for `main`).

| Setting | State | Source |
| --- | --- | --- |
| Branch name pattern | `main` | — |
| Require a pull request before merging | ON | Phase 11 PR-only flow |
| ↳ Require approvals | `0` | Single-maintainer; no second reviewer |
| ↳ Dismiss stale pull request approvals when new commits are pushed | ON | — |
| Require status checks to pass before merging | ON | PA-30 |
| ↳ Require branches to be up to date before merging | ON | PA-30 |
| ↳ Required status checks | `backend-test`, `backend-lint`, `frontend`, `backend-lockfile`, `workflow-pins` | PA-30 — match exactly (S-7) |
| Require conversation resolution before merging | ON | — |
| Require linear history | ON | PA-30 |
| Require signed commits | OFF | Defensible for single-admin; GitHub-account auth IS the trust boundary |
| Require deployments to succeed | OFF | n/a — no GitHub Environments |
| Lock branch | OFF | — |
| Do not allow bypassing the above settings | ON | Even admin subject to checks (BA-26) |
| Restrict who can push | OFF | Single-admin |
| Allow force pushes | OFF | — |
| Allow deletions | OFF | — |

CODEOWNERS: not required for v1 (single-admin). Add when / if a
second maintainer joins; require maintainer review on security-
sensitive paths (`docker/`, `app/auth/`, `app/crypto/`,
`.github/workflows/`).

### What this rule does NOT protect against

`required_approving_review_count: 0` is the single-admin trade-off:
the maintainer can open a PR, watch CI go green, and self-merge —
no human review happens. `enforce_admins: true` keeps the maintainer
subject to the 5 required *CI* checks but does NOT add a human
reviewer. If the project gains a second maintainer, bump
`required_approving_review_count` to `1` and add CODEOWNERS so the
security-sensitive paths above require maintainer review.

The same configuration via `gh api` (apply, not just check):

```bash
gh api --method PUT \
  -H "Accept: application/vnd.github+json" \
  /repos/<OWNER>/Restream_Plus/branches/main/protection \
  -F required_status_checks.strict=true \
  -F 'required_status_checks.contexts[]=backend-test' \
  -F 'required_status_checks.contexts[]=backend-lint' \
  -F 'required_status_checks.contexts[]=frontend' \
  -F 'required_status_checks.contexts[]=backend-lockfile' \
  -F 'required_status_checks.contexts[]=workflow-pins' \
  -F enforce_admins=true \
  -F required_pull_request_reviews.required_approving_review_count=0 \
  -F required_pull_request_reviews.dismiss_stale_reviews=true \
  -F required_pull_request_reviews.require_code_owner_reviews=false \
  -F required_conversation_resolution=true \
  -F required_linear_history=true \
  -F allow_force_pushes=false \
  -F allow_deletions=false \
  -F restrictions=null
```

(Some fields are typed booleans in the GitHub API; `-F` form-encodes
strings. If a field fails to apply, use the `--input` flag with a
JSON file — see the [GitHub API reference for branch protection](https://docs.github.com/en/rest/branches/branch-protection).)

## How to verify

```bash
gh api repos/<OWNER>/Restream_Plus/branches/main/protection \
  --jq '{
    required_checks:  .required_status_checks.contexts,
    strict:           .required_status_checks.strict,
    linear_history:   .required_linear_history.enabled,
    enforce_admins:   .enforce_admins.enabled,
    allow_force:      .allow_force_pushes.enabled,
    allow_delete:     .allow_deletions.enabled
  }'
```

**Expected output:**

```json
{
  "required_checks": [
    "backend-test",
    "backend-lint",
    "frontend",
    "backend-lockfile",
    "workflow-pins"
  ],
  "strict": true,
  "linear_history": true,
  "enforce_admins": true,
  "allow_force": false,
  "allow_delete": false
}
```

Run this from [release-checklist.md](release-checklist.md) step 9 and
on demand whenever a workflow job name changes.

## What breaks if this drifts

| Drift | Consequence |
| --- | --- |
| `required_checks` missing one of the 5 PA-30 names | A PR can merge without that gate. Worst case: a PR that fails `workflow-pins` (unpinned action SHA) silently weakens supply-chain guarantees. |
| Old check name still required but workflow job renamed | Branch is permanently un-mergeable for everyone, including you. Re-apply with the new name. |
| `enforce_admins=false` | The maintainer can merge through red CI by clicking "Bypass". Defeats the purpose of branch protection for a single-maintainer project. |
| `linear_history=false` | Merge commits land on main. History becomes harder to bisect; CI cache keys (Phase 11 PA-11) start straddling unrelated branches. |
| `allow_force_pushes=true` | Maintainer can overwrite shipped commits. PA-19 tag immutability survives (tags are separate), but commit SHAs in past releases stop being authoritative. |

## See also

- [release-checklist.md](release-checklist.md) — step 9 verifies this.
- [ghcr-retention.md](ghcr-retention.md) — companion repo-settings doc.
- [phase-11-design-memo.md §I](../architecture/phase-11-design-memo.md) — PA-30 canonical statement.
