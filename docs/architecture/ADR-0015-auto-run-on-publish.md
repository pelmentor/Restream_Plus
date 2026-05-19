# ADR-0015: Auto-run-on-publish — OBS publishing is the only run trigger

## Status
Accepted — 2026-05-19

## Context

Phase 8 shipped a manual control plane: the dashboard's hero card
held a big START / STOP button that posted to `POST /api/run/start`
and `POST /api/run/stop`. The supervisor's `RunState` machine was
six-state — `OFFLINE → STARTING → ARMED → LIVE → STOPPING → ERROR` —
with `START_REQUESTED` and `STOP_REQUESTED` as the operator-driven
forward and reverse triggers.

The intermediate `ARMED` state was already publish-aware:
`notify_obs_publish_began()` (called by the internal RTMP webhook
in production and the MediaMTX `/internal/mtx/auth` webhook in dev)
flipped `ARMED → LIVE` when OBS started publishing, and the inverse
flipped `LIVE → ARMED` when OBS stopped. But the FSM's bracketing
transitions still required two extra operator clicks: one to spawn
workers before pointing OBS at the ingest, one to drain them
afterwards.

Operator feedback (slice-11): _"I don't want to press Start / Stop
every time. The OBS publish IS the signal. Mirror it."_

## Decision

**OBS publishing is the single trigger for the run lifecycle.** The
manual control plane is removed. Specifically:

1. **No manual REST endpoints.** `POST /api/run/start` and
   `POST /api/run/stop` are deleted. `GET /api/run/state` survives as
   the read surface. The supervisor exposes only `enable_target` /
   `disable_target` / `reset_target_worker` and the publish webhooks
   for state mutation.
2. **The drain owns the lifecycle.** When
   `_drain_pending_actions_locked` pops an `OBS_PUBLISH_BEGAN` action
   while `RunState == OFFLINE`, it calls `_start_run_locked()` (newly
   extracted from `start_run`, assumes `_lock` held) which drives
   `OFFLINE → STARTING → ARMED` and lets the existing logic finish
   `ARMED → LIVE`. When the drain pops `OBS_PUBLISH_ENDED` while
   `RunState in {LIVE, ARMED}`, it calls `_stop_run_locked(reason="publish_idle")`
   immediately — **no grace window**.
3. **No idle-timeout / grace period.** Publish-end means stop now.
   This is the operator's explicit choice; the simpler mental model
   ("OBS publishing = streaming") wins over the per-blip robustness
   of a debounced teardown.
4. **Frontend hero is status-only.** `HeroCard` shows a non-interactive
   `StatePill` (READY / STARTING / ARMED / LIVE / STOPPING / ERROR)
   plus the existing `HeroBody` (ingest URL, live stats). The
   `InlinePromptCard` for missing VK credentials renders automatically
   in `OFFLINE` whenever an enabled VK target lacks a key — the
   "Save" button persists the key only (it no longer triggers a
   start; the next OBS publish handles that).
5. **Audit-event vocabulary updated.** `run_stopped.data.reason`
   adds `publish_idle` (the canonical auto-stop reason) and drops
   `user_stop` (which had no remaining producer).
   `SessionsHistoryRepository.VALID_END_REASONS` becomes
   `{"normal", "publish_idle", "control_plane_crash", "error"}`.

The 6-state `RunState` FSM itself is unchanged. `ARMED` is now a
brief transient between worker-spawn and first-frame arrival (held
only as long as the workers take to come up before the drain
processes the queued `OBS_PUBLISH_BEGAN`); the `(LIVE,
OBS_PUBLISH_ENDED) → ARMED` legacy transition is unreachable in
auto-mode because the drain calls `_stop_run_locked` directly from
`LIVE`, skipping the `ARMED` interlude. We keep the transition
table entry as-is — it costs nothing and preserves the property-test
exhaustiveness.

## Rationale

### Why no grace window?

The "right" answer for a viewer-facing service is _some_ grace —
typically 15–30 s — so an OBS network blip or scene-switch glitch
doesn't tear down workers and respawn them, causing every viewer to
see a stream-drop on every platform. The operator explicitly rejected
this:

- Their dev box runs OBS and the MediaMTX side-car on the same
  Windows host (127.0.0.1). Network blips are essentially nil.
- The simpler "OBS publishing = streaming to platforms" model is
  worth more to them than the robustness gain of a debounced stop.
- The grace can be added later (the publish-end → stop pipeline is
  funneled through `_stop_run_locked(reason="publish_idle")` so a
  future debounce timer slots in cleanly without rearchitecting).

### Why keep `start_run` / `stop_run` as public methods?

The lifespan calls `await supervisor.stop_run(reason="normal")` on
process shutdown. Tests call `start_run()` / `stop_run()` directly
to drive deterministic state transitions without the webhook
plumbing. The public methods now just `async with self._lock:` and
delegate to the `_*_locked` form — they're a 4-line wrapper.

### Why not auto-clear-and-restart on rotation lock?

`_start_run_locked` raises `RunBlockedByRotationError` when a
passphrase rotation is in flight. In auto-mode this means: a
publish that arrives during rotation is silently dropped (the drain
suppresses the exception per the comment at the call site). The
operator must restart OBS after rotation completes.

This is acceptable because passphrase rotation is rare (operator-
initiated, deliberate). A retry timer that re-attempts after
rotation finishes would re-introduce the kind of asynchronous
"surprise start" behavior we just removed by replacing the button.

### Interaction with the lifetime spawn cap (FG2-H2)

`MAX_LIFETIME_SPAWN_ATTEMPTS = 1000` (slice 10) is the absolute cap
on worker respawns per `(target, role)` since process boot. With no
grace, a flaky OBS that bounces 10× a minute now also drives ~10×
the full-session cycles per minute, which means ~10× the credential
decrypt + worker spawn churn. The cap still kicks in at 1000
attempts — that's still about an hour-and-a-half of pathological
bouncing per target before the operator has to toggle the target
off+on. Documented here so the interaction is searchable.

### Interaction with VK per-session credential clearing

`_wipe_per_session_credentials` runs inside `_stop_run_locked` —
which is now called on every OBS disconnect. VK-Live's per-session
credentials are therefore wiped on every disconnect / reconnect
cycle. Operators of VK-Live should expect to re-fill the VK key in
the inline-prompt card after every OBS session-end (existing
phase-8 flow, now triggered more often). If this proves friction in
production, the follow-up is either:

  - debounce the wipe (separate from the run-stop debounce), or
  - relax the VK-key lifetime to "until app restart" (a security
    posture change that needs its own ADR).

### Why the `_spa_fallback` invariants still hold

The route-fallthrough order in `app/main.py` is unchanged. We
removed POST routes; the SPA-fallback only matches GET, so the
shadowing relationships described in ADR-0014 are unaffected.

## Consequences

### Positive
- One fewer interaction surface. Operator forgetfulness ("I clicked
  Start in OBS but not in the panel") is structurally impossible.
- Test surface shrinks: ~7 manual control-plane tests deleted; the
  remaining state tests use the publish webhooks as the canonical
  driver. `FakeSupervisor.notify_obs_publish_began` now drives the
  full forward path, mirroring the production drain.
- Hero card visual rhythm is preserved by a `StatePill` of the same
  visual mass as the old button; no layout shift.

### Negative
- Every OBS network blip causes a full session cycle: workers drain,
  VK credentials wipe, credentials re-decrypt, workers respawn,
  fanout reconnects to every target. Viewers see a 5–25 s drop on
  every target platform per blip (platform-dependent: YouTube ~20 s,
  Twitch ~5 s, others vary).
- VK-Live credentials require re-paste after every disconnect (see
  Rationale above). If the operator works with frequent OBS scene
  cuts that drop the publisher temporarily, this is painful.
- Recovery from `ERROR` state still requires explicit
  `ERROR_CLEARED` — which no longer has a UI control. Until the next
  iteration adds an in-card "Clear error" affordance, recovery is:
  process restart, or `state.error = false` via direct DB edit. The
  ERROR state is reached only on credential-decrypt or worker-spawn
  failure during `_start_run_locked`, so this is rare.

### Neutral
- `RunBlockedByRotationError` becomes a silent-drop in the drain,
  not a 409 in a REST handler. The structural shape (rotation-lock
  acquisition is atomic with the OFFLINE precheck inside `_lock`)
  is unchanged.

## Code references
- [app/fanout/supervisor.py — `_start_run_locked` / `_stop_run_locked` / `_drain_pending_actions_locked`](../../app/fanout/supervisor.py)
- [app/api/run.py — read-only `/api/run/state` only](../../app/api/run.py)
- [app/repositories/sessions_history.py — `VALID_END_REASONS`](../../app/repositories/sessions_history.py)
- [web/src/components/HeroCard.tsx — status-only hero](../../web/src/components/HeroCard.tsx)
- [web/src/components/InlinePromptCard.tsx — `onSave` (was `onUseAndStart`)](../../web/src/components/InlinePromptCard.tsx)
- ADR-0003 (RTMP ingest + fanout) — webhook contract (unchanged)
- ADR-0012 (MediaMTX dev side-car) — `/internal/mtx/lifecycle` (unchanged)
- ADR-0013 (audit event vocabulary) — `run_stopped.data.reason` update
