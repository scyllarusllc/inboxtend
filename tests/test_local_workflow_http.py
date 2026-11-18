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

from app import Handler
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
