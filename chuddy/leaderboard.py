"""Calendar periods shared by local and friends score rankings."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TIMEZONE = 'America/Toronto'


def period_bounds(period='all_time', at=None):
    if period not in ('all_time', 'today', 'week'):
        raise ValueError('Choose today, week or all_time.')
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        raise ValueError('Ranking time must include a time zone.')
    local = at.astimezone(ZoneInfo(TIMEZONE))
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = None if period == 'all_time' else today
    if period == 'week':
        start -= timedelta(days=start.weekday())
    return (start.astimezone(timezone.utc).isoformat() if start else None,
            at.astimezone(timezone.utc).isoformat())
