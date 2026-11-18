"""Local single-admin Gmail task workspace. Reads Gmail headers and snippets only; never sends email."""
from __future__ import annotations
import json
import os
import secrets
import time
import sqlite3
from html import escape
from contextlib import contextmanager, closing
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote
import ai
import gmail_web
import settings as prefs
from auth import AuthManager, SESSION_SECONDS, cookie_token, read_config
from tasks import connection, DEFAULT_DB, list_tasks, task_summary, get_task, task_for_source, create_task, update_task, events, delete_task, ConflictError

ROOT = Path(__file__).resolve().parent
NAV_ITEMS = (('home','/','Home'),('inbox','/user/inbox','Inbox'),('tasks','/user/tasks','Tasks'),('privacy','/privacy','Privacy'))
# name -> (title, description, scripts, active nav item, signed in)
PAGES = {
    'home': ('Inbox Tend · Review work from email', 'Review email-derived work with a human in control.', (), 'home', False),
    'login': ('Sign in · Inbox Tend', 'Sign in to the Inbox Tend workspace.', ('/static/login.js',), None, False),
    'inbox': ('Inbox · Inbox Tend', 'Review your Gmail inbox and turn messages into local tasks.', ('/static/common.js','/static/inbox.js'), 'inbox', True),
    'tasks': ('Tasks · Inbox Tend', 'Track local follow-up tasks, owners, deadlines and status.', ('/static/common.js','/static/tasks.js'), 'tasks', True),
    'settings': ('Settings · Inbox Tend', 'Timezone, display, mail sync and account settings.', ('/static/common.js','/static/settings.js'), 'settings', True),
    'privacy': ('Privacy · Inbox Tend', 'What Inbox Tend reads, keeps and never does.', (), 'privacy', False),
}
STATIC_FILES = {'/static/styles.css':'text/css','/static/common.js':'text/javascript','/static/inbox.js':'text/javascript',
                '/static/tasks.js':'text/javascript','/static/settings.js':'text/javascript','/static/login.js':'text/javascript'}

def nav_html(active, signed_in):
    """Identical four links on every page; only the current-page marker and the auth control vary."""
    links = ''.join('<a href="%s"%s>%s</a>' % (href, ' aria-current="page"' if key == active else '', label)
                    for key, href, label in NAV_ITEMS)
    if not signed_in:
        return links + '<a href="/login">Sign in</a>'
    current = ' aria-current="page"' if active == 'settings' else ''
    return links + f'<a href="/user/settings"{current}>Settings</a><button id="logout" type="button">Sign out</button>'

def render_page(name):
    """Compose trusted static page content with one shared header and footer."""
    title, description, scripts, active, signed_in = PAGES[name]
    layout = (ROOT/'static'/'layout.html').read_text()
    content = (ROOT/'static'/'pages'/f'{name}.html').read_text()
    replacements = {
        '{{TITLE}}': escape(title, quote=True),
        '{{DESCRIPTION}}': escape(description, quote=True),
        '{{SCRIPT}}': ''.join(f'<script src="{src}" defer></script>' for src in scripts),
        '{{NAV}}': nav_html(active, signed_in),
        '{{CONTENT}}': content,
    }
    for marker, value in replacements.items():
        layout = layout.replace(marker, value)
    return layout.encode('utf-8')

def summarize(message):
    """A deterministic excerpt, not AI extraction."""
    s = ' '.join((message.get('snippet') or '').split())
    return s if len(s) <= 150 else s[:147].rstrip() + '...'

def draft_reply(message):
    return ("Hello,\n\nThank you for your message about '" + (message.get('subject') or '(no subject)') + "'. "
            "I will review the details and follow up after checking the record. "
            "This is a draft for your review.\n\nBest regards")

def proposal(message, user=None):
    """Rule-based starting point for a task; no deadline is inferred."""
    user = user or prefs.DEFAULTS
    subject = (message.get('subject') or '').strip() or '(No subject)'
    return {'source':{'provider':'gmail','message_id':message['id'],'thread_id':message['thread_id'],
                      'subject':subject,'date':message['date']},
            'inferred':{'title':subject[:200],'description':summarize(message),
                        'priority':user['default_priority'],'due_date':None,'due_time':None,
                        'due_timezone':user['timezone'],'project_label':'','customer_label':''},
            'note':'Rule-based suggestion from the Gmail subject and snippet; no deadline was inferred.'}

class Handler(BaseHTTPRequestHandler):
    db_path = DEFAULT_DB
    auth_manager = None
    gmail_config = {}

    def _send(self,status,payload,mime,extra_headers=()):
        self.send_response(status)
        for key,value in (('Content-Type',mime),('Content-Length',str(len(payload))),('Cache-Control','no-store'),
                          ('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
                          ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")):
            self.send_header(key,value)
        for key,value in extra_headers:
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self,status,data,extra_headers=()):
        self._send(status,json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8',extra_headers)

    def _redirect(self,location):
        self.send_response(303)
        self.send_header('Location',location)
        self.send_header('Cache-Control','no-store')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Length','0')
        self.end_headers()

    def _host_ok(self):
        host = self.headers.get('Host','')
        name = host.split(':',1)[0].lower()
        return name in ('127.0.0.1','localhost','[::1]') and host.count(':') <= 1

    def _gmail_message(self,session,message_id):
        """Fetch a message from the signed-in user's Gmail; the client never supplies message content."""
        if not isinstance(message_id,str) or not message_id:
            raise RequestError(400,'Message ID required')
        try:
            with self._db() as db: return gmail_web.message(db,self.gmail_config,session['id'],message_id)
        except gmail_web.GmailConnectionError as exc: raise RequestError(503,str(exc)) from None
        except gmail_web.GmailReadError as exc: raise RequestError(424,str(exc)) from None  # 4xx: Cloudflare replaces origin 502/504 with an HTML page

    def _ai_inputs(self,session,message_id):
        """Message text (and optionally small text attachments) for one explicit AI request."""
        with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
        if not user['ai_enabled']: raise RequestError(403,'The AI assistant is turned off in Settings')
        message = self._gmail_message(session,message_id)
        files = []
        if user['ai_attachments']:
            with self._db() as db:
                for item in message['attachments']:
                    if len(files) >= ai.MAX_ATTACHMENTS: break
                    if not item['stored'] or not (item['mime_type'] in ('text/plain','text/csv') or item['name'].lower().endswith(('.txt','.csv'))): continue
                    row = gmail_web.attachment(db,session['id'],message['id'],item['id'])
                    files.append((item['name'],item['mime_type'],bytes(row['content'])[:ai.MAX_ATTACHMENT_CHARS * 4].decode('utf-8','replace')))
        return message,files,user

    def _db(self):
        @contextmanager
        def opened():
            with closing(connection(self.db_path)) as db:
                with db:
                    yield db
        return opened()

    def _read_json(self):
        if self.headers.get('Content-Type','').split(';')[0].strip().lower() != 'application/json':
            raise RequestError(415,'Content-Type must be application/json')
        try:
            length = int(self.headers.get('Content-Length',''))
            if not 1 <= length <= 16_384:
                raise ValueError()
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError()
            value = json.loads(body)
            if not isinstance(value,dict):
                raise ValueError()
            return value
        except (ValueError,TypeError,UnicodeError):
            raise RequestError(400,'Invalid JSON request') from None

    def _same_origin(self):
        if not self._host_ok():
            raise RequestError(403,'Invalid Host')
        origin = self.headers.get('Origin')
        allowed = {'http://' + self.headers['Host']}
        redirect = self.gmail_config.get('INBOX_TEND_GOOGLE_REDIRECT_URI','')
        if redirect:
            public = urlparse(redirect)
            if public.scheme == 'https' and public.netloc:
                allowed.add('https://' + public.netloc)
        if origin and origin not in allowed:
            raise RequestError(403,'Cross-origin write blocked')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise RequestError(403,'Cross-site write blocked')
    def _session(self):
        return self.auth_manager.session(cookie_token(self.headers.get('Cookie'))) if self.auth_manager else None

    def _write_ok(self,session):
        self._same_origin()
        if not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf_token']):
            raise RequestError(403,'CSRF token required')

    def _cookie_header(self,token,max_age):
        secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
        return f'it_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}{secure}'

    def _route(self,write=False):
        path = urlparse(self.path).path
        if not self._host_ok(): raise RequestError(403,'Invalid Host')
        if not write:
            if path == '/':
                return self._send(200,render_page('home'),'text/html; charset=utf-8')
            if path == '/login':
                if self._session(): return self._redirect('/user/inbox')
                return self._send(200,render_page('login'),'text/html; charset=utf-8')
            if path in STATIC_FILES:
                return self._send(200,(ROOT/path.lstrip('/')).read_bytes(),STATIC_FILES[path]+'; charset=utf-8')
            if path == '/privacy':
                return self._send(200,render_page('privacy'),'text/html; charset=utf-8')
            if path == '/api/health': return self._json(200,{'status':'ok'})
        if write and path == '/api/auth/login' and self.command == 'POST':
            self._same_origin()
            data = self._read_json()
            if not self.auth_manager:
                raise RequestError(503,'Login is not configured')
            result = self.auth_manager.authenticate(data.get('username'),data.get('password'))
            if result == 'throttled': raise RequestError(429,'Too many attempts; try again in a few minutes')
            if result == 'invalid': raise RequestError(401,'Invalid username or password')
            token,_ = self.auth_manager.new_session(result['id'])
            return self._json(200,{'authenticated':True,'user':{'username':result['username'],'role':result['role']}},
                              (('Set-Cookie',self._cookie_header(token,SESSION_SECONDS)),))
        session = self._session()
        if not session:
            if not write and path.startswith('/user/'):
                return self._redirect('/login')
            raise RequestError(401,'Sign in required')
        if not write and path == '/api/connectors/gmail/callback':
            query = parse_qs(urlparse(self.path).query)
            if 'error' in query:
                return self._redirect('/user/inbox?gmail=declined')
            if len(query.get('state',[])) != 1 or len(query.get('code',[])) != 1:
                raise RequestError(400,'Invalid OAuth callback')
            try:
                with self._db() as db:
                    gmail_web.finish(db,self.gmail_config,session['id'],cookie_token(self.headers.get('Cookie')),query['state'][0],query['code'][0])
            except gmail_web.GmailConnectionError:
                return self._redirect('/user/inbox?gmail=failed')
            return self._redirect('/user/inbox?gmail=connected')
        if not write and path == '/user/dashboard':
            return self._redirect('/user/inbox')  # Legacy URL
        if not write and path in ('/user/inbox','/user/tasks','/user/settings'):
            return self._send(200,render_page(path.rsplit('/',1)[1]),'text/html; charset=utf-8')
        if write:
            self._write_ok(session)
            data = self._read_json()
            if path == '/api/connectors/gmail/connect' and self.command == 'POST':
                try:
                    with self._db() as db:
                        url = gmail_web.begin(db,self.gmail_config,session['id'],cookie_token(self.headers.get('Cookie')))
                except gmail_web.GmailConnectionError as exc: raise RequestError(503,str(exc)) from None
                return self._json(200,{'authorization_url':url})
            if path == '/api/connectors/gmail/sync' and self.command == 'POST':
                with self._db() as db:
                    if not gmail_web.status(db,session['id']): raise RequestError(503,'Gmail is disconnected')
                if not gmail_web.configured(self.gmail_config): raise RequestError(503,'Gmail is not configured')
                # Runs in the background: a full sync outlasts proxy timeouts. Poll /status for progress.
                return self._json(202,{'sync':gmail_web.start_sync(self.db_path,self.gmail_config,session['id'])})
            if path == '/api/connectors/gmail/disconnect' and self.command == 'POST':
                with self._db() as db: gmail_web.disconnect(db,session['id'])
                return self._json(200,{'connected':False})
            if path == '/api/settings' and self.command == 'PATCH':
                with self._db() as db:
                    prefs.save(db,session['id'],data)
                    return self._json(200,{'settings':prefs.effective(db,session['id'],self.gmail_config)})
            if path == '/api/auth/password' and self.command == 'POST':
                result = self.auth_manager.change_password(session['id'],data.get('current'),data.get('new'),cookie_token(self.headers.get('Cookie')))
                if result == 'throttled': raise RequestError(429,'Too many attempts; try again in a few minutes')
                if result == 'invalid': raise RequestError(403,'Current password is incorrect')
                return self._json(200,{'changed':True})
            if path == '/api/translate' and self.command == 'POST':
                with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
                if not user['translate_enabled']: raise RequestError(403,'Translation is turned off in Settings')
                language = user['translate_language'].strip()
                if not language: raise RequestError(400,'Choose the language to translate into in Settings')
                message = self._gmail_message(session,data.get('id'))
                force = data.get('force') is True
                def answer(found,cached):
                    return self._json(200,{'translation':dict(found,cached=cached,provider=ai.PROVIDERS.get(found['provider'],{}).get('name',found['provider']))})
                with self._db() as db: found = gmail_web.get_translation(db,session['id'],message['id'],language)
                if found and not force: return answer(found,True)
                try:
                    with ai.translation_guard(session['id'],user['ai_timeout']):
                        with self._db() as db: found = gmail_web.get_translation(db,session['id'],message['id'],language)
                        if found and not force:  # Reaching the lock with a stored result means another request finished while this one queued.
                            return answer(found,True)
                        result = ai.translate(self.gmail_config,message,language,user)
                        provider = ai.provider_of(user)
                        with self._db() as db: gmail_web.save_translation(db,session['id'],message['id'],language,result,provider,user['ai_model'] or 'provider default')
                        with self._db() as db: found = gmail_web.get_translation(db,session['id'],message['id'],language)
                        return answer(found,False)
                except ai.AIBusy as exc: raise RequestError(429,str(exc)) from None
                except ai.AIError as exc: raise RequestError(424,str(exc)) from None
            if path.startswith('/api/ai/') and self.command == 'POST':
                try:
                    with ai.guard(session['id']):
                        if path == '/api/ai/test':
                            with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
                            return self._json(200,{'ok':True,'seconds':ai.ping(self.gmail_config,user),'provider':ai.PROVIDERS[ai.provider_of(user)]['name']})
                        if path == '/api/ai/task':
                            message,files,user = self._ai_inputs(session,data.get('id'))
                            result = ai.suggest_task(self.gmail_config,message,files,user)
                            base = proposal(message,user)
                            inferred = dict(base['inferred'],title=result['title'],description=result['description'],priority=result['priority'],
                                            due_date=result['due_date'],due_time=result['due_time'],owner=result['owner'] or '')
                            return self._json(200,{'source':base['source'],'inferred':inferred,
                                                   'note':'AI suggestion from '+ai.PROVIDERS[ai.provider_of(user)]['name']+'. Check it against the message before saving.',
                                                   'ai':{'confidence':result['confidence'],'evidence':result['evidence'],'notes':result['notes'],
                                                         'provider':ai.PROVIDERS[ai.provider_of(user)]['name'],
                                                         'model':user['ai_model'] or 'provider default'}})
                        if path == '/api/ai/reply':
                            message,files,user = self._ai_inputs(session,data.get('id'))
                            guidance = data.get('guidance','')
                            if not isinstance(guidance,str): raise RequestError(400,'Invalid guidance')
                            return self._json(200,{'draft':ai.draft_reply(self.gmail_config,message,files,guidance,user),'sent':False,'template':False,'ai':True})
                except ai.AIBusy as exc: raise RequestError(429,str(exc)) from None
                except ai.AIError as exc: raise RequestError(424,str(exc)) from None
            if path == '/api/auth/logout' and self.command == 'POST':
                self.auth_manager.revoke(cookie_token(self.headers.get('Cookie')))
                return self._json(200,{'signed_out':True},
                                  (('Set-Cookie',self._cookie_header('',0)),))
            if path == '/api/draft' and self.command == 'POST':
                message = self._gmail_message(session,data.get('id'))
                return self._json(200,{'draft':draft_reply(message),'sent':False,'template':True})
            if path == '/api/tasks' and self.command == 'POST':
                message = dict(self._gmail_message(session,data.get('message_id')),provider='gmail')
                with self._db() as db:
                    data.setdefault('due_timezone',prefs.effective(db,session['id'],self.gmail_config)['timezone'])
                    existing = task_for_source(db,'gmail',message['id'],session['id'])
                    if existing:
                        return self._json(409,{'error':'Task already exists','task':existing})
                    task = create_task(db,message,data,session['id'])
                return self._json(201,{'task':task})
            parts = path.strip('/').split('/')
            if len(parts)==3 and parts[:2]==['api','tasks'] and parts[2].isdigit():
                task_id = int(parts[2])
                if self.command == 'PATCH':
                    with self._db() as db: task = update_task(db,task_id,data,session['id'])
                    if task is None: raise RequestError(404,'Task not found')
                    return self._json(200,{'task':task})
                if self.command == 'DELETE':
                    if data.get('confirm') != 'DELETE': raise RequestError(400,'Explicit DELETE confirmation required')
                    with self._db() as db: removed = delete_task(db,task_id,session['id'])
                    if not removed: raise RequestError(404,'Task not found')
                    return self._json(200,{'deleted':True})
            raise RequestError(404,'Not found')
        if path == '/api/connectors/gmail/status':
            with self._db() as db:
                connected = gmail_web.status(db,session['id'])
                stamp = gmail_web.last_sync(db,session['id']) if connected else None
                stored = gmail_web.stored_count(db,session['id']) if connected else 0
                counts = gmail_web.folder_counts(db,session['id']) if connected else {'inbox':0,'sent':0}
            return self._json(200,{'configured':gmail_web.configured(self.gmail_config),'connected':connected,
                                   'last_sync':stamp,'stored':stored,'counts':counts,'sync':gmail_web.sync_progress(session['id'])})
        if path == '/api/connectors/gmail/messages':  # Local copy only; POST /sync talks to Gmail.
            folder = parse_qs(urlparse(self.path).query).get('folder',['inbox'])[0]
            if folder not in gmail_web.FOLDERS: raise RequestError(400,'Unknown folder')
            with self._db() as db:
                if not gmail_web.status(db,session['id']): raise RequestError(503,'Gmail is disconnected')
                rows,stamp = gmail_web.cached_inbox(db,session['id'],folder),gmail_web.last_sync(db,session['id'])
            return self._json(200,{'messages':rows,'folder':folder,'last_sync':stamp,'read_only':True})
        if path == '/api/ai/status':
            with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
            return self._json(200,dict(ai.status(self.gmail_config),enabled=user['ai_enabled'],provider=ai.provider_of(user)))
        if path == '/api/ai/models':
            provider = parse_qs(urlparse(self.path).query).get('provider',[ai.DEFAULT_PROVIDER])[0]
            if provider not in ai.PROVIDERS: raise RequestError(400,'Unknown AI provider')
            return self._json(200,{'provider':provider,'models':ai.models(self.gmail_config,provider)})
        if path == '/api/settings':
            with self._db() as db: values = prefs.effective(db,session['id'],self.gmail_config)
            return self._json(200,{'settings':values,'defaults':prefs.DEFAULTS,'username':session['username']})
        if path == '/api/session':
            return self._json(200,{'csrf_token':session['csrf_token'],'user':{'username':session['username'],'role':session['role']}})
        parts = path.strip('/').split('/')
        if parts[:4]==['api','connectors','gmail','messages'] and len(parts) in (5,7,8):
            if len(parts)==5:
                return self._json(200,{'message':self._gmail_message(session,parts[4])})
            if parts[5]=='attachments' and parts[6].isdigit() and len(parts) in (7,8):
                with self._db() as db: row = gmail_web.attachment(db,session['id'],parts[4],int(parts[6]))
                if row is None: raise RequestError(404,'Attachment not found')
                if row['content'] is None: raise RequestError(404,'Attachment content was not stored ('+(row['skipped_reason'] or 'unavailable')+')')
                content = bytes(row['content'])
                if len(parts)==8 and parts[7]=='preview':
                    if not (row['mime_type'] in ('text/plain','text/csv') or row['filename'].lower().endswith(('.txt','.csv'))):
                        raise RequestError(415,'Preview is available for text and CSV files only')
                    return self._json(200,{'name':row['filename'],'mime_type':row['mime_type'],'trusted':False,
                                           'content':content[:65536].decode('utf-8','replace'),'truncated':len(content)>65536})
                if len(parts)==7:  # Always a download: never rendered inline and never trusted for its declared type.
                    safe = ''.join(c if c.isalnum() or c in '._-' else '_' for c in row['filename'])[:100] or 'attachment'
                    disposition = f"attachment; filename=\"{safe}\"; filename*=UTF-8''{quote(row['filename'])}"
                    return self._send(200,content,'application/octet-stream',(('Content-Disposition',disposition),))
        if len(parts)==6 and parts[:4]==['api','connectors','gmail','messages'] and parts[5]=='proposal':
            message = self._gmail_message(session,parts[4])
            with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
            return self._json(200,proposal(message,user))
        if path == '/api/tasks/summary':
            with self._db() as db:
                summary = task_summary(db,user_id=session['id'])
                zone = prefs.effective(db,session['id'],self.gmail_config)['timezone']
            return self._json(200,{'summary':summary,'timezone':zone})
        if path == '/api/tasks':
            with self._db() as db:
                rows = list_tasks(db,user_id=session['id'])
                zone = prefs.effective(db,session['id'],self.gmail_config)['timezone']
            return self._json(200,{'tasks':rows,'timezone':zone})
        if len(parts) in (3,4) and parts[:2]==['api','tasks'] and parts[2].isdigit():
            with self._db() as db:
                task = get_task(db,int(parts[2]),user_id=session['id'])
                if not task: raise RequestError(404,'Task not found')
                if len(parts)==4 and parts[3]=='events': return self._json(200,{'events':events(db,task['id'],session['id'])})
                if len(parts)==3: return self._json(200,{'task':task})
        raise RequestError(404,'Not found')

    def _handle(self,write=False):
        try:
            self._route(write)
        except RequestError as exc:
            self._json(exc.status,{'error':exc.message})
        except ConflictError as exc:
            self._json(409,{'error':str(exc)})
        except ValueError as exc:
            self._json(400,{'error':str(exc)})
        except gmail_web.GmailConnectionError:
            self._json(503,{'error':'Gmail connection unavailable'})
        except sqlite3.Error:
            self._json(503,{'error':'Task database unavailable'})

    def do_GET(self): self._handle()
    def do_POST(self): self._handle(True)
    def do_PATCH(self): self._handle(True)
    def do_DELETE(self): self._handle(True)
    def log_message(self,*args): pass

class RequestError(Exception):
    def __init__(self,status,message): self.status,self.message=status,message

if __name__=='__main__':
    host,port='127.0.0.1',int(os.environ.get('INBOX_TEND_PORT','8765'))
    config=read_config(ROOT/'.env')
    Handler.gmail_config=config
    Handler.auth_manager=AuthManager(DEFAULT_DB,username=config.get('INBOX_TEND_ADMIN_USERNAME',''),
                                     password_hash=config.get('INBOX_TEND_ADMIN_PASSWORD_HASH',''))
    print(f'Inbox Tend: http://{host}:{port}')
    ThreadingHTTPServer((host,port),Handler).serve_forever()
