"""Single-admin local authentication; tokens are stored only as hashes in SQLite."""
from __future__ import annotations
from collections import deque
from contextlib import closing
from datetime import datetime, timezone
from hashlib import pbkdf2_hmac, sha256
from http.cookies import SimpleCookie
from pathlib import Path
import base64
import hmac
import os
import re
import secrets
import threading
import time
from tasks import connection

SESSION_IDLE_SECONDS = 7 * 86400   # Signed out after this long without using the app; every use pushes it forward.
SESSION_MAX_SECONDS = 30 * 86400   # Hard limit from sign-in, however active: after this, sign in again.
SESSION_SECONDS = SESSION_MAX_SECONDS   # Cookie lifetime; the server enforces the shorter idle limit.
_RENEW_AFTER = 3600                # Extend at most once an hour so reading does not write on every request.
ROUNDS = 310_000
_TOKEN = re.compile(r'^[A-Za-z0-9_-]{40,100}$')
_DUMMY_HASH = None


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _unb64(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def hash_password(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError('Password must be 12–1024 characters')
    salt = salt or secrets.token_bytes(16)
    digest = pbkdf2_hmac('sha256', password.encode(), salt, ROUNDS)
    return f'pbkdf2_sha256${ROUNDS}${_b64(salt)}${_b64(digest)}'


def verify_password(password, encoded):
    try:
        kind, count, salt, expected = encoded.split('$')
        rounds = int(count)
        if kind != 'pbkdf2_sha256' or not 200_000 <= rounds <= 1_000_000:
            return False
        salt_bytes, expected_bytes = _unb64(salt), _unb64(expected)
        if len(salt_bytes) != 16 or len(expected_bytes) != 32:
            return False
        value = pbkdf2_hmac('sha256', password.encode(), salt_bytes, rounds)
        return hmac.compare_digest(value, expected_bytes)
    except (AttributeError, ValueError, TypeError, UnicodeError):
        return False


def read_config(path):
    """Read only named values; never execute a .env file as shell code."""
    values = {}
    if Path(path).is_file():
        for line in Path(path).read_text().splitlines():
            key, separator, value = line.partition('=')
            if separator and key in CONFIG_KEYS:
                values[key] = value
    for key in CONFIG_KEYS:
        if key in os.environ:
            values[key] = os.environ[key]
    return values


CONFIG_KEYS = ('INBOX_TEND_ADMIN_USERNAME', 'INBOX_TEND_ADMIN_PASSWORD_HASH',
               'INBOX_TEND_GOOGLE_CLIENT_ID', 'INBOX_TEND_GOOGLE_CLIENT_SECRET',
               'INBOX_TEND_GOOGLE_REDIRECT_URI', 'INBOX_TEND_TOKEN_KEY',
               'INBOX_TEND_SYNC_QUERY', 'INBOX_TEND_SYNC_SENT_QUERY', 'INBOX_TEND_SYNC_MAX_MESSAGES',
               'INBOX_TEND_ATTACHMENT_MAX_BYTES', 'INBOX_TEND_CODEX_BIN', 'INBOX_TEND_CLAUDE_BIN')


def cookie_token(header, name='it_session'):
    try:
        jar = SimpleCookie()
        jar.load(header or '')
        token = jar[name].value if name in jar else ''
        return token if _TOKEN.fullmatch(token) else ''
    except Exception:
        return ''


class AuthManager:
    def __init__(self, db_path, *, username='', password_hash=''):
        self.db_path = db_path
        self.failures = deque()
        self.lock = threading.Lock()
        with closing(connection(db_path)) as db:
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone() is None:
                if not username and not password_hash:
                    return
                if (not isinstance(username,str) or not 1 <= len(username) <= 254
                        or username != username.strip() or not self._valid_hash(password_hash)):
                    raise ValueError('Invalid admin bootstrap configuration')
                now = datetime.now(timezone.utc).isoformat()
                with db:
                    db.execute('INSERT INTO users(username,password_hash,role,created_at,updated_at) VALUES (?,?,?,?,?)',
                               (username, password_hash, 'admin', now, now))

    @staticmethod
    def _valid_hash(value):
        try:
            kind, count, salt, digest = value.split('$')
            return (kind == 'pbkdf2_sha256' and 200_000 <= int(count) <= 1_000_000
                    and len(_unb64(salt)) == 16 and len(_unb64(digest)) == 32)
        except (ValueError, TypeError, AttributeError):
            return False

    def authenticate(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 254 or len(password) > 1024:
            return 'invalid'
        now = time.monotonic()
        with self.lock:
            while self.failures and self.failures[0] < now - 300:
                self.failures.popleft()
            if len(self.failures) >= 10:
                return 'throttled'
        with closing(connection(self.db_path)) as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if row is None:
                global _DUMMY_HASH
                if _DUMMY_HASH is None:
                    _DUMMY_HASH = hash_password('not-a-valid-user-password')
                verify_password(password, _DUMMY_HASH)
                valid = False
            else:
                valid = verify_password(password, row['password_hash'])
            if not valid:
                with self.lock:
                    self.failures.append(time.monotonic())
                return 'invalid'
            with self.lock:
                self.failures.clear()
            return dict(row)

    def change_password(self, user_id, current, new, keep_token=''):
        """Returns 'ok', 'invalid' (wrong current password) or 'throttled'. Other sessions are signed out."""
        if not isinstance(current, str) or not isinstance(new, str) or len(current) > 1024:
            return 'invalid'
        with self.lock:
            now = time.monotonic()
            while self.failures and self.failures[0] < now - 300:
                self.failures.popleft()
            if len(self.failures) >= 10:
                return 'throttled'
        with closing(connection(self.db_path)) as db:
            row = db.execute('SELECT password_hash FROM users WHERE id=?', (user_id,)).fetchone()
            if row is None or not verify_password(current, row['password_hash']):
                with self.lock:
                    self.failures.append(time.monotonic())
                return 'invalid'
            if new == current:
                raise ValueError('The new password must differ from the current one')
            encoded = hash_password(new)  # ValueError (400) unless 12-1024 characters
            keep = sha256(keep_token.encode()).hexdigest() if keep_token else ''
            with db:
                db.execute('UPDATE users SET password_hash=?,updated_at=? WHERE id=?',
                           (encoded, datetime.now(timezone.utc).isoformat(), user_id))
                db.execute('DELETE FROM user_sessions WHERE user_id=? AND token_hash!=?', (user_id, keep))
            with self.lock:
                self.failures.clear()
            return 'ok'

    def new_session(self, user_id):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        with closing(connection(self.db_path)) as db:
            with db:
                db.execute('DELETE FROM user_sessions WHERE expires_at<=?', (now,))
                db.execute('INSERT INTO user_sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES (?,?,?,?,?)',
                           (sha256(token.encode()).hexdigest(), user_id, csrf, now + SESSION_IDLE_SECONDS,
                            datetime.now(timezone.utc).isoformat()))
        return token, csrf

    def session(self, token):
        if not token or not _TOKEN.fullmatch(token):
            return None
        with closing(connection(self.db_path)) as db:
            digest = sha256(token.encode()).hexdigest()
            row = db.execute('''SELECT s.csrf_token,s.expires_at,s.created_at,u.id,u.username,u.role
                                FROM user_sessions s JOIN users u ON u.id=s.user_id
                                WHERE s.token_hash=?''', (digest,)).fetchone()
            now = int(time.time())
            if not row or row['expires_at'] <= now:
                return None
            try:
                hard_limit = int(datetime.fromisoformat(row['created_at']).timestamp()) + SESSION_MAX_SECONDS
            except (TypeError, ValueError):
                hard_limit = row['expires_at']   # Unreadable start time: never extend such a session.
            if now >= hard_limit:
                return None
            wanted = min(now + SESSION_IDLE_SECONDS, hard_limit)
            if wanted - row['expires_at'] >= _RENEW_AFTER:   # Sliding expiry: using the app keeps you signed in.
                with db:
                    db.execute('UPDATE user_sessions SET expires_at=? WHERE token_hash=?', (wanted, digest))
            result = dict(row)
            result.pop('created_at')
            return result

    def revoke(self, token):
        if token and _TOKEN.fullmatch(token):
            with closing(connection(self.db_path)) as db:
                with db:
                    db.execute('DELETE FROM user_sessions WHERE token_hash=?', (sha256(token.encode()).hexdigest(),))
