"""Session-bound Google web OAuth and encrypted, per-user Gmail credentials."""
from __future__ import annotations
import base64
import hashlib
import json
import secrets
import threading
import time
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
from connectors.gmail_readonly import SCOPES, GmailReadError, list_message_ids, get_message_full, get_attachment
from tasks import connection, peer_for
import settings as prefs

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
        db.execute('DELETE FROM message_translations WHERE user_id=?',(user_id,))  # Disconnecting also removes the local copy
        db.execute('DELETE FROM gmail_attachments WHERE user_id=?',(user_id,))
        db.execute('DELETE FROM gmail_messages WHERE user_id=?',(user_id,))

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

def _row(r):
    return {'id':r['message_id'],'thread_id':r['thread_id'],'from':r['from_addr'],'to':r['to_addr'],
            'subject':r['subject'],'date':r['date_header'],'internal_date':r['internal_date'],'snippet':r['snippet'],
            'direction':'sent' if '"SENT"' in (r['label_ids'] or '') else 'received'}

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

def _store(db,user_id,item,in_inbox=0):  # Folder flags are set on insert only; run_sync's reconcile owns them afterwards.
    h = item['headers']
    db.execute('''INSERT INTO gmail_messages
        (user_id,message_id,thread_id,from_addr,subject,date_header,snippet,internal_date,in_inbox,fetched_at,
         to_addr,cc_addr,label_ids,headers_json,body_text,body_html,size_estimate,peer_email,peer_name,has_full)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        ON CONFLICT(user_id,message_id) DO UPDATE SET thread_id=excluded.thread_id,from_addr=excluded.from_addr,
          subject=excluded.subject,date_header=excluded.date_header,snippet=excluded.snippet,
          internal_date=excluded.internal_date,fetched_at=excluded.fetched_at,
          to_addr=excluded.to_addr,cc_addr=excluded.cc_addr,label_ids=excluded.label_ids,
          headers_json=excluded.headers_json,body_text=excluded.body_text,body_html=excluded.body_html,
          size_estimate=excluded.size_estimate,peer_email=excluded.peer_email,peer_name=excluded.peer_name,has_full=1''',
        (user_id,item['id'],item.get('thread_id') or '',h.get('from',''),h.get('subject',''),h.get('date',''),
         item.get('snippet',''),item.get('internal_date',0),in_inbox,int(time.time()),
         h.get('to',''),h.get('cc',''),json.dumps(item.get('label_ids',[])),json.dumps(item.get('all_headers',[])),
         item.get('body_text',''),item.get('body_html',''),item.get('size_estimate',0),*peer_for(json.dumps(item.get('label_ids',[])),h.get('from',''),h.get('to',''))))
    db.execute('DELETE FROM gmail_attachments WHERE user_id=? AND message_id=?',(user_id,item['id']))
    for a in item.get('attachments',[]):
        db.execute('INSERT INTO gmail_attachments(user_id,message_id,part_id,filename,mime_type,size_bytes,content,skipped_reason) '
                   'VALUES (?,?,?,?,?,?,?,?)',(user_id,item['id'],a['part_id'],a['filename'],a['mime_type'],
                   len(a['content']) if a['content'] is not None else a['size'],a['content'],a['skipped_reason']))

def last_sync(db,user_id):
    row = db.execute('SELECT last_sync FROM gmail_connections WHERE user_id=?',(user_id,)).fetchone()
    return row['last_sync'] if row else None

FOLDERS = {'inbox':'in_inbox','sent':'in_sent'}

def cached_inbox(db,user_id,folder='inbox'):
    """Folder rows from the local SQLite copy; makes no Gmail request."""
    column = FOLDERS[folder]
    rows = db.execute('SELECT m.*,(SELECT COUNT(*) FROM gmail_attachments a WHERE a.user_id=m.user_id AND a.message_id=m.message_id) AS n_att '
                      f'FROM gmail_messages m WHERE m.user_id=? AND m.{column}=1 ORDER BY m.internal_date DESC, m.message_id DESC',(user_id,))
    return [dict(_row(r),attachment_count=r['n_att']) for r in rows]

VIEWS = ('messages','threads','people')

def grouped_inbox(db,user_id,folder,view):
    """The folder as the user wants to see it. `messages`: one entry per message. `threads`: one per Gmail
    conversation. `people`: one per other party (sender of received mail, first recipient of sent mail).
    A group is listed in a folder when any of its messages is in it, and holds all stored messages of the
    conversation or person, from both folders. Purely local: no Gmail request."""
    if view == 'messages':
        return {'messages':cached_inbox(db,user_id,folder)}
    column = FOLDERS[folder]
    rows = db.execute('SELECT message_id,thread_id,from_addr,to_addr,subject,date_header,snippet,internal_date,label_ids,'
                      f'peer_email,peer_name,{column} AS in_folder,'
                      '(SELECT COUNT(*) FROM gmail_attachments a WHERE a.user_id=m.user_id AND a.message_id=m.message_id) AS n_att '
                      'FROM gmail_messages m WHERE user_id=? ORDER BY internal_date DESC, message_id DESC',(user_id,))  # Metadata-only rows (older syncs) group fine too.
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
        group['in_folder'] = group['in_folder'] or bool(r['in_folder'])
    def who(address, fallback=''):
        return names.get(address) or names_to.get(address) or (address.split('@')[0] if address else '') or fallback
    result = []
    for group in groups.values():
        if not group.pop('in_folder'): continue
        messages = group['messages']          # newest first
        group['last'] = messages[0]
        group['count'] = len(messages)
        group['attachment_count'] = sum(m['attachment_count'] for m in messages)
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
    return {'groups':result}

def group_name(address):
    name, email_address = parseaddr(address or '')
    return (name or email_address.split('@')[0]).strip()

def folder_counts(db,user_id):
    row = db.execute('SELECT COALESCE(SUM(in_inbox),0),COALESCE(SUM(in_sent),0) FROM gmail_messages WHERE user_id=?',(user_id,)).fetchone()
    return {'inbox':row[0],'sent':row[1]}

def stored_count(db,user_id):
    return db.execute('SELECT COUNT(*) FROM gmail_messages WHERE user_id=? AND has_full=1',(user_id,)).fetchone()[0]

def _listing(token,query,limit):
    ids, page = [], None
    while len(ids) < limit:
        result = list_message_ids(token,max_results=min(100,limit - len(ids)),query=query,page_token=page)
        ids += result['message_ids']
        page = result['next_page_token']
        if not page or not result['message_ids']: break
    return ids[:limit]

def _reconcile(db,user_id,column,ids):
    """Flag listed messages as in the folder; stored ones newer than the oldest listed message but absent
    from the listing have left it (archived, deleted, moved) and are unflagged but kept."""
    if not ids:
        db.execute(f'UPDATE gmail_messages SET {column}=0 WHERE user_id=?',(user_id,)); return
    marks = ','.join('?' * len(ids))
    db.execute(f'UPDATE gmail_messages SET {column}=1 WHERE user_id=? AND message_id IN ({marks})',(user_id,*ids))
    floor = db.execute(f'SELECT MIN(internal_date) FROM gmail_messages WHERE user_id=? AND message_id IN ({marks})',
                       (user_id,*ids)).fetchone()[0] or 0
    db.execute(f'UPDATE gmail_messages SET {column}=0 WHERE user_id=? AND {column}=1 AND internal_date>=? '
               f'AND message_id NOT IN ({marks})',(user_id,floor,*ids))

def run_sync(db,config,user_id,*,report=lambda **kw: None,opener=urlopen):
    """Download every inbox and sent message not stored in full yet (bodies and attachments), then reconcile
    the folder flags. Each message is committed as it arrives, so an interrupted sync resumes where it stopped."""
    settings = sync_settings(config,prefs.stored(db,user_id))
    token = _access_token(db,config,user_id,opener=opener)
    inbox_ids = _listing(token,settings['query'],settings['max_messages'])
    sent_ids = _listing(token,settings['sent_query'],settings['max_messages']) if settings['sync_sent'] else None
    have_full = {r[0] for r in db.execute('SELECT message_id FROM gmail_messages WHERE user_id=? AND has_full=1',(user_id,))}
    todo = [i for i in dict.fromkeys(inbox_ids + (sent_ids or [])) if i not in have_full]
    done = failed = 0
    first_error = None
    report(total=len(todo),done=0,failed=0)
    if todo:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch,token,i,settings['attachment_max']):i for i in todo}
            for future in as_completed(futures):
                try:
                    item = future.result()
                    with db: _store(db,user_id,item)
                    done += 1
                except (GmailReadError,ValueError) as exc:
                    failed += 1; first_error = first_error or str(exc)
                report(done=done,failed=failed)
    if todo and not done:
        raise GmailReadError(first_error or 'No messages could be downloaded')
    with db:
        _reconcile(db,user_id,'in_inbox',inbox_ids)
        if sent_ids is not None:  # Sent mail sync switched off: keep what is stored, change nothing.
            _reconcile(db,user_id,'in_sent',sent_ids)
        db.execute('UPDATE gmail_connections SET last_sync=? WHERE user_id=?',(int(time.time()),user_id))
    if failed:
        report(error=f'{failed} message(s) could not be downloaded; press Refresh to retry.')

# Progress of the background sync, per app user (in memory; a restart simply ends a running sync).
_PROGRESS, _LOCK = {}, threading.Lock()

def sync_progress(user_id):
    with _LOCK:
        return dict(_PROGRESS.get(user_id) or {'running':False,'total':0,'done':0,'failed':0,'error':None})

def _update(user_id,**fields):
    with _LOCK:
        _PROGRESS.setdefault(user_id,{'running':False,'total':0,'done':0,'failed':0,'error':None}).update(fields)

def _background(db_path,config,user_id):
    problem = None
    try:
        with closing(connection(db_path)) as db:
            run_sync(db,config,user_id,report=lambda **kw: _update(user_id,**kw))
    except (GmailConnectionError,GmailReadError) as exc: problem = str(exc)
    except Exception: problem = 'Sync failed unexpectedly'
    _update(user_id,running=False,**({'error':problem} if problem else {}))

def start_sync(db_path,config,user_id):
    """Start a sync thread unless one is already running for this user. Returns the current progress."""
    with _LOCK:
        current = _PROGRESS.get(user_id)
        if current and current['running']: return dict(current)
        _PROGRESS[user_id] = {'running':True,'total':0,'done':0,'failed':0,'error':None}
    threading.Thread(target=_background,args=(db_path,config,user_id),daemon=True,name=f'gmail-sync-{user_id}').start()
    return sync_progress(user_id)

def message(db,config,user_id,message_id,*,opener=urlopen):
    """One complete message from the local copy; Gmail is contacted only if it is missing or stored without its body."""
    if not isinstance(message_id,str) or not message_id:
        raise GmailReadError('Invalid message ID')
    row = db.execute('SELECT * FROM gmail_messages WHERE user_id=? AND message_id=?',(user_id,message_id)).fetchone()
    if row and row['has_full']: return _detail(db,user_id,row)
    token = _access_token(db,config,user_id,opener=opener)
    try: item = _fetch(token,message_id,sync_settings(config,prefs.stored(db,user_id))['attachment_max'])
    except ValueError: raise GmailReadError('Invalid message ID') from None
    with db: _store(db,user_id,item)
    return _detail(db,user_id,db.execute('SELECT * FROM gmail_messages WHERE user_id=? AND message_id=?',(user_id,item['id'])).fetchone())

def _detail(db,user_id,row):
    detail = _row(row)
    detail.update({'to':row['to_addr'],'cc':row['cc_addr'],'labels':json.loads(row['label_ids'] or '[]'),
                   'body_text':row['body_text'],'has_html':bool(row['body_html']),'size_estimate':row['size_estimate'],
                   'headers':json.loads(row['headers_json'] or '[]'),
                   'attachments':[{'id':a['id'],'name':a['filename'],'mime_type':a['mime_type'],'size_bytes':a['size_bytes'],
                                   'stored':bool(a['has_content']),'skipped_reason':a['skipped_reason']}
                                  for a in db.execute('SELECT id,filename,mime_type,size_bytes,skipped_reason,content IS NOT NULL AS has_content FROM gmail_attachments '
                                                      'WHERE user_id=? AND message_id=? ORDER BY id',(user_id,row['message_id']))]})
    if row['thread_id']:
        detail['thread'] = [dict(_row(t),) for t in db.execute(
            'SELECT * FROM gmail_messages WHERE user_id=? AND thread_id=? ORDER BY internal_date, message_id',
            (user_id,row['thread_id']))]
    else:
        detail['thread'] = []
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
