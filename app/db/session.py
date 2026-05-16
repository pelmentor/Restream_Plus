"""Async session factory.

The factory is the *only* way to get a SQLAlchemy `AsyncSession` in the
app. Repositories accept a session instance via their constructor;
they do not import the factory directly. This keeps the dependency
direction one-way (engine → factory → caller → repo) and makes
test-time substitution trivial.

Usage:
    engine = create_engine(settings)
    factory = make_sessionmaker(engine)
    async with factory() as s:
        repo = UsersRepository(s)
        user = await repo.get_by_username("admin")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncEngine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build an `async_sessionmaker` bound to the given engine.

    `expire_on_commit=False` because repositories convert ORM rows to
    Pydantic DTOs before returning, so the typical "use-after-commit"
    pitfall doesn't apply — we never hand an ORM row across a commit
    boundary.
    """
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
