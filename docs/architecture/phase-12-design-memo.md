# Phase 12 — Ops docs (locked design)

**Status:** Accepted — 2026-05-17
**Synthesis of:** Software Architect + Backend Architect parallel passes (Rule №3).
**Binds:** every file under `docs/ops/`. Implementation MUST encode the
invariants in §M. Drift = Rule №5 audit failure.

---

## Overview

Phase 12 is the operator-facing documentation layer. It is *not* a place
to invent new behavior — it documents the surface the code already
ships (Phases 0–11). Where the code currently disagrees with an ADR,
the doc encodes shipping reality and the gap is logged here as a
carryover item (§N), not papered over.

Scope: **7 files** under `docs/ops/` plus one canonical compose stub
under `docs/ops/compose.yaml`. The 5 files originally listed in
`docs/CODE_PLAN.md` (deployment, backup-restore, upgrade, maintenance,
release-checklist) plus the 2 files Phase 11 invariants PA-30 / PA-31
require (branch-protection, ghcr-retention).

Audience: a single sysadmin running Restream_Plus on a home server,
NAS, or small VPS. Linux comfortable, `docker` + `docker compose`
literate, no fleet management, no on-call rotation. The 3 am failure
mode is *they will not know until they sit down with coffee*.

---

## A. Information architecture across the 7 docs

Each file has one purpose, one audience, and one entry point. Anything
that doesn't fit gets a cross-link, not a duplicate paragraph.

| File | Purpose | Audience | Entry point |
| --- | --- | --- | --- |
| `deployment.md` | "How do I get Restream_Plus running on this machine, today, in production?" | First-time installer; also operator moving hosts | README "Production deployment" |
| `backup-restore.md` | "How do I take a recoverable backup of `/data` while the container is running, and how do I restore one?" | Day-N operator | deployment.md "do this immediately after first boot" |
| `upgrade.md` | "How do I move from version A to version B without losing data or downtime beyond a controlled window?" | Day-N operator with an existing deployment | Release notes; deployment.md |
| `maintenance.md` | "What recurring tasks does this thing need so it doesn't slowly rot?" | Day-N operator | deployment.md |
| `release-checklist.md` | "I am the maintainer cutting a release. What must I do, in order, before/after pushing a `vX.Y.Z` tag?" | Project maintainer (NOT the end-user operator) | README "Maintainer notes" |
| `branch-protection.md` | "How do I configure GitHub branch protection on `main` so the required status checks match PA-30, and how do I verify them?" | Repo admin | release-checklist.md, phase-11 memo PA-30 |
| `ghcr-retention.md` | "How is the GHCR registry pruned, what's protected, and how do I verify the policy matches PA-31?" | Repo admin / package owner | release-checklist.md, phase-11 memo PA-31 |

Plus: `docs/ops/compose.yaml` — the canonical reference compose file
that `deployment.md` and `release-checklist.md` both reference. One
file, one truth.

### Per-doc length budget

- `deployment.md`: multi-page (5–8 screens). Most readers' first
  contact; explicit verification at every step.
- `backup-restore.md`: 1 page.
- `upgrade.md`: 1 page.
- `maintenance.md`: 1 page.
- `release-checklist.md`: 1 page (numbered checklist; the maintainer
  runs straight down it).
- `branch-protection.md`: ½ page.
- `ghcr-retention.md`: ½ page.

---

## B. Common front-matter (every doc)

```yaml
---
applies-to: vX.Y.Z+        # or "edge" for branch-protection / ghcr-retention
last-verified: 2026-05-17
audience: operator         # or "maintainer" for release-checklist
---
```

- `applies-to` — the operator's "is this doc valid for the version I'm
  running?" check. Most expensive question to answer wrong.
- `last-verified` — staleness signal. The release-checklist makes
  refreshing it a step (§I.4 below).
- `audience` — one-word filter so a maintainer doesn't waste five
  minutes reading an end-user doc.

For the initial v1 ship the value of `applies-to` is `v1.0.0+`. For
`branch-protection.md` and `ghcr-retention.md` the value is `edge`
because their content tracks the GitHub repo state, not the published
image.

---

## C. Body skeleton — two patterns

**Procedure docs** (deployment, backup-restore, upgrade, maintenance,
release-checklist):

1. **TL;DR** — 3–5 lines, so the operator confirms they're in the
   right doc.
2. **When to use this** — entry condition.
3. **Prerequisites** — tools, access, state assumed.
4. **Procedure** — numbered. Every step has *what to run*, *expected
   output*, *what to do if the output differs*.
5. **Verify** — explicit success criteria (a `curl … | grep …`, not
   "looks fine").
6. **Troubleshoot** — symptom → cause → fix table, no prose.
7. **See also** — outbound cross-links.

**Reference docs** (branch-protection, ghcr-retention):

1. **What this configures** — one paragraph.
2. **Contract** — the invariant (PA-30, PA-31) restated with a back-link.
3. **How to set** — UI path + scripted equivalent (`gh api`).
4. **How to verify** — read-only command returning current state.
5. **What breaks if this drifts** — concrete consequence.
6. **See also.**

The "Verify" / "How to verify" section is non-negotiable for every
file: every doc must contain at least one machine-verifiable assertion
the operator can script into local monitoring. Rule №5 audit grep
target.

---

## D. Single sources of truth — what NOT to duplicate

Drift between two docs that state the same fact is the slowest, most
painful failure mode in operations docs. The table below names the
canonical home for every load-bearing fact and how the ops docs cite
it.

| Fact | Canonical home | Operator-visible in | Strategy |
| --- | --- | --- | --- |
| Container ports `:1935/tcp`, `:8000/tcp` | `docker/Dockerfile` `EXPOSE` line | deployment.md ports table | Copy; CI grep tripwire keeps doc + Dockerfile aligned (SA-3). |
| Image tag table (`:vX.Y.Z`, `:vX.Y`, `:vX`, `:latest`, `:edge`, `:sha-<short>`) | phase-11-design-memo §F + `.github/workflows/release.yml` metadata-action | deployment.md, release-checklist.md, ghcr-retention.md | Copy as a byte-identical block in all three; CI grep tripwire (SA-4). |
| Env-var surface (`RESTREAM_*`) | `app/config.py` (Pydantic `AppSettings`) | deployment.md "Configuration" appendix | Copy the field list; CI grep tripwire (SA-5). |
| `docker stop -t 30` rule | Phase 10 §D + ADR-0010 + I-D4 | deployment.md, upgrade.md, maintenance.md, compose.yaml | Copy the operator-facing rule one sentence per doc; link Phase 10 §D for the why. |
| `/livez` vs `/readyz` semantics | ADR-0011 + Phase 10 §H | deployment.md (HEALTHCHECK note + reverse-proxy stanza), upgrade.md (verification), maintenance.md (weekly check) | Link ADR-0011; copy the operator rule "orchestrator probes `/livez`; reverse proxies probe `/readyz`; never swap" verbatim. |
| Cosign verify command | phase-11-design-memo §E + this memo §G | deployment.md (first-boot verify), upgrade.md (pre-pull gate), release-checklist.md (post-publish verify) | Byte-identical in all three; CI grep tripwire (SA-8). |
| `uid:gid = 10001:10001` | Phase 10 §F + Dockerfile | deployment.md (volume troubleshooting) | Copy the uid; link Phase 10 §F. |
| `CAP_NET_BIND_SERVICE` rootless-podman footgun (I-G5) | Phase 10 §G | deployment.md (rootless-podman section) | Copy the workaround; link Phase 10 §G. |
| `schema_version` model | ADR-0007 + `app/db/schema_init.py` | upgrade.md | Link ADR-0007 + the schema_init module; copy the operator rule "rolling back across a schema bump = restore-from-backup, not stop-old-start-prior". |
| nginx logs may carry stream-key fragments | Phase 10 §I (I-I1..I-I4) | deployment.md (troubleshooting), backup-restore.md (what's in a backup), maintenance.md (log handling) | Copy the warning; link Phase 10 §I. Three docs touch this — CI grep tripwire on the warning text (SA-21). |
| Branch-protection required-checks list | phase-11-design-memo PA-30 | branch-protection.md | Copy the five job IDs verbatim; CI grep tripwire matches `ci.yml` job names (SA-16). |
| GHCR retention contract | phase-11-design-memo PA-31 | ghcr-retention.md | Copy the contract; link PA-31. |

**Rule for the writer:** if a fact appears in two docs and is not in
this table, add a row first. Otherwise it is a drift bomb.

---

## E. Reversibility — what level of futureproofing each claim gets

Operators read docs as eternal truth. Each claim is one of:

- **Eternal (v1 line):** uid 10001, gid 10001, container internal
  ports 1935 + 8000, `/data` mount point, `RESTREAM_PASSPHRASE_SOURCE`
  values, ADR-grade contracts. Stated as bare fact, no version
  qualifier. Changing any of these is a major-version event with an
  ADR; out of Phase 12's horizon. Per Rule №2, no migration path —
  the doc gets rewritten for the new major.
- **Per-release:** the current `:vX.Y.Z` value used in examples, the
  current image digest in cosign-verify samples, the current
  `SCHEMA_VERSION`, contents of "what's new" callouts. Use
  `vX.Y.Z` as a substitutable placeholder; qualified by the doc's
  `applies-to:` + `last-verified:` front-matter.
- **Per-deployment:** operator's reverse-proxy choice, operator's
  volume mount path, operator's host port mapping, operator's choice
  of passphrase-source mode. Parameterize with `<PLACEHOLDER>` named
  variables and call them out under Prerequisites.

### Forbidden patterns (CI grep tripwires)

- **No "in v2 we will…"** statements (SA-19). Operators read this and
  plan around it. If a future change is anticipated, the doc says
  "this is the v1 contract" and stops.
- **No "if you're on legacy version X, do Y instead"** (SA-20). Per
  Rule №2; the docs cover one supported v1 surface.
- **No auto-generated docs.** The release workflow does not edit ops
  docs. The release-checklist makes a *manual* "edit `last-verified:`
  in N files" step (SA-22). Auto-editing creates a back door and
  breaks the read-grep audit trail.

---

## F. Failure modes the docs must rescue from

For each doc, the worst-day scenarios the doc directly addresses. This
is the doc writer's prioritization checklist.

### deployment.md

1. **Container exits immediately.** `RESTREAM_MASTER_PASSPHRASE`
   empty or missing → entrypoint fail-fast. First troubleshooting row.
2. **Shell-mangled passphrase.** Operator wrote
   `-e RESTREAM_MASTER_PASSPHRASE=$ecretP@$$word` unquoted →
   `$ecretP@` shell-interpolates to empty → container starts with a
   different passphrase than typed → DB encrypted under unknown key.
   Show quoted forms only; troubleshooting row: *"after first start,
   if you cannot log in: STOP, do NOT wipe `/data`, file an issue —
   your passphrase may have been shell-mangled."*
3. **Reverse proxy returns 502 forever.** Probe points at `/livez`
   instead of `/readyz`, OR container is locked in paste mode and
   `/readyz` correctly returns 503. Probe-mode decision tree.
4. **Volume permission denied at first boot.** SELinux/AppArmor blocks
   the chown by `init-data`. `chown 10001:10001 /srv/restream` is the
   workaround.
5. **Rootless podman silently rejects privileged ports.** I-G5
   workaround.

### backup-restore.md

1. **`cp -r /data` of a hot container** yields a half-written WAL.
   Forbidden in bold, in the TL;DR, *and* in the procedure.
2. **Operator lost the master passphrase.** Encrypted columns are
   unrecoverable. Callout: *"back up the passphrase separately, in a
   password manager, with the same retention as the data."*
3. **Restore-to-new-host fails on `schema_version` mismatch.** Routes
   to upgrade.md.

### upgrade.md

1. **Skipped version (vN → vN+2)** — per ADR-0007 single-shot schema,
   straight upgrade between releases at the same `SCHEMA_VERSION` is
   supported; releases that bump `SCHEMA_VERSION` require the
   export/reimport flow. The release notes must say which.
2. **`docker stop` (no `-t 30`)** cuts the audit row.
3. **Rollback after a schema migration ran.** Wedged on new tag;
   operator stops and pulls prior tag; prior tag refuses to boot
   because `SCHEMA_VERSION` mismatches. Bold conditional: *"if any
   migration ran, rollback = restore-from-backup, not
   start-prior-tag."*

### maintenance.md

1. **`/data/logs/nginx-error.log` fills the disk.** Monthly rotation
   recipe.
2. **Quarterly platform-URL drift.** VK / YouTube / Twitch / Kick can
   move endpoints; quarterly cross-check against `platforms-reference.md`.
3. **GHCR retention silently prunes `:sha-<short>`** pinned in
   production. Routes to ghcr-retention.md and the rule "do not pin
   production to `:sha-<short>`".
4. **"I'm locked out of my own panel."** In-memory rate-limit reset
   on container restart — surface once at the ops layer (BA-20).

### release-checklist.md

1. **Maintainer pushes `vX.Y.Z` while CI is red on main.** Branch
   protection prevents merge but admin override could bypass. First
   step: "main CI is green at HEAD"; explicitly forbid admin overrides
   (BA-26).
2. **Tag immutability violation attempt.** PA-19 fails the publish;
   the only path is the next patch.
3. **Cosign-verify fails on the freshly-published image.** Routes to
   incident path (do not announce; investigate).
4. **Skipping the platforms-reference URL spot-check.**

### branch-protection.md

1. **Required-check renamed; old name still required.** Branch
   permanently un-mergeable. Verify step lists current required
   checks vs the workflow's actual job IDs.
2. **Admin bypass enabled.** Default GitHub setting allows admins to
   bypass branch protection. Doc instructs disabling this.

### ghcr-retention.md

1. **Retention policy not configured** → registry grows forever / cost
   surprise.
2. **Retention policy too aggressive** → semver tag deleted. Should be
   impossible per PA-31; verify recipe catches misconfiguration.
3. **`:sha-<short>` pulled in production gets pruned.** Operator
   misuse; doc names it and points at deployment.md image-pin table.

**Out of scope:** anything where the design was correct and the
failure is a code bug. Those are issues, not runbook items.

---

## G. Verifiability — every claim is testable

Every procedure step has the shape **command → expected output → fail
action**. The operator never interprets "looks fine"; the doc provides
the regex / numeric / status match.

### Conventions

- Commands in bash fenced blocks; leading `$` only when output follows.
- Expected output in a separate fenced block; multi-line shows the
  load-bearing line + `# ... (truncated)`.
- Match condition stated: *"the line containing `status` is `alive`"*,
  not *"you should see alive somewhere"*.
- Where useful, a one-liner that returns 0 / non-zero so the operator
  can script the check.

### The cosign verify command — single source

Three docs print this command (deployment.md, upgrade.md,
release-checklist.md). All three blocks are byte-identical and CI
grep tripwire keeps them so (SA-8). Form:

```bash
cosign verify \
  --certificate-identity-regexp "^https://github\\.com/<owner>/Restream_Plus/\\.github/workflows/release\\.yml@refs/tags/v[0-9.]+$" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/<owner>/restream-plus:vX.Y.Z
```

The doc enumerates three possible outcomes:

- **Success:** specific log lines (certificate identity match, OIDC
  issuer match). Operator proceeds.
- **Signature not found / verification failed:** STOP. Do not deploy.
  Do not retry with `--insecure-skip-verify`. Confirm registry
  hostname is `ghcr.io` (not a typosquat). File an issue.
- **Cosign tool itself errored:** retry with a known-good cosign
  version (doc names the minimum cosign version tested at
  release time).

No `--allow-insecure` / `--skip-verification` flag appears in any doc.
The cosign-verify is a gate, not a suggestion. CI grep tripwire on
the negative match (SA-9).

### Other high-stakes verifications

- **First-boot `/livez`:** `curl -fsS http://127.0.0.1:8000/livez` →
  `{"status":"alive"}`. Non-200 = wedged or still booting; wait 60 s
  and re-check, then `docker logs`.
- **First-boot `/readyz`:** `/readyz` returns `{"status":"ready", ...}`
  with five sub-checks `ok: true` (db, schema, rtmp_ingest, supervisor,
  passphrase). Doc enumerates and points at troubleshooting per
  sub-check.
- **Volume permissions:** `docker exec restream-plus stat -c '%U:%G %a' /data`
  → `restream:restream 750`.
- **Schema version:** `docker exec restream-plus sqlite3 /data/restream.db
  "PRAGMA user_version"` → matches the release notes' stated value
  (the binary's compiled-in `SCHEMA_VERSION` constant).
- **Branch protection in place:** `gh api` recipe (§J).
- **GHCR retention policy in place:** `gh api` recipe (§K).

Each is grep-targetable for the Rule №5 audit.

---

## H. Sensitive material the docs must NEVER do

Forbidden-patterns checklist. The doc-writer Rule №4 brief includes
this list as the security-pass grep target.

1. No example `RESTREAM_MASTER_PASSPHRASE` literal values. Use
   `<REDACTED — generate with openssl rand -hex 32>` placeholder.
   No `hunter2`, no `correct-horse-battery-staple` (famous → invites
   copy-paste).
2. No `.env` committed to git. Every `.env` example uses `chmod 600`
   + `.gitignore` reference.
3. No debug commands that emit sensitive bytes to stdout: `cat
   /data/restream.db`, `hexdump`, `xxd`, `docker inspect | grep
   PASSPHRASE`, `docker exec env`, `docker exec cat /proc/1/environ`.
4. No paste of `/data/logs/nginx-*.log` into a public issue. Phase 10
   I-I1..I-I4 → these can carry ingest-key fragments. Three docs
   touch the warning; CI grep tripwire (SA-21).
5. No paste of `docker logs` output unfiltered. Use the redaction
   `sed` pipeline (BA §G).
6. No `--insecure-skip-verify` / `--allow-insecure` on cosign.
7. No `cp -r /data` as a backup primitive (only as DO-NOT example).
   `docker cp` of a hot container — same rule.
8. No suggestion to share the master passphrase with anyone for
   support. A support flow that requires it is a design failure.
9. No `docker run --privileged` workaround. If a procedure seems to
   need it, the procedure is wrong.
10. No `:latest` or `:edge` as recommended production pins. Production
    = `:vX.Y.Z` per phase-11 §F + R-2 (SA-12).
11. No `chmod 777` or `chown root:root /data` workaround. `init-data`
    + uid 10001 are load-bearing.

---

## I. Cross-doc procedural decisions

### I.1 Reverse-proxy recipes — Caddy + nginx + Traefik

All three. Each is one snippet (≤ 25 lines), no prose, with `<host>`,
`<container>`, `<cert-path>` placeholders. Each recipe must:

- Terminate TLS with a real cert.
- Pass `X-Forwarded-For` + `X-Forwarded-Proto`.
- Pass WebSocket upgrade headers (`Upgrade`, `Connection`) — broken
  panel live status without these.
- Set `RESTREAM_TRUSTED_PROXIES=<proxy-IP>/32` in the container env
  (else rate limit buckets every request as 127.0.0.1).
- Proxy `/livez`, `/readyz`, `/healthz` as well (so the reverse proxy
  can be the probe target).

### I.2 Compose file — canonical reference

`docs/ops/compose.yaml` is the single canonical compose definition.
- `deployment.md` shows it inline as the recommended deployment shape.
- `release-checklist.md` smoke step uses it on a scratch host.
- One source of truth.

It must use:
- Image pin `ghcr.io/<owner>/restream-plus:vX.Y.Z` (not `:latest`).
- `stop_grace_period: 30s` (compose-side I-D4).
- Named volume for `/data`.
- `--env-file /etc/restream.env` style env-loading (NOT inline
  `environment:` blocks that leak in `docker inspect`).
- Restart policy `unless-stopped`.
- Healthcheck inherited from image; no override.

### I.3 Configuration appendix in deployment.md (BA J-3)

The exhaustive env-var table lives as an appendix in `deployment.md`.
Not a standalone `configuration.md` — operators look in deployment.md
first; splitting drives extra clicks. Generated against `app/config.py`
field list; CI grep tripwire (SA-5).

### I.4 Release notes mechanism (BA J-2)

The GitHub release body is the canonical changelog for v1. Generated
manually by the maintainer using a template (in
`release-checklist.md`). No `RELEASES.md` / `CHANGELOG.md` tracked in
git for v1 — the release body lives at the tag.

The release-checklist mandates a `[schema-bump]` annotation in the
release body for any release that bumps `SCHEMA_VERSION`, plus the
export/reimport recipe added to `upgrade.md` in the same release
commit (BA-22).

### I.5 Restore verification without exposing a stream key (BA J-1)

For v1, restore verification is done by: (1) schema + row-count
probe via `sqlite3` (no plaintext exposure), then (2) log in and
start a brief test stream — fanout to platforms is the strongest
end-to-end signal. The "test decrypt without revealing" UI affordance
is logged as a Phase 13+ enhancement in §N.

### I.6 The `RESTREAM_RESET_ADMIN_PASSWORD` flow (SA J-6)

Lives in two places:
- `deployment.md` troubleshooting: "I lost my admin password" → set
  `RESTREAM_RESET_ADMIN_PASSWORD=1` + `RESTREAM_ADMIN_PASSWORD=<new>`
  on next container start.
- `maintenance.md` compromise response: same procedure, framed as the
  recovery path when in-UI rotation is not possible.

No standalone `password-reset.md`.

### I.7 Log retention default (SA J-7)

The log retention default lives in `app/db/schema.sql` (settings row,
`log_retention_days=14`). `maintenance.md` mentions retention under
monthly cadence and points at the panel (Settings → Logs) for the
operator-tunable knob.

### I.8 Automated GHCR retention verification (SA J-5, BA J-5)

Operator-quarterly via `maintenance.md`. A scheduled GHA workflow that
opens an issue on drift is logged as Phase 13+ in §N. Phase 12 is
operator-side, not workflow-side.

---

## J. Branch protection contract (PA-30) — exact settings

The doc encodes the literal UI state, with navigation:

`Settings → Branches → Branch protection rules → Add rule`

Branch name pattern: `main`.

| Setting | State | Source |
| --- | --- | --- |
| Require a pull request before merging | ON | Phase 11 PR-only flow |
| ↳ Require approvals | 0 | Single-maintainer project |
| ↳ Dismiss stale pull request approvals when new commits are pushed | ON | |
| Require status checks to pass before merging | ON | PA-30 |
| ↳ Require branches to be up to date before merging | ON | |
| ↳ Required status checks | `backend-test`, `backend-lint`, `frontend`, `backend-lockfile`, `workflow-pins` | PA-30 exact list |
| Require conversation resolution before merging | ON | |
| Require linear history | ON | PA-30 |
| Require signed commits | OFF | Defensible for single-admin; GitHub-account auth is the trust boundary |
| Do not allow bypassing the above settings | ON | Even admin subject to checks (BA-26) |
| Allow force pushes | OFF | |
| Allow deletions | OFF | |

CODEOWNERS: not required for v1 (single-admin). Add when / if a
second maintainer joins.

Audit recipe (the operator's verify step):

```bash
gh api repos/<owner>/Restream_Plus/branches/main/protection \
  --jq '{
    required_checks: .required_status_checks.contexts,
    strict: .required_status_checks.strict,
    linear_history: .required_linear_history.enabled,
    enforce_admins: .enforce_admins.enabled,
    allow_force: .allow_force_pushes.enabled,
    allow_delete: .allow_deletions.enabled
  }'
```

Expected output is shown in the doc; any deviation is the operator's
trigger to re-apply settings.

---

## K. GHCR retention contract (PA-31) — exact policy

Policy:
- Untagged manifests older than 30 days → DELETE.
- Tagged manifests → NEVER auto-delete (PA-19 immutability).

Rationale: multi-arch manifest lists (`:vX.Y.Z`) reference per-arch
digests (the amd64-only and arm64-only sub-images). When `:edge` is
bumped, the prior `:edge` manifest-list overwrite leaves the per-arch
digests *untagged* — still in storage, costing $. After 30 days they
are presumed not-being-rolled-back-to.

UI path: package settings → Manage Actions access → Retention rules.

Verification (one-shot, quarterly per `maintenance.md`):

```bash
gh api -H "Accept: application/vnd.github+json" \
   /users/<owner>/packages/container/restream-plus/versions \
   --jq '[.[] | select(.metadata.container.tags | length == 0) |
          {id, updated_at}] | sort_by(.updated_at) | first'
```

Output is the oldest untagged version's update timestamp. If > 30 days
old, the policy is NOT in effect. Doc encodes this with a "run
quarterly" annotation.

**Note for operators pulling by digest:** digests survive tag deletion
as long as ANY tag still references the manifest. Operators pulling
`:sha-<short>` are at risk after 30 days if no semver tag promoted
that build. `:sha-<short>` is a debugging anchor, not a deployment pin
(SA-12 / BA-30).

---

## L. Threat model the ops docs encode (operator-side)

Code-level threat model is in ADR-0006. Ops docs encode the USAGE
threat model — what the operator can do that the code cannot defend
against. Top 10, with the doc that owns each:

| # | Operator-side failure | Owning doc |
| --- | --- | --- |
| T-1 | `:1935` published to a public interface — anyone on the LAN/Internet can hijack the publish slot | `deployment.md` |
| T-2 | `nginx-error.log` pasted publicly — upstream error context can carry the ingest key | `deployment.md` (log handling), `maintenance.md` |
| T-3 | `docker stop` without `-t 30` → SIGKILL mid-publish → corrupted recording + audit gap | `deployment.md`, `upgrade.md`, `maintenance.md` |
| T-4 | Short / dictionary master passphrase | `deployment.md` |
| T-5 | `docker inspect` exposes env-mode passphrase | `deployment.md`, `maintenance.md` |
| T-6 | `cp -r /data` of a hot DB → corrupted restore | `backup-restore.md` |
| T-7 | Pulling `:latest` for production | `deployment.md`, `upgrade.md` |
| T-8 | Pre-existing `/data` bind-mount owned by wrong uid → `init-data` chown fails | `deployment.md` (rootless / SELinux subsection) |
| T-9 | Rootless podman missing `--cap-add NET_BIND_SERVICE` for privileged-port bind | `deployment.md` |
| T-10 | Skipping cosign verification on `docker pull` | `upgrade.md`, `release-checklist.md` |

---

## M. Locked invariants (S-1 .. S-30)

The implementation MUST encode each. These are what Rule №5 audit
will grep for in the delivered doc set.

### Structure / metadata

- **S-1** Every file under `docs/ops/` carries YAML front-matter with
  `applies-to`, `last-verified`, `audience` keys, all populated.
- **S-2** Every procedure doc (deployment, backup-restore, upgrade,
  maintenance, release-checklist) follows the §C procedure skeleton:
  TL;DR → When to use → Prerequisites → Procedure → Verify →
  Troubleshoot → See also.
- **S-3** Every reference doc (branch-protection, ghcr-retention)
  follows the §C reference skeleton: What this configures → Contract
  → How to set → How to verify → What breaks if this drifts → See
  also.
- **S-18** Every doc has at least one machine-verifiable command in
  its Verify / How to verify section.

### Single-source-of-truth tripwires

- **S-4** Container ports in `deployment.md` (1935, 8000) match
  `EXPOSE` in `docker/Dockerfile`.
- **S-5** Image tag table is byte-identical in `deployment.md` and
  `ghcr-retention.md` (the two docs operators read for the tag
  policy). `release-checklist.md` does NOT reproduce the table — it
  references the canonical home in `deployment.md`.
- **S-6** Env-var appendix in `deployment.md` enumerates the
  `RESTREAM_*` fields from `app/config.py` (no extra, no missing).
- **S-7** Branch-protection required-check list in
  `branch-protection.md` matches the five job names in
  `.github/workflows/ci.yml`: `backend-test`, `backend-lint`,
  `frontend`, `backend-lockfile`, `workflow-pins`.

### Operational rules

- **S-8** Every occurrence of `docker stop` in any ops doc (or
  `docs/ops/compose.yaml`) carries `-t 30` or its compose equivalent
  (`stop_grace_period: 30s`). No bare `docker stop` anywhere.
- **S-9** Cosign verify command in `deployment.md`, `upgrade.md`,
  `release-checklist.md` is byte-identical and includes BOTH
  `--certificate-identity-regexp` (or `--certificate-identity`) and
  `--certificate-oidc-issuer`.
- **S-10** No doc shows a `--insecure-skip-verify` / `--allow-insecure`
  flag on cosign or equivalent fallback.
- **S-11** `deployment.md` documents both `RESTREAM_PASSPHRASE_SOURCE=env`
  and `=paste` modes with the operational trade stated plainly:
  *"in paste mode, your restreamer is offline after every restart
  until you log in."*
- **S-12** `deployment.md` "Network exposure" subsection states
  explicitly that `:1935` MUST NOT be bound to a public interface,
  and shows `-p 127.0.0.1:1935:1935` as the safe form.
- **S-13** `deployment.md` documents `RESTREAM_TRUSTED_PROXIES` and
  warns: *"if you put Restream_Plus behind a reverse proxy and forget
  this var, every request rate-limits as if from 127.0.0.1 (annoying
  but safe)."*
- **S-14** No doc references a `.first-boot-password` file or a
  stdout-printed first-boot password. Docs state shipping reality:
  `RESTREAM_ADMIN_PASSWORD` is required on first boot. The
  ADR-0005-vs-code drift is logged in §N as a Phase 12 carryover.
- **S-15** `deployment.md` recommends `--env-file` over inline `-e`
  for the passphrase (avoids `docker inspect` exposure noted in
  Phase 10 DA-7).

### Backup / upgrade rules

- **S-16** `backup-restore.md` uses `sqlite3 .backup` (or
  `VACUUM INTO`) as the primitive. `cp -r /data` appears ONLY as a
  DO-NOT counter-example.
- **S-17** `backup-restore.md` enumerates the exact backup file set:
  the `sqlite3 .backup` output + `/data/.kdf_salt` + `/data/.kdf_salt.previous`
  (if present) + `/data/tls/` (if non-empty). `/data/logs/` is
  EXCLUDED (sensitive + non-essential).
- **S-19** `backup-restore.md` includes the rotation-vs-backup warning
  verbatim: a backup pre-rotation is undecryptable after 24 h unless
  it contained `.kdf_salt.previous` AND the operator remembers the
  old passphrase.
- **S-20** `upgrade.md` states the schema-version contract: binary
  refuses to boot on ANY mismatch (higher or lower); cross-bump
  rollback = restore-from-backup.
- **S-21** `upgrade.md` enumerates the pre-upgrade ordered checklist:
  backup → cosign verify → docker pull → docker stop -t 30 → docker
  run new → /readyz → smoke → only then delete old image.

### Log-handling rules

- **S-22** Every claim about `/data/logs/nginx-*.log` content
  includes a "do not share publicly" warning. Three docs touch this
  (deployment.md, backup-restore.md, maintenance.md); the warning
  text is identical.
- **S-23** `maintenance.md` includes the `docker logs --since 5m | sed`
  redaction pipeline for support-bundle generation; warns against
  attaching raw `/data/logs/` files.

### Tag-policy rules

- **S-24** Every doc that mentions `:latest` or `:edge` explicitly
  warns it moves on release / main HEAD and recommends `:vX.Y.Z`
  for production.
- **S-25** `ghcr-retention.md` states the policy verbatim: "untagged
  > 30 days deleted; tagged never auto-deleted" (PA-31).
- **S-26** `ghcr-retention.md` includes the `gh api` verification
  snippet with the "run quarterly" annotation.

### Release / branch-protection rules

- **S-27** `release-checklist.md` requires a manual smoke test that
  goes through a real RTMP publish (OBS or `ffmpeg`), not just
  `/livez`.
- **S-28** `release-checklist.md` includes a manual step to update
  `last-verified:` in all 7 ops docs (one bullet, six file paths).
- **S-29** `release-checklist.md` requires the cosign verify step
  against the operator's local clock (catches OIDC drift).
- **S-30** No doc contains "in v2 we will…", "future versions will…",
  "this will change in…", "if you're on legacy version", or
  "back-compat" / "migrating from" language. CI grep tripwires
  (negative matches) for both classes (Rule №2 at the doc layer).

---

## N. Carryovers, drifts, and Phase 12+ items

These are NOT blockers for Phase 12 ship; they are tracked here for
future hardening passes. Each is one of: (a) a drift between an ADR
and shipping code that needs reconciliation, (b) a small feature gap
that became visible while writing the ops surface, or (c) an
automation we deliberately deferred to keep Phase 12 doc-only.

### (a) ADR-0005 admin-bootstrap autogen drift

ADR-0005 §"Bootstrap" promises: *"if [RESTREAM_ADMIN_PASSWORD is]
absent, generates a random 20-char passphrase and prints it to stdout
exactly once."* Shipping code (`app/db/schema_init.py::_seed_initial_rows`)
does NOT implement this — if `RESTREAM_ADMIN_PASSWORD` is unset, the
admin row gets `password_hash=NULL`, `first_run_complete=0`, and the
operator cannot log in.

Phase 12 docs document shipping reality (`RESTREAM_ADMIN_PASSWORD` is
required on first boot). The drift must be reconciled in a follow-up
phase: either implement the autogen path in `_seed_initial_rows` OR
amend ADR-0005 §"Bootstrap" to match shipping. Either is acceptable
under Rule №2; not both.

### (b) Panel "test decrypt without revealing" affordance

Restore verification in `backup-restore.md` currently relies on
"start a brief test stream" as the end-to-end signal. A panel UI
that exposes per-credential "test decrypt OK / FAIL" without revealing
the key would make restore verification one click. Small Phase 13+
task; non-blocking.

### (c) Scheduled GHCR retention drift check

A scheduled GHA workflow that runs the `gh api` retention check
nightly and opens an issue on drift. Out of Phase 12 scope (Phase 12
is doc-only); Phase 13+ candidate.

### (d) Phase 11 carryovers still open

Phase 11 §N already logged five items; none gets new attention in
Phase 12:

1. Ingest-key leak CI smoke (Phase 5 property test is the gate).
2. `NGINX_RTMP_MODULE_TREE_SHA` git-tree-hash defense.
3. GPG verify nginx tarball as a CI gate.
4. arm64 runtime smoke under qemu fallback.
5. Renovate self-hosted script-pin gate.

### (e) Carryovers from Phases 9–10 still open

Same four items from prior session-handoff, none touched by Phase 12:

1. `first-run-complete` auto-flip in supervisor on first successful
   `live` transition.
2. YouTube backup-ingest toggle.
3. AuthReprompt grant-expired auto-retry.
4. HTTP sessions revoke reprompt.

---

## O. Acceptance criteria (extends CODE_PLAN.md Phase 12)

1. All 7 files exist under `docs/ops/` with the required
   front-matter (S-1).
2. `docs/ops/compose.yaml` exists and uses `:vX.Y.Z` pin,
   `stop_grace_period: 30s`, named `/data` volume.
3. All 30 invariants in §M grep-pass.
4. `docs/architecture/README.md` index updated to reference this memo.
5. `docs/CODE_PLAN.md` Phase 12 entry marked ✅ COMPLETE with file
   inventory, reviewer summary, and Rule №5 audit row.
6. `docs/SESSION_HANDOFF.md` Status table row 12 set to ✅; new
   "Phase 12 — what landed" section added.
7. `README.md` "Production deployment" line points at
   `docs/ops/deployment.md`.

---

## Closing note

Phase 12 closes the loop opened in Phase 0 (set up the prompts +
rules) by handing the project to an operator who has never seen the
codebase. The docs are the contract; the invariants in §M keep the
docs from rotting; the carryovers in §N are honest about the gaps
that remain. Per Rule №2, no doc anywhere promises a future migration
path — when v2 lands, the docs get rewritten, not patched.
