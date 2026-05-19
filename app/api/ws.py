"""WebSocket router + bus broadcaster — `/ws`.

Per Phase 6 design memo §Q3 / §Q4:

- **Cookie-only auth** (no bearer on WS for v1). The handler reads
  `__Host-rp_session` from `websocket.cookies` BEFORE `accept()`;
  invalid → `close(code=4401)`. Locked state → `close(code=4503)`.
- **Single drainer.** `ws_broadcaster(bus, registry)` is the only
  consumer of `bus.drain()` and runs in the API TaskGroup (the
  lifespan in `app/main.py` schedules it). It broadcasts each
  `BusEvent` to every connected client's queue (non-blocking).
- **Per-client backpressure.** Each WS handler owns a
  `Queue(maxsize=256)`. On `put_nowait` failure → sets
  `subscriber.overflowed=True`; the WS handler closes the socket
  with code 1011 on the next iteration. We do NOT drop-oldest at
  the per-client layer — that silently desyncs the client's view
  (per the design memo).
- **Initial state snapshot on connect.** Before forwarding any
  incremental event, the handler sends a synthetic `state.full`
  payload (current RunState + every Target's snapshot +
  `dropped_total`) so a reconnecting client gets a deterministic
  resync point.

Wire envelope (`schemas.WsEnvelope`): `{"v": 1, "event": "...",
"data": {...}}`. Versioned so a Phase-7 frontend pinned to v1 still
works after a future shape change.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.schemas import (
    WS_PROTOCOL_VERSION,
    RunStateView,
    TargetSnapshot,
    WorkerProgressView,
    WorkerSnapshotView,
    WsEnvelope,
)
from app.auth.key_material import KeyMaterialState
from app.auth.sessions import session_cookie_name
from app.config import AppSettings
from app.domain.target import Target
from app.domain.worker_state import WorkerSnapshot
from app.fanout.event_bus import (
    BusEvent,
    DropAlertEvent,
    EventBus,
    HostStatsEvent,
    RunStateChangedEvent,
    TargetSnapshotEvent,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.fanout.supervisor import Supervisor

_logger = structlog.get_logger(__name__)

router = APIRouter(tags=["ws"])

WS_PER_CLIENT_QUEUE_CAPACITY: Final[int] = 256
"""Per-client queue cap. Backed by the design memo §Q3 trade-off:
larger than this and a slow client holds a long tail; smaller and
ordinary network jitter triggers spurious overflows. 256 events at
~1Hz progress per worker is ~4 minutes of headroom, which is enough
for a network hiccup but not for a comatose client."""

WS_CLOSE_AUTH_FAIL: Final[int] = 4401
"""Custom close code (4000-4999 reserved-for-application range).
Distinguishes auth failure from a generic 1006 network error so the
client can render a meaningful message."""

WS_CLOSE_LOCKED: Final[int] = 4503
WS_CLOSE_OVERLOAD: Final[int] = 1011  # RFC 6455 "internal error"


@dataclass(slots=True, eq=False)
class WsSubscriber:
    """One connected WS client. Held in the broadcaster's registry.

    `eq=False` keeps the default identity-based `__eq__` + `__hash__`
    so subscribers can live in a `set[]` without two coincidentally-
    equal queues colliding."""

    queue: asyncio.Queue[BusEvent]
    overflowed: bool = False


@dataclass(slots=True, eq=False)
class WsRegistry:
    """Set of active subscribers + a lock for membership mutations.

    The broadcaster iterates a snapshot (so adds/removes during the
    iteration are safe), but the snapshot is taken under the lock so
    we never race a fully-emptied set.
    """

    subscribers: set[WsSubscriber] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def add(self, sub: WsSubscriber) -> None:
        async with self.lock:
            self.subscribers.add(sub)

    async def remove(self, sub: WsSubscriber) -> None:
        async with self.lock:
            self.subscribers.discard(sub)

    async def snapshot(self) -> tuple[WsSubscriber, ...]:
        async with self.lock:
            return tuple(self.subscribers)


# ----------------------------------------------------------------------
# Broadcaster — single drainer, scheduled by lifespan
# ----------------------------------------------------------------------


async def ws_broadcaster(bus: EventBus, registry: WsRegistry) -> None:
    """Drain `bus` and fan out to every subscriber's queue.

    Lives in the API TG (started by the lifespan). The drainer is the
    only consumer of `bus.drain()` (the bus is single-subscriber).
    On `put_nowait` failure we mark the subscriber overflowed; the
    per-client handler picks that up and closes the socket on its
    next loop iteration.
    """
    async for event in bus.drain():
        subs = await registry.snapshot()
        for sub in subs:
            if sub.overflowed:
                continue
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                sub.overflowed = True
                _logger.warning("ws_client_overflow")


# ----------------------------------------------------------------------
# /ws handler
# ----------------------------------------------------------------------


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """Upgrade, auth, snapshot, then forward events from the queue.

    Order of operations:
      1. Read cookie BEFORE `accept()`; close 4401 if missing/invalid.
      2. Check KeyMaterial; close 4503 if not READY.
      3. accept(); register subscriber; emit `state.full`.
      4. Loop: read one queued BusEvent → send envelope. On overflow
         flag, close 1011. On client disconnect, propagate cleanly.
    """
    auth_state = websocket.app.state.auth
    # WebSocket handlers can't use FastAPI Depends; pull settings directly
    # off app.state. Cookie name varies by `cookie_secure` mode (per
    # session_cookie_name docstring) so we must NOT hardcode it.
    settings: AppSettings = websocket.app.state.settings
    cookie_name = session_cookie_name(secure=settings.cookie_secure)
    cookie_value = websocket.cookies.get(cookie_name)
    if not cookie_value:
        await websocket.close(code=WS_CLOSE_AUTH_FAIL)
        return
    # Hex Audit BA-F19 (slice 10): same cookie-length cap as the REST
    # cookie-auth path (`app/auth/deps.py`). Reject over-long values
    # before HMAC; a 10 MB cookie smuggled through a permissive CDN
    # would otherwise burn HMAC cycles per WS handshake attempt.
    from app.auth.sessions import MAX_SESSION_COOKIE_VALUE_LENGTH as _MAX_COOKIE_LEN

    if len(cookie_value) > _MAX_COOKIE_LEN:
        await websocket.close(code=WS_CLOSE_AUTH_FAIL)
        return

    # Locked state: even with a valid cookie, refuse — the supervisor
    # has nothing to broadcast yet and the cookie verifier would 503
    # anyway via the require_unlocked_keys path.
    if auth_state.key_material.state != KeyMaterialState.READY:
        await websocket.close(code=WS_CLOSE_LOCKED)
        return

    # Verify the cookie directly via the persistence layer rather than
    # constructing a `SessionAuthService` — that would unnecessarily
    # hand the WS path a reference to the login rate-limiter (security
    # review H-2: a future verify_cookie change that touches the
    # limiter could let an unauthenticated WS hammerer DoS legitimate
    # logins).
    from datetime import UTC, datetime

    from app.auth.last_seen_coalescer import LAST_SEEN_TOUCH_TIMEOUT_SECONDS
    from app.auth.sessions import compute_session_token_hash
    from app.repositories.sessions import HttpSessionsRepository
    from app.repositories.users import UsersRepository

    keys = auth_state.key_material.require_ready()
    token_hash = compute_session_token_hash(cookie_value, keys.session_token_mac_key)
    sessionmaker = auth_state.sessionmaker
    coalescer = auth_state.last_seen_coalescer
    async with sessionmaker() as session:
        sessions_repo = HttpSessionsRepository(session)
        users_repo = UsersRepository(session)
        http_session = await sessions_repo.get_by_token_hash(token_hash)
        if http_session is None:
            await websocket.close(code=WS_CLOSE_AUTH_FAIL)
            return
        now = datetime.now(tz=UTC)
        if http_session.expires_at <= now:
            await sessions_repo.delete(token_hash)
            await session.commit()
            coalescer.forget(token_hash)
            await websocket.close(code=WS_CLOSE_AUTH_FAIL)
            return
        user = await users_repo.get_by_id(http_session.user_id)
        if user is None:
            await websocket.close(code=WS_CLOSE_AUTH_FAIL)
            return
        # Slice 7: coalesce + timeout-wrap the touch. WS reconnect spam
        # used to multiply this into one SQLite write per accept
        # (FG2-H1 unbounded touch, FG2-M7 no timeout); now it's at most
        # one write per LAST_SEEN_COALESCE_WINDOW_SECONDS, bounded by
        # LAST_SEEN_TOUCH_TIMEOUT_SECONDS, with the upgrade proceeding
        # even on timeout (the column is observability, not a guard).
        if coalescer.should_write(token_hash):
            try:
                await asyncio.wait_for(
                    sessions_repo.touch_last_seen(token_hash, now),
                    timeout=LAST_SEEN_TOUCH_TIMEOUT_SECONDS,
                )
                await session.commit()
                coalescer.mark_written(token_hash)
            except TimeoutError:
                # Reviewer slice-7 H-2: `wait_for` cancels the
                # in-flight UPDATE; the AsyncSession holds a
                # connection-level transaction that should be
                # explicitly rolled back before the connection
                # returns to the pool. `AsyncSession.__aexit__`
                # rolls back on close anyway, but the explicit
                # rollback documents the intent and removes any
                # ambiguity around partially-applied state.
                await session.rollback()
                _logger.warning(
                    "touch_last_seen_timeout",
                    session_token_fingerprint=token_hash[:4].hex(),
                    timeout_seconds=LAST_SEEN_TOUCH_TIMEOUT_SECONDS,
                    via="ws",
                )

    supervisor: Supervisor = websocket.app.state.supervisor
    bus: EventBus = websocket.app.state.event_bus
    registry: WsRegistry = websocket.app.state.ws_registry

    # Accept BEFORE registering the subscriber. If `accept()` raises
    # (protocol negotiation failure), the broadcaster would otherwise
    # keep enqueuing events into an orphaned queue until the next
    # broadcaster iteration prunes via the unregister path — but the
    # unregister only runs in the `finally` below, so events could
    # pile up in a dead queue between `add` and the exception.
    # Reviewer H1: reorder accept→register so a failed accept never
    # leaks a live subscriber.
    await websocket.accept()
    subscriber = WsSubscriber(queue=asyncio.Queue(maxsize=WS_PER_CLIENT_QUEUE_CAPACITY))
    await registry.add(subscriber)

    try:
        await _send_envelope(websocket, "state.full", _build_state_full(supervisor, bus))
        await _forward_loop(websocket, subscriber)
    except WebSocketDisconnect:
        pass
    finally:
        await registry.remove(subscriber)


async def _forward_loop(websocket: WebSocket, sub: WsSubscriber) -> None:
    """Pump events from the subscriber's queue to the WS until closed."""
    while True:
        if sub.overflowed:
            await websocket.close(code=WS_CLOSE_OVERLOAD)
            return
        try:
            event = await sub.queue.get()
        except asyncio.CancelledError:
            raise
        envelope = _event_to_envelope(event)
        if envelope is None:
            # Unsupported event kind — skip silently rather than crash
            # the client's WS over a future-event we don't render.
            continue
        try:
            await websocket.send_json(envelope.model_dump(mode="json"))
        except WebSocketDisconnect:
            return
        except Exception:
            _logger.exception("ws_send_failed")
            return


def _event_to_envelope(event: BusEvent) -> WsEnvelope | None:
    """Map `BusEvent.payload` → wire-name + data dict."""
    payload = event.payload
    if isinstance(payload, RunStateChangedEvent):
        return WsEnvelope(
            event="run.state.changed",
            data={
                "new_state": payload.new_state.value,
                "previous_state": payload.previous_state.value,
                "cause": payload.cause,
                "at": event.at.isoformat(),
            },
        )
    if isinstance(payload, TargetSnapshotEvent):
        snapshots = tuple(
            _snapshot_to_view(s).model_dump(mode="json") for s in payload.snapshots_by_role
        )
        return WsEnvelope(
            event="target.snapshot",
            data={
                "target_id": payload.target_id,
                "ui_state": payload.ui_state.value,
                "snapshots_by_role": snapshots,
                "at": event.at.isoformat(),
            },
        )
    if isinstance(payload, HostStatsEvent):
        return WsEnvelope(
            event="host.stats",
            data={
                "cpu_total_pct": payload.cpu_total_pct,
                "cpu_by_target": [
                    {
                        "target_id": entry.worker_id.target_id,
                        "role": entry.worker_id.role.value,
                        "cpu_pct": entry.cpu_pct,
                    }
                    for entry in payload.cpu_by_target
                ],
                "rss_bytes": payload.rss_bytes,
                "ingest_kbps": payload.ingest_kbps,
                "at": event.at.isoformat(),
            },
        )
    if isinstance(payload, DropAlertEvent):
        return WsEnvelope(
            event="bus.drop_alert",
            data={"dropped_total": payload.dropped_total, "at": event.at.isoformat()},
        )
    # WorkerEvent goes through TargetSnapshotEvent projection at the
    # supervisor; if one ever reaches the bus directly, ignore here.
    return None


def _snapshot_to_view(s: WorkerSnapshot) -> WorkerSnapshotView:
    """Project a domain `WorkerSnapshot` into its WS-friendly view.

    Single source of truth for the projection — keeps the `target.snapshot`
    envelope and the `state.full` initial payload in sync. If a new field
    is added to either side, change here.
    """
    progress_view: WorkerProgressView | None = None
    if s.last_progress is not None:
        progress_view = WorkerProgressView(
            bitrate_kbps=s.last_progress.bitrate_kbps,
            fps=s.last_progress.fps,
            drop_frames=s.last_progress.drop_frames,
            speed=s.last_progress.speed,
            at=s.last_progress.at,
        )
    return WorkerSnapshotView(
        role=s.role,
        state=s.state,
        last_event_at=s.last_event_at,
        last_error=s.last_error,
        breaker_failures_in_window=s.breaker_failures_in_window,
        last_progress=progress_view,
    )


def _build_state_full(supervisor: Supervisor, bus: EventBus) -> dict[str, object]:
    """Compose the synthetic `state.full` payload sent on connect."""
    targets_view = tuple(
        TargetSnapshot(
            target_id=t.id,
            ui_state=t.ui_state(),
            snapshots_by_role=tuple(
                _snapshot_to_view(s)
                for role in t.expected_worker_roles()
                if (s := t.worker_set.role(role)) is not None
            ),
        )
        for t in supervisor.targets()
    )
    view = RunStateView(
        run_state=supervisor.run_state(),
        targets=targets_view,
        heartbeat_age_seconds=supervisor.heartbeat_age().total_seconds(),
        dropped_total=bus.dropped_total,
        run_state_changed_at=supervisor.run_state_changed_at(),
    )
    return view.model_dump(mode="json")


async def _send_envelope(websocket: WebSocket, event: str, data: dict[str, object]) -> None:
    envelope = WsEnvelope(event=event, data=data)
    with contextlib.suppress(WebSocketDisconnect):
        await websocket.send_json(envelope.model_dump(mode="json"))


# Suppress unused-import flag for symbols intentionally re-exported.
_ = (Target, WS_PROTOCOL_VERSION, logging)


__all__ = [
    "WS_CLOSE_AUTH_FAIL",
    "WS_CLOSE_LOCKED",
    "WS_CLOSE_OVERLOAD",
    "WS_PER_CLIENT_QUEUE_CAPACITY",
    "WsRegistry",
    "WsSubscriber",
    "router",
    "ws_broadcaster",
]
