"""Centralised datetime serialisation.

SQLite doesn't natively store timezone info, so SQLAlchemy returns
tz-naive `datetime` objects on read even though we wrote them with
`datetime.now(timezone.utc)`. If we `.isoformat()` a naive datetime we
produce `"2026-04-24T15:48:00.123456"` — no suffix — and browsers
interpret that as local time.

`iso_utc` assumes any naive datetime from our DB is already in UTC
(which is true: every writer uses `datetime.now(timezone.utc)`), and
emits `"2026-04-24T15:48:00.123456+00:00"` so JS parses it correctly.
"""
from __future__ import annotations

from datetime import datetime, timezone


def iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
