"""Per-user preferences, stored as JSON values in the `user_settings` table.

Only values the user saved are stored. Everything else falls back to the defaults below, and for
the mail-sync limits to the optional `.env` values (INBOX_TEND_SYNC_MAX_MESSAGES,
INBOX_TEND_ATTACHMENT_MAX_BYTES), so existing installs behave exactly as before.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PRIORITIES = ('low', 'medium', 'high')
DEFAULTS = {
    'timezone': 'Pacific/Saipan',   # Default for new task deadlines; also how mail times are displayed.
    'due_soon_hours': 48,           # A task due within this many hours is flagged "due soon".
    'default_priority': 'medium',   # Priority pre-selected when suggesting a task.
    'sync_sent': True,              # Also mirror sent mail.
    'sync_max_messages': 200,       # Per folder.
    'attachment_max_mb': 10,        # Larger attachments are recorded as metadata only.
}
_INT_LIMITS = {'due_soon_hours': (1, 720), 'sync_max_messages': (1, 5000), 'attachment_max_mb': (0, 100)}


def valid_timezone(name) -> str:
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or name != name.strip():
        raise ValueError('Invalid timezone')
    try:
        ZoneInfo(name)
    except Exception:  # Unknown key, bad characters, missing tz database entry.
        raise ValueError('Unknown timezone: use an IANA name such as Asia/Tokyo or America/New_York') from None
    return name


def _clean(key, value):
    if key not in DEFAULTS:
        raise ValueError(f'Unknown setting: {key}')
    if key == 'timezone':
        return valid_timezone(value)
    if key == 'default_priority':
        if value not in PRIORITIES:
            raise ValueError('Invalid default priority')
        return value
    if key == 'sync_sent':
        if not isinstance(value, bool):
            raise ValueError('Invalid value for sync_sent')
        return value
    low, high = _INT_LIMITS[key]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{key} must be a whole number from {low} to {high}')
    return value


def stored(db, user_id) -> dict:
    """Only the values this user has explicitly saved (invalid leftovers are ignored)."""
    out = {}
    for row in db.execute('SELECT key,value FROM user_settings WHERE user_id=?', (user_id,)):
        try:
            out[row['key']] = _clean(row['key'], json.loads(row['value']))
        except (ValueError, TypeError):
            continue
    return out


def effective(db, user_id, config=None) -> dict:
    """Defaults, then .env sync limits, then the user's saved values."""
    config = config or {}
    values = dict(DEFAULTS)
    try:
        values['sync_max_messages'] = _clean('sync_max_messages', int(config['INBOX_TEND_SYNC_MAX_MESSAGES']))
    except (KeyError, ValueError, TypeError):
        pass
    try:
        values['attachment_max_mb'] = _clean('attachment_max_mb', int(config['INBOX_TEND_ATTACHMENT_MAX_BYTES']) // (1024 * 1024))
    except (KeyError, ValueError, TypeError):
        pass
    values.update(stored(db, user_id))
    return values


def save(db, user_id, changes: dict) -> None:
    if not isinstance(changes, dict) or not changes:
        raise ValueError('No settings to save')
    cleaned = {key: _clean(key, value) for key, value in changes.items()}  # Validate everything before writing.
    now = datetime.now(timezone.utc).isoformat()
    with db:
        for key, value in cleaned.items():
            db.execute('INSERT INTO user_settings(user_id,key,value,updated_at) VALUES (?,?,?,?) '
                       'ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                       (user_id, key, json.dumps(value), now))
