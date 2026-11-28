"""Local single-admin Gmail task workspace. Reads Gmail headers and snippets only; never sends email."""
from __future__ import annotations
import json
import os
import re
import secrets
import time
import sqlite3
import threading
import traceback
from html import escape
from contextlib import contextmanager, closing
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import ai
import gmail_web
import archive
import imap_web
import google_login
import mailsearch
import flags
import topics
import websites
import settings as prefs
import mailstats
import local_translation
import translation_preload
import agentsteers_bridge
import integration_feed
from task_project import export_task, project_path, read_project
from auth import AuthManager, SESSION_SECONDS, cookie_token, read_config
from tasks import connection, DEFAULT_DB, get_task, task_for_source, scope_source, task_for_scope, create_task, update_task, delete_task, task_images, task_image, save_task_image_review, task_work_plan, save_task_work_plan, rotate_task_image, task_image_review, ConflictError, reserve_agentsteers_dispatch, record_agentsteers_update

ROOT = Path(__file__).resolve().parent
_TRANSLATION_TIMINGS = {}
_TRANSLATION_TIMINGS_LOCK = threading.Lock()
NAV_ITEMS = (('inbox','/user/inbox','Inbox','inbox'),('archived','/user/archived','Archived','box-archive'),('tasks','/user/tasks','Tasks','list-check'))
# name -> (title, description, scripts, active nav item, signed in)
PAGES = {
    'home': ('Inbox Tend · Gmail and IMAP mail to tasks', 'Review Gmail and IMAP mail, translate text, and track follow-up tasks on your own server.', (), 'home', False),
    'login': ('Sign in · Inbox Tend', 'Sign in to the Inbox Tend workspace.', ('/static/login.js',), None, False),
    'inbox': ('Inbox · Inbox Tend', 'Review your Gmail inbox and turn messages into local tasks.', ('/static/common.js','/static/inbox.js','/static/vendor/chartjs/chart.umd.min.js','/static/stats.js'), 'inbox', True),
    'archived': ('Archived · Inbox Tend', 'Mail from senders you archived: read automatically and kept out of the Inbox.', ('/static/common.js','/static/archived.js'), 'archived', True),
    'tasks': ('Tasks · Inbox Tend', 'Track local follow-up tasks, owners, deadlines and status.', ('/static/common.js','/static/task-parts.js','/static/tasks.js'), 'tasks', True),
    'task': ('Task · Inbox Tend', 'One follow-up task: details, deadline, history and images.', ('/static/common.js','/static/task-parts.js','/static/task.js','/static/zh.js'), 'tasks', True),
    'mail': ('Email · Inbox Tend', 'Read one email with its conversation, attachments and task.', ('/static/common.js','/static/mail.js'), 'inbox', True),
    'settings': ('Settings · Inbox Tend', 'Timezone, display, mail sync and account settings.', ('/static/common.js','/static/settings.js'), 'settings', True),
    'privacy': ('Privacy · Inbox Tend', 'What Inbox Tend reads, keeps and never does.', (), None, False),
}
VENDOR_TYPES = {'.css':'text/css; charset=utf-8','.woff2':'font/woff2','.js':'text/javascript','.svg':'image/svg+xml'}
SCREEN_TYPES = {'.jpg':'image/jpeg','.png':'image/png'}   # Screenshots of the demo data, shown on the public home page
STATIC_FILES = {'/static/icons/favicon.svg':'image/svg+xml','/static/icons/favicon-32.png':'image/png','/static/icons/apple-touch-icon.png':'image/png',
                '/static/styles.css':'text/css','/static/common.js':'text/javascript','/static/inbox.js':'text/javascript','/static/task-parts.js':'text/javascript','/static/task.js':'text/javascript','/static/zh.js':'text/javascript','/static/mail.js':'text/javascript','/static/archived.js':'text/javascript','/static/stats.js':'text/javascript',
                '/static/tasks.js':'text/javascript','/static/settings.js':'text/javascript','/static/login.js':'text/javascript',
                '/static/dev-reload.js':'text/javascript','/static/stimulus-app.js':'text/javascript'}

def _nav_link(href, label, icon, current):
    return ('<a href="%s"%s class="nav-link"><i class="fa-solid fa-%s fa-fw" aria-hidden="true"></i><span>%s</span></a>'
            % (href, ' aria-current="page"' if current else '', icon, label))

def nav_html(active, signed_in):
    """Identical links on every page (the logo is the link home); only the current-page marker and the auth control vary."""
    links = ''.join(_nav_link(href, label, icon, key == active) for key, href, label, icon in NAV_ITEMS)
    if not signed_in:
        return links + _nav_link('/login', 'Sign in', 'right-to-bracket', False)
    return (links + _nav_link('/user/settings', 'Settings', 'gear', active == 'settings') +
            '<button id="logout" type="button"><i class="fa-solid fa-right-from-bracket fa-fw" aria-hidden="true"></i><span>Sign out</span></button>')

def render_page(name, session=False):
    """Compose trusted static page content with one shared header and footer."""
    title, description, scripts, active, signed_in = PAGES[name]
    inside = signed_in or session   # Someone who is signed in lands in their inbox from the logo; the public site moves to the footer.
    layout = (ROOT/'static'/'layout.html').read_text()
    content = (ROOT/'static'/'pages'/f'{name}.html').read_text()
    replacements = {
        '{{TITLE}}': escape(title, quote=True),
        '{{DESCRIPTION}}': escape(description, quote=True),
        '{{SCRIPT}}': ('<script type="module" src="/static/stimulus-app.js"></script>' if signed_in else '') +
                      ''.join(f'<script src="{src}" defer></script>' for src in scripts +
                              (('/static/dev-reload.js',) if os.environ.get('INBOX_TEND_DEV_RELOAD') == '1' else ())),
        '{{NAV}}': nav_html(active, signed_in),
        '{{BRAND_HREF}}': '/user/inbox' if inside else '/',
        '{{BRAND_LABEL}}': 'Inbox Tend: open my inbox' if inside else 'Inbox Tend home',
        '{{FOOTER_SITE}}': '<a href="/" class="underline">Official website</a> · ' if inside and name != 'home' else '',
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
    return {'source':{'provider':'imap' if message['id'].startswith('imap-') else 'gmail','message_id':message['id'],'thread_id':message['thread_id'],
                      'subject':subject,'date':message['date']},
            'inferred':{'title':subject[:200],'description':summarize(message),
                        'priority':user['default_priority'],'due_date':None,'due_time':None,
                        'due_timezone':user['timezone'],'project_label':'','customer_label':''},
            'note':'Rule-based suggestion from the Gmail subject and snippet; no deadline was inferred.'}

def raster_preview_mime(mime,content):
    signatures = {
        'image/png': content.startswith(b'\x89PNG\r\n\x1a\n'),
        'image/jpeg': content.startswith(b'\xff\xd8\xff'),
        'image/gif': content.startswith((b'GIF87a',b'GIF89a')),
        'image/webp': content.startswith(b'RIFF') and content[8:12] == b'WEBP',
    }
    return mime if len(content) <= ai.MAX_IMAGE_BYTES and signatures.get(mime,False) else None

class Handler:
    db_path = DEFAULT_DB
    auth_manager = None
    gmail_config = {}

    def _send(self,status,payload,mime,extra_headers=()):
        raise NotImplementedError('The ASGI adapter supplies HTTP responses')

    def _json(self,status,data,extra_headers=()):
        self._send(status,json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8',extra_headers)

    def _redirect(self,location,extra_headers=()):
        raise NotImplementedError('The ASGI adapter supplies HTTP redirects')

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

    def _glogin_cookie(self,token,max_age=google_login.PENDING_SECONDS):
        """The short-lived cookie that ties a Google sign-in to the browser that started it."""
        secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
        return f'it_glogin={token}; HttpOnly; SameSite=Lax; Path=/api/auth/google; Max-Age={max_age}{secure}'

    def _google_callback(self):
        """Google sends the person back here, signed in or not: sign in (or link, from Settings) or explain why not."""
        query = parse_qs(urlparse(self.path).query)
        clear = (('Set-Cookie',self._glogin_cookie('',0)),)
        session = self._session()
        back = '/login'
        try:
            if 'error' in query: raise google_login.GoogleLoginError('declined')
            if len(query.get('state',[])) != 1 or len(query.get('code',[])) != 1 or not self.auth_manager: raise google_login.GoogleLoginError('failed')
            with self._db() as db:
                row,claims = google_login.finish(db,self.gmail_config,cookie_token(self.headers.get('Cookie'),'it_glogin'),query['state'][0],query['code'][0])
                if row['purpose'] == 'link':
                    back = '/user/settings'
                    if not session or session['id'] != row['user_id']: raise google_login.GoogleLoginError('failed')
                    google_login.link(db,session['id'],claims)
                    return self._redirect('/user/settings?google=linked',clear)
                user_id = google_login.sign_in(db,claims)
            token,_ = self.auth_manager.new_session(user_id)
            return self._redirect('/user/inbox',clear + (('Set-Cookie',self._cookie_header(token,SESSION_SECONDS)),))
        except google_login.GoogleLoginError as exc:
            return self._redirect(f'{back}?google={exc.code}',clear)

    def _route(self,write=False):
        path = urlparse(self.path).path
        if not self._host_ok(): raise RequestError(403,'Invalid Host')
        if not write:
            if path == '/':
                return self._send(200,render_page('home',bool(self._session())),'text/html; charset=utf-8')
            if path == '/login':
                if self._session(): return self._redirect('/user/inbox')
                return self._send(200,render_page('login'),'text/html; charset=utf-8')
            if path == '/favicon.ico':   # Browsers ask for this path on their own
                return self._send(200,(ROOT/'static/icons/favicon-32.png').read_bytes(),'image/png',(('Cache-Control','public, max-age=86400'),))
            if path in STATIC_FILES:
                return self._send(200,(ROOT/path.lstrip('/')).read_bytes(),STATIC_FILES[path]+'; charset=utf-8')
            if path.startswith('/static/screens/'):
                kind = SCREEN_TYPES.get(Path(path).suffix)
                target = (ROOT/'static'/path[len('/static/'):]).resolve()
                if kind is None or not target.is_relative_to(ROOT/'static'/'screens') or not target.is_file():
                    raise RequestError(404,'Not found')
                return self._send(200,target.read_bytes(),kind,(('Cache-Control','public, max-age=3600'),))
            if path.startswith('/static/vendor/'):   # Third-party assets (Font Awesome, flag icons): only .css, .woff2 and .svg inside static/vendor.
                kind = VENDOR_TYPES.get(Path(path).suffix)
                target = (ROOT/'static'/path[len('/static/'):]).resolve()
                if kind is None or not target.is_relative_to(ROOT/'static'/'vendor') or not target.is_file():
                    raise RequestError(404,'Not found')
                return self._send(200,target.read_bytes(),kind,(('Cache-Control','public, max-age=86400'),))
            if path == '/LICENSE':   # AGPL: the license text is offered to everyone who uses the service
                return self._send(200,(ROOT/'LICENSE').read_bytes(),'text/plain; charset=utf-8')
            if path == '/privacy':
                return self._send(200,render_page('privacy',bool(self._session())),'text/html; charset=utf-8')
            if path == '/api/health': return self._json(200,{'status':'ok'})
        if not write and path == '/integration/v1/tasks':
            try:
                with self._db() as db:
                    result = integration_feed.read_page(db, self.headers.get('Authorization',''), self.path)
            except integration_feed.FeedError as exc:
                raise RequestError(exc.status, exc.message) from None
            return self._json(200, result, (('Cache-Control','private, no-store'),))
        if not write and path == '/api/auth/providers':   # Public: lets the login page show only what works
            return self._json(200,{'google':bool(self.auth_manager) and google_login.configured(self.gmail_config)})
        if not write and path == '/api/auth/google/start':
            if not self.auth_manager or not google_login.configured(self.gmail_config): return self._redirect('/login?google=off')
            with self._db() as db: url,browser = google_login.begin(db,self.gmail_config,'login')
            return self._redirect(url,(('Set-Cookie',self._glogin_cookie(browser)),))
        if not write and path == '/api/auth/google/callback':
            return self._google_callback()
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
                return self._redirect('/user/settings?gmail=declined')
            if len(query.get('state',[])) != 1 or len(query.get('code',[])) != 1:
                raise RequestError(400,'Invalid OAuth callback')
            try:
                with self._db() as db:
                    level,next_page = gmail_web.finish(db,self.gmail_config,session['id'],cookie_token(self.headers.get('Cookie')),query['state'][0],query['code'][0])
                    prefs.save(db,session['id'],{'gmail_write_read':gmail_web.write_access(db,session['id'])})  # On while at least one account may change read/unread.
            except gmail_web.GmailConnectionError:
                return self._redirect('/user/settings?gmail=failed')
            if level == 'modify': return self._redirect('/user/settings?gmail=write')
            return self._redirect('/user/settings?gmail=readonly' if next_page == 'settings' else '/user/inbox?gmail=connected')
        if not write and path == '/user/dashboard':
            return self._redirect('/user/inbox')  # Legacy URL
        if not write and path in ('/user/inbox','/user/tasks','/user/settings','/user/archived'):
            return self._send(200,render_page(path.rsplit('/',1)[1]),'text/html; charset=utf-8')
        if not write and re.fullmatch(r'/user/task/\d{1,12}',path):
            return self._send(200,render_page('task'),'text/html; charset=utf-8')
        if not write and re.fullmatch(r'/user/mail/[A-Za-z0-9._:~%-]{1,200}',path):
            return self._send(200,render_page('mail'),'text/html; charset=utf-8')
        if write:
            self._write_ok(session)
            data = self._read_json()
            if path == '/api/connectors/gmail/connect' and self.command == 'POST':
                try:
                    with self._db() as db:
                        access = data.get('access','readonly')
                        if access not in gmail_web.LEVELS: raise RequestError(400,'Unknown access level')
                        next_page = data.get('next','inbox')
                        if next_page not in ('inbox','settings'): raise RequestError(400,'Unknown return page')
                        account_id = data.get('account_id')
                        if account_id is not None and (not isinstance(account_id,int) or isinstance(account_id,bool)): raise RequestError(400,'Invalid account')
                        url = gmail_web.begin(db,self.gmail_config,session['id'],cookie_token(self.headers.get('Cookie')),access,next_page,account_id)
                except gmail_web.GmailConnectionError as exc: raise RequestError(503,str(exc)) from None
                return self._json(200,{'authorization_url':url})
            if path == '/api/connectors/imap/connect' and self.command == 'POST':
                try:
                    with self._db() as db:
                        account_id = imap_web.add(db,self.gmail_config,session['id'],data.get('email'),data.get('host','imap.mail.me.com'),data.get('username'),data.get('password'))
                except (ValueError,imap_web.IMAPError) as exc: raise RequestError(400,str(exc)) from None
                except (OSError,imap_web.imaplib.IMAP4.error): raise RequestError(424,'Cannot connect to IMAP server or sign in') from None
                return self._json(201,{'account_id':account_id})
            if path == '/api/connectors/imap/disconnect' and self.command == 'POST':
                account_id=data.get('account_id')
                if not isinstance(account_id,int) or isinstance(account_id,bool) or account_id>=0: raise RequestError(400,'Invalid IMAP account')
                try:
                    with self._db() as db: imap_web.remove(db,session['id'],account_id)
                except imap_web.IMAPError as exc: raise RequestError(404,str(exc)) from None
                return self._json(200,{'connected':True})
            if path == '/api/connectors/gmail/sync' and self.command == 'POST':
                with self._db() as db:
                    if not gmail_web.status(db,session['id']): raise RequestError(503,'No mail account is connected')
                    if not gmail_web.configured(self.gmail_config) and not any(a['provider']=='imap' for a in gmail_web.accounts(db,session['id'])): raise RequestError(503,'No mail connector is configured')
                # Runs in the background: a full sync outlasts proxy timeouts. Poll /status for progress.
                account_id = data.get('account_id')
                if account_id is not None:
                    with self._db() as db:
                        if not isinstance(account_id,int) or isinstance(account_id,bool) or account_id not in [a['id'] for a in gmail_web.accounts(db,session['id'])]:
                            raise RequestError(404,'Unknown Gmail account')
                return self._json(202,{'sync':gmail_web.start_sync(self.db_path,self.gmail_config,session['id'],account_id)})
            if path == '/api/connectors/gmail/disconnect' and self.command == 'POST':
                account_id = data.get('account_id')   # One account, or (none given) all of them
                with self._db() as db:
                    if account_id is not None and (not isinstance(account_id,int) or isinstance(account_id,bool) or account_id not in [a['id'] for a in gmail_web.accounts(db,session['id'])]):
                        raise RequestError(404,'Unknown Gmail account')
                    gmail_web.disconnect(db,session['id'],account_id)
                    if not gmail_web.write_access(db,session['id']): prefs.save(db,session['id'],{'gmail_write_read':False})
                    connected = gmail_web.status(db,session['id'])
                return self._json(200,{'connected':connected})
            if path == '/api/connectors/gmail/check' and self.command == 'POST':   # Is this Gmail account's authorisation still valid, and for how long?
                account_id = data.get('account_id')
                with self._db() as db:
                    if not isinstance(account_id,int) or isinstance(account_id,bool) or account_id not in [a['id'] for a in gmail_web.accounts(db,session['id']) if a['provider']=='gmail']:
                        raise RequestError(404,'Unknown Gmail account')
                    return self._json(200,gmail_web.check(db,self.gmail_config,session['id'],account_id))
            if path == '/api/auth/google/link' and self.command == 'POST':   # Settings -> Account: add a Google account that may sign in as this user
                try:
                    with self._db() as db: url,browser = google_login.begin(db,self.gmail_config,'link',session['id'])
                except gmail_web.GmailConnectionError as exc: raise RequestError(503,str(exc)) from None
                return self._json(200,{'authorization_url':url},(('Set-Cookie',self._glogin_cookie(browser)),))
            if path == '/api/auth/google/unlink' and self.command == 'POST':
                if not isinstance(data.get('id'),int) or isinstance(data.get('id'),bool): raise RequestError(400,'Invalid link')
                with self._db() as db:
                    if not google_login.unlink(db,session['id'],data['id']): raise RequestError(404,'Link not found')
                return self._json(200,{'unlinked':True})
            if path == '/api/settings' and self.command == 'PATCH':
                with self._db() as db:
                    prefs.save(db,session['id'],data)
                    return self._json(200,{'settings':prefs.effective(db,session['id'],self.gmail_config)})
            if path == '/api/auth/password' and self.command == 'POST':
                result = self.auth_manager.change_password(session['id'],data.get('current'),data.get('new'),cookie_token(self.headers.get('Cookie')))
                if result == 'throttled': raise RequestError(429,'Too many attempts; try again in a few minutes')
                if result == 'invalid': raise RequestError(403,'Current password is incorrect')
                return self._json(200,{'changed':True})
            if path == '/api/connectors/gmail/messages/read-all' and self.command == 'POST':   # Everything unread that the list currently shows
                if 'ids' in data:   # One conversation or person: its own messages, whatever else is in the list
                    chosen = data['ids']
                    if not isinstance(chosen,list) or not 0 < len(chosen) <= 1000 or not all(isinstance(i,str) and 0 < len(i) <= 200 for i in chosen): raise RequestError(400,'Invalid messages')
                    with self._db() as db:
                        ids = gmail_web.unread_among(db,session['id'],chosen)
                        user = prefs.effective(db,session['id'],self.gmail_config)
                        return self._json(200,gmail_web.mark_all_read(db,self.gmail_config,session['id'],ids,write=user['gmail_write_read']))
                folder = data.get('folder','inbox')
                if folder not in gmail_web.FOLDERS: raise RequestError(400,'Unknown folder')
                account_id = data.get('account_id')
                if account_id is not None and (not isinstance(account_id,int) or isinstance(account_id,bool)): raise RequestError(400,'Invalid account')
                q = data.get('q','')
                if not isinstance(q,str) or len(q) > 200: raise RequestError(400,'Invalid search')
                with self._db() as db:
                    if account_id is not None and account_id not in [a['id'] for a in gmail_web.accounts(db,session['id'])]: raise RequestError(404,'Unknown Gmail account')
                    search = mailsearch.parse(q,unread=data.get('unread') is True,attachments=data.get('attachments') is True,tz=prefs.effective(db,session['id'],self.gmail_config)['timezone'])
                    ids = gmail_web.unread_ids(db,session['id'],folder,search if mailsearch.condition(search)[0] else None,account_id)
                    user = prefs.effective(db,session['id'],self.gmail_config)
                    return self._json(200,gmail_web.mark_all_read(db,self.gmail_config,session['id'],ids,write=user['gmail_write_read']))
            parts_w = path.strip('/').split('/')
            if len(parts_w)==8 and parts_w[:4]==['api','connectors','gmail','messages'] and parts_w[5]=='attachments' and parts_w[6].isdigit() and parts_w[7]=='rotate' and self.command == 'POST':
                if type(data.get('rotation')) is not int or data['rotation'] not in (0,90,180,270): raise RequestError(400,'Rotation must be 0, 90, 180 or 270')
                with self._db() as db:
                    if not gmail_web.rotate_attachment(db,session['id'],parts_w[4],int(parts_w[6]),data['rotation']): raise RequestError(404,'Attachment not found')
                return self._json(200,{'rotation':data['rotation']})
            if len(parts_w) == 6 and parts_w[:4] == ['api','connectors','gmail','messages'] and parts_w[5] == 'read' and self.command == 'POST':
                if not isinstance(data.get('read'),bool): raise RequestError(400,'"read" must be true or false')
                with self._db() as db:
                    user = prefs.effective(db,session['id'],self.gmail_config)
                    outcome = gmail_web.set_read(db,self.gmail_config,session['id'],parts_w[4],data['read'],write=user['gmail_write_read'])
                if outcome is None: raise RequestError(404,'Message not found')
                return self._json(200,dict(outcome,id=parts_w[4]))
            if path == '/api/archived/senders' and self.command == 'POST':   # Archive a sender: from one of their messages
                mid = data.get('message_id')
                if not isinstance(mid,str) or not 0 < len(mid) <= 200: raise RequestError(400,'Message ID required')
                with self._db() as db:
                    try: result = archive.add_sender(db,self.gmail_config,session['id'],mid)
                    except ValueError as exc: raise RequestError(400,str(exc)) from None
                if result is None: raise RequestError(404,'Message not found')
                return self._json(200,result)
            if path == '/api/archived/senders/remove' and self.command == 'POST':
                address = data.get('address')
                if not isinstance(address,str) or not 0 < len(address) <= 254: raise RequestError(400,'Address required')
                with self._db() as db: restored = archive.remove_sender(db,session['id'],address)
                if restored is None: raise RequestError(404,'That sender is not on the archived list')
                return self._json(200,{'removed':True,'restored':restored})
            if path == '/api/topics/guess' and self.command == 'POST':
                with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
                if not user['topics_enabled']: raise RequestError(403,'Topic icons are turned off in Settings')
                return self._json(202,{'topics':topics.start(self.db_path,self.gmail_config,session['id'],user)})
            if path == '/api/topics/reset' and self.command == 'POST':
                with self._db() as db: topics.reset(db,session['id'])
                return self._json(200,{'reset':True})
            if path == '/api/websites/guess' and self.command == 'POST':
                with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
                if not user['websites_enabled']: raise RequestError(403,'Website guesses are turned off in Settings')
                return self._json(202,{'websites':websites.start(self.db_path,self.gmail_config,session['id'],user)})
            if path == '/api/flags/guess' and self.command == 'POST':
                with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
                if not user['flags_enabled']: raise RequestError(403,'Country flags are turned off in Settings')
                return self._json(202,{'flags':flags.start(self.db_path,self.gmail_config,session['id'],user)})
            if path == '/api/flags/reset' and self.command == 'POST':
                with self._db() as db: flags.reset(db,session['id'])
                return self._json(200,{'reset':True})
            if path == '/api/translation/packs/install' and self.command == 'POST':
                try:
                    state = local_translation.start_install(data.get('source'),data.get('target'))
                except local_translation.TranslationError as exc: raise RequestError(400,str(exc)) from None
                return self._json(202,{'installation':state})
            if path == '/api/translate' and self.command == 'POST':
                started = time.perf_counter()
                with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
                if not user['translate_enabled']: raise RequestError(403,'Translation is turned off in Settings')
                language = user['translate_language'].strip()
                if not language: raise RequestError(400,'Choose the language to translate into in Settings')
                message = self._gmail_message(session,data.get('id'))
                fetched = time.perf_counter()
                force = data.get('force') is True
                def answer(found,cached):
                    from mail_content import readable_body
                    return self._json(200,{'translation':dict(found,body=readable_body(found['body']),cached=cached,provider='Argos Translate' if found['provider']=='argos' else ai.PROVIDERS.get(found['provider'],{}).get('name',found['provider']))})
                with self._db() as db:
                    found = gmail_web.get_translation(db,session['id'],message['id'],language)
                    if not found and not force: found = gmail_web.get_translation(db,session['id'],message['id'],local_translation.LANGUAGES[language])
                if found and not force: return answer(found,True)
                if language not in {item['code'] for item in local_translation.installed_targets()}:
                    raise RequestError(424,'The target language pack is not installed on this server. Install it in Settings → Translation.')
                try:
                    with ai.translation_guard(session['id'],60):
                        queued = time.perf_counter()
                        with self._db() as db: found = gmail_web.get_translation(db,session['id'],message['id'],language)
                        if found and not force:  # Reaching the lock with a stored result means another request finished while this one queued.
                            return answer(found,True)
                        result = local_translation.translate_message(message,language,user['translate_max_chars'])
                        translated = time.perf_counter()
                        with self._db() as db: gmail_web.save_translation(db,session['id'],message['id'],language,result,'argos','installed language pack')
                        with self._db() as db: found = gmail_web.get_translation(db,session['id'],message['id'],language)
                        with _TRANSLATION_TIMINGS_LOCK:
                            _TRANSLATION_TIMINGS[session['id']] = {'fetch_seconds':round(fetched-started,2),
                                'queue_seconds':round(queued-fetched,2), 'model_seconds':round(translated-queued,2),
                                'total_seconds':round(time.perf_counter()-started,2),
                                'input_chars':min(len(message.get('body_text') or message.get('snippet') or ''),user['translate_max_chars']),
                                'at':int(time.time())}
                        return answer(found,False)
                except ai.AIBusy as exc: raise RequestError(429,str(exc)) from None
                except ai.AIError as exc: raise RequestError(424,str(exc)) from None
                except local_translation.TranslationError as exc: raise RequestError(424,str(exc)) from None
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
                        if path == '/api/ai/work':
                            task_id = data.get('task_id')
                            if type(task_id) is not int: raise RequestError(400,'Task ID required')
                            with self._db() as db:
                                task = get_task(db,task_id,user_id=session['id'])
                                user = prefs.effective(db,session['id'],self.gmail_config)
                                saved = task_work_plan(db,task_id,session['id'])
                            if not task: raise RequestError(404,'Task not found')
                            if saved and data.get('refresh') is not True:
                                return self._json(200,{'saved':saved})   # Already done once: no second AI run unless asked.
                            if not user['ai_enabled']: raise RequestError(403,'The AI assistant is turned off in Settings')
                            try:
                                if not project_path(task_id).is_dir():
                                    export_task(self.db_path,session['id'],task_id)
                                project = read_project(task_id)
                            except (OSError, ValueError) as exc:
                                raise RequestError(503,'Could not read the task project directory') from None
                            plan = ai.work_plan(self.gmail_config,task,None,[],user,
                                                project_text=project['text'],project_images=project['images'])
                            result = {'plan':plan,'email_used':bool(project['text'] or project['images']),
                                      'project_used':True,'project_files':project['text_files'],
                                      'project_images':project['image_count'],
                                      'note':'Based on the task project folder.',
                                      'provider':ai.PROVIDERS[ai.provider_of(user)]['name'],'model':user['ai_model'] or 'provider default'}
                            with self._db() as db: saved = save_task_work_plan(db,task_id,session['id'],result)
                            return self._json(200,{'saved':saved})
                        if path == '/api/ai/work-translate':
                            task_id = data.get('task_id')
                            if type(task_id) is not int: raise RequestError(400,'Task ID required')
                            with self._db() as db:
                                task = get_task(db,task_id,user_id=session['id'])
                                saved = task_work_plan(db,task_id,session['id'])
                                user = prefs.effective(db,session['id'],self.gmail_config)
                            if not task or not saved: raise RequestError(404,'No saved AI plan for this task')
                            plan = saved['plan']
                            if any(isinstance(plan.get(k),dict) and plan[k].get('zh') for k in ('summary','approach')): return self._json(200,{'saved':saved})   # Already has Chinese.
                            if not user['ai_enabled']: raise RequestError(403,'The AI assistant is turned off in Settings')
                            done = ai.translate_plan(self.gmail_config,plan,user)
                            result = {k:v for k,v in saved.items() if k not in ('plan','created_at')}
                            result['plan'] = done
                            with self._db() as db: saved = save_task_work_plan(db,task_id,session['id'],result)
                            return self._json(200,{'saved':saved})
                        if path == '/api/ai/reply':
                            message,files,user = self._ai_inputs(session,data.get('id'))
                            guidance = data.get('guidance','')
                            if not isinstance(guidance,str): raise RequestError(400,'Invalid guidance')
                            return self._json(200,{'draft':ai.draft_reply(self.gmail_config,message,files,guidance,user),'sent':False,'template':False,'ai':True})
                except ai.AIBusy as exc: raise RequestError(429,str(exc)) from None
                except ai.AIError as exc: raise RequestError(424,str(exc)) from None
            if path == '/api/agentsteers/queue-summary' and self.command == 'POST':
                # Authenticated same-origin, CSRF-checked read-only remote call.
                # Never expose raw remote errors, credentials, or any job metadata.
                try:
                    counts = agentsteers_bridge.queue_summary()
                except agentsteers_bridge.BridgeError:
                    raise RequestError(503,'AgentSteers mock queue summary unavailable') from None
                return self._json(200,{'queue':counts,'mode':'mock_only'})
            if path == '/api/agentsteers/probe' and self.command == 'POST':
                # Authenticated same-origin request with CSRF, read-only remote ping.
                try:
                    info = agentsteers_bridge.probe()
                except agentsteers_bridge.BridgeError as exc:
                    return self._json(200,{'bridge':{
                        'state':'connection_failed','can_dispatch':False,
                        'message':'AgentSteers mock connection unavailable: '+str(exc)[:120]}})
                return self._json(200,{'bridge':info})
            if path == '/api/auth/logout' and self.command == 'POST':
                self.auth_manager.revoke(cookie_token(self.headers.get('Cookie')))
                return self._json(200,{'signed_out':True},
                                  (('Set-Cookie',self._cookie_header('',0)),))
            if path == '/api/draft' and self.command == 'POST':
                message = self._gmail_message(session,data.get('id'))
                return self._json(200,{'draft':draft_reply(message),'sent':False,'template':True})
            if path == '/api/tasks' and self.command == 'POST':
                self._gmail_message(session,data.get('message_id'))
                with self._db() as db:
                    message = scope_source(db,session['id'],data.get('message_id'),data.get('scope','messages'),data.get('account_id'))
                    data.setdefault('due_timezone',prefs.effective(db,session['id'],self.gmail_config)['timezone'])
                    existing = task_for_scope(db,message,session['id'])
                    if existing:
                        return self._json(409,{'error':'Task already exists','task':existing})
                    task = create_task(db,message,data,session['id'])
                return self._json(201,{'task':task})
            if path == '/api/tasks/from-message' and self.command == 'POST':
                intent = data.get('intent')
                suggestions = {
                    'translate_images': ('Translate attached images into Chinese', 'Read the text in every attached image, translate it into Chinese, and record anything unclear.'),
                    'explain_images': ('Explain what the attached images mean', 'Review every attached image, explain its meaning, and identify any information that needs verification.'),
                    'review_images': ('Review attached images and decide next steps', 'Review all attached images, translate any text into Chinese, and decide what follow-up is needed.'),
                }
                if intent not in suggestions: raise RequestError(400,'Unknown image task suggestion')
                self._gmail_message(session,data.get('message_id'))
                with self._db() as db:
                    message = scope_source(db,session['id'],data.get('message_id'),data.get('scope','messages'),data.get('account_id'))
                    placeholders = ','.join('?' for _ in message['source_ids'])
                    image_count = db.execute(f"SELECT COUNT(*) FROM gmail_attachments WHERE user_id=? AND message_id IN ({placeholders}) AND mime_type LIKE 'image/%'",
                                             (session['id'],*message['source_ids'])).fetchone()[0]
                    if not image_count: raise RequestError(400,'This message has no image attachments')
                    existing = task_for_scope(db,message,session['id'])
                    if existing: return self._json(200,{'task':existing,'existing':True})
                    user = prefs.effective(db,session['id'],self.gmail_config)
                    title,description = suggestions[intent]
                    task = create_task(db,message,{'title':title,'description':description,'priority':user['default_priority'],
                                                   'due_date':None,'due_time':None,'due_timezone':user['timezone']},session['id'])
                return self._json(201,{'task':task,'existing':False})
            parts = path.strip('/').split('/')
            if len(parts)==6 and parts[:2]==['api','tasks'] and parts[2].isdigit() and parts[3]=='images' and parts[4].isdigit() and parts[5]=='rotate' and self.command == 'POST':
                if data.get('rotation') not in (0,90,180,270) or type(data.get('rotation')) is not int: raise RequestError(400,'Rotation must be 0, 90, 180 or 270')
                with self._db() as db:
                    if not rotate_task_image(db,int(parts[2]),int(parts[4]),session['id'],data['rotation']): raise RequestError(404,'Task image not found')
                return self._json(200,{'rotation':data['rotation']})
            if len(parts)==5 and parts[:2]==['api','tasks'] and parts[2].isdigit() and parts[3:] == ['images','review'] and self.command == 'POST':
                task_id = int(parts[2])
                with self._db() as db:
                    task = get_task(db,task_id,user_id=session['id'])
                    if not task: raise RequestError(404,'Task not found')
                    kept = task_image_review(db,task_id,session['id'])
                    if kept and data.get('refresh') is not True: return self._json(200,{'review':kept})   # Done before: no second AI run unless asked.
                    user = prefs.effective(db,session['id'],self.gmail_config)
                    if not user['ai_enabled']: raise RequestError(403,'The AI assistant is turned off in Settings')
                    files = task_images(db,task_id,session['id'])
                    if not files: raise RequestError(400,'This task has no images')
                    if len(files) > ai.MAX_IMAGE_ATTACHMENTS: raise RequestError(400,f'Review at most {ai.MAX_IMAGE_ATTACHMENTS} images at a time')
                    images = []
                    for file in files:
                        row = task_image(db,task_id,file['id'],session['id'])
                        if row['content'] is None or not raster_preview_mime(row['mime_type'],bytes(row['content'])):
                            raise RequestError(400,'All task images must be stored, supported and at most 2 MiB for AI review')
                        images.append((row['filename'],row['mime_type'],bytes(row['content'])))
                context = {'direction':'received','from':'Task source','to':'Task owner','date':task['created_at'],
                           'subject':task['title'],'body_text':task['description']}
                try:
                    with ai.guard(session['id']):
                        result = ai.review_images(self.gmail_config,context,images,user)
                except ai.AIBusy as exc: raise RequestError(429,str(exc)) from None
                except ai.AIError as exc: raise RequestError(424,str(exc)) from None
                result = dict(result,provider=ai.PROVIDERS[ai.provider_of(user)]['name'],model=user['ai_model'] or 'provider default')
                with self._db() as db: saved = save_task_image_review(db,task_id,session['id'],result)
                return self._json(200,{'review':saved})
            if len(parts)==4 and parts[:2]==['api','tasks'] and parts[2].isdigit() and parts[3]=='agentsteers-dispatch' and self.command == 'POST':
                # Authenticated session + same-origin + CSRF checked above; explicit confirmation in request.
                if data.get('confirm') != 'DISPATCH MOCK':
                    raise RequestError(400,'Explicit mock dispatch confirmation required')
                if not isinstance(data.get('revision'),int) or isinstance(data.get('revision'),bool):
                    raise RequestError(400,'A valid task revision is required')
                if not agentsteers_bridge.configured() or os.environ.get('INBOX_TEND_AGENTSTEERS_BRIDGE_ENABLED') != '1':
                    raise RequestError(503,'AgentSteers mock bridge is not configured')
                task_id = int(parts[2])
                with self._db() as db:
                    task = reserve_agentsteers_dispatch(db,task_id,data['revision'],session['id'])
                if task is None: raise RequestError(404,'Task not found')
                try:
                    outcome = agentsteers_bridge.submit(task)
                    with self._db() as db:
                        task = record_agentsteers_update(db,task_id,session['id'],
                            outcome['status'],outcome['task_id'],{"message":"Mock task submitted; no actual code was executed."})
                except (agentsteers_bridge.BridgeError,KeyError,ValueError) as exc:
                    with self._db() as db:
                        record_agentsteers_update(db,task_id,session['id'],'dispatch_error',
                            result={"message":"Mock dispatch failed or result uncertain; consult AgentSteers before retrying."})
                    raise RequestError(503,'AgentSteers mock dispatch did not complete: '+str(exc)[:160]) from None
                return self._json(200,{'task':task,'simulated':True})
            if len(parts)==4 and parts[:2]==['api','tasks'] and parts[2].isdigit() and parts[3]=='agentsteers-cancel' and self.command == 'POST':
                if data.get('confirm') != 'CANCEL MOCK':
                    raise RequestError(400,'Explicit mock cancellation confirmation required')
                if not isinstance(data.get('revision'),int) or isinstance(data.get('revision'),bool):
                    raise RequestError(400,'A valid task revision is required')
                task_id=int(parts[2])
                with self._db() as db:
                    task=get_task(db,task_id,user_id=session['id'])
                if not task: raise RequestError(404,'Task not found')
                if task['revision'] != data['revision']:
                    raise ConflictError('Task changed; reload before cancellation')
                if not task['agentsteers_job_id'] or task['agent_status'] not in ('queued_mock','recovery_needed'):
                    raise RequestError(409,'No cancellable mock job')
                try:
                    outcome=agentsteers_bridge.cancel(task['agentsteers_job_id'])
                    with self._db() as db:
                        updated=record_agentsteers_update(db,task_id,session['id'],'cancelled',
                            outcome['task_id'],
                            {'message':'Mock task cancelled by user. No real execution occurred.'})
                except agentsteers_bridge.BridgeError:
                    # Remote may have accepted the cancellation before connection dropped.
                    # Preserve the last known state and never claim success.
                    raise RequestError(503,'Cancellation could not be confirmed. Refresh the mock job status.') from None
                return self._json(200,{'task':updated,'simulated':True})
            if len(parts)==4 and parts[:2]==['api','tasks'] and parts[2].isdigit() and parts[3]=='agentsteers-refresh' and self.command == 'POST':
                task_id=int(parts[2])
                with self._db() as db: task=get_task(db,task_id,user_id=session['id'])
                if not task: raise RequestError(404,'Task not found')
                if not task['agentsteers_job_id']: raise RequestError(409,'Task has not been dispatched')
                try:
                    info=agentsteers_bridge.status(task['agentsteers_job_id'])
                    with self._db() as db:
                        updated=record_agentsteers_update(db,task_id,session['id'],info['status'],
                            info['task_id'],info.get('result'))
                except agentsteers_bridge.BridgeError as exc:
                    # A confirmed remote task_not_found after an authenticated
                    # status lookup requires manual reconciliation, not redispatch.
                    if 'task_not_found' in str(exc):
                        with self._db() as db:
                            task = record_agentsteers_update(
                                db,task_id,session['id'],'recovery_needed',
                                task['agentsteers_job_id'],
                                {'message':'Remote mock job not found. Check persistent store or server identity; do not submit again.'})
                        return self._json(200,{'task':task,'simulated':True,'recovery_needed':True})
                    raise RequestError(503,'AgentSteers status unavailable: '+str(exc)[:160]) from None
                except (KeyError,ValueError):
                    raise RequestError(503,'AgentSteers returned an invalid mock task status') from None
                return self._json(200,{'task':updated,'simulated':True})
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
        if path == '/api/auth/google/links':
            with self._db() as db: found = google_login.links(db,session['id'])
            return self._json(200,{'configured':google_login.configured(self.gmail_config),'links':found,'redirect_uri':google_login.redirect_uri(self.gmail_config)})
        if path == '/api/translation/languages':
            return self._json(200,{'languages':local_translation.installed_targets()})
        if path == '/api/translation/packs':
            with _TRANSLATION_TIMINGS_LOCK: recent = _TRANSLATION_TIMINGS.get(session['id'])
            return self._json(200,{'catalog':[{'code':code,'name':name} for code,name in local_translation.LANGUAGES.items()],
                                   'installed':local_translation.installed_pairs(),
                                   'installation':local_translation.install_status(),
                                   'runtime_ready':local_translation.runtime_ready(), 'recent':recent,
                                   'preload':translation_preload.status(session['id'])})
        if path == '/api/connectors/gmail/stats':
            raw = parse_qs(urlparse(self.path).query).get('account',[''])[0]
            account_id = int(raw) if raw.lstrip('-').isdigit() else None
            with self._db() as db:
                if account_id is not None and account_id not in [a['id'] for a in gmail_web.accounts(db,session['id'])]: raise RequestError(404,'Unknown Gmail account')
                tz = prefs.effective(db,session['id'],self.gmail_config)['timezone']
                return self._json(200,mailstats.compute(db,session['id'],tz,account_id))
        if path == '/api/archived/senders':
            with self._db() as db: return self._json(200,{'senders':archive.senders(db,session['id'])})
        if path == '/api/connectors/gmail/status':
            with self._db() as db:
                connected = gmail_web.status(db,session['id'])
                stamp = gmail_web.last_sync(db,session['id']) if connected else None
                stored = gmail_web.stored_count(db,session['id']) if connected else 0
                counts = gmail_web.folder_counts(db,session['id']) if connected else {'inbox':0,'sent':0}
                writable = gmail_web.write_access(db,session['id']) if connected else False
                accounts,remote = gmail_web.overview(db,self.gmail_config,session['id']) if connected else ([],None)
                guessing = dict(flags.progress(session['id']),pending=flags.pending_count(db,session['id']) if connected else 0)
                labelling = dict(topics.progress(session['id']),pending=topics.pending_count(db,session['id']) if connected else 0)
                linking = dict(websites.progress(session['id']),pending=websites.pending_count(db,session['id']) if connected else 0)
                limit = prefs.effective(db,session['id'],self.gmail_config)['sync_max_messages'] if connected else None
            return self._json(200,{'configured':gmail_web.configured(self.gmail_config) or any(a['provider']=='imap' for a in accounts),'gmail_configured':gmail_web.configured(self.gmail_config),'connected':connected,'accounts':accounts,
                                   'last_sync':stamp,'stored':stored,'counts':counts,'write_access':writable,'remote':remote,'flags':guessing,'topics':labelling,'websites':linking,'sync_limit':limit,'sync':gmail_web.sync_progress(session['id'])})
        if path == '/api/connectors/gmail/messages':  # Local copy only; POST /sync talks to Gmail.
            query = parse_qs(urlparse(self.path).query)
            folder = query.get('folder',['inbox'])[0]
            view = query.get('view',['messages'])[0]
            if folder not in gmail_web.FOLDERS: raise RequestError(400,'Unknown folder')
            if view not in gmail_web.VIEWS: raise RequestError(400,'Unknown view')
            with self._db() as db:
                if not gmail_web.status(db,session['id']): raise RequestError(503,'Gmail is disconnected')
                search = mailsearch.parse(query.get('q',[''])[0],unread=query.get('unread',[''])[0] == '1',attachments=query.get('attachments',[''])[0] == '1',tz=prefs.effective(db,session['id'],self.gmail_config)['timezone'])
                searching = bool(mailsearch.condition(search)[0])
                wanted = query.get('account',[''])[0]
                if wanted and (not wanted.lstrip('-').isdigit() or int(wanted) not in [a['id'] for a in gmail_web.accounts(db,session['id'])]): raise RequestError(404,'Unknown mail account')
                account_id = int(wanted) if wanted else None
                listing = gmail_web.grouped_inbox(db,session['id'],folder,view,search if searching else None,account_id)
                counts = gmail_web.folder_counts(db,session['id'],account_id)
                unread_here = len(gmail_web.unread_ids(db,session['id'],folder,search if searching else None,account_id))
                stamp = gmail_web.last_sync(db,session['id'],account_id)
            return self._json(200,dict(listing,folder=folder,view=view,last_sync=stamp,counts=counts,unread_in_view=unread_here,read_only=True,searching=searching))
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
                message = self._gmail_message(session,parts[4])
                with self._db() as db:
                    user = prefs.effective(db,session['id'],self.gmail_config)
                    message['website'] = websites.get(db,session['id'],parts[4]) if user['websites_enabled'] else ''
                return self._json(200,{'message':message})
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
                if len(parts)==8 and parts[7]=='thumbnail':
                    mime = raster_preview_mime(row['mime_type'],content)
                    if not mime:
                        raise RequestError(415,'Image preview unavailable')
                    return self._send(200,content,mime,(('Content-Disposition','inline'),))
                if len(parts)==7:  # Always a download: never rendered inline and never trusted for its declared type.
                    safe = ''.join(c if c.isalnum() or c in '._-' else '_' for c in row['filename'])[:100] or 'attachment'
                    disposition = f"attachment; filename=\"{safe}\"; filename*=UTF-8''{quote(row['filename'])}"
                    return self._send(200,content,'application/octet-stream',(('Content-Disposition',disposition),))
        if len(parts)==6 and parts[:4]==['api','connectors','gmail','messages'] and parts[5]=='proposal':
            message = self._gmail_message(session,parts[4])
            with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
            return self._json(200,proposal(message,user))
        if path == '/api/agentsteers/status':
            # Only reached after login; never disclose private tokens or endpoint URLs.
            return self._json(200,{'bridge':agentsteers_bridge.readiness()})
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
            traceback.print_exc()
            self._json(503,{'error':'Task database unavailable'})
        except (BrokenPipeError, ConnectionResetError):   # The browser left (navigated away, closed the tab): nothing to answer.
            pass
        except Exception:  # Never drop the connection: answer in JSON and keep the traceback in the log (journalctl).
            traceback.print_exc()
            self._json(500,{'error':'Internal error; see the server log'})

class RequestError(Exception):
    def __init__(self,status,message): self.status,self.message=status,message

if __name__=='__main__':
    import uvicorn
    uvicorn.run('asgi:app', host='127.0.0.1', port=int(os.environ.get('INBOX_TEND_PORT','8765')))
