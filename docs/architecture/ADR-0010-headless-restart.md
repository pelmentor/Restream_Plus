# ADR-0010: Headless restart & master-passphrase availability

## Status
Accepted — 2026-05-15

## Context

Per ADR-0006, the master passphrase is the root of the in-memory KEK
that decrypts stream keys. The container refuses to start without it.

Software Architect review F# SA-B.passphrase / SA-E.headless flagged the
operational consequence: a homelab box that loses power at 3am restarts,
the container starts, the control plane refuses to function until the
user pastes the passphrase. The stream pipeline is down until the user
notices the next morning.

The original ADR-0006 had nothing on this. Two failure modes were
implicit:

1. Default to env-var, hope nobody puts secrets in `docker run -e`,
   accept the operational risk by silence.
2. Default to interactive prompt, accept the homelab failure mode by
   silence.

Neither is acceptable under Rule №1. The ADR must *name* the choice.

## Decision

**Two supported passphrase-source modes, user-selected via env. Default
is `env` (operational ergonomics first). Documentation makes the
trade-off explicit so the user picks consciously.**

### Mode 1 — `env` (default)

```bash
docker run -d \
  -e RESTREAM_PASSPHRASE_SOURCE=env \
  -e RESTREAM_MASTER_PASSPHRASE='...' \
  ...
```

- The control plane reads `RESTREAM_MASTER_PASSPHRASE` at boot.
- Derives the root KEK (Argon2id, see ADR-0006).
- Service comes up fully on container start. No human in the loop.

**Trade given:** the passphrase is visible to anyone with shell on the
host (`/proc/<pid>/environ`, `docker inspect`, shell history). For a
single-user appliance where shell-on-host = root-equivalent already,
this is an acceptable trade. The README and deployment doc say so
explicitly.

### Mode 2 — `paste` (opt-in)

```bash
docker run -d \
  -e RESTREAM_PASSPHRASE_SOURCE=paste \
  ...
```

- Container starts in a "locked" state.
- nginx-rtmp is **up**, but `on_publish` returns `HTTP 503` until
  unlocked — OBS publishes get rejected with a clear error tail. This
  prevents a strange race where streaming "works" but is unencrypted /
  unconfigured.
- The control plane HTTP server is up at `/api/unlock` (a dedicated
  endpoint that accepts `{passphrase}` only). `/api/auth/login`
  returns 503 `service_locked` while the process is locked — login
  and unlock are distinct flows operating on different identities
  (passphrase = KEK root of trust; password = admin user
  credential). An earlier draft of this ADR conflated the two; that
  is retracted per ADR-0005 §"Boot-time KEK availability and
  locked-mode endpoint surface".
- The web UI shows a single-purpose paste screen. After unlock:
  - Root KEK is derived and held in memory.
  - The control plane transitions to ready.
  - `on_publish` starts returning 2xx for valid ingest keys.
- The `/unlock` endpoint is rate-limited (3 attempts per IP per 15
  min) and gated behind a one-shot `unlock_attempt_token` issued in
  the paste screen to prevent CSRF-style attempts.

**Trade given:** every container restart needs a human. Homelab
appliance with power loss at 3am = pipeline offline until morning. The
user accepts this knowingly.

### Mode 3 — TPM / OS keyring (deferred to v2)

A future mode would have the host's TPM or OS keyring seal the
passphrase, unsealed by container start on the same host. Implementation
is platform-specific (Linux: TPM2 via `tpm2-tools` or systemd-credentials;
macOS: Keychain; Windows: DPAPI). Out of scope for v1; the env
selector reserves `RESTREAM_PASSPHRASE_SOURCE=sealed` for the future
value.

### What is *not* changed

- `RESTREAM_MASTER_PASSPHRASE` set via env is still ignored if
  `RESTREAM_PASSPHRASE_SOURCE=paste`. No silent fallbacks. We refuse
  ambiguity.
- The Argon2id parameters and HKDF derivations are the same in both
  modes (see ADR-0006). Only the *source* of the passphrase differs.
- Forgot-password flow (ADR-0005) is unchanged: stop the container,
  set `RESTREAM_RESET_ADMIN_PASSWORD=1`, restart. That only resets the
  admin password, not the master passphrase. Forgetting the
  passphrase means the stream keys are lost regardless of mode (this
  is correct cryptographic behavior — see ADR-0006).

### Documentation requirement

The deployment doc (`docs/ops/deployment.md`, to be written) MUST
include a comparison table of the two modes with operational impact
plain-spoken: "if you reboot for any reason in `paste` mode, your
restreamer is offline until you log in."

## Consequences

**Easier:**
- Power-loss recovery in `env` mode is automatic.
- Both modes share the same code path past the KEK derivation point.
- Choice is explicit, not implicit.

**Harder:**
- Two boot paths to maintain. Acceptable — they diverge only in the
  10 lines that read the passphrase.

**Rejected alternatives:**

- **Always require paste** — silently breaks homelab boxes. Not in the
  appliance's spirit.
- **Always use env** — silently leaks the passphrase to anyone with
  shell on the host without ever telling them. Not safe for the users
  who'd reach for paste mode if it existed.
- **Sealed mode in v1** — adds platform-specific code and host
  dependencies. Defer until the v1 surface stabilizes.

## Open questions / revisit triggers

- If a sealed-mode implementation lands (v2), this ADR is amended with
  the third mode and the documentation table grows a row.
- If the `paste` mode UI proves frustrating in practice, a one-shot
  "remember on this host for 30 days" option could be added — but
  that immediately collapses back to env-mode-with-extra-steps; it is
  *not* added by default.
