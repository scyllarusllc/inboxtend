"""Local fictional inbox and reviewed task demo. Never connects to or sends email."""
from __future__ import annotations
import json
import os
import secrets
import sqlite3
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from tasks import connection, DEFAULT_DB, list_tasks, get_task, task_for_source, create_task, update_task, events, delete_task, ConflictError

ROOT = Path(__file__).resolve().parent
MESSAGES = [
 {'id':'sample-1','thread_id':'thread-1','provider':'demo','from':'alex@example.org','subject':'Website login issue','date':'2026-11-15','body':'Hi, the login form sometimes fails after submitting. Could you investigate and let us know what you find?','priority':'high'},
 {'id':'sample-2','thread_id':'thread-2','provider':'demo','from':'team@example.org','subject':'Friday project update','date':'2026-11-14','body':'Please send a short progress update for the dashboard project when you have time.','priority':'medium'},
 {'id':'sample-3','thread_id':'thread-3','provider':'demo','from':'newsletter@example.org','subject':'Weekly design newsletter','date':'2026-11-13','body':"This week's design roundup includes ideas for accessible forms and simpler navigation.",'priority':'low'},
 {'id':'sample-website-followup','thread_id':'thread-website-followup','provider':'demo','from':'casey@example.org','subject':'Website project: response and start date','date':'2026-11-18','body':'Hello, I have not heard back about the website project. Our notes show a different service start date than the one in the last update. Could you check the agreed date and reply by November 20, 2026? Thank you.','priority':'high'}
]

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
    csrf_token = secrets.token_urlsafe(32)

    def _send(self,status,payload,mime):
        self.send_response(status)
        for key,value in (('Content-Type',mime),('Content-Length',str(len(payload))),('Cache-Control','no-store'),
                          ('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
                          ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")):
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self,status,data):
        self._send(status,json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')

    def _host_ok(self):
        host = self.headers.get('Host','')
        name = host.split(':',1)[0].lower()
        return name in ('127.0.0.1','localhost','[::1]') and host.count(':') <= 1

    def _message(self,id):
        return next((m for m in MESSAGES if m['id']==id),None)

    def _db(self):
        return connection(self.db_path)

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

    def _write_ok(self):
        if not self._host_ok():
            raise RequestError(403,'Invalid Host')
        origin = self.headers.get('Origin')
        expected = 'http://' + self.headers['Host']
        if origin and origin != expected:
            raise RequestError(403,'Cross-origin write blocked')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise RequestError(403,'Cross-site write blocked')
        if not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),self.csrf_token):
            raise RequestError(403,'CSRF token required')

    def _route(self,write=False):
        path = urlparse(self.path).path
        if write:
            self._write_ok()
            data = self._read_json()
            if path == '/api/draft' and self.command == 'POST':
                message = self._message(data.get('id'))
                if not message: raise RequestError(404,'Message not found')
                return self._json(200,{'draft':draft_reply(message),'sent':False,'mode':'demo','template':True})
            if path == '/api/tasks' and self.command == 'POST':
                message = self._message(data.get('message_id'))
                if not message: raise RequestError(404,'Message not found')
                with self._db() as db:
                    existing = task_for_source(db,'demo',message['id'])
                    if existing:
                        return self._json(409,{'error':'Task already exists','task':existing})
                    task = create_task(db,message,data)
                return self._json(201,{'task':task})
            parts = path.strip('/').split('/')
            if len(parts)==3 and parts[:2]==['api','tasks'] and parts[2].isdigit():
                task_id = int(parts[2])
                if self.command == 'PATCH':
                    with self._db() as db: task = update_task(db,task_id,data)
                    if task is None: raise RequestError(404,'Task not found')
                    return self._json(200,{'task':task})
                if self.command == 'DELETE':
                    if data.get('confirm') != 'DELETE': raise RequestError(400,'Explicit DELETE confirmation required')
                    with self._db() as db: removed = delete_task(db,task_id)
                    if not removed: raise RequestError(404,'Task not found')
                    return self._json(200,{'deleted':True})
            raise RequestError(404,'Not found')
        if not self._host_ok(): raise RequestError(403,'Invalid Host')
        if path == '/':
            return self._send(200,(ROOT/'static'/'index.html').read_bytes(),'text/html; charset=utf-8')
        if path == '/static/app.js':
            return self._send(200,(ROOT/'static'/'app.js').read_bytes(),'text/javascript; charset=utf-8')
        if path == '/api/session':
            return self._json(200,{'csrf_token':self.csrf_token,'mode':'demo'})
        if path == '/api/messages':
            return self._json(200,{'mode':'demo','messages':[dict(m,summary=summarize(m)) for m in MESSAGES]})
        if path == '/api/health': return self._json(200,{'status':'ok','mode':'demo'})
        parts = path.strip('/').split('/')
        if len(parts)==4 and parts[:2]==['api','messages'] and parts[3]=='proposal':
            message = self._message(parts[2])
            if not message: raise RequestError(404,'Message not found')
            return self._json(200,proposal(message))
        if path == '/api/tasks':
            with self._db() as db: rows = list_tasks(db)
            return self._json(200,{'tasks':rows,'timezone':'Pacific/Saipan'})
        if len(parts) in (3,4) and parts[:2]==['api','tasks'] and parts[2].isdigit():
            with self._db() as db:
                task = get_task(db,int(parts[2]))
                if not task: raise RequestError(404,'Task not found')
                if len(parts)==4 and parts[3]=='events': return self._json(200,{'events':events(db,task['id'])})
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
    print(f'Inbox Tend local demo: http://{host}:{port}')
    print('Fictional messages only. No account connected. No email sending.')
    ThreadingHTTPServer((host,port),Handler).serve_forever()
