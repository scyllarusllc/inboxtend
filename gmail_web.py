"""Session-bound Google web OAuth and encrypted, per-user Gmail credentials."""
from __future__ import annotations
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # Everything except Gmail works without the optional dependency.
    AESGCM = None
from connectors.gmail_readonly import SCOPES, GmailReadError, list_message_ids, get_message_metadata

AUTHORIZE = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
SCOPE = SCOPES[0]

class GmailConnectionError(RuntimeError):
    pass

def _key(config):
    if AESGCM is None:
        raise GmailConnectionError('Gmail encryption dependency is unavailable')
    try:
        key = base64.urlsafe_b64decode(config['INBOX_TEND_TOKEN_KEY'] + '===')
        if len(key) != 32: raise ValueError()
        return key
    except (KeyError, ValueError):
        raise GmailConnectionError('Gmail encryption key is unavailable') from None

def configured(config):
    required = ('INBOX_TEND_GOOGLE_CLIENT_ID','INBOX_TEND_GOOGLE_CLIENT_SECRET',
                'INBOX_TEND_GOOGLE_REDIRECT_URI','INBOX_TEND_TOKEN_KEY')
    if not all(config.get(k) for k in required): return False
    uri = urlparse(config['INBOX_TEND_GOOGLE_REDIRECT_URI'])
    if (uri.path != '/api/connectors/gmail/callback' or uri.query or uri.fragment or
        (uri.scheme != 'https' and not (uri.scheme == 'http' and uri.hostname in ('localhost','127.0.0.1')))):
        return False
    try:
        _key(config)
        return True
    except GmailConnectionError:
        return False

def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')

def _post(url, fields, *, opener=urlopen):
    req = Request(url, data=urlencode(fields).encode('ascii'), method='POST',
                  headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'})
    try:
        with opener(req, timeout=15) as response: raw = response.read(100_001)
        if len(raw) > 100_000: raise ValueError()
        data = json.loads(raw)
        if not isinstance(data, dict): raise ValueError()
        return data
    except HTTPError as exc:
        raise GmailConnectionError(f'Google authorization failed (HTTP {exc.code})') from None
    except (URLError, OSError, TimeoutError, ValueError):
        raise GmailConnectionError('Google authorization request failed') from None

def begin(db, config, user_id, session_token):
    if not configured(config): raise GmailConnectionError('Gmail is not configured')
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    session_hash = hashlib.sha256(session_token.encode()).hexdigest()
    with db:
        db.execute('DELETE FROM gmail_oauth_pending WHERE user_id=? OR expires_at<?', (user_id,int(time.time())))
        db.execute('INSERT INTO gmail_oauth_pending(state_hash,user_id,session_hash,verifier,expires_at) VALUES (?,?,?,?,?)',
                   (hashlib.sha256(state.encode()).hexdigest(),user_id,session_hash,verifier,int(time.time())+300))
    return AUTHORIZE + '?' + urlencode({
        'client_id':config['INBOX_TEND_GOOGLE_CLIENT_ID'],
        'redirect_uri':config['INBOX_TEND_GOOGLE_REDIRECT_URI'],
        'response_type':'code','scope':SCOPE,'state':state,'code_challenge':challenge,
        'code_challenge_method':'S256','access_type':'offline','prompt':'consent',
        'include_granted_scopes':'false'})

def finish(db, config, user_id, session_token, state, code, *, opener=urlopen):
    if not isinstance(state,str) or len(state)>200 or not isinstance(code,str) or not code or len(code)>4096:
        raise GmailConnectionError('Invalid OAuth callback')
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    session_hash = hashlib.sha256(session_token.encode()).hexdigest()
    with db:
        row = db.execute('SELECT * FROM gmail_oauth_pending WHERE state_hash=? AND user_id=? AND session_hash=? AND expires_at>?',
                         (state_hash,user_id,session_hash,int(time.time()))).fetchone()
        if not row: raise GmailConnectionError('OAuth request expired or belongs to another session')
        db.execute('DELETE FROM gmail_oauth_pending WHERE state_hash=?',(state_hash,))
    result = _post(TOKEN, {'client_id':config['INBOX_TEND_GOOGLE_CLIENT_ID'],
                           'client_secret':config['INBOX_TEND_GOOGLE_CLIENT_SECRET'],
                           'code':code,'code_verifier':row['verifier'],
                           'redirect_uri':config['INBOX_TEND_GOOGLE_REDIRECT_URI'],
                           'grant_type':'authorization_code'},opener=opener)
    if (set(result.get('scope','').split()) != {SCOPE} or result.get('token_type','').lower() != 'bearer'
        or not isinstance(result.get('refresh_token'),str) or not result['refresh_token']
        or not isinstance(result.get('access_token'),str) or not result['access_token']):
        raise GmailConnectionError('Google did not grant the required read-only offline access')
    key = _key(config)
    nonce = secrets.token_bytes(12)
    encrypted = nonce + AESGCM(key).encrypt(nonce,result['refresh_token'].encode(),str(user_id).encode())
    with db:
        db.execute('INSERT INTO gmail_connections(user_id,refresh_token,connected_at) VALUES (?,?,?) '
                   'ON CONFLICT(user_id) DO UPDATE SET refresh_token=excluded.refresh_token,connected_at=excluded.connected_at',
                   (user_id,encrypted,int(time.time())))

def status(db,user_id):
    return db.execute('SELECT 1 FROM gmail_connections WHERE user_id=?',(user_id,)).fetchone() is not None

def disconnect(db,user_id):
    with db:
        db.execute('DELETE FROM gmail_oauth_pending WHERE user_id=?',(user_id,))
        db.execute('DELETE FROM gmail_connections WHERE user_id=?',(user_id,))

def _access_token(db,config,user_id,*,opener=urlopen):
    row = db.execute('SELECT refresh_token FROM gmail_connections WHERE user_id=?',(user_id,)).fetchone()
    if not row: raise GmailConnectionError('Gmail is disconnected')
    try:
        encrypted = row['refresh_token']
        refresh = AESGCM(_key(config)).decrypt(encrypted[:12],encrypted[12:],str(user_id).encode()).decode()
    except Exception:
        raise GmailConnectionError('Stored Gmail credential cannot be decrypted') from None
    result = _post(TOKEN, {'client_id':config['INBOX_TEND_GOOGLE_CLIENT_ID'],
                           'client_secret':config['INBOX_TEND_GOOGLE_CLIENT_SECRET'],
                           'refresh_token':refresh,'grant_type':'refresh_token'},opener=opener)
    if (not isinstance(result.get('access_token'),str) or not result['access_token']
        or result.get('token_type','').lower() != 'bearer'
        or ('scope' in result and set(result['scope'].split()) != {SCOPE})):
        raise GmailConnectionError('Google did not return read-only access')
    return result['access_token']

def _summary(item):
    headers = item['headers']
    return {'id':item['id'],'thread_id':item['thread_id'],
            'from':headers.get('from',''),'subject':headers.get('subject',''),
            'date':headers.get('date',''),'snippet':item['snippet']}

def inbox(db,config,user_id,*,opener=urlopen):
    token = _access_token(db,config,user_id,opener=opener)
    ids = list_message_ids(token,max_results=20,query='in:inbox')
    return [_summary(get_message_metadata(token,message_id)) for message_id in ids['message_ids']]

def message(db,config,user_id,message_id,*,opener=urlopen):
    """Fetch one message's headers and snippet for the signed-in user (never trusts client-supplied content)."""
    token = _access_token(db,config,user_id,opener=opener)
    try:
        return _summary(get_message_metadata(token,message_id))
    except ValueError:
        raise GmailReadError('Invalid message ID') from None
