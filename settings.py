"""Per-user preferences, stored as JSON values in the `user_settings` table.

Only values the user saved are stored. Everything else falls back to the defaults below, and for
the mail-sync limits to the optional `.env` values (INBOX_TEND_SYNC_MAX_MESSAGES,
INBOX_TEND_ATTACHMENT_MAX_BYTES), so existing installs behave exactly as before.
"""
from __future__ import annotations
import json
import re
from local_translation import language_code, TranslationError
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PRIORITIES = ('low', 'medium', 'high')
AI_PROVIDERS = ('codex', 'claude')
INBOX_VIEWS = ('threads', 'messages', 'people')
DEFAULTS = {
    'timezone': 'Pacific/Saipan',   # Default for new task deadlines; also how mail times are displayed.
    'due_soon_hours': 48,           # A task due within this many hours is flagged "due soon".
    'default_priority': 'medium',   # Priority pre-selected when suggesting a task.
    'inbox_view': 'threads',        # How the Inbox lists mail: threads (conversations), messages or people.
    'sync_sent': True,              # Also mirror sent mail.
    'mark_read_on_open': True,      # Opening a message marks it read.
    'gmail_write_read': False,      # Also apply read/unread to Gmail (needs the gmail.modify grant; set when it is granted).
    'sync_max_messages': 200,       # Per folder.
    'auto_sync_minutes': 5,         # Fetch new mail automatically this often (0 = only on Refresh).
    'attachment_max_mb': 10,        # Larger attachments are recorded as metadata only.
    # Optional AI assistant (Codex CLI). Off by default: nothing leaves this server until the user turns it on
    # AND presses an AI button.
    'ai_enabled': False,
    'ai_provider': 'codex',         # codex (Codex CLI) or claude (Claude Code CLI).
    'ai_model': '',                 # Empty = the provider's own default model.
    'ai_language': '',              # Empty = write in the language of the email.
    'ai_timeout': 60,               # Seconds (10-1800). A proxy in front may cut slower requests.
    'ai_attachments': False,        # Also send small text/CSV attachments with the email.
    # Offline translation of opened messages using installed Argos language packs.
    'translate_enabled': False,
    'translate_language': '',
    'translate_max_chars': 5000,
    # Country flags next to correspondents, guessed once per sender by the AI provider and cached. Enabling it means
    # new senders' names, addresses, date offsets, subjects and a short snippet are sent to that provider.
    'flags_enabled': False,
    # A topic icon on every message (money, job, love, ...), guessed once per message by the AI provider and cached.
    # Enabling it means each new message's subject, sender and first characters are sent to that provider.
    'topics_enabled': False,
}
_INT_LIMITS = {'due_soon_hours': (1, 720), 'sync_max_messages': (1, 5000), 'attachment_max_mb': (0, 100), 'auto_sync_minutes': (0, 1440), 'ai_timeout': (10, 1800)}
_BOOLS = ('sync_sent', 'mark_read_on_open', 'gmail_write_read', 'ai_enabled', 'ai_attachments', 'translate_enabled', 'flags_enabled', 'topics_enabled')


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
    if key in _BOOLS:
        if not isinstance(value, bool):
            raise ValueError(f'Invalid value for {key}')
        return value
    if key == 'inbox_view':
        if value not in INBOX_VIEWS:
            raise ValueError('Unknown inbox view')
        return value
    if key == 'ai_provider':
        if value not in AI_PROVIDERS:
            raise ValueError('Unknown AI provider')
        return value
    if key == 'ai_model':
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}|', value.strip()):
            raise ValueError('Invalid model name')
        return value.strip()
    if key == 'translate_language':
        if not isinstance(value, str): raise ValueError('Invalid translation language')
        if not value.strip(): return ''
        try: return language_code(value)
        except TranslationError as exc: raise ValueError(str(exc)) from None
    if key == 'translate_max_chars':
        if type(value) is not int or value not in (1000, 3000, 5000):
            raise ValueError('Choose a supported translation length')
        return value
    if key == 'ai_language':
        if not isinstance(value, str) or len(value.strip()) > 40 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Language must be at most 40 characters')
        return value.strip()
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
        values['auto_sync_minutes'] = _clean('auto_sync_minutes', int(config['INBOX_TEND_AUTO_SYNC_MINUTES']))
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
