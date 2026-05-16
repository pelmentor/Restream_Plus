# ADR-0009: Credential model and lifecycle

## Status
Accepted — 2026-05-15

## Context

The initial domain placed the "stream key" directly on the `Target`
aggregate as a field. This worked for Twitch, YouTube, Kick (long-lived
keys), and worked-with-an-asterisk for VK (single-use per broadcast,
wiped after each session — implemented by *not* exposing it in Settings
and prompting at START time, with field-clearing post-session).

Software Architect review (F# SA-A1) named the problem: VK's per-session
key was modelled as "a field we sometimes wipe" — a code branch that
crossed three contexts (UI / control plane / secrets). That kind of
coordination is exactly what aggregates exist to prevent.

A second adjacent concern: a future target type (e.g., a SaaS with
OAuth + WebRTC) would need refresh tokens with expiry. That's a
*third* credential lifetime, and the "branch on type" approach grows
quadratically with target type × credential lifetime.

## Decision

**Lift `Credential` out of `Target` into its own entity, with an
explicit `lifetime` policy. VK becomes a value of an enum, not a
special case in code.**

### Data model

```python
class CredentialLifetime(str, Enum):
    PERSISTENT     = "persistent"        # stored, reused (Twitch/YouTube/Kick/custom)
    PER_SESSION    = "per_session"       # collected at START, wiped at END (VK)
    REFRESH_TOKEN  = "refresh_token"     # OAuth-shaped, deferred to a future ADR

class Credential:
    id: CredentialId
    target_id: TargetId
    lifetime: CredentialLifetime
    ciphertext: bytes                    # AES-256-GCM, see ADR-0006
    nonce: bytes
    salt: bytes                          # per-record, see ADR-0006 amend
    last4: str                           # for UI display ("●●●● ●●●● ●●●● 7F2A")
    created_at: AwareDatetime
    expires_at: AwareDatetime | None     # used by REFRESH_TOKEN (None for others)
```

`Target` keeps URL, label, type, enabled flag, settings. It has
**one or zero** active Credentials (zero for VK with no pending
session; one for everyone else with a configured key).

### Target ↔ Credential interactions per lifetime

| Lifetime          | UI Settings form               | START flow                                     | END flow                       |
| ----------------- | ------------------------------ | ---------------------------------------------- | ------------------------------ |
| `PERSISTENT`      | Editable secret field          | Use stored credential                          | No change                      |
| `PER_SESSION`     | No secret field (info copy)    | Prompt inline on Dashboard before start        | Delete the credential row      |
| `REFRESH_TOKEN`   | OAuth connect button           | Use stored access token; refresh if expired    | No change (token persists)     |

The discriminator that picks which behavior is `target.type` →
`TARGET_TYPE_TO_CREDENTIAL_LIFETIME[target.type]`:

```python
TARGET_TYPE_TO_CREDENTIAL_LIFETIME = {
    TargetType.TWITCH:    CredentialLifetime.PERSISTENT,
    TargetType.YOUTUBE:   CredentialLifetime.PERSISTENT,
    TargetType.KICK:      CredentialLifetime.PERSISTENT,
    TargetType.VK_LIVE:   CredentialLifetime.PER_SESSION,
    TargetType.CUSTOM:    CredentialLifetime.PERSISTENT,
}
```

There is **one place** in the codebase that branches on this enum: the
`CredentialPolicy` module. UI components query
`policy.lifetime_for(target.type)` and render accordingly. The
supervisor queries it to know what to wipe at END.

### Encryption

Per ADR-0006 amended (see that ADR), `Credential` ciphertexts use a
per-record random `salt` field as AAD, not the row's primary key. This
makes `Credential` rows portable across DB exports/imports.

### "Stored key for next session only" advanced toggle (VK)

The original `ux-flows.md` § Settings spec for VK included an "advanced
toggle" allowing a one-shot stored key for headless start (e.g., a
schedule that calls `/api/run/start` via API token).

Under the credential model, that becomes a Credential with
`lifetime=PER_SESSION` stored in advance — the same code path as a
mid-flight paste, just persisted earlier. After the session ends, the
END handler wipes it as it would any per-session credential. No code
branch.

### Migration from the initial schema

There is none. We have no production data. Per Rule №2, the schema in
ADR-0007 ships with `targets` and `credentials` as separate tables from
day one. No "v1 with key-on-target, v2 with split."

## Consequences

**Easier:**
- VK is no longer a special case in three places. There is one
  `CredentialPolicy` enum-driven module.
- Adding a 4th lifetime (e.g., `EXCHANGEABLE_CODE` for upload tokens) is
  a new enum value plus a UI form variant.
- The "delete the credential" step at session end is a single
  operation against the credentials table, not a field-wipe on the
  target row.

**Harder:**
- One more table. One more join when fetching "the target with its
  credential" — but the query plan is a single indexed lookup; no
  perf concern at our scale.

**Rejected alternatives:**

- **Keep credential on Target with a `lifetime` field** — does not pay
  for itself; you still have the "wipe a field" code branch and you
  lose the ability for one Target to have multiple credentials over
  time (history would mix with active state).
- **Separate tables per lifetime** (`persistent_credentials`,
  `session_credentials`, `oauth_credentials`) — explodes the join
  surface. Rejected.

## Open questions / revisit triggers

- When the first OAuth target type lands, the refresh-token timing,
  expiry-detection, and token-endpoint URL handling get their own ADR
  (ADR-0009-supplement or ADR-0012, depending on scope).
- If we ever support credential history (audit "what key was active on
  2026-04-10?"), the `credentials` table grows a `superseded_at`
  column rather than a new table.
