"""Run-sessions history router: GET /api/sessions (paginated).

Cursor-based pagination on `started_at DESC` — pass `before` (an
ISO-8601 timestamp) to fetch older entries. The default page size is
50; an explicit `limit` is capped at 200 so a misbehaving client
can't request unbounded reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.schemas import RunHistoryItem, RunHistoryPage, compute_duration_seconds
from app.auth.deps import AuthenticatedRequestDep, SessionDep
from app.repositories.sessions_history import SessionsHistoryRepository

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=RunHistoryPage)
async def list_run_history(
    _: AuthenticatedRequestDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[datetime | None, Query()] = None,
) -> RunHistoryPage:
    repo = SessionsHistoryRepository(session)
    # Cursor-pushed-to-SQL per code review H-3: fetch `limit + 1` to
    # detect "is there a next page?", then slice to `limit`. The repo
    # applies `before` at the SQL layer so the count is correct by
    # construction.
    rows = await repo.list_recent(limit=limit + 1, before=before)
    has_more = len(rows) > limit
    page = list(rows[:limit])
    next_before = page[-1].started_at if has_more and page else None
    return RunHistoryPage(
        items=tuple(
            RunHistoryItem(
                id=r.id,
                started_at=r.started_at,
                ended_at=r.ended_at,
                end_reason=r.end_reason,
                duration_seconds=compute_duration_seconds(r.started_at, r.ended_at),
            )
            for r in page
        ),
        next_before=next_before,
    )


__all__ = ["router"]
