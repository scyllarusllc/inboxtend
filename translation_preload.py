"""Fill the existing SQLite translation cache for recent unread mail when CPU is idle."""
from __future__ import annotations

from contextlib import closing
import os
import threading
import time

import ai
import gmail_web
import local_translation
import settings as prefs
from tasks import connection

_STATE: dict[int, dict] = {}
_FAILED: set[tuple[int, str, str]] = set()
_LOCK = threading.Lock()


def status(user_id: int) -> dict:
    with _LOCK:
        return dict(_STATE.get(user_id, {'completed': 0, 'last_error': '', 'last_at': None}))


def _idle() -> bool:
    try:
        return os.getloadavg()[0] < max(.5, min(os.cpu_count() or 1, 4) * .35)
    except OSError:
        return False


def _candidate(db, user_id: int, language: str):
    rows = db.execute('''SELECT message_id, subject, body_text, snippet, label_ids, read_override
        FROM gmail_messages m WHERE user_id=? AND in_inbox=1 AND has_full=1
        AND NOT EXISTS (SELECT 1 FROM message_translations t WHERE t.user_id=m.user_id
                        AND t.message_id=m.message_id AND t.language_key=?)
        ORDER BY internal_date DESC LIMIT 30''', (user_id, language)).fetchall()
    return next((row for row in rows if gmail_web._is_unread(row)
                 and (row['body_text'] or row['snippet'] or row['subject'])
                 and (user_id, row['message_id'], language) not in _FAILED), None)


def run_once(db_path, config) -> None:
    """At most one message per pass; never fetch mail or download models here."""
    if not _idle() or not local_translation.runtime_ready():
        return
    with closing(connection(db_path)) as db:
        users = [row[0] for row in db.execute('SELECT DISTINCT user_id FROM gmail_connections')]
        for user_id in users:
            if not _idle() or gmail_web.sync_progress(user_id)['running']:
                return
            user = prefs.effective(db, user_id, config)
            language = user['translate_language']
            if not (user['translate_enabled'] and user['translate_preload'] and language):
                continue
            if language not in {pair['code'] for pair in local_translation.installed_targets()}:
                continue
            row = _candidate(db, user_id, language)
            if row is None:
                continue
            try:
                with ai.translation_guard(user_id, 0):
                    # A foreground request may have filled the cache since candidate selection.
                    if gmail_web.get_translation(db, user_id, row['message_id'], language):
                        continue
                    result = local_translation.translate_message(dict(row), language, user['translate_max_chars'])
                    gmail_web.save_translation(db, user_id, row['message_id'], language,
                                               result, 'argos', 'installed language pack')
            except ai.AIBusy:
                return
            except local_translation.TranslationError as exc:
                with _LOCK:
                    _FAILED.add((user_id, row['message_id'], language))
                    _STATE.setdefault(user_id, {'completed': 0})['last_error'] = str(exc)[:160]
                return
            with _LOCK:
                state = _STATE.setdefault(user_id, {'completed': 0})
                state.update(completed=state['completed'] + 1, last_error='', last_at=int(time.time()))
            return


def start(db_path, config) -> None:
    def loop():
        while True:
            time.sleep(30)
            try:
                run_once(db_path, config)
            except Exception:
                # Background work must not take the mail server down.
                pass
    threading.Thread(target=loop, daemon=True, name='translation-preload').start()
