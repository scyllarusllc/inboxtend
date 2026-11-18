"""Local single-admin Gmail task workspace. Reads Gmail headers and snippets only; never sends email."""
from __future__ import annotations
import json
import os
import secrets
import sqlite3
from html import escape
from contextlib import contextmanager, closing
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import gmail_web
from auth import AuthManager, SESSION_SECONDS, cookie_token, read_config
from tasks import connection, DEFAULT_DB, list_tasks, task_summary, get_task, task_for_source, create_task, update_task, events, delete_task, ConflictError

ROOT = Path(__file__).resolve().parent
PAGE_NAV = {
    'public': '<a href="/">Home</a><a href="/user/dashboard">Workspace</a><a href="/login">Sign in</a>',
    'workspace': '<a href="/">Home</a><a href="#inbox-workspace">Inbox</a><a href="#task-workspace">Tasks</a><a href="#privacy-note">Privacy</a><button id="logout" type="button">Sign out</button>',
}
PAGES = {
    'home': ('Inbox Tend · Review work from email', 'Review email-derived work with a human in control.', '', 'public'),
    'login': ('Sign in · Inbox Tend', 'Sign in to the Inbox Tend workspace.', '/static/login.js', 'public'),
    'index': ('Inbox Tend · Workspace', 'Review your Gmail inbox and track local follow-up tasks.', '/static/app.js', 'workspace'),
}

def render_page(name):
    """Compose trusted static page content with one shared header and footer."""
    title, description, script, nav = PAGES[name]
    layout = (ROOT/'static'/'layout.html').read_text()
    content = (ROOT/'static'/'pages'/f'{name}.html').read_text()
    replacements = {
        '{{TITLE}}': escape(title, quote=True),
        '{{DESCRIPTION}}': escape(description, quote=True),
        '{{SCRIPT}}': f'<script src="{script}" defer></script>' if script else '',
        '{{NAV}}': PAGE_NAV[nav],
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

def proposal(message):
    """Rule-based starting point for a task; no deadline is inferred."""
    subject = (message.get('subject') or '').strip() or '(No subject)'
    return {'source':{'provider':'gmail','message_id':message['id'],'thread_id':message['thread_id'],
                      'subject':subject,'date':message['date']},
            'inferred':{'title':subject[:200],'description':summarize(message),
                        'priority':'medium','due_date':None,'due_time':None,
                        'due_timezone':'Pacific/Saipan','project_label':'','customer_label':''},
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
        except gmail_web.GmailReadError as exc: raise RequestError(502,str(exc)) from None

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
                if self._session(): return self._redirect('/user/dashboard')
                return self._send(200,render_page('login'),'text/html; charset=utf-8')
            if path == '/static/app.js':
                return self._send(200,(ROOT/'static'/'app.js').read_bytes(),'text/javascript; charset=utf-8')
            if path == '/static/login.js':
                return self._send(200,(ROOT/'static'/'login.js').read_bytes(),'text/javascript; charset=utf-8')
            if path == '/static/styles.css':
                return self._send(200,(ROOT/'static'/'styles.css').read_bytes(),'text/css; charset=utf-8')
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
            from urllib.parse import parse_qs
            query = parse_qs(urlparse(self.path).query)
            if 'error' in query:
                return self._redirect('/user/dashboard?gmail=declined')
            if len(query.get('state',[])) != 1 or len(query.get('code',[])) != 1:
                raise RequestError(400,'Invalid OAuth callback')
            try:
                with self._db() as db:
                    gmail_web.finish(db,self.gmail_config,session['id'],cookie_token(self.headers.get('Cookie')),query['state'][0],query['code'][0])
            except gmail_web.GmailConnectionError:
                return self._redirect('/user/dashboard?gmail=failed')
            return self._redirect('/user/dashboard?gmail=connected')
        if not write and path == '/user/dashboard':
            return self._send(200,render_page('index'),'text/html; charset=utf-8')
        if write:
            self._write_ok(session)
            data = self._read_json()
            if path == '/api/connectors/gmail/connect' and self.command == 'POST':
                try:
                    with self._db() as db:
                        url = gmail_web.begin(db,self.gmail_config,session['id'],cookie_token(self.headers.get('Cookie')))
                except gmail_web.GmailConnectionError as exc: raise RequestError(503,str(exc)) from None
                return self._json(200,{'authorization_url':url})
            if path == '/api/connectors/gmail/disconnect' and self.command == 'POST':
                with self._db() as db: gmail_web.disconnect(db,session['id'])
                return self._json(200,{'connected':False})
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
            with self._db() as db: connected = gmail_web.status(db,session['id'])
            return self._json(200,{'configured':gmail_web.configured(self.gmail_config),'connected':connected})
        if path == '/api/connectors/gmail/messages':
            try:
                with self._db() as db: rows = gmail_web.inbox(db,self.gmail_config,session['id'])
            except gmail_web.GmailConnectionError as exc: raise RequestError(503,str(exc)) from None
            except gmail_web.GmailReadError as exc: raise RequestError(502,str(exc)) from None
            return self._json(200,{'messages':rows,'mode':'gmail','read_only':True})
        if path == '/api/session':
            return self._json(200,{'csrf_token':session['csrf_token'],'user':{'username':session['username'],'role':session['role']}})
        parts = path.strip('/').split('/')
        if len(parts)==6 and parts[:4]==['api','connectors','gmail','messages'] and parts[5]=='proposal':
            return self._json(200,proposal(self._gmail_message(session,parts[4])))
        if path == '/api/tasks/summary':
            with self._db() as db: summary = task_summary(db,user_id=session['id'])
            return self._json(200,{'summary':summary,'timezone':'Pacific/Saipan'})
        if path == '/api/tasks':
            with self._db() as db: rows = list_tasks(db,user_id=session['id'])
            return self._json(200,{'tasks':rows,'timezone':'Pacific/Saipan'})
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
