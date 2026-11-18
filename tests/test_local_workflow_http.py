"""HTTP smoke tests for the local, fictional-message workflow."""
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import Handler


class LocalWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.original_path = Handler.db_path
        Handler.db_path = Path(cls.temp.name) / "tasks.sqlite3"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        Handler.db_path = cls.original_path
        cls.temp.cleanup()

    def call(self, path, method="GET", payload=None, csrf=None):
        headers = {}
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
        status, proposal = self.call("/api/messages/sample-website-followup/proposal")
        self.assertEqual(status, 200)
        self.assertEqual(proposal["inferred"]["due_date"], "2026-11-20")
        body = dict(proposal["inferred"], message_id="sample-website-followup", owner="")
        status, result = self.call("/api/tasks", "POST", body, token)
        self.assertEqual(status, 201)
        task = result["task"]
        self.assertEqual(task["status"], "new")
        duplicate_status, _ = self.call("/api/tasks", "POST", body, token)
        self.assertEqual(duplicate_status, 409)
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
        status, _ = self.call("/api/draft", "POST", {"id": "sample-1"})
        self.assertEqual(status, 403)
        _, session = self.call("/api/session")
        status, response = self.call("/api/draft", "POST",
                                     {"id": "sample-1"}, session["csrf_token"])
        self.assertEqual(status, 200)
        self.assertIs(response["sent"], False)
        self.assertIs(response["template"], True)


if __name__ == "__main__":
    unittest.main()
