# ADR-0014: SPA URL cap + traversal-guarding policy for `_spa_fallback`

## Status
Accepted — 2026-05-19 (slice 9 — codifying the v1.1.3 hotfix per
Hex Audit SA-F9)

## Context

The control plane mounts a single catch-all GET handler
(`app/main.py::_spa_fallback`) AFTER every API router has had its
chance, so React Router can resolve deep links client-side without
a per-route backend handler. The handler is reached by any GET
request whose path does NOT start with `api/`, `internal/`, `ws`,
`livez`, or `readyz`.

v1.1.3 shipped a hotfix for a route-compounding bug: relative
routes in `web/src/components/settings/SettingsSidebar.tsx` made
the browser redirect from `/settings/foo/general` to
`/settings/foo/general/general` to
`/settings/foo/general/general/general` … until the URL exceeded
the OS path-length limit. The backend `_spa_fallback` then called
`Path.is_file()` on a >4 KB path, the syscall returned ENAMETOOLONG,
the unhandled OSError became HTTP 500, and operators saw "the panel
is broken" instead of "the SPA is misrouting".

The frontend bug was patched in v1.1.3 (settings routes now use
absolute paths). The backend hardening added in the same hotfix —
codepoint cap, byte-space cap, path-traversal guard, OSError
catch — was the defence-in-depth the route-loop class of bug
demanded. **None of it was in an ADR.** A future contributor
removing "what looks like belt-and-suspenders" caps would
reintroduce the v1.1.3 outage shape. This ADR pins the policy.

Two subsequent hex-audit findings extended the original hotfix:

- **FG2-C3** (slice 2 of the 2026-05-18 hex audit) added the
  byte-space cap to defeat the multi-byte-codepoint bypass (a
  600-codepoint string of 4-byte emoji or supplementary-plane CJK
  characters is 2400 bytes after `fsencode` — past the safe margin
  even though the codepoint check passes).
- **Path-traversal hardening** added `resolve()` +
  `relative_to(resolved_root)` guards so `..` segments can't
  escape `spa_dir`.

## Decision

**`_spa_fallback` enforces a 4-step guard before touching the
filesystem: API-prefix shadow, codepoint cap (1024), byte-space
cap (2048), and resolved-path containment. Anything that fails a
guard falls through to `index.html` (so the SPA renders its own
NotFound) OR returns a real 404 (when the API surface is shadowed
or the resolve itself errors).**

### The 4 guards (in order)

```python
# Guard 1: API-prefix shadow → real 404 (never SPA shell on these)
api_prefixes = ("api/", "internal/", "ws", "livez", "readyz")
if any(full_path.startswith(p) for p in api_prefixes):
    raise HTTPException(status_code=404)

# Guard 2: codepoint cap → fall through to index.html
if len(full_path) > 1024:
    return FileResponse(index_html)

# Guard 3: byte-space cap (multi-byte UTF-8 expansion) → fall through to index.html
if len(full_path.encode("utf-8", errors="replace")) > 2048:
    return FileResponse(index_html)

# Guard 4: resolve + containment → real 404 on traversal or resolve error
try:
    candidate = (spa_dir / full_path).resolve()
except (OSError, ValueError):
    raise HTTPException(status_code=404) from None
try:
    candidate.relative_to(resolved_root)
except ValueError:
    raise HTTPException(status_code=404) from None

# Now safe to stat
if candidate.is_file():
    return FileResponse(candidate)
return FileResponse(index_html)
```

### The numbers

| Cap | Value | Why |
| --- | --- | --- |
| `max_path_codepoints` | **1024** | well under Linux ext4's `NAME_MAX=255` per-component AND `PATH_MAX=4096` total; rejects obvious abuse without limiting legitimate SPA deep links (the longest legitimate route in the panel as of v1.1.4 is ~80 chars) |
| `max_path_bytes` | **2048** | below the 4096-byte ENAMETOOLONG cliff on ext4; allows up to ~512 emoji or ~512 4-byte CJK supplementary-plane chars in a path that survived the codepoint check (the codepoint check alone would pass 1024 × 4 = 4096 bytes, which is exactly the cliff) |
| `api_prefixes` | tuple of 5 fixed strings | matches every router prefix the app mounts; adding a new router prefix MUST add its prefix here OR the SPA shell will silently shadow a 404'd API route, which is exactly the v1.1.1 / v1.1.2 release-blocker failure mode |

### Fallthrough vs 404 — which guard returns which

**Real 404:**
- API-prefix shadow (Guard 1): a request to `api/typo` got past
  every router and reached the fallback; we MUST 404 it so
  frontend fetch typos surface as real errors, not silent HTML
  responses that the SPA loader can't parse.
- Resolve error (Guard 4 first try): paths that can't even be
  resolved (embedded NUL → ValueError, syscall error → OSError)
  are not legitimate SPA deep links; 404 keeps them
  distinguishable from normal deep-links in access logs.
- Containment failure (Guard 4 second try): `..` traversal
  attempt; 404 instead of 403 because we don't want to confirm
  "you almost escaped" to a probe.

**Fall through to `index.html`:**
- Codepoint cap (Guard 2): the path is too long to be a sane SPA
  deep link, but we don't know it's malicious — could be a
  routing bug like v1.1.3. Falling through lets the SPA render
  its own NotFound page (consistent UX with "deep link to a
  removed feature") rather than a server-side 404.
- Byte cap (Guard 3): same reasoning as Guard 2.
- `is_file()` miss (after Guards 1-4): the path is well-formed
  but doesn't point to a real asset; this IS the SPA-deep-link
  case React Router was meant to handle.

The distinction matters: an operator debugging "why does my
custom panel URL show NotFound" wants to see HTML, not 404, in
the network tab — the SPA shell loading and the React Router
deciding NotFound is the EXPECTED behavior for unknown deep
links. Real 404s are reserved for cases where the SPA shell
itself isn't the right answer (API path miss, traversal
attempt).

## Consequences

**Easier:**

- The v1.1.3 route-compounding outage shape is closed by Guard 2
  + Guard 3. Even if a future SettingsSidebar regression
  reintroduces relative routes, the backend won't 500.
- Path traversal is closed by Guard 4. The fallback only ever
  serves files inside `resolved_root`; symlinks pointing outside
  fail containment.
- The cap values are in code AND in the ADR; a future "let's bump
  the cap" question has both numbers and reasoning to point at.
- The `is_file()` wrapped in `try/except OSError`+`ValueError`
  catches anything that slipped past the caps (the cap-then-stat
  ordering is the architectural fix; the wrapped OSError is the
  belt).

**Harder:**

- A legitimate user with a 1025-codepoint deep link gets the SPA
  shell instead of the deep-link page they expected. Acceptable:
  we have no such deep link in the product, and adding one would
  require lifting the cap in a NEW ADR (this section), which
  would force the contributor to think about whether the link
  shape is sane.
- The byte cap is "fall through, not 404" which means an attacker
  hammering `/<lots-of-emoji>` gets back the SPA shell repeatedly
  — that's ~2 KB of HTML per request. Acceptable: the rate
  limiter (ADR-0005) gates the login surface and the WS upgrade;
  the SPA shell is uncached read-only HTML that any visitor sees
  on first page load anyway.
- Adding a new API router prefix requires updating
  `api_prefixes`. If forgotten, the next deploy will silently
  shadow that prefix with the SPA shell — caught by integration
  tests that GET the new route and assert JSON, not HTML.

## Rejected alternatives

- **Higher caps (e.g., 4096 bytes)** — exactly the ext4 cliff;
  one byte over and we're back to v1.1.3. Rejected; the bound
  has to be below the syscall ceiling, not at it.
- **Lower caps (e.g., 256 bytes)** — rejects legitimate deep
  links to long resource paths (a hypothetical
  `/targets/<uuid>/credentials/<uuid>/reveal-history?tab=...`
  could go past 256). Rejected; 1024/2048 is the right slack.
- **Return 404 on the cap path** — see fallthrough vs 404
  reasoning above. Rejected; SPA-loader UX wins.
- **Reject API-prefix shadow at the router layer (FastAPI)** —
  considered. FastAPI's path-operation precedence already gives
  every router a chance before the catch-all reaches; the
  prefix-shadow check in `_spa_fallback` is the *defensive*
  layer (what if a router didn't register, or a future refactor
  drops a router?). Keep both.
- **Use `starlette.staticfiles.StaticFiles` directly** —
  considered. StaticFiles serves files but doesn't fall through
  to index.html for unknown paths (the history-API fallback
  IS the reason we have a hand-rolled handler). Could be hybrid
  (mount StaticFiles + fallback for unknowns) but the extra
  mount config doesn't buy anything our handler doesn't already
  do, and splitting the cap logic across two mounts would make
  Guard 1 / Guard 4 ordering harder to reason about. Rejected.

## Open questions / revisit triggers

- If a router prefix changes (e.g., we add `/dev/` for the dev
  panel), update `api_prefixes` AND this ADR's table.
- If a legitimate deep link approaches 1024 codepoints, the
  product has a routing-shape problem worth solving before
  lifting the cap.
- If the SPA shell ever becomes large enough that serving it
  on every malformed-path request becomes a bandwidth concern,
  the byte-cap fallthrough can switch to a tiny static 404 page
  — but that's a v2 concern.
- If we ever serve user-uploaded content from `/data/` and want
  to use the same fallback, this ADR is INSUFFICIENT — the
  resolved-root containment check assumes a single static SPA
  directory, not a user-content path with finer-grained ACLs.
- The `is_file()` wrapped in `try/except OSError, ValueError`
  inside the handler is the LAST defence: even if every cap is
  bypassed (say, a future Python pathlib change), the wrap
  ensures a 500 cannot escape. Removing the wrap would require
  removing this ADR (the v1.1.3 incident vindicates the
  defence-in-depth posture).
