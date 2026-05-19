"""Internal MediaMTX webhooks — loopback auth + ingest key match.

Mirrors `test_internal_rtmp.py` but for the MediaMTX adapter
(`/internal/mtx/auth` + `/internal/mtx/lifecycle`). See
`app/api/internal_mtx.py` for the production / dev contract.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.domain.run_state import RunState
from app.fanout._testing import FakeSupervisor
from app.repositories.settings_repo import SettingsRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _current_ingest_key(sm: async_sessionmaker[AsyncSession]) -> str:
    async with sm() as s:
        return (await SettingsRepository(s).get()).ingest_key_current


# ----------------------------------------------------------------------
# /internal/mtx/auth
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_publish_with_correct_key_returns_200(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
    fake_supervisor: FakeSupervisor,
) -> None:
    key = await _current_ingest_key(api_sessionmaker)
    fake_supervisor._run_state = RunState.ARMED
    r = await client.post(
        "/internal/mtx/auth",
        json={"action": "publish", "path": f"live/{key}", "protocol": "rtmp"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert any(c[0] == "notify_obs_publish_began" for c in fake_supervisor.calls)


@pytest.mark.asyncio
async def test_auth_publish_with_wrong_key_returns_403(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        "/internal/mtx/auth",
        json={
            "action": "publish",
            "path": "live/not-the-key",
            "protocol": "rtmp",
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "ingest_key_invalid"


@pytest.mark.asyncio
async def test_auth_publish_with_malformed_path_returns_403(
    client: httpx.AsyncClient,
) -> None:
    # The handler insists on exactly `live/<key>` shape — the same
    # `application/<stream-name>` convention nginx-rtmp uses. Anything
    # else looks like an attacker probing for nested paths.
    # Also rejects shell metacharacters in the key portion — defence
    # in depth for the `runOnReady`/`runOnNotReady` curl hook in
    # `dev/mediamtx.yml` (which interpolates the path into a shell-
    # executed curl command). secrets.token_urlsafe() can't produce
    # these chars, so we tighten _extract_ingest_key to the same
    # charset and reject them at the auth gate regardless.
    shell_meta_paths = (
        "live/$(id)", "live/`whoami`", "live/key;rm",
        "live/key with space", "live/k\ny",
    )
    for bad in (
        "", "wrong", "live", "live/", "live/key/extra", "../etc",
        *shell_meta_paths,
    ):
        r = await client.post(
            "/internal/mtx/auth",
            json={"action": "publish", "path": bad, "protocol": "rtmp"},
        )
        assert r.status_code == 403, f"path={bad!r} → {r.status_code}"
        assert r.json()["detail"] == "ingest_key_invalid"


@pytest.mark.asyncio
async def test_auth_read_always_allowed(
    client: httpx.AsyncClient,
) -> None:
    # Reads are loopback-only by virtue of `_assert_loopback`. The
    # local ffmpeg fan-out workers pulling from MediaMTX don't need a
    # key, same as how nginx-rtmp's `allow play 127.0.0.1; deny play
    # all;` works.
    r = await client.post(
        "/internal/mtx/auth",
        json={"action": "read", "path": "live/anything", "protocol": "rtmp"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_auth_admin_actions_refused(
    client: httpx.AsyncClient,
) -> None:
    # MediaMTX's `api`, `metrics`, `pprof`, `playback` actions are
    # disabled at the config level in `dev/mediamtx.yml`; the
    # endpoint refuses them as a defence-in-depth so a misconfigured
    # mediamtx.yml can't accidentally expose them.
    for action in ("api", "metrics", "pprof", "playback"):
        r = await client.post(
            "/internal/mtx/auth",
            json={"action": action, "path": "", "protocol": "rtmp"},
        )
        assert r.status_code == 403, f"action={action} → {r.status_code}"


@pytest.mark.asyncio
async def test_auth_rejects_unknown_action(
    client: httpx.AsyncClient,
) -> None:
    # Pydantic's `Literal[…]` rejects unknown actions before our
    # handler runs. 422 is the FastAPI default for body validation
    # failures.
    r = await client.post(
        "/internal/mtx/auth",
        json={"action": "delete-everything", "path": "x", "protocol": "rtmp"},
    )
    assert r.status_code == 422


# ----------------------------------------------------------------------
# /internal/mtx/lifecycle
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_not_ready_notifies_supervisor(
    client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    fake_supervisor._run_state = RunState.LIVE
    r = await client.post(
        "/internal/mtx/lifecycle",
        data={"event": "not_ready", "path": "live/whatever"},
    )
    assert r.status_code == 200
    assert any(
        c[0] == "notify_obs_publish_ended" for c in fake_supervisor.calls
    )


@pytest.mark.asyncio
async def test_lifecycle_ready_is_informational(
    client: httpx.AsyncClient,
    fake_supervisor: FakeSupervisor,
) -> None:
    # `ready` is informational — the supervisor was already notified
    # during the auth hook. This endpoint stays 200 but emits no
    # `notify_obs_publish_began` (avoid double-counting).
    before = list(fake_supervisor.calls)
    r = await client.post(
        "/internal/mtx/lifecycle",
        data={"event": "ready", "path": "live/whatever"},
    )
    assert r.status_code == 200
    new_calls = [c for c in fake_supervisor.calls if c not in before]
    assert all(c[0] != "notify_obs_publish_began" for c in new_calls)
    assert all(c[0] != "notify_obs_publish_ended" for c in new_calls)


@pytest.mark.asyncio
async def test_lifecycle_unknown_event_is_422(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        "/internal/mtx/lifecycle",
        data={"event": "something_else", "path": "x"},
    )
    assert r.status_code == 422


# Loopback-rejection path — same reasoning as in
# `test_internal_rtmp.py`: httpx.ASGITransport always presents the
# request as coming from 127.0.0.1, so we cannot construct a
# non-loopback peer through this test transport. The handler reads
# `request.client.host` against `ipaddress.is_loopback` and the
# happy paths above exercise the loopback-accept branch. A dedicated
# unit test on `_assert_loopback` is a follow-up if a real-network
# exploit ever lands.


# ----------------------------------------------------------------------
# Hex Audit footgun-hunter F3/F4 (2026-05-18): body-size cap
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_rejects_oversized_body(
    client: httpx.AsyncClient,
) -> None:
    # A 10 MB JSON body must be rejected (413) BEFORE FastAPI parses it.
    # Without the entry-level cap, the body parser allocates the full
    # payload (and Pydantic per-field max_length runs too late to help).
    huge_path = "live/" + "A" * 10_000_000
    r = await client.post(
        "/internal/mtx/auth",
        json={"action": "publish", "path": huge_path, "protocol": "rtmp"},
    )
    assert r.status_code == 413


def test_assert_small_body_rejects_missing_content_length() -> None:
    # Direct unit-level check: chunked clients without Content-Length
    # are refused (411) — the cap can only be enforced when the wire
    # declares the body size up front. ASGITransport always backfills
    # a Content-Length so this scenario is unreachable through the
    # client fixture; we exercise the helper directly instead.
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.api.internal_mtx import _assert_small_body

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/internal/mtx/auth",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
    }
    req = Request(scope)
    with pytest.raises(HTTPException) as excinfo:
        _assert_small_body(req)
    assert excinfo.value.status_code == 411


def test_assert_small_body_rejects_oversized_content_length() -> None:
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.api.internal_mtx import MAX_INTERNAL_MTX_BODY_BYTES, _assert_small_body

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/internal/mtx/auth",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(MAX_INTERNAL_MTX_BODY_BYTES + 1).encode("ascii")),
        ],
        "client": ("127.0.0.1", 12345),
    }
    req = Request(scope)
    with pytest.raises(HTTPException) as excinfo:
        _assert_small_body(req)
    assert excinfo.value.status_code == 413


def test_assert_small_body_accepts_normal_content_length() -> None:
    from starlette.requests import Request

    from app.api.internal_mtx import _assert_small_body

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/internal/mtx/auth",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", b"128"),
        ],
        "client": ("127.0.0.1", 12345),
    }
    req = Request(scope)
    _assert_small_body(req)  # no exception


@pytest.mark.asyncio
async def test_lifecycle_rejects_oversized_form(
    client: httpx.AsyncClient,
) -> None:
    big_path = "A" * 200_000
    r = await client.post(
        "/internal/mtx/lifecycle",
        data={"event": "ready", "path": big_path},
    )
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_auth_rejects_oversized_path_field_via_pydantic(
    client: httpx.AsyncClient,
) -> None:
    # Layered defence: even when the body itself is under 4 KB, an
    # individual field above its per-field cap is rejected by Pydantic
    # validation (422). Keeps the contract "every str field is bounded."
    too_long_path = "live/" + "A" * 600  # over MtxAuthBody.path max_length=512
    r = await client.post(
        "/internal/mtx/auth",
        json={"action": "publish", "path": too_long_path, "protocol": "rtmp"},
    )
    assert r.status_code == 422


# ----------------------------------------------------------------------
# Hex Audit CR2-H1 (2026-05-18): MAX_INTERNAL_MTX_BODY_BYTES is Final
# ----------------------------------------------------------------------


def test_max_body_bytes_is_final() -> None:
    """The Final annotation is mypy-enforced; runtime introspection
    via `typing.get_type_hints` confirms the marker is present so a
    refactor that drops it would be caught at this layer too."""
    import typing

    from app.api import internal_mtx

    hints = typing.get_type_hints(internal_mtx, include_extras=True)
    assert "MAX_INTERNAL_MTX_BODY_BYTES" in hints
    # The hint's origin is `Final[int]` at this point.
    assert typing.get_origin(hints["MAX_INTERNAL_MTX_BODY_BYTES"]) is typing.Final
    # And the value is unchanged.
    assert internal_mtx.MAX_INTERNAL_MTX_BODY_BYTES == 4 * 1024


# ----------------------------------------------------------------------
# Hex Audit FG2-M3 (2026-05-18): _extract_ingest_key length cap
# ----------------------------------------------------------------------


def test_extract_ingest_key_rejects_overlong_path() -> None:
    """Early length check rejects oversized paths BEFORE splitting,
    bounded by `_MAX_PATH_FOR_KEY_EXTRACT`."""
    from app.api.internal_mtx import _MAX_PATH_FOR_KEY_EXTRACT, _extract_ingest_key

    overlong = "live/" + "a" * (_MAX_PATH_FOR_KEY_EXTRACT + 1)
    assert _extract_ingest_key(overlong) is None


def test_extract_ingest_key_accepts_normal_length() -> None:
    """Sanity: a normal `live/<22-char-key>` shape passes the cap."""
    from app.api.internal_mtx import _extract_ingest_key

    assert _extract_ingest_key("live/" + "a" * 22) == "a" * 22


# ----------------------------------------------------------------------
# Hex Audit BA-F1 (2026-05-18): rotation grace window enforcement
# ----------------------------------------------------------------------


async def _rotate_with_grace(
    sm: async_sessionmaker[AsyncSession],
    *,
    new_key: str,
    grace_until: datetime,
) -> str:
    """Rotate to `new_key`, returning the previous key (now in the
    grace window)."""
    async with sm() as s, s.begin():
        repo = SettingsRepository(s)
        before = await repo.get()
        await repo.rotate_ingest_key(new_key=new_key, grace_until=grace_until)
        return before.ingest_key_current


@pytest.mark.asyncio
async def test_auth_accepts_previous_key_within_grace(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
    fake_supervisor: FakeSupervisor,
) -> None:
    grace = datetime.now(tz=UTC) + timedelta(hours=24)
    previous = await _rotate_with_grace(
        api_sessionmaker, new_key="new-mtx-key", grace_until=grace
    )
    fake_supervisor._run_state = RunState.ARMED
    r = await client.post(
        "/internal/mtx/auth",
        json={"action": "publish", "path": f"live/{previous}", "protocol": "rtmp"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_auth_rejects_previous_key_past_grace(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    expired = datetime.now(tz=UTC) - timedelta(seconds=1)
    previous = await _rotate_with_grace(
        api_sessionmaker, new_key="new-mtx-key-2", grace_until=expired
    )
    r = await client.post(
        "/internal/mtx/auth",
        json={"action": "publish", "path": f"live/{previous}", "protocol": "rtmp"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "ingest_key_invalid"


def test_mtx_keys_match_grace_logic() -> None:
    """Unit-level: mirror of internal_rtmp grace logic."""
    from app.api.internal_mtx import _keys_match

    now = datetime.now(tz=UTC)
    assert (
        _keys_match("old", "new", "old", grace_until=now + timedelta(minutes=1), now=now)
        is True
    )
    assert (
        _keys_match("old", "new", "old", grace_until=now - timedelta(seconds=1), now=now)
        is False
    )
    assert _keys_match("old", "new", "old", grace_until=None, now=now) is False
    assert _keys_match("a", "a", None, grace_until=None, now=now) is True
