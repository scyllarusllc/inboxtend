"""HTTP smoke tests for the Gmail-backed task workflow (Gmail itself is mocked)."""
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import time
from app import Handler
import gmail_web
from tasks import connection
from auth import AuthManager, hash_password


GMAIL_MESSAGE = {'id':'msg-1','thread_id':'thread-1','from':'Casey <casey@example.org>',
                 'subject':'Website project: response needed','date':'Wed, 18 Nov 2026 09:00:00 +1000',
                 'snippet':'Could you check the agreed date and reply?'}


class LocalWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / "tasks.sqlite3"
        class TestHandler(Handler):
            db_path = path
            auth_manager = AuthManager(path, username='admin@example.test',
                                       password_hash=hash_password('fictional-test-password-123'))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_address[1])
        login = Request(cls.base + '/api/auth/login',
                        data=json.dumps({'username':'admin@example.test','password':'fictional-test-password-123'}).encode(),
                        headers={'Content-Type':'application/json'}, method='POST')
        with urlopen(login) as response:
            cls.cookie = response.headers['Set-Cookie'].split(';',1)[0]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def call(self, path, method="GET", payload=None, csrf=None):
        headers = {'Cookie':self.cookie}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            if csrf is not None:
                headers["X-CSRF-Token"] = csrf
        request = Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=4) as result:
                return result.status, json.loads(result.read())
        except HTTPError as error:
            with closing(error):
                return error.code, json.loads(error.read())

    def test_proposal_create_duplicate_status_and_summary(self):
        _, session = self.call("/api/session")
        token = session["csrf_token"]
        with patch('app.gmail_web.message', return_value=GMAIL_MESSAGE) as fetched:
            status, proposal = self.call("/api/connectors/gmail/messages/msg-1/proposal")
            self.assertEqual(status, 200)
            self.assertEqual(proposal["inferred"]["title"], GMAIL_MESSAGE["subject"])
            self.assertIsNone(proposal["inferred"]["due_date"])
            body = dict(proposal["inferred"], message_id="msg-1", owner="")
            status, result = self.call("/api/tasks", "POST", body, token)
            self.assertEqual(status, 201)
            task = result["task"]
            self.assertEqual(task["status"], "new")
            self.assertEqual(task["source_provider"], "gmail")
            duplicate_status, _ = self.call("/api/tasks", "POST", body, token)
            self.assertEqual(duplicate_status, 409)
            self.assertEqual(fetched.call_args.args[3], "msg-1")
        status, updated = self.call("/api/tasks/" + str(task["id"]), "PATCH",
                                    {"revision": task["revision"], "status": "waiting"}, token)
        self.assertEqual(status, 200)
        self.assertEqual(updated["task"]["status"], "waiting")
        _, summary = self.call("/api/tasks/summary")
        self.assertEqual(summary["summary"]["total"], 1)
        self.assertEqual(summary["summary"]["by_status"]["waiting"], 1)
        _, history = self.call("/api/tasks/" + str(task["id"]) + "/events")
        self.assertEqual(len(history["events"]), 2)

    def test_csrf_required_and_draft_never_sends(self):
        status, _ = self.call("/api/draft", "POST", {"id": "msg-1"})
        self.assertEqual(status, 403)
        _, session = self.call("/api/session")
        with patch('app.gmail_web.message', return_value=GMAIL_MESSAGE):
            status, response = self.call("/api/draft", "POST",
                                         {"id": "msg-1"}, session["csrf_token"])
        self.assertEqual(status, 200)
        self.assertIs(response["sent"], False)
        self.assertIs(response["template"], True)

    def test_inbox_reads_local_copy_and_sync_needs_csrf(self):
        status, _ = self.call("/api/connectors/gmail/sync", "POST", {})
        self.assertEqual(status, 403)  # no CSRF token
        _, session = self.call("/api/session")
        status, _ = self.call("/api/connectors/gmail/sync", "POST", {}, session["csrf_token"])
        self.assertEqual(status, 503)  # not connected for this user
        status, body = self.call("/api/connectors/gmail/messages")
        self.assertEqual(status, 503)
        status, body = self.call("/api/connectors/gmail/status")
        self.assertEqual((body["connected"], body["last_sync"]), (False, None))

    def test_message_detail_attachments_download_preview_and_scoping(self):
        path = self.server.RequestHandlerClass.db_path
        item = {'id':'full-1','thread_id':'t','snippet':'snip','internal_date':5,'label_ids':[],'size_estimate':1,
                'all_headers':[],'headers':{'subject':'Report','from':'a@example.org','to':'me@example.org','date':'D'},
                'body_text':'Line one\nLine two','body_html':'','attachments':[
                    {'part_id':'1','filename':'../evil"name.csv','mime_type':'text/csv','size':8,'content':b'a,b\n1,2\n','skipped_reason':''},
                    {'part_id':'2','filename':'huge.bin','mime_type':'application/octet-stream','size':99,'content':None,'skipped_reason':'larger than the size limit'},
                    {'part_id':'3','filename':'page.html','mime_type':'text/html','size':10,'content':b'<script>1</script>','skipped_reason':''}]}
        db = connection(path)
        with db:
            gmail_web._store(db, 1, item, 1)
            db.execute("INSERT OR REPLACE INTO gmail_connections(user_id,refresh_token,connected_at) VALUES (1,x'00',0)")
        ids = {r['filename']: r['id'] for r in db.execute("SELECT id,filename FROM gmail_attachments")}
        db.close()
        status, body = self.call("/api/connectors/gmail/messages/full-1")
        self.assertEqual(status, 200)
        self.assertEqual(body['message']['body_text'], 'Line one\nLine two')
        self.assertEqual(sorted(a['stored'] for a in body['message']['attachments']), [False, True, True])
        status, listing = self.call("/api/connectors/gmail/messages")
        self.assertEqual(listing['messages'][0]['attachment_count'], 3)
        base = "/api/connectors/gmail/messages/full-1/attachments/"
        request = Request(self.base + base + str(ids['../evil"name.csv']), headers={'Cookie': self.cookie})
        with urlopen(request) as response:
            self.assertEqual(response.headers['Content-Type'], 'application/octet-stream')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            disposition = response.headers['Content-Disposition']
            self.assertTrue(disposition.startswith('attachment;'))
            self.assertNotIn('/', disposition.split('filename*')[0]); self.assertNotIn('"evil', disposition)
            self.assertEqual(response.read(), b'a,b\n1,2\n')
        status, preview = self.call(base + str(ids['../evil"name.csv']) + "/preview")
        self.assertEqual((status, preview['content'], preview['trusted']), (200, 'a,b\n1,2\n', False))
        self.assertEqual(self.call(base + str(ids['page.html']) + "/preview")[0], 415)  # HTML is never previewed
        self.assertEqual(self.call(base + str(ids['huge.bin']))[0], 404)  # not stored
        self.assertEqual(self.call(base + "999999")[0], 404)
        self.assertEqual(self.call("/api/connectors/gmail/messages/other-message/attachments/" + str(ids['huge.bin']))[0], 404)

    def test_sync_runs_in_background_and_reports_progress(self):
        _, session = self.call("/api/session")
        db = connection(self.server.RequestHandlerClass.db_path)
        with db:
            db.execute("INSERT OR REPLACE INTO gmail_connections(user_id,refresh_token,connected_at) VALUES (1,x'00',0)")
        db.close()
        def fake_run(db, config, user_id, *, report, **kw):
            report(total=3, done=3, failed=0)
        gmail_web._PROGRESS.clear()
        with patch.object(gmail_web, 'run_sync', side_effect=fake_run), patch.object(gmail_web, 'configured', return_value=True):
            self.assertEqual(self.call("/api/connectors/gmail/sync", "POST", {})[0], 403)  # CSRF required
            status, body = self.call("/api/connectors/gmail/sync", "POST", {}, session["csrf_token"])
            self.assertEqual(status, 202)
            for _ in range(100):
                _, state = self.call("/api/connectors/gmail/status")
                if not state['sync']['running']: break
                time.sleep(0.05)
        self.assertEqual((state['sync']['total'], state['sync']['done'], state['sync']['running']), (3, 3, False))

    def test_demo_message_api_is_gone_and_gmail_disconnected_is_reported(self):
        self.assertEqual(self.call("/api/messages")[0], 404)
        status, _ = self.call("/api/connectors/gmail/messages/msg-1/proposal")
        self.assertEqual(status, 503)  # Gmail not connected for this user
        _, session = self.call("/api/session")
        status, _ = self.call("/api/tasks", "POST",
                              {"message_id": "sample-1", "title": "x", "description": "", "priority": "low"},
                              session["csrf_token"])
        self.assertEqual(status, 503)


if __name__ == "__main__":
    unittest.main()
