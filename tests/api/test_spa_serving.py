"""SPA static-file mount tests.

The container builds the React SPA to `/app/web/dist/` and the FastAPI
app mounts it as a catch-all with a history-API fallback. v1.0.0 and
v1.1.0 both shipped WITHOUT this mount — the panel was unreachable in
a browser, only the JSON API responded. v1.1.1 added the mount; these
tests pin the contract so a future refactor can't silently drop it.

We construct a fake SPA tree under `tmp_path` and pass it to
`_mount_spa` directly, avoiding the production path lookup.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.main import _mount_spa
from fastapi import FastAPI
from starlette.testclient import TestClient


def _fake_spa(tmp_path: Path) -> Path:
    """Create a minimal SPA tree: index.html, /assets/main.js."""
    spa = tmp_path / "spa"
    spa.mkdir()
    (spa / "index.html").write_text(
        "<!doctype html><html><head><title>Restream+</title></head>"
        '<body><div id="root"></div></body></html>'
    )
    assets = spa / "assets"
    assets.mkdir()
    (assets / "main.js").write_text("console.log('hi');")
    (spa / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    return spa


def _app_with_spa(spa_dir: Path) -> FastAPI:
    """Bare FastAPI app + the SPA mount, no other routers.

    The mount must work without depending on the real lifespan,
    middleware, or auth stack.
    """
    app = FastAPI()
    _mount_spa(app, spa_dir)
    return app


def test_root_serves_index_html(tmp_path: Path) -> None:
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<div id="root">' in r.text


def test_assets_file_served_as_real_file(tmp_path: Path) -> None:
    # /assets/main.js must return the actual JS bytes, NOT the SPA shell.
    # If the catch-all served index.html for asset paths, the browser
    # would load index.html with `Content-Type: application/javascript`
    # and the SPA would never boot.
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    r = client.get("/assets/main.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_favicon_served_as_real_file(tmp_path: Path) -> None:
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.content.startswith(b"\x00\x00\x01\x00")


def test_spa_deep_link_falls_back_to_index_html(tmp_path: Path) -> None:
    # /settings/security has no on-disk file; the SPA fallback should
    # serve index.html so React Router can resolve client-side. Without
    # this, a hard refresh on any SPA route 404s.
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    r = client.get("/settings/security")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<div id="root">' in r.text


def test_api_prefix_returns_real_404_not_spa_shell(tmp_path: Path) -> None:
    # A 404 for /api/nonexistent must stay a 404 — silently serving the
    # SPA shell would hide frontend fetch typos behind an HTML body.
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    r = client.get("/api/nonexistent")
    assert r.status_code == 404


def test_internal_prefix_returns_real_404(tmp_path: Path) -> None:
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    r = client.get("/internal/foo")
    assert r.status_code == 404


def test_ws_path_returns_real_404(tmp_path: Path) -> None:
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    r = client.get("/ws")
    assert r.status_code == 404


def test_path_traversal_rejected(tmp_path: Path) -> None:
    # `../../etc/passwd`-style requests must NOT escape the SPA dir.
    # The TestClient normalises some `..` segments client-side, so we
    # test the most realistic vector: a relative path that resolves
    # outside the mount root.
    spa = _fake_spa(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("never serve this")
    client = TestClient(_app_with_spa(spa))
    # Sibling-of-SPA file via a literal `..` path component.
    r = client.get("/..%2fsecret.txt")
    # 404 is correct (the resolved path falls outside spa_dir).
    assert r.status_code in (404, 400)
    assert "never serve this" not in r.text


# The "create_app() must succeed when no SPA dir exists" case is
# implicitly covered by the rest of the test suite — every API test
# constructs the app via the same `create_app()` path, and CI runs
# pytest from a fresh checkout where neither `/app/web/dist/` nor
# `<repo>/web/dist/` exists (the frontend isn't built for backend
# tests). If the SPA mount were load-bearing for app construction,
# every test would fail; so its optionality is the test.


# v1.1.3 regression coverage — see docs/releases/v1.1.3.md.
# In v1.1.2, a relative `Navigate to="general"` in the SPA router
# compounded segments onto the URL on every catch-all hit
# (`/settings/foo/general/general/general/…`). The browser kept
# navigating until the resulting path exceeded ext4's 4096-byte
# filename limit, at which point the backend SPA fallback crashed in
# `Path.is_file()` → uvicorn returned 500. The frontend bug is fixed
# in web/src/pages/settings/index.tsx, but the backend must also
# refuse to crash on absurd inputs regardless of any client bug.


def test_extremely_long_path_does_not_crash(tmp_path: Path) -> None:
    # An obscenely long path (well past ext4's ENAMETOOLONG cap) must
    # not raise OSError from inside the handler. We accept either
    # index.html (current behavior — length cap short-circuits before
    # stat) or a real HTTP 404; what we MUST never see is HTTP 500.
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    long_segment = "general/" * 600  # ~4800 chars, well over 4096
    r = client.get(f"/settings/{long_segment}")
    assert r.status_code != 500
    assert r.status_code in (200, 404)


def test_path_with_unstattable_segments_does_not_crash(tmp_path: Path) -> None:
    # A path that resolves to a non-file inside spa_dir but trips
    # stat() with OSError (NUL byte, weird Unicode, etc.) must fall
    # through gracefully. We exercise the OSError branch by requesting
    # a path with a NUL byte, which Path.is_file() rejects on POSIX
    # and Windows alike.
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    # Starlette URL-decodes %00 — the resulting Path raises ValueError
    # on construction or OSError on stat; both must be caught.
    r = client.get("/settings/with%00nul")
    assert r.status_code != 500


def test_resolve_oserror_is_caught_and_returns_404(tmp_path: Path) -> None:
    # Pins the `resolve()` guard specifically — distinct from the
    # `is_file()` guard exercised by the long-path / NUL-byte tests.
    # Without this, a future refactor that moves the try/except off
    # the resolve() call site would silently regress: the long-path
    # test would still pass (length cap short-circuits) and the
    # NUL test might still pass (OSError can fire from stat() too),
    # leaving the resolve() guard untested.
    #
    # The 404 status (not 200) verifies the v1.1.1 invariant that
    # paths we can't reason about return an honest 404, not the SPA
    # shell — see app/main.py::_spa_fallback for the why.
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    with patch("pathlib.Path.resolve", side_effect=OSError("injected")):
        r = client.get("/settings/normal-path")
    assert r.status_code == 404


# Hex Audit FG2-C3 (2026-05-18): byte-length cap on top of the
# codepoint cap. A path of multi-byte UTF-8 codepoints that fits the
# 1024-codepoint cap can still exceed the safe byte budget when
# fsencoded — `Path.resolve()` then risks ENAMETOOLONG on Linux ext4.
# The handler falls through to index.html on either cap match.


def test_multibyte_path_under_codepoint_cap_but_over_byte_cap_falls_through(
    tmp_path: Path,
) -> None:
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    # 600 codepoints of 4-byte UTF-8 (Mathematical Bold Italic A, U+1D468)
    # = 2400 bytes — past the 2048-byte cap but under the 1024-codepoint
    # cap. Must not crash; must return the SPA shell (200) since the
    # path is well-formed but inadmissible.
    mathy_a = "\U0001d468"  # 4-byte UTF-8
    payload = mathy_a * 600
    r = client.get(f"/{payload}")
    assert r.status_code != 500
    # Cap fired → fell through to FileResponse(index_html).
    assert r.status_code == 200
    assert '<div id="root">' in r.text


def test_short_multibyte_path_still_resolves_normally(tmp_path: Path) -> None:
    # A modest multi-byte path well under BOTH caps still hits the
    # normal `resolve()` → `is_file()` flow (and falls through to the
    # SPA shell because the file doesn't exist).
    spa = _fake_spa(tmp_path)
    client = TestClient(_app_with_spa(spa))
    r = client.get("/\U0001d468/profile")  # 1 4-byte codepoint + ascii
    assert r.status_code == 200
    assert '<div id="root">' in r.text
