# Phase 9 — Frontend Settings + backend additions (locked design)

**Status:** Locked 2026-05-16. Binding for Phase 9 code.
**Synthesis of:** four agency-agent design passes — UX Architect, UI Designer,
Software Architect, Backend Architect — per Rule №3.

Phase 9 ships the entire Settings surface: SettingsShell + 9 tabs +
6 new design-system components + 6 new backend endpoints + 1 new
reprompt scope + 5 new error codes. No schema bump (Rule №2 holds).

---

## A. Module layout (frontend)

```
web/src/
├── pages/settings/
│   ├── SettingsShell.tsx           # sidebar + breadcrumb + <Outlet/>
│   ├── GeneralTab.tsx              # ingest key, idle timeout, log retention, TLS
│   ├── SecurityTab.tsx             # change-pwd, rotate-passphrase, API tokens, HTTP sessions, logout-all
│   ├── SessionsTab.tsx             # run history (sessions_history)
│   ├── AboutTab.tsx                # version, SHA, uptime, last reboots, logs path, diagnostics
│   └── targets/
│       ├── TwitchTab.tsx           # thin wrapper around <PersistentTargetForm type="twitch"/>
│       ├── YouTubeTab.tsx
│       ├── KickTab.tsx
│       ├── CustomTab.tsx           # list + slide-out add/edit form
│       └── VKTab.tsx               # different shape — per_session credential
├── components/
│   ├── settings/
│   │   ├── SettingsSidebar.tsx
│   │   ├── SettingsSection.tsx         # the section card pattern
│   │   ├── SettingsStatusRegion.tsx    # the single page-scoped aria-live
│   │   ├── PersistentTargetForm.tsx    # shared Twitch/YT/Kick/Custom shape
│   │   ├── VkTargetForm.tsx            # PER_SESSION variant
│   │   ├── StreamKeyField.tsx          # state machine §H
│   │   ├── UrlPresetSelect.tsx         # preset dropdown + Custom URL
│   │   ├── ApiTokensTable.tsx
│   │   ├── HttpSessionsTable.tsx       # NOTE: lives in SecurityTab (see §B)
│   │   ├── RunHistoryTable.tsx
│   │   ├── RotatePassphraseDialog.tsx
│   │   ├── ChangePasswordDialog.tsx
│   │   ├── IngestKeyRotateDialog.tsx
│   │   └── UptimeTicker.tsx
│   ├── SecretField.tsx             # generic; variants masked|entry|paste_only
│   ├── CopyToClipboard.tsx
│   ├── DestructiveConfirm.tsx      # popconfirm (Radix Popover)
│   ├── TypeToConfirmDialog.tsx     # high-stakes dialog (Radix Dialog)
│   ├── OneTimeRevealBanner.tsx
│   ├── Slider.tsx
│   ├── AuthRepromptDialog.tsx      # the modal body
│   └── AuthRepromptHost.tsx        # the singleton context provider
├── hooks/
│   ├── useSettings.ts
│   ├── useTargetsAdmin.ts          # create/update/delete + credential set/clear/reveal
│   ├── useApiTokens.ts
│   ├── useHttpSessions.ts          # list + revoke single
│   ├── useRunHistory.ts            # useInfiniteQuery (cursor)
│   ├── useAbout.ts
│   ├── useAuthReprompt.ts          # (scope) => Promise<grantId>
│   ├── useRotatePassphrase.ts      # composite
│   ├── useChangePassword.ts        # composite
│   └── useRevealStreamKey.ts       # composite (reprompt + reveal + 60s countdown)
└── lib/
    ├── queryKeys.ts                # NEW — central QueryKey constants
    ├── repromptScopes.ts           # NEW — Zod-typed scope union mirror of backend
    ├── formErrors.ts               # NEW — ApiError → react-hook-form mapping
    ├── targetTypeSpecs.ts          # NEW — frontend mirror of TARGET_TYPE_SPECS
    └── schemas/
        ├── settings.ts             # EXTEND
        ├── targets.ts              # EXTEND
        ├── apiTokens.ts            # NEW
        ├── sessions.ts             # NEW — HttpSessionView, RunHistoryItem/Page
        ├── auth.ts                 # EXTEND — RepromptIssueRequest/Grant, ChangePasswordRequest, RotatePassphraseRequest
        └── about.ts                # NEW
```

**No barrel files. Per-tab hooks (no generic data layer). T-suffix
type aliases for Zod-inferred shapes. Phosphor icons only.**

---

## B. Routing (locked)

```
/settings              → SettingsShell (lazy chunk)
   ├─ (index)          → <Navigate to="general" replace/>
   ├─ general          → GeneralTab
   ├─ targets          → <Navigate to="twitch" replace/>
   ├─ targets/twitch   → TwitchTab
   ├─ targets/youtube  → YouTubeTab
   ├─ targets/kick     → KickTab
   ├─ targets/vk       → VKTab
   ├─ targets/custom   → CustomTab
   ├─ security         → SecurityTab        (includes HTTP sessions sub-section)
   ├─ sessions         → SessionsTab        (run history)
   ├─ about            → AboutTab
   └─ *                → <Navigate to="general" replace/>
```

**Decisions:**
- ONE lazy chunk for `/settings/*` (no per-tab Suspense; Phase 8 at 168 KB
  gzipped + Phase 9's ~50 KB estimate fits in the 256 KB budget).
- ONE route per target type (NOT `/settings/targets/:type`).
- Active HTTP sessions live UNDER Security tab (UX recommendation —
  semantic home). `/settings/sessions` route is **run history only**.
  The naming clash is unavoidable but in-context never ambiguous.

---

## C. SettingsShell IA + visual lock

**Desktop (≥ 768 px):** CSS grid `240px minmax(0,1fr); gap var(--space-8)`.
Sidebar sticky inside `<main>`, max-w 1200 px shell unchanged. Main column
`max-width: 800px` (form readability — ~70 ch).

**Mobile (≤ 640 px):** horizontal scroll-snap tab strip sticky below header.
Section cards become borderless full-bleed (per UI Designer Q10).

**Tab order (locked):**
1. General
2. Targets (group → Twitch / YouTube / Kick / VK / Custom)
3. Security
4. Sessions (run history)
5. About

**Active state:** `bg-(--color-accent-faint)` + `[data-on-accent-faint]`
attribute (focus-ring fallback Phase 7) + inset-left `--color-accent` bar.
The bar is the color-blind-safe second signal.

**Breadcrumb:** "← Dashboard" link inside sidebar header on desktop;
inside the sticky tab strip on mobile.

**Page H1:** `--text-2xl font-semibold`. Optional subtitle `--text-sm
text-(--color-fg-muted)` underneath. Section heading is `--text-lg
font-semibold` inside the card header strip.

---

## D. Six new components (locked specs)

### D1. SecretField (variants masked | entry | paste_only)

- **masked:** displays `•••• •••• •••• 7F2A` (Unicode bullets, mono,
  tabular-nums). Eye icon click → `useAuthReprompt()` →
  `useRevealStreamKey()`. On grant: server returns plaintext, field
  shows it for 60 s with inline-right pill `Hides in 0:42` (mono,
  tabular-nums; flips to `--color-warn` at ≤10 s). At T=0, 200 ms opacity
  crossfade re-mask (instant under reduced-motion).
- **entry:** plain `<input type="password">` + show/hide eye toggle.
  Used inside OneTimeRevealBanner (pre-revealed) and Change-stream-key
  sub-form.
- **paste_only:** `autoComplete="new-password" spellCheck={false}`,
  no reveal toggle, no persistence. Used by Phase 8's InlinePromptCard
  (lifted to SecretField in Phase 9) and VK "store for next session".

ARIA: input `aria-label` = field's visible label (not "Password");
helper as `aria-describedby`; countdown announcements go to the
**page-scoped status region**, not a per-field region.

Lock icon (Phosphor `Lock`) + disabled state when KEK unavailable.
Disabled-state announcement: "Locked; rotate passphrase to edit."

`data-1p-ignore="true"` on all SecretFields (1Password should NOT
offer to save stream keys).

### D2. CopyToClipboard

State machine: `idle → copying → copied (1500 ms) → idle | failed (3000 ms) → idle`.

Inline `<span aria-live="polite" class="sr-only">` per instance for the
"Copied" / "Copy failed" announcement (this is the one allowed exception
to "page-scoped only" — hyper-local feedback never collides with
form-status).

Props (locked):
```ts
interface CopyToClipboardProps {
  readonly value: string;
  readonly variant?: "inline" | "standalone";
  readonly label?: string;       // defaults t("copy.idle")
  readonly context?: string;     // sr description e.g., "stream key"
}
```

Non-secure-context fallback: synthesize hidden `<span>` + select + show
"Press Ctrl/⌘+C" tooltip (Mac-detected via `navigator.platform`).

### D3. DestructiveConfirm (two flavors)

**Inline popconfirm** (Radix Popover): anchored to trigger; outside-click,
Escape, or 8 s inactivity → close + focus return. Confirm danger pill +
Cancel ghost. Used for: clear stream key, revoke API token, revoke single
HTTP session.

**Type-to-confirm dialog** (Radix Dialog, separate component
`TypeToConfirmDialog.tsx`): centered modal max-w-480, dialog-scoped
`aria-live="polite"` region announcing "X of N characters match" with
500 ms throttle. Confirm button disabled until exact match
(case-sensitive). Focus the input on open; return to trigger on close.

**Decision rule (locked):**
- Popconfirm for "lose ≤ 1 hour of work" (revoke single session, revoke
  single API token, clear stream key, disable target).
- Type-to-confirm for "lose multiple sessions or destroy data" (delete
  target, regenerate ingest key, rotate passphrase form does NOT use
  type-to-confirm — its TWO password fields ARE the ceremony, see §F.1).

**Phrases (locked):**
- Delete target: type the **target label** (case-sensitive).
- Regenerate ingest key: type `REGENERATE` (uppercase, fixed string).
- Logout everywhere: type `log out` (lowercase, fixed string).
- Rotate passphrase: NO type-to-confirm; the two password fields are
  the ceremony.

### D4. OneTimeRevealBanner

`bg-(--color-warn-faint)` + `border-l-4 border-(--color-warn)`. Composition:
icon + title + body + embedded SecretField (variant `entry`, pre-revealed)
+ CopyToClipboard adjacent + right-aligned `"I've saved it — dismiss"`
primary button.

**Focus on mount:** the CopyToClipboard button. Rationale: the user's
intent is "get this token into my clipboard". Pressing Enter immediately
copies + live region announces "Copied".

**Page status region** announces on mount: "New {what} created. Copy it
now; it will not be shown again." On dismiss: banner unmounts; parent
clears the value from local state (server already wiped).

### D5. Slider (Radix Slider)

24×24 thumb, 4 px track, `--color-accent` range. Inline numeric display
right-aligned (`tabular-nums`, `--font-mono`, `--text-sm`,
`--color-fg-strong`, 80 px column). `aria-valuetext` = "N seconds" with
human form at round minutes (e.g., "60 seconds (1 minute)").

### D6. AuthReprompt (Dialog + hook + singleton host)

**`<AuthRepromptHost>`** mounted ONCE inside `<RequireAuth>`, as a
**sibling of `<StatusStreamHost>` and `<AppShell>`** (matches Phase 8
pattern exactly).

**Hook (locked):**
```ts
export function useAuthReprompt(): (scope: RepromptScope) => Promise<string>;
```
Each call hits `POST /api/auth/reprompt` (no grant caching). Resolves
`grant_id` or rejects with `ApiError` (custom code `"cancelled"` for
user-cancel, `"reprompt_busy"` for parallel-call rejection).

**Dialog (Radix Dialog modal):** title + scope-specific subtitle + single
password input (`autoComplete="current-password"`, autoFocus) + Cancel +
Confirm. Inline dialog-scoped `aria-live="polite"` region for errors.

**Error envelopes:**
- 401 invalid_credentials → inline error, password input reset, re-focus.
- 429 → countdown banner inside dialog using `Retry-After`.
- Other → "couldn't reach server" inline.

**`silenceGlobalErrors: true`** on the reprompt POST so 401 doesn't
route to /login.

**Focus return:** the hook captures `document.activeElement` at
`request()` time and restores it on resolution (more robust than
Radix's default return).

**Auto-retry on destructive call's 403 REPROMPT_INVALID_OR_EXPIRED:**
ONE retry — re-prompt and re-fire. Second failure surfaces toast.
Implementation in the mutation's `onError` with an `alreadyRetried`
context flag.

---

## E. AuthReprompt contract (the central pattern)

```ts
async function onDestructiveClick() {
  let grantId: string;
  try {
    grantId = await reprompt("rotate_passphrase");
  } catch { return; }                            // user cancelled / busy / network
  await mutation.mutateAsync({ grantId, ...payload });
}
```

The destructive API call sends header `X-Reprompt-Grant: <grantId>`.

---

## F. Cross-tab flows (locked)

### F.1 Rotate passphrase

Form: TWO password fields — `current_passphrase` (master passphrase, the
secret that derives the KEK) AND the AuthReprompt asks for `password`
(admin password, ADR-0005). Two knowledge proofs because:

- Reprompt alone: stolen-cookie + stolen-admin-password attacker
  could re-key with their own passphrase, locking the operator out.
- Master passphrase alone: misses the cookie-binding defense (ADR-0005).

Flow:
1. User fills `current_passphrase`, `new_passphrase`, `confirm_new`.
2. Click `Rotate passphrase` → `await reprompt("rotate_passphrase")` →
   admin password dialog.
3. Mutation in flight (~1 s with backend timing floor + Argon2id):
   spinner + button label `"Rotating… (re-encrypting credentials)"`.
4. 204 → `queryClient.clear()` + `navigate("/login", { replace: true,
   state: { flash: "passphraseRotated" }})`.
5. `<LoginPage>` reads `state.flash`, renders Banner info "Passphrase
   rotated. Sign in with the new one."
6. After login, `safeNext(state.from)` returns to `/settings/security`.

Failure modes per backend §G:
- 401 INVALID_CREDENTIALS during reprompt → inline dialog error.
- 422 from `same_passphrase` / `string_too_short` → form field error.
- 409 RUN_ACTIVE → toast "Stop the stream before rotating the
  passphrase." Form does not submit.
- 500 → toast "Passphrase rotation failed; old passphrase still in
  effect." (Backend guarantees atomicity per §K.)

### F.2 Change password

Same skeleton, simpler. Form: old/new/confirm. Reprompt scope
`change_password`. On 204 → `queryClient.clear()` + navigate to /login
with flash `"passwordChanged"`. Banner "Password changed. Sign in with
the new one."

### F.3 Regenerate ingest key

1. Click `Regenerate` → TypeToConfirmDialog (`REGENERATE`).
2. On match → `await reprompt("regenerate_ingest_key")`.
3. POST `/api/settings/rotate-ingest-key` → `{plaintext, last4,
   grace_until}`.
4. `OneTimeRevealBanner` at top of tab. Masked field updates to new
   `last4` immediately on banner render.
5. Dismiss → banner unmounts. `["settings"]` invalidated.

### F.4 Reveal ingest key / reveal stream key

1. Click eye → `await reprompt("reveal_ingest_key" | "reveal_stream_key")`.
2. POST `/api/settings/reveal-ingest-key` or
   `POST /api/targets/{id}/reveal-credential` with
   `X-Reprompt-Grant`.
3. SecretField transitions to `revealed` state with 60 s countdown.
4. CopyToClipboard available adjacent.
5. At T=0 (or eye-click) → re-mask. Page status region announces.

### F.5 API token creation

1. "New token" → inline form replaces button.
2. Submit → POST `/api/security/tokens` → 201 `{plaintext, token: {...}}`.
3. OneTimeRevealBanner appears below form, above table.
4. Table re-renders with new row (Phase 8 invalidation pattern).
5. Dismiss → banner unmounts; inline form clears label but stays open
   (so creating multiple tokens is one click each after the first).

### F.6 Active HTTP sessions

- List under Security tab, separate section "Signed-in sessions".
- Each row: device summary (UA snippet), IP, last-seen relative, "this
  device" pill where `is_current === true`, Revoke button.
- Revoke button → inline popconfirm → DELETE
  `/api/security/sessions/{fingerprint}`. No reprompt (matches
  logout-all precedent).
- If revoking your own current session → next request 401 → bus →
  /login. Expected.
- "Log out everywhere" → TypeToConfirmDialog (`log out`) → POST
  `/api/auth/logout-all` → `queryClient.clear()` + navigate /login.

---

## G. State machines (frontend, locked)

### G.1 StreamKeyField

```
empty → masked (on first set or page load with last4)
masked → revealing (eye click → reprompt)
revealing → revealed (server returns plaintext)
revealed → masked (60 s elapsed OR eye click OR tab blur)
* → entering (Change-key button)
entering → masked (Save success — new last4)
entering → previous (Cancel)
```

Plaintext lives in local component state only. NEVER in TanStack cache.
NEVER in localStorage. Cleared on unmount via `useEffect` return.

### G.2 UrlPresetSelect

```
state: "preset" | "custom"
initial: presetUrls.includes(currentUrl) ? "preset" : "custom"
preset →(user picks "Custom URL…") custom
custom →(user clicks "Use preset") preset
```

Local state inside the component; form sees only the final `url` string.

---

## H. Per-target tab architecture (locked)

**`<PersistentTargetForm type="twitch|youtube|kick|custom">`** — ONE
shared component for all four. Reads `TARGET_TYPE_SPECS[type]` from
`lib/targetTypeSpecs.ts` (frontend mirror of `app/domain/target_types.py`).
Renders only the fields listed in `spec.fields`.

Sections per persistent-target tab:
1. **Status header** — platform icon + name + Enabled switch + "Last
   session" pill.
2. **Identity** — Label.
3. **Ingest URL** — preset dropdown + "Custom URL…" escape.
4. **Stream key** — StreamKeyField + Reveal + Change + Clear buttons.
5. **Danger zone** — Delete target (TypeToConfirmDialog).

**YouTube backup-ingest toggle** is **OUT OF SCOPE for Phase 9** (backend
worker model doesn't yet express "one target, two workers"; UX
Architect Q4). Punted to Phase 10 with its own ADR.

**`<VkTargetForm>`** is its own component. Same Sections 1–3 + an
**advanced disclosure** "Use a stored key for next session only" with a
SecretField (variant `entry`) + Save. Backend's `lifetime_for(vk_live)`
returns `per_session` regardless of client claim — server enforces.

**Custom target tab**: list of cards + "Add custom target" button →
slide-out form (Radix Dialog `modal={false}` like Phase 8) with same
fields as Persistent + URL is free-text only. Slide-out NOT a sub-route
(keeps `/settings/targets/custom` stable as the list view).

**Unsaved-changes guard:** intercept route nav with a small Dialog
"Discard changes?" with Cancel + Discard buttons. Locked.

---

## I. Backend additions (6 endpoints + repo additions)

### I.1 POST `/api/targets/{target_id}/reveal-credential`

Reprompt: `REVEAL_STREAM_KEY` (existing). Response:
```python
class RevealCredentialResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    plaintext: str
    last4: str
```
Handler: consume grant → load target → load credential → AEAD-decrypt
with `keys.stream_key_kek` + per-row salt → audit
`credential_revealed{target_id, actor, last4}` (NO plaintext) → return.

Missing credential (VK per_session between broadcasts) → 404
`CREDENTIAL_NOT_FOUND` (no special-case error).

### I.2 POST `/api/settings/reveal-ingest-key`

New scope: `REVEAL_INGEST_KEY = "reveal_ingest_key"`. Response:
```python
class IngestKeyRevealResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    plaintext: str
    last4: str
```
Handler: consume grant → load settings → audit `ingest_key_revealed{
actor, last4}` → return. (Plaintext lives in `settings.ingest_key_current`,
NOT AES-wrapped, because nginx-rtmp's on_publish webhook needs it.)

### I.3 POST `/api/security/rotate-passphrase` (the meaty one)

Reprompt: `ROTATE_PASSPHRASE` (existing). Request:
```python
class RotatePassphraseRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    old_passphrase: SecretStr = Field(min_length=1, max_length=4096)
    new_passphrase: SecretStr = Field(min_length=12, max_length=4096)
```
Response: 204.

**Handler logic** (order matters):

1. Consume reprompt grant.
2. RunState gate: if `supervisor.run_state() != OFFLINE` → 409 `RUN_ACTIVE`.
3. Acquire `rotation_lock` (asyncio.Lock on AuthState).
4. Validate: reject same passphrase → 422 `SAME_PASSPHRASE`.
5. Verify `old_passphrase` derives current KEK via new
   `KeyMaterial.verify_passphrase(old)` (constant-time compare on
   derived subkeys). Mismatch → 401 `INVALID_CREDENTIALS`.
6. Derive `new_root`, `new_stream_key_kek`,
   `new_api_token_mac_key`, `new_session_token_mac_key` from
   `new_passphrase` + fresh `new_kdf_salt`.
7. Write `.kdf_salt.previous` and `.kdf_salt.new` files
   (atomic-write, no rename yet).
8. Open ONE transaction (`async with session.begin()`):
   - For each credential row: AEAD-decrypt old → AEAD-encrypt new with
     fresh per-row salt → UPDATE row.
   - `HttpSessionsRepository.delete_all()` (revoke ALL sessions).
   - `ApiTokensRepository.delete_all()` (HARD delete — can't re-HMAC
     without the bearer plaintext, and `api_token_created`/`revoked`
     audit events already give the trail).
   - Commit.
9. Atomic `tmp.replace(.kdf_salt)` — single syscall, either succeeds
   or doesn't touch target. On failure: log CRITICAL
   `passphrase_rotation_orphaned` with old+new salts hex-encoded for
   operator recovery. (Manual recovery, single-user appliance.)
10. `KeyMaterial._rotate(new_kdf_salt, new_keys)` — atomic Python
    attribute assignment under GIL.
11. Audit: `passphrase_rotated{actor, sessions_revoked_count,
    api_tokens_revoked_count, credentials_rewrapped_count}`.
12. `response.delete_cookie(...)`; 204.
13. **Constant-time floor: 1.0 s** wrapping the whole handler.

**24-h cleanup task** for `.kdf_salt.previous`: new lifespan
`asyncio.create_task`. On startup, check existing file mtime and
schedule the rest of the window or unlink immediately.

### I.4 GET `/api/security/sessions`

Response:
```python
class HttpSessionView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id_fingerprint: str          # sha256(token_hash).hexdigest()[:16]
    created_at: AwareDatetime
    last_seen_at: AwareDatetime
    ip: str | None
    user_agent: str | None
    is_current: bool
```
No reprompt. New repo method
`HttpSessionsRepository.list_active_for_user(user_id, now) →
list[HttpSessionDTO]` filtering by `expires_at > now`. `is_current`
computed via comparison with `auth.session.token_hash`.

### I.5 DELETE `/api/security/sessions/{id_fingerprint}`

No reprompt (matches logout-all). Scan user's sessions for the
matching fingerprint, delete by `token_hash`. 404 `SESSION_NOT_FOUND`
if not found. Audit `session_revoked{actor, fingerprint}`.

If revoking own current session → `delete_cookie`; SPA's next request
401s → /login.

### I.6 GET `/api/about`

Response:
```python
class AboutView(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: str
    build_sha: str
    started_at: AwareDatetime
    uptime_seconds: float
    last_reboots: tuple[AwareDatetime, ...]
```

`started_at` captured in lifespan (`app.state.started_at`). `last_reboots`
derived from audit_log via new method
`AuditLogRepository.list_app_starts(limit=5)`. Lifespan emits
`app_started{version, build_sha}` so the current boot lands in the list.

No reprompt.

---

## J. Reprompt scope additions

```python
# app/auth/reprompts.py::RepromptScope
REVEAL_INGEST_KEY = "reveal_ingest_key"
```

No other additions. REVEAL_STREAM_KEY / ROTATE_PASSPHRASE /
REGENERATE_INGEST_KEY / CHANGE_PASSWORD / DELETE_TARGET /
REVOKE_API_TOKEN / CLEAR_CREDENTIAL all exist.

---

## K. New error codes

```python
# app/api/errors.py::ErrorCode
CREDENTIAL_NOT_FOUND = "credential_not_found"
NEW_PASSPHRASE_TOO_WEAK = "new_passphrase_too_weak"   # reserved for future server-side strength
SAME_PASSPHRASE = "same_passphrase"
RUN_ACTIVE = "run_active"
SESSION_NOT_FOUND = "session_not_found"
```

The `tests/api/test_errors.py` ERROR_CODES tuple pin will need updating.

---

## L. KeyMaterial API additions

```python
# app/auth/key_material.py

async def verify_passphrase(self, passphrase: str) -> bool:
    """Constant-time check: does `passphrase` derive the same
    DerivedKeys we hold in memory? Returns False if not READY or
    passphrase doesn't match. Compares all three subkeys via
    secrets.compare_digest."""

def _rotate(self, *, new_kdf_salt: bytes, new_keys: DerivedKeys) -> None:
    """In-process rotation. Single call-site (rotate-passphrase handler,
    after DB commit + salt-file rename). Two atomic attribute
    assignments — concurrent readers see either both-old or both-new."""
```

Concurrency: in-flight requests holding the OLD `DerivedKeys` reference
continue to use it. Since the same transaction that re-wrapped credentials
also revoked the in-flight request's session, the request is on its way
out anyway. Window is microseconds. Accept.

---

## M. AuthState / lifespan additions

- `AuthState.rotation_lock: asyncio.Lock` — gates concurrent rotate.
- `app.state.started_at: datetime` — captured at lifespan start.
- Lifespan emits `app_started{version, build_sha}` audit event.
- New lifespan `asyncio.create_task` for 24-h `.kdf_salt.previous`
  cleanup, with startup check.

Supervisor: `start_run` raises `RUN_ACTIVE` (409) if rotation_lock
held. (One-line check; the lock is a process-wide guard.)

**Slice 8 amendment (Hex Audit BA-F4 + FG3-F1, 2026-05-19 — SA2-F8
ADR closure).** The original lifespan `app_started` emit was wrapped
in `contextlib.suppress(Exception)` — the safety net was load-bearing
because the pre-slice-8 audit repo opened its own session AND ran
`PRAGMA wal_checkpoint(TRUNCATE)` on every append; an unclean prior
shutdown with a dirty WAL could OSError on the boot-time checkpoint
and crash-loop the container via s6 restart. Slice 8 removes BOTH
the `contextlib.suppress` (audit-write failure on boot is now
**fatal**, because the audit row is the only durable evidence the
process came up) AND the per-call checkpoint by passing
`checkpoint=False` to `append_standalone(...)` (the
`_audit_log_retention_loop` is the canonical WAL-hygiene actor; the
boot-time row defers its WAL truncate to the first retention tick).
Net: the boot row is durable, the WAL race is gone, and a degraded
DB at boot surfaces as one loud structured-log critical event and a
hard exit — not a silent crash-loop. See **ADR-0007 §"Audit-log
durability and transactional shape"** and the slice 8.5 burndown
entry for FG3-F1.

---

## N. Repository additions

- `CredentialsRepository.list_all_for_rewrap() → Sequence[CredentialDTO]`
- `CredentialsRepository.rewrap(id, salt, nonce, ciphertext) → None`
- `HttpSessionsRepository.list_active_for_user(user_id, now) →
   Sequence[HttpSessionDTO]`
- `HttpSessionsRepository.delete_all() → int`
- `ApiTokensRepository.delete_all() → int`
- `AuditLogRepository.list_app_starts(limit=5) → Sequence[AwareDatetime]`

No new tables. No schema bump. Rule №2 holds.

---

## O. Cache invalidation matrix (frontend)

| Mutation | Invalidates |
|---|---|
| PATCH `/api/settings` | `["settings"]` |
| POST `/api/settings/rotate-ingest-key` | `["settings"]` |
| POST `/api/settings/reveal-ingest-key` | **none** (read-only) |
| POST `/api/targets/{id}/reveal-credential` | **none** (read-only) |
| POST `/api/targets` | `["targets"]` |
| PATCH `/api/targets/{id}` | `["targets"]`, optionally `["run","state"]` |
| DELETE `/api/targets/{id}` | `["targets"]`, `["run","state"]` |
| PUT `/api/targets/{id}/credential` | `["targets"]`, `["run","state"]` |
| DELETE `/api/targets/{id}/credential` | `["targets"]`, `["run","state"]` |
| POST `/api/security/tokens` | `["settings","tokens"]` |
| DELETE `/api/security/tokens/{id}` | `["settings","tokens"]` |
| DELETE `/api/security/sessions/{fp}` | `["security","sessions"]` |
| POST `/api/auth/logout-all` | `queryClient.clear()` + navigate /login |
| POST `/api/security/rotate-passphrase` | `queryClient.clear()` + navigate /login |
| POST `/api/auth/change-password` | `queryClient.clear()` + navigate /login |

---

## P. Microcopy + messages.ts additions

New top-level namespaces in `web/src/messages.ts`:

- `settings.*` — page titles, subtitles, section headings, save-state strings.
- `secret.*` — SecretField labels, countdown announcements, lock helper.
- `copy.*` — CopyToClipboard states + announcements.
- `confirm.*` — DestructiveConfirm + TypeToConfirmDialog body + footer.
- `reveal.*` — OneTimeRevealBanner copy.
- `slider.*` — idle timeout label + helper + value-text format.
- `reprompt.*` — AuthReprompt title + scope-specific subtitles + errors.
- `login.passphraseRotatedBanner`, `login.passwordChangedBanner`.
- `auth.changePassword.*`, `auth.rotatePassphrase.*`.

**Verbs (locked):**
- `Save changes` for multi-field section saves.
- `Save key` / `Save for next session` for single-purpose sub-forms.
- `Create token` / `Add custom target` for entity creation.
- `Rotate passphrase` / `Change password` / `Regenerate` /
  `Delete target` — literal action verbs for destructive flows.
- `Discard` to cancel a dirty form (NOT "Cancel").
- `Cancel` for modals / popconfirms only.
- NEVER `Apply`, `Submit`, `OK`, `Update`.

**Warning copy pattern:** "Action verb you, consequence" — e.g.,
"Rotating the passphrase **revokes every session and API token**."

**Success toast wording:** past-tense verb + object (4 s auto-dismiss):
"Password changed.", "Passphrase rotated.", "Token revoked.", "Stream
key updated.", "Target deleted.", "Custom target added.", "Settings
saved." No emoji, no exclamation marks.

---

## Q. Accessibility invariants (Phase 9-specific)

1. **Page-scoped status region:** one `aria-live="polite" aria-atomic="true"`
   region per `<SettingsShell>` mount (NOT per tab). Owned by a hook
   `useSettingsStatusAnnouncer()`. Clears 5 s after each push.

2. **Dashboard's run-state badge region remains the single owner of
   that concern** (Phase 8 §W #1). Settings adds ONE form-status
   region (this one). Total app-wide live regions for distinct concerns:
   - run-state badge (Dashboard chrome — survives on Settings routes too)
   - page-title route-change announcer (Phase 7)
   - settings page-status (new this phase)
   - per-instance CopyToClipboard (D2 — local feedback exception)
   - dialog-scoped regions inside modals (DestructiveConfirm match
     counter; AuthReprompt error)

3. **AuthReprompt focus return:** capture `document.activeElement` at
   `request()` time; restore on resolution.

4. **OneTimeRevealBanner focus on mount:** the CopyToClipboard button
   (per UI Designer rationale: Enter immediately copies; SR users hear
   the primary affordance first).

5. **Type-to-confirm live region:** dialog-scoped, throttled 500 ms,
   announces on (a) first keystroke, (b) reaching exact match,
   (c) breaking previous match.

6. **SecretField countdown announcements:** discrete at 60s/30s/10s/0s
   only (NOT continuous), routed through the page-scoped status region.

7. **Sessions table is a real `<table>`** with `<thead>` and
   `<th scope="col">`. Not a `<div>` grid.

8. **Sliders honor `aria-valuetext` contract** per design-system §6.18.

9. **`react/jsx-no-literals` lint stays on** — every user-facing string
   via `t()`.

10. **Settings tabs are real ARIA tabs** (`role="tablist"` with
    `aria-orientation="vertical"` on desktop). Routing drives
    `aria-selected`. Arrow-key navigation + Home/End + auto-activate
    (route change on focus).

---

## R. Token additions (tokens.css)

ONE addition:

```css
--shadow-popover: 0 8px 24px rgb(0 0 0 / 0.12),
                  0 2px 4px rgb(0 0 0 / 0.06);
```

For the inline popconfirm + future per-tile context menu + AccountMenu
dropdown. Distinct from `--shadow-md` (card lift) and `--shadow-lg`
(modal/slide-out). Everything else composes from existing tokens.

---

## S. Bundle budget

Phase 8 = 168 KB gzipped of 256 KB. Phase 9 estimate:
- 6 design-system components: ~10 KB
- 9 page modules + composite hooks: ~25 KB
- react-hook-form + @hookform/resolvers + Zod schemas: ~12 KB (rhf
  already in deps; resolvers adds ~3 KB)
- Radix Slider + Popover + Toast: ~10 KB
- TanStack `useInfiniteQuery` glue: ~2 KB

Total estimate: ~60 KB → ~225 KB. Within budget; ~30 KB headroom.

If actual lands above 240 KB, the first lazy-split candidate is
`RotatePassphraseDialog.tsx` + `useRotatePassphrase.ts` (rarely used,
heavy form). Don't pre-split.

---

## T. Test plan (backend Phase 9)

New test files (one per endpoint):

- `tests/api/test_reveal_credential.py` (happy + REPROMPT_* + 404 +
  audit redaction).
- `tests/api/test_reveal_ingest_key.py` (happy + new scope binding +
  audit).
- `tests/api/test_rotate_passphrase.py` (happy 0/1/5 creds + wrong old
  + same_passphrase + weak + RUN_ACTIVE + atomicity failure injection +
  salt-rename failure path + post-rotate reveal works with new keys).
- `tests/api/test_security_sessions.py` (list + is_current + bearer-auth
  rows have is_current=false).
- `tests/api/test_security_sessions_delete.py` (happy + 404 + own-current
  yields cookie-deletion + audit).
- `tests/api/test_about.py` (version + dev-sha + uptime + last_reboots
  ordering + lifespan-emitted app_started).

Update `tests/api/test_errors.py` ERROR_CODES tuple to include the 5
new codes.

---

## U. Invariants Phase 9 must respect (numbered, 20)

1. Single `<AuthRepromptHost>` inside `<RequireAuth>` (sibling of
   `<StatusStreamHost>` and `<AppShell>`).
2. Every destructive mutation calls `await reprompt(scope)` BEFORE
   firing the destructive POST/DELETE.
3. The reprompt hook NEVER caches grants across calls.
4. Plaintext credentials (stream keys, ingest key, API tokens,
   passphrases) NEVER touch the TanStack cache.
5. `queryClient.clear()` + `navigate("/login")` on rotate-passphrase /
   change-password / logout-all success.
6. Server error envelopes drive UI; never invent client-side errors that
   masquerade as server errors.
7. `<RunStateBadge>`'s single `aria-live` region is owned by Dashboard
   chrome (Phase 7 §O #4 + Phase 8 §W #1). Settings adds ONE
   page-scoped region for form-status feedback.
8. `silenceGlobalErrors: true` on every Settings mutation AND the
   reprompt POST.
9. Cache invalidation table in §O is the source of truth.
10. `useInfiniteQuery` for cursor-paginated endpoints
    (`/api/sessions`).
11. VK target form uses the SAME `PUT /api/targets/{id}/credential`
    endpoint as other types; server's `lifetime_for(target.type)`
    enforces per_session regardless of client claim.
12. ONE route per target type (`/settings/targets/twitch`, …); NOT
    `/settings/targets/:type`.
13. `/settings` redirects to `/settings/general`. Unknown sub-paths
    under `/settings/*` redirect to General (not top-level NotFound).
14. Phosphor-only icons (Phase 8 §W #11 extends).
15. No new localStorage keys. Settings are server-side.
16. No barrel files.
17. ONE lazy chunk for `/settings/*` (not per-tab, not per-form).
18. Bundle stays under 256 KB gzipped.
19. All user-facing strings via `messages.ts::t()`.
20. Form submit success → toast for save-and-stay; `queryClient.clear()
    + navigate("/login")` for revoke-current-session flows.

---

## V. Out of scope (Phase 10+)

1. Bulk target import/export.
2. Credential backup (export encrypted bundle).
3. CSV/JSON export of run history.
4. Webhook integrations.
5. Full in-app log viewer.
6. Per-target settings beyond URL/key/label/enabled (bitrate caps,
   custom ffmpeg flags).
7. Multiple instances per named platform (Custom is the escape hatch).
8. Avatar / per-user theming.
9. Health-check page beyond About's Diagnostics button.
10. Audit log viewer (operators read via SQLite).
11. API token expiry / scopes.
12. Bulk session-history deletion.
13. YouTube backup-ingest toggle (backend worker model needs Phase 10
    ADR).

---

**End of Phase 9 design memo.** This document is binding for Phase 9
code. Deviations require an explicit deviation note in the implementing
PR description and a synthesis-pass amendment here.
