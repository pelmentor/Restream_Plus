"""Static guard: domain layer must not import from outer layers.

The domain owes the supervisor (Phase 5) and HTTP (Phase 6) its
primitives, but it must remain a pure-Python island. AST-scan every
module under app/domain/ and reject forbidden imports. This is the
mechanical enforcement of the layered-architecture rule documented in
`app/domain/__init__.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DOMAIN_ROOT = Path(__file__).resolve().parents[2] / "app" / "domain"

# Modules forbidden anywhere in the dependency tree of app.domain.
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "asyncio",
        "subprocess",
        "app.repositories",
        "app.db",
        "app.crypto",
        "app.auth",
        "app.api",
        "app.fanout",
    }
)


def _collect_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def _module_files() -> list[Path]:
    return sorted(p for p in DOMAIN_ROOT.glob("*.py") if p.name != "__pycache__")


@pytest.mark.parametrize("module_file", _module_files(), ids=lambda p: p.name)
def test_no_forbidden_imports(module_file: Path) -> None:
    imports = _collect_imports(module_file)
    leaks: set[str] = set()
    for forbidden in FORBIDDEN_MODULES:
        for name in imports:
            if name == forbidden or name.startswith(forbidden + "."):
                leaks.add(name)
    assert not leaks, (
        f"{module_file.name} imports forbidden module(s): {sorted(leaks)}; "
        "domain layer must remain pure-Python"
    )


def test_at_least_one_module_scanned() -> None:
    # Tripwire: if the glob returns nothing, the parametrize is no-op
    # and every forbidden import passes vacuously.
    assert _module_files(), "no app/domain/*.py modules found to scan"
