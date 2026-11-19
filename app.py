"""Local single-admin Gmail task workspace. Reads Gmail headers and snippets only; never sends email."""
from __future__ import annotations
import json
import os
import secrets
import time
import sqlite3
import traceback
from html import escape
from contextlib import contextmanager, closing
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote
import ai
import gmail_web
import google_login
import mailsearch
import flags
import topics
import settings as prefs
import mailstats
from auth import AuthManager, SESSION_SECONDS, cookie_token, read_config
from tasks import connection, DEFAULT_DB, list_tasks, task_summary, get_task, task_for_source, create_task, update_task, events, delete_task, ConflictError

ROOT = Path(__file__).resolve().parent
NAV_ITEMS = (('inbox','/user/inbox','Inbox','inbox'),('tasks','/user/tasks','Tasks','list-check'))
# name -> (title, description, scripts, active nav item, signed in)
PAGES = {
    'home': ('Inbox Tend · Review work from email', 'Review email-derived work with a human in control.', (), 'home', False),
    'login': ('Sign in · Inbox Tend', 'Sign in to the Inbox Tend workspace.', ('/static/login.js',), None, False),
    'inbox': ('Inbox · Inbox Tend', 'Review your Gmail inbox and turn messages into local tasks.', ('/static/common.js','/static/inbox.js','/static/vendor/chartjs/chart.umd.min.js','/static/stats.js'), 'inbox', True),
    'tasks': ('Tasks · Inbox Tend', 'Track local follow-up tasks, owners, deadlines and status.', ('/static/common.js','/static/tasks.js'), 'tasks', True),
    'settings': ('Settings · Inbox Tend', 'Timezone, display, mail sync and account settings.', ('/static/common.js','/static/settings.js'), 'settings', True),
    'privacy': ('Privacy · Inbox Tend', 'What Inbox Tend reads, keeps and never does.', (), None, False),
}
VENDOR_TYPES = {'.css':'text/css; charset=utf-8','.woff2':'font/woff2','.js':'text/javascript','.svg':'image/svg+xml'}
SCREEN_TYPES = {'.jpg':'image/jpeg','.png':'image/png'}   # Screenshots of the demo data, shown on the public home page
STATIC_FILES = {'/static/icons/favicon.svg':'image/svg+xml','/static/icons/favicon-32.png':'image/png','/static/icons/apple-touch-icon.png':'image/png',
                '/static/styles.css':'text/css','/static/common.js':'text/javascript','/static/inbox.js':'text/javascript','/static/stats.js':'text/javascript',
                '/static/tasks.js':'text/javascript','/static/settings.js':'text/javascript','/static/login.js':'text/javascript'}

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
        headers = {'Content-Type':mime,'Content-Length':str(len(payload)),'Cache-Control':'no-store',
                   'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                   'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}
        for key,value in extra_headers:   # A caller's header replaces the default of the same name instead of repeating it.
            headers[next((k for k in headers if k.lower() == key.lower()),key)] = value
        self.send_response(status)
        for key,value in headers.items():
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self,status,data,extra_headers=()):
        self._send(status,json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8',extra_headers)

    def _redirect(self,location,extra_headers=()):
        self.send_response(303)
        self.send_header('Location',location)
        for key,value in extra_headers: self.send_header(key,value)
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
                return self._send(200,render_page('home'),'text/html; charset=utf-8')
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
                return self._send(200,render_page('privacy'),'text/html; charset=utf-8')
            if path == '/api/health': return self._json(200,{'status':'ok'})
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
        if not write and path in ('/user/inbox','/user/tasks','/user/settings'):
            return self._send(200,render_page(path.rsplit('/',1)[1]),'text/html; charset=utf-8')
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
            if path == '/api/connectors/gmail/sync' and self.command == 'POST':
                with self._db() as db:
                    if not gmail_web.status(db,session['id']): raise RequestError(503,'Gmail is disconnected')
                if not gmail_web.configured(self.gmail_config): raise RequestError(503,'Gmail is not configured')
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
            if len(parts_w) == 6 and parts_w[:4] == ['api','connectors','gmail','messages'] and parts_w[5] == 'read' and self.command == 'POST':
                if not isinstance(data.get('read'),bool): raise RequestError(400,'"read" must be true or false')
                with self._db() as db:
                    user = prefs.effective(db,session['id'],self.gmail_config)
                    outcome = gmail_web.set_read(db,self.gmail_config,session['id'],parts_w[4],data['read'],write=user['gmail_write_read'])
                if outcome is None: raise RequestError(404,'Message not found')
                return self._json(200,dict(outcome,id=parts_w[4]))
            if path == '/api/topics/guess' and self.command == 'POST':
                with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
                if not user['topics_enabled']: raise RequestError(403,'Topic icons are turned off in Settings')
                return self._json(202,{'topics':topics.start(self.db_path,self.gmail_config,session['id'],user)})
            if path == '/api/topics/reset' and self.command == 'POST':
                with self._db() as db: topics.reset(db,session['id'])
                return self._json(200,{'reset':True})
            if path == '/api/flags/guess' and self.command == 'POST':
                with self._db() as db: user = prefs.effective(db,session['id'],self.gmail_config)
                if not user['flags_enabled']: raise RequestError(403,'Country flags are turned off in Settings')
                return self._json(202,{'flags':flags.start(self.db_path,self.gmail_config,session['id'],user)})
            if path == '/api/flags/reset' and self.command == 'POST':
                with self._db() as db: flags.reset(db,session['id'])
                return self._json(200,{'reset':True})
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
        if path == '/api/auth/google/links':
            with self._db() as db: found = google_login.links(db,session['id'])
            return self._json(200,{'configured':google_login.configured(self.gmail_config),'links':found,'redirect_uri':google_login.redirect_uri(self.gmail_config)})
        if path == '/api/connectors/gmail/stats':
            raw = parse_qs(urlparse(self.path).query).get('account',[''])[0]
            account_id = int(raw) if raw.isdigit() else None
            with self._db() as db:
                if account_id is not None and account_id not in [a['id'] for a in gmail_web.accounts(db,session['id'])]: raise RequestError(404,'Unknown Gmail account')
                tz = prefs.effective(db,session['id'],self.gmail_config)['timezone']
                return self._json(200,mailstats.compute(db,session['id'],tz,account_id))
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
                limit = prefs.effective(db,session['id'],self.gmail_config)['sync_max_messages'] if connected else None
            return self._json(200,{'configured':gmail_web.configured(self.gmail_config),'connected':connected,'accounts':accounts,
                                   'last_sync':stamp,'stored':stored,'counts':counts,'write_access':writable,'remote':remote,'flags':guessing,'topics':labelling,'sync_limit':limit,'sync':gmail_web.sync_progress(session['id'])})
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
                if wanted and (not wanted.isdigit() or int(wanted) not in [a['id'] for a in gmail_web.accounts(db,session['id'])]): raise RequestError(404,'Unknown Gmail account')
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
        except (BrokenPipeError, ConnectionResetError):   # The browser left (navigated away, closed the tab): nothing to answer.
            pass
        except Exception:  # Never drop the connection: answer in JSON and keep the traceback in the log (journalctl).
            traceback.print_exc()
            self._json(500,{'error':'Internal error; see the server log'})

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
    gmail_web.start_auto_sync(DEFAULT_DB,config)
    print(f'Inbox Tend: http://{host}:{port}')
    ThreadingHTTPServer((host,port),Handler).serve_forever()
