"""Session-bound Google web OAuth and encrypted, per-user Gmail credentials."""
from __future__ import annotations
import base64
import hashlib
import json
import secrets
import threading
import time
import concurrent.futures as cf
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from email.utils import parseaddr
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # Everything except Gmail works without the optional dependency.
    AESGCM = None
from connectors.gmail_modify import MODIFY_SCOPE, GmailWriteError, set_unread as gmail_set_unread, clear_unread_many as gmail_clear_unread_many
from connectors.gmail_readonly import SCOPES, GmailReadError, GmailRateLimitError, cooldown_remaining, get_totals, get_address, list_message_ids, get_message_full, get_attachment
from tasks import connection, peer_for
import settings as prefs
import mailsearch
import flags
import topics
from mail_content import readable_body, looks_like_html

AUTHORIZE = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
SCOPE = SCOPES[0]
LEVELS = ('readonly','modify')

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

def begin(db, config, user_id, session_token, level='readonly', next_page='inbox', account_id=None):
    """Start Google sign-in. With `account_id` an existing account is re-authorised (for example to allow marking mail
    read); without it, a new account is added (or, if it turns out to be one already connected, refreshed)."""
    if not configured(config): raise GmailConnectionError('Gmail is not configured')
    if level not in LEVELS: raise GmailConnectionError('Unknown access level')
    if next_page not in ('inbox','settings'): raise GmailConnectionError('Unknown return page')
    hint = None
    if account_id is not None:
        account = db.execute('SELECT email FROM gmail_connections WHERE id=? AND user_id=?',(account_id,user_id)).fetchone()
        if account is None: raise GmailConnectionError('Unknown Gmail account')
        hint = account['email'] or None
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    session_hash = hashlib.sha256(session_token.encode()).hexdigest()
    with db:
        db.execute('DELETE FROM gmail_oauth_pending WHERE user_id=? OR expires_at<?', (user_id,int(time.time())))
        db.execute('INSERT INTO gmail_oauth_pending(state_hash,user_id,session_hash,verifier,expires_at,level,next,account_id) VALUES (?,?,?,?,?,?,?,?)',
                   (hashlib.sha256(state.encode()).hexdigest(),user_id,session_hash,verifier,int(time.time())+300,level,next_page,account_id))
    return AUTHORIZE + '?' + urlencode(dict(({'login_hint':hint} if hint else {}),**{
        'client_id':config['INBOX_TEND_GOOGLE_CLIENT_ID'],
        'redirect_uri':config['INBOX_TEND_GOOGLE_REDIRECT_URI'],
        'response_type':'code','scope':SCOPE if level == 'readonly' else MODIFY_SCOPE,'state':state,'code_challenge':challenge,
        'code_challenge_method':'S256','access_type':'offline','prompt':'consent' if account_id is not None else 'select_account consent',
        'include_granted_scopes':'false'}))

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
    granted = set(result.get('scope','').split())
    scope_ok = granted == {SCOPE} if row['level'] == 'readonly' else (MODIFY_SCOPE in granted and granted <= {SCOPE, MODIFY_SCOPE})
    if (not scope_ok or result.get('token_type','').lower() != 'bearer'
        or not isinstance(result.get('refresh_token'),str) or not result['refresh_token']
        or not isinstance(result.get('access_token'),str) or not result['access_token']):
        raise GmailConnectionError('Google did not grant the requested access')
    key = _key(config)
    nonce = secrets.token_bytes(12)
    encrypted = nonce + AESGCM(key).encrypt(nonce,result['refresh_token'].encode(),str(user_id).encode())
    try: email = get_address(result['access_token'],opener=opener)
    except GmailReadError: email = ''
    expected = None
    if row['account_id'] is not None:
        expected = db.execute('SELECT id,email FROM gmail_connections WHERE id=? AND user_id=?',(row['account_id'],user_id)).fetchone()
        if expected is None: raise GmailConnectionError('The Gmail account was removed meanwhile')
        if expected['email'] and email and email != expected['email']:
            raise GmailConnectionError(f"You signed in as {email}, not {expected['email']}")
    with db:
        target = expected['id'] if expected is not None else None
        if target is None:
            found = db.execute('SELECT id FROM gmail_connections WHERE user_id=? AND email=?',(user_id,email)).fetchone()
            target = found['id'] if found else None
        if target is not None:
            db.execute('UPDATE gmail_connections SET refresh_token=?,connected_at=?,scopes=?,email=COALESCE(NULLIF(?,\'\'),email) WHERE id=?',
                       (encrypted,int(time.time()),' '.join(sorted(granted)),email,target))
        else:
            cursor = db.execute('INSERT INTO gmail_connections(user_id,email,refresh_token,connected_at,scopes) VALUES (?,?,?,?,?)',
                                (user_id,email,encrypted,int(time.time()),' '.join(sorted(granted))))
            first = db.execute("SELECT 1 FROM gmail_connections WHERE user_id=? AND prefix='' AND id<>?",(user_id,cursor.lastrowid)).fetchone() is None
            if not first: db.execute('UPDATE gmail_connections SET prefix=? WHERE id=?',(f'{cursor.lastrowid}~',cursor.lastrowid))
    return row['level'], row['next']

def accounts(db,user_id):
    """The user's connected Gmail accounts, oldest first."""
    gmail = [dict(id=r['id'],email=r['email'],prefix=r['prefix'],connected_at=r['connected_at'],last_sync=r['last_sync'],write=MODIFY_SCOPE in set(r['scopes'].split()),provider='gmail') for r in db.execute('SELECT * FROM gmail_connections WHERE user_id=? ORDER BY id',(user_id,))]
    imap = [dict(id=r['id'],email=r['email'],prefix=f"imap-{r['id']}~",connected_at=r['connected_at'],last_sync=r['last_sync'],write=False,provider='imap') for r in db.execute('SELECT * FROM imap_connections WHERE user_id=? ORDER BY id',(user_id,))]
    return gmail + imap

def _account(db,user_id,account_id=None):
    """One of the user's accounts: the given one, or (no id) the first. Raises when there is none."""
    row = (db.execute('SELECT * FROM gmail_connections WHERE id=? AND user_id=?',(account_id,user_id)).fetchone() if account_id is not None
           else db.execute('SELECT * FROM gmail_connections WHERE user_id=? ORDER BY id LIMIT 1',(user_id,)).fetchone())
    if not row: raise GmailConnectionError('Gmail is disconnected' if account_id is None else 'Unknown Gmail account')
    return row

def _granted(row):
    return set((row['scopes'] or '').split()) or {SCOPE}   # Connections made before write access existed are read-only.

def write_access(db,user_id,account_id=None):
    """True when the user granted gmail.modify (an opt-in; the default connection is read-only): for the given
    account, or (no id) for at least one of them."""
    return any(a['write'] for a in accounts(db,user_id) if account_id is None or a['id'] == account_id)

def status(db,user_id):
    return bool(accounts(db,user_id))

def label(account):
    """Short name for an account in the UI and in messages."""
    return account['email'] or f"Gmail account {account['id']}"

def _split(db,user_id,message_id):
    """Account and Gmail's own id for a stored message id (stored ids of later accounts start with '<account id>~')."""
    if '~' in message_id:
        head, _, gmail_id = message_id.partition('~')
        return _account(db,user_id,int(head) if head.isdigit() else -1), gmail_id
    row = db.execute('SELECT account_id FROM gmail_messages WHERE user_id=? AND message_id=?',(user_id,message_id)).fetchone()
    return _account(db,user_id,row['account_id'] if row and row['account_id'] is not None else None), message_id

def disconnect(db,user_id,account_id=None):
    """Remove one account (with all of its stored mail) or, without an id, every account of the user. The mail of
    other accounts is untouched. Tasks are kept."""
    with db:
        db.execute('DELETE FROM gmail_oauth_pending WHERE user_id=?',(user_id,))
        if account_id is not None:
            _account(db,user_id,account_id)
            own = 'SELECT message_id FROM gmail_messages WHERE user_id=? AND account_id=?'
            for table in ('message_topics','message_websites','message_translations','gmail_attachments'):
                db.execute(f'DELETE FROM {table} WHERE user_id=? AND message_id IN ({own})',(user_id,user_id,account_id))
            db.execute('DELETE FROM gmail_messages WHERE user_id=? AND account_id=?',(user_id,account_id))
            db.execute('DELETE FROM gmail_connections WHERE id=? AND user_id=?',(account_id,user_id))
            db.execute('DELETE FROM sender_countries WHERE user_id=? AND peer_email NOT IN (SELECT peer_email FROM gmail_messages WHERE user_id=?)',(user_id,user_id))
            if status(db,user_id): return
        gmail_ids = 'SELECT message_id FROM gmail_messages WHERE user_id=? AND (account_id IS NULL OR account_id>=0)'
        for table in ('message_topics','message_websites','message_translations','gmail_attachments'):
            db.execute(f'DELETE FROM {table} WHERE user_id=? AND message_id IN ({gmail_ids})',(user_id,user_id))
        db.execute('DELETE FROM gmail_messages WHERE user_id=? AND (account_id IS NULL OR account_id>=0)',(user_id,))
        db.execute('DELETE FROM gmail_connections WHERE user_id=?',(user_id,))

def _scope_matches(returned,expected):
    if MODIFY_SCOPE in expected:
        return MODIFY_SCOPE in returned and returned <= {SCOPE,MODIFY_SCOPE}
    return returned == {SCOPE}

def _access_token(db,config,user_id,*,account_id=None,opener=urlopen):
    row = _account(db,user_id,account_id)
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
        or ('scope' in result and not _scope_matches(set(result['scope'].split()),_granted(row)))):
        raise GmailConnectionError('Google returned different access than was granted; reconnect Gmail')
    return result['access_token']

DEFAULTS = {'INBOX_TEND_SYNC_QUERY':'in:inbox','INBOX_TEND_SYNC_SENT_QUERY':'in:sent','INBOX_TEND_SYNC_MAX_MESSAGES':200,
            'INBOX_TEND_ATTACHMENT_MAX_BYTES':10 * 1024 * 1024}

def _int_setting(config,key,lo,hi):
    try: value = int(config.get(key,DEFAULTS[key]))
    except (TypeError,ValueError): value = DEFAULTS[key]
    return max(lo,min(hi,value))

def sync_settings(config, user=None):
    """Sync limits: the user's saved preferences (see settings.py) win over the .env values."""
    user = user or {}
    query = str(config.get('INBOX_TEND_SYNC_QUERY') or DEFAULTS['INBOX_TEND_SYNC_QUERY'])[:1024]
    sent_query = str(config.get('INBOX_TEND_SYNC_SENT_QUERY') or DEFAULTS['INBOX_TEND_SYNC_SENT_QUERY'])[:1024]
    max_messages = user.get('sync_max_messages', _int_setting(config,'INBOX_TEND_SYNC_MAX_MESSAGES',1,5000))
    attachment_max = (user['attachment_max_mb'] * 1024 * 1024 if 'attachment_max_mb' in user
                      else _int_setting(config,'INBOX_TEND_ATTACHMENT_MAX_BYTES',0,100 * 1024 * 1024))
    return {'query':query,'sent_query':sent_query,'max_messages':max_messages,
            'attachment_max':attachment_max,'sync_sent':user.get('sync_sent',True)}

def _is_unread(r):
    """Unread = Gmail's UNREAD label as of the last sync, unless the user marked it read/unread here."""
    override = r['read_override'] if 'read_override' in r.keys() else None
    return '"UNREAD"' in (r['label_ids'] or '') if override is None else override == 0

def _row(r):
    return {'id':r['message_id'],'thread_id':r['thread_id'],'from':r['from_addr'],'to':r['to_addr'],
            'subject':r['subject'],'date':r['date_header'],'internal_date':r['internal_date'],'snippet':r['snippet'],
            'has_full':bool(r['has_full']),
            'direction':'sent' if '"SENT"' in (r['label_ids'] or '') else 'received','unread':_is_unread(r),
            'peer':r['peer_email'] if 'peer_email' in r.keys() else '',
            'account_id':r['account_id'] if 'account_id' in r.keys() else None}

def set_read(db,config,user_id,message_id,read,*,write=False,opener=urlopen):
    """Mark a message read or unread. Always recorded in Inbox Tend; with `write` (the user's choice) and the
    gmail.modify grant of the message's own account, the UNREAD label is changed in Gmail too. A failed Gmail update
    keeps the local state and is reported, never raised. Returns None if the message is unknown."""
    row = db.execute('SELECT label_ids,account_id FROM gmail_messages WHERE user_id=? AND message_id=?',(user_id,message_id)).fetchone()
    if row is None: return None
    want_unread = not read
    outcome = {'unread':want_unread,'gmail_changed':False,'imap_changed':False,'gmail_error':None}
    if row['account_id'] is not None and row['account_id'] < 0:
        import imap_web
        try:
            imap_web.set_seen(db,config,user_id,row['account_id'],[message_id],read)
            labels = [x for x in json.loads(row['label_ids'] or '[]') if x != 'UNREAD']
            if want_unread: labels.append('UNREAD')
            with db:
                db.execute('UPDATE gmail_messages SET label_ids=?,read_override=NULL WHERE user_id=? AND message_id=?',
                           (json.dumps(labels),user_id,message_id))
            outcome['imap_changed'] = True
            return outcome
        except (imap_web.IMAPError,ValueError) as exc:
            outcome['gmail_error'] = str(exc)
    if write and not message_id.startswith('imap-'):
        try: account, gmail_id = _split(db,user_id,message_id)
        except GmailConnectionError as exc: account, gmail_id = None, ''; outcome['gmail_error'] = str(exc)
        if account is not None and MODIFY_SCOPE not in _granted(account):
            outcome['gmail_error'] = f"Write access to Gmail has not been granted for {label(account)} (Settings → Gmail connection)."
        elif account is not None:
            try:
                gmail_set_unread(_access_token(db,config,user_id,account_id=account['id'],opener=opener),gmail_id,want_unread)
                try: labels = json.loads(row['label_ids'] or '[]')
                except ValueError: labels = []
                labels = [x for x in labels if x != 'UNREAD'] + (['UNREAD'] if want_unread else [])
                with db:  # Gmail now agrees, so no local override is needed.
                    db.execute('UPDATE gmail_messages SET label_ids=?,read_override=NULL WHERE user_id=? AND message_id=?',
                               (json.dumps(labels),user_id,message_id))
                outcome['gmail_changed'] = True
                return outcome
            except (GmailConnectionError,GmailWriteError,ValueError) as exc:
                outcome['gmail_error'] = str(exc)
    gmail_unread = '"UNREAD"' in (row['label_ids'] or '')
    override = None if want_unread == gmail_unread else (0 if want_unread else 1)  # Back in step with Gmail: follow it again.
    with db:
        db.execute('UPDATE gmail_messages SET read_override=? WHERE user_id=? AND message_id=?',(override,user_id,message_id))
    return outcome

def unread_ids(db,user_id,folder='inbox',search=None,account_id=None):
    """Stored ids of the unread messages in a folder that match the search and account filter."""
    return [r['id'] for r in cached_inbox(db,user_id,folder,search,account_id) if r['unread']]

def unread_among(db,user_id,ids):
    """Of the given stored ids, the ones that are this user's messages and still unread."""
    ids = list(dict.fromkeys(ids))
    marks = ','.join('?' * len(ids))
    return [r['message_id'] for r in db.execute(f'SELECT message_id,label_ids,read_override FROM gmail_messages WHERE user_id=? AND message_id IN ({marks})',(user_id,*ids))
            if _is_unread(r)]

def mark_all_read(db,config,user_id,ids,*,write=False,opener=urlopen):
    """Mark many messages read: always in Inbox Tend, and with `write` also in Gmail for the messages whose own account
    has the gmail.modify grant (one batch request per account). A failed Gmail update keeps the local change and is
    reported, never raised. Returns {'marked', 'gmail_changed', 'gmail_error'}."""
    ids = list(dict.fromkeys(ids))
    outcome = {'marked':len(ids),'gmail_changed':0,'imap_changed':0,'gmail_error':None}
    if not ids: return outcome
    marks = ','.join('?' * len(ids))
    with db:   # Read here; if Gmail still says unread, remember the local override (cleared below once Gmail agrees).
        db.execute("UPDATE gmail_messages SET read_override=CASE WHEN label_ids LIKE '%\"UNREAD\"%' THEN 1 ELSE NULL END "
                   f"WHERE user_id=? AND message_id IN ({marks})",(user_id,*ids))
    by_account, errors, skipped = {}, [], {}
    for r in db.execute(f'SELECT message_id,account_id FROM gmail_messages WHERE user_id=? AND message_id IN ({marks})',(user_id,*ids)):
        by_account.setdefault(r['account_id'],[]).append(r['message_id'])
    for account_id,stored in by_account.items():
        if account_id is not None and account_id < 0:
            import imap_web
            try:
                imap_web.set_seen(db,config,user_id,account_id,stored,True)
                with db:
                    for mid in stored:
                        labels = [x for x in json.loads(db.execute('SELECT label_ids FROM gmail_messages WHERE user_id=? AND message_id=?',(user_id,mid)).fetchone()[0] or '[]') if x != 'UNREAD']
                        db.execute('UPDATE gmail_messages SET label_ids=?,read_override=NULL WHERE user_id=? AND message_id=?',(json.dumps(labels),user_id,mid))
                outcome['imap_changed'] += len(stored)
            except (imap_web.IMAPError,ValueError) as exc: errors.append(str(exc))
            continue
        if not write: continue
        try: account = _account(db,user_id,account_id)
        except GmailConnectionError as exc: errors.append(str(exc)); continue
        if MODIFY_SCOPE not in _granted(account):
            skipped[label(account)] = len(stored); continue
        try:
            gmail_clear_unread_many(_access_token(db,config,user_id,account_id=account['id'],opener=opener),[m[len(account['prefix']):] for m in stored],opener=opener)
        except (GmailConnectionError,GmailWriteError,ValueError) as exc:
            errors.append(f'{label(account)}: {exc}'); continue
        with db:
            for m in stored:
                labels = [x for x in json.loads(db.execute('SELECT label_ids FROM gmail_messages WHERE user_id=? AND message_id=?',(user_id,m)).fetchone()[0] or '[]') if x != 'UNREAD']
                db.execute('UPDATE gmail_messages SET label_ids=?,read_override=NULL WHERE user_id=? AND message_id=?',(json.dumps(labels),user_id,m))
        outcome['gmail_changed'] += len(stored)
    notes = errors + [f'{n} message(s) of {name} were not changed in Gmail: no write access for that account' for name,n in skipped.items()]
    outcome['gmail_error'] = '; '.join(notes) or None
    return outcome

def _fetch(token,message_id,attachment_max):
    """Network only (runs in worker threads): the message plus the attachments small enough to keep."""
    item = get_message_full(token,message_id)
    for a in item['attachments']:
        content, reason = a.pop('data'), ''
        if content is None and a.get('attachment_id'):
            if a['size'] > attachment_max: reason = 'larger than the size limit'
            else:
                try: content = get_attachment(token,message_id,a['attachment_id'],max_bytes=attachment_max)
                except (GmailReadError,ValueError): reason = 'download failed'
        elif content is None: reason = 'no content'
        if content is not None and len(content) > attachment_max: content, reason = None, 'larger than the size limit'
        a['content'], a['skipped_reason'] = content, reason
    return item

def _store(db,user_id,item,in_inbox=0,account=None):  # Folder flags are set on insert only; run_sync's reconcile owns them afterwards.
    """Save one downloaded message. `account` (a gmail_connections row) says whose it is; its prefix is put before
    Gmail's ids so that two accounts can never overwrite each other's mail."""
    prefix = account['prefix'] if account is not None else ''
    account_id = account['id'] if account is not None else None
    sid = prefix + item['id']
    thread = prefix + item['thread_id'] if item.get('thread_id') else ''
    h = item['headers']
    db.execute('''INSERT INTO gmail_messages
        (user_id,message_id,thread_id,from_addr,subject,date_header,snippet,internal_date,in_inbox,fetched_at,
         to_addr,cc_addr,label_ids,headers_json,body_text,body_html,size_estimate,peer_email,peer_name,has_full,account_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)
        ON CONFLICT(user_id,message_id) DO UPDATE SET thread_id=excluded.thread_id,from_addr=excluded.from_addr,
          subject=excluded.subject,date_header=excluded.date_header,snippet=excluded.snippet,
          internal_date=excluded.internal_date,fetched_at=excluded.fetched_at,
          to_addr=excluded.to_addr,cc_addr=excluded.cc_addr,label_ids=excluded.label_ids,
          headers_json=excluded.headers_json,body_text=excluded.body_text,body_html=excluded.body_html,
          size_estimate=excluded.size_estimate,peer_email=excluded.peer_email,peer_name=excluded.peer_name,has_full=1,
          account_id=COALESCE(gmail_messages.account_id,excluded.account_id)''',
        (user_id,sid,thread,h.get('from',''),h.get('subject',''),h.get('date',''),
         item.get('snippet',''),item.get('internal_date',0),in_inbox,int(time.time()),
         h.get('to',''),h.get('cc',''),json.dumps(item.get('label_ids',[])),json.dumps(item.get('all_headers',[])),
         item.get('body_text',''),item.get('body_html',''),item.get('size_estimate',0),*peer_for(json.dumps(item.get('label_ids',[])),h.get('from',''),h.get('to','')),
         account_id))
    turned = attachment_rotations(db,user_id,sid)
    db.execute('DELETE FROM gmail_attachments WHERE user_id=? AND message_id=?',(user_id,sid))
    for a in item.get('attachments',[]):
        db.execute('INSERT INTO gmail_attachments(user_id,message_id,part_id,filename,mime_type,size_bytes,content,skipped_reason,rotation) '
                   'VALUES (?,?,?,?,?,?,?,?,?)',(user_id,sid,a['part_id'],a['filename'],a['mime_type'],
                   len(a['content']) if a['content'] is not None else a['size'],a['content'],a['skipped_reason'],turned.get(a['part_id'],0)))

def attachment_rotations(db,user_id,message_id):
    """How the user turned each image, so a re-sync (which rewrites the rows) keeps it."""
    return {r['part_id']:r['rotation'] for r in db.execute('SELECT part_id,rotation FROM gmail_attachments WHERE user_id=? AND message_id=?',(user_id,message_id))}


def rotate_attachment(db,user_id,message_id,attachment_id,rotation):
    if rotation not in (0,90,180,270): raise ValueError('Rotation must be 0, 90, 180 or 270')
    with db:
        return db.execute('UPDATE gmail_attachments SET rotation=? WHERE user_id=? AND message_id=? AND id=?',(rotation,user_id,message_id,attachment_id)).rowcount == 1


def last_sync(db,user_id,account_id=None):
    """When mail was last synced: for the given account, or (no id) the most recent sync of any account."""
    if account_id is not None:
        table = 'imap_connections' if account_id < 0 else 'gmail_connections'
        row = db.execute(f'SELECT last_sync FROM {table} WHERE user_id=? AND id=?',(user_id,account_id)).fetchone()
        return row[0] if row else None
    return max((a['last_sync'] or 0 for a in accounts(db,user_id)),default=0) or None

FOLDERS = {'inbox':'in_inbox','sent':'in_sent'}

def cached_inbox(db,user_id,folder='inbox',search=None,account_id=None):
    """Folder rows from the local SQLite copy; makes no Gmail request. `search` is a mailsearch.parse() result."""
    column = FOLDERS[folder]
    where, params = mailsearch.condition(search) if search else ('',[])
    rows = db.execute('SELECT m.*,(SELECT COUNT(*) FROM gmail_attachments a WHERE a.user_id=m.user_id AND a.message_id=m.message_id) AS n_att '
                      f'FROM gmail_messages m WHERE m.user_id=? AND m.{column}=1' + (' AND m.account_id=?' if account_id is not None else '') + (f' AND ({where})' if where else '') +
                      ' ORDER BY m.internal_date DESC, m.message_id DESC',(user_id,*(() if account_id is None else (account_id,)),*params))
    return topics.attach(db,user_id,flags.attach(db,user_id,[dict(_row(r),attachment_count=r['n_att']) for r in rows]))

VIEWS = ('messages','threads','people')

def grouped_inbox(db,user_id,folder,view,search=None,account_id=None):
    """The folder as the user wants to see it. `messages`: one entry per message. `threads`: one per Gmail
    conversation. `people`: one per other party (sender of received mail, first recipient of sent mail).
    A group is listed in a folder when any of its messages is in it, and holds all stored messages of the
    conversation or person, from both folders. Purely local: no Gmail request."""
    if view == 'messages':
        return {'messages':cached_inbox(db,user_id,folder,search,account_id)}
    column = FOLDERS[folder]
    where, params = mailsearch.condition(search) if search else ('',[])
    rows = db.execute('SELECT m.account_id,m.message_id,m.thread_id,m.from_addr,m.to_addr,m.subject,m.date_header,m.snippet,m.internal_date,m.label_ids,m.read_override,'
                      f'm.peer_email,m.peer_name,m.{column} AS in_folder,' + (f'CASE WHEN {where} THEN 1 ELSE 0 END' if where else '1') + ' AS hit,'
                      '(SELECT COUNT(*) FROM gmail_attachments a WHERE a.user_id=m.user_id AND a.message_id=m.message_id) AS n_att '
                      'FROM gmail_messages m WHERE m.user_id=?' + (' AND m.account_id=?' if account_id is not None else '') + ' ORDER BY m.internal_date DESC, m.message_id DESC',
                      (*params,user_id,*(() if account_id is None else (account_id,))))  # Metadata-only rows (older syncs) group fine too.
    groups, names, names_to = {}, {}, {}
    for r in rows:
        if r['peer_email'] and r['peer_name']:  # Newest display name per address; names people chose for themselves win.
            table = names_to if '"SENT"' in (r['label_ids'] or '') else names
            table.setdefault(r['peer_email'], r['peer_name'])
        if view == 'threads' and r['thread_id']:
            key = 't:' + r['thread_id']
        elif view == 'people' and r['peer_email']:
            key = 'p:' + r['peer_email']
        else:
            key = 'm:' + r['message_id']
        group = groups.setdefault(key,{'id':key,'kind':'thread' if view == 'threads' else 'person','messages':[],'in_folder':False,
                                       'address':r['peer_email'] if view == 'people' else ''})
        group['messages'].append(dict(_row(r),attachment_count=r['n_att']))
        group['in_folder'] = group['in_folder'] or bool(r['in_folder'] and r['hit'])   # With a search, a hit inside this folder lists the group.
        group['matches'] = group.get('matches',0) + (1 if r['in_folder'] and r['hit'] else 0)
    def who(address, fallback=''):
        return names.get(address) or names_to.get(address) or (address.split('@')[0] if address else '') or fallback
    result = []
    for group in groups.values():
        if not group.pop('in_folder'): continue
        messages = group['messages']          # newest first
        group['last'] = messages[0]
        group['count'] = len(messages)
        group['attachment_count'] = sum(m['attachment_count'] for m in messages)
        group['unread_count'] = sum(1 for m in messages if m['unread'])
        if view == 'people':
            group['title'] = who(group['address'], '(Unknown)')
        else:
            group['title'] = messages[-1]['subject'] or '(No subject)'
            seen, labels = set(), []
            for m in messages:                # One entry per person, newest first; the address decides who is who.
                address = parseaddr(m['from'])[1].lower()
                label = 'You' if m['direction'] == 'sent' else who(address, 'Unknown')
                if (address or label) not in seen:
                    seen.add(address or label); labels.append(label)
            group['participants'] = labels[:4]
        result.append(group)
    flags.attach(db,user_id,[m for g in result for m in g['messages']])
    topics.attach(db,user_id,[m for g in result for m in g['messages']])
    for g in result:
        g['topics'] = topics.top(g['messages'])
    for g in result:
        g.update({k:g['last'][k] for k in ('country','country_conf','country_reason')})
    flags.attach_cards(db,user_id,result)
    return {'groups':result}

def group_name(address):
    name, email_address = parseaddr(address or '')
    return (name or email_address.split('@')[0]).strip()

def folder_counts(db,user_id,account_id=None):
    scope, args = (' AND account_id=?', (user_id,account_id)) if account_id is not None else ('', (user_id,))
    row = db.execute('SELECT COALESCE(SUM(in_inbox),0),COALESCE(SUM(in_sent),0) FROM gmail_messages WHERE user_id=?'+scope,args).fetchone()
    unread = sum(1 for r in db.execute('SELECT label_ids,read_override FROM gmail_messages WHERE user_id=? AND in_inbox=1'+scope,args) if _is_unread(r))
    return {'inbox':row[0],'sent':row[1],'unread':unread}

def stored_count(db,user_id,account_id=None):
    scope, args = (' AND account_id=?', (user_id,account_id)) if account_id is not None else ('', (user_id,))
    return db.execute('SELECT COUNT(*) FROM gmail_messages WHERE user_id=? AND has_full=1'+scope,args).fetchone()[0]

def _listing(token,query,limit):
    ids, page = [], None
    while len(ids) < limit:
        result = list_message_ids(token,max_results=min(100,limit - len(ids)),query=query,page_token=page)
        ids += result['message_ids']
        page = result['next_page_token']
        if not page or not result['message_ids']: break
    return ids[:limit]

def _reconcile(db,user_id,account_id,column,ids):
    """Flag listed messages as in the folder; stored ones of this account newer than the oldest listed message but
    absent from the listing have left it (archived, deleted, moved) and are unflagged but kept. Other accounts'
    mail is never touched."""
    if not ids:
        db.execute(f'UPDATE gmail_messages SET {column}=0 WHERE user_id=? AND account_id=?',(user_id,account_id)); return
    marks = ','.join('?' * len(ids))
    db.execute(f'UPDATE gmail_messages SET {column}=1 WHERE user_id=? AND account_id=? AND message_id IN ({marks})',(user_id,account_id,*ids))
    floor = db.execute(f'SELECT MIN(internal_date) FROM gmail_messages WHERE user_id=? AND account_id=? AND message_id IN ({marks})',
                       (user_id,account_id,*ids)).fetchone()[0] or 0
    db.execute(f'UPDATE gmail_messages SET {column}=0 WHERE user_id=? AND account_id=? AND {column}=1 AND internal_date>=? '
               f'AND message_id NOT IN ({marks})',(user_id,account_id,floor,*ids))

def remote_counts(db,config,user_id,*,account_id=None,max_age=600,force=False,opener=urlopen):
    """How many messages Gmail holds (mailbox, inbox, sent) for one account (default: the first), cached for `max_age`
    seconds so that polling the status never calls Google more than a few times an hour. Never raises: on failure the
    older value (or None) is returned."""
    try: account = _account(db,user_id,account_id)
    except GmailConnectionError: return None
    try: cached = json.loads(account['remote_counts']) if account['remote_counts'] else None
    except ValueError: cached = None
    if cached and not force and time.time() - cached.get('checked_at',0) < max_age: return cached
    try:
        totals = get_totals(_access_token(db,config,user_id,account_id=account['id'],opener=opener),opener=opener)
    except (GmailConnectionError,GmailReadError,ValueError):
        return cached
    if all(v is None for v in totals.values()): return cached   # An unexpected answer: keep what we had.
    totals['checked_at'] = int(time.time())
    with db:
        db.execute('UPDATE gmail_connections SET remote_counts=? WHERE id=?',(json.dumps(totals),account['id']))
    return totals

def overview(db,config,user_id):
    """Accounts with their own numbers, plus the totals over all of them (Gmail's counts are cached, see remote_counts)."""
    result, remote_all = [], []
    for a in accounts(db,user_id):
        remote = remote_counts(db,config,user_id,account_id=a['id']) if a['provider']=='gmail' else None
        remote_all.append(remote)
        result.append(dict(a,label=label(a),stored=stored_count(db,user_id,a['id']),counts=folder_counts(db,user_id,a['id']),remote=remote))
    total = None
    if remote_all and all(remote_all):
        keys = ('all','threads','inbox','inbox_unread','sent')
        total = {k:(sum(r[k] for r in remote_all) if all(r.get(k) is not None for r in remote_all) else None) for k in keys}
        total['checked_at'] = min(r.get('checked_at',0) for r in remote_all)
    return result, total

def _refresh_unread(db,user_id,account,listed_ids,unread_ids):
    """Stored messages keep the labels they had when downloaded, so a message read in Gmail since would stay
    "unread" here. For the messages in the sync window, set or clear UNREAD from Gmail's current answer."""
    if not listed_ids: return
    prefix = account['prefix']
    marks = ','.join('?' * len(listed_ids))
    for row in db.execute(f'SELECT message_id,label_ids FROM gmail_messages WHERE user_id=? AND account_id=? AND message_id IN ({marks})',(user_id,account['id'],*[prefix + i for i in listed_ids])).fetchall():
        try: labels = json.loads(row['label_ids'] or '[]')
        except ValueError: labels = []
        want = row['message_id'][len(prefix):] in unread_ids
        if want == ('UNREAD' in labels): continue
        labels = [x for x in labels if x != 'UNREAD'] + (['UNREAD'] if want else [])
        db.execute('UPDATE gmail_messages SET label_ids=? WHERE user_id=? AND message_id=?',(json.dumps(labels),user_id,row['message_id']))

def _learn_address(db,account,token,opener):
    """Accounts connected before addresses were recorded learn theirs on the next sync (best effort)."""
    if account['email']: return
    try: email = get_address(token,opener=opener)
    except (GmailReadError,ValueError): return
    if email:
        try:
            with db: db.execute('UPDATE gmail_connections SET email=? WHERE id=?',(email,account['id']))
        except Exception: pass   # The same address is already connected as another account: leave this one as it is.

def run_sync(db,config,user_id,*,account_id=None,report=lambda **kw: None,opener=urlopen):
    """Download every inbox and sent message of one account (default: the first) that is not stored in full yet
    (bodies and attachments), then reconcile the folder flags. Each message is committed as it arrives, so an
    interrupted sync resumes where it stopped."""
    account = _account(db,user_id,account_id)
    account_id, prefix = account['id'], account['prefix']
    settings = sync_settings(config,prefs.stored(db,user_id))
    token = _access_token(db,config,user_id,account_id=account_id,opener=opener)
    _learn_address(db,account,token,opener)
    remote_counts(db,config,user_id,account_id=account_id,force=True,opener=opener)  # So the page can show "x of y" while syncing.
    inbox_ids = _listing(token,settings['query'],settings['max_messages'])
    sent_ids = _listing(token,settings['sent_query'],settings['max_messages']) if settings['sync_sent'] else None
    have_full = {r[0][len(prefix):] for r in db.execute('SELECT message_id FROM gmail_messages WHERE user_id=? AND account_id=? AND has_full=1',(user_id,account_id))}
    todo = [i for i in dict.fromkeys(inbox_ids + (sent_ids or [])) if i not in have_full]
    done = failed = 0
    first_error = None
    report(total=len(todo),done=0,failed=0)
    if todo:
        limited_in_a_row = 0
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_fetch,token,i,settings['attachment_max']):i for i in todo}
            for future in as_completed(futures):
                try:
                    item = future.result()
                    with db: _store(db,user_id,item,account=account)
                    done += 1; limited_in_a_row = 0
                except cf.CancelledError:
                    continue
                except (GmailReadError,ValueError) as exc:
                    failed += 1; first_error = first_error or str(exc)
                    limited_in_a_row = limited_in_a_row + 1 if isinstance(exc,GmailRateLimitError) else 0
                    if limited_in_a_row >= 4:  # Gmail keeps refusing even after backing off: stop hammering it.
                        pool.shutdown(wait=False,cancel_futures=True)
                        break
                report(done=done,failed=failed)
    if todo and not done:
        raise GmailReadError(first_error or 'No messages could be downloaded')
    unread_ids = set(_listing(token,f"({settings['query']}) is:unread",settings['max_messages']))
    stored = lambda ids: [prefix + i for i in ids]
    with db:
        _refresh_unread(db,user_id,account,inbox_ids,unread_ids)
        _reconcile(db,user_id,account_id,'in_inbox',stored(inbox_ids))
        if sent_ids is not None:  # Sent mail sync switched off: keep what is stored, change nothing.
            _reconcile(db,user_id,account_id,'in_sent',stored(sent_ids))
        db.execute('UPDATE gmail_connections SET last_sync=? WHERE id=?',(int(time.time()),account_id))
    skipped = len(todo) - done
    if skipped:
        note = first_error or 'Reason unknown.'
        report(error=f"{skipped} message(s) could not be downloaded. {note}" + ('' if 'press Refresh' in note.lower() else ' Press Refresh to retry; what is saved is kept.'))

# Progress of the background sync, per app user (in memory; a restart simply ends a running sync).
_PROGRESS, _LOCK = {}, threading.Lock()

def sync_progress(user_id):
    with _LOCK:
        return dict(_PROGRESS.get(user_id) or {'running':False,'total':0,'done':0,'failed':0,'error':None},rate_limited_s=int(cooldown_remaining()))

def _update(user_id,**fields):
    with _LOCK:
        _PROGRESS.setdefault(user_id,{'running':False,'total':0,'done':0,'failed':0,'error':None}).update(fields)

def _background(db_path,config,user_id,account_ids):
    """Sync the accounts one after the other. Progress adds up over all of them; a failing account is reported by
    name and does not stop the others."""
    problems, base = [], {'total':0,'done':0,'failed':0}
    try:
        with closing(connection(db_path)) as db:
            several = len(account_ids) > 1
            for account_id in account_ids:
                name = next((a['email'] for a in accounts(db,user_id) if a['id']==account_id),'') if several else ''
                current = {'total':0,'done':0,'failed':0}
                def report(**kw):
                    for key in current:
                        if key in kw: current[key] = kw[key]
                    update = {key:base[key] + current[key] for key in current if key in kw}
                    if 'error' in kw: update['error'] = (name + ': ' if name else '') + kw['error']
                    _update(user_id,**update)
                try:
                    if account_id is not None and account_id < 0:
                        import imap_web
                        imap_web.sync(db,config,user_id,account_id,report=report)
                    else: run_sync(db,config,user_id,account_id=account_id,report=report)
                except (GmailConnectionError,GmailReadError) as exc: problems.append((name + ': ' if name else '') + str(exc))
                except Exception as exc: problems.append((name + ': ' if name else '') + ('IMAP sync failed: '+str(exc) if account_id is not None and account_id < 0 else 'Sync failed unexpectedly'))
                for key in base: base[key] += current[key]
    except Exception: problems.append('Sync failed unexpectedly')
    _update(user_id,running=False,**({'error':' '.join(problems)} if problems else {}))

def start_sync(db_path,config,user_id,account_id=None):
    """Start a sync thread (all of the user's accounts, or just `account_id`) unless one is already running for this
    user. Returns the current progress."""
    with _LOCK:
        current = _PROGRESS.get(user_id)
        if current and current['running']: return dict(current)
        _PROGRESS[user_id] = {'running':True,'total':0,'done':0,'failed':0,'error':None}
    with closing(connection(db_path)) as db:
        ids = [account_id] if account_id is not None else [a['id'] for a in accounts(db,user_id)]
    threading.Thread(target=_background,args=(db_path,config,user_id,ids or [None]),daemon=True,name=f'gmail-sync-{user_id}').start()
    return sync_progress(user_id)

_AUTO_LAST = {}

def _auto_loop(db_path,config):
    """Every 30 s: sync each connected user whose own "Fetch new mail every N minutes" setting has elapsed."""
    import time
    while True:
        time.sleep(30)
        try:
            if cooldown_remaining() > 0: continue
            with closing(connection(db_path)) as db:
                due = []
                for user_id in [r[0] for r in db.execute('SELECT user_id FROM gmail_connections UNION SELECT user_id FROM imap_connections')]:
                    minutes = prefs.effective(db,user_id,config)['auto_sync_minutes']
                    if minutes and time.time() - _AUTO_LAST.get(user_id,0) >= minutes*60: due.append(user_id)
            for user_id in due:
                _AUTO_LAST[user_id] = time.time()   # One attempt per interval, even when it fails (a revoked token must not be retried every tick).
                start_sync(db_path,config,user_id)
        except Exception: pass

def start_auto_sync(db_path,config):
    """Start the background thread that refreshes connected users' mail on a timer (per-user setting)."""
    import time
    with closing(connection(db_path)) as db:
        for r in db.execute('SELECT user_id,MAX(last_sync) FROM (SELECT user_id,last_sync FROM gmail_connections UNION ALL SELECT user_id,last_sync FROM imap_connections) GROUP BY user_id'):
            minutes = prefs.effective(db,r[0],config)['auto_sync_minutes'] or 5
            _AUTO_LAST[r[0]] = max(r[1] or 0,time.time() - minutes*60 + 60)   # First run about a minute after start unless synced recently.
    threading.Thread(target=_auto_loop,args=(db_path,config),daemon=True,name='gmail-auto-sync').start()

def message(db,config,user_id,message_id,*,opener=urlopen):
    """One complete message from the local copy; Gmail is contacted only if it is missing or stored without its body."""
    if not isinstance(message_id,str) or not message_id:
        raise GmailReadError('Invalid message ID')
    row = db.execute('SELECT * FROM gmail_messages WHERE user_id=? AND message_id=?',(user_id,message_id)).fetchone()
    if row and row['has_full']: return _detail(db,user_id,row)
    if message_id.startswith('imap-'): raise GmailReadError('IMAP message is not in the local cache; sync this account first')
    account, gmail_id = _split(db,user_id,message_id)
    token = _access_token(db,config,user_id,account_id=account['id'],opener=opener)
    try: item = _fetch(token,gmail_id,sync_settings(config,prefs.stored(db,user_id))['attachment_max'])
    except ValueError: raise GmailReadError('Invalid message ID') from None
    with db: _store(db,user_id,item,account=account)
    return _detail(db,user_id,db.execute('SELECT * FROM gmail_messages WHERE user_id=? AND message_id=?',(user_id,account['prefix'] + item['id'])).fetchone())

def _detail(db,user_id,row):
    detail = _row(row)
    detail.update({'to':row['to_addr'],'cc':row['cc_addr'],'labels':json.loads(row['label_ids'] or '[]'),
                   'body_text':readable_body(row['body_text'], row['body_html']),
                   'has_html':bool(row['body_html']) or looks_like_html(row['body_text']),'size_estimate':row['size_estimate'],
                   'headers':json.loads(row['headers_json'] or '[]'),
                   'attachments':[{'id':a['id'],'name':a['filename'],'mime_type':a['mime_type'],'size_bytes':a['size_bytes'],
                                   'stored':bool(a['has_content']),'skipped_reason':a['skipped_reason'],'rotation':a['rotation']}
                                  for a in db.execute('SELECT id,filename,mime_type,size_bytes,skipped_reason,rotation,content IS NOT NULL AS has_content FROM gmail_attachments '
                                                      'WHERE user_id=? AND message_id=? ORDER BY id',(user_id,row['message_id']))]})
    if row['thread_id']:
        detail['thread'] = [dict(_row(t),) for t in db.execute(
            'SELECT * FROM gmail_messages WHERE user_id=? AND thread_id=? ORDER BY internal_date, message_id',
            (user_id,row['thread_id']))]
    else:
        detail['thread'] = []
    flags.attach(db,user_id,[detail]+detail['thread'])
    topics.attach(db,user_id,[detail]+detail['thread'])
    detail['person'] = flags.card(db,user_id,row['peer_email'])
    return detail

def attachment(db,user_id,message_id,attachment_id):
    return db.execute('SELECT * FROM gmail_attachments WHERE user_id=? AND message_id=? AND id=?',
                      (user_id,message_id,attachment_id)).fetchone()

def get_translation(db,user_id,message_id,language):
    row = db.execute('SELECT * FROM message_translations WHERE user_id=? AND message_id=? AND language_key=?',
                     (user_id,message_id,language.casefold())).fetchone()
    return None if row is None else {'subject':row['subject'],'body':row['body'],'source_language':row['source_language'],
                                     'already_in_language':bool(row['same_language']),'language':row['language'],
                                     'provider':row['provider'],'model':row['model'],'created_at':row['created_at']}

def save_translation(db,user_id,message_id,language,result,provider,model):
    with db:
        db.execute('''INSERT INTO message_translations(user_id,message_id,language_key,language,subject,body,source_language,same_language,provider,model,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,message_id,language_key) DO UPDATE SET language=excluded.language,
            subject=excluded.subject,body=excluded.body,source_language=excluded.source_language,same_language=excluded.same_language,
            provider=excluded.provider,model=excluded.model,created_at=excluded.created_at''',
            (user_id,message_id,language.casefold(),language,result['subject'],result['body'],result['source_language'],
             int(result['already_in_language']),provider,model,int(time.time())))
