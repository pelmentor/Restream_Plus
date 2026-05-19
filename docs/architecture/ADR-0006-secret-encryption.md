# ADR-0006: Secret encryption at rest — AES-256-GCM derived from master passphrase

## Status
Accepted — 2026-05-15

## Context

The system stores per-target **stream keys**, which are credentials that
allow broadcasting on the user's channels. They must:

- Never be logged.
- Never be returned to the client in plaintext after creation. The
  Settings UI shows a placeholder ("●●●● ●●●● ●●●● 7F2A") with last 4
  chars; "reveal" requires a password re-prompt that creates an
  ephemeral 60-second reveal session.
- Be encrypted at rest in the SQLite database.
- Not be re-encrypt-able by an attacker who steals the DB file alone —
  the encryption key must not live in the DB.

The master encryption key cannot be hardcoded, cannot live in env files
in plaintext that ship with the image, and cannot live in the volume
(otherwise stealing the volume = total compromise).

## Decision

**A master passphrase, supplied via the `RESTREAM_MASTER_PASSPHRASE`
environment variable on container start, is the root of trust. From it
we derive separate keys via HKDF for distinct purposes. Stream keys are
encrypted with AES-256-GCM using the derived stream-key wrapping key.**

### Key derivation

```
master_passphrase  (per ADR-0010: env var or paste-on-start)
        │
        ▼
   Argon2id(master_passphrase, salt = persistent random 16B in /data/.kdf_salt)
        │   parameters: time_cost=3, memory_cost=128MiB, parallelism=2
        ▼
   root_key  (32 bytes, lives in process memory only — never written)
        │
        ├── HKDF-SHA256(root_key, info="stream-key-wrap-v1")    → stream_key_kek
        ├── HKDF-SHA256(root_key, info="api-token-mac-v1")      → api_token_mac_key
        └── HKDF-SHA256(root_key, info="session-token-mac-v1")  → session_token_mac_key
```

The third info (`session-token-mac-v1`) is **not** the same primitive
as the dropped `session-cookie-mac`. The dropped one was for *signed
cookies* (Flask-style stateless sessions, MAC carried in the cookie
value) — we rejected that pattern in ADR-0005 in favor of opaque
server-side IDs. The new one is a keyed-HMAC fingerprint of the
opaque cookie value, stored in the DB at `sessions.token_hash`, so
that a database-only attacker (the threat that matters: stolen
backup, cloned volume) cannot use a leaked `token_hash` BLOB to forge
or replay a session — they have the fingerprint, not the cookie value
that produces it, and reversing the fingerprint requires the HMAC key
which lives in process memory only.

The principle of key separation (each purpose gets its own derived
key, so compromise of one does not compromise the others) is
load-bearing in this codebase; reusing `api-token-mac-v1` for the
session-cookie fingerprint would have worked cryptographically (the
256-bit cookie value makes cross-table brute search infeasible
regardless) but blurs the trust boundary for no gain. Per Backend
Architect review of Phase 3 auth: the extra `derive_subkey` call
costs nothing at boot and gives us a clean grep-able binding.

### Encryption of a credential (per ADR-0009, "Credential" is the
### entity; stream keys are one kind)

Inserting or updating a Credential:

```python
salt  = secrets.token_bytes(16)        # PER-RECORD random salt
nonce = secrets.token_bytes(12)
aad   = b"credential:stream_key:v1" + salt   # type discriminator + salt
ct    = AESGCM(stream_key_kek).encrypt(nonce, plaintext_key.encode(), aad)
db.store(target_id, salt=salt, nonce=nonce, ciphertext=ct,
         last4=plaintext_key[-4:])
plaintext_key = None  # let the GC reap it
```

- Nonce is unique per record. We do **not** reuse nonces.
- **AAD does NOT contain `target_id`** (per F# BA-4 amendment). The
  earlier draft used `target:{target_id}:stream_key:v1` as AAD, which
  meant a backup restored after the row was deleted-and-recreated
  could not be decrypted because the new `target_id` differed. With a
  per-record random salt + fixed-string type discriminator, the
  ciphertext is portable across exports/imports as long as the row
  itself is preserved (salt + nonce + ct together).
- `last4` is stored separately so the UI can show "●●●● ●●●● ●●●● XXXX".
- The plaintext is held in memory only long enough to encrypt. At
  worker-spawn time, the supervisor decrypts, formats the destination
  URL, passes the URL via the Worker's `argv`, and clears the local
  plaintext reference. The decrypted plaintext lives for ≤ tens of
  milliseconds.

### Why not the command line containing the key

ffmpeg has to receive the URL with the key concatenated. We have two
options:

1. Pass the full URL as the last argv element. On Linux, `argv` is
   visible via `/proc/<pid>/cmdline` only to processes running as the
   same uid (or root). Since the entire container runs as the
   `restream` uid (ADR-0004) and only the control plane and its
   workers run under it, this is acceptable.
2. Pass via stdin (`ffmpeg -i pipe:0` doesn't work for RTMP output —
   the URL is an output arg). Workaround: write a tiny named pipe
   trick or use ffmpeg's `protocol_blacklist` and an indirection. Not
   worth the complexity.

We choose option 1 and document it in the threat model. If the
container's user namespace is breached, the attacker already has the
key from memory anyway.

### What is encrypted

- ✅ Stream keys (per Credential, per ADR-0009).
- ✅ Future refresh tokens (same encryption path, per ADR-0009).
- ✅ API tokens (HMAC fingerprints under `api-token-mac-v1`, not
   encrypted ciphertext, but functionally equivalent — see ADR-0005).
- ✅ Session cookie values (HMAC fingerprints under
   `session-token-mac-v1` stored in `sessions.token_hash`; the cookie
   plaintext is never persisted — see ADR-0005). The cookie itself
   travels with `Secure; HttpOnly`, and a leaked session is revocable
   from the UI.
- ❌ Audit log entries, target labels, target URLs, settings flags.
   These are not credentials.

### Log redaction invariant (per F# SA-A leak)

Stream keys must **never** appear in any log file written under
`/data/logs/`. The invariant is enforced by:

- All worker stderr is piped through `RedactionSink` (ADR-0008).
- nginx access log uses a `redacted` format that strips URL paths
  under `/live/`.
- Application logs use a `structlog` processor that scans field
  values for known credential patterns and redacts them at
  serialization time (defense-in-depth — keys should not be logged
  at all, but a forgotten log line that includes one is caught
  before it hits disk).

This invariant is asserted in tests: a property-based test inserts a
random credential into a Worker's fake stderr stream and asserts the
resulting log file does not contain it. CI fails if the invariant
breaks.

### Migration / passphrase rotation — STOP-THE-WORLD operation

Per F# SA-C.rot: rotation cannot happen while workers are running,
because workers hold derived keys in memory and a mid-rotation
respawn would fail. Rotation is therefore explicitly a stop-the-world
operation:

1. RunState must be `offline`. If `live` or `armed`, the UI refuses to
   open the rotation dialog with copy: "Stop the stream before
   rotating the passphrase."
2. UI: Settings → Security → "Rotate master passphrase".
3. Form requires current passphrase + new passphrase.
4. Control plane sets a "rotation in progress" flag (rejects START
   attempts until done).
5. Derives both keys; walks `credentials` (per ADR-0009); for each,
   decrypts with old, re-encrypts with new (new salt, new nonce, new
   ciphertext).
6. Writes the new `.kdf_salt` atomically (write-then-rename) AND keeps
   the previous salt as `.kdf_salt.previous` for 24 hours, so that a
   backup taken in that window can still be restored against either
   passphrase. The previous-salt file is removed by a scheduled task
   24 h after rotation, and that removal is audited.
7. Clears the rotation flag. Service is ready for START again.
8. On next container restart, the user supplies the **new**
   passphrase. (In paste mode per ADR-0010, the unlock screen accepts
   only the new one.)

Compromised-passphrase response: the user should rotate, then *also*
re-issue stream keys at the platforms (Twitch / YouTube / Kick / VK
Live's per-session keys are session-scoped already), because the
attacker may have captured plaintext keys before rotation.

## Consequences

**Easier:**
- The DB file alone (e.g., backed up to S3) is useless without the
  passphrase. This is the threat model that matters.
- AES-256-GCM is one of the best-vetted symmetric primitives.
- HKDF cleanly separates keys by purpose. Compromise of one derived
  key does not compromise the others.
- Argon2id makes a passphrase brute-force expensive.

**Harder:**
- Losing the passphrase = losing the stream keys. The user has to
  re-add them. This is correct behavior; "forgot passphrase" recovery
  would mean we secretly stored the key somewhere, which defeats the
  encryption.
- Memory dumps of the running container expose `root_key` and derived
  keys. We mitigate (run as non-root, no swap, hardened container
  filesystem) but do not eliminate. Self-hosted, single-tenant — the
  threat is the DB backup, not the running RAM.

**Rejected alternatives:**
- **Sqlcipher (encrypted SQLite database file)**: encrypts everything
  uniformly. We only need to encrypt secrets. Sqlcipher's footprint
  in the Python ecosystem is patchier than `cryptography`'s AES-GCM.
  Also, the per-field approach with AAD gives us cleaner audit
  (see ADR-0007 §"Audit log scope" for the rotation/credential events
  this enables — `passphrase_rotation_started`,
  `passphrase_rotated`, `passphrase_rotation_orphaned`,
  `credential_set`, `credential_revealed`, `credential_cleared`,
  `kdf_salt_previous_cleared`; all carry `target_id` or operator
  context per the slice-7 BA-F5 AAD-v2 binding) and rotation semantics
  (per-record salt + nonce means a single row's ciphertext can be
  re-wrapped without rewriting siblings, and a backup taken before
  the rename can still be restored against either passphrase during
  the 24 h grace window).
- **Hashicorp Vault / external KMS**: enormous operational overhead
  for a single-user self-hosted app. The user does not run Vault.
- **Storing plaintext, relying on the volume being on an encrypted
  disk**: most users don't have full-disk encryption on a rented VPS,
  and even if they do, snapshots leak. Rejected.
- **Master key in env (instead of passphrase)**: subtly worse — env
  vars are easier to leak (process listings, shell history,
  docker-inspect). A passphrase forces the user to think "this is a
  password," and the Argon2id derivation gives us legitimate resistance
  to weak choices.

## In-process plaintext exposure (Phase 5 amendment, 2026-05-16)

ADR-0003 §"Ingest credential threat model" already accepts that the
**ingest** key is visible in the loopback ffmpeg's `/proc/<pid>/cmdline`,
on the basis that the same uid runs the entire container. This
amendment makes the same acceptance explicit for **outbound** stream
keys (Twitch / YouTube / Kick / VK Live / Custom):

- Phase 5's `FFmpegWorker` passes the plaintext output URL — which
  embeds the platform stream key — as the last positional argument to
  `asyncio.create_subprocess_exec`. The URL therefore lives in the
  kernel-readable `/proc/<pid>/cmdline` for the lifetime of each
  ffmpeg child.
- We accept this for the same threat-model reasons as ADR-0003: the
  entire container (control plane + ffmpeg children) runs under one
  uid, so any process inside the container that can read `/proc` can
  already read process memory directly and lift the plaintext from
  there. The argv exposure adds zero attack surface relative to the
  in-memory plaintext that AES-GCM's encrypt-at-rest model already
  trusts the running process with.
- A non-argv channel (writing the URL to a private pipe and using
  ffmpeg's `pipe:` syntax) is technically possible only for inputs;
  RTMP outputs are positional in ffmpeg's argv parser. A standalone
  helper that hands the URL via fd-passing would add complexity for
  no real-world threat reduction inside the container trust boundary.
- The defence-in-depth measures that DO matter and are in place:
  (a) `start_new_session=True` so SIGTERM does not bleed across
  process groups; (b) `close_fds=True` so DB / KEK fds do not
  inherit; (c) `RedactionSink` strips any incidental URL appearance
  in stderr; (d) `CredentialRegistry` strips it from any structlog
  event anywhere in the process.

The container boundary is the trust boundary. A host-side compromise
that reads `/proc/<container-pid>/cmdline` is a strictly larger
problem than this ADR can solve.

## Open questions / revisit triggers

- If the platform list grows to include things that issue OAuth tokens
  rather than static stream keys (e.g., Facebook Live), we'd need
  refresh-token storage, which has the same encryption shape but
  different lifecycle. The ADR survives.
- If the user reports "I lost my passphrase" repeatedly, we revisit
  with a clearer first-time-setup wizard, not with a back-door.
