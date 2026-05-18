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
