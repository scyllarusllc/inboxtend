"""Gmail-style search over the local SQLite copy of the mail.

Words match the subject, sender, recipients, snippet and body; all words must match. Operators narrow it down:
`from:casey` `to:dana` `subject:invoice` `is:unread` `is:read` `has:attachment`; use quotes for phrases
(`subject:"website project"`). Everything is turned into one parameterised SQL condition on the table alias `m`;
user text is never spliced into the SQL.
"""
from __future__ import annotations
import re

MAX_QUERY = 200
MAX_TOKENS = 12
_TOKEN = re.compile(r'(?:(\w+):)?(?:"([^"]*)"|(\S+))')
_UNREAD = '((m.read_override IS NULL AND m.label_ids LIKE \'%"UNREAD"%\') OR m.read_override = 0)'
_ATTACHMENT = 'EXISTS (SELECT 1 FROM gmail_attachments a WHERE a.user_id=m.user_id AND a.message_id=m.message_id)'
_TEXT_COLUMNS = ('m.subject', 'm.from_addr', 'm.to_addr', 'm.snippet', 'm.body_text')


def _like(value: str) -> str:
    return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def parse(query: str, *, unread: bool = False, attachments: bool = False) -> dict:
    parsed = {'words': [], 'from': [], 'to': [], 'subject': [], 'unread': unread, 'read': False, 'attachment': attachments}
    for key, quoted, bare in _TOKEN.findall((query or '')[:MAX_QUERY])[:MAX_TOKENS]:
        value = (quoted if quoted else bare).strip()
        key = key.lower()
        if not value:
            continue
        if key in ('from', 'to', 'subject'):
            parsed[key].append(value)
        elif key == 'is' and value.lower() in ('unread', 'read'):
            parsed['unread' if value.lower() == 'unread' else 'read'] = True
        elif key == 'has' and value.lower() in ('attachment', 'attachments'):
            parsed['attachment'] = True
        else:  # Plain words, and unknown operators ("note:3pm") searched literally.
            parsed['words'].append((key + ':' + value) if key else value)
    return parsed


def condition(parsed: dict):
    """(sql, params) for the parsed search, or ('', []) when nothing is being searched for."""
    parts, params = [], []
    for word in parsed['words']:
        parts.append('(' + ' OR '.join(f"{c} LIKE ? ESCAPE '\\'" for c in _TEXT_COLUMNS) + ')')
        params += [_like(word)] * len(_TEXT_COLUMNS)
    for column, key in (('m.from_addr', 'from'), ('m.to_addr', 'to'), ('m.subject', 'subject')):
        for value in parsed[key]:
            parts.append(f"{column} LIKE ? ESCAPE '\\'")
            params.append(_like(value))
    if parsed['unread']:
        parts.append(_UNREAD)
    if parsed['read']:
        parts.append('NOT ' + _UNREAD)
    if parsed['attachment']:
        parts.append(_ATTACHMENT)
    return (' AND '.join(parts), params) if parts else ('', [])
