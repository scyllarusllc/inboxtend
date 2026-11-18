import base64
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch
from tasks import connection
from tasks import create_task, list_tasks, get_task, update_task, events, delete_task
import gmail_web
from app import Handler, RequestError
from email.message import Message

CONFIG = {
    'INBOX_TEND_GOOGLE_CLIENT_ID':'test.apps.googleusercontent.com',
    'INBOX_TEND_GOOGLE_CLIENT_SECRET':'test-only-secret',
    'INBOX_TEND_GOOGLE_REDIRECT_URI':'https://inboxtend.scyllar.us/api/connectors/gmail/callback',
    'INBOX_TEND_TOKEN_KEY':base64.urlsafe_b64encode(b'k'*32).decode(),
}

class Response:
    def __init__(self, data): self.data=json.dumps(data).encode()
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self,n): return self.data

class GmailWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=connection(Path(self.tmp.name)/'db.sqlite3')
    def tearDown(self): self.db.close();self.tmp.cleanup()
    def test_session_bound_one_time_state_pkce_and_encryption(self):
        url=gmail_web.begin(self.db,CONFIG,1,'session-a')
        query=parse_qs(urlparse(url).query)
        self.assertEqual(query['scope'],[gmail_web.SCOPE])
        self.assertEqual(query['code_challenge_method'],['S256'])
        self.assertEqual(query['redirect_uri'],[CONFIG['INBOX_TEND_GOOGLE_REDIRECT_URI']])
        state=query['state'][0]
        with self.assertRaises(gmail_web.GmailConnectionError):
            gmail_web.finish(self.db,CONFIG,1,'session-b',state,'code')
        with self.assertRaises(gmail_web.GmailConnectionError):
            gmail_web.finish(self.db,CONFIG,2,'session-a',state,'code')
        calls=[]
        def fake_open(request,timeout):
            body=parse_qs(request.data.decode());calls.append(body)
            return Response({'scope':gmail_web.SCOPE,'token_type':'Bearer','access_token':'ephemeral-access','refresh_token':'private-refresh'})
        gmail_web.finish(self.db,CONFIG,1,'session-a',state,'code',opener=fake_open)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['code_verifier'][0] != '',True)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(calls[0]['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(challenge,query['code_challenge'][0])
        stored=self.db.execute('SELECT refresh_token FROM gmail_connections WHERE user_id=1').fetchone()[0]
        self.assertNotIn(b'private-refresh',stored)
        self.assertFalse(gmail_web.status(self.db,2))
        with self.assertRaises(gmail_web.GmailConnectionError):
            gmail_web.finish(self.db,CONFIG,1,'session-a',state,'code',opener=fake_open)
        gmail_web.disconnect(self.db,1)
        self.assertFalse(gmail_web.status(self.db,1))
    def test_read_only_inbox_and_user_scope(self):
        url=gmail_web.begin(self.db,CONFIG,1,'session-a')
        state=parse_qs(urlparse(url).query)['state'][0]
        gmail_web.finish(self.db,CONFIG,1,'session-a',state,'code',opener=lambda *a,**k:Response({
            'scope':gmail_web.SCOPE,'token_type':'Bearer','access_token':'a','refresh_token':'r'}))
        with self.assertRaises(gmail_web.GmailConnectionError):
            gmail_web.inbox(self.db,CONFIG,2)
        with patch.object(gmail_web,'list_message_ids',return_value={'message_ids':['abc'],'next_page_token':None}) as listed, patch.object(gmail_web,'get_message_metadata',return_value={'id':'abc','thread_id':'thread','snippet':'preview','headers':{'subject':'Subject','from':'Sender','date':'Today'}}) as got:
            rows=gmail_web.inbox(self.db,CONFIG,1,opener=lambda *a,**k:Response({'token_type':'Bearer','access_token':'short-lived'}))
        self.assertEqual(rows[0]['snippet'],'preview')
        listed.assert_called_once_with('short-lived',max_results=20,query='in:inbox')
        got.assert_called_once_with('short-lived','abc')
    def test_rejects_broader_scope_and_invalid_config(self):
        self.assertFalse(gmail_web.configured(dict(CONFIG,INBOX_TEND_TOKEN_KEY='wrong')))
        url=gmail_web.begin(self.db,CONFIG,1,'session-a')
        state=parse_qs(urlparse(url).query)['state'][0]
        with self.assertRaises(gmail_web.GmailConnectionError):
            gmail_web.finish(self.db,CONFIG,1,'session-a',state,'code',opener=lambda *a,**k:Response({
                'scope':gmail_web.SCOPE+' https://www.googleapis.com/auth/gmail.modify',
                'token_type':'Bearer','access_token':'a','refresh_token':'r'}))
        self.assertFalse(gmail_web.status(self.db,1))

    def test_expired_and_replaced_oauth_state_cannot_exchange(self):
        first=gmail_web.begin(self.db,CONFIG,1,'session-a')
        first_state=parse_qs(urlparse(first).query)['state'][0]
        second=gmail_web.begin(self.db,CONFIG,1,'session-a')
        second_state=parse_qs(urlparse(second).query)['state'][0]
        with self.assertRaises(gmail_web.GmailConnectionError):
            gmail_web.finish(self.db,CONFIG,1,'session-a',first_state,'code')
        with self.db:
            self.db.execute('UPDATE gmail_oauth_pending SET expires_at=0')
        with self.assertRaises(gmail_web.GmailConnectionError):
            gmail_web.finish(self.db,CONFIG,1,'session-a',second_state,'code')

    def test_gmail_routes_require_app_session_without_network_socket(self):
        handler = Handler.__new__(Handler)
        handler.auth_manager = None
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8765'
        for path, method in [('/api/connectors/gmail/status','GET'),
                             ('/api/connectors/gmail/messages','GET'),
                             ('/api/connectors/gmail/connect','POST'),
                             ('/api/connectors/gmail/disconnect','POST')]:
            handler.path, handler.command = path, method
            with self.assertRaises(RequestError) as error:
                handler._route(write=method=='POST')
            self.assertEqual(error.exception.status,401)

    def test_proxy_preserves_public_origin_for_csrf_check(self):
        handler = Handler.__new__(Handler)
        handler.gmail_config = CONFIG
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8765'
        handler.headers['Origin'] = 'https://inboxtend.scyllar.us'
        handler._same_origin()
        handler.headers.replace_header('Origin','https://evil.example')
        with self.assertRaises(RequestError) as error:
            handler._same_origin()
        self.assertEqual(error.exception.status,403)

    def test_task_queries_are_user_scoped(self):
        message={'id':'demo-one','thread_id':'thread-one','provider':'demo'}
        fields={'title':'Private task','description':'Private note','priority':'low'}
        task=create_task(self.db,message,fields,user_id=1)
        self.assertEqual(len(list_tasks(self.db,user_id=1)),1)
        self.assertEqual(list_tasks(self.db,user_id=2),[])
        self.assertIsNone(get_task(self.db,task['id'],user_id=2))
        self.assertIsNone(update_task(self.db,task['id'],{'revision':1,'status':'done'},user_id=2))
        self.assertEqual(events(self.db,task['id'],user_id=2),[])
        self.assertFalse(delete_task(self.db,task['id'],user_id=2))
        self.assertIsNotNone(get_task(self.db,task['id'],user_id=1))

    def test_legacy_task_migrates_to_first_admin(self):
        path=Path(self.tmp.name)/'legacy.sqlite3'
        old=sqlite3.connect(path)
        old.execute('''CREATE TABLE tasks(id INTEGER PRIMARY KEY, message_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL, description TEXT NOT NULL, owner TEXT NOT NULL,
            due_date TEXT, priority TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        old.execute("INSERT INTO tasks VALUES (1,'legacy','Old task','','',NULL,'low','new','2026-11-18','2026-11-18')")
        old.commit();old.close()
        with connection(path) as migrated:
            self.assertEqual(get_task(migrated,1,user_id=1)['user_id'],1)
            self.assertIsNone(get_task(migrated,1,user_id=2))
        with connection(path) as migrated:
            self.assertEqual(list_tasks(migrated,user_id=1)[0]['title'],'Old task')

if __name__=='__main__': unittest.main()
