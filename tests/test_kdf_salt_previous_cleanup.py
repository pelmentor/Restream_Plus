"""Hex Audit FG2-H5 (slice 7) — audit-before-unlink invariant.

The `_kdf_salt_previous_cleanup_once` helper MUST emit the audit
event BEFORE unlinking the file. If the audit raises, the unlink is
skipped — next tick retries (mtime unchanged, the file is still
there) and the forensic record is guaranteed once persistence
recovers. This test pins that contract.

The loop wrapper is trivial (sleep + delegate); the testable
invariant lives in the inner helper.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from app.main import KDF_SALT_PREVIOUS_GRACE, _kdf_salt_previous_cleanup_once


class _FakeAuditRepo:
    """Stand-in for `AuditLogRepository`. Records calls and can be
    configured to raise on `append_standalone`.

    The kdf-salt cleanup helper has no caller session, so it routes
    through `append_standalone` (Hex Audit BA-F4, slice 8).
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail = fail

    async def append_standalone(
        self,
        *,
        event_type: str,
        actor: str | None = None,
        target_id: str | None = None,
        data: dict[str, object] | None = None,
        at: object = None,
        checkpoint: bool = True,
    ) -> None:
        self.calls.append(
            {
                "event_type": event_type,
                "actor": actor,
                "data": data,
            }
        )
        if self.fail:
            raise RuntimeError("audit append failed")


def _make_aged_file(path: Path, age_seconds: float) -> None:
    """Touch `path` and backdate its mtime by `age_seconds`."""
    path.write_bytes(b"old-salt")
    past = time.time() - age_seconds
    import os

    os.utime(path, (past, past))


@pytest.mark.asyncio
async def test_audit_succeeds_then_unlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: file aged > grace → audit fires → file unlinked."""
    salt_path = tmp_path / "kdf_salt.previous"
    _make_aged_file(salt_path, age_seconds=KDF_SALT_PREVIOUS_GRACE.total_seconds() + 60)

    audit = _FakeAuditRepo()
    monkeypatch.setattr(
        "app.repositories.audit_log.AuditLogRepository",
        lambda _sm: audit,
    )

    await _kdf_salt_previous_cleanup_once(salt_path, sessionmaker_=object())

    assert len(audit.calls) == 1
    assert audit.calls[0]["event_type"] == "kdf_salt_previous_cleared"
    data = audit.calls[0]["data"]
    assert isinstance(data, dict)
    assert data["path"] == str(salt_path)
    assert "mtime" in data
    # File was unlinked after the audit landed.
    assert not salt_path.exists()


@pytest.mark.asyncio
async def test_audit_failure_skips_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FG2-H5 invariant: if audit raises, the file MUST NOT be unlinked.

    Forensic record is then guaranteed when the next tick re-runs the
    sequence (file still there, audit retried)."""
    salt_path = tmp_path / "kdf_salt.previous"
    _make_aged_file(salt_path, age_seconds=KDF_SALT_PREVIOUS_GRACE.total_seconds() + 60)

    audit = _FakeAuditRepo(fail=True)
    monkeypatch.setattr(
        "app.repositories.audit_log.AuditLogRepository",
        lambda _sm: audit,
    )

    await _kdf_salt_previous_cleanup_once(salt_path, sessionmaker_=object())

    # Audit was attempted...
    assert len(audit.calls) == 1
    # ...but the unlink did NOT happen.
    assert salt_path.exists()


@pytest.mark.asyncio
async def test_within_grace_window_no_op(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file younger than the grace window is left alone — no audit,
    no unlink."""
    salt_path = tmp_path / "kdf_salt.previous"
    # 5 minutes old << 24 h grace.
    _make_aged_file(salt_path, age_seconds=300.0)

    audit = _FakeAuditRepo()
    monkeypatch.setattr(
        "app.repositories.audit_log.AuditLogRepository",
        lambda _sm: audit,
    )

    await _kdf_salt_previous_cleanup_once(salt_path, sessionmaker_=object())

    assert audit.calls == []
    assert salt_path.exists()


@pytest.mark.asyncio
async def test_missing_file_no_op(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No file → benign no-op. The common case after the cleanup
    succeeded on the previous tick."""
    salt_path = tmp_path / "kdf_salt.previous"
    audit = _FakeAuditRepo()
    monkeypatch.setattr(
        "app.repositories.audit_log.AuditLogRepository",
        lambda _sm: audit,
    )

    await _kdf_salt_previous_cleanup_once(salt_path, sessionmaker_=object())

    assert audit.calls == []
