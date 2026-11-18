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
    def _connect(self,user_id=1):
        url=gmail_web.begin(self.db,CONFIG,user_id,'session-a')
        state=parse_qs(urlparse(url).query)['state'][0]
        gmail_web.finish(self.db,CONFIG,user_id,'session-a',state,'code',opener=lambda *a,**k:Response({
            'scope':gmail_web.SCOPE,'token_type':'Bearer','access_token':'a','refresh_token':'r'}))
    @staticmethod
    def _full(i,when,attachments=()):
        return {'id':i,'thread_id':'t'+i,'snippet':'snip '+i,'internal_date':when,'label_ids':['INBOX'],'size_estimate':99,
                'all_headers':[['Subject','Subject '+i],['From','Sender '+i],['X-Custom','kept']],
                'headers':{'subject':'Subject '+i,'from':'Sender '+i,'date':'Day '+i,'to':'me@example.test'},
                'body_text':'Full body of '+i,'body_html':'<p>Full body of '+i+'</p>',
                'attachments':[dict(a) for a in attachments]}
    TOKEN=staticmethod(lambda *a,**k:Response({'token_type':'Bearer','access_token':'short-lived'}))
    def _sync(self,ids,fulls,config=CONFIG,attachments=None,user_id=1):
        listing=lambda t,**k:{'message_ids':[] if 'is:unread' in k.get('query','') else ids,'next_page_token':None}
        with patch.object(gmail_web,'list_message_ids',side_effect=listing), \
             patch.object(gmail_web,'get_message_full',side_effect=lambda t,i:fulls[i]) as got, \
             patch.object(gmail_web,'get_attachment',side_effect=lambda t,m,a,max_bytes:(attachments or {})[a]) as att:
            gmail_web.run_sync(self.db,config,user_id,opener=self.TOKEN)
        return got,att
    def test_sync_stores_everything_locally_fetches_only_new_and_reconciles(self):
        self._connect()
        with self.assertRaises(gmail_web.GmailConnectionError):
            gmail_web.run_sync(self.db,CONFIG,2)
        self.assertEqual(gmail_web.cached_inbox(self.db,1),[])
        self.assertIsNone(gmail_web.last_sync(self.db,1))
        got,_=self._sync(['b','a'],{'a':self._full('a',100),'b':self._full('b',200)})
        self.assertEqual(got.call_count,2)
        rows=gmail_web.cached_inbox(self.db,1)
        self.assertEqual([r['id'] for r in rows],['b','a'])  # newest first
        self.assertEqual(rows[0]['subject'],'Subject b')
        self.assertIsNotNone(gmail_web.last_sync(self.db,1))
        self.assertEqual(gmail_web.stored_count(self.db,1),2)
        # Reading is purely local and returns the complete message.
        with patch.object(gmail_web,'list_message_ids') as listed, patch.object(gmail_web,'get_message_full') as full:
            detail=gmail_web.message(self.db,CONFIG,1,'a')
        listed.assert_not_called();full.assert_not_called()
        self.assertEqual((detail['body_text'],detail['to'],detail['labels']),('Full body of a','me@example.test',['INBOX']))
        self.assertIn(['X-Custom','kept'],detail['headers'])
        # Second sync downloads only 'c'; 'b' (newer than the oldest listed message, now absent) left the inbox.
        got,_=self._sync(['c','a'],{'c':self._full('c',300)})
        got.assert_called_once_with('short-lived','c')
        self.assertEqual([r['id'] for r in gmail_web.cached_inbox(self.db,1)],['c','a'])
        self.assertEqual(gmail_web.message(self.db,CONFIG,1,'b')['body_text'],'Full body of b')  # still stored
        self.assertEqual(gmail_web.cached_inbox(self.db,2),[])
        gmail_web.disconnect(self.db,1)
        for table in ('gmail_messages','gmail_attachments'):
            self.assertEqual(self.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0)
    def test_attachments_are_stored_capped_and_user_scoped(self):
        self._connect()
        small={'part_id':'1','filename':'notes.csv','mime_type':'text/csv','size':9,'attachment_id':'A1','data':None}
        inline={'part_id':'2','filename':'tiny.txt','mime_type':'text/plain','size':2,'attachment_id':None,'data':b'hi'}
        huge={'part_id':'3','filename':'big.bin','mime_type':'application/octet-stream','size':50,'attachment_id':'A3','data':None}
        got,att=self._sync(['m'],{'m':self._full('m',1,[small,inline,huge])},attachments={'A1':b'a,b\n1,2\n'},
                           config=dict(CONFIG,INBOX_TEND_ATTACHMENT_MAX_BYTES='20'))
        att.assert_called_once()  # the oversized one is never downloaded
        row=gmail_web.cached_inbox(self.db,1)[0]
        self.assertEqual(row['attachment_count'],3)
        files={f['name']:f for f in gmail_web.message(self.db,CONFIG,1,'m')['attachments']}
        self.assertTrue(files['notes.csv']['stored']);self.assertTrue(files['tiny.txt']['stored'])
        self.assertFalse(files['big.bin']['stored']);self.assertIn('size limit',files['big.bin']['skipped_reason'])
        self.assertEqual(bytes(gmail_web.attachment(self.db,1,'m',files['notes.csv']['id'])['content']),b'a,b\n1,2\n')
        self.assertIsNone(gmail_web.attachment(self.db,2,'m',files['notes.csv']['id']))
        self.assertIsNone(gmail_web.attachment(self.db,1,'other',files['notes.csv']['id']))
    def test_partial_failure_keeps_progress_and_total_failure_raises(self):
        self._connect()
        def flaky(t,i):
            if i=='b': raise gmail_web.GmailReadError('boom')
            return self._full(i,100)
        reports=[]
        with patch.object(gmail_web,'list_message_ids',return_value={'message_ids':['a','b'],'next_page_token':None}), \
             patch.object(gmail_web,'get_message_full',side_effect=flaky):
            gmail_web.run_sync(self.db,CONFIG,1,opener=self.TOKEN,report=lambda **kw:reports.append(kw))
        self.assertEqual(gmail_web.stored_count(self.db,1),1)  # 'a' kept; a retry only needs 'b'
        self.assertIn('could not be downloaded',reports[-1]['error'])
        with patch.object(gmail_web,'list_message_ids',return_value={'message_ids':['x'],'next_page_token':None}), \
             patch.object(gmail_web,'get_message_full',side_effect=gmail_web.GmailReadError('Gmail API rejected the read request (HTTP 403).')):
            with self.assertRaises(gmail_web.GmailReadError):
                gmail_web.run_sync(self.db,CONFIG,1,opener=self.TOKEN)
    def test_listing_follows_pages_up_to_the_configured_limit(self):
        pages=[{'message_ids':['a','b'],'next_page_token':'p2'},{'message_ids':['c','d'],'next_page_token':'p3'}]
        with patch.object(gmail_web,'list_message_ids',side_effect=pages) as listed:
            ids=gmail_web._listing('tok','in:inbox',3)
        self.assertEqual(ids,['a','b','c'])
        self.assertEqual([c.kwargs['page_token'] for c in listed.call_args_list],[None,'p2'])
        self.assertEqual(gmail_web.sync_settings({'INBOX_TEND_SYNC_MAX_MESSAGES':'junk'})['max_messages'],200)
        self.assertEqual(gmail_web.sync_settings({'INBOX_TEND_SYNC_MAX_MESSAGES':'999999'})['max_messages'],5000)
    def test_background_sync_reports_progress_and_runs_once(self):
        import threading
        gmail_web._PROGRESS.clear()
        gate=threading.Event()
        def fake_run(db,config,user_id,*,report,**k):
            report(total=2,done=1,failed=0); gate.wait(5)
        path=Path(self.tmp.name)/'db.sqlite3'
        with patch.object(gmail_web,'run_sync',side_effect=fake_run) as run:
            first=gmail_web.start_sync(path,CONFIG,1)
            second=gmail_web.start_sync(path,CONFIG,1)  # already running: no second thread
            self.assertTrue(first['running'] and second['running'])
            gate.set()
            for _ in range(100):
                if not gmail_web.sync_progress(1)['running']: break
                import time; time.sleep(0.05)
        self.assertEqual(run.call_count,1)
        done=gmail_web.sync_progress(1)
        self.assertEqual((done['running'],done['total'],done['done']),(False,2,1))
    def test_single_message_fetch_is_user_scoped_and_validates_id(self):
        self._connect()
        with self.assertRaises(gmail_web.GmailConnectionError):
            gmail_web.message(self.db,CONFIG,2,'abc')
        with patch.object(gmail_web,'get_message_full',return_value=self._full('abc',1)) as got:
            row=gmail_web.message(self.db,CONFIG,1,'abc',opener=self.TOKEN)
            again=gmail_web.message(self.db,CONFIG,1,'abc',opener=self.TOKEN)  # served from SQLite
        self.assertEqual((row['id'],row['subject'],row['body_text']),('abc','Subject abc','Full body of abc'))
        self.assertEqual(again,row)
        got.assert_called_once_with('short-lived','abc')
        self.assertEqual(gmail_web.cached_inbox(self.db,1),[])  # fetched on demand, not flagged as inbox
        with self.assertRaises(gmail_web.GmailReadError):
            gmail_web.message(self.db,CONFIG,1,'../bad',opener=self.TOKEN)
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
                             ('/api/connectors/gmail/messages/abc/proposal','GET'),
                             ('/api/connectors/gmail/connect','POST'),
                             ('/api/connectors/gmail/sync','POST'),
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
        message={'id':'gmail-one','thread_id':'thread-one','provider':'gmail'}
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
