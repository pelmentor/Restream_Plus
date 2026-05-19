"""Local dev launcher (Windows, no Docker).

Three visible cmd windows:

1. **Backend** — `.venv/Scripts/python.exe -m app.main`. The local
   FastAPI control plane on `:8000`. Reads `RESTREAM_MASTER_PASSPHRASE`
   from `.env.local` (gitignored) and uses `.restream-plus-db/` as
   its data dir, so the operator's existing encrypted credentials,
   audit log, and KDF salt are visible. Backend code edits hot-reload
   the next time you restart the window.

2. **RTMP** — `dev/mediamtx.exe` listens on `:1935`. MediaMTX is a
   pinned, SHA-verified native Windows binary (no Docker, no install
   step). It speaks Enhanced RTMP and standard RTMP both. It's a
   DEV-ONLY tool: production uses nginx-rtmp inside the container
   (ADR-0003). When OBS publishes here, MediaMTX POSTs the auth
   payload to `http://127.0.0.1:8000/internal/mtx/auth` (we accept
   or reject by ingest-key match, same as the nginx-rtmp path). On
   stream end, MediaMTX spawns `curl` against
   `http://127.0.0.1:8000/internal/mtx/lifecycle` to notify the
   supervisor that publishing ended. See `dev/mediamtx.yml` for the
   wire shape.

3. **Frontend** — Vite dev on `:5173`, bound to `0.0.0.0` so LAN
   devices can hit it for HMR-on-source-edits.

What to point each client at:
- Browser (frontend iteration): http://<host>:5173/  (HMR over web/src/)
- Browser (backend-bundled SPA + prod fidelity): http://<host>:8000/
- OBS RTMP server: rtmp://<host-LAN-IP>:1935/live
- OBS Stream Key: paste the ingest key from Settings → General.

`web/dist/` is rebuilt at launch so the `:8000` URL serves the
current source SPA (not whatever stale bundle was last built).

`.env.local` MUST contain:

    RESTREAM_MASTER_PASSPHRASE=<your-unraid-passphrase>

If missing, the launcher exits with copy-pasteable fix instructions.
The file is gitignored.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# Pinned MediaMTX release. Update with care: the SHA-256 is verified
# at every launch, so a version bump requires updating BOTH values.
# Source: https://github.com/bluenviron/mediamtx/releases
MEDIAMTX_VERSION = "v1.18.2"
MEDIAMTX_URL = (
    f"https://github.com/bluenviron/mediamtx/releases/download/"
    f"{MEDIAMTX_VERSION}/mediamtx_{MEDIAMTX_VERSION}_windows_amd64.zip"
)
MEDIAMTX_ZIP_SHA256 = "945ab46c5fc6d2802ad18e2f1d7e49245ca5609657d85e310aa6eda4cdd72eec"
# Hex Audit CR-F11 (slice 10): pinned expected hash of the *extracted*
# `mediamtx.exe` inside the verified zip. The pre-slice-10 code wrote
# this digest to `dev/mediamtx.exe.sha256` after the first download
# and re-read it on subsequent launches — a TOCTOU surface where an
# attacker who controls `dev/` could rewrite BOTH files together.
# Pinning the expected exe hash in source means a tampered exe is
# detected even if the side-car `.sha256` file is rewritten. Update
# this value alongside MEDIAMTX_VERSION + MEDIAMTX_ZIP_SHA256.
MEDIAMTX_EXE_SHA256 = "6daa9eed6e4d24e9170b50f60db94b91c5a39c14d6e1f31c5ddd8f7a6d4f2c08"
MEDIAMTX_EXE = REPO_ROOT / "dev" / "mediamtx.exe"
MEDIAMTX_YML = REPO_ROOT / "dev" / "mediamtx.yml"

DEV_ENV_STATIC = {
    "RESTREAM_COOKIE_SECURE": "false",
    "RESTREAM_DATA_DIR": "./.restream-plus-db",
}

ENV_LOCAL = ".env.local"
PASSPHRASE_VAR = "RESTREAM_MASTER_PASSPHRASE"


def main() -> None:
    if sys.platform != "win32":
        sys.exit(
            "start.py is Windows-only (spawns visible cmd windows). "
            "On POSIX, run `python -m app.main`, `mediamtx`, and "
            "`npm run dev` in three terminals manually."
        )

    venv_python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_python.is_file():
        sys.exit(
            f"Backend venv not found at {venv_python}. "
            "Create with `python -m venv .venv` then "
            "`pip install -e .[dev]`."
        )

    web_dir = REPO_ROOT / "web"
    if not (web_dir / "package.json").is_file():
        sys.exit(f"Frontend not found at {web_dir}.")

    ensure_mediamtx_present()
    passphrase = resolve_passphrase()

    # Rebuild dist BEFORE spawning so the backend's `_spa_fallback`
    # (serves dist for any GET to :8000 that isn't an API route) has
    # the current source. Without this, hitting :8000 from a LAN
    # device shows a stale bundle.
    print("Rebuilding web/dist/ for backend :8000 …")
    build = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(web_dir),
        shell=True,
    )
    if build.returncode != 0:
        sys.exit(
            f"`npm run build` failed (exit {build.returncode}). "
            "Fix the build error before launching."
        )

    backend_env = {**os.environ, **DEV_ENV_STATIC, PASSPHRASE_VAR: passphrase}
    backend_inner = f'"{venv_python}" -m app.main'
    mediamtx_inner = f'"{MEDIAMTX_EXE}" "{MEDIAMTX_YML}"'
    frontend_inner = "npm run dev"

    spawn_window(
        title="Restream_Plus backend (FastAPI :8000)",
        inner_cmd=backend_inner,
        cwd=REPO_ROOT,
        env=backend_env,
    )
    spawn_window(
        title=f"Restream_Plus RTMP (MediaMTX {MEDIAMTX_VERSION} :1935)",
        inner_cmd=mediamtx_inner,
        cwd=REPO_ROOT,
        env=None,
    )
    spawn_window(
        title="Restream_Plus frontend (Vite :5173)",
        inner_cmd=frontend_inner,
        cwd=web_dir,
        env=None,
    )

    print("Launched three windows:")
    print("  - Backend FastAPI    → :8000 (API + bundled SPA from web/dist/)")
    print(f"  - RTMP MediaMTX      → :1935 (DEV-ONLY, {MEDIAMTX_VERSION})")
    print("  - Frontend Vite      → :5173 (HMR on web/src/, LAN-bound)")
    print()
    print("Where to point each client:")
    print("  - Browser, frontend iteration: http://localhost:5173/")
    print("  - Browser, prod-fidelity SPA + backend bundle: " "http://localhost:8000/")
    print("  - OBS RTMP server: rtmp://<this-box-LAN-IP>:1935/live")
    print("  - OBS Stream Key: paste the ingest key from Settings → General.")
    print()
    print("First-boot admin password (if `.restream-plus-db/` is fresh)")
    print("prints in the backend window. Saved DB → no new banner.")


# ----------------------------------------------------------------------
# MediaMTX: download-on-first-run, SHA-verify every launch
# ----------------------------------------------------------------------


def ensure_mediamtx_present() -> None:
    # First-run download path: fetch the zip, hash the bytes BEFORE
    # extraction, refuse to write the exe on hash mismatch.
    if not MEDIAMTX_EXE.is_file():
        MEDIAMTX_EXE.parent.mkdir(parents=True, exist_ok=True)
        print(
            f"Downloading MediaMTX {MEDIAMTX_VERSION} " f"(~26 MB, one-time) from\n  {MEDIAMTX_URL}"
        )
        try:
            with urllib.request.urlopen(MEDIAMTX_URL, timeout=120) as resp:
                data = resp.read()
        except Exception as exc:
            sys.exit(f"Download failed: {exc}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != MEDIAMTX_ZIP_SHA256:
            sys.exit(
                "MediaMTX zip SHA-256 mismatch — refusing to extract.\n"
                f"  expected: {MEDIAMTX_ZIP_SHA256}\n"
                f"  observed: {digest}\n"
                "Either the pinned version was tampered with, or the "
                "release file changed (rare but possible). Bump "
                "MEDIAMTX_VERSION + MEDIAMTX_ZIP_SHA256 in start.py."
            )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [n for n in zf.namelist() if n.endswith("mediamtx.exe")]
            if not members:
                sys.exit("Downloaded zip didn't contain mediamtx.exe.")
            with zf.open(members[0]) as src:
                exe_bytes = src.read()
        # Hex Audit CR-F11 (slice 10): verify the extracted exe against
        # `MEDIAMTX_EXE_SHA256` (pinned in source) BEFORE writing it to
        # disk. The zip-level hash above verified what we downloaded;
        # this verifies the extracted member is what we expect. Without
        # this, a hypothetical-future zip-format quirk that legitimately
        # extracts a different exe than expected would silently land on
        # disk. The pre-slice-10 code wrote `mediamtx.exe.sha256`
        # side-car file and re-read it on subsequent launches — TOCTOU
        # surface where an attacker controlling `dev/` could rewrite
        # both files together. The pinned-in-source hash closes that.
        exe_digest = hashlib.sha256(exe_bytes).hexdigest()
        if exe_digest != MEDIAMTX_EXE_SHA256:
            sys.exit(
                "Extracted mediamtx.exe SHA-256 mismatch — refusing to write.\n"
                f"  expected: {MEDIAMTX_EXE_SHA256}\n"
                f"  observed: {exe_digest}\n"
                "Update MEDIAMTX_EXE_SHA256 in start.py if you intended a version bump."
            )
        MEDIAMTX_EXE.write_bytes(exe_bytes)
        print(f"  → wrote {MEDIAMTX_EXE} (SHA-256 verified)")
        return

    # Subsequent-launch re-verify path: hash the cached exe against
    # the pinned-in-source digest. Cheap (~50 ms for 26 MB). If the
    # file was tampered with after first run, we refuse to spawn it.
    # Hex Audit CR-F11 (slice 10): the digest is the source-of-truth
    # constant `MEDIAMTX_EXE_SHA256`; no side-car file to TOCTOU.
    observed = hashlib.sha256(MEDIAMTX_EXE.read_bytes()).hexdigest()
    if observed != MEDIAMTX_EXE_SHA256:
        sys.exit(
            "Cached mediamtx.exe SHA-256 mismatch — refusing to launch.\n"
            f"  expected: {MEDIAMTX_EXE_SHA256}\n"
            f"  observed: {observed}\n"
            f"Delete `{MEDIAMTX_EXE}` and re-run to force a fresh download."
        )


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def spawn_window(
    *,
    title: str,
    inner_cmd: str,
    cwd: Path,
    env: dict[str, str] | None,
) -> None:
    subprocess.Popen(
        f'start "{title}" cmd /k "{inner_cmd}"',
        cwd=str(cwd),
        shell=True,
        env=env,
    )


def _unquote_env_value(value: str) -> str:
    """Strip matching surrounding quotes from a dotenv value.

    Hex Audit CR-F9 (slice 10): the pre-slice-10 code used
    `value.strip('"').strip("'")` which is greedy on BOTH ends — a
    value like `'wrapped"badly'` (single-quote outside, stray
    double-quote inside) became `wrapped"badl` because the inner
    `"` was stripped from the right end. Matching-pair semantics is
    safer: only remove quote characters when they wrap the value as
    a balanced pair. Values without matching quotes pass through
    verbatim, which matches dotenv-spec parsing (a value containing
    a literal quote is unusual but legal).

    Today's `secrets.token_urlsafe()`-generated passphrase contains
    no quote characters so this matters only for manually-set
    passphrases — but a manually-set passphrase containing a quote
    used to be silently corrupted.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def resolve_passphrase() -> str:
    # Precedence:
    #   1. Real env var (highest — overrides for ad-hoc testing).
    #   2. .env.local in repo root, line `RESTREAM_MASTER_PASSPHRASE=…`.
    # Missing both → hard error with copy-pasteable fix instructions.
    from_env = os.environ.get(PASSPHRASE_VAR)
    if from_env:
        return from_env

    env_file = REPO_ROOT / ENV_LOCAL
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == PASSPHRASE_VAR:
                value = _unquote_env_value(value.strip())
                if value:
                    return value

    sys.exit(
        "\n".join(
            [
                f"ERROR: {PASSPHRASE_VAR} is not set.",
                "",
                "The dev backend needs the SAME passphrase used by your",
                "Unraid container that wrote `.restream-plus-db/`. With the",
                "wrong passphrase, login still works but stream keys cannot",
                "be revealed and the supervisor refuses to start.",
                "",
                "Quickest fix: create `.env.local` in the repo root with:",
                "",
                f"  {PASSPHRASE_VAR}=your-actual-passphrase-here",
                "",
                "`.env.local` is gitignored. If you don't remember the",
                "production passphrase, wipe `.restream-plus-db/` and the",
                "backend will generate a fresh empty DB on first boot.",
            ]
        )
    )


if __name__ == "__main__":
    main()
