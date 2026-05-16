"""Restream_Plus control plane.

See `docs/SESSION_HANDOFF.md` for the project orientation and
`docs/architecture/README.md` for the ADR index.
"""

from app.version import BUILD_SHA, __version__

__all__ = ["__version__", "BUILD_SHA"]
