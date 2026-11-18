import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from app import Handler, PAGES, ROOT, draft_reply, proposal, render_page, summarize
from auth import AuthManager, hash_password
from tasks import connection, decorate, create_task, get_task, update_task, events, ConflictError

MESSAGE={'id':'msg-1','thread_id':'thread-1','from':'a@example.org','subject':'Subject','date':'Today','snippet':'Snippet'}

class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path_for_test = Path(cls.tmp.name) / 'tasks.sqlite3'
        class TestHandler(Handler):
            db_path = path_for_test
            auth_manager = AuthManager(path_for_test,username='admin@example.test',
                                       password_hash=hash_password('fictional-test-password-123'))
        cls.server = ThreadingHTTPServer(('127.0.0.1',0),TestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        login=Request(cls.url+'/api/auth/login',data=json.dumps({'username':'admin@example.test','password':'fictional-test-password-123'}).encode(),
                      headers={'Content-Type':'application/json'},method='POST')
        with urlopen(login) as response:
            cls.cookie=response.headers['Set-Cookie'].split(';',1)[0]
        cls.csrf = cls.request('/api/session')['csrf_token']
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()
    @classmethod
    def request(cls,path,method='GET',body=None,headers=None):
        h = {'Cookie':cls.cookie,**(headers or {})}
        if body is not None:
            h = {'Content-Type':'application/json','X-CSRF-Token':cls.csrf,**h}
            body = json.dumps(body).encode()
        req = Request(cls.url+path,data=body,headers=h,method=method)
        try:
            with urlopen(req) as response: return json.load(response)
        except HTTPError as error:
            error.close()
            raise
    def test_gmail_derived_helpers(self):
        message={'id':'m1','thread_id':'t1','subject':'Hello','from':'a@example.org','date':'Today','snippet':'word '*60}
        self.assertTrue(summarize(message).endswith('...'))
        self.assertIn('Hello',draft_reply(message))
        self.assertNotIn('finished',draft_reply(message))
        self.assertEqual(proposal(message)['source']['provider'],'gmail')
        self.assertIsNone(proposal(message)['inferred']['due_date'])
        self.assertEqual(proposal(dict(message,subject=''))['inferred']['title'],'(No subject)')
    def test_list_and_page(self):
        with urlopen(Request(self.url+'/user/dashboard',headers={'Cookie':self.cookie})) as response:
            page=response.read().decode().lower()
            for word in ('demo','fictional','sample'):
                self.assertNotIn(word,page)
        with urlopen(self.url) as response:
            page=response.read()
            self.assertIn(b'Inbox Tend',page)
            self.assertIn(b'href="/static/styles.css"',page)
            self.assertNotIn(b'id="task-summary"',page)
        with urlopen(Request(self.url+'/user/dashboard',headers={'Cookie':self.cookie})) as response:
            page=response.read()
            self.assertIn(b'id="task-summary"',page)
            self.assertNotIn("'unsafe-inline'",response.headers['Content-Security-Policy'])
        with urlopen(self.url+'/static/styles.css') as response:
            self.assertEqual(response.headers.get_content_type(),'text/css')
            self.assertIn(b'.workspace-grid',response.read())
    def test_private_routes_require_login(self):
        for path in ('/api/connectors/gmail/messages','/api/tasks','/api/session'):
            with self.assertRaises(HTTPError) as error:
                urlopen(self.url+path)
            self.assertEqual(error.exception.code,401)
            error.exception.close()
        with urlopen(self.url+'/user/dashboard') as response:
            self.assertIn(b'Sign in to Inbox Tend',response.read())
        with urlopen(self.url+'/login') as response:
            self.assertIn(b'id="login-form"',response.read())
    def test_shared_header_footer_on_all_pages(self):
        pages=[]
        for path,headers in (('/',{}),('/login',{}),('/user/dashboard',{'Cookie':self.cookie})):
            with urlopen(Request(self.url+path,headers=headers)) as response:
                page=response.read().decode()
            self.assertEqual(page.count('<header class="site-header">'),1)
            self.assertEqual(page.count('<footer class="site-footer">'),1)
            self.assertEqual(page.count('href="/static/styles.css"'),1)
            self.assertIn('aria-label="Main navigation"',page)
            pages.append(page)
        footers=[page.split('<footer class="site-footer">',1)[1].split('</footer>',1)[0] for page in pages]
        self.assertEqual(footers[0],footers[1])
        self.assertEqual(footers[1],footers[2])
        self.assertIn('id="logout"',pages[2])
        self.assertNotIn('id="logout"',pages[0])
    def test_built_pages_match_shared_layout(self):
        for name in PAGES:
            self.assertEqual((ROOT/'static'/f'{name}.html').read_bytes(),render_page(name))
    def test_login_logout_and_csrf(self):
        cross_site=Request(self.url+'/api/auth/login',data=b'{"username":"admin@example.test","password":"fictional-test-password-123"}',
                           headers={'Content-Type':'application/json','Origin':'https://evil.example'},method='POST')
        with self.assertRaises(HTTPError) as error:urlopen(cross_site)
        self.assertEqual(error.exception.code,403)
        error.exception.close()
        wrong=Request(self.url+'/api/auth/login',data=b'{"username":"admin@example.test","password":"wrong"}',
                      headers={'Content-Type':'application/json'},method='POST')
        with self.assertRaises(HTTPError) as error:urlopen(wrong)
        self.assertEqual(error.exception.code,401)
        error.exception.close()
        login=Request(self.url+'/api/auth/login',data=b'{"username":"admin@example.test","password":"fictional-test-password-123"}',
                      headers={'Content-Type':'application/json','X-Forwarded-Proto':'https'},method='POST')
        with urlopen(login) as response:
            cookie=response.headers['Set-Cookie']
            self.assertIn('HttpOnly',cookie)
            self.assertIn('SameSite=Lax',cookie)
            self.assertIn('Secure',cookie)
            token_cookie=cookie.split(';',1)[0]
        with urlopen(Request(self.url+'/api/session',headers={'Cookie':token_cookie})) as response:
            csrf=json.load(response)['csrf_token']
        logout=Request(self.url+'/api/auth/logout',data=b'{}',method='POST',
                       headers={'Cookie':token_cookie,'Content-Type':'application/json','X-CSRF-Token':csrf})
        with urlopen(logout) as response:
            self.assertEqual(json.load(response),{'signed_out':True})
            self.assertIn('Max-Age=0',response.headers['Set-Cookie'])
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url+'/api/session',headers={'Cookie':token_cookie}))
        self.assertEqual(error.exception.code,401)
        error.exception.close()
    def test_create_dedup_persist_update_reopen_draft(self):
        fields={'message_id':'msg-1','title':'Check start date and reply','description':'Verify agreement first',
                'priority':'high','owner':'Test owner','due_date':'2026-11-20','due_time':'17:00',
                'due_timezone':'Pacific/Saipan','project_label':'Website project','customer_label':'Test client'}
        with patch('app.gmail_web.message',return_value=MESSAGE):
            return self._create_flow(fields)
    def _create_flow(self,fields):
        task=self.request('/api/tasks','POST',fields)['task']
        self.assertEqual(task['review_state'],'reviewed')
        self.assertEqual(task['due_date'],'2026-11-20')
        self.assertEqual(self.request('/api/tasks/'+str(task['id']))['task']['title'],task['title'])
        self.assertEqual(len(self.request('/api/tasks')['tasks']),1)
        with self.assertRaises(HTTPError) as error:self.request('/api/tasks','POST',fields)
        self.assertEqual(error.exception.code,409)
        task_id=task['id']
        for status in ('in_progress','waiting','done','new'):
            task=self.request(f'/api/tasks/{task_id}','PATCH',{'revision':task['revision'],'status':status})['task']
            self.assertEqual(task['status'],status)
        self.assertEqual(len(self.request(f'/api/tasks/{task_id}/events')['events']),5)
        draft=self.request('/api/draft','POST',{'id':'msg-1'})
        self.assertFalse(draft['sent']);self.assertTrue(draft['template'])
        self.assertIn('review',draft['draft'])
        with connection(self.server.RequestHandlerClass.db_path) as db:
            self.assertEqual(get_task(db,task_id)['status'],'new')
        with self.assertRaises(HTTPError) as error:self.request(f'/api/tasks/{task_id}','PATCH',{'revision':1,'status':'done'})
        self.assertEqual(error.exception.code,409)
    def test_security_validation(self):
        fields={'message_id':'msg-sec','title':'x','description':'','priority':'high'}
        with patch('app.gmail_web.message',return_value=dict(MESSAGE,id='msg-sec')):
            self._validation_cases(fields)
    def _validation_cases(self,fields):
        with self.assertRaises(HTTPError) as error:self.request('/api/tasks','POST',fields,{'Origin':'https://evil.example'})
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(HTTPError) as error:self.request('/api/tasks','POST',fields,{'X-CSRF-Token':'wrong'})
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(HTTPError) as error:self.request('/api/tasks','POST',dict(fields,title='<script>'+('x'*201)))
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(HTTPError) as error:self.request('/api/tasks','POST',dict(fields,due_date='2026-02-30',due_time='17:00'))
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(HTTPError) as error:self.request('/api/tasks','POST',dict(fields,description='x'*17_000))
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(HTTPError) as error:self.request('/api/tasks','POST',dict(fields,priority='urgent'))
        self.assertEqual(error.exception.code,400)
    def test_explicit_retention_delete(self):
        fields={'message_id':'msg-2','title':'Temporary','description':'','priority':'medium'}
        with patch('app.gmail_web.message',return_value=dict(MESSAGE,id='msg-2')):
            task=self.request('/api/tasks','POST',fields)['task']
        path=f"/api/tasks/{task['id']}"
        with self.assertRaises(HTTPError) as error:self.request(path,'DELETE',{'confirm':'no'})
        self.assertEqual(error.exception.code,400)
        self.assertTrue(self.request(path,'DELETE',{'confirm':'DELETE'})['deleted'])
        with self.assertRaises(HTTPError) as error:self.request(path)
        self.assertEqual(error.exception.code,404)
    def test_deadlines(self):
        item={'status':'new','due_date':'2026-11-20','due_time':'17:00','due_timezone':'Pacific/Saipan'}
        self.assertEqual(decorate(item,datetime(2026,11,18,7,0,tzinfo=timezone.utc))['deadline_state'],'due_soon')
        self.assertEqual(decorate(item,datetime(2026,11,20,8,0,tzinfo=timezone.utc))['deadline_state'],'overdue')
        self.assertEqual(decorate(dict(item,status='done'),datetime(2026,11,20,8,0,tzinfo=timezone.utc))['deadline_state'],'none')
    def test_legacy_migration(self):
        import sqlite3
        path=Path(self.tmp.name)/'legacy.sqlite3'
        old=sqlite3.connect(path)
        old.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, message_id TEXT NOT NULL UNIQUE,title TEXT NOT NULL,description TEXT NOT NULL,owner TEXT NOT NULL,due_date TEXT,priority TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
        old.execute("INSERT INTO tasks VALUES (1,'old','Old task','','','2026-11-20','low','new','2026-11-18','2026-11-18')")
        old.commit();old.close()
        with connection(path) as db:
            task=get_task(db,1)
            self.assertEqual(task['source_provider'],'demo')
            self.assertEqual(task['due_timezone'],'Pacific/Saipan')
            self.assertEqual(task['deadline_state'] in ('upcoming','due_soon','overdue'),True)
        AuthManager(path,username='admin@example.test',password_hash=hash_password('fictional-test-password-123'))
        with connection(path) as db:
            self.assertEqual(get_task(db,1)['title'],'Old task')
            self.assertEqual(db.execute('SELECT username FROM users').fetchone()[0],'admin@example.test')

if __name__=='__main__':unittest.main()
