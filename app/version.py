"""Version + build identity.

`__version__` tracks the project's semver string and matches
`[project].version` in `pyproject.toml`.

`BUILD_SHA` is the short git SHA injected by the Docker build via
`RESTREAM_BUILD_SHA`. Outside the container it stays `None` — the
healthz endpoint surfaces that as `"dev"`.
"""

from __future__ import annotations

import os

__version__: str = "0.1.0"

BUILD_SHA: str | None = os.environ.get("RESTREAM_BUILD_SHA") or None
