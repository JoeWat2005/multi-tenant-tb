"""Tests for app.util.dates.iso_utc — defends against the SQLite-tz bug."""
from __future__ import annotations

from datetime import datetime, timezone

from app.util.dates import iso_utc


def test_none_returns_none():
    assert iso_utc(None) is None


def test_naive_datetime_assumed_utc():
    naive = datetime(2026, 4, 25, 12, 0, 0)
    out = iso_utc(naive)
    assert out is not None
    assert out.endswith("+00:00")


def test_aware_datetime_preserved():
    aware = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    out = iso_utc(aware)
    assert out == "2026-04-25T12:00:00+00:00"
