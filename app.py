"""Local fictional inbox and reviewed task demo. Never connects to or sends email."""
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
    'index': ('Inbox Tend · Workspace preview', 'Review fictional messages and track local follow-up tasks.', '/static/app.js', 'workspace'),
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

MESSAGES = [
 {'id':'sample-1','thread_id':'thread-1','provider':'demo','from':'alex@example.org','subject':'Website login issue','date':'2026-11-15','body':'Hi, the login form sometimes fails after submitting. Could you investigate and let us know what you find?','priority':'high'},
 {'id':'sample-2','thread_id':'thread-2','provider':'demo','from':'team@example.org','subject':'Friday project update','date':'2026-11-14','body':'Please send a short progress update for the dashboard project when you have time.','priority':'medium'},
 {'id':'sample-3','thread_id':'thread-3','provider':'demo','from':'newsletter@example.org','subject':'Weekly design newsletter','date':'2026-11-13','body':"This week's design roundup includes ideas for accessible forms and simpler navigation.",'priority':'low'},
 {'id':'sample-website-followup','thread_id':'thread-website-followup','provider':'demo','from':'casey@example.org','subject':'Website project: response and start date','date':'2026-11-18','body':'Hello, I have not heard back about the website project. Our notes show a different service start date than the one in the last update. Could you check the agreed date and reply by November 20, 2026? Thank you.','priority':'high'},
 {'id':'sample-website-sent','thread_id':'thread-website-followup','provider':'demo','from':'me@example.org','direction':'sent','subject':'Re: Website project: response and start date','date':'2026-11-17','body':'Hello, I will review the recorded service start date and send a confirmed update once the project notes have been checked.','priority':'medium'}
]


# Fictional attachment content only. Preview data is deliberately separate from the
# public message listing and is never executed, uploaded, or handed to an agent.
DEMO_ATTACHMENTS = {
    'sample-website-followup': [
        {
            'id': 'start-date-note',
            'name': 'service-start-date.txt',
            'mime_type': 'text/plain',
            'content': 'FICTIONAL DEMO NOTE\\nOriginal proposed start: November 1, 2026.\\nClient believes the start is November 15, 2026.\\nThe agreement must be reviewed before confirming either date.',
        },
        {
            'id': 'sample-budget',
            'name': 'project-items.csv',
            'mime_type': 'text/csv',
            'content': 'Item,Status\\nLogin form,Needs review\\nService start date,Needs confirmation',
        },
    ],
}

def attachment_metadata(message_id):
    return [{'id':item['id'], 'name':item['name'], 'mime_type':item['mime_type'],
             'size_bytes':len(item['content'].encode('utf-8')),
             'preview_available':True, 'mode':'demo'}
            for item in DEMO_ATTACHMENTS.get(message_id, [])]

def attachment_preview(message_id, attachment_id):
    for item in DEMO_ATTACHMENTS.get(message_id, []):
        if item['id'] == attachment_id:
            content = item['content']
            if item['mime_type'] not in ('text/plain','text/csv') or len(content.encode('utf-8')) > 64 * 1024:
                return None
            return {'id':item['id'], 'name':item['name'],
                    'mime_type':item['mime_type'], 'content':content,
                    'truncated':False, 'mode':'demo', 'trusted':False}
    return None

def summarize(message):
    """A deterministic excerpt, not AI extraction."""
    s = ' '.join(message['body'].split())
    return s if len(s) <= 150 else s[:147].rstrip() + '...'

def draft_reply(message):
    return ("Hello,\n\nThank you for following up about '" + message['subject'] + "'. "
            "I will review the details, including any date discrepancy, and confirm the next steps after checking the record. "
            "This is a draft for your review.\n\nBest regards")

def proposal(message):
    if message['id'] == 'sample-website-followup':
        inferred = {'title':'Review website project start date and reply',
                    'description':'Check the agreed service start date and prepare a response to the follow-up.',
                    'priority':'high','due_date':'2026-11-20','due_time':'17:00',
                    'due_timezone':'Pacific/Saipan','project_label':'Website project','customer_label':'Fictional client'}
        note = 'Rule-based demo suggestion from the fictional message; verify the start date before responding.'
    else:
        inferred = {'title':message['subject'],'description':'Review this message and decide next steps.',
                    'priority':message['priority'],'due_date':None,'due_time':None,
                    'due_timezone':'Pacific/Saipan','project_label':'','customer_label':''}
        note = 'Rule-based demo suggestion; no deadline was inferred.'
    return {'source':{'provider':'demo','message_id':message['id'],'thread_id':message['thread_id'],
                      'subject':message['subject'],'date':message['date']},
            'inferred':inferred,'note':note}

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

    def _message(self,id):
        return next((m for m in MESSAGES if m['id']==id),None)

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
        expected = 'http://' + self.headers['Host']
        if origin and origin != expected:
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
            if path == '/api/health': return self._json(200,{'status':'ok','mode':'demo'})
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
                message = self._message(data.get('id'))
                if not message: raise RequestError(404,'Message not found')
                return self._json(200,{'draft':draft_reply(message),'sent':False,'mode':'demo','template':True})
            if path == '/api/tasks' and self.command == 'POST':
                message = self._message(data.get('message_id'))
                if not message: raise RequestError(404,'Message not found')
                with self._db() as db:
                    existing = task_for_source(db,'demo',message['id'],session['id'])
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
            return self._json(200,{'csrf_token':session['csrf_token'],'user':{'username':session['username'],'role':session['role']},'mode':'demo'})
        if path == '/api/messages':
            return self._json(200,{'mode':'demo','messages':[dict(m,direction=m.get('direction','received'),summary=summarize(m),attachments=attachment_metadata(m['id'])) for m in MESSAGES]})
        parts = path.strip('/').split('/')
        if len(parts)==5 and parts[:2]==['api','messages'] and parts[3]=='attachments':
            message = self._message(parts[2])
            if not message: raise RequestError(404,'Message not found')
            result = attachment_preview(parts[2], parts[4])
            if result is None: raise RequestError(404,'Attachment preview not available')
            return self._json(200,result)
        if len(parts)==4 and parts[:2]==['api','messages'] and parts[3]=='proposal':
            message = self._message(parts[2])
            if not message: raise RequestError(404,'Message not found')
            return self._json(200,proposal(message))
        if path == '/api/tasks/summary':
            with self._db() as db: summary = task_summary(db,user_id=session['id'])
            return self._json(200,{'summary':summary,'timezone':'Pacific/Saipan','mode':'demo'})
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
