# Phase 11 design memo — CI/CD: GHA workflows + GHCR multi-arch publish

**Status:** Accepted (synthesis of Software Architect + Backend Architect agency-agent passes,
plus convergence resolutions; binding for Phase 11 code).
**Scope:** Three GitHub Actions workflow files + Renovate config + hash-pinned
pip lockfiles + tarball SHA-256 store + bootstrap scripts. Closes Phase 10
DA-4 (Renovate config), DA-5 (pip `--require-hashes`), DA-6 (tarball SHA-256).
**Authors:** synthesis by the implementing agent from Rule №3 parallel passes
2026-05-17.

---

## Rule №3 process

Two agency-agent personas were dispatched in parallel against a brief that
enumerated 22 + 14 design dimensions:

- **Software Architect** — owned workflow topology, trigger/concurrency matrix,
  multi-arch build strategy, caching, image tagging, branch protection,
  reversibility, latency budgets. Returned a 33-invariant set `PA-1..PA-33`.
- **Backend Architect** — owned supply-chain primitives: pip-compile workflow,
  cross-platform hash generation, tarball SHA-256 procedure, Renovate config,
  SBOM/scan/provenance/action-pinning, CI secrets surface, runtime smoke,
  reproducibility, failure-mode catalog. Returned an 18-invariant set
  `PB-1..PB-18`.

The synthesis below adopts both invariant sets and resolves the 10 explicit
convergence points (`G-1..G-10`) + 5 open questions (`J-1..J-5`) the two
agents flagged.

---

## A — Locked decisions (convergence resolutions)

| ID | Topic | Resolution | Rationale |
| --- | --- | --- | --- |
| **R-1** | Hash lockfile tool (J-1 / G-1) | `uv pip compile --generate-hashes --universal --python-version 3.12` | uv 0.11 is ~10–100× faster than `pip-tools`; `--universal` solves cross-platform hash gaps natively (single lockfile valid for x86_64 + aarch64). Both agents converged here. |
| **R-2** | `:latest` semantics (J-2 / G-8) | `:latest` moves on **tag push only**; `:edge` tracks main. CODE_PLAN.md acceptance amended. | Original CODE_PLAN wording conflated "image build workflow publishes to GHCR on main push" with "the `:latest` tag tracks main." The latter is an operator footgun: operators expect `:latest` = last stable release. SA's amendment is correct. |
| **R-3** | arm64 build venue (J-4 / G-4) | Native `ubuntu-24.04-arm` matrix runners. qemu remains the documented fallback (`PA-9` — change one runner label, no architectural reshape). | Rule №1: proper crutchless solution up-front. Native arm64 runners are free for public repos as of mid-2025 and cut main-build latency from 12-18 min (qemu) to 4-6 min (native). |
| **R-4** | Cosign signing (J-5 / G-3) | **Adopt in Phase 11.** Keyless OIDC via `sigstore/cosign-installer`. No key management. | Cost: ~20s per image; benefit: cryptographic, verifiable image signatures with GitHub as the OIDC issuer. SA's PA-18 invariant accepted in full. |
| **R-5** | Renovate vs Dependabot (G-5) | **Renovate.** | Both agents converged. Renovate's `customManagers` for `.github/checksums.env` regex bumping + `postUpgradeTasks` for lockfile regeneration are unique capabilities that Dependabot cannot match. Required for atomic version+SHA bumps. |
| **R-6** | Image vulnerability threshold (G-6) | HIGH+ blocks publish; `only-fixed: true`; MEDIUM reports non-blocking. | Both agents converged. Avoids the "30 unfixable LOW CVEs in glibc" treadmill while still gating on real risk. |
| **R-7** | Smoke failure semantics (G-10) | **Block publish.** No `:smoke-failed` tag. | Both agents converged. A publicly-pulled image with a known boot bug is worse than no image. |
| **R-8** | Checksums file location (D-7 vs B.1) | `.github/checksums.env`. | BA's location: CI-adjacent (lives alongside the workflows that consume it), and Renovate's `customManagers` regex matches files under `.github/` cleanly. |
| **R-9** | `NGINX_RTMP_MODULE_TREE_SHA` defense (BA PB-4) | **Defer to a future hardening pass.** | BA's git-tree-hash defense requires `git` in the `nginx-builder` stage (extra ~50 MB) and is overkill given Renovate's atomic bump flow. GitHub's tarball stability has held since their 2023 commitment. Tarball SHA-256 alone is sufficient for Phase 11. |
| **R-10** | `SOURCE_DATE_EPOCH` (BA F.1) | **Adopt.** Set from `git log -1 --pretty=%ct`. Output `rewrite-timestamp=true`. | Free reproducibility win (two builds from same commit → byte-identical digests). No cache impact. |
| **R-11** | `npm audit signatures` in Dockerfile (BA PB-15) | **Adopt.** Runs in `frontend-builder` stage. | Catches malicious npm publishes via npm's signed-package keyserver. ~5s cost. |
| **R-12** | Ingest-key leak smoke (BA E.2) | **Defer.** Requires ffmpeg + RTMP source synthesis in CI; high complexity for a redaction sink already covered by 1000-sample property test in `tests/fanout/test_redaction.py`. | The Phase 5 redaction test is exhaustive; the CI smoke would be a thinner, slower re-litigation. Phase 12 ops verification adds the synthetic publish path. |
| **R-13** | Nginx GPG verify (BA B.3) | Documented in `scripts/refresh-nginx-checksum.sh` as the manual procedure; **not a CI gate.** | The CI gate is the SHA-256 of the tarball pinned in `.github/checksums.env`. The GPG verify happens once, by a human, at bump time, before the SHA goes into checksums.env. This is the same trust chain as Debian's apt-secure model. |
| **R-14** | Phase 10 `NGINX_RTMP_MODULE_SHA` bump | `9879e1de32b2624e44ec8d99a9e286d59c41bd54` (Phase 10) is **NOT a real commit** in `arut/nginx-rtmp-module` — slipped through Phase 10 audit. Bump to tag `v1.2.2` = commit `23e1873aa62acb58b7881eed2a501f5bf35b82e9` (Dec 2024 stable release). | Phase 5 audit miss surfaces here. Image actually does not build today; Phase 11 also closes this defect. |

---

## B — Workflow topology + trigger matrix

### B.1 Files (PA-1)

Three top-level workflow files. No `workflow_call` reuse (premature abstraction
for three callsites with zero polymorphism — re-evaluate Phase 13+).

| File | Purpose |
| --- | --- |
| `.github/workflows/ci.yml` | PR + main-push validation: tests, lints, typecheck, frontend build, lockfile drift, action-pin enforcement, optional PR image smoke. |
| `.github/workflows/build-image.yml` | Main-push multi-arch build → `:edge` + `:sha-<short>` on GHCR. SBOM + SLSA provenance + cosign keyless signature. Runtime smoke (amd64). |
| `.github/workflows/release.yml` | Tag-push (`v*.*.*`) multi-arch build → `:vX.Y.Z` + `:vX.Y` + `:vX` + `:latest` + `:sha-<short>`. Same supply-chain artifacts as build-image. Tag immutability gate. |

### B.2 Trigger matrix (PA-2)

| Workflow | Triggers |
| --- | --- |
| `ci.yml` | `pull_request`, `push: branches: [main]`, `workflow_dispatch`. `paths-ignore: ['**.md', 'docs/**', 'LICENSE', '.gitignore', '.gitattributes']`. |
| `build-image.yml` | `push: branches: [main]`, `workflow_dispatch`. **No** path filter — every main commit gets `:sha-<short>` (bisect anchor). |
| `release.yml` | `push: tags: ['v*.*.*']`, `workflow_dispatch` with `tag` input. |

### B.3 Concurrency (PA-3)

```yaml
# ci.yml
concurrency:
  group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.sha }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

# build-image.yml + release.yml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false   # NEVER cancel a mid-flight publish
```

PR runs cancel on push-force; main builds and tag publishes never cancel.

### B.4 Runner pin (PA-7)

`ubuntu-24.04` everywhere (amd64). `ubuntu-24.04-arm` for arm64 jobs.
**No** `ubuntu-latest` — grep tripwire in `workflow-pins` lint job.

---

## C — ci.yml job topology + sub-5-min PR budget (PA-4, PA-33)

Five parallel jobs. PR feedback p95 budget = **5 min**. The Phase 10
backend test suite is 636 tests, Phase 7-9 frontend has 9 vitest tests.

| Job | Wall-clock target | Scripts |
| --- | --- | --- |
| `backend-test` | ≤ 4 min | `pip install -r docker/requirements-dev.hashes.lock --require-hashes` + `pip install -e . --no-deps`; `pytest -n auto` |
| `backend-lint` | ≤ 90 s | `ruff check .`, `ruff format --check .`, `mypy --strict app/` |
| `frontend` | ≤ 3 min | `npm ci` (cached); `npm run lint && npm run typecheck && npm run test && npm run build` |
| `backend-lockfile` | ≤ 30 s | Regenerate both hash lockfiles via uv into `/tmp`; `diff` against committed lockfiles; fail on drift. |
| `workflow-pins` | ≤ 10 s | Grep enforcement: every `uses:` line in `.github/workflows/*.yml` matches `@[a-f0-9]{40}( # .*)?$`; no `ubuntu-latest`. |

Optional non-blocking job: `pr-image-smoke` (amd64 build + boot probe) —
informational; not a required status check.

### C.1 Caching (PA-11, PA-12)

| Cache | Key | Restore-keys |
| --- | --- | --- |
| pip wheels | `pip-${{ runner.os }}-${{ hashFiles('docker/requirements*.hashes.lock', 'pyproject.toml') }}` | `pip-${{ runner.os }}-` |
| npm modules | `actions/setup-node@<sha>` built-in `cache: 'npm'`, `cache-dependency-path: web/package-lock.json` | n/a |
| Docker layer cache (image workflows) | buildx `type=gha,scope=${arch_tag}` (per-arch scopes — no shared cache) | n/a |

### C.2 PR safety (PA-5)

`pr-image-smoke` and `frontend` jobs do **not** request `secrets:` or
elevated `permissions:`. Fork-PRs cannot exfiltrate. Default workflow-level
permission is `contents: read`.

---

## D — Multi-arch build strategy + caching (PA-9, PA-10, PA-11)

### D.1 Matrix per-arch on native runners

```yaml
strategy:
  fail-fast: false
  matrix:
    include:
      - platform: linux/amd64
        runner: ubuntu-24.04
        arch_tag: amd64
      - platform: linux/arm64
        runner: ubuntu-24.04-arm
        arch_tag: arm64
```

Per-arch jobs push **by-digest only**
(`type=image,push-by-digest=true,name=ghcr.io/...,push=true`).

### D.2 Manifest merge

A separate `merge-manifests` job depends on both arch jobs, runs
`docker buildx imagetools create` to assemble the multi-arch manifest list
under human-readable tags. Tags never reference single-arch images directly.

### D.3 Reproducibility (PB-14, R-10)

```yaml
build-args: |
  SOURCE_DATE_EPOCH=${{ steps.epoch.outputs.value }}
outputs: |
  type=image,name=ghcr.io/...,rewrite-timestamp=true,push-by-digest=true,push=true
```

`SOURCE_DATE_EPOCH = $(git log -1 --pretty=%ct)` ensures deterministic image
digests across rebuilds from the same commit. Third parties can rebuild and
verify our published digest.

---

## E — Supply-chain integrity (D-7, D-13, D-10 → PB-1..PB-18)

### E.1 Tarball SHA-256 store — `.github/checksums.env` (R-8, PA-14)

Single source of truth for all upstream tarball pins:

```bash
# .github/checksums.env  (Renovate maintains via customManagers)
NGINX_VERSION=1.26.2
NGINX_SHA256=627fe086209bba80a2853a0add9d958d7ebbdffa1a8467a5784c9a6b4f03d738
NGINX_RTMP_MODULE_SHA=23e1873aa62acb58b7881eed2a501f5bf35b82e9
NGINX_RTMP_MODULE_TARBALL_SHA256=b688919355c0acccdda24eb83c6306df3d450cb0b13664f16b8e3d1f521c3bb5
S6_OVERLAY_VERSION=3.2.0.2
S6_OVERLAY_NOARCH_SHA256=6dbcde158a3e78b9bb141d7bcb5ccb421e563523babbe2c64470e76f4fd02dae
S6_OVERLAY_X86_64_SHA256=59289456ab1761e277bd456a95e737c06b03ede99158beb24f12b165a904f478
S6_OVERLAY_AARCH64_SHA256=8b22a2eaca4bf0b27a43d36e65c89d2701738f628d1abd0cea5569619f66f785
```

Workflow loads via `cat .github/checksums.env >> $GITHUB_ENV`, then passes
each as `--build-arg` to `docker/build-push-action`. The Dockerfile's
existing `WARNING: ... unset` branch (Phase 10 reviewer H1 mitigation) is
a **local-dev affordance only** — a CI guard step rejects empty values
before invoking the build (PA-13).

### E.2 Hash-pinned pip — `docker/requirements.hashes.lock` (PB-1, PB-2, PB-15, PB-17)

Generated by:

```bash
uv pip compile pyproject.toml \
    --generate-hashes \
    --python-version 3.12 \
    --universal \
    --output-file docker/requirements.hashes.lock
```

`--universal` mode emits hashes for all platforms the resolver can target;
the Dockerfile's `pip install --require-hashes` picks the matching wheel for
the running arch. Cross-platform gap (BA §A.2) handled natively.

Dev lockfile generated the same way with `--extra dev`:
`docker/requirements-dev.hashes.lock` (used by CI's `backend-test` job).

Dockerfile change (one line):

```dockerfile
COPY docker/requirements.hashes.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
```

The Phase 10 `docker/requirements.lock` is **deleted** (Rule №2 — no
migration baggage). Single artifact, single command, single invariant.

### E.3 Cosign keyless signing (R-4, PA-18)

```yaml
- uses: sigstore/cosign-installer@<sha>
- name: Sign image
  run: |
    cosign sign --yes \
      ghcr.io/${{ github.repository_owner }}/restream-plus@${{ steps.merge.outputs.digest }}
```

Uses GitHub OIDC token (no key management). Signature attestations go to
Sigstore Rekor public transparency log. Verifiers run
`cosign verify --certificate-identity-regexp '^https://github\.com/.../\.github/workflows/'`
`--certificate-oidc-issuer 'https://token.actions.githubusercontent.com'`.

### E.4 SLSA provenance + SBOM (PB-9, PB-10)

`docker/build-push-action` flags:

```yaml
provenance: mode=max   # SLSA L2 build provenance attestation
sbom: true             # SPDX SBOM attestation
```

Both attached as OCI artifacts. Combined with GHA OIDC-signed provenance,
reaches effective SLSA Level 3 (non-falsifiable provenance from a trusted
builder). ~50 KB attachment per image, no impact on pull size.

### E.5 Image scan — `grype` + `syft` (PB-11)

After build, before push:

```yaml
- uses: anchore/sbom-action@<sha>   # syft
  with: { image: <id>, format: spdx-json, output-file: sbom.spdx.json }
- uses: anchore/scan-action@<sha>   # grype
  with: { image: <id>, fail-build: true, severity-cutoff: high, only-fixed: true }
```

HIGH+ fixable CVEs block publish; unfixable CVEs report but don't block
(debian-slim baseline always has unfixable LOW/MEDIUM in glibc, etc.).

### E.6 Action pinning (PA-24, PB-6, R-5)

Every `uses:` pinned by full 40-char commit SHA, with trailing `# <semver>`
comment. Including first-party `actions/*` and `docker/*` (uniformity has
no downside). Enforced by the `workflow-pins` CI job grep.

### E.7 npm supply chain (PB-15)

`frontend-builder` Dockerfile stage adds `npm audit signatures` after
`npm ci`. Fails the build if any installed package's npm-published
signature fails verification.

CI's `frontend` job runs `npm audit --audit-level=high` separately
(non-blocking on local dev; blocking on PR via CI).

---

## F — Image tagging + GHCR auth (PA-19..PA-23)

### F.1 Tag policy (R-2)

| Event | Tags |
| --- | --- |
| Push to main | `:edge`, `:sha-<short-7>` |
| Tag `v1.2.3` | `:v1.2.3`, `:v1.2`, `:v1`, `:latest`, `:sha-<short-7>` |
| PR (build-only, not pushed) | none (loaded into local engine only) |

`:latest` moves **only** on tag push. `:edge` is the main-tracking tag.
`:vX.Y.Z` is **immutable** — CI fails on overwrite attempt
(`docker buildx imagetools inspect` precondition before push).

`docker/metadata-action` config:

```yaml
- uses: docker/metadata-action@<sha>
  id: meta
  with:
    images: ghcr.io/${{ github.repository_owner }}/restream-plus
    tags: |
      # build-image.yml
      type=edge,branch=main
      type=sha,format=short,prefix=sha-
      # release.yml — keep the `v` prefix on semver tags to match the
      # operator contract (deployment.md, ghcr-retention.md, README).
      # The `{{version}}` template strips the leading `v` by design;
      # `pattern=v{{version}}` composes it back. Stillborn #3 of v1.0.0
      # was caused by the bare-`{{version}}` form — image got published
      # at `:1.0.0` while every doc said `:v1.0.0`.
      type=semver,pattern=v{{version}}
      type=semver,pattern=v{{major}}.{{minor}}
      type=semver,pattern=v{{major}},enable=${{ !startsWith(github.ref, 'refs/tags/v0.') }}
      type=raw,value=latest,enable=${{ github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v') }}
```

(The `v0.*` exclude on the major tag avoids the broken-by-design `:v0`
multi-meaning during pre-1.0 development.)

### F.2 GHCR auth (PA-22, PA-23)

```yaml
# Workflow-level permissions block — explicit, not inherited
permissions:
  contents: read           # checkout
  packages: write          # GHCR push
  id-token: write          # cosign OIDC + SLSA provenance
  attestations: write      # provenance/SBOM upload
```

`GITHUB_TOKEN` only. No PATs anywhere (PB-13 + PA-22 grep tripwire).

---

## G — Renovate config + cadence (DA-4 closure, PA-25..PA-26, PB-16)

### G.1 Tool choice (R-5)

Renovate, not Dependabot. Justified by:

1. `customManagers` regex for `.github/checksums.env` (Dependabot has
   no equivalent).
2. `postUpgradeTasks` for automatic `requirements.hashes.lock` regen on
   `pyproject.toml` bump (Dependabot can't do this).
3. Grouping rules that scale beyond "one PR per dep".

### G.2 Grouping (PB-16)

| Group | Members | Cadence | Auto-merge |
| --- | --- | --- | --- |
| `docker-base-images` | `FROM` digests for `debian:12-slim`, `python:3.12-slim`, `node:22-alpine` | weekly | no |
| `github-actions` | every `uses:` line | weekly | patch + minor |
| `python-deps-crypto` | `argon2-cffi*`, `cryptography`, `cffi`, `pycparser` | as released; `minimumReleaseAge: 7 days` | **no** (ADR-0006 trust root) |
| `python-deps-runtime` | rest of `[project.dependencies]` | weekly; `minimumReleaseAge: 3 days` | patch only |
| `python-deps-dev` | `[project.optional-dependencies] dev` | weekly | patch + minor |
| `nginx-stack` | `NGINX_VERSION`, `NGINX_SHA256`, `NGINX_RTMP_MODULE_SHA`, `NGINX_RTMP_MODULE_TARBALL_SHA256` | as released | no (smoke against live RTMP) |
| `s6-overlay` | `S6_OVERLAY_VERSION` + three SHA values | as released | no |
| `npm-runtime` | `web/package.json` runtime deps | weekly | patch |
| `npm-dev` | `web/package.json` dev deps | weekly | patch + minor |

### G.3 Vulnerability fast-track

Separate `vulnerabilityAlerts` config fires immediately on GHSA/CVE
regardless of weekly cadence. Auto-merges patch-only CVE fixes in
`python-deps-runtime`, `python-deps-dev`, `npm-*` if tests pass.
**Never** auto-merges crypto, base-image, or nginx-stack security PRs.

### G.4 Cross-file bump (PA-26)

`scripts/regenerate-lockfile.sh` is invoked from Renovate's
`postUpgradeTasks` when `pyproject.toml` changes; it regenerates both
hash lockfiles via uv and commits to the same Renovate PR branch.

`scripts/refresh-checksums.sh` (similar mechanism) refreshes the four
SHA fields in `.github/checksums.env` when `NGINX_VERSION`,
`NGINX_RTMP_MODULE_SHA`, or `S6_OVERLAY_VERSION` changes. This is
the **only** mechanism that writes `.github/checksums.env`; human
edits are documented as "emergency override only".

---

## H — Build-time + runtime smoke (PA-27, PA-28, PA-29)

### H.1 Build-time smoke (8 checks per arch)

Run on every published image, both arches:

| # | Check | Backs invariant |
| --- | --- | --- |
| H1 | `docker image inspect <ref> --format='{{.Size}}'` → assert ≤ 420 MB | Phase 10 I-B3 |
| H2 | `docker run --rm <ref> getcap /usr/sbin/nginx \| grep -q cap_net_bind_service` | Phase 10 I-G2 + reviewer L3 |
| H3 | `docker run --rm <ref> nginx -t -c /etc/nginx/nginx.conf` | nginx-config syntax |
| H4 | `docker run --rm --entrypoint='' <ref> /opt/venv/bin/python -c "import app.main"` | venv intact, app importable |
| H5 | `docker run --rm --entrypoint='' <ref> sh -c '! find /opt/venv -name __pycache__ -type d \| grep -q .'` | no pycache bloat (I-B4) |
| H6 | `docker run --rm --entrypoint='' <ref> id restream \| grep -q "uid=10001"` | load-bearing uid (I-F1) |
| H7 | `docker run --rm --entrypoint='' <ref> stat -c '%U:%G' /data \| grep -qx "restream:restream"` | /data ownership |
| H8 | Entrypoint fail-fast: `docker run --rm <ref>` (no passphrase) MUST exit non-zero | Phase 10 I-17 |

### H.2 Runtime smoke (amd64 only on PR; both arches on tag releases)

Spin the just-built image with a synthetic passphrase; probe boot path:

```bash
PASS=$(openssl rand -hex 32)
docker run -d --rm --name smoke \
    -e RESTREAM_MASTER_PASSPHRASE="$PASS" \
    -p 18000:8000 -p 11935:1935 \
    "$IMAGE_REF"

# Poll /livez for up to 90s (Phase 10 HEALTHCHECK --start-period=60s + 30s slack)
for i in $(seq 1 18); do
    sleep 5
    if curl -fsS http://127.0.0.1:18000/livez >/dev/null 2>&1; then
        LIVE=1; break
    fi
done
[ "${LIVE:-0}" = "1" ] || { docker logs smoke; exit 1; }

# Direct s6 readiness verification
nc -z 127.0.0.1 11935 || { docker logs smoke; exit 1; }
HEALTH=$(docker inspect --format='{{.State.Health.Status}}' smoke)
[ "$HEALTH" != "unhealthy" ] || { docker logs smoke; exit 1; }

# I-D4: docker stop -t 30 graceful shutdown
time docker stop -t 30 smoke
```

Failure → hard block on publish (R-7). The arm64 runtime smoke runs on
the native arm64 runner (no qemu cold-argon2id penalty).

### H.3 What runtime smoke does NOT cover

Per R-12, no synthetic RTMP publish — Phase 5's 1000-sample redaction
property test already exhaustively covers the leak path. The CI smoke
proves the boot path; the unit test proves the redaction. Two separate
concerns, two separate gates.

---

## I — Branch protection contract (PA-30, PA-32)

Required status checks for `main` (set out-of-band in repo settings):

| Job | Required | Rationale |
| --- | --- | --- |
| `ci / backend-test` | ✅ | 636 tests must pass |
| `ci / backend-lint` | ✅ | ruff + mypy strict |
| `ci / frontend` | ✅ | tsc + eslint + vitest + bundle-size |
| `ci / backend-lockfile` | ✅ | Hash-lockfile drift gate |
| `ci / workflow-pins` | ✅ | SHA-pin enforcement |
| `ci / pr-image-smoke` | ⚠️ informational | 8-12 min cost; visible signal, not blocking |

Plus: "Require linear history" ON (Rule №1 culture; no merge commits on
main). "Require branches up to date" ON.

Workflow-file paths AND job IDs are part of the public CI contract;
renames need an ADR + branch-protection update in the same PR.

---

## J — Latency budgets (PA-33)

| Path | Budget | Defense |
| --- | --- | --- |
| PR feedback (required checks) | **< 5 min** p95 | Parallel jobs, aggressive pip/npm cache, no qemu, no image push |
| Main push end-to-end | **< 15 min** | Per-arch native runners; per-arch gha cache scopes |
| Tag release end-to-end | **< 20 min** | Same as main + ~20 s cosign + ~10 s manifest merge |
| PR image smoke (optional) | **< 10 min** | amd64 only, hot layer cache |

---

## K — Reversibility audit (PA-31, PA-32)

Sticky:
- `ghcr.io/<owner>/restream-plus` namespace (renaming = break every pin).
- Published `:vX.Y.Z` tags (immutable contract).
- `:latest` semantic (last release, NOT main).
- Workflow file paths + required-check job IDs (branch protection couples).
- Cosign OIDC issuer identity (= GitHub).

Reversible:
- `docker/requirements.hashes.lock` path (internal).
- GHCR retention policy (untagged > 30 days deleted; tagged never auto-deleted).
- Renovate config (any edit ships in a normal PR).
- arm64 runner choice (PA-9 — one label change).

---

## L — Failure-mode catalog (BA F.2 condensed)

| Failure | Alarm | Mitigation |
| --- | --- | --- |
| PyPI down → install fails | Workflow exits non-zero | `nick-fields/retry@<sha>` wrap, 3 attempts, exponential backoff |
| GitHub commit-zip 404 | `curl -fsSL` exit 22 inside Dockerfile | Documented escape: vendor-mirror to repo-owned cache (Phase 12+); for Phase 11, on-call within 24 h |
| Tarball SHA-256 mismatch | `sha256sum -c -` fails inside Dockerfile RUN | Loud, fail-closed. Treat as supply-chain incident until proven benign re-gzip. |
| qemu arm64 fallback >30 min | Job `timeout-minutes: 45` triggers | Acceptable on rare native-runner outage; surface to maintainer |
| GHCR rate-limit on push | 429 from docker push | `nick-fields/retry@<sha>` exponential backoff |
| Renovate PR breaks build | CI fails → branch protection blocks merge | Fail-closed by design |
| Upstream re-released same version with new SHA | Loud build failure | Renovate `minimumReleaseAge` gives window; human triage |
| SBOM generation OOM | Job fails | **Do not skip.** Supply-chain > publishing. Block; investigate. |
| `uv` non-determinism between versions | `backend-lockfile` diff fails | Pin uv version (`astral-sh/setup-uv@<sha>` with explicit `version:`); document in CONTRIBUTING |

---

## M — Locked invariants (consolidated PA-1..PA-33 + PB-1..PB-18 + R-1..R-14)

The implementation MUST encode each. Listed as testable one-liners.

### Workflow topology + triggers

- **PA-1** Three top-level workflow files: `ci.yml`, `build-image.yml`, `release.yml`. No `workflow_call`.
- **PA-2** `build-image.yml` runs on every push to main, no path filter. `:sha-<short>` is monotone-with-history.
- **PA-3** `cancel-in-progress: true` only on the PR path of `ci.yml`.
- **PA-7** No `ubuntu-latest` anywhere. `ubuntu-24.04` + `ubuntu-24.04-arm` only.

### CI safety + secrets

- **PA-4** Required status checks: `backend-test`, `backend-lint`, `frontend`, `backend-lockfile`, `workflow-pins`.
- **PA-5** PR workflow never produces a pushable image. `pr-image-smoke` has no `secrets:`.
- **PA-8** No third-party secrets. GHCR auth = `GITHUB_TOKEN` only.
- **PA-22** No PAT in any workflow.
- **PA-23** `permissions:` is workflow-level and explicit; no implicit `write-all`.
- **PB-13** CI uses `RESTREAM_MASTER_PASSPHRASE` only as `openssl rand -hex 32`, never sourced from `secrets`.

### Multi-arch + caching

- **PA-9** Arm64 build uses native arm64 runner; qemu fallback via runner-label change only.
- **PA-10** Per-arch jobs push by digest; `merge-manifests` assembles tags.
- **PA-11** Docker layer cache scopes are `gha:amd64` and `gha:arm64`. No shared scope.
- **PA-12** `pyproject.toml` invalidates pip cache; `package-lock.json` invalidates npm cache.

### Supply chain — tarballs

- **PA-13** CI fails if any value in `.github/checksums.env` is empty.
- **PA-14** `.github/checksums.env` is the single source of truth for tarball SHAs. Renovate bumps version + SHA atomically.

### Supply chain — pip

- **PB-1** Dockerfile uses `pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock`. Supersedes Phase 10 I-A5.
- **PB-2** `docker/requirements.hashes.lock` exists; `docker/requirements.lock` does NOT.
- **PB-7** `backend-lockfile` job fails on drift between `pyproject.toml` and either hash lockfile.
- **PB-17** No `--no-deps` in pip invocation (`--require-hashes` is exhaustive).

### Supply chain — actions + provenance + scan

- **PA-24** Every `uses:` pinned by full 40-char SHA. `workflow-pins` CI job enforces.
- **PB-9** Every published image has SPDX SBOM as OCI attestation.
- **PB-10** Every published image has SLSA `provenance: mode=max` attestation.
- **PA-18** Every published image has a cosign keyless OIDC signature.
- **PB-11** Image scan with `severity-cutoff: high, only-fixed: true` runs before push; HIGH+ fixable CVEs block.
- **PB-15** `frontend-builder` Dockerfile stage runs `npm audit signatures`.

### Tagging + tag immutability

- **PA-19** `:vX.Y.Z` tags are immutable. CI fails on overwrite.
- **PA-20** `:latest` moves on tag push only. `:edge` tracks main.
- **PA-21** Every published image has a `:sha-<short-7>` tag.

### Renovate

- **PA-25** Renovate is the only dep-bump bot. No `.github/dependabot.yml`.
- **PA-26** `scripts/refresh-checksums.sh` (invoked from `postUpgradeTasks`) is the only mechanism that writes `.github/checksums.env`. Human edits = "emergency override only".
- **PB-16** Renovate's `python-deps-crypto` group has `automerge: false`.

### Smoke

- **PA-27** Eight build-time smoke checks (H1-H8) on every published image, both arches.
- **PA-28** Runtime smoke verifies `/livez=200`, RTMP port reachable, HEALTHCHECK not `unhealthy`, graceful `docker stop -t 30`.
- **PA-29** Runtime smoke is the externally-visible proxy for `_notify_s6_ready()` having fired.

### Reproducibility

- **PB-14** `SOURCE_DATE_EPOCH = git log -1 --pretty=%ct` + `rewrite-timestamp=true`. Same-commit rebuilds produce byte-identical digests.

### Latency + ops contracts

- **PA-30** Five required status checks (above). Documented in `docs/ops/branch-protection.md` (Phase 12).
- **PA-31** GHCR retention: untagged > 30 days deleted; tagged never auto-deleted.
- **PA-32** Workflow paths + job IDs are public CI contract.
- **PA-33** PR critical-path budget = 5 min p95.

### Phase 10 corrections

- **R-14** `NGINX_RTMP_MODULE_SHA` bumped to `23e1873aa62acb58b7881eed2a501f5bf35b82e9` (tag `v1.2.2`). The Phase 10 value did not exist on GitHub.

---

## N — Phase 11 → 12 carryover (open items)

These are NOT blockers for Phase 11 ship; they are tracked for Phase 12
(ops docs) or future hardening passes:

1. **Ingest-key leak CI smoke (R-12 deferred)** — Phase 5's property test
   covers redaction exhaustively; Phase 12 ops verification can add a
   synthetic RTMP publish path against the built image.
2. **`NGINX_RTMP_MODULE_TREE_SHA` defense (R-9 deferred)** — git-tree-hash
   verification of the extracted module source. Sufficient as a future
   hardening once `git` is acceptable in the nginx-builder stage.
3. **GPG verify of nginx tarball (R-13)** — manual procedure in
   `scripts/refresh-nginx-checksum.sh`; not a CI gate. Becomes load-bearing
   if upstream supply-chain hygiene degrades.
4. **arm64 runtime smoke under qemu (if native runner unavailable)** —
   PA-9 fallback covers the build; runtime smoke would need qemu acceptance
   of ~5-10× argon2id latency.
5. **GHCR retention policy enforcement (PA-31)** — set via GitHub UI per
   repo policy; Phase 12 documents the operator procedure.

---

**End of memo.** Implementation begins immediately; Rule №4 review on
the produced diff before Phase 11 is declared complete.
