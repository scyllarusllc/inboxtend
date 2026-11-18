import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from app import Handler, MESSAGES, draft_reply, summarize
from tasks import connection, decorate, create_task, get_task, update_task, events, ConflictError

class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        class TestHandler(Handler):
            db_path = Path(cls.tmp.name) / 'tasks.sqlite3'
        cls.server = ThreadingHTTPServer(('127.0.0.1',0),TestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.csrf = cls.request('/api/session')['csrf_token']
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()
    @classmethod
    def request(cls,path,method='GET',body=None,headers=None):
        h = headers or {}
        if body is not None:
            h = {'Content-Type':'application/json','X-CSRF-Token':cls.csrf,**h}
            body = json.dumps(body).encode()
        req = Request(cls.url+path,data=body,headers=h,method=method)
        try:
            with urlopen(req) as response: return json.load(response)
        except HTTPError as error:
            error.close()
            raise
    def test_samples(self):
        self.assertEqual(len(MESSAGES),5)
        self.assertTrue(summarize(MESSAGES[0]))
        self.assertNotIn('finished',draft_reply(MESSAGES[-1]))
    def test_list_and_page(self):
        self.assertEqual(len(self.request('/api/messages')['messages']),5)
        with urlopen(self.url) as response:
            page=response.read()
            self.assertIn(b'Inbox Tend',page)
            self.assertIn(b'href="/static/styles.css"',page)
            self.assertIn(b'id="task-summary"',page)
            self.assertNotIn("'unsafe-inline'",response.headers['Content-Security-Policy'])
        with urlopen(self.url+'/static/styles.css') as response:
            self.assertEqual(response.headers.get_content_type(),'text/css')
            self.assertIn(b'.workspace-grid',response.read())
    def test_create_dedup_persist_update_reopen_draft(self):
        fields={'message_id':'sample-website-followup','title':'Check start date and reply','description':'Verify agreement first',
                'priority':'high','owner':'Demo owner','due_date':'2026-11-20','due_time':'17:00',
                'due_timezone':'Pacific/Saipan','project_label':'Website project','customer_label':'Fictional client'}
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
        draft=self.request('/api/draft','POST',{'id':'sample-website-followup'})
        self.assertFalse(draft['sent']);self.assertTrue(draft['template'])
        self.assertIn('review',draft['draft'])
        with connection(self.server.RequestHandlerClass.db_path) as db:
            self.assertEqual(get_task(db,task_id)['status'],'new')
        with self.assertRaises(HTTPError) as error:self.request(f'/api/tasks/{task_id}','PATCH',{'revision':1,'status':'done'})
        self.assertEqual(error.exception.code,409)
    def test_security_validation(self):
        fields={'message_id':'sample-1','title':'x','description':'','priority':'high'}
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
        fields={'message_id':'sample-2','title':'Temporary','description':'','priority':'medium'}
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

if __name__=='__main__':unittest.main()
