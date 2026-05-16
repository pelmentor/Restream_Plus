"""Mirror of `tests/domain/test_no_outward_imports.py` for `app.fanout`.

The fanout layer (Phase 5) sits between domain (Phase 4) and the
HTTP/WS API (Phase 6). It is allowed to import from domain,
repositories, crypto, auth, and logging_setup — those are below it in
the dependency stack. It must NOT import from `app.api` (which doesn't
exist yet but will in Phase 6) — that direction is reserved for the
HTTP layer to wire fanout in via lifespan + Depends.

This test walks the AST of every `app/fanout/*.py` and rejects any
import from `app.api`. Adding a new forbidden module to the set is a
one-line change here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

FANOUT_ROOT = Path(__file__).resolve().parents[2] / "app" / "fanout"
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "app.api",
    }
)


def _collect_fanout_modules() -> list[Path]:
    return sorted(p for p in FANOUT_ROOT.glob("*.py") if p.name != "__init__.py")


def _imported_modules(source: str, file: Path) -> set[str]:
    tree = ast.parse(source, filename=str(file))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            out.add(node.module)
    return out


@pytest.mark.parametrize("file", _collect_fanout_modules(), ids=lambda p: p.name)
def test_no_forbidden_imports(file: Path) -> None:
    source = file.read_text(encoding="utf-8")
    imports = _imported_modules(source, file)
    for forbidden in FORBIDDEN_MODULES:
        for imp in imports:
            is_forbidden = imp == forbidden or imp.startswith(forbidden + ".")
            assert not is_forbidden, (
                f"{file.name} imports {imp!r}, which is in the forbidden set "
                f"({forbidden!r}). The fanout layer must not depend on the API layer."
            )
