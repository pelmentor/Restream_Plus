# ADR-0012: MediaMTX dev-sidecar — RTMP ingest for Docker-less local dev

## Status
Accepted — 2026-05-19 (slice 9, retroactively documenting the
2026-05-18 implementation per Hex Audit SA-F6)

## Context

ADR-0003 picks nginx-rtmp (compiled with `nginx-rtmp-module` inside
the production Docker image) as the single ingest implementation.
That decision still holds in production. But the dev box (operator's
Windows 10 desktop, RTX 5060 + OBS) has no Docker. Running the full
image locally means either spinning up Docker Desktop (heavyweight
for a fast inner loop) or running the FastAPI app under `python -m
app.main` against `localhost:8000` — which works for everything
EXCEPT the RTMP ingest, because nginx-rtmp lives inside the image.

Before slice 8 of Phase 12 (2026-05-18) the dev box had no way to
run OBS → ingest → fanout end-to-end without building/pulling the
image. That made the supervisor + fanout + auth + redaction
integration impossible to smoke-test locally; the only loop was
"push to GHA, wait for the image, ship to Unraid, hope". This was
the latent cost behind the v1.1.0 / v1.1.1 / v1.1.2 / v1.1.3
release sequence — four shipped-and-broken releases in one day
because the operator had no fast local smoke loop.

MediaMTX is the obvious candidate: pure Go binary, single static
executable, supports RTMP as a first-class protocol, has an
HTTP-webhook auth mechanism (`authMethod: http`) that is contract-
compatible with our `on_publish` design. Phase 13's multi-track
exploration (ADR-0003 §"Open questions / revisit triggers") even
considered MediaMTX as a *production* replacement and deferred it
because of OBS + ffmpeg upstream blockers — but the dev-sidecar
use case has none of those blockers (operator sends single-track,
we accept single-track, ffmpeg fanout is unaffected).

## Decision

**Ship a dev-only MediaMTX sidecar, spawned from `start.py`, with
a loopback-only webhook contract that mirrors the production
nginx-rtmp `on_publish` / `on_publish_done` shape. NEVER substitute
nginx-rtmp in production; the sidecar is gated to `start.py` and
the operator's local dev box.**

### Sidecar layout

```
dev/
└── mediamtx.yml          # locked-down config (RTMP only, no API/metrics)

app/api/internal_mtx.py   # /internal/mtx/auth + /internal/mtx/lifecycle
                          # mirror /internal/rtmp/publish[+_done]

start.py                  # spawns MediaMTX as a child process AND
                          # the FastAPI app; both run under the same
                          # operator login; SIGTERM propagates to both
```

The control plane mounts the `/internal/mtx/*` router unconditionally
(production build included). The endpoints are loopback-gated identically
to `/internal/rtmp/*`: any non-loopback peer gets 403. In production
with nginx-rtmp running, MediaMTX simply isn't there to call them —
the routes exist but no traffic reaches them.

### `dev/mediamtx.yml` posture

Locked-down by design — every non-RTMP protocol and every API surface
is disabled:

| MediaMTX feature | Posture | Why |
| --- | --- | --- |
| `rtmp` | **on** (`:1935`) | the one feature we want |
| `rtmpEncryption` | off | LAN-only dev; OBS sends plain RTMP from the same host |
| `rtsp` / `hls` / `webrtc` / `srt` | off | none of them are part of the production contract; OFF removes the attack surface |
| `api` / `metrics` / `pprof` / `playback` | off | we don't read them, and a stray firewall hole on the dev box could expose them otherwise |
| `authMethod: http` | on, points to `http://127.0.0.1:8000/internal/mtx/auth` | every publish (and read) goes through the FastAPI auth webhook before MediaMTX accepts it |
| `runOnReady` / `runOnNotReady` | `curl` shim → `/internal/mtx/lifecycle` | MediaMTX's hooks are process-exec, not HTTP — the curl shim translates them into HTTP POSTs so the side effect (notify the supervisor) lives in Python, not in shell |
| `logLevel` | `warn` | matches production nginx-rtmp; `info` would log the ingest key in `[path live/<KEY>] runOnReady command started` and there is no built-in path-redaction equivalent to nginx-rtmp's `map $uri $safe_uri` |

### Webhook contract (mirrors `/internal/rtmp/*` per ADR-0003)

`POST /internal/mtx/auth` — MediaMTX `authHTTPAddress` adapter:

- Loopback-gated via FastAPI `dependencies=[Depends(_assert_loopback)]`.
- Body-size capped at 4 KiB BEFORE Pydantic validation (Hex Audit
  FG2-F3/F4). Legitimate MediaMTX auth bodies are <300 bytes.
  Chunked / missing `Content-Length` is rejected with 411.
- Body shape: `MtxAuthBody` (`action`, `path`, `protocol`, `ip`,
  `query`, etc.). Per-field `max_length` is layered defence behind
  the entry-level cap.
- `action == "read"` → loopback-only by route gate, allowed without
  further checks (the only readers are our ffmpeg fan-out workers
  in the same process tree).
- `action == "publish"` → extract ingest key from `path` (must
  match `live/<key>` with `[A-Za-z0-9_-]+` charset, ≤ 256 chars);
  compare timing-safe via `hmac.compare_digest` against
  `settings.ingest_key_current` + `previous` (BA-F1 grace window
  enforcement). On match, `supervisor.notify_obs_publish_began()`
  and return 200.
- Any other `action` → 403 (we never expose MediaMTX API/metrics/
  pprof through any auth path).
- In `paste`-mode-locked state → 503 `service_locked`.

`POST /internal/mtx/lifecycle` — `runOnReady` / `runOnNotReady` curl
target:

- Loopback-gated + body-size capped (same as auth).
- Form body: `event=ready|not_ready`, `path=<MTX_PATH>` (path is
  reserved for future per-stream routing, currently unused but
  preserved on the wire to anchor the FG2-F4 `max_length=512` cap).
- `event=not_ready` → `supervisor.notify_obs_publish_ended()`.
- `event=ready` → informational; auth webhook already notified.

### Why not the production ingest?

The dev sidecar is **deliberately not** a path toward making
MediaMTX the production ingest. Two blockers from ADR-0003's
Phase 13 exploration (still open as of 2026-05-19):

1. **OBS Custom Service has no
   `multitrack_video_configuration_url` field** → Multitrack Video
   toggle is greyed out for our endpoint regardless of which ingest
   server runs behind it.
2. **ffmpeg stable cannot emit Enhanced RTMP multi-track FLV
   output** → even if (1) cleared, we couldn't forward multi-track
   to Twitch from the Debian-packaged ffmpeg.

The empirical 2026-05-18 same-day test (operator built this exact
sidecar, tried OBS → `:1935` → fanout with Multitrack Video ON →
OBS failed "Output failure / No URL available for current service")
confirmed blocker 1 end-to-end. Blocker 2 is still upstream-pending.
The sidecar therefore stays in dev and **MUST NOT** be promoted to
production until both blockers clear AND a fresh ADR is written.

### What this ADR DOES NOT cover

- **Production ingest** — that's ADR-0003. Re-read it before
  considering any MediaMTX-as-production-ingest work.
- **Multi-track forwarding** — deferred per ADR-0003 §"Open
  questions" Phase 13 entry.
- **`start.py` cross-platform UX** — `dev/mediamtx.yml`'s
  `runOnReady` shim uses `curl`, which is shipped with Windows 10
  1803+. Pre-1803 hosts fail; documented in `dev/README` (when
  written). Other launcher concerns (passphrase resolution,
  ngrok/Cloudflare tunneling, dev-cert TLS) are out of scope here
  and tracked in `start.py` directly.
- **MediaMTX upstream version pinning** — `start.py` is responsible
  for the download/extract/verify flow; that's mechanical, not
  architectural.

## Consequences

**Easier:**

- The operator's inner loop is now `python start.py` → OBS push →
  fanout in ≤ 5 s. No Docker, no GHA round-trip, no Unraid push.
- Phase 12's "we shipped four broken releases in one day because
  there was no fast local smoke loop" failure mode is closed.
- The production webhook contract gets dual-implementation
  exercise: every change to `/internal/rtmp/publish` is checked
  against the same supervisor + auth + redaction codepath via
  `/internal/mtx/auth`. Divergence between the two is caught by
  the supervisor's behavior, not by reviewer eyeballing two router
  files.

**Harder:**

- Two webhook adapters to keep aligned. Mitigation: the auth
  helpers (`_assert_loopback`, `_assert_small_body`,
  `_extract_ingest_key`, `_keys_match`) are duplicated almost
  verbatim across `internal_rtmp.py` and `internal_mtx.py`; any
  drift would surface as a test failure since both routers share
  the supervisor + settings fixtures. If divergence accumulates,
  fold the shared helpers into `app/api/internal_common.py`.
- MediaMTX's process-exec hook model needs a curl shim, which adds
  one OS dependency (curl on Windows 10 1803+ ships it; older
  hosts need the manual install).
- The endpoints exist in production builds even though no
  production traffic reaches them. Acceptable: the loopback gate
  + 403 makes them safe-by-construction; removing them would
  require a dev-mode build flag, which is its own complexity.

## Rejected alternatives

- **Run nginx-rtmp directly on the dev box** — needs WSL2 or a
  bare-metal Linux dev machine. Operator's dev box is Windows; WSL
  adds its own networking quirks. Rejected.
- **Skip local RTMP smoke testing entirely** — the cost is "ship
  blind, find bugs on Unraid, push another release". The v1.1.0-1.1.3
  same-day cascade shows what this looks like. Rejected.
- **Bundle MediaMTX into the production image** — confusing (two
  ingest servers in one container; which one accepts the publish?),
  doubles the attack surface, and ADR-0003's reasons for nginx-rtmp
  in production still apply. Rejected.
- **Use the production Docker image locally on Windows via Docker
  Desktop** — works, but operator measured ~30-60 s inner-loop time
  vs ~3-5 s for the sidecar approach. The friction was load-bearing
  in the v1.1.x release cascade. Rejected for the dev inner loop;
  Docker Desktop is still the right answer for full-image smoke
  testing before tag-push.

## Open questions / revisit triggers

- If MediaMTX upstream changes the webhook body shape, our
  `MtxAuthBody` Pydantic model breaks the dev sidecar (production
  unaffected). Mitigation: the per-field validators reject anything
  that doesn't match the locked-in shape; a breaking change
  surfaces as 422 in `start.py`'s console, loud and obvious.
- If the operator moves to a Linux dev machine, the curl shim
  still works (Linux ships curl). The MediaMTX binary download
  switches platforms via `start.py` — mechanical, no ADR change.
- If the helpers diverge enough to be confusing, fold them into
  `app/api/internal_common.py` (named here so the next reader
  doesn't redo the analysis).
- If a future Phase 13 retry happens and the upstream blockers
  clear, this ADR is **replaced** (not amended) with a production-
  ingest ADR — the dev-sidecar posture and the production posture
  have different threat models and config postures, and conflating
  them in one ADR would muddy both.
