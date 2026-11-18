"""Sign in with Google (OpenID Connect, authorisation-code flow with PKCE).

Only people who already have an account here can sign in this way; nobody is registered by it. A Google account is
accepted when it is linked to a user (by Google's stable `sub`, set under Settings -> Account) or when its verified
email address is exactly a user's username, in which case it is linked on that first sign-in. The Gmail access
(reading mail) is a separate OAuth grant; this only asks for `openid email`.
"""
from __future__ import annotations
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import urlopen

from gmail_web import AUTHORIZE, TOKEN, GmailConnectionError, _b64, _post

CALLBACK_PATH = '/api/auth/google/callback'
ISSUERS = ('https://accounts.google.com', 'accounts.google.com')
PENDING_SECONDS = 600
MAX_PENDING = 200


class GoogleLoginError(RuntimeError):
    """A sign-in problem that is safe to name to the user (`code` is shown on the login page)."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def redirect_uri(config):
    """The callback Google sends people back to: the Gmail callback's address with this path. It must also be listed
    as an authorised redirect URI of the same Google OAuth client."""
    base = urlparse(config.get('INBOX_TEND_GOOGLE_REDIRECT_URI') or '')
    return urlunparse((base.scheme, base.netloc, CALLBACK_PATH, '', '', ''))


def configured(config):
    if not all(config.get(k) for k in ('INBOX_TEND_GOOGLE_CLIENT_ID', 'INBOX_TEND_GOOGLE_CLIENT_SECRET', 'INBOX_TEND_GOOGLE_REDIRECT_URI')):
        return False
    uri = urlparse(config['INBOX_TEND_GOOGLE_REDIRECT_URI'])
    return bool(uri.netloc) and (uri.scheme == 'https' or (uri.scheme == 'http' and uri.hostname in ('localhost', '127.0.0.1')))


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def begin(db, config, purpose='login', user_id=None):
    """Start sign-in. Returns (authorization_url, browser_token). The browser token goes into a short-lived cookie: the
    callback only counts when it comes back through the same browser that started it."""
    if not configured(config):
        raise GmailConnectionError('Sign in with Google is not configured')
    if purpose not in ('login', 'link') or (purpose == 'link' and user_id is None):
        raise GmailConnectionError('Unknown sign-in purpose')
    state, verifier, nonce, browser = (secrets.token_urlsafe(32), secrets.token_urlsafe(64), secrets.token_urlsafe(24), secrets.token_urlsafe(32))
    now = int(time.time())
    with db:
        db.execute('DELETE FROM google_login_pending WHERE expires_at<?', (now,))
        db.execute('DELETE FROM google_login_pending WHERE state_hash NOT IN (SELECT state_hash FROM google_login_pending ORDER BY expires_at DESC LIMIT ?)', (MAX_PENDING,))
        db.execute('INSERT INTO google_login_pending(state_hash,browser_hash,verifier,nonce,purpose,user_id,expires_at) VALUES (?,?,?,?,?,?,?)',
                   (_hash(state), _hash(browser), verifier, nonce, purpose, user_id, now + PENDING_SECONDS))
    url = AUTHORIZE + '?' + urlencode({
        'client_id': config['INBOX_TEND_GOOGLE_CLIENT_ID'], 'redirect_uri': redirect_uri(config), 'response_type': 'code',
        'scope': 'openid email', 'state': state, 'nonce': nonce, 'code_challenge': _b64(hashlib.sha256(verifier.encode()).digest()),
        'code_challenge_method': 'S256', 'prompt': 'select_account', 'include_granted_scopes': 'false'})
    return url, browser


def _claims(id_token, config, nonce, now):
    """The id_token comes straight from Google's token endpoint over TLS, so (as OpenID Connect allows) its signature is not
    checked again; its claims are."""
    try:
        payload = id_token.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except (AttributeError, IndexError, ValueError):
        raise GoogleLoginError('failed') from None
    audience = claims.get('aud')
    if (not isinstance(claims, dict) or claims.get('iss') not in ISSUERS or audience != config['INBOX_TEND_GOOGLE_CLIENT_ID']
            or not isinstance(claims.get('exp'), int) or claims['exp'] <= now
            or not secrets.compare_digest(str(claims.get('nonce', '')), nonce)
            or not isinstance(claims.get('sub'), str) or not claims['sub'] or len(claims['sub']) > 255
            or not isinstance(claims.get('email'), str) or not 3 <= len(claims['email']) <= 254):
        raise GoogleLoginError('failed')
    if claims.get('email_verified') is not True:
        raise GoogleLoginError('unverified')
    return {'sub': claims['sub'], 'email': claims['email'].strip().lower()}


def finish(db, config, browser_token, state, code, *, opener=urlopen):
    """Check the callback and exchange the code. Returns (pending row, {'sub','email'}); raises GoogleLoginError."""
    if (not isinstance(state, str) or not 0 < len(state) <= 200 or not isinstance(code, str) or not 0 < len(code) <= 4096
            or not isinstance(browser_token, str) or not browser_token):
        raise GoogleLoginError('failed')
    now = int(time.time())
    with db:
        row = db.execute('SELECT * FROM google_login_pending WHERE state_hash=? AND browser_hash=? AND expires_at>?',
                         (_hash(state), _hash(browser_token), now)).fetchone()
        if row is None:
            raise GoogleLoginError('expired')
        db.execute('DELETE FROM google_login_pending WHERE state_hash=?', (row['state_hash'],))
    try:
        result = _post(TOKEN, {'client_id': config['INBOX_TEND_GOOGLE_CLIENT_ID'], 'client_secret': config['INBOX_TEND_GOOGLE_CLIENT_SECRET'],
                               'code': code, 'code_verifier': row['verifier'], 'redirect_uri': redirect_uri(config),
                               'grant_type': 'authorization_code'}, opener=opener)
    except GmailConnectionError:
        raise GoogleLoginError('failed') from None
    return row, _claims(result.get('id_token'), config, row['nonce'], now)


def linked_user(db, sub):
    row = db.execute('SELECT user_id FROM google_identities WHERE sub=?', (sub,)).fetchone()
    return row['user_id'] if row else None


def link(db, user_id, claims):
    """Link a Google account to a user. A Google account belongs to one user only."""
    owner = linked_user(db, claims['sub'])
    if owner is not None and owner != user_id:
        raise GoogleLoginError('taken')
    with db:
        db.execute('INSERT INTO google_identities(sub,user_id,email,linked_at) VALUES (?,?,?,?) '
                   'ON CONFLICT(sub) DO UPDATE SET email=excluded.email', (claims['sub'], user_id, claims['email'], int(time.time())))


def sign_in(db, claims):
    """The user id for this Google account, or raises GoogleLoginError('unknown'): linked accounts win; otherwise a
    verified email that is exactly a username is linked now. Nothing else gets in."""
    user_id = linked_user(db, claims['sub'])
    if user_id is not None:
        db.execute('UPDATE google_identities SET email=? WHERE sub=?', (claims['email'], claims['sub']))
        db.commit()
        return user_id
    row = db.execute('SELECT id FROM users WHERE lower(username)=?', (claims['email'],)).fetchone()
    if row is None:
        raise GoogleLoginError('unknown')
    link(db, row['id'], claims)
    return row['id']


def links(db, user_id):
    return [{'id': r['id'], 'email': r['email'], 'linked_at': r['linked_at']}
            for r in db.execute('SELECT id,email,linked_at FROM google_identities WHERE user_id=? ORDER BY id', (user_id,))]


def unlink(db, user_id, link_id):
    with db:
        return db.execute('DELETE FROM google_identities WHERE id=? AND user_id=?', (link_id, user_id)).rowcount > 0
